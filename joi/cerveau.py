"""Le cerveau de Joi : une session GitHub Copilot (via le Copilot SDK) qui porte sa personnalité.

La réponse est diffusée en continu (streaming) et découpée en phrases, pour que la voix
puisse commencer à parler avant que la réponse complète soit écrite.
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

    async def demarrer(self) -> None:
        self._boucle = asyncio.get_running_loop()
        self._client = CopilotClient()
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
            _, contexte = await asyncio.gather(self._client.start(), self._compte.contexte_reveil())
            consignes += self._compte.consignes() + contexte
            outils_permis += self._compte.outils_permis()
            permission = self._compte.permission
            options["mcp_servers"] = self._compte.serveurs_mcp()
        else:
            await self._client.start()
        self._session = await self._client.create_session(
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
        self._session.on(self._sur_evenement)

    async def arreter(self) -> None:
        if self._session:
            await self._session.disconnect()
        if self._client:
            await self._client.stop()

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
        if systeme:
            await self._session.send(texte, source="system")
        else:
            if self._compte:
                # Ton tour de parole : c'est lui qui peut confirmer l'envoi d'un courriel.
                self._compte.noter_tour(texte)
            await self._session.send(texte)
        tampon = ""
        try:
            while True:
                morceau = await asyncio.wait_for(self._file.get(), timeout=120)
                if morceau is FIN_DE_REPONSE:
                    break
                if isinstance(morceau, Exception):
                    raise morceau
                if isinstance(morceau, (_Recherche, _Consultation)):
                    yield morceau
                    continue
                if self.premier_token is None:
                    self.premier_token = time.monotonic()
                tampon += morceau
                phrases, tampon = couper_phrases(tampon)
                for phrase in phrases:
                    yield phrase
            if tampon.strip():
                yield tampon.strip()
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
