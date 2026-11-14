"""La reconnaissance des voix du mode écoute : des empreintes vocales calculées sur l'ordinateur.

AssemblyAI étiquette les voix (A, B…) segment par segment : le « A » de 9 h n'est pas forcément le
« A » de 14 h, et sa diarisation s'effondre parfois (une seule « phrase » de 30 min pour tout un
dialogue). Ce module reconnaît les voix sur toute la journée, sans rien envoyer ailleurs :

1. À chaque segment (joi/transcription.py, après la transcription et avant l'effacement du MP3) :
   - la parole est découpée en fenêtres de 1,5 à 4 s à partir des mots d'AssemblyAI (words[]) ;
     ses étiquettes A/B ne servent que d'indice (on coupe une fenêtre quand elles changent) ;
   - chaque fenêtre reçoit une empreinte (SpeechBrain ECAPA : 192 nombres) ;
   - elle est comparée aux voix connues : « toi » (et plus tard les personnes qui ont donné leur
     accord, voir plus bas), avec un lissage dans chaque phrase ;
   - les autres fenêtres sont regroupées dans le segment : « inconnu-HHMMSS-n ».
   Résultat : HHMMSS.voix.json à côté de la transcription (des étiquettes, aucune empreinte), et les
   empreintes moyennes des groupes du segment dans donnees/empreintes/AAAA-MM-JJ/HHMMSS.npz.
2. À la consolidation, les groupes de chaque segment sont rattachés aux voix de la journée
   (« voix inconnue N »), dans l'ordre chronologique. Une voix déjà numérotée garde son numéro.
3. Le soir (JOI_FIN_JOURNEE, ou plus tard si une écoute est en cours) : le bilan de la journée
   (interlocuteurs, voix de média probables, voix de passage) est ajouté au résumé
   journees/AAAA-MM-JJ.md, puis toutes les empreintes anonymes du jour sont effacées.

L'empreinte de « toi » s'apprend pendant les conversations avec Joi (joi/serveur.py) : l'audio d'un
tour de parole reste en mémoire le temps de calculer son empreinte, puis il est oublié. Seule
l'empreinte est gardée, avec les ECHANTILLONS_MAX derniers tours. En secours, on peut l'apprendre
sur une plage d'un enregistrement où l'on parle seul (commande « enroler » plus bas).

Une empreinte vocale est une donnée biométrique. Au Québec (Loi 25, et loi concernant le cadre
juridique des technologies de l'information), il faut le consentement exprès de la personne, et une
banque de caractéristiques biométriques se déclare à la Commission d'accès à l'information. D'où :
les voix anonymes ne vivent qu'une journée ; un nom ne sera donné à une voix qu'avec l'accord de la
personne, noté dans consentements.json (le retrait de l'accord efface son empreinte) ; les clients
restent toujours anonymes.

    donnees/empreintes/
    ├── modele/                        le modèle ECAPA (téléchargé une fois, ~80 Mo)
    ├── toi.npz                        ton empreinte : tes derniers échantillons
    ├── consentements.json             (à venir) les personnes qui ont donné leur accord
    ├── personnes/NOM.npz              (à venir) leurs empreintes
    ├── AAAA-MM-JJ/                    empreintes anonymes du jour, effacées le soir
    │   ├── HHMMSS.npz                 les groupes de voix d'un segment
    │   └── journee.npz                les voix de la journée
    └── rejeu/AAAA-MM-JJ/HHMMSS.npz    cache du rejeu (empreintes de chaque fenêtre)

    donnees/enregistrements/AAAA-MM-JJ/
    ├── HHMMSS.voix.json               les voix d'un segment : fenêtres et étiquettes
    └── voix-journee.json              rattachement des groupes aux voix du jour, puis bilan du soir

Ligne de commande (sans AssemblyAI, pour régler les seuils sur une journée gardée avec
JOI_GARDER_AUDIO=oui) :

    python -m joi.empreintes controle                      le modèle reconnaît-il une même voix ?
    python -m joi.empreintes rejouer 2026-11-13            les voix de la journée, avec extraits
    python -m joi.empreintes enroler 2026-11-13 12:45:10-12:46:30   apprendre ta voix sur une plage
    python -m joi.empreintes oublier toi                   effacer ton empreinte
    python -m joi.empreintes effacer-rejeu                 effacer le cache du rejeu
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as dt_time
from pathlib import Path

from . import config

try:  # numpy vient avec PyTorch : sans lui, la reconnaissance des voix est simplement désactivée.
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

log = logging.getLogger("joi.empreintes")

SOURCE_MODELE = "speechbrain/spkrec-ecapa-voxceleb"
TAUX = 16_000
LOT = 32  # fenêtres par calcul

DOSSIER = config.DOSSIER_EMPREINTES
DOSSIER_MODELE = DOSSIER / "modele"
DOSSIER_REJEU = DOSSIER / "rejeu"
DOSSIER_PERSONNES = DOSSIER / "personnes"
FICHIER_TOI = DOSSIER / "toi.npz"
FICHIER_CONSENTEMENTS = DOSSIER / "consentements.json"

# Lissage : une fenêtre isolée dans sa phrase garde sa voix seulement si la similarité est nette.
MARGE_LISSAGE = 0.10
# Empreinte de « toi » : nombre de tours gardés (moyenne glissante), et garde-fou contre un tour
# qui ne serait pas ta voix (quelqu'un d'autre parle à Joi) une fois l'empreinte bien établie.
ECHANTILLONS_MAX = 60
ECHANTILLONS_FIABLES = 8
ECART_REJET = 0.15
ENROLEMENT_MIN_S = 2.0
ENROLEMENT_MAX_S = 20.0
# Un groupe inconnu d'au moins GROUPE_CONNU_MIN_S secondes dont l'empreinte moyenne dépasse
# JOI_VOIX_SEUIL_TOI + ECART_GROUPE_CONNU est une voix connue (réglé sur le 2026-11-13 : 0,70 en
# médiane pour la même voix, 0,51 au plus pour les autres).
GROUPE_CONNU_MIN_S = 3.0
ECART_GROUPE_CONNU = 0.15
# Journée : un groupe de segment de moins de PETIT_GROUPE_S secondes se rattache à une voix du jour
# avec un seuil abaissé de ECART_PETIT_GROUPE (réglé sur le 2026-11-13).
PETIT_GROUPE_S = 10.0
ECART_PETIT_GROUPE = 0.15
# Bilan : deux fenêtres à moins de ce délai (en s) l'une de l'autre forment une « alternance ».
ALTERNANCE_S = 5.0
# Une voix de média probable : parle au moins ce temps (s) dans la journée, ne répond jamais à toi
# et alterne rarement avec quelqu'un (alternances par minute de parole).
MEDIA_MIN_S = 120.0
MEDIA_ALTERNANCES_MINUTE = 0.5
# Plages horaires du bilan : au-delà de ce silence (s), une voix commence une nouvelle plage.
PLAGE_PAUSE_S = 300.0

_modele = None
_indisponible: str | None = None  # raison, si la reconnaissance ne peut pas fonctionner
_verrou_modele = threading.Lock()  # chargement et calculs (un seul à la fois)
_verrou_fichiers = threading.Lock()  # profils et fichiers du jour

_RE_INCONNU = re.compile(r"inconnu-(.+)-(\d+)")


# --- Modèle ------------------------------------------------------------------------------------

def active() -> bool:
    """Vrai si la reconnaissance des voix est demandée et peut fonctionner."""
    return config.JOI_RECONNAISSANCE_VOIX and np is not None and _indisponible is None


def etat() -> str:
    if not config.JOI_RECONNAISSANCE_VOIX:
        return "désactivée"
    if np is None or _indisponible:
        return f"indisponible ({_indisponible or 'numpy manquant'})"
    return "prête" if _modele is not None else "modèle pas encore chargé"


def _charger():
    """Le modèle ECAPA, chargé une seule fois (téléchargé la première fois). None s'il manque une
    bibliothèque ; une autre erreur (réseau) remonte et sera retentée au prochain appel."""
    global _modele, _indisponible
    with _verrou_modele:
        if _modele is not None or _indisponible:
            return _modele
        try:
            import torch
            from speechbrain.inference.speaker import EncoderClassifier
            from speechbrain.utils.fetching import LocalStrategy
        except ImportError as e:
            _indisponible = f"bibliothèque manquante : {e.name}"
            log.warning("Reconnaissance des voix désactivée (%s). Voir le README : torch et speechbrain.",
                        _indisponible)
            return None
        # Joi converse pendant ce temps : on laisse la moitié du processeur au reste.
        torch.set_num_threads(max(1, (os.cpu_count() or 2) // 2))
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        debut = time.perf_counter()
        _modele = EncoderClassifier.from_hparams(
            source=SOURCE_MODELE,
            savedir=str(DOSSIER_MODELE),
            run_opts={"device": "cpu"},
            local_strategy=LocalStrategy.COPY,  # pas de liens symboliques sous Windows
        )
        _modele.eval()
        log.info("Modèle de reconnaissance des voix prêt en %.1f s", time.perf_counter() - debut)
        return _modele


def preparer() -> None:
    """Charge le modèle d'avance (au démarrage de Joi), pour qu'il soit prêt au premier tour."""
    if not config.JOI_RECONNAISSANCE_VOIX or np is None:
        return
    try:
        _charger()
    except Exception as e:
        log.warning("Modèle de reconnaissance des voix pas encore disponible : %s", e)


def _normer(e):
    return e / np.maximum(np.linalg.norm(e, axis=-1, keepdims=True), 1e-9)


def calculer(signal, morceaux: list[tuple[float, float]]):
    """Une empreinte normée par morceau (début et fin en secondes) du signal 16 kHz."""
    modele = _charger()
    if modele is None:
        raise RuntimeError(_indisponible or "modèle indisponible")
    import torch

    # Des lots de morceaux de durées voisines : moins de calcul perdu sur le remplissage.
    ordre = sorted(range(len(morceaux)), key=lambda i: morceaux[i][1] - morceaux[i][0])
    tries = [morceaux[i] for i in ordre]
    sorties = []
    for i in range(0, len(tries), LOT):
        lot = [signal[int(d * TAUX):int(f * TAUX)] for d, f in tries[i:i + LOT]]
        longueur = max(max(len(x) for x in lot), 1)
        ondes = np.zeros((len(lot), longueur), dtype=np.float32)
        for j, x in enumerate(lot):
            ondes[j, :len(x)] = x
        relatives = torch.tensor([max(len(x), 1) / longueur for x in lot], dtype=torch.float32)
        with _verrou_modele, torch.inference_mode():
            e = modele.encode_batch(torch.from_numpy(ondes), relatives)
        sorties.append(e.squeeze(1).cpu().numpy().astype(np.float32))
    if not sorties:
        return np.zeros((0, 192), dtype=np.float32)
    resultat = np.empty((len(morceaux), sorties[0].shape[1]), dtype=np.float32)
    resultat[ordre] = np.concatenate(sorties)
    return _normer(resultat)


def lire_audio(chemin: Path):
    """Le signal d'un fichier audio, en mono 16 kHz (float32)."""
    try:
        import soundfile

        signal, taux = soundfile.read(str(chemin), dtype="float32", always_2d=False)
    except Exception as e:  # libsndfile trop ancien pour le MP3 : on passe par ffmpeg
        if not shutil.which("ffmpeg"):
            raise RuntimeError(f"audio illisible ({e}) et ffmpeg introuvable") from e
        brut = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(chemin), "-f", "s16le", "-ac", "1", "-ar", str(TAUX), "-"],
            capture_output=True, check=True,
        ).stdout
        signal, taux = np.frombuffer(brut, dtype="<i2").astype(np.float32) / 32768, TAUX
    if signal.ndim > 1:
        signal = signal.mean(axis=1)
    if taux != TAUX:
        from scipy.signal import resample_poly

        signal = resample_poly(signal, TAUX, taux).astype(np.float32)
    return signal


# --- Fenêtres de parole ------------------------------------------------------------------------

@dataclass
class Fenetre:
    """Un passage de parole de 1,5 à 4 s, découpé à partir des mots d'AssemblyAI."""

    debut: int  # ms depuis le début du segment
    fin: int
    etiquette: str  # l'étiquette de voix d'AssemblyAI : un simple indice
    phrase: int  # numéro de la phrase (utterance) d'AssemblyAI
    texte: str

    @property
    def duree_s(self) -> float:
        return (self.fin - self.debut) / 1000


def _phrases(resultat: dict) -> list[dict]:
    """Les phrases d'une transcription, avec leurs mots (une seule sans reconnaissance des voix)."""
    phrases = resultat.get("utterances")
    if phrases is None:
        phrases = [{"speaker": "?", "start": 0, "text": resultat.get("text") or "",
                    "words": resultat.get("words") or []}]
    return phrases


def decouper(resultat: dict) -> list[Fenetre]:
    """Découpe la parole en fenêtres : mots consécutifs tant que le silence entre deux mots reste
    court, que la fenêtre ne dépasse pas la durée maximale et que l'étiquette d'AssemblyAI ne
    change pas. Les fenêtres trop courtes sont ignorées. On ne se fie jamais à la durée d'une
    phrase : une « phrase » d'AssemblyAI peut durer 30 min, silences compris."""
    silence = config.JOI_VOIX_SILENCE_S * 1000
    maxi = config.JOI_VOIX_FENETRE_MAX_S * 1000
    mini = config.JOI_VOIX_FENETRE_MIN_S * 1000
    fenetres: list[Fenetre] = []

    def fermer(mots: list[dict], etiquette: str, numero: int) -> None:
        if mots and mots[-1]["end"] - mots[0]["start"] >= mini:
            debut = int(mots[0]["start"])
            fenetres.append(Fenetre(debut, int(min(mots[-1]["end"], debut + maxi)), etiquette, numero,
                                    " ".join(m.get("text") or "" for m in mots)))

    for numero, phrase in enumerate(_phrases(resultat)):
        courants: list[dict] = []
        etiquette_courante = "?"
        for mot in phrase.get("words") or []:
            if mot.get("start") is None or mot.get("end") is None:
                continue
            etiquette = str(mot.get("speaker") or phrase.get("speaker") or "?")
            if courants and (
                mot["start"] - courants[-1]["end"] > silence
                or mot["end"] - courants[0]["start"] > maxi
                or etiquette != etiquette_courante
            ):
                fermer(courants, etiquette_courante, numero)
                courants = []
            if not courants:
                etiquette_courante = etiquette
            courants.append(mot)
        fermer(courants, etiquette_courante, numero)
    return fenetres


def plages_de_parole(mots: list[dict], marge_ms: int = 100) -> list[tuple[int, int]]:
    """Les plages (ms) où l'on parle dans une liste de mots, sans les silences entre les phrases."""
    silence = config.JOI_VOIX_SILENCE_S * 1000
    plages: list[list[int]] = []
    for mot in mots:
        if mot.get("start") is None or mot.get("end") is None:
            continue
        debut, fin = int(mot["start"]) - marge_ms, int(mot["end"]) + marge_ms
        if plages and debut - plages[-1][1] <= silence:
            plages[-1][1] = max(plages[-1][1], fin)
        else:
            plages.append([max(debut, 0), fin])
    return [(d, f) for d, f in plages]


# --- Voix connues : toi, et (plus tard) les personnes qui ont donné leur accord -----------------

@dataclass
class Profil:
    nom: str
    empreinte: object  # vecteur normé
    echantillons: int


def _fichier_profil(nom: str) -> Path:
    return FICHIER_TOI if nom == "toi" else DOSSIER_PERSONNES / f"{nom}.npz"


def _lire_profil(nom: str) -> dict | None:
    fichier = _fichier_profil(nom)
    if not fichier.exists():
        return None
    try:
        with np.load(fichier, allow_pickle=False) as donnees:
            return {k: donnees[k] for k in donnees.files}
    except (OSError, ValueError) as e:
        log.warning("Empreinte de %s illisible : %s", nom, e)
        return None


def _moyenne(echantillons, durees):
    return _normer((echantillons * np.asarray(durees, dtype=np.float32)[:, None]).sum(axis=0))


def consentements() -> list[dict]:
    """Les personnes qui ont donné leur accord pour que Joi reconnaisse leur voix (nom, date,
    façon dont l'accord a été donné). Personne pour l'instant : l'ajout viendra plus tard."""
    try:
        return json.loads(FICHIER_CONSENTEMENTS.read_text(encoding="utf-8")).get("personnes", [])
    except (OSError, json.JSONDecodeError):
        return []


def profils_connus() -> list[Profil]:
    """Toi, plus les personnes dont l'accord est noté au registre (et seulement elles)."""
    noms = ["toi"] + [p["nom"] for p in consentements() if p.get("nom") and p["nom"] != "toi"]
    profils = []
    for nom in noms:
        donnees = _lire_profil(nom)
        if donnees is not None and len(donnees.get("echantillons", [])):
            profils.append(Profil(nom, _moyenne(donnees["echantillons"], donnees["durees"]),
                                  len(donnees["echantillons"])))
    return profils


def ajouter_echantillon(nom: str, empreinte, duree_s: float, source: str, verifier: bool = True) -> float | None:
    """Ajoute un échantillon à l'empreinte d'une voix connue (moyenne glissante). Renvoie la
    similarité avec l'empreinte d'avant (None si l'échantillon est écarté ou si c'est le premier)."""
    with _verrou_fichiers:
        donnees = _lire_profil(nom) or {}
        echantillons = donnees.get("echantillons", np.zeros((0, len(empreinte)), dtype=np.float32))
        durees = list(donnees.get("durees", []))
        quand = list(donnees.get("dates", []))
        sources = list(donnees.get("sources", []))
        similarite = None
        if len(echantillons):
            similarite = float(_moyenne(echantillons, durees) @ empreinte)
            if verifier and len(echantillons) >= ECHANTILLONS_FIABLES \
                    and similarite < config.JOI_VOIX_SEUIL_TOI - ECART_REJET:
                log.info("Tour écarté de l'empreinte de %s : voix trop différente (similarité %.2f)",
                         nom, similarite)
                return None
        echantillons = np.vstack([echantillons, empreinte[None, :]])[-ECHANTILLONS_MAX:]
        durees = (durees + [duree_s])[-ECHANTILLONS_MAX:]
        quand = (quand + [datetime.now().isoformat(timespec="seconds")])[-ECHANTILLONS_MAX:]
        sources = (sources + [source])[-ECHANTILLONS_MAX:]
        fichier = _fichier_profil(nom)
        fichier.parent.mkdir(parents=True, exist_ok=True)
        temporaire = fichier.with_suffix(".tmp")
        with temporaire.open("wb") as f:
            np.savez(f, echantillons=echantillons.astype(np.float32), durees=np.asarray(durees, np.float32),
                     dates=np.asarray(quand), sources=np.asarray(sources))
        temporaire.replace(fichier)
    return similarite


def retirer_echantillons(nom: str, garder_pas) -> int:
    """Retire de l'empreinte d'une voix connue les échantillons dont la source vérifie garder_pas.
    Renvoie le nombre d'échantillons retirés (l'empreinte est effacée s'il n'en reste aucun)."""
    with _verrou_fichiers:
        donnees = _lire_profil(nom)
        if donnees is None:
            return 0
        sources = [str(s) for s in donnees.get("sources", [])]
        garder = [i for i, source in enumerate(sources) if not garder_pas(source)]
        retires = len(sources) - len(garder)
        if not retires:
            return 0
        fichier = _fichier_profil(nom)
        if not garder:
            fichier.unlink(missing_ok=True)
            return retires
        temporaire = fichier.with_suffix(".tmp")
        with temporaire.open("wb") as f:
            np.savez(f, **{cle: valeurs[garder] for cle, valeurs in donnees.items()})
        temporaire.replace(fichier)
    return retires


def _nom_valide(nom: str) -> bool:
    return bool(re.fullmatch(r"[^\W\d_][\w' -]{0,39}", nom)) and nom.lower() != "toi"


def _a_consenti(nom: str) -> bool:
    return any(p.get("nom") == nom for p in consentements())


def ajouter_consentement(nom: str, facon: str) -> None:
    """Inscrit au registre l'accord exprès d'une personne pour que Joi reconnaisse sa voix."""
    with _verrou_fichiers:
        personnes = [p for p in consentements() if p.get("nom") != nom]
        personnes.append({"nom": nom, "date": datetime.now().isoformat(timespec="seconds"), "facon": facon})
        FICHIER_CONSENTEMENTS.parent.mkdir(parents=True, exist_ok=True)
        FICHIER_CONSENTEMENTS.write_text(json.dumps({"personnes": personnes}, ensure_ascii=False, indent=2),
                                         encoding="utf-8")


def enroler_pcm(pcm: bytes) -> float | None:
    """Ajoute à l'empreinte de « toi » l'audio d'un tour de conversation (PCM 16 bits, 16 kHz).
    L'audio n'est pas gardé : seule son empreinte l'est."""
    signal = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
    duree = len(signal) / TAUX
    if duree < ENROLEMENT_MIN_S:
        return None
    empreinte = calculer(signal, [(0.0, duree)])[0]
    similarite = ajouter_echantillon("toi", empreinte, duree, "conversation")
    if similarite is not None:
        log.info("Ta voix : tour de %.1f s appris (similarité avec ton empreinte : %.2f)", duree, similarite)
    return similarite


def oublier(nom: str) -> bool:
    """Efface l'empreinte d'une voix connue, et retire la personne du registre des accords."""
    with _verrou_fichiers:
        fichier = _fichier_profil(nom)
        existait = fichier.exists()
        fichier.unlink(missing_ok=True)
        if nom != "toi" and FICHIER_CONSENTEMENTS.exists():
            personnes = [p for p in consentements() if p.get("nom") != nom]
            FICHIER_CONSENTEMENTS.write_text(
                json.dumps({"personnes": personnes}, ensure_ascii=False, indent=2), encoding="utf-8")
    return existait


# --- Étiquetage d'un segment ---------------------------------------------------------------------

def regrouper(empreintes, seuil: float) -> list[int]:
    """Regroupement hiérarchique (lien moyen, distance cosinus) : deux groupes fusionnent tant que
    leur similarité moyenne dépasse le seuil. Numéros à partir de 1, par ordre d'apparition."""
    if len(empreintes) == 0:
        return []
    if len(empreintes) == 1:
        return [1]
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import pdist

    distances = np.clip(pdist(empreintes.astype(np.float64), metric="cosine"), 0, 2)
    groupes = fcluster(linkage(distances, method="average"), t=1 - seuil, criterion="distance")
    ordre: dict[int, int] = {}
    return [ordre.setdefault(int(g), len(ordre) + 1) for g in groupes]


def _centroides(fenetres: list[Fenetre], empreintes, indices: list[int], groupes: list[int]):
    """L'empreinte moyenne (pondérée par la durée) et la durée de chaque groupe."""
    k = max(groupes, default=0)
    centroides = np.zeros((k, empreintes.shape[1] if len(empreintes) else 192), dtype=np.float32)
    durees = np.zeros(k, dtype=np.float32)
    for i, g in zip(indices, groupes):
        centroides[g - 1] += empreintes[i] * fenetres[i].duree_s
        durees[g - 1] += fenetres[i].duree_s
    return (_normer(centroides) if k else centroides), durees


def etiqueter(fenetres: list[Fenetre], empreintes, profils: list[Profil], id_segment: str):
    """Donne une voix à chaque fenêtre d'un segment. Renvoie (voix, centroïdes des groupes inconnus,
    durées de ces groupes) ; voix est le contenu de HHMMSS.voix.json, sans aucune empreinte."""
    n = len(fenetres)
    noms: list[str | None] = [None] * n
    meilleures = np.zeros(n, dtype=np.float32)
    if profils and n:
        similarites = empreintes @ np.stack([p.empreinte for p in profils]).T
        indices = similarites.argmax(axis=1)
        meilleures = similarites.max(axis=1)
        seuil = config.JOI_VOIX_SEUIL_TOI
        noms = [profils[j].nom if s >= seuil else None for j, s in zip(indices, meilleures)]
        # Lissage : une fenêtre entourée de deux voisines de la même phrase, d'accord entre elles
        # mais pas avec elle, prend leur voix, sauf si sa propre similarité est nette.
        lisses = list(noms)
        for i in range(1, n - 1):
            avant, apres = fenetres[i - 1], fenetres[i + 1]
            if not (avant.phrase == fenetres[i].phrase == apres.phrase):
                continue
            voisine = noms[i - 1]
            if voisine != noms[i + 1] or voisine == noms[i]:
                continue
            if noms[i] is not None and meilleures[i] >= seuil + MARGE_LISSAGE:
                continue
            if voisine is None:
                lisses[i] = None
            else:
                j = next(k for k, p in enumerate(profils) if p.nom == voisine)
                if similarites[i, j] >= seuil - MARGE_LISSAGE:
                    lisses[i] = voisine
        noms = lisses

    inconnues = [i for i in range(n) if noms[i] is None]
    groupes = regrouper(empreintes[inconnues], config.JOI_VOIX_SEUIL_SEGMENT) if inconnues else []
    centroides, durees = _centroides(fenetres, empreintes, inconnues, groupes)

    # Deuxième passe : l'empreinte moyenne d'un groupe est bien plus nette que celle d'une fenêtre.
    # Un groupe très proche d'une voix connue est cette voix (sinon, les fenêtres de « toi » restées
    # sous le seuil formeraient un faux interlocuteur).
    if profils and len(durees):
        similarites_groupes = centroides @ np.stack([p.empreinte for p in profils]).T
        reconnus: dict[int, str] = {}
        for g in range(len(durees)):
            j = int(similarites_groupes[g].argmax())
            if durees[g] >= GROUPE_CONNU_MIN_S and similarites_groupes[g, j] >= config.JOI_VOIX_SEUIL_TOI + ECART_GROUPE_CONNU:
                reconnus[g + 1] = profils[j].nom
        if reconnus:
            restes = [(i, g) for i, g in zip(inconnues, groupes) if g not in reconnus]
            for i, g in zip(inconnues, groupes):
                if g in reconnus:
                    noms[i] = reconnus[g]
            renumerotes: dict[int, int] = {}
            inconnues = [i for i, _ in restes]
            groupes = [renumerotes.setdefault(g, len(renumerotes) + 1) for _, g in restes]
            centroides, durees = _centroides(fenetres, empreintes, inconnues, groupes)

    for i, g in zip(inconnues, groupes):
        noms[i] = f"inconnu-{id_segment}-{g}"

    voix = {
        "version": 1,
        "segment": id_segment,
        "profils": [p.nom for p in profils],
        "fenetres": [
            {"debut": f.debut, "fin": f.fin, "voix": nom, "etiquette": f.etiquette, "phrase": f.phrase,
             "similarite": round(float(s), 3)}
            for f, nom, s in zip(fenetres, noms, meilleures)
        ],
        "groupes": {str(g + 1): round(float(d), 1) for g, d in enumerate(durees)},
    }
    return voix, centroides, durees


def _id_segment(segment: dict) -> tuple[str, str]:
    """(jour, identifiant) d'un segment : « 2026-11-13/111304.mp3 » -> (« 2026-11-13 », « 111304 »)."""
    chemin = Path(segment["fichier"])
    return chemin.parent.name, chemin.stem


def fichier_voix(segment: dict) -> Path:
    return (config.DOSSIER_ENREGISTREMENTS / segment["fichier"]).with_suffix(".voix.json")


def lire_voix(segment: dict) -> dict | None:
    try:
        return json.loads(fichier_voix(segment).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def analyser_segment(mp3: Path, segment: dict, resultat: dict, cache_rejeu: bool = False) -> dict:
    """Reconnaît les voix d'un segment, avant que son audio soit effacé. Écrit HHMMSS.voix.json et
    les empreintes des groupes inconnus du segment. Renvoie le contenu de HHMMSS.voix.json.
    cache_rejeu : reprendre les empreintes des fenêtres déjà calculées par le rejeu (reconsolidation)."""
    jour, id_segment = _id_segment(segment)
    debut = time.perf_counter()
    fenetres = decouper(resultat)
    if cache_rejeu:
        empreintes = _empreintes_rejeu(jour, mp3, fenetres, cache=True)
        valides = [i for i in range(len(fenetres)) if np.linalg.norm(empreintes[i]) > 0]
        fenetres, empreintes = [fenetres[i] for i in valides], empreintes[valides]
    else:
        signal = lire_audio(mp3) if fenetres else np.zeros(0, dtype=np.float32)
        fenetres = [f for f in fenetres if f.fin * TAUX / 1000 <= len(signal) + TAUX // 10]
        empreintes = calculer(signal, [(f.debut / 1000, f.fin / 1000) for f in fenetres])
    profils = profils_connus()
    voix, centroides, durees = etiqueter(fenetres, empreintes, profils, id_segment)
    voix["debut"] = segment["debut"]

    with _verrou_fichiers:
        dossier = DOSSIER / jour
        dossier.mkdir(parents=True, exist_ok=True)
        with (dossier / f"{id_segment}.npz").open("wb") as f:
            np.savez(f, centroides=centroides, durees=durees)
    fichier_voix(segment).write_text(json.dumps(voix, ensure_ascii=False), encoding="utf-8")

    compte = Counter(f["voix"] for f in voix["fenetres"])
    log.info(
        "Voix du segment %s : %d fenêtre(s), %d de toi, %d groupe(s) inconnu(s) (%.0f s)",
        segment["fichier"], len(fenetres), sum(v for k, v in compte.items() if k == "toi"),
        len(voix["groupes"]), time.perf_counter() - debut,
    )
    return voix


def _fusionner(morceau: dict, suivant: dict) -> None:
    morceau["mots"] += suivant["mots"]
    morceau["fin"] = max(morceau["fin"], suivant["fin"])
    morceau["start"] = min(morceau["start"], suivant["start"])


def phrases_par_voix(resultat: dict, voix: dict) -> list[dict]:
    """Les phrases d'AssemblyAI réétiquetées avec les voix reconnues. Une phrase qui contient
    plusieurs voix est découpée. Chaque mot prend la voix de sa fenêtre ; hors fenêtre, celle de
    la fenêtre la plus proche dans la même phrase, sinon la voix la plus fréquente de son
    étiquette AssemblyAI dans le segment."""
    fenetres = sorted(voix.get("fenetres") or [], key=lambda f: f["debut"])
    debuts = [f["debut"] for f in fenetres]
    minimum = config.JOI_VOIX_FENETRE_MIN_S * 1000
    par_etiquette: dict[str, Counter] = defaultdict(Counter)
    for f in fenetres:
        par_etiquette[f.get("etiquette")][f["voix"]] += f["fin"] - f["debut"]

    def selon_etiquette(etiquette: str) -> str:
        compte = par_etiquette.get(etiquette)
        return compte.most_common(1)[0][0] if compte else "?"

    def voix_du_mot(mot: dict, numero: int, etiquette: str) -> str:
        milieu = (mot["start"] + mot["end"]) / 2
        i = bisect_right(debuts, milieu) - 1
        if i >= 0 and fenetres[i]["fin"] >= milieu:
            return fenetres[i]["voix"]
        voisines = [f for f in (fenetres[i] if i >= 0 else None, fenetres[i + 1] if i + 1 < len(fenetres) else None)
                    if f is not None and f.get("phrase") == numero]
        if voisines:
            return min(voisines, key=lambda f: min(abs(f["debut"] - milieu), abs(f["fin"] - milieu)))["voix"]
        return selon_etiquette(etiquette)

    resultat_phrases: list[dict] = []
    for numero, phrase in enumerate(_phrases(resultat)):
        etiquette = str(phrase.get("speaker") or "?")
        mots = [m for m in phrase.get("words") or [] if m.get("start") is not None and m.get("end") is not None]
        if not mots:
            resultat_phrases.append({"start": phrase.get("start") or 0, "speaker": selon_etiquette(etiquette),
                                     "text": phrase.get("text") or ""})
            continue
        morceaux: list[dict] = []
        for mot in mots:
            v = voix_du_mot(mot, numero, str(mot.get("speaker") or etiquette))
            if not morceaux or morceaux[-1]["speaker"] != v:
                morceaux.append({"start": mot["start"], "fin": mot["end"], "speaker": v, "mots": []})
            morceaux[-1]["mots"].append(mot.get("text") or "")
            morceaux[-1]["fin"] = mot["end"]
        # Un morceau plus court qu'une fenêtre (« Si », « euh ») n'a pas pu être attribué de façon
        # fiable : il rejoint le morceau précédent de la phrase (ou le suivant, en début de phrase).
        i = 0
        while len(morceaux) > 1 and i < len(morceaux):
            if morceaux[i]["fin"] - morceaux[i]["start"] >= minimum:
                i += 1
                continue
            if i > 0:
                _fusionner(morceaux[i - 1], morceaux.pop(i))
                # Le morceau suivant peut maintenant toucher un morceau de la même voix.
                if i < len(morceaux) and morceaux[i - 1]["speaker"] == morceaux[i]["speaker"]:
                    _fusionner(morceaux[i - 1], morceaux.pop(i))
                i -= 1
            else:
                suivant = morceaux.pop(1)
                _fusionner(morceaux[0], suivant)
                morceaux[0]["speaker"] = suivant["speaker"]
        resultat_phrases.extend(morceaux)
    for p in resultat_phrases:
        p.pop("fin", None)
        if "mots" in p:
            p["text"] = " ".join(p.pop("mots"))
    return resultat_phrases


# --- La journée : rattachement des groupes, bilan, effacement --------------------------------------

class Journee:
    """Les voix inconnues d'une journée. Les groupes de chaque segment y sont rattachés dans l'ordre
    chronologique : au plus proche s'il ressemble assez, sinon à une nouvelle voix.

    Les voix connues (toi) servent aussi de point d'attache : les fenêtres de ta voix restées sous
    le seuil d'une fenêtre seule forment de petits groupes, qui doivent te revenir plutôt que de
    devenir de faux interlocuteurs. Un tel groupe est affecté au nom (« toi ») plutôt qu'à un numéro."""

    def __init__(self, profils: list[Profil] | None = None):
        self.numeros: list[int] = []
        self.centroides: list = []
        self.durees: list[float] = []
        self.affectations: dict[str, dict[str, int | str]] = {}
        self.prochain = 1
        self.profils = profils or []

    def rattacher(self, id_segment: str, centroides, durees) -> dict[str, int | str]:
        resultat: dict[str, int | str] = {}
        # Les groupes les plus longs d'abord : leur empreinte moyenne est la plus fiable.
        for i in sorted(range(len(durees)), key=lambda i: -float(durees[i])):
            c, d = centroides[i], float(durees[i])
            # L'empreinte moyenne d'un petit groupe (une ou deux fenêtres) est bruitée : on est moins
            # exigeant, sinon chaque bribe deviendrait une « voix de passage » de plus.
            seuil = config.JOI_VOIX_SEUIL_JOURNEE - (ECART_PETIT_GROUPE if d < PETIT_GROUPE_S else 0)
            meilleure_voix = float((np.stack(self.centroides) @ c).max()) if self.centroides else -1.0
            if self.profils:
                proches = np.stack([p.empreinte for p in self.profils]) @ c
                j = int(proches.argmax())
                if proches[j] >= seuil and proches[j] >= meilleure_voix:
                    resultat[str(i + 1)] = self.profils[j].nom
                    continue
            if self.centroides:
                similarites = np.stack(self.centroides) @ c
                j = int(similarites.argmax())
                if similarites[j] >= seuil:
                    self.centroides[j] = _normer(self.centroides[j] * self.durees[j] + c * d)
                    self.durees[j] += d
                    resultat[str(i + 1)] = self.numeros[j]
                    continue
            self.numeros.append(self.prochain)
            self.centroides.append(c)
            self.durees.append(d)
            resultat[str(i + 1)] = self.prochain
            self.prochain += 1
        self.affectations[id_segment] = resultat
        return resultat


def _fichier_journee(jour: str) -> Path:
    return config.DOSSIER_ENREGISTREMENTS / jour / "voix-journee.json"


def _lire_json(fichier: Path) -> dict:
    try:
        return json.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _ecrire_json(fichier: Path, contenu: dict) -> None:
    temporaire = fichier.with_suffix(".tmp")
    temporaire.write_text(json.dumps(contenu, ensure_ascii=False, indent=2), encoding="utf-8")
    temporaire.replace(fichier)


def _charger_journee(jour: str) -> tuple[Journee, dict]:
    etat_jour = _lire_json(_fichier_journee(jour))
    journee = Journee(profils_connus())
    journee.affectations = etat_jour.get("affectations", {})
    numeros = [n for a in journee.affectations.values() for n in a.values() if isinstance(n, int)]
    journee.prochain = max(numeros, default=0) + 1
    fichier = DOSSIER / jour / "journee.npz"
    if fichier.exists():
        with np.load(fichier, allow_pickle=False) as donnees:
            journee.numeros = [int(n) for n in donnees["numeros"]]
            journee.centroides = list(donnees["centroides"])
            journee.durees = [float(d) for d in donnees["durees"]]
    return journee, etat_jour


def rattacher_jour(jour: str) -> dict[str, dict[str, int]]:
    """Rattache aux voix de la journée les segments analysés qui ne le sont pas encore. Renvoie les
    affectations : {segment: {groupe du segment: numéro de la voix du jour}}."""
    if np is None:
        return _lire_json(_fichier_journee(jour)).get("affectations", {})
    with _verrou_fichiers:
        journee, etat_jour = _charger_journee(jour)
        dossier = DOSSIER / jour
        nouveaux = False
        for fichier in sorted(dossier.glob("*.npz")) if dossier.exists() else []:
            if fichier.stem == "journee" or fichier.stem in journee.affectations:
                continue
            with np.load(fichier, allow_pickle=False) as donnees:
                journee.rattacher(fichier.stem, donnees["centroides"], donnees["durees"])
            nouveaux = True
        if nouveaux:
            with (dossier / "journee.npz").open("wb") as f:
                np.savez(f, numeros=np.asarray(journee.numeros, dtype=np.int32),
                         centroides=np.asarray(journee.centroides, dtype=np.float32).reshape(-1, 192),
                         durees=np.asarray(journee.durees, dtype=np.float32))
            etat_jour["affectations"] = journee.affectations
            _ecrire_json(_fichier_journee(jour), etat_jour)
        return journee.affectations


def nom_affiche(locuteur: str, affectations: dict[str, dict[str, int]]) -> str:
    """« inconnu-111304-2 » -> « voix inconnue 3 » (la voix du jour à laquelle le groupe est rattaché)."""
    m = _RE_INCONNU.fullmatch(locuteur)
    if not m:
        return locuteur
    numero = affectations.get(m.group(1), {}).get(m.group(2))
    if isinstance(numero, str):  # un groupe rattaché à une voix connue (toi)
        return numero
    return f"voix inconnue {numero}" if numero else "voix inconnue"


def _voix_du_jour(segments: list[tuple[datetime, dict]], affectations: dict) -> list[tuple[datetime, datetime, str, str]]:
    """Toutes les fenêtres de la journée, dans l'ordre : (début, fin, voix, segment)."""
    fenetres = []
    for debut, voix in segments:
        for f in voix.get("fenetres") or []:
            nom = nom_affiche(f["voix"], {voix.get("segment"): affectations.get(voix.get("segment"), {})})
            if nom == "voix inconnue":  # groupe pas encore rattaché
                continue
            fenetres.append((debut + timedelta(milliseconds=f["debut"]),
                             debut + timedelta(milliseconds=f["fin"]), nom, voix.get("segment")))
    fenetres.sort()
    return fenetres


def calculer_bilan(segments: list[tuple[datetime, dict]], affectations: dict) -> dict:
    """Le bilan des voix d'une journée : pour chaque voix, sa durée de parole, ses plages horaires,
    ses alternances avec les autres, et sa classe (toi, interlocuteur, média probable, passage)."""
    fenetres = _voix_du_jour(segments, affectations)
    voix: dict[str, dict] = {}
    for debut, fin, nom, segment in fenetres:
        v = voix.setdefault(nom, {"nom": nom, "duree_s": 0.0, "plages": [], "segments": set(),
                                  "alternances": 0, "avec_toi": 0})
        v["duree_s"] += (fin - debut).total_seconds()
        v["segments"].add(segment)
        if v["plages"] and (debut - v["plages"][-1][1]).total_seconds() <= PLAGE_PAUSE_S:
            v["plages"][-1][1] = max(v["plages"][-1][1], fin)
        else:
            v["plages"].append([debut, fin])
    for (_, fin_a, a, _), (debut_b, _, b, _) in zip(fenetres, fenetres[1:]):
        if a != b and (debut_b - fin_a).total_seconds() <= ALTERNANCE_S:
            voix[a]["alternances"] += 1
            voix[b]["alternances"] += 1
            if a == "toi":
                voix[b]["avec_toi"] += 1
            if b == "toi":
                voix[a]["avec_toi"] += 1

    for v in voix.values():
        minutes = max(v["duree_s"] / 60, 1e-9)
        if not v["nom"].startswith("voix inconnue"):
            v["classe"] = "connue"
        elif v["duree_s"] >= MEDIA_MIN_S and v["avec_toi"] == 0 \
                and v["alternances"] / minutes < MEDIA_ALTERNANCES_MINUTE:
            v["classe"] = "media"
        elif v["duree_s"] >= config.JOI_VOIX_INTERLOCUTEUR_S:
            v["classe"] = "interlocuteur"
        else:
            v["classe"] = "passage"
        v["segments"] = sorted(v["segments"])
        v["plages"] = [[d.strftime("%H:%M"), f.strftime("%H:%M")] for d, f in v["plages"]]
        v["duree_s"] = round(v["duree_s"], 1)

    def numero(v: dict) -> int:
        m = re.search(r"\d+$", v["nom"])
        return int(m.group()) if m else 0

    return {"voix": sorted(voix.values(), key=lambda v: (v["classe"] != "connue", numero(v)))}


def _segments_du_jour(jour: str) -> list[tuple[datetime, dict]]:
    segments = []
    for fichier in sorted((config.DOSSIER_ENREGISTREMENTS / jour).glob("*.voix.json")):
        voix = _lire_json(fichier)
        if voix.get("debut"):
            segments.append((datetime.fromisoformat(voix["debut"]), voix))
    return segments


def bilan_jour(jour: str) -> dict:
    return calculer_bilan(_segments_du_jour(jour), rattacher_jour(jour))


def _duree(secondes: float) -> str:
    if secondes < 60:
        return f"{secondes:.0f} s"
    if secondes < 3600:
        return f"{secondes / 60:.0f} min"
    return f"{int(secondes // 3600)} h {int(secondes % 3600 // 60):02d}"


def _plages(v: dict, maxi: int = 4) -> str:
    plages = [f"{d}–{f}" if d != f else d for d, f in v["plages"]]
    return ", ".join(plages[:maxi]) + (" …" if len(plages) > maxi else "")


def legende(jour: str) -> str:
    """Les voix de la journée jusqu'ici, pour la bibliothécaire."""
    bilan = bilan_jour(jour)
    if not bilan["voix"]:
        return ""
    noms = {"interlocuteur": "interlocuteur", "media": "voix de média probable (vidéo, conférence, "
            "haut-parleur : pas une personne présente)", "passage": "voix de passage", "connue": "voix reconnue"}
    lignes = [f"- {v['nom']} : {_duree(v['duree_s'])} de parole ({_plages(v)}), {noms[v['classe']]}"
              for v in bilan["voix"] if v["classe"] != "passage"]
    passages = [v["nom"].removeprefix("voix inconnue ") for v in bilan["voix"] if v["classe"] == "passage"]
    if passages:
        lignes.append(f"- voix de passage (quelques secondes chacune, peu fiables) : voix inconnue "
                      f"{', '.join(passages)}")
    if not any(v["nom"] == "toi" for v in bilan["voix"]):
        lignes.append("- toi : voix pas encore reconnue aujourd'hui (son empreinte n'est peut-être pas "
                      "encore apprise) ; « toi » est peut-être l'une des voix inconnues.")
    return "\n".join(lignes)


def texte_bilan(bilan: dict) -> str:
    """Le bilan du soir, en Markdown, pour le résumé de la journée."""
    par_classe: dict[str, list[dict]] = defaultdict(list)
    for v in bilan["voix"]:
        par_classe[v["classe"]].append(v)
    toi = next((v for v in par_classe["connue"] if v["nom"] == "toi"), None)
    interlocuteurs, medias, passages = par_classe["interlocuteur"], par_classe["media"], par_classe["passage"]

    # Les personnes nommées comptent comme interlocuteurs, avec la même règle de durée que les autres.
    n = len(interlocuteurs) + sum(1 for v in par_classe["connue"]
                                  if v is not toi and v["duree_s"] >= config.JOI_VOIX_INTERLOCUTEUR_S)
    if toi:
        phrase = (f"{n} interlocuteur{'s' if n > 1 else ''} en plus de toi" if n
                  else "Aucun interlocuteur en plus de toi")
    else:
        phrase = f"{n} voix d'interlocuteur{'s' if n > 1 else ''}, toi compris sans doute" if n \
            else "Aucun interlocuteur"
    if passages:
        phrase += ", et quelques voix de passage" if len(passages) > 1 else ", et une voix de passage"
    lignes = ["## Voix entendues", "", f"{phrase}.", ""]
    if toi:
        lignes.append(f"- Toi : {_duree(toi['duree_s'])} de parole reconnue ({_plages(toi)}).")
    else:
        lignes.append("- Toi : voix pas reconnue (empreinte pas encore apprise) ; tu es sans doute "
                      "l'un des interlocuteurs ci-dessous.")
    for v in par_classe["connue"]:
        if v is not toi:
            lignes.append(f"- {v['nom']} : {_duree(v['duree_s'])} ({_plages(v)}).")
    for v in interlocuteurs:
        lignes.append(f"- {v['nom'].capitalize()} : {_duree(v['duree_s'])} ({_plages(v)}).")
    if medias:
        lignes.append("- Voix de média probables (vidéo, conférence, haut-parleur) : "
                      + "; ".join(f"{v['nom']}, {_duree(v['duree_s'])} ({_plages(v, 2)})" for v in medias) + ".")
    if passages:
        lignes.append(f"- {len(passages)} voix de passage (moins de {config.JOI_VOIX_INTERLOCUTEUR_S:.0f} s "
                      "chacune).")
    lignes += ["", "_Reconnaissance locale des voix ; les empreintes anonymes de la journée sont effacées._"]
    return "\n".join(lignes)


def journees_ouvertes() -> list[str]:
    """Les jours qui ont encore des empreintes anonymes."""
    if not DOSSIER.exists():
        return []
    return sorted(d.name for d in DOSSIER.iterdir() if d.is_dir() and re.fullmatch(r"\d{4}-\d\d-\d\d", d.name))


def heure_fin_journee() -> dt_time:
    """JOI_FIN_JOURNEE (« 18:00 », « 18h30 », « 18 ») ; 18:00 si la valeur est illisible."""
    m = re.fullmatch(r"(\d{1,2})(?:\s*[:hH]\s*(\d{2})?)?", config.JOI_FIN_JOURNEE.strip())
    if m and int(m.group(1)) < 24 and int(m.group(2) or 0) < 60:
        return dt_time(int(m.group(1)), int(m.group(2) or 0))
    log.warning("JOI_FIN_JOURNEE illisible (%r) : 18:00 par défaut.", config.JOI_FIN_JOURNEE)
    return dt_time(18, 0)


def fin_de_journee_passee(jour: str, maintenant: datetime | None = None) -> bool:
    maintenant = maintenant or datetime.now()
    aujourdhui = maintenant.date().isoformat()
    if jour != aujourdhui:
        return jour < aujourdhui
    return maintenant.time() >= heure_fin_journee()


def preparer_bilan(jour: str) -> str:
    """Calcule le bilan du soir et le garde dans voix-journee.json (sans empreinte). Renvoie son texte."""
    bilan = bilan_jour(jour)
    with _verrou_fichiers:
        etat_jour = _lire_json(_fichier_journee(jour))
        etat_jour.setdefault("bilans", []).append({"le": datetime.now().isoformat(timespec="seconds"), **bilan})
        _fichier_journee(jour).parent.mkdir(parents=True, exist_ok=True)
        _ecrire_json(_fichier_journee(jour), etat_jour)
    return texte_bilan(bilan) if bilan["voix"] else ""


def effacer_journee(jour: str) -> None:
    """Efface toutes les empreintes anonymes d'une journée. Les étiquettes (voix.json) restent."""
    with _verrou_fichiers:
        shutil.rmtree(DOSSIER / jour, ignore_errors=True)
    log.info("Empreintes anonymes du %s effacées.", jour)


# --- Rejeu et commandes -----------------------------------------------------------------------------

def _segments_enregistres(jour: str) -> list[tuple[datetime, Path, dict]]:
    """Les segments d'une journée qui ont encore leur audio et leur transcription."""
    from . import enregistreur

    debuts = {
        s["fichier"]: datetime.fromisoformat(s["debut"])
        for _chemin, manifeste, _active in enregistreur.seances()
        for s in manifeste["segments"] if s["fichier"].startswith(jour + "/")
    }
    segments = []
    for mp3 in sorted((config.DOSSIER_ENREGISTREMENTS / jour).glob("*.mp3")):
        brut = mp3.with_suffix(".json")
        if not brut.exists():
            continue
        debut = debuts.get(f"{jour}/{mp3.name}") or datetime.strptime(f"{jour} {mp3.stem[:6]}", "%Y-%m-%d %H%M%S")
        segments.append((debut, mp3, json.loads(brut.read_text(encoding="utf-8"))))
    return segments


def _empreintes_rejeu(jour: str, mp3: Path, fenetres: list[Fenetre], cache: bool):
    fichier = DOSSIER_REJEU / jour / f"{mp3.stem}.npz"
    bornes = np.asarray([[f.debut, f.fin] for f in fenetres], dtype=np.int64).reshape(-1, 2)
    if cache and fichier.exists():
        with np.load(fichier, allow_pickle=False) as donnees:
            if donnees["bornes"].shape == bornes.shape and (donnees["bornes"] == bornes).all():
                return donnees["empreintes"]
    signal = lire_audio(mp3)
    fenetres_valides = [f.fin * TAUX / 1000 <= len(signal) + TAUX // 10 for f in fenetres]
    empreintes = np.zeros((len(fenetres), 192), dtype=np.float32)
    garder = [i for i, ok in enumerate(fenetres_valides) if ok]
    if garder:
        empreintes[garder] = calculer(signal, [(fenetres[i].debut / 1000, fenetres[i].fin / 1000) for i in garder])
    if cache:
        fichier.parent.mkdir(parents=True, exist_ok=True)
        with fichier.open("wb") as f:
            np.savez(f, bornes=bornes, empreintes=empreintes)
    return empreintes


@dataclass
class _SegmentRejoue:
    debut: datetime
    stem: str
    fenetres: list[Fenetre]
    empreintes: object
    voix: dict
    affectation: dict[str, int]

    def voix_du_jour(self, i: int) -> str:
        return nom_affiche(self.voix["fenetres"][i]["voix"], {self.stem: self.affectation})


def _analyser_rejeu(jour: str, segments, profils: list[Profil], cache: bool) -> tuple[Journee, list[_SegmentRejoue]]:
    """Le travail du soir sur une journée enregistrée, en mémoire : fenêtres, empreintes (en cache),
    voix de chaque segment, rattachement aux voix de la journée."""
    journee = Journee(profils)
    analyses = []
    for debut, mp3, resultat in segments:
        fenetres = decouper(resultat)
        empreintes = _empreintes_rejeu(jour, mp3, fenetres, cache)
        valides = [i for i in range(len(fenetres)) if np.linalg.norm(empreintes[i]) > 0]
        fenetres = [fenetres[i] for i in valides]
        empreintes = empreintes[valides]
        voix, centroides, durees = etiqueter(fenetres, empreintes, profils, mp3.stem)
        voix["debut"] = debut.isoformat()
        affectation = journee.rattacher(mp3.stem, centroides, durees)
        analyses.append(_SegmentRejoue(debut, mp3.stem, fenetres, empreintes, voix, affectation))
    return journee, analyses


def _passage(a: "_SegmentRejoue", i: int, nom: str, maxi_ms: int = 8000) -> tuple[int, int, str]:
    """Un passage à écouter : la fenêtre i, prolongée par les fenêtres suivantes de la même voix
    (tant qu'elles se suivent de près), jusqu'à environ 8 s. (début ms, fin ms, texte)."""
    debut, fin, textes = a.fenetres[i].debut, a.fenetres[i].fin, [a.fenetres[i].texte]
    for j in range(i + 1, len(a.fenetres)):
        f = a.fenetres[j]
        if a.voix_du_jour(j) != nom or f.debut - fin > 1000 or f.fin - debut > maxi_ms:
            break
        fin = f.fin
        textes.append(f.texte)
    return debut, fin, " ".join(textes)


def jouer(signal) -> None:
    """Joue un signal 16 kHz dans le haut-parleur (winsound sous Windows, sinon ffplay)."""
    import io
    import wave

    crete = float(np.abs(signal).max()) if len(signal) else 0.0
    if crete > 0:  # les enregistrements du micro sont souvent faibles : on les monte au même niveau
        signal = signal * (0.9 / crete)
    pcm = (np.clip(signal, -1, 1) * 32767).astype("<i2").tobytes()
    tampon = io.BytesIO()
    with wave.open(tampon, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(TAUX)
        wav.writeframes(pcm)
    try:
        import winsound

        winsound.PlaySound(tampon.getvalue(), winsound.SND_MEMORY)
    except ImportError:
        if not shutil.which("ffplay"):
            print("    (lecture impossible : ni winsound ni ffplay)")
            return
        subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-"],
                       input=tampon.getvalue(), check=False)


def enroler_voix(jour: str, numero: int, confirme: bool = False, ecouter: bool = False,
                 personne: str = "toi") -> int:
    """Apprend une voix à partir d'une voix trouvée par le rejeu (« voix inconnue N ») : pas besoin de
    trouver une plage où la personne parle seule, il suffit de la reconnaître dans les extraits.
    Seules les fenêtres les plus typiques de cette voix servent (la moitié la plus proche de son
    empreinte moyenne), pour écarter celles qui auraient été mal regroupées.

    Les échantillons déjà tirés de cette journée pour cette personne sont remplacés : si l'on s'est
    trompé de voix, on recommence simplement avec le bon numéro. Une autre personne que toi n'est
    acceptée que si son accord est inscrit au registre (commande « consentement »)."""
    if personne != "toi" and not _a_consenti(personne):
        print(f"{personne} n'est pas au registre des accords. Une empreinte vocale est une donnée "
              f"biométrique : demande-lui d'abord son accord exprès, puis inscris-le avec\n"
              f"  python -m joi.empreintes consentement ajouter {personne} --facon \"…\"")
        return 0
    segments = _segments_enregistres(jour)
    if not segments:
        print(f"Aucun segment avec audio et transcription pour le {jour}.")
        return 0
    profils = profils_connus()  # mêmes numéros que « rejouer », lancé dans le même état
    _journee, analyses = _analyser_rejeu(jour, segments, profils, cache=True)
    nom = f"voix inconnue {numero}"
    siennes = [(a, i) for a in analyses for i in range(len(a.fenetres)) if a.voix_du_jour(i) == nom]
    if not siennes:
        print(f"Pas de « {nom} » le {jour}. Lance d'abord : python -m joi.empreintes rejouer {jour}")
        return 0
    empreintes = np.stack([a.empreintes[i] for a, i in siennes])
    durees = np.asarray([a.fenetres[i].duree_s for a, i in siennes], dtype=np.float32)
    centre = _normer((empreintes * durees[:, None]).sum(axis=0))
    similarites = empreintes @ centre
    gardees = sorted((k for k in range(len(siennes)) if similarites[k] >= np.median(similarites)),
                     key=lambda k: siennes[k][0].debut + timedelta(milliseconds=siennes[k][0].fenetres[siennes[k][1]].debut))
    total = float(durees[gardees].sum())
    print(f"{nom} : {len(siennes)} fenêtres ({_duree(float(durees.sum()))}) ; les {len(gardees)} plus typiques "
          f"({_duree(total)}) serviront. Extraits :")
    if ecouter:
        print("(chaque extrait est joué après son texte ; Ctrl+C pour arrêter l'écoute)")
    signaux: dict[str, object] = {}
    for k in gardees[::max(1, len(gardees) // 10)][:10]:
        a, i = siennes[k]
        debut, fin, texte = _passage(a, i, nom) if ecouter else (a.fenetres[i].debut, a.fenetres[i].fin,
                                                                 a.fenetres[i].texte)
        instant = a.debut + timedelta(milliseconds=debut)
        print(f"  [{instant:%H:%M:%S}] {texte[:110] if not ecouter else texte}")
        if ecouter:
            try:
                if a.stem not in signaux:
                    signaux[a.stem] = lire_audio(config.DOSSIER_ENREGISTREMENTS / jour / f"{a.stem}.mp3")
                jouer(signaux[a.stem][max(0, int((debut - 300) * TAUX / 1000)):int((fin + 300) * TAUX / 1000)])
            except KeyboardInterrupt:
                print("  (écoute arrêtée)")
                ecouter = False
    if not confirme:
        try:
            question = "toi qui parles" if personne == "toi" else f"{personne} qui parle"
            reponse = input(f"\nEst-ce bien {question} dans ces extraits ? (o/n) ").strip().lower()
        except EOFError:
            reponse = ""
        if reponse not in ("o", "oui", "y", "yes"):
            print("Rien n'a été appris.")
            return 0

    retires = retirer_echantillons(personne, lambda source: source.endswith(f" du {jour}"))
    if retires:
        print(f"{retires} échantillon(s) déjà tiré(s) du {jour} pour {personne} : remplacé(s).")
    # Des échantillons d'environ 10 s de parole, répartis dans la journée ; on laisse de la place
    # aux échantillons des conversations (ECHANTILLONS_MAX au total).
    paquets, courant = [], []
    for k in gardees:
        courant.append(k)
        if durees[courant].sum() >= 10:
            paquets.append(courant)
            courant = []
    if courant and durees[courant].sum() >= ENROLEMENT_MIN_S:
        paquets.append(courant)
    place = max(1, ECHANTILLONS_MAX * 2 // 3)
    if len(paquets) > place:
        paquets = [paquets[int(j * len(paquets) / place)] for j in range(place)]
    for paquet in paquets:
        echantillon = _normer((empreintes[paquet] * durees[paquet, None]).sum(axis=0))
        ajouter_echantillon(personne, echantillon, float(durees[paquet].sum()), f"{nom} du {jour}", verifier=False)
    cible = "ton empreinte" if personne == "toi" else f"l'empreinte de {personne}"
    print(f"\n{len(paquets)} échantillon(s) ajouté(s) à {cible}. Vérifie avec : "
          f"python -m joi.empreintes rejouer {jour}")
    return len(paquets)


def _lister_voix(valeurs) -> str:
    """Les voix du jour d'un segment : les numéros dans l'ordre, puis les voix connues (toi…)."""
    valeurs = set(valeurs)
    return ", ".join([str(n) for n in sorted(v for v in valeurs if isinstance(v, int))]
                     + sorted(v for v in valeurs if isinstance(v, str))) or "-"


def rejouer(jour: str, cache: bool = True, details: bool = False) -> dict:
    """Rejoue la reconnaissance des voix d'une journée à partir de ses MP3 et transcriptions, sans
    AssemblyAI et sans rien écrire dans le journal ni la mémoire. Affiche les voix trouvées."""
    segments = _segments_enregistres(jour)
    if not segments:
        print(f"Aucun segment avec audio et transcription pour le {jour}.")
        return {}
    profils = profils_connus()
    print(f"Rejeu du {jour} : {len(segments)} segment(s). Voix connues : "
          f"{', '.join(f'{p.nom} ({p.echantillons} échantillons)' for p in profils) or 'aucune'}.")
    print(f"Seuils : toi {config.JOI_VOIX_SEUIL_TOI}, segment {config.JOI_VOIX_SEUIL_SEGMENT}, "
          f"journée {config.JOI_VOIX_SEUIL_JOURNEE} ; fenêtres de {config.JOI_VOIX_FENETRE_MIN_S} à "
          f"{config.JOI_VOIX_FENETRE_MAX_S} s.\n")

    journee, analyses = _analyser_rejeu(jour, segments, profils, cache)
    voix_segments = [(a.debut, a.voix) for a in analyses]
    textes = {(a.stem, f.debut): f.texte for a in analyses for f in a.fenetres}
    for a in analyses:
        debut, voix, affectation = a.debut, a.voix, a.affectation
        etiquettes = sorted({f.etiquette for f in a.fenetres})
        print(f"{debut:%H:%M} {a.stem} : {len(a.fenetres)} fenêtres, AssemblyAI {'/'.join(etiquettes) or '-'}"
              f" -> voix du jour {_lister_voix(affectation.values())}"
              + (f", toi {sum(f['fin'] - f['debut'] for f in voix['fenetres'] if f['voix'] == 'toi') / 1000:.0f} s"
                 if profils else ""))
        if details:
            croise: dict[str, Counter] = defaultdict(Counter)
            for f in voix["fenetres"]:
                croise[nom_affiche(f["voix"], {a.stem: affectation})][f["etiquette"]] += (f["fin"] - f["debut"]) / 1000
            for nom, compte in sorted(croise.items()):
                print(f"      {nom:<18} " + ", ".join(f"{e} {s:.0f} s" for e, s in compte.most_common()))

    bilan = calculer_bilan(voix_segments, journee.affectations)
    print()
    classes = {"connue": "", "interlocuteur": "INTERLOCUTEUR", "media": "MÉDIA PROBABLE", "passage": "passage"}
    for v in bilan["voix"]:
        print(f"{v['nom']:<18} {_duree(v['duree_s']):>8}  {classes[v['classe']]:<15} "
              f"alternances {v['alternances']} (avec toi {v['avec_toi']})  {_plages(v, 6)}")
        if v["classe"] == "passage" and not details:
            continue
        # Trois extraits : les plus longues fenêtres, réparties dans la journée.
        siennes = [(d, s, f) for d, s in voix_segments for f in s["fenetres"]
                   if nom_affiche(f["voix"], {s["segment"]: journee.affectations[s["segment"]]}) == v["nom"]]
        siennes.sort(key=lambda x: x[0] + timedelta(milliseconds=x[2]["debut"]))
        tiers = [siennes[i * len(siennes) // 3:(i + 1) * len(siennes) // 3] for i in range(3)]
        for tranche in tiers:
            if not tranche:
                continue
            d, s, f = max(tranche, key=lambda x: x[2]["fin"] - x[2]["debut"])
            instant = d + timedelta(milliseconds=f["debut"])
            print(f"      [{instant:%H:%M:%S}] {textes.get((s['segment'], f['debut']), '')[:110]}")
    print()
    print(texte_bilan(bilan))
    return bilan


def _lire_plage(jour: str, plage: str) -> tuple[datetime, datetime]:
    debut, _, fin = plage.partition("-")
    lire = lambda h: datetime.strptime(f"{jour} {h if h.count(':') == 2 else h + ':00'}",  # noqa: E731
                                       "%Y-%m-%d %H:%M:%S")
    return lire(debut), lire(fin)


def enroler_plages(jour: str, plages: list[str]) -> int:
    """Apprend ta voix sur des plages horaires d'une journée enregistrée où tu parles seul (un
    appel téléphonique, par exemple). Seuls les mots transcrits servent, pas les silences."""
    segments = _segments_enregistres(jour)
    ajoutes = 0
    for plage in plages:
        debut_plage, fin_plage = _lire_plage(jour, plage)
        for debut, mp3, resultat in segments:
            duree = timedelta(seconds=float(resultat.get("audio_duration") or 0))
            if debut > fin_plage or debut + duree < debut_plage:
                continue
            a = (debut_plage - debut).total_seconds() * 1000
            b = (fin_plage - debut).total_seconds() * 1000
            mots = [m for p in _phrases(resultat) for m in p.get("words") or []
                    if m.get("start") is not None and a <= m["start"] and m["end"] <= b]
            parole = plages_de_parole(mots)
            if not parole:
                continue
            signal = lire_audio(mp3)
            # Des morceaux d'environ 10 s de parole : chacun devient un échantillon.
            morceaux, courant, total = [], [], 0.0
            for d, f in parole:
                courant.append(signal[int(d * TAUX / 1000):int(f * TAUX / 1000)])
                total += (f - d) / 1000
                if total >= 10:
                    morceaux.append(np.concatenate(courant))
                    courant, total = [], 0.0
            if total >= ENROLEMENT_MIN_S:
                morceaux.append(np.concatenate(courant))
            for morceau in morceaux:
                empreinte = calculer(morceau, [(0.0, len(morceau) / TAUX)])[0]
                similarite = ajouter_echantillon("toi", empreinte, len(morceau) / TAUX, f"plage {jour} {plage}",
                                                 verifier=False)
                ajoutes += 1
                print(f"  {plage} ({mp3.stem}) : {len(morceau) / TAUX:.1f} s de parole apprise"
                      + (f", similarité avec ton empreinte : {similarite:.2f}" if similarite is not None else ""))
    return ajoutes


def controle() -> None:
    """Premier contrôle du modèle : des phrases d'une même voix de synthèse (le cache de la voix de
    Joi) doivent donner des empreintes proches, et loin de voix humaines enregistrées."""
    phrases = sorted(config.DOSSIER_VOIX.glob("*.mp3"), key=lambda f: f.stat().st_size, reverse=True)[:6]
    if len(phrases) < 2:
        print("Pas assez de phrases dans donnees/voix/ pour le contrôle.")
        return
    signaux = [lire_audio(f) for f in phrases]
    empreintes = np.vstack([calculer(s, [(0.0, len(s) / TAUX)]) for s in signaux])
    similarites = empreintes @ empreintes.T
    print("Similarités entre phrases de la voix de Joi (doivent être élevées) :")
    for i, f in enumerate(phrases):
        print(f"  {f.stem[:8]} ({len(signaux[i]) / TAUX:4.1f} s) " + " ".join(f"{s:5.2f}" for s in similarites[i]))
    hors_diagonale = similarites[~np.eye(len(phrases), dtype=bool)]
    print(f"  moyenne {hors_diagonale.mean():.2f}, minimum {hors_diagonale.min():.2f}")

    for jour in sorted((d.name for d in config.DOSSIER_ENREGISTREMENTS.iterdir() if d.is_dir()), reverse=True):
        segments = _segments_enregistres(jour)
        if not segments:
            continue
        _debut, mp3, resultat = segments[len(segments) // 2]
        fenetres = decouper(resultat)[:40]
        humaines = calculer(lire_audio(mp3), [(f.debut / 1000, f.fin / 1000) for f in fenetres])
        croisees = humaines @ empreintes.T
        print(f"Similarités entre voix humaines ({jour}/{mp3.stem}, {len(fenetres)} fenêtres) et la voix "
              f"de Joi (doivent être basses) : moyenne {croisees.mean():.2f}, maximum {croisees.max():.2f}")
        break


def main(arguments: list[str] | None = None) -> None:
    analyseur = argparse.ArgumentParser(prog="python -m joi.empreintes",
                                        description="Reconnaissance des voix du mode écoute.")
    commandes = analyseur.add_subparsers(dest="commande", required=True)
    commandes.add_parser("controle", help="vérifie que le modèle reconnaît une même voix")
    r = commandes.add_parser("rejouer", help="rejoue une journée enregistrée (sans AssemblyAI)")
    r.add_argument("jour", help="AAAA-MM-JJ")
    r.add_argument("--sans-cache", action="store_true", help="recalcule les empreintes des fenêtres")
    r.add_argument("--details", action="store_true", help="détail par segment et extraits des voix de passage")
    r.add_argument("--seuil-toi", type=float)
    r.add_argument("--seuil-segment", type=float)
    r.add_argument("--seuil-journee", type=float)
    e = commandes.add_parser("enroler", help="apprend ta voix sur des plages où tu parles seul")
    e.add_argument("jour", help="AAAA-MM-JJ")
    e.add_argument("plages", nargs="+", help="HH:MM:SS-HH:MM:SS (heures réelles de la journée)")
    v = commandes.add_parser("enroler-voix",
                             help="apprend ta voix à partir d'une voix trouvée par le rejeu (voix inconnue N)")
    v.add_argument("jour", help="AAAA-MM-JJ")
    v.add_argument("numero", type=int, help="le numéro de la voix dans le rejeu (voix inconnue N)")
    v.add_argument("--oui", action="store_true", help="ne pas demander de confirmation")
    v.add_argument("--ecouter", action="store_true", help="joue chaque extrait dans le haut-parleur")
    v.add_argument("--nom", default="toi",
                   help="une autre personne que toi (son accord doit être au registre, voir « consentement »)")
    c = commandes.add_parser("consentement", help="registre des accords pour reconnaître la voix d'une personne")
    c.add_argument("action", choices=["ajouter", "retirer", "liste"])
    c.add_argument("nom", nargs="?")
    c.add_argument("--facon", help="comment l'accord a été donné (ex. « accord verbal, en personne »)")
    o = commandes.add_parser("oublier", help="efface une empreinte (toi, ou une personne)")
    o.add_argument("nom")
    commandes.add_parser("effacer-rejeu", help="efface le cache des empreintes du rejeu")
    b = commandes.add_parser("bilan", help="bilan des voix d'une journée maintenant, sans attendre le soir")
    b.add_argument("jour", help="AAAA-MM-JJ")
    rc = commandes.add_parser("reconsolider",
                              help="reprend une journée déjà apprise avec les voix reconnues (Copilot), puis son bilan")
    rc.add_argument("jour", help="AAAA-MM-JJ")
    args = analyseur.parse_args(arguments)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s : %(message)s")
    if args.commande == "controle":
        controle()
    elif args.commande == "rejouer":
        for nom, valeur in (("JOI_VOIX_SEUIL_TOI", args.seuil_toi), ("JOI_VOIX_SEUIL_SEGMENT", args.seuil_segment),
                            ("JOI_VOIX_SEUIL_JOURNEE", args.seuil_journee)):
            if valeur is not None:
                setattr(config, nom, valeur)
        rejouer(args.jour, cache=not args.sans_cache, details=args.details)
    elif args.commande == "enroler":
        n = enroler_plages(args.jour, args.plages)
        print(f"{n} échantillon(s) ajouté(s) à ton empreinte." if n else "Aucune parole trouvée dans ces plages.")
    elif args.commande == "enroler-voix":
        enroler_voix(args.jour, args.numero, confirme=args.oui, ecouter=args.ecouter, personne=args.nom)
    elif args.commande == "consentement":
        if args.action == "liste":
            for p in consentements() or [{"nom": "(personne)", "date": "", "facon": ""}]:
                print(f"{p['nom']}  {p.get('date', '')}  {p.get('facon', '')}")
        elif not args.nom or not _nom_valide(args.nom):
            print("Donne un prénom valide (lettres, espaces, tirets ; pas « toi »).")
        elif args.action == "ajouter":
            if not args.facon:
                print("Précise comment l'accord a été donné : --facon \"accord verbal, en personne\"")
            else:
                ajouter_consentement(args.nom, args.facon)
                print(f"Accord de {args.nom} inscrit. Tu peux maintenant : "
                      f"python -m joi.empreintes enroler-voix AAAA-MM-JJ N --nom {args.nom} --ecouter")
        else:
            efface = oublier(args.nom)
            print(f"Accord de {args.nom} retiré" + (", et son empreinte effacée." if efface else "."))
    elif args.commande == "oublier":
        print("Empreinte effacée." if oublier(args.nom) else "Aucune empreinte à ce nom.")
    elif args.commande in ("bilan", "reconsolider"):
        import asyncio

        from . import transcription

        # Le travail prend du temps (Copilot pour reconsolider) : on montre où il en est.
        logging.getLogger("joi").setLevel(logging.INFO)
        if args.commande == "reconsolider":
            asyncio.run(transcription.reconsolider_journee(args.jour))
        fermes = asyncio.run(transcription.fermer_journees(forcer=[args.jour]))
        print(f"Bilan du {args.jour} ajouté au résumé de la journée, empreintes anonymes effacées."
              if fermes else f"Aucun bilan pour le {args.jour} (pas de voix analysées, ou journée pas finie de traiter).")
    elif args.commande == "effacer-rejeu":
        shutil.rmtree(DOSSIER_REJEU, ignore_errors=True)
        print("Cache du rejeu effacé.")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    main()
