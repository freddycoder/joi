"""Le serveur de Joi : relie le navigateur (micro, haut-parleur, visuel) aux oreilles, au cerveau et à la voix.

Protocole du WebSocket /ws entre le navigateur et le serveur :

  navigateur -> serveur
    binaire                                  audio du micro, PCM 16 bits mono 16 kHz
    {"type": "texte", "texte": "..."}        message tapé au clavier (au lieu de parler)

  serveur -> navigateur
    binaire                                  une phrase de Joi, en MP3
    {"type": "etat", "etat": "..."}          eveil | ecoute | reflechit | parle
    {"type": "transcription", "texte", "final"}   ce que la personne dit
    {"type": "reponse", "texte": "..."}      une phrase de Joi (sous-titre)
    {"type": "fin_reponse"}                  Joi a fini d'envoyer sa réponse
    {"type": "stop_audio"}                   couper l'audio en cours (interruption)
    {"type": "erreur", "message": "..."}
"""

import asyncio
import json
import logging
import random
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, journal, memoire, voix
from .cerveau import RECHERCHE, Cerveau
from .oreilles import Oreilles, Tour

log = logging.getLogger("joi.serveur")

# Nombre de mots entendus pendant que Joi parle avant de considérer qu'on l'interrompt.
MOTS_POUR_INTERROMPRE = 2

ORIGINES_AUTORISEES = {
    f"http://localhost:{config.JOI_PORT}",
    f"http://127.0.0.1:{config.JOI_PORT}",
}

# Ce que Joi dit tout de suite quand elle fouille sa mémoire, pour que l'attente ne paraisse pas.
PHRASES_DE_RECHERCHE = [
    "Hmm, laisse-moi y repenser…",
    "Attends, ça me dit quelque chose…",
    "Une seconde, je fouille dans mes souvenirs…",
    "Laisse-moi me rappeler…",
]

# Délai avant de rattraper, au démarrage, les échanges non consolidés : la consolidation lance
# une deuxième session Copilot, qui ralentirait le réveil de Joi et ses premières réponses.
DELAI_RATTRAPAGE = 300  # secondes

# Tâches de fond (consolidation de la mémoire) : on garde une référence pour qu'elles aillent au bout.
_taches_de_fond: set[asyncio.Task] = set()
_conversations_actives = 0


def _en_fond(coroutine) -> None:
    tache = asyncio.create_task(coroutine)
    _taches_de_fond.add(tache)
    tache.add_done_callback(_taches_de_fond.discard)


def lancer_consolidation() -> None:
    _en_fond(memoire.consolider())


async def _rattraper_plus_tard() -> None:
    """Rattrape les échanges non consolidés (par exemple si la fenêtre a été fermée brutalement),
    mais seulement quand personne ne parle à Joi : sinon, la fin de la conversation s'en chargera."""
    await asyncio.sleep(DELAI_RATTRAPAGE)
    if _conversations_actives == 0:
        await memoire.consolider()


async def _prechauffer_voix() -> None:
    """Synthétise d'avance les phrases de recherche (elles restent ensuite en cache sur le disque)."""
    for phrase in PHRASES_DE_RECHERCHE:
        try:
            await voix.synthetiser(phrase)
        except Exception as e:
            log.warning("Préchauffage de la voix impossible : %s", e)
            return


@asynccontextmanager
async def cycle_de_vie(_app: FastAPI):
    memoire.initialiser()
    _en_fond(_rattraper_plus_tard())
    _en_fond(_prechauffer_voix())
    yield


app = FastAPI(title="Joi", lifespan=cycle_de_vie)
app.mount("/static", StaticFiles(directory=config.DOSSIER_FRONTEND), name="static")


@app.get("/")
async def accueil():
    return FileResponse(config.DOSSIER_FRONTEND / "index.html")


@app.websocket("/ws")
async def websocket_joi(ws: WebSocket):
    # Seule la page de Joi peut se connecter : sans ce contrôle, n'importe quel site ouvert
    # dans le navigateur pourrait utiliser ce WebSocket local (et tes quotas Copilot / AssemblyAI).
    if ws.headers.get("origin") not in ORIGINES_AUTORISEES:
        await ws.close(code=1008)
        return
    await ws.accept()
    await Conversation(ws).executer()


class Conversation:
    """Une conversation entre une personne (un onglet du navigateur) et Joi."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.cerveau = Cerveau()
        self.oreilles = Oreilles(sur_tour=self.sur_tour, sur_erreur=self.signaler_erreur)
        self.tache_reponse: asyncio.Task | None = None
        self._verrou_envoi = asyncio.Lock()

    async def envoyer(self, **message) -> None:
        async with self._verrou_envoi:
            await self.ws.send_json(message)

    async def envoyer_audio(self, audio: bytes) -> None:
        async with self._verrou_envoi:
            await self.ws.send_bytes(audio)

    async def signaler_erreur(self, message: str) -> None:
        try:
            await self.envoyer(type="erreur", message=message)
        except Exception:
            pass

    async def executer(self) -> None:
        global _conversations_actives
        _conversations_actives += 1
        try:
            await self._executer()
        finally:
            _conversations_actives -= 1

    async def _executer(self) -> None:
        if not config.ASSEMBLYAI_API_KEY:
            await self.signaler_erreur("ASSEMBLYAI_API_KEY manquante dans le fichier .env")
            await self.ws.close()
            return

        await self.envoyer(type="etat", etat="eveil")
        debut = time.perf_counter()
        try:
            await asyncio.gather(self.cerveau.demarrer(), self.oreilles.ouvrir())
        except Exception as e:
            log.exception("Réveil impossible")
            await self.signaler_erreur(f"Je n'arrive pas à me réveiller : {e}")
            await self.ws.close()
            return
        log.info("Joi est réveillée en %.1f s", time.perf_counter() - debut)
        await self.envoyer(type="etat", etat="ecoute")

        try:
            while True:
                message = await self.ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes"):
                    await self.oreilles.envoyer_audio(message["bytes"])
                elif message.get("text"):
                    await self.traiter_commande(message["text"])
        except WebSocketDisconnect:
            pass
        finally:
            await self.interrompre()
            await self.oreilles.fermer()
            await self.cerveau.arreter()
            # La conversation est finie : Joi range ses souvenirs.
            lancer_consolidation()

    async def traiter_commande(self, brut: str) -> None:
        commande = json.loads(brut)
        if commande.get("type") == "texte" and commande.get("texte", "").strip():
            await self.sur_tour(Tour(commande["texte"].strip(), True, -1))

    async def sur_tour(self, tour: Tour) -> None:
        await self.envoyer(type="transcription", texte=tour.texte, final=tour.final)

        if not tour.final:
            # On parle par-dessus Joi : elle se tait.
            if len(tour.texte.split()) >= MOTS_POUR_INTERROMPRE:
                await self.interrompre()
            return

        if not tour.texte:
            return
        await self.interrompre()
        self.tache_reponse = asyncio.create_task(self.repondre(tour.texte))

    async def interrompre(self) -> None:
        tache = self.tache_reponse
        self.tache_reponse = None
        if tache and not tache.done():
            tache.cancel()
            try:
                await tache
            except asyncio.CancelledError:
                pass
            await self.cerveau.interrompre()
        try:
            await self.envoyer(type="stop_audio")
        except Exception:
            pass

    async def repondre(self, texte: str) -> None:
        journal.noter("toi", texte)
        await self.envoyer(type="etat", etat="reflechit")
        a_parle = False
        dites: list[str] = []
        termine = False
        debut = time.perf_counter()
        premiere_phrase: float | None = None
        recherches = 0

        async def dire(phrase: str) -> None:
            nonlocal a_parle
            await self.envoyer(type="reponse", texte=voix.nettoyer(phrase))
            audio = await voix.synthetiser(phrase)
            if audio:
                if not a_parle:
                    await self.envoyer(type="etat", etat="parle")
                    a_parle = True
                await self.envoyer_audio(audio)

        try:
            async for morceau in self.cerveau.repondre(texte):
                if isinstance(morceau, RECHERCHE):
                    recherches += 1
                    log.info("Joi fouille sa mémoire (%.1f s) : %r",
                             time.perf_counter() - debut, str(morceau))
                    # Une seule phrase d'attente, et seulement si elle n'a encore rien dit.
                    if recherches == 1 and not dites:
                        await dire(random.choice(PHRASES_DE_RECHERCHE))
                    continue
                if premiere_phrase is None:
                    premiere_phrase = time.perf_counter() - debut
                dites.append(morceau)
                await dire(morceau)
            termine = True
            log.info(
                "Réponse : première phrase en %s, complète en %.1f s, %d recherche(s) en mémoire",
                f"{premiere_phrase:.1f} s" if premiere_phrase is not None else "—",
                time.perf_counter() - debut, recherches,
            )
            await self.envoyer(type="fin_reponse")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("Erreur pendant la réponse")
            await self.signaler_erreur(f"Oups, j'ai perdu le fil : {e}")
            await self.envoyer(type="etat", etat="ecoute")
        finally:
            journal.noter("joi", " ".join(dites), interrompu=not termine)
