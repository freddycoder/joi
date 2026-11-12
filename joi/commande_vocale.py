"""Les commandes vocales qui changent le mode de Joi.

« Joi, écoute seulement » (conversation -> écoute)
    En conversation, la transcription en streaming est active : on cherche la phrase dans chaque
    tour final, avant de l'envoyer au cerveau. La comparaison tolère les erreurs habituelles
    (« joie », accents, ponctuation, « écoute seule »...).

« Joi, reviens » (écoute -> conversation)
    En écoute, le streaming est fermé (on ne paie pas). On reconnaît la phrase hors ligne avec Vosk
    et un petit modèle français (environ 40 Mo, téléchargé une seule fois dans donnees/vosk/).
    La reconnaissance est restreinte par une grammaire : Vosk ne choisit qu'entre quelques mots,
    ce qui demande peu de processeur. Le vocabulaire du modèle ne connaît pas « Joi », mais
    « joie » se prononce pareil.

Si Vosk n'est pas installé, si le modèle ne se télécharge pas, ou si JOI_COMMANDE_VOCALE=non,
le bouton de l'interface reste le seul moyen de revenir : Joi fonctionne quand même.
"""

import asyncio
import json
import logging
import re
import shutil
import unicodedata
import zipfile
from typing import Awaitable, Callable

from . import config

log = logging.getLogger("joi.commande_vocale")

# ---------- « Joi, écoute seulement » (dans la transcription en streaming) ----------

# Au-delà, ce n'est plus une commande mais une phrase qui contient les mots (« je lui ai dit... »).
MOTS_MAX_COMMANDE = 8
_COMMANDE_ECOUTE = re.compile(
    r"\b(?:joi|joie|joy|joey|joee)\b(?: \w+)? (?:ecoute|ecoutes|ecouter) (?:seulement|seule?|juste)\b"
)


def _normaliser(texte: str) -> str:
    """Minuscules, sans accents ni ponctuation : « Joie, écoute seule ! » -> « joie ecoute seule »."""
    sans_accents = unicodedata.normalize("NFD", texte.lower())
    sans_accents = "".join(c for c in sans_accents if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w]+", " ", sans_accents).split())


def demande_ecoute(texte: str) -> bool:
    """Vrai si ce tour de parole est la commande « Joi, écoute seulement »."""
    normalise = _normaliser(texte)
    return len(normalise.split()) <= MOTS_MAX_COMMANDE and bool(_COMMANDE_ECOUTE.search(normalise))


# ---------- « Joi, reviens » (Vosk, hors ligne) ----------

NOM_MODELE = "vosk-model-small-fr-0.22"
URL_MODELE = f"https://alphacephei.com/vosk/models/{NOM_MODELE}.zip"
DOSSIER_MODELE = config.DOSSIER_VOSK / NOM_MODELE
TAUX_ECHANTILLONNAGE = 16_000

# La phrase recherchée, plus des mots qui lui ressemblent : sans eux, Vosk ramène « toi, reviens »
# ou « joie, viens » à « joie reviens ». « [unk] » absorbe tout le reste (bruit, autres phrases).
GRAMMAIRE = ["joie reviens", "je reviens", "reviens", "joie", "viens", "toi", "moi", "revenir", "[unk]"]
# Confiance minimale de chacun des deux mots (de 0 à 1).
CONFIANCE_MIN = 0.9
# « reviens » doit finir la phrase : le mot suivant, s'il y en a un, vient après une pause (en s).
PAUSE_APRES = 0.3
# Audio en attente de reconnaissance (en paquets de ~50 ms) : au-delà, le plus vieux est oublié.
FILE_MAX = 200

_modele = None
_chargement: asyncio.Task | None = None


def _telecharger() -> None:
    """Télécharge et décompresse le modèle Vosk (une seule fois)."""
    import httpx

    config.DOSSIER_VOSK.mkdir(parents=True, exist_ok=True)
    archive = config.DOSSIER_VOSK / f"{NOM_MODELE}.zip.part"
    log.info("Commande vocale : téléchargement du modèle %s (environ 40 Mo)...", NOM_MODELE)
    with httpx.stream("GET", URL_MODELE, follow_redirects=True, timeout=60) as reponse:
        reponse.raise_for_status()
        with archive.open("wb") as fichier:
            for morceau in reponse.iter_bytes(1 << 20):
                fichier.write(morceau)
    temporaire = config.DOSSIER_VOSK / f"{NOM_MODELE}.tmp"
    shutil.rmtree(temporaire, ignore_errors=True)
    with zipfile.ZipFile(archive) as zip_:
        zip_.extractall(temporaire)
    # L'archive contient un dossier du nom du modèle.
    (temporaire / NOM_MODELE).replace(DOSSIER_MODELE)
    shutil.rmtree(temporaire, ignore_errors=True)
    archive.unlink(missing_ok=True)
    log.info("Commande vocale : modèle installé dans %s", DOSSIER_MODELE)


def _charger():
    try:
        import vosk
    except ImportError:
        log.warning("Commande vocale « Joi, reviens » indisponible : Vosk n'est pas installé "
                    "(pip install -r requirements.txt). Le bouton fonctionne toujours.")
        return None
    if not DOSSIER_MODELE.is_dir():
        _telecharger()
    vosk.SetLogLevel(-1)
    return vosk.Model(str(DOSSIER_MODELE))


async def preparer():
    """Le modèle Vosk, chargé une seule fois (et téléchargé au besoin). None si indisponible."""
    global _chargement
    if not config.JOI_COMMANDE_VOCALE:
        return None
    if _modele is not None:
        return _modele
    if _chargement is None:
        _chargement = asyncio.create_task(_preparer())
    return await asyncio.shield(_chargement)


async def _preparer():
    global _modele
    try:
        _modele = await asyncio.to_thread(_charger)
    except Exception as e:
        log.warning("Commande vocale « Joi, reviens » indisponible : %s. Le bouton fonctionne toujours.", e)
    return _modele


def _est_commande_retour(resultat: dict) -> bool:
    """Cherche « joie reviens », bien reconnu, et suivi d'une pause, dans un résultat de Vosk."""
    mots = resultat.get("result") or []
    for i in range(len(mots) - 1):
        joie, reviens = mots[i], mots[i + 1]
        if joie["word"] != "joie" or reviens["word"] != "reviens":
            continue
        log.info("Commande vocale candidate : « joie %.2f, reviens %.2f »", joie["conf"], reviens["conf"])
        if min(joie["conf"], reviens["conf"]) < CONFIANCE_MIN:
            continue
        if i + 2 < len(mots) and mots[i + 2]["start"] - reviens["end"] < PAUSE_APRES:
            continue
        return True
    return False


class DetecteurRetour:
    """En mode écoute, écoute le micro en attendant « Joi, reviens ».

    ecouter() reçoit le même PCM que l'enregistreur ; la reconnaissance se fait dans un fil séparé
    pour ne pas bloquer le serveur. Quand la phrase est reconnue, sur_retour() est appelée une fois.
    """

    def __init__(self, sur_retour: Callable[[], Awaitable[None]]):
        self._sur_retour = sur_retour
        self._file: asyncio.Queue[bytes] = asyncio.Queue(maxsize=FILE_MAX)
        self._tache: asyncio.Task | None = None
        self._pret = False

    def demarrer(self) -> None:
        if config.JOI_COMMANDE_VOCALE:
            self._tache = asyncio.create_task(self._executer())

    def ecouter(self, pcm: bytes) -> None:
        if not self._pret:
            return
        if self._file.full():
            self._file.get_nowait()  # le processeur ne suit pas : on oublie le plus vieux
        self._file.put_nowait(pcm)

    async def arreter(self) -> None:
        tache, self._tache = self._tache, None
        self._pret = False
        if tache and tache is not asyncio.current_task():
            tache.cancel()
            try:
                await tache
            except asyncio.CancelledError:
                pass

    async def _executer(self) -> None:
        import vosk

        modele = await preparer()
        if modele is None:
            return
        reconnaisseur = vosk.KaldiRecognizer(modele, TAUX_ECHANTILLONNAGE, json.dumps(GRAMMAIRE))
        reconnaisseur.SetWords(True)
        self._pret = True
        log.info("Commande vocale : « Joi, reviens » est à l'écoute")
        while True:
            # On regroupe l'audio en attente : moins d'allers-retours avec le fil de reconnaissance.
            morceaux = [await self._file.get()]
            while not self._file.empty():
                morceaux.append(self._file.get_nowait())
            fin_de_phrase = await asyncio.to_thread(reconnaisseur.AcceptWaveform, b"".join(morceaux))
            if fin_de_phrase and _est_commande_retour(json.loads(reconnaisseur.Result())):
                log.info("Commande vocale : « Joi, reviens »")
                self._pret = False
                await self._sur_retour()
                return
