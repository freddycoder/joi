"""Le cerveau de Joi : une session GitHub Copilot (via le Copilot SDK) qui porte sa personnalité.

La réponse est diffusée en continu (streaming) et découpée en phrases, pour que la voix
puisse commencer à parler avant que la réponse complète soit écrite. La première phrase peut même
être coupée plus tôt, à une virgule (couper_premiere).

Toutes les conversations partagent un client Copilot (un processus de la CLI), démarré avec le
serveur : chaque réveil n'y crée qu'une session. La bibliothécaire (joi/memoire.py) garde le sien.
"""

import asyncio
import logging
import re
import time
from typing import AsyncIterator

from copilot import CopilotClient
from copilot.generated.rpc import PermissionDecisionReject
from copilot.session_events import SessionEventType

from . import config, memoire

log = logging.getLogger("joi.cerveau")

# Une fin de phrase : ponctuation forte suivie d'un espace, ou un saut de ligne.
FIN_DE_PHRASE = re.compile(r"(?<=[.!?…])\s+|\n+")
FIN_DE_REPONSE = object()
# Un nouveau message du modèle commence dans le même tour (souvent après un outil, parfois deux
# réponses d'affilée) : ce qui reste du précédent est une phrase finie, à ne pas coller au suivant.
NOUVEAU_MESSAGE = object()

# Pour la première phrase seulement : si aucune fin de phrase n'est arrivée après ce nombre de mots,
# on coupe à une virgule, un point-virgule ou un deux-points suivi d'une espace. La voix commence
# plus tôt ; la suite se recolle à l'oreille. En français, la virgule décimale (« 3,5 ») n'est pas
# suivie d'une espace : elle n'est jamais coupée.
MOTS_AVANT_COUPURE = 8
COUPURE_DOUCE = re.compile(r"(?<=[,;:])\s+")


class _Recherche(str):
    """Marqueur produit par repondre() quand Joi fouille sa mémoire (le texte = la recherche)."""


class _Consultation(str):
    """Marqueur produit par repondre() quand Joi consulte son compte (le texte = l'outil)."""


RECHERCHE = _Recherche
CONSULTATION = _Consultation


def couper_phrases(tampon: str) -> tuple[list[str], str]:
    """Sépare les phrases complètes du reste encore en cours d'écriture."""
    morceaux = FIN_DE_PHRASE.split(tampon)
    completes = [m.strip() for m in morceaux[:-1] if m.strip()]
    return completes, morceaux[-1]


def couper_premiere(tampon: str, mots_min: int = MOTS_AVANT_COUPURE) -> tuple[str | None, str]:
    """La première phrase, coupée plus tôt : (le morceau jusqu'à la dernière virgule, le reste), ou
    (None, tampon) s'il n'y a pas encore assez de mots ou pas de virgule suivie d'une espace."""
    if len(tampon.split()) < mots_min:
        return None, tampon
    coupures = list(COUPURE_DOUCE.finditer(tampon))
    if not coupures:
        return None, tampon
    morceau = tampon[:coupures[-1].start()].strip()
    if not morceau:
        return None, tampon
    return morceau, tampon[coupures[-1].end():]


# --- Le client Copilot partagé ------------------------------------------------------------------

_client_partage: CopilotClient | None = None
_verrou_client: asyncio.Lock | None = None


async def client_partage(neuf: bool = False) -> CopilotClient:
    """Le client Copilot de toutes les conversations, démarré au premier appel (le serveur l'appelle
    dès son lancement). neuf : le remplacer (il ne répond plus) ; ses sessions sont alors perdues."""
    global _client_partage, _verrou_client
    if _verrou_client is None:
        _verrou_client = asyncio.Lock()
    async with _verrou_client:
        if neuf and _client_partage is not None:
            ancien, _client_partage = _client_partage, None
            try:
                await ancien.stop()
            except Exception:
                pass
        if _client_partage is None:
            client = CopilotClient()
            await client.start()
            _client_partage = client
        return _client_partage


async def arreter_client_partage() -> None:
    global _client_partage
    client, _client_partage = _client_partage, None
    if client:
        try:
            await client.stop()
        except Exception as e:
            log.warning("Arrêt du client Copilot : %s", e)


def refuser_permission(requete, invocation):
    """Toute demande d'action hors de ses outils (fichiers, terminal...) est refusée."""
    log.warning("Permission refusée : %s", getattr(requete, "kind", requete))
    return PermissionDecisionReject(feedback="Joi n'a pas le droit d'utiliser d'outils.")


class Cerveau:
    """modele : le modèle Copilot (JOI_MODELE par défaut ; le banc d'essai en essaie d'autres).
    avec_compte : brancher le compte de Joi s'il est réglé (le banc d'essai s'en passe)."""

    def __init__(self, modele: str | None = config.JOI_MODELE, avec_compte: bool = True):
        self.modele = modele
        self._avec_compte = avec_compte
        # La dernière réponse, pour la mesure de la latence (time.monotonic) : l'envoi du message,
        # et l'arrivée du premier morceau de texte.
        self.envoi: float | None = None
        self.premier_token: float | None = None
        self._client: CopilotClient | None = None
        self._session = None
        self._boucle: asyncio.AbstractEventLoop | None = None
        self._file: asyncio.Queue | None = None
        self._inactif = asyncio.Event()
        self._inactif.set()
        self._compte = None  # joi.compte.integration.Integration, seulement si JOI_COMPTE est réglé
        # Les messages du modèle dans le tour en cours : celui des derniers morceaux de texte, et
        # combien ont produit du texte (deux ou trois messages collés se lisaient d'une traite).
        self._message: str | None = None
        self._messages = 0

    async def demarrer(self) -> None:
        self._boucle = asyncio.get_running_loop()
        consignes = config.FICHIER_PERSONNALITE.read_text(encoding="utf-8") + memoire.contexte_reveil()
        # Joi n'a qu'un outil, se_souvenir : pas de fichiers, pas de terminal.
        outils_permis = [memoire.outil_se_souvenir.name]
        permission = refuser_permission
        options = {}
        if config.JOI_COMPTE and self._avec_compte:
            # Le compte (optionnel) : ses outils MCP, ses consignes et le contexte du réveil. Sans
            # JOI_COMPTE, rien de joi.compte n'est importé et la session reste celle d'avant.
            from .compte.integration import Integration

            self._compte = Integration(repli=refuser_permission)
            self._client, contexte = await asyncio.gather(client_partage(), self._compte.contexte_reveil())
            consignes += self._compte.consignes() + contexte
            outils_permis += self._compte.outils_permis()
            permission = self._compte.permission
            options["mcp_servers"] = self._compte.serveurs_mcp()
        else:
            self._client = await client_partage()

        def nouvelle_session(client: CopilotClient):
            return client.create_session(
                tools=[memoire.outil_se_souvenir],
                available_tools=outils_permis,
                on_permission_request=permission,
                system_message={"mode": "replace", "content": consignes},
                model=self.modele,
                streaming=True,
                skip_custom_instructions=True,
                working_directory=str(config.DOSSIER_DONNEES),
                **options,
            )

        try:
            self._session = await nouvelle_session(self._client)
        except Exception as e:
            # Le client partagé ne répond plus (processus de la CLI arrêté ?) : on en démarre un neuf.
            # Seulement le type de l'erreur : son message pourrait reprendre la requête, donc les
            # consignes (mémoire, courriels), et le journal technique s'affiche au tableau de bord.
            log.warning("Session Copilot impossible sur le client partagé (%s) : nouveau client.",
                        type(e).__name__)
            self._client = await client_partage(neuf=True)
            self._session = await nouvelle_session(self._client)
        self._session.on(self._sur_evenement)

    async def arreter(self) -> None:
        """Ferme la session ; le client partagé reste ouvert pour la conversation suivante."""
        if self._session:
            await self._session.disconnect()

    def _sur_evenement(self, evenement) -> None:
        # Le SDK peut appeler ce gestionnaire depuis un autre fil : on repasse par la boucle asyncio.
        self._boucle.call_soon_threadsafe(self._traiter_evenement, evenement)

    def _traiter_evenement(self, evenement) -> None:
        genre = evenement.type
        if genre == SessionEventType.SESSION_IDLE:
            self._inactif.set()
        file = self._file
        if file is None:
            return
        if genre == SessionEventType.ASSISTANT_MESSAGE_DELTA:
            if evenement.data.delta_content:
                # On repère le changement de message par son identifiant plutôt que par
                # ASSISTANT_MESSAGE_START : un message sans texte (un appel d'outil seul) ne compte pas.
                identifiant = getattr(evenement.data, "message_id", None)
                if identifiant != self._message:
                    if self._messages:
                        file.put_nowait(NOUVEAU_MESSAGE)
                    self._message = identifiant
                    self._messages += 1
                file.put_nowait(evenement.data.delta_content)
        elif genre == SessionEventType.TOOL_EXECUTION_START:
            if evenement.data.tool_name == memoire.outil_se_souvenir.name:
                arguments = evenement.data.arguments or {}
                file.put_nowait(_Recherche(arguments.get("recherche", "") if isinstance(arguments, dict) else ""))
            elif self._compte:
                from .compte.integration import outil_du_compte

                outil = outil_du_compte(evenement.data.tool_name)
                if outil:
                    file.put_nowait(_Consultation(outil))
        elif genre == SessionEventType.SESSION_IDLE:
            file.put_nowait(FIN_DE_REPONSE)
        elif genre == SessionEventType.SESSION_ERROR:
            file.put_nowait(RuntimeError(evenement.data.message))

    async def repondre(self, texte: str, systeme: bool = False) -> AsyncIterator[str]:
        """Envoie ce que la personne a dit et produit la réponse de Joi, phrase par phrase.

        Quand Joi fouille sa mémoire, un marqueur RECHERCHE (isinstance) est produit entre les phrases ;
        quand elle consulte son compte, un marqueur CONSULTATION.
        systeme : le message ne vient pas de la personne (un courriel reçu, glissé par le serveur)."""
        self._file = asyncio.Queue()
        self._inactif.clear()
        self.envoi, self.premier_token = time.monotonic(), None
        self._message, self._messages = None, 0
        if systeme:
            await self._session.send(texte, source="system")
        else:
            if self._compte:
                # Ton tour de parole : c'est lui qui peut confirmer l'envoi d'un courriel.
                self._compte.noter_tour(texte)
            await self._session.send(texte)
        tampon = ""
        premiere = True  # rien n'est encore sorti : la première phrase peut être coupée plus tôt
        try:
            while True:
                morceau = await asyncio.wait_for(self._file.get(), timeout=120)
                if morceau is FIN_DE_REPONSE:
                    break
                if isinstance(morceau, Exception):
                    raise morceau
                if isinstance(morceau, (_Recherche, _Consultation)):
                    # Ce qui précède l'outil part d'abord : sinon il serait dit après la phrase
                    # d'attente, dans le désordre.
                    if tampon.strip():
                        premiere = False
                        yield tampon.strip()
                    tampon = ""
                    yield morceau
                    continue
                if morceau is NOUVEAU_MESSAGE:
                    # Le message précédent est fini : son reste part comme une phrase entière.
                    if tampon.strip():
                        premiere = False
                        yield tampon.strip()
                    tampon = ""
                    continue
                if self.premier_token is None:
                    self.premier_token = time.monotonic()
                tampon += morceau
                phrases, tampon = couper_phrases(tampon)
                if premiere and not phrases:
                    debut, tampon = couper_premiere(tampon)
                    phrases = [debut] if debut else []
                for phrase in phrases:
                    premiere = False
                    yield phrase
            if tampon.strip():
                yield tampon.strip()
            if self._messages > 1:
                log.info("Le modèle a envoyé %d messages dans ce tour.", self._messages)
        finally:
            self._file = None

    async def interrompre(self) -> None:
        """Coupe la réponse en cours (quand on parle par-dessus Joi)."""
        self._file = None
        if self._inactif.is_set():
            return
        try:
            await self._session.abort()
            await asyncio.wait_for(self._inactif.wait(), timeout=5)
        except Exception as e:  # une interruption ratée ne doit pas casser la conversation
            log.warning("Interruption incomplète : %s", e)
            self._inactif.set()
