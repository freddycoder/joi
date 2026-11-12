"""Le serveur de Joi : relie le navigateur (micro, haut-parleur, visuel) aux oreilles, au cerveau et à la voix.

Protocole du WebSocket /ws entre le navigateur et le serveur :

  navigateur -> serveur
    binaire                                  audio du micro, PCM 16 bits mono 16 kHz
    {"type": "texte", "texte": "..."}        message tapé au clavier (au lieu de parler) ; ignoré en mode écoute
    {"type": "mode", "mode": "..."}          demande de changer de mode : conversation | ecoute
                                             (on peut aussi le dire : voir joi/commande_vocale.py)

  serveur -> navigateur
    binaire                                  une phrase de Joi, en MP3
    {"type": "etat", "etat": "..."}          eveil | ecoute | reflechit | parle
    {"type": "transcription", "texte", "final"}   ce que la personne dit
    {"type": "reponse", "texte": "..."}      une phrase de Joi (sous-titre)
    {"type": "fin_reponse"}                  Joi a fini d'envoyer sa réponse
    {"type": "stop_audio"}                   couper l'audio en cours (interruption)
    {"type": "mode", "mode": "..."}          le mode en vigueur (envoyé au réveil et à chaque changement,
                                             même si le mode demandé était déjà actif)
    {"type": "erreur", "message": "..."}

Les deux modes :
  conversation   l'audio part en streaming vers AssemblyAI, Joi répond (mode au réveil)
  ecoute         Joi se tait : la session AssemblyAI est fermée (on ne paie pas) et l'audio est
                 enregistré sur le disque par joi/enregistreur.py. Au retour en conversation (ou à la
                 fermeture de l'onglet), joi/transcription.py le transcrit en fond, puis la mémoire
                 l'intègre.

On change de mode avec le bouton de l'interface, ou à la voix : « Joi, écoute seulement » est repéré
dans la transcription en streaming, « Joi, reviens » est reconnu hors ligne par Vosk.
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

from . import commande_vocale, config, journal, memoire, transcription, voix
from .cerveau import RECHERCHE, Cerveau
from .enregistreur import Enregistreur
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
    """Range les souvenirs : la conversation qui vient de finir, et les écoutes en attente."""
    _en_fond(memoire.consolider())
    _en_fond(transcription.apprendre())


async def _rattraper_plus_tard() -> None:
    """Rattrape les échanges non consolidés (par exemple si la fenêtre a été fermée brutalement)
    et les écoutes pas encore transcrites (Joi arrêtée pendant une écoute ou une transcription),
    mais seulement quand personne ne parle à Joi : sinon, la fin de la conversation s'en chargera."""
    await asyncio.sleep(DELAI_RATTRAPAGE)
    if _conversations_actives == 0:
        await asyncio.gather(memoire.consolider(), transcription.apprendre())


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
    # Télécharge le modèle de « Joi, reviens » dès maintenant (une seule fois), pour qu'il soit prêt
    # à la première écoute.
    _en_fond(commande_vocale.preparer())
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
        self.oreilles = self._nouvelles_oreilles()
        self.mode = "conversation"
        self.enregistreur: Enregistreur | None = None
        self.detecteur_retour: commande_vocale.DetecteurRetour | None = None
        self.tache_reponse: asyncio.Task | None = None
        self.tache_bascule: asyncio.Task | None = None
        self._verrou_mode = asyncio.Lock()
        self._verrou_envoi = asyncio.Lock()

    def _nouvelles_oreilles(self) -> Oreilles:
        return Oreilles(sur_tour=self.sur_tour, sur_erreur=self.signaler_erreur)

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
        await self.envoyer(type="mode", mode=self.mode)
        await self.envoyer(type="etat", etat="ecoute")

        try:
            while True:
                message = await self.ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes"):
                    if self.enregistreur:
                        self.enregistreur.ecrire(message["bytes"])
                        if self.detecteur_retour:
                            self.detecteur_retour.ecouter(message["bytes"])
                    else:
                        await self.oreilles.envoyer_audio(message["bytes"])
                elif message.get("text"):
                    await self.traiter_commande(message["text"])
        except WebSocketDisconnect:
            pass
        finally:
            if self.tache_bascule:
                self.tache_bascule.cancel()
            await self.interrompre()
            await self._arreter_detecteur()
            # Fermer l'onglet en mode écoute finalise le segment en cours.
            self._arreter_enregistrement()
            await self.oreilles.fermer()
            await self.cerveau.arreter()
            # La conversation est finie : Joi range ses souvenirs (et transcrit l'écoute, s'il y en a une).
            lancer_consolidation()

    async def traiter_commande(self, brut: str) -> None:
        commande = json.loads(brut)
        genre = commande.get("type")
        if genre == "texte" and commande.get("texte", "").strip():
            if self.mode == "conversation":
                await self.sur_tour(Tour(commande["texte"].strip(), True, -1))
        elif genre == "mode" and commande.get("mode") in ("conversation", "ecoute"):
            await self.changer_mode(commande["mode"])

    async def changer_mode(self, mode: str) -> None:
        # Le bouton et la voix peuvent demander un changement en même temps : un seul à la fois.
        async with self._verrou_mode:
            if mode != self.mode:
                if mode == "ecoute":
                    await self._passer_en_ecoute()
                else:
                    await self._revenir_en_conversation()
            await self.envoyer(type="mode", mode=self.mode)

    def _basculer_en_fond(self, mode: str, confirmation: str | None = None) -> None:
        """Change de mode dans une tâche à part : une commande vocale arrive des oreilles (ou du
        détecteur), qu'on doit justement fermer pendant le changement."""
        if self.tache_bascule and not self.tache_bascule.done():
            return

        async def basculer() -> None:
            await self.changer_mode(mode)
            if confirmation and self.mode == mode:
                await self.dire_seule(confirmation)

        self.tache_bascule = asyncio.create_task(basculer())

    async def _retour_vocal(self) -> None:
        # Joi confirme à voix haute : on ne regarde pas forcément l'écran quand on la rappelle.
        self._basculer_en_fond("conversation", confirmation="Je suis là.")

    async def dire_seule(self, phrase: str) -> None:
        """Une courte phrase de Joi, hors d'une réponse du cerveau."""
        try:
            audio = await voix.synthetiser(phrase)
        except Exception as e:
            log.warning("Synthèse impossible : %s", e)
            return
        if audio:
            await self.envoyer(type="reponse", texte=phrase)
            await self.envoyer(type="etat", etat="parle")
            await self.envoyer_audio(audio)
            await self.envoyer(type="fin_reponse")

    async def _passer_en_ecoute(self) -> None:
        # Le mode change d'abord : un tour final qui arriverait pendant la fermeture des oreilles
        # ne doit pas déclencher de réponse.
        self.mode = "ecoute"
        await self.interrompre()
        await self.oreilles.fermer()
        enregistreur = Enregistreur()
        try:
            enregistreur.ouvrir()
        except OSError as e:
            log.exception("Enregistrement impossible")
            await self.signaler_erreur(f"Je n'arrive pas à enregistrer : {e}")
            await self._revenir_en_conversation()
            return
        self.enregistreur = enregistreur
        self.detecteur_retour = commande_vocale.DetecteurRetour(sur_retour=self._retour_vocal)
        self.detecteur_retour.demarrer()
        await self.envoyer(type="etat", etat="ecoute")

    async def _revenir_en_conversation(self) -> None:
        await self._arreter_detecteur()
        if self._arreter_enregistrement():
            # L'écoute est finie : Joi l'apprend en fond pendant que la conversation reprend.
            _en_fond(transcription.apprendre())
        self.mode = "conversation"
        await self.envoyer(type="etat", etat="eveil")
        # Une nouvelle session AssemblyAI recommence la numérotation des tours : nouvelles oreilles.
        self.oreilles = self._nouvelles_oreilles()
        try:
            await self.oreilles.ouvrir()
        except Exception as e:
            log.exception("Réouverture des oreilles impossible")
            await self.signaler_erreur(f"Je n'arrive plus à t'entendre : {e}")
        await self.envoyer(type="etat", etat="ecoute")

    async def _arreter_detecteur(self) -> None:
        detecteur, self.detecteur_retour = self.detecteur_retour, None
        if detecteur:
            await detecteur.arreter()

    def _arreter_enregistrement(self) -> bool:
        """Finalise la séance d'écoute en cours. Renvoie True s'il y en avait une."""
        enregistreur, self.enregistreur = self.enregistreur, None
        if not enregistreur:
            return False
        try:
            enregistreur.fermer()
        except Exception:
            log.exception("Finalisation de l'enregistrement impossible")
        return True

    async def sur_tour(self, tour: Tour) -> None:
        if self.mode != "conversation":
            return
        await self.envoyer(type="transcription", texte=tour.texte, final=tour.final)

        if not tour.final:
            # On parle par-dessus Joi : elle se tait.
            if len(tour.texte.split()) >= MOTS_POUR_INTERROMPRE:
                await self.interrompre()
            return

        if not tour.texte:
            return
        if commande_vocale.demande_ecoute(tour.texte):
            log.info("Commande vocale : « %s »", tour.texte)
            self._basculer_en_fond("ecoute")
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
