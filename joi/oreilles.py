"""Les oreilles de Joi : transcription en temps réel avec AssemblyAI (Streaming API v3).

Fonctionnement :
1. On ouvre un WebSocket vers AssemblyAI en précisant le format audio et le modèle.
2. On y pousse l'audio brut du micro (PCM 16 bits, mono, 16 kHz) par petits paquets de ~50 ms.
3. AssemblyAI renvoie des messages JSON :
   - « Begin »       : la session est ouverte (on y vérifie la configuration acceptée) ;
   - « Turn »        : la transcription du tour de parole en cours. Tant que end_of_turn est faux,
                       c'est une transcription partielle ; quand il passe à vrai, la personne a fini de parler ;
   - « Termination » : la session est terminée.

Les mots d'un tour final (words[], start/end en ms depuis le début de la session) accompagnent le
Tour : avec le tampon des dernières secondes envoyées (TamponAudio), le serveur retrouve l'audio du
tour pour apprendre la voix de la personne (voir joi/empreintes.py). Ce tampon ne vit qu'en mémoire.

Une coupure qui ne vient pas de fermer() (AssemblyAI ferme une session après 3 h, code 3008, ou
après une erreur de son côté ; le réseau tombe) est signalée par sur_coupure(code, raison) : la
conversation recrée alors des oreilles (voir joi/serveur.py). coupure_recuperable() dit si une
nouvelle session a une chance de marcher. Codes vérifiés le 2026-11-17 dans la documentation
d'AssemblyAI (« Common session errors and closures »).
"""

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import urlencode

import websockets
from websockets.exceptions import ConnectionClosed

from . import config

log = logging.getLogger("joi.oreilles")

URL_STREAMING = "wss://streaming.assemblyai.com/v3/ws"
TAUX_ECHANTILLONNAGE = 16_000
OCTETS_PAR_MS = TAUX_ECHANTILLONNAGE * 2 // 1000  # PCM 16 bits mono
DUREE_TAMPON_S = 60


@dataclass
class Tour:
    """Un tour de parole transcrit."""

    texte: str
    final: bool
    ordre: int
    mots: list[dict] = field(default_factory=list)  # tour final : mots avec start/end (ms de session)
    # Tour final, pour mesurer la détection de fin de tour (time.monotonic) : l'arrivée du tour, et
    # celle de l'audio de la fin du dernier mot (None si elle n'est plus dans le tampon).
    recu: float | None = None
    fin_parole: float | None = None


def coupure_recuperable(code: int | None, raison: str = "") -> bool:
    """Vrai si une nouvelle session a une chance de marcher après cette fermeture. Faux quand le
    problème reviendrait à l'identique : clé refusée ou compte bloqué (1008, sauf trop de sessions
    à la fois), message invalide (3006, sauf l'inactivité), paquets audio mal formés (3007)."""
    raison = (raison or "").lower()
    if code == 1008:
        return "concurren" in raison or "too many" in raison
    if code == 3006:
        return "inactiv" in raison
    if code == 3007:
        return False
    return True  # 1000, 1006 (réseau), 1011, 3005 (erreur du serveur), 3008 (3 h atteintes)…


def _code_et_raison(e: ConnectionClosed) -> tuple[int, str]:
    if e.rcvd is None:
        return 1006, "connexion perdue sans fermeture propre"
    return e.rcvd.code, e.rcvd.reason


class TamponAudio:
    """Les dernières secondes d'audio envoyées à AssemblyAI. Les horodatages d'AssemblyAI comptent
    en ms depuis le début de la session : on garde la position de chaque paquet dans le flux, et
    l'instant où il est arrivé (pour savoir si Joi parlait à ce moment-là)."""

    def __init__(self, secondes: float = DUREE_TAMPON_S):
        self._max = int(secondes * 1000 * OCTETS_PAR_MS)
        self._paquets: deque[tuple[int, float, bytes]] = deque()  # (position en octets, instant, audio)
        self._total = 0
        self._taille = 0

    def ajouter(self, pcm: bytes) -> None:
        self._paquets.append((self._total, time.monotonic(), pcm))
        self._total += len(pcm)
        self._taille += len(pcm)
        while self._paquets and self._taille - len(self._paquets[0][2]) >= self._max:
            self._taille -= len(self._paquets.popleft()[2])

    def extraire(self, debut_ms: int, fin_ms: int) -> bytes | None:
        """L'audio entre deux instants de la session, ou None s'il n'est plus dans le tampon."""
        if not self._paquets:
            return None
        a, b = max(debut_ms, 0) * OCTETS_PAR_MS, fin_ms * OCTETS_PAR_MS
        if a < self._paquets[0][0] or b <= a:
            return None
        morceaux = []
        for position, _instant, pcm in self._paquets:
            if position + len(pcm) <= a or position >= b:
                continue
            morceaux.append(pcm[max(a - position, 0):min(b - position, len(pcm))])
        audio = b"".join(morceaux)
        return audio[:len(audio) - len(audio) % 2]

    def instant(self, ms: int) -> float | None:
        """L'instant (time.monotonic) où l'audio de ce moment de la session est arrivé."""
        position = ms * OCTETS_PAR_MS
        for debut, instant, pcm in self._paquets:
            if debut <= position < debut + len(pcm):
                return instant
        return None


class Oreilles:
    def __init__(
        self,
        sur_tour: Callable[[Tour], Awaitable[None]],
        sur_erreur: Callable[[str], Awaitable[None]],
        sur_coupure: Callable[[int | None, str], Awaitable[None]] | None = None,
    ):
        self._sur_tour = sur_tour
        self._sur_erreur = sur_erreur
        self.sur_coupure = sur_coupure
        self._ws = None
        self._tache_ecoute: asyncio.Task | None = None
        self._dernier_tour_final = -1
        self._erreur_signalee = False
        self._derniere_erreur = ""  # le message d'erreur d'AssemblyAI, plus parlant que la raison de fermeture
        self._fermeture_voulue = False
        # La session est tombée sans qu'on la ferme (ou n'a pas pu s'ouvrir) : l'audio n'est plus
        # envoyé. recuperable : une nouvelle session a une chance de marcher (coupure_recuperable).
        self.coupee = False
        self.recuperable = True
        self.tampon = TamponAudio()

    async def ouvrir(self) -> None:
        parametres = {
            "sample_rate": TAUX_ECHANTILLONNAGE,
            "encoding": "pcm_s16le",
            "speech_model": config.ASSEMBLYAI_MODELE,
            "language_codes": json.dumps(["fr"]),
            # Mots que la reconnaissance doit privilégier (sinon « Joi » devient « joie »,
            # « Lysdor » devient « list d'or »...). Voir JOI_MOTS_CLES dans .env.
            "keyterms_prompt": json.dumps(config.MOTS_CLES),
        }
        url = f"{URL_STREAMING}?{urlencode(parametres)}"
        # La clé passe dans l'en-tête Authorization (sans « Bearer »), jamais dans l'URL.
        try:
            self._ws = await websockets.connect(
                url, additional_headers={"Authorization": config.ASSEMBLYAI_API_KEY}
            )
        except BaseException:
            self.coupee = True
            raise
        self._tache_ecoute = asyncio.create_task(self._ecouter())

    async def envoyer_audio(self, pcm: bytes) -> None:
        if self._ws is None or self.coupee:
            return
        try:
            await self._ws.send(pcm)
        except ConnectionClosed as e:
            # La réception le verra aussi ; le premier des deux prévient la conversation.
            await self._signaler_coupure(*_code_et_raison(e))
            return
        self.tampon.ajouter(pcm)

    async def fermer(self) -> None:
        self._fermeture_voulue = True
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
        code, raison = None, ""
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
                    try:
                        await self._traiter_tour(message)
                    except Exception:  # un tour mal traité ne doit pas rendre Joi sourde
                        log.exception("Tour de parole mal traité")
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
                    self._derniere_erreur = str(detail)
                    log.error("Erreur AssemblyAI : %s", detail)
                    await self._sur_erreur(f"AssemblyAI : {detail}")
                else:
                    log.debug("Message AssemblyAI ignoré : %s", message)
        except ConnectionClosed as e:
            code, raison = _code_et_raison(e)
            # Les erreurs d'AssemblyAI (clé invalide, paramètre refusé...) arrivent
            # sous forme de fermeture du WebSocket avec un code et une raison.
            if not self._erreur_signalee and not self._fermeture_voulue and code != 1000:
                message = f"AssemblyAI a fermé la connexion ({code}) : {raison}"
                log.error(message)
                await self._sur_erreur(message)
        except Exception as e:  # message illisible : on ferme plutôt que de rester sourde sans le dire
            log.exception("Réception d'AssemblyAI interrompue")
            code, raison = None, str(e)
            await self._ws.close()
        if code is None and self._ws is not None and self._ws.close_code is not None:
            code, raison = self._ws.close_code, self._ws.close_reason or raison
        # Une fin qui ne vient pas de fermer() : session trop longue, erreur, réseau. Même une fin
        # « normale » (Termination non demandée, code 1000) laisse Joi sourde : on le signale.
        await self._signaler_coupure(code, self._derniere_erreur or raison)

    async def _signaler_coupure(self, code: int | None, raison: str) -> None:
        if self._fermeture_voulue or self.coupee:
            return
        self.coupee = True
        self.recuperable = coupure_recuperable(code, raison)
        log.warning("Oreilles coupées (code %s) : %s", code, raison or "sans raison donnée")
        if self.sur_coupure:
            try:
                await self.sur_coupure(code, raison)
            except Exception:
                log.exception("Coupure des oreilles mal signalée")

    async def _traiter_tour(self, message: dict) -> None:
        ordre = message.get("turn_order", 0)
        if message.get("end_of_turn"):
            # Un même tour peut être confirmé deux fois (brut puis formaté) : on ne garde que le premier.
            if ordre <= self._dernier_tour_final:
                return
            self._dernier_tour_final = ordre
            texte = message.get("utterance") or message.get("transcript", "")
            mots = message.get("words") or []
            # L'instant où l'audio de la fin du dernier mot est arrivé : la différence avec maintenant
            # est le temps qu'AssemblyAI a mis à décider que la personne avait fini de parler.
            fin_parole = self.tampon.instant(max(int(mots[-1].get("end") or 0) - 1, 0)) if mots else None
            await self._sur_tour(Tour(texte.strip(), True, ordre, mots, time.monotonic(), fin_parole))
        else:
            # Transcription partielle : les mots déjà confirmés + ceux encore en cours.
            mots = message.get("words") or []
            texte = " ".join(m["text"] for m in mots) or message.get("transcript", "")
            if texte.strip():
                await self._sur_tour(Tour(texte.strip(), False, ordre))
