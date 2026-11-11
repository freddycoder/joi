"""Le cerveau de Joi : une session GitHub Copilot (via le Copilot SDK) qui porte sa personnalité.

La réponse est diffusée en continu (streaming) et découpée en phrases, pour que la voix
puisse commencer à parler avant que la réponse complète soit écrite.
"""

import asyncio
import logging
import re
from typing import AsyncIterator

from copilot import CopilotClient
from copilot.generated.rpc import PermissionDecisionReject
from copilot.session_events import SessionEventType

from . import config

log = logging.getLogger("joi.cerveau")

# Une fin de phrase : ponctuation forte suivie d'un espace, ou un saut de ligne.
FIN_DE_PHRASE = re.compile(r"(?<=[.!?…])\s+|\n+")
FIN_DE_REPONSE = object()


def couper_phrases(tampon: str) -> tuple[list[str], str]:
    """Sépare les phrases complètes du reste encore en cours d'écriture."""
    morceaux = FIN_DE_PHRASE.split(tampon)
    completes = [m.strip() for m in morceaux[:-1] if m.strip()]
    return completes, morceaux[-1]


def refuser_permission(requete, invocation):
    """Joi ne fait que converser : toute demande d'action (fichiers, terminal...) est refusée."""
    log.warning("Permission refusée : %s", getattr(requete, "kind", requete))
    return PermissionDecisionReject(feedback="Joi n'a pas le droit d'utiliser d'outils.")


class Cerveau:
    def __init__(self):
        self._client: CopilotClient | None = None
        self._session = None
        self._boucle: asyncio.AbstractEventLoop | None = None
        self._file: asyncio.Queue | None = None
        self._inactif = asyncio.Event()
        self._inactif.set()

    async def demarrer(self) -> None:
        self._boucle = asyncio.get_running_loop()
        self._client = CopilotClient()
        await self._client.start()
        self._session = await self._client.create_session(
            # Joi n'a aucun outil (fichiers, terminal...) : elle ne fait que converser.
            available_tools=[],
            on_permission_request=refuser_permission,
            system_message={
                "mode": "replace",
                "content": config.FICHIER_PERSONNALITE.read_text(encoding="utf-8"),
            },
            model=config.JOI_MODELE,
            streaming=True,
            skip_custom_instructions=True,
            working_directory=str(config.DOSSIER_DONNEES),
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
        elif genre == SessionEventType.SESSION_IDLE:
            file.put_nowait(FIN_DE_REPONSE)
        elif genre == SessionEventType.SESSION_ERROR:
            file.put_nowait(RuntimeError(evenement.data.message))

    async def repondre(self, texte: str) -> AsyncIterator[str]:
        """Envoie ce que la personne a dit et produit la réponse de Joi, phrase par phrase."""
        self._file = asyncio.Queue()
        self._inactif.clear()
        await self._session.send(texte)
        tampon = ""
        try:
            while True:
                morceau = await asyncio.wait_for(self._file.get(), timeout=120)
                if morceau is FIN_DE_REPONSE:
                    break
                if isinstance(morceau, Exception):
                    raise morceau
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
