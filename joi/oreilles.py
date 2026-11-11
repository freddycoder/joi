"""Les oreilles de Joi : transcription en temps réel avec AssemblyAI (Streaming API v3).

Fonctionnement :
1. On ouvre un WebSocket vers AssemblyAI en précisant le format audio et le modèle.
2. On y pousse l'audio brut du micro (PCM 16 bits, mono, 16 kHz) par petits paquets de ~50 ms.
3. AssemblyAI renvoie des messages JSON :
   - « Begin »       : la session est ouverte (on y vérifie la configuration acceptée) ;
   - « Turn »        : la transcription du tour de parole en cours. Tant que end_of_turn est faux,
                       c'est une transcription partielle ; quand il passe à vrai, la personne a fini de parler ;
   - « Termination » : la session est terminée.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlencode

import websockets
from websockets.exceptions import ConnectionClosed

from . import config

log = logging.getLogger("joi.oreilles")

URL_STREAMING = "wss://streaming.assemblyai.com/v3/ws"
TAUX_ECHANTILLONNAGE = 16_000
MOTS_CLES = ["Joi"]


@dataclass
class Tour:
    """Un tour de parole transcrit."""

    texte: str
    final: bool
    ordre: int


class Oreilles:
    def __init__(
        self,
        sur_tour: Callable[[Tour], Awaitable[None]],
        sur_erreur: Callable[[str], Awaitable[None]],
    ):
        self._sur_tour = sur_tour
        self._sur_erreur = sur_erreur
        self._ws = None
        self._tache_ecoute: asyncio.Task | None = None
        self._dernier_tour_final = -1
        self._erreur_signalee = False

    async def ouvrir(self) -> None:
        parametres = {
            "sample_rate": TAUX_ECHANTILLONNAGE,
            "encoding": "pcm_s16le",
            "speech_model": config.ASSEMBLYAI_MODELE,
            "language_codes": json.dumps(["fr"]),
            # Mots que la reconnaissance doit privilégier (sinon « Joi » devient « joie »).
            "keyterms_prompt": json.dumps(MOTS_CLES),
        }
        url = f"{URL_STREAMING}?{urlencode(parametres)}"
        # La clé passe dans l'en-tête Authorization (sans « Bearer »), jamais dans l'URL.
        self._ws = await websockets.connect(
            url, additional_headers={"Authorization": config.ASSEMBLYAI_API_KEY}
        )
        self._tache_ecoute = asyncio.create_task(self._ecouter())

    async def envoyer_audio(self, pcm: bytes) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(pcm)
        except ConnectionClosed:
            pass

    async def fermer(self) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": "Terminate"}))
            if self._tache_ecoute:
                await asyncio.wait_for(self._tache_ecoute, timeout=3)
        except (ConnectionClosed, asyncio.TimeoutError):
            pass
        finally:
            await self._ws.close()
            self._ws = None

    async def _ecouter(self) -> None:
        try:
            async for brut in self._ws:
                message = json.loads(brut)
                genre = message.get("type")

                if genre == "Begin":
                    log.info(
                        "Session AssemblyAI ouverte (%s), configuration : %s",
                        message.get("id"),
                        message.get("configuration"),
                    )
                elif genre == "Turn":
                    await self._traiter_tour(message)
                elif genre == "Termination":
                    log.info(
                        "Session AssemblyAI terminée (%s s d'audio)",
                        message.get("audio_duration_seconds"),
                    )
                    break
                elif "error" in message or genre == "Error":
                    # AssemblyAI explique le problème dans un message juste avant de fermer.
                    detail = message.get("error") or message.get("message") or message
                    self._erreur_signalee = True
                    log.error("Erreur AssemblyAI : %s", detail)
                    await self._sur_erreur(f"AssemblyAI : {detail}")
                else:
                    log.debug("Message AssemblyAI ignoré : %s", message)
        except ConnectionClosed as e:
            if self._erreur_signalee:
                return
            # Les erreurs d'AssemblyAI (clé invalide, paramètre refusé...) arrivent
            # sous forme de fermeture du WebSocket avec un code et une raison.
            if e.rcvd and e.rcvd.code != 1000:
                raison = f"AssemblyAI a fermé la connexion ({e.rcvd.code}) : {e.rcvd.reason}"
                log.error(raison)
                await self._sur_erreur(raison)

    async def _traiter_tour(self, message: dict) -> None:
        ordre = message.get("turn_order", 0)
        if message.get("end_of_turn"):
            # Un même tour peut être confirmé deux fois (brut puis formaté) : on ne garde que le premier.
            if ordre <= self._dernier_tour_final:
                return
            self._dernier_tour_final = ordre
            texte = message.get("utterance") or message.get("transcript", "")
            await self._sur_tour(Tour(texte.strip(), True, ordre))
        else:
            # Transcription partielle : les mots déjà confirmés + ceux encore en cours.
            mots = message.get("words") or []
            texte = " ".join(m["text"] for m in mots) or message.get("transcript", "")
            if texte.strip():
                await self._sur_tour(Tour(texte.strip(), False, ordre))
