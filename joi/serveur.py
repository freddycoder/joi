"""Le serveur de Joi : relie le navigateur (micro, haut-parleur, visuel) aux oreilles, au cerveau et à la voix.

Protocole du WebSocket /ws entre le navigateur et le serveur :

  navigateur -> serveur
    binaire                                  audio du micro, PCM 16 bits mono 16 kHz
    {"type": "texte", "texte": "..."}        message tapé au clavier (au lieu de parler) ; ignoré en mode écoute
    {"type": "mode", "mode": "..."}          demande de changer de mode : conversation | ecoute
                                             (on peut aussi le dire : voir joi/commande_vocale.py)
    {"type": "lecture", "en_cours": bool}    la voix de Joi commence ou finit de jouer dans le
                                             haut-parleur (pour ne pas apprendre sa voix comme la tienne)

  serveur -> navigateur
    binaire                                  une phrase de Joi, en MP3
    {"type": "etat", "etat": "..."}          eveil | ecoute | reflechit | parle
    {"type": "transcription", "texte", "final"}   ce que la personne dit
    {"type": "reponse", "texte": "..."}      une phrase de Joi (sous-titre)
    {"type": "fin_reponse"}                  Joi a fini d'envoyer sa réponse
    {"type": "stop_audio"}                   couper l'audio en cours (interruption)
    {"type": "mode", "mode": "..."}          le mode en vigueur (envoyé au réveil et à chaque changement,
                                             même si le mode demandé était déjà actif)
    {"type": "erreur", "message": "..."}     un problème, ou la fin d'un problème (« Je t'entends de nouveau »)

Les coupures :
  - le navigateur perd le WebSocket alors que Joi était réveillée (serveur relancé, mise en veille) :
    il se reconnecte seul, avec un délai croissant (1, 2, 5, 10, puis 30 s), et redemande le mode
    écoute s'il y était. Le serveur repart toujours en conversation : une nouvelle séance d'écoute
    commence, l'ancienne a été finalisée à la fermeture du WebSocket (puis transcrite) ;
  - AssemblyAI ferme la session (3 h au plus, erreur, réseau) : en conversation, de nouvelles oreilles
    avec un délai croissant (_reconnecter_oreilles), annoncé par un message « erreur » ;
  - en écoute, plus aucun audio pendant SILENCE_MAX_ECOUTE secondes alors que le WebSocket est
    ouvert (micro coupé, onglet gelé) : message « erreur » et avertissement au journal technique.

Routes HTTP du tableau de bord (panneaux latéraux de l'interface, voir joi/monitoring.py) :
    GET /api/parametres     les réglages en vigueur (sans la clé API)
    GET /api/monitoring     l'état des coulisses : voix, transcription, mémoire, écoute, compte, événements

Le compte de Joi (optionnel, JOI_COMPTE ; voir joi/compte/) : ses outils sont servis à la session
Copilot par un serveur MCP. Ici, la veille vérifie les nouveaux courriels toutes les
JOI_COURRIEL_VERIFIER_MINUTES et les glisse dans la conversation, sous forme de message système,
quand Joi est inactive depuis 10 s (en mode écoute : au retour). Sans compte, rien de tout ça.

Les deux modes :
  conversation   l'audio part en streaming vers AssemblyAI, Joi répond (mode au réveil)
  ecoute         Joi se tait : la session AssemblyAI est fermée (on ne paie pas) et l'audio est
                 enregistré sur le disque par joi/enregistreur.py. Toutes les JOI_APPRENDRE_MINUTES
                 d'audio, et au retour en conversation (ou à la fermeture de l'onglet),
                 joi/transcription.py le transcrit en fond, puis la mémoire
                 l'intègre.

On change de mode avec le bouton de l'interface, ou à la voix : « Joi, écoute seulement » est repéré
dans la transcription en streaming, « Joi, reviens » est reconnu hors ligne par Vosk.

Reconnaissance de ta voix (joi/empreintes.py) : à la fin de chaque tour de conversation d'au moins
2 s, l'audio du tour (gardé en mémoire par les oreilles) donne une empreinte qui affine celle de
« toi ». L'audio n'est jamais écrit sur le disque. Un tour qui chevauche une lecture de la voix de
Joi (signalée par le navigateur) est ignoré.
"""

import asyncio
import json
import logging
import math
import random
import re
import time
from collections import Counter, deque
from contextlib import asynccontextmanager
from datetime import date
from typing import Callable

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import commande_vocale, config, empreintes, enregistreur, journal, memoire, monitoring, transcription, voix
from .cerveau import CONSULTATION, RECHERCHE, Cerveau, arreter_client_partage, client_partage
from .enregistreur import OCTETS_PAR_SECONDE, Enregistreur
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

# Ce qu'elle dit quand elle consulte son compte (courriels, agenda, tâches ; seulement si JOI_COMPTE).
PHRASES_DE_CONSULTATION = [
    "Je regarde ça…",
    "Une seconde, je vérifie…",
    "Laisse-moi regarder…",
]
# Outils du compte sans phrase d'attente : Joi parle déjà du courriel qu'elle prépare ou envoie.
SANS_PHRASE_D_ATTENTE = {"preparer_courriel", "envoyer_courriel"}

# Un courriel reçu est annoncé seulement quand Joi est inactive depuis ce délai (secondes) : personne
# ne parle, elle ne parle pas.
INACTIVITE_AVANT_ANNONCE = 10

# Délai avant de rattraper, au démarrage, les échanges non consolidés : la consolidation lance
# une deuxième session Copilot, qui ralentirait le réveil de Joi et ses premières réponses.
DELAI_RATTRAPAGE = 300  # secondes

# Vérification de la fin de journée (bilan des voix), en secondes.
INTERVALLE_FIN_DE_JOURNEE = 15 * 60

# Phrases d'une réponse synthétisées en même temps au plus (voir Conversation.repondre).
SYNTHESES_EN_AVANCE = 3

# Marge autour d'une lecture de la voix de Joi (latence du haut-parleur et du micro), en secondes.
MARGE_LECTURE = 0.5

# Reconnexion des oreilles après une coupure d'AssemblyAI : secondes avant chaque essai (le dernier
# délai se répète). Une nouvelle session recommence la numérotation des tours : nouvelles oreilles.
DELAIS_RECONNEXION = (1, 2, 5, 10, 30)

# En écoute, le WebSocket peut rester ouvert alors que plus aucun audio n'arrive (micro débranché,
# onglet gelé) : au-delà de ce silence total, en secondes, on le dit au navigateur et au journal.
SILENCE_MAX_ECOUTE = 30
INTERVALLE_GARDE_SILENCE = 5

# Tâches de fond (consolidation de la mémoire) : on garde une référence pour qu'elles aillent au bout.
_taches_de_fond: set[asyncio.Task] = set()
_conversations_actives = 0
_conversations: set["Conversation"] = set()  # pour le tableau de bord


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


async def _surveiller_fin_de_journee() -> None:
    """Le bilan des voix de chaque journée finie (après JOI_FIN_JOURNEE, ou une journée passée), puis
    l'effacement de ses empreintes anonymes. Tout de suite au démarrage : le portable était peut-être
    fermé à l'heure dite, ou Joi arrêtée. Ensuite, une vérification régulière plutôt qu'une minuterie
    jusqu'au soir, qui se décalerait pendant une mise en veille. Une journée dont une écoute est en
    cours, ou pas encore apprise, attend : la fin de l'écoute (transcription.apprendre) s'en charge."""
    while True:
        try:
            await transcription.fermer_journees()
        except Exception:
            log.exception("Bilan des voix de la journée impossible")
        await asyncio.sleep(INTERVALLE_FIN_DE_JOURNEE)


async def _veiller_courriels() -> None:
    """Compte actif seulement : toutes les JOI_COURRIEL_VERIFIER_MINUTES, pendant qu'une conversation
    est ouverte, les nouveaux courriels (notés au journal par la veille). Les pertinents sont confiés
    aux conversations, qui les annoncent quand Joi est inactive (ou au retour du mode écoute)."""
    from .compte import reglages, veille

    if reglages.VERIFIER_MINUTES <= 0:
        return
    while True:
        await asyncio.sleep(reglages.VERIFIER_MINUTES * 60)
        if not _conversations:
            continue  # personne n'est là : le prochain réveil les rattrapera
        nouveaux = await veille.instance().verifier()
        pertinents = [c for c in nouveaux if c.pertinent]
        if len(pertinents) < len(nouveaux):
            log.info("%d courriel(s) seulement noté(s) au journal (infolettre, « Autres »).",
                     len(nouveaux) - len(pertinents))
        if pertinents:
            for conversation in list(_conversations):
                conversation.recevoir_courriels(pertinents)


async def _prechauffer_voix() -> None:
    """Synthétise d'avance les phrases de recherche (elles restent ensuite en cache sur le disque)."""
    phrases = PHRASES_DE_RECHERCHE + (PHRASES_DE_CONSULTATION if config.JOI_COMPTE else [])
    for phrase in phrases:
        try:
            await voix.synthetiser(phrase)
        except Exception as e:
            log.warning("Préchauffage de la voix impossible : %s", e)
            return


async def _prechauffer_cerveau() -> None:
    """Démarre le client Copilot partagé avant le premier réveil : il ne reste qu'une session à créer."""
    try:
        await client_partage()
    except Exception as e:
        log.warning("Client Copilot pas encore démarré (il le sera au réveil) : %s", e)


@asynccontextmanager
async def cycle_de_vie(_app: FastAPI):
    monitoring.installer()
    memoire.initialiser()
    _en_fond(_prechauffer_cerveau())
    _en_fond(_rattraper_plus_tard())
    _en_fond(_prechauffer_voix())
    # Télécharge le modèle de « Joi, reviens » dès maintenant (une seule fois), pour qu'il soit prêt
    # à la première écoute.
    _en_fond(commande_vocale.preparer())
    # Le modèle de reconnaissance des voix (~10 s à charger, téléchargé une fois), prêt au premier tour.
    _en_fond(asyncio.to_thread(empreintes.preparer))
    _en_fond(_surveiller_fin_de_journee())
    if config.JOI_COMPTE_REFUSE:
        log.warning("JOI_COMPTE=%s n'est pas pris en charge (microsoft seulement pour l'instant) : "
                    "Joi démarre sans compte.", config.JOI_COMPTE_REFUSE)
    if config.JOI_COMPTE:
        _en_fond(_veiller_courriels())
    yield
    await arreter_client_partage()


app = FastAPI(title="Joi", lifespan=cycle_de_vie)
app.mount("/static", StaticFiles(directory=config.DOSSIER_FRONTEND), name="static")


@app.get("/")
async def accueil():
    return FileResponse(config.DOSSIER_FRONTEND / "index.html")


def _verifier_hote(requete: Request) -> None:
    """Les routes du tableau de bord ne répondent qu'à la page de Joi (pas à un autre site, ni à un
    nom de domaine qui pointerait vers 127.0.0.1)."""
    hote = requete.headers.get("host", "")
    origine = requete.headers.get("origin")
    if f"http://{hote}" not in ORIGINES_AUTORISEES or (origine and origine not in ORIGINES_AUTORISEES):
        raise HTTPException(status_code=403)


@app.get("/api/parametres")
async def api_parametres(requete: Request):
    _verifier_hote(requete)
    return {"groupes": monitoring.parametres()}


@app.get("/api/monitoring")
async def api_monitoring(requete: Request):
    _verifier_hote(requete)
    # Les manifestes vivants des écoutes en cours se lisent ici, sur la boucle qui les modifie.
    segments = Counter()
    for _chemin, manifeste, _active in enregistreur.seances():
        segments.update(s.get("etat", "?") for s in manifeste["segments"])
    ecoute = {
        "segments": dict(segments),
        "seances": [c.etat_ecoute() for c in _conversations if c.enregistreur],
    }
    return {**await asyncio.to_thread(_etat_disque), "ecoute": ecoute, "compte": _etat_compte(),
            "latence": monitoring.resume_latences()}


def _etat_compte() -> dict:
    if not config.JOI_COMPTE:
        return {"actif": False}
    from .compte import veille

    return veille.instance().etat()


def _etat_disque() -> dict:
    fichiers_memoire = len(list(config.DOSSIER_MEMOIRE.rglob("*.md")))
    cache_fichiers, cache_octets = monitoring.taille_dossier(config.DOSSIER_VOIX)
    return {
        "serveur": {
            "depuis_s": round(time.time() - monitoring.DEMARRAGE),
            "conversations": _conversations_actives,
            "taches_de_fond": len(_taches_de_fond),
        },
        "voix": {**monitoring.voix, "cache_fichiers": cache_fichiers, "cache_octets": cache_octets},
        "transcription": {**monitoring.transcription, "en_cours": transcription._verrou.locked()},
        "memoire": {
            "consolidation_en_cours": memoire._verrou_consolidation.locked(),
            "derniere_consolidation": journal.derniere_consolidation(),
            "fichiers": fichiers_memoire,
        },
        "evenements": list(monitoring.evenements)[-15:],
    }


class MesureReponse:
    """Où part le temps d'une réponse, de la fin de ta phrase au premier son (instants time.monotonic) :

      fin de parole → tour final   AssemblyAI décide que tu as fini (l'audio du dernier mot est arrivé
                                   à fin_parole, voir Oreilles._traiter_tour)
      envoi → 1er mot → 1re phrase le cerveau (Copilot)
      synthèse                     la voix de la première phrase (en distinguant le cache)
      MP3 envoyé → lecture         le navigateur décode et commence à jouer (message « lecture »)

    Le premier son peut être une phrase d'attente (« Laisse-moi me rappeler… ») quand Joi fouille sa
    mémoire : c'est bien ce que la personne entend en premier."""

    def __init__(self, tour: Tour | None):
        self.fin_parole = tour.fin_parole if tour else None
        self.tour_final = tour.recu if tour else None
        self.envoi: float | None = None
        self.premier_token: float | None = None
        self.premiere_phrase: float | None = None
        self.synthese: dict | None = None  # {"cache", "duree_s"} de la première phrase dite
        self.mp3_envoye: float | None = None
        self.lecture: float | None = None
        self.recherches = 0
        self.reponse_finie = False

    @staticmethod
    def _ecart(a: float | None, b: float | None) -> float | None:
        return round(b - a, 2) if a is not None and b is not None else None

    def resume(self) -> dict:
        debut = self.fin_parole or self.tour_final or self.envoi
        return {
            "fin_de_tour": self._ecart(self.fin_parole, self.tour_final),
            "premier_mot": self._ecart(self.envoi, self.premier_token),
            "premiere_phrase": self._ecart(self.envoi, self.premiere_phrase),
            "synthese": round(self.synthese["duree_s"], 2) if self.synthese else None,
            "cache": self.synthese.get("cache") if self.synthese else None,
            "lecture": self._ecart(self.mp3_envoye, self.lecture),
            "total": self._ecart(debut, self.lecture or self.mp3_envoye),
            "recherches": self.recherches,
        }

    def ligne(self) -> str:
        r = self.resume()

        def s(cle: str) -> str:
            return f"{r[cle]:.1f} s" if r[cle] is not None else "—"

        cache = " (cache)" if r["cache"] else ""
        debut = "fin de parole" if self.fin_parole else "tour final" if self.tour_final else "envoi"
        return (f"Latence : fin de tour {s('fin_de_tour')} · cerveau : 1er mot {s('premier_mot')}, "
                f"1re phrase {s('premiere_phrase')} · synthèse {s('synthese')}{cache} · "
                f"lecture {s('lecture')} · total {s('total')} ({debut} → premier son)"
                + (f" · {r['recherches']} recherche(s)" if r["recherches"] else ""))


class GardeFouSilence:
    """Retient l'instant du dernier paquet audio reçu. verifier() le signale une fois quand le
    silence total dépasse le seuil ; audio_recu() dit, une fois, combien de temps il a duré quand
    l'audio revient. L'horloge est injectable pour les tests."""

    def __init__(self, seuil_s: float = SILENCE_MAX_ECOUTE, horloge: Callable[[], float] = time.monotonic):
        self.seuil_s = seuil_s
        self._horloge = horloge
        self.dernier_audio = horloge()
        self.en_alerte = False

    def reprendre(self) -> None:
        """Une nouvelle écoute commence : le compte repart de zéro."""
        self.dernier_audio = self._horloge()
        self.en_alerte = False

    def audio_recu(self) -> float | None:
        maintenant = self._horloge()
        absence, self.dernier_audio = maintenant - self.dernier_audio, maintenant
        if self.en_alerte:
            self.en_alerte = False
            return absence
        return None

    def verifier(self) -> float | None:
        absence = self._horloge() - self.dernier_audio
        if not self.en_alerte and absence > self.seuil_s:
            self.en_alerte = True
            return absence
        return None


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

    def __init__(self, ws: WebSocket, horloge: Callable[[], float] = time.monotonic):
        self.ws = ws
        self.cerveau = Cerveau()
        self.oreilles = self._nouvelles_oreilles()
        self.mode = "conversation"
        self.enregistreur: Enregistreur | None = None
        self._audio_non_appris = 0.0  # secondes d'écoute terminées, pas encore confiées à transcription
        self.detecteur_retour: commande_vocale.DetecteurRetour | None = None
        self.tache_reponse: asyncio.Task | None = None
        # L'interruption de la réponse précédente (abort du cerveau, jusqu'à 5 s) : hors de la
        # réception d'AssemblyAI, et attendue avant la réponse suivante.
        self._tache_interruption: asyncio.Task | None = None
        self.tache_bascule: asyncio.Task | None = None
        self.tache_reconnexion: asyncio.Task | None = None
        self.garde_silence = GardeFouSilence(horloge=horloge)
        self.tache_garde: asyncio.Task | None = None
        # La mesure de la dernière réponse, complétée quand le navigateur commence à la jouer.
        self._mesure: MesureReponse | None = None
        self._verrou_mode = asyncio.Lock()
        self._verrou_envoi = asyncio.Lock()
        # Les lectures de la voix de Joi dans le navigateur : [début, fin ou None] (time.monotonic).
        self._lectures: deque[list] = deque(maxlen=50)
        # Compte de Joi : les courriels à annoncer (courriel, arrivé pendant l'écoute), et le dernier
        # signe de vie de la conversation (pour annoncer seulement quand Joi est inactive).
        self._courriels: list[tuple] = []
        self._derniere_activite = time.monotonic()
        # Les journées dont une écoute vient d'être apprise : Joi le sait (et peut le dire) dès qu'elle
        # est inactive et que l'apprentissage est fini.
        self._journees_apprises: set[str] = set()
        self.tache_annonces: asyncio.Task | None = None

    def _nouvelles_oreilles(self) -> Oreilles:
        oreilles = Oreilles(sur_tour=self.sur_tour, sur_erreur=self.signaler_erreur)

        async def coupure(code: int | None, raison: str) -> None:
            self._oreilles_coupees(oreilles, code, raison)

        oreilles.sur_coupure = coupure
        return oreilles

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
        _conversations.add(self)
        try:
            await self._executer()
        finally:
            _conversations_actives -= 1
            _conversations.discard(self)

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
        memoire.abonner(self.journees_apprises)
        self.tache_annonces = asyncio.create_task(self._annoncer())
        self.tache_garde = asyncio.create_task(self._surveiller_audio())

        try:
            while True:
                message = await self.ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes"):
                    absence = self.garde_silence.audio_recu()
                    if absence is not None and self.mode == "ecoute":
                        log.info("Écoute : l'audio est revenu après %.0f s de silence total.", absence)
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
            for tache in (self.tache_bascule, self.tache_reconnexion, self.tache_annonces, self.tache_garde):
                if tache:
                    tache.cancel()
            memoire.desabonner(self.journees_apprises)
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
        self._derniere_activite = time.monotonic()
        if genre == "texte" and commande.get("texte", "").strip():
            if self.mode == "conversation":
                await self.sur_tour(Tour(commande["texte"].strip(), True, -1))
        elif genre == "mode" and commande.get("mode") in ("conversation", "ecoute"):
            await self.changer_mode(commande["mode"])
        elif genre == "lecture":
            self._noter_lecture(bool(commande.get("en_cours")))

    # ---------- Ta voix ----------

    def _noter_lecture(self, en_cours: bool) -> None:
        maintenant = time.monotonic()
        self._derniere_activite = maintenant
        if en_cours:
            mesure = self._mesure
            if mesure and mesure.mp3_envoye is not None and mesure.lecture is None:
                mesure.lecture = maintenant
                # La lecture peut commencer (phrase d'attente) avant la première phrase du cerveau :
                # la mesure est notée à la fin de la réponse.
                if mesure.reponse_finie:
                    self._terminer_mesure()
            if not self._lectures or self._lectures[-1][1] is not None:
                self._lectures.append([maintenant, None])
        elif self._lectures and self._lectures[-1][1] is None:
            self._lectures[-1][1] = maintenant

    def _terminer_mesure(self) -> None:
        """Note la mesure de la dernière réponse (journal technique et tableau de bord)."""
        mesure, self._mesure = self._mesure, None
        if mesure is None or mesure.envoi is None:
            return
        log.info(mesure.ligne())
        monitoring.latences.append(mesure.resume())

    def _joi_parlait(self, debut: float, fin: float) -> bool:
        maintenant = time.monotonic()
        return any(
            d - MARGE_LECTURE <= fin and (f if f is not None else maintenant) + MARGE_LECTURE >= debut
            for d, f in self._lectures
        )

    def _apprendre_ma_voix(self, tour: Tour) -> None:
        """Affine l'empreinte de « toi » avec l'audio de ce tour, s'il est assez long et que Joi ne
        parlait pas en même temps. L'audio est oublié dès que son empreinte est calculée."""
        if not tour.mots or not empreintes.active():
            return
        plages = empreintes.plages_de_parole(tour.mots)
        parole_ms = sum(f - d for d, f in plages)
        if parole_ms < empreintes.ENROLEMENT_MIN_S * 1000:
            return
        # Les premières secondes de parole suffisent (et un long monologue ne pèse pas trop).
        retenues, total = [], 0
        for d, f in plages:
            f = min(f, d + int(empreintes.ENROLEMENT_MAX_S * 1000) - total)
            retenues.append((d, f))
            total += f - d
            if total >= empreintes.ENROLEMENT_MAX_S * 1000:
                break
        tampon = self.oreilles.tampon
        debut, fin = tampon.instant(retenues[0][0]), tampon.instant(retenues[-1][1] - 1)
        if debut is None or fin is None:
            return
        if self._joi_parlait(debut, fin):
            log.debug("Tour non appris : Joi parlait en même temps.")
            return
        morceaux = [tampon.extraire(d, f) for d, f in retenues]
        if any(m is None for m in morceaux):
            return
        _en_fond(self._enroler(b"".join(morceaux)))

    @staticmethod
    async def _enroler(pcm: bytes) -> None:
        try:
            await asyncio.to_thread(empreintes.enroler_pcm, pcm)
        except Exception as e:
            log.warning("Empreinte de ta voix non mise à jour : %s", e)

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
        self._audio_non_appris = 0.0
        enregistreur = Enregistreur(sur_segment_fini=self._segment_fini)
        try:
            enregistreur.ouvrir()
        except OSError as e:
            log.exception("Enregistrement impossible")
            await self.signaler_erreur(f"Je n'arrive pas à enregistrer : {e}")
            await self._revenir_en_conversation()
            return
        self.enregistreur = enregistreur
        self.garde_silence.reprendre()
        self.detecteur_retour = commande_vocale.DetecteurRetour(sur_retour=self._retour_vocal)
        self.detecteur_retour.demarrer()
        await self.envoyer(type="etat", etat="ecoute")

    def _segment_fini(self, duree_s: float) -> None:
        """Un segment d'écoute vient de se terminer : après assez d'audio, Joi l'apprend sans
        attendre la fin de l'écoute, pendant que l'enregistrement continue."""
        if config.JOI_APPRENDRE_MINUTES <= 0:
            return
        self._audio_non_appris += duree_s
        if self._audio_non_appris >= config.JOI_APPRENDRE_MINUTES * 60:
            log.info("Écoute : %.0f min d'audio en attente, Joi les apprend sans attendre la fin.",
                     self._audio_non_appris / 60)
            self._audio_non_appris = 0.0
            _en_fond(transcription.apprendre())

    def etat_ecoute(self) -> dict:
        """La séance d'écoute en cours, pour le tableau de bord."""
        e = self.enregistreur
        courant = e._octets_segment / OCTETS_PAR_SECONDE
        termines = sum(s.get("duree_s") or 0 for s in e.manifeste["segments"][:-1])
        prochaine = None
        if config.JOI_APPRENDRE_MINUTES > 0:
            # L'analyse part à la fin du premier segment qui fait dépasser le seuil.
            manque = config.JOI_APPRENDRE_MINUTES * 60 - self._audio_non_appris
            segments = max(1, math.ceil(manque / e.duree_segment_s))
            prochaine = round(segments * e.duree_segment_s - courant)
        return {
            "debut": e.manifeste.get("debut"),
            "audio_s": round(termines + courant),
            "segment_courant_s": round(courant),
            "duree_segment_s": e.duree_segment_s,
            "non_appris_s": round(self._audio_non_appris + courant),
            "prochaine_analyse_s": prochaine,
        }

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
            await self.signaler_erreur(f"Je n'arrive plus à t'entendre : {e}. J'essaie encore…")
            self._reconnecter_en_fond()
        await self.envoyer(type="etat", etat="ecoute")

    # ---------- Oreilles coupées ----------

    def _oreilles_coupees(self, oreilles: Oreilles, code: int | None, raison: str) -> None:
        """AssemblyAI a fermé la session sans qu'on le demande (3 h atteintes, erreur, réseau). En
        conversation, Joi recrée ses oreilles avec un délai croissant ; en écoute, la session est
        déjà fermée exprès et le retour en conversation en ouvrira une nouvelle."""
        if oreilles is not self.oreilles or self.mode != "conversation":
            return
        if not oreilles.recuperable:
            log.error("Oreilles coupées (code %s, %s) : une nouvelle session échouerait de la même façon.",
                      code, raison)
            _en_fond(self.signaler_erreur(f"Je ne t'entends plus ({raison or code}). Relance-moi une fois "
                                          "le problème réglé."))
            return
        _en_fond(self.signaler_erreur("Je ne t'entends plus : je me reconnecte…"))
        self._reconnecter_en_fond()

    def _reconnecter_en_fond(self) -> None:
        if self.tache_reconnexion and not self.tache_reconnexion.done():
            return  # la tâche en cours verra que les oreilles actuelles sont coupées
        self.tache_reconnexion = asyncio.create_task(self._reconnecter_oreilles())

    def _a_reconnecter(self) -> bool:
        return self.mode == "conversation" and self.oreilles.coupee and self.oreilles.recuperable

    async def _reconnecter_oreilles(self) -> None:
        """De nouvelles oreilles, tant que Joi est en conversation et que les siennes sont coupées.
        Un changement de mode entre deux essais (écoute, ou retour qui ouvre ses propres oreilles)
        arrête les essais ; le verrou du mode évite de se croiser avec lui. Des oreilles qui tombent
        aussitôt rouvertes (pendant que cette tâche finit) sont reprises ici, avec un délai qui
        continue de croître : la vérification de la boucle ne cède pas la main avant de finir."""
        essai = 0
        while self._a_reconnecter():
            essai += 1
            await asyncio.sleep(DELAIS_RECONNEXION[min(essai, len(DELAIS_RECONNEXION)) - 1])
            async with self._verrou_mode:
                if not self._a_reconnecter():
                    return
                nouvelles = self._nouvelles_oreilles()
                try:
                    await nouvelles.ouvrir()
                except Exception as e:
                    log.warning("Reconnexion des oreilles : essai %d raté (%s)", essai, e)
                    continue
                self.oreilles = nouvelles
            log.info("Oreilles reconnectées (essai %d) : nouvelle session AssemblyAI.", essai)
            await self.signaler_erreur("Je t'entends de nouveau.")
            if not (self.tache_reponse and not self.tache_reponse.done()):
                await self.envoyer(type="etat", etat="ecoute")

    # ---------- Silence total en écoute ----------

    async def _surveiller_audio(self) -> None:
        while True:
            await asyncio.sleep(INTERVALLE_GARDE_SILENCE)
            await self._verifier_silence()

    async def _verifier_silence(self) -> None:
        """En écoute, plus aucun audio alors que le WebSocket est ouvert : l'enregistrement est arrêté
        sans que personne le voie. On le dit au navigateur, et au journal (donc au tableau de bord)."""
        if self.mode != "ecoute" or not self.enregistreur:
            return
        absence = self.garde_silence.verifier()
        if absence is None:
            return
        log.warning("Écoute : aucun audio reçu depuis %.0f s (micro coupé ? onglet gelé ?).", absence)
        await self.signaler_erreur(f"Je n'entends plus rien depuis {absence:.0f} s : le micro est-il "
                                   "toujours branché ?")

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
        self._derniere_activite = time.monotonic()
        await self.envoyer(type="transcription", texte=tour.texte, final=tour.final)

        # Ce rappel vient de la réception d'AssemblyAI : rien ici ne doit attendre longtemps, sinon
        # les transcriptions s'arrêtent. L'interruption (jusqu'à 5 s d'abort du cerveau) part donc
        # dans une tâche, et la réponse attend qu'elle soit finie.
        if not tour.final:
            # On parle par-dessus Joi : elle se tait.
            if len(tour.texte.split()) >= MOTS_POUR_INTERROMPRE:
                self._interrompre_en_fond()
            return

        if not tour.texte:
            return
        self._apprendre_ma_voix(tour)
        if commande_vocale.demande_ecoute(tour.texte):
            log.info("Commande vocale : « %s »", tour.texte)
            self._basculer_en_fond("ecoute")
            return
        interruption = self._interrompre_en_fond()
        self.tache_reponse = asyncio.create_task(self._repondre_apres(interruption, tour))

    async def _repondre_apres(self, interruption: asyncio.Task, tour: Tour) -> None:
        # shield : si cette réponse est elle-même interrompue pendant l'attente, l'interruption
        # précédente va quand même au bout (la suivante l'attend).
        await asyncio.shield(interruption)
        await self.repondre(tour.texte, tour=tour)

    def _interrompre_en_fond(self) -> asyncio.Task:
        """Coupe la réponse en cours tout de suite (une réponse qui attendait son tour ne partira
        jamais), puis arrête le cerveau dans une tâche. Les interruptions s'enchaînent dans l'ordre :
        une nouvelle réponse ne part qu'une fois la précédente vraiment arrêtée."""
        tache, self.tache_reponse = self.tache_reponse, None
        if tache and not tache.done():
            tache.cancel()
        else:
            tache = None
        precedente = self._tache_interruption
        self._tache_interruption = asyncio.create_task(self._interrompre(tache, precedente))
        return self._tache_interruption

    async def _interrompre(self, tache: asyncio.Task | None, precedente: asyncio.Task | None) -> None:
        if precedente and not precedente.done():
            await asyncio.wait([precedente])
        if tache:
            try:
                await tache
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("Réponse mal terminée")
            await self.cerveau.interrompre()  # sans effet si le cerveau ne répondait pas
        try:
            await self.envoyer(type="stop_audio")
        except Exception:
            pass

    async def interrompre(self) -> None:
        """Coupe la réponse en cours et attend qu'elle soit arrêtée (changement de mode, fermeture)."""
        await asyncio.shield(self._interrompre_en_fond())

    async def repondre(self, texte: str, systeme: bool = False, tour: Tour | None = None) -> None:
        """La réponse de Joi à ce que la personne a dit (ou tapé). systeme : un message glissé par le
        serveur (un courriel reçu, une écoute apprise) ; Joi peut alors répondre [rien], qui n'est ni dit ni écrit au journal."""
        mesure = None
        if not systeme:
            if self._mesure:  # la réponse précédente n'a jamais été jouée : on la note quand même
                self._terminer_mesure()
            mesure = self._mesure = MesureReponse(tour)
            journal.noter("toi", texte)
            await self.envoyer(type="etat", etat="reflechit")
        a_parle = False
        dites: list[str] = []  # les phrases données par le cerveau
        envoyees: list[str] = []  # celles vraiment parties vers le navigateur : ce que retient le journal
        attentes: set[str] = set()  # la phrase d'attente (recherche, consultation), pas une réponse
        termine = False
        silence = False  # elle a répondu [rien] à un message système
        debut = time.perf_counter()
        premiere_phrase: float | None = None
        recherches = consultations = 0
        attente_dite = False

        # La voix : chaque phrase est synthétisée dès que le cerveau la donne (SYNTHESES_EN_AVANCE à
        # la fois au plus), pendant que les précédentes partent vers le navigateur, toujours dans
        # l'ordre. Avant, la phrase suivante attendait la fin de la synthèse de la précédente.
        a_envoyer: asyncio.Queue = asyncio.Queue()  # (phrase, tâche de synthèse), puis None
        syntheses: list[asyncio.Task] = []
        limite = asyncio.Semaphore(SYNTHESES_EN_AVANCE)

        async def synthetiser(phrase: str) -> tuple[bytes, dict]:
            async with limite:
                infos: dict = {}
                return await voix.synthetiser(phrase, infos), infos

        async def envoyer_dans_l_ordre() -> None:
            nonlocal a_parle
            while (suivante := await a_envoyer.get()) is not None:
                phrase, synthese = suivante
                await self.envoyer(type="reponse", texte=voix.nettoyer(phrase))
                if phrase not in attentes:  # « Laisse-moi me rappeler… » n'a jamais été journalisé
                    envoyees.append(phrase)
                try:
                    audio, infos = await synthese
                except Exception as e:  # une phrase sans voix (le sous-titre reste) plutôt que tout perdre
                    # Le type seulement : le message peut reprendre la réponse du fournisseur de voix.
                    log.warning("Synthèse impossible : %s", type(e).__name__)
                    continue
                if audio:
                    if not a_parle:
                        await self.envoyer(type="etat", etat="parle")
                        a_parle = True
                    await self.envoyer_audio(audio)
                    if mesure and mesure.mp3_envoye is None:
                        mesure.synthese, mesure.mp3_envoye = infos, time.monotonic()

        envoi = asyncio.create_task(envoyer_dans_l_ordre())

        async def dire(phrase: str) -> None:
            if systeme and not a_parle and not dites:
                await self.envoyer(type="etat", etat="reflechit")  # efface le sous-titre précédent
            synthese = asyncio.create_task(synthetiser(phrase))
            # Une synthèse ratée après une interruption n'est jamais attendue : on lit son erreur.
            synthese.add_done_callback(lambda t: t.cancelled() or t.exception())
            syntheses.append(synthese)
            a_envoyer.put_nowait((phrase, synthese))

        async def attendre(phrases: list[str]) -> None:
            # Une seule phrase d'attente, et seulement si elle n'a encore rien dit.
            nonlocal attente_dite
            if not attente_dite and not dites and not systeme:
                attente_dite = True
                attente = random.choice(phrases)
                attentes.add(attente)
                await dire(attente)

        try:
            async for morceau in self.cerveau.repondre(texte, systeme=systeme):
                if mesure:
                    mesure.envoi, mesure.premier_token = self.cerveau.envoi, self.cerveau.premier_token
                if isinstance(morceau, RECHERCHE):
                    recherches += 1
                    log.info("Joi fouille sa mémoire (%.1f s) : %r",
                             time.perf_counter() - debut, str(morceau))
                    await attendre(PHRASES_DE_RECHERCHE)
                    continue
                if isinstance(morceau, CONSULTATION):
                    consultations += 1
                    log.info("Joi consulte son compte (%.1f s) : %s", time.perf_counter() - debut, morceau)
                    if str(morceau) not in SANS_PHRASE_D_ATTENTE:
                        await attendre(PHRASES_DE_CONSULTATION)
                    continue
                if systeme and not dites and _est_rien(morceau):
                    silence = True
                if silence:
                    continue
                if premiere_phrase is None:
                    premiere_phrase = time.perf_counter() - debut
                    if mesure:
                        mesure.premiere_phrase = time.monotonic()
                dites.append(morceau)
                await dire(morceau)
            a_envoyer.put_nowait(None)
            await envoi  # toute la voix est partie
            termine = True
            if silence:
                log.info("Message système : Joi a choisi de ne rien dire.")
            else:
                log.info(
                    "Réponse : première phrase en %s, complète en %.1f s, %d recherche(s) en mémoire%s",
                    f"{premiere_phrase:.1f} s" if premiere_phrase is not None else "—",
                    time.perf_counter() - debut, recherches,
                    f", {consultations} consultation(s) du compte" if consultations else "",
                )
            if dites or attente_dite or not systeme:
                await self.envoyer(type="fin_reponse")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("Erreur pendant la réponse")
            await self.signaler_erreur(f"Oups, j'ai perdu le fil : {e}")
            await self.envoyer(type="etat", etat="ecoute")
        finally:
            # Une interruption annule les synthèses en attente et ce qui restait à envoyer.
            for tache in (envoi, *syntheses):
                tache.cancel()
            # Seulement ce qui est parti : après une interruption, les phrases encore en synthèse n'ont
            # été ni dites ni montrées, et la mémoire (consolidée depuis le journal) ne doit pas le croire.
            journal.noter("joi", " ".join(envoyees), interrompu=not termine)
            self._derniere_activite = time.monotonic()
            if mesure:
                mesure.recherches = recherches
                mesure.reponse_finie = True
                # Sans son, ou déjà en lecture : rien de plus à attendre du navigateur.
                if self._mesure is mesure and (mesure.mp3_envoye is None or mesure.lecture is not None):
                    self._terminer_mesure()

    # ---------- Courriels reçus (compte de Joi) ----------

    def recevoir_courriels(self, courriels: list) -> None:
        """De nouveaux courriels pertinents : annoncés dès que Joi est inactive (en mode écoute : au
        retour en conversation, en signalant qu'ils sont arrivés pendant l'écoute)."""
        self._courriels += [(c, self.mode == "ecoute") for c in courriels]

    def _inactive(self) -> bool:
        maintenant = time.monotonic()
        if self.mode != "conversation":
            return False
        if self.tache_reponse and not self.tache_reponse.done():
            return False
        if self.tache_bascule and not self.tache_bascule.done():
            return False
        # Sa voix joue encore dans le navigateur (au-delà de 2 min, on suppose une fin non signalée).
        if self._lectures and self._lectures[-1][1] is None and maintenant - self._lectures[-1][0] < 120:
            return False
        return maintenant - self._derniere_activite >= INACTIVITE_AVANT_ANNONCE

    # ---------- Écoute apprise ----------

    def journees_apprises(self, jours: list[str]) -> None:
        """Une écoute vient d'entrer dans la mémoire (appelé par memoire.consolider_ecoute)."""
        self._journees_apprises.update(jours)

    async def _annoncer(self) -> None:
        """Glisse dans la conversation, quand Joi est inactive, les courriels reçus et les journées
        apprises. Une journée attend la fin de l'apprentissage en cours : pendant une longue écoute, Joi
        apprend chaque heure, puis la fin au retour ; elle ne l'annonce qu'une fois, tout appris."""
        while True:
            await asyncio.sleep(2)
            if not self._inactive():
                continue
            if self._courriels:
                lot, self._courriels = self._courriels, []
                log.info("Annonce possible de %d courriel(s) : Joi juge s'ils sont utiles.", len(lot))
                self.tache_reponse = asyncio.create_task(self.repondre(_message_courriels(lot), systeme=True))
            elif self._journees_apprises and not _apprentissage_en_cours():
                jours, self._journees_apprises = sorted(self._journees_apprises), set()
                log.info("Mémoire : écoute du %s apprise, Joi le sait.", ", ".join(jours))
                self.tache_reponse = asyncio.create_task(self.repondre(_message_journees(jours), systeme=True))


def _apprentissage_en_cours() -> bool:
    """Une transcription d'écoute ou une consolidation de la mémoire est en cours."""
    return transcription._verrou.locked() or memoire._verrou_consolidation.locked()


def _message_journees(jours: list[str]) -> str:
    """Le message système qui dit à Joi qu'elle vient d'apprendre une écoute (glissé dans la conversation)."""
    aujourdhui = date.today().isoformat()
    lignes = []
    for jour in jours:
        quand = f"aujourd'hui ({jour})" if jour == aujourdhui else f"le {jour}"
        resume = memoire.description(f"journees/{jour}.md")
        lignes.append(f"- {quand} : journees/{jour}.md" + (f" ({resume})" if resume else ""))
    return (
        "[mémoire mise à jour]\n"
        "Tu viens de finir d'apprendre ce que tu as entendu en écoutant en silence. Le résumé est "
        "maintenant dans ta mémoire :\n" + "\n".join(lignes) + "\n"
        "Pour en parler, cherche-le avec se_souvenir en nommant la journée (« aujourd'hui », "
        "« 16 novembre »). Si tu as dit plus tôt que tu n'avais pas ces notes, ce n'est plus vrai. "
        "Ce message ne vient pas de la personne. Dis-lui en une phrase courte que tu as fini d'apprendre "
        "ce que tu as entendu et que tu peux lui en parler, sans le résumer d'office. Si vous êtes au "
        "milieu d'un autre sujet et que ce serait déplacé, réponds exactement [rien] : tu en parleras "
        "quand elle le demandera."
    )


def _est_rien(phrase: str) -> bool:
    """La réponse [rien] (Joi juge un message système inutile à annoncer)."""
    brut = phrase.strip().lower()
    return brut.startswith("[rien") or re.sub(r"\W", "", journal.normaliser(brut)) == "rien"


def _message_courriels(lot: list[tuple]) -> str:
    """Le message système qui présente à Joi des courriels reçus (glissé dans la conversation)."""
    from .compte.modele import externe, ligne_courriel

    pendant_ecoute = any(pendant for _, pendant in lot)
    lignes = externe("\n".join(f"- {ligne_courriel(c)}" for c, _ in lot))
    if pendant_ecoute:
        return (
            "[courriels reçus pendant que tu écoutais en silence]\n"
            f"{lignes}\n"
            "Ce message ne vient pas de la personne. Si l'un de ces courriels lui est vraiment utile "
            "(quelqu'un qu'elle connaît, une réponse attendue, quelque chose d'urgent), dis-le-lui en une "
            "phrase, en commençant par « Pendant que j'écoutais… », sans le lire. Publicité, infolettre, "
            "notification automatique, ou dans le doute : réponds exactement [rien]."
        )
    entete = "[courriel reçu]" if len(lot) == 1 else "[courriels reçus]"
    return (
        f"{entete}\n{lignes}\n"
        "Ce message ne vient pas de la personne : ta boîte vient de recevoir ce courriel. S'il lui est "
        "vraiment utile (quelqu'un qu'elle connaît, une réponse attendue, quelque chose d'urgent), "
        "annonce-le en une phrase, sans le lire. Publicité, infolettre, notification automatique, ou "
        "dans le doute : réponds exactement [rien]."
    )
