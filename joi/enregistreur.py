"""L'enregistreur de Joi : en mode écoute, l'audio du micro est gardé sur le disque, en MP3.

Une séance d'écoute (du passage en mode écoute jusqu'au retour en conversation, ou à la fermeture
de l'onglet) est découpée en segments d'environ 30 minutes, pour deux raisons : survivre à un
plantage (au pire, on perd la fin d'un segment) et rester sous les limites d'envoi de la
transcription différée.

    donnees/enregistrements/
    └── AAAA-MM-JJ/
        ├── seance-HHMMSS.json     le manifeste de la séance
        ├── HHMMSS.mp3             un segment (nommé d'après l'heure réelle de son début)
        └── ...

Le manifeste :

    {
      "debut": "2026-11-12T08:30:00",       heure locale, comme le journal
      "fin": "2026-11-12T12:01:13" | null,   null tant que la séance est en cours (ou si elle a planté)
      "segments": [
        {"fichier": "2026-11-12/083000.mp3", "debut": "2026-11-12T08:30:00.123456",
         "duree_s": 1800.0, "etat": "en_cours" | "enregistre" | "transcrit" | "consolide" | "echec"}
      ]
    }

« debut » est l'heure réelle du premier échantillon du segment : une phrase entendue à start_ms
dans le segment a été dite à debut + start_ms. Un segment resté « en_cours » dans une séance qui
n'est plus active vient d'un plantage : son MP3 est lisible jusqu'au dernier paquet écrit.

Les segments déjà terminés d'une séance encore en cours peuvent être transcrits sans attendre la
fin de l'écoute (toutes les JOI_APPRENDRE_MINUTES). Pour que l'enregistreur et la transcription ne
s'écrasent pas l'un l'autre, ils travaillent alors sur le même manifeste en mémoire (seances()).

La suite (états « transcrit », « consolide » et « echec », et les champs « transcription »,
« id_transcription », « essais », « erreur ») est gérée par joi/transcription.py.
"""

import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import lameenc

from . import config

log = logging.getLogger("joi.enregistreur")

TAUX_ECHANTILLONNAGE = 16_000
OCTETS_PAR_SECONDE = TAUX_ECHANTILLONNAGE * 2  # PCM 16 bits mono
# Environ 14 Mo par heure : la voix reste intelligible, et la reconnaissance n'a pas besoin de plus.
DEBIT_MP3_KBPS = 32
# On encode par paquets d'une seconde plutôt qu'à chaque paquet de 50 ms reçu du navigateur.
TAILLE_TAMPON = OCTETS_PAR_SECONDE


# Les séances en cours d'enregistrement, avec leur manifeste vivant (celui que l'enregistreur modifie).
_seances_actives: dict[Path, dict] = {}


def _horodatage(instant: datetime) -> str:
    return instant.isoformat(timespec="microseconds")


def lire_manifeste(chemin: Path) -> dict | None:
    try:
        manifeste = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Manifeste d'écoute illisible (%s) : %s", chemin.name, e)
        return None
    return manifeste if isinstance(manifeste.get("segments"), list) else None


def sauver_manifeste(chemin: Path, manifeste: dict) -> None:
    # Écriture atomique : un plantage en pleine écriture ne laisse pas un manifeste illisible.
    temporaire = chemin.with_suffix(".tmp")
    try:
        temporaire.write_text(json.dumps(manifeste, ensure_ascii=False, indent=2), encoding="utf-8")
        temporaire.replace(chemin)
    except OSError as e:
        log.warning("Manifeste d'écoute non sauvegardé : %s", e)


def seances(dossier: Path | None = None) -> list[tuple[Path, dict, bool]]:
    """Toutes les séances, de la plus ancienne à la plus récente : (manifeste sur le disque,
    manifeste, en cours ?). Pour une séance en cours, c'est le manifeste vivant de l'enregistreur :
    son dernier segment (« en_cours ») est en train d'être écrit et ne doit pas être touché."""
    dossier = dossier or config.DOSSIER_ENREGISTREMENTS
    resultat = []
    for chemin in sorted(dossier.glob("????-??-??/seance-*.json")):
        vivant = _seances_actives.get(chemin.resolve())
        if vivant is not None:
            resultat.append((chemin, vivant, True))
            continue
        manifeste = lire_manifeste(chemin)
        if manifeste is not None:
            resultat.append((chemin, manifeste, False))
    resultat.sort(key=lambda s: s[1].get("debut", ""))
    return resultat


def seances_terminees(dossier: Path | None = None) -> list[tuple[Path, dict]]:
    """Les séances qui ne sont plus en cours d'enregistrement (finies ou interrompues par un
    plantage), de la plus ancienne à la plus récente, avec leur manifeste."""
    return [(chemin, manifeste) for chemin, manifeste, active in seances(dossier) if not active]


class Enregistreur:
    """Une séance d'écoute. ecrire() reçoit le PCM du micro, fermer() finalise le tout.

    sur_segment_fini(duree_s) est appelé chaque fois qu'un segment se termine pendant l'écoute
    (pas à la fermeture) : le segment est alors prêt à être transcrit."""

    def __init__(
        self,
        dossier: Path | None = None,
        duree_segment_s: float | None = None,
        sur_segment_fini: Callable[[float], None] | None = None,
    ):
        self.dossier = dossier or config.DOSSIER_ENREGISTREMENTS
        self.duree_segment_s = duree_segment_s or config.JOI_SEGMENT_MINUTES * 60
        self.sur_segment_fini = sur_segment_fini
        self.manifeste: dict = {}
        self.fichier_manifeste: Path | None = None
        self._encodeur: lameenc.Encoder | None = None
        self._mp3 = None  # fichier du segment en cours
        self._octets_segment = 0
        self._tampon = bytearray()

    @property
    def ouvert(self) -> bool:
        return self.fichier_manifeste is not None

    def ouvrir(self) -> None:
        maintenant = datetime.now()
        jour = self.dossier / maintenant.strftime("%Y-%m-%d")
        jour.mkdir(parents=True, exist_ok=True)
        self.fichier_manifeste = _chemin_libre(jour, f"seance-{maintenant:%H%M%S}", ".json")
        self.manifeste = {"debut": maintenant.isoformat(timespec="seconds"), "fin": None, "segments": []}
        _seances_actives[self.fichier_manifeste.resolve()] = self.manifeste
        try:
            self._nouveau_segment()
        except OSError:
            _seances_actives.pop(self.fichier_manifeste.resolve(), None)
            self.fichier_manifeste = None
            raise
        log.info("Écoute : enregistrement dans %s", self.fichier_manifeste.parent)

    def ecrire(self, pcm: bytes) -> None:
        if not self.ouvert:
            return
        if len(pcm) % 2:  # un échantillon coupé en deux n'arrive pas en pratique, mais lameenc le refuserait
            pcm = pcm[:-1]
        self._tampon.extend(pcm)
        if len(self._tampon) >= TAILLE_TAMPON:
            self._vider_tampon()
        if self._octets_segment >= self.duree_segment_s * OCTETS_PAR_SECONDE:
            duree = self._finir_segment()
            self._nouveau_segment()
            if duree and self.sur_segment_fini:
                try:
                    self.sur_segment_fini(duree)
                except Exception:
                    log.exception("Fin de segment mal signalée")

    def fermer(self) -> None:
        if not self.ouvert:
            return
        try:
            self._vider_tampon()
            self._finir_segment()
            self.manifeste["fin"] = datetime.now().isoformat(timespec="seconds")
            self._sauver_manifeste()
            duree = sum(s["duree_s"] for s in self.manifeste["segments"])
            log.info("Écoute terminée : %d segment(s), %.1f min d'audio",
                     len(self.manifeste["segments"]), duree / 60)
        finally:
            _seances_actives.pop(self.fichier_manifeste.resolve(), None)
            self.fichier_manifeste = None

    # ---------- Segments ----------

    def _nouveau_segment(self) -> None:
        debut = datetime.now()
        jour = self.dossier / debut.strftime("%Y-%m-%d")  # un segment peut commencer après minuit
        jour.mkdir(parents=True, exist_ok=True)
        chemin = _chemin_libre(jour, debut.strftime("%H%M%S"), ".mp3")

        self._encodeur = lameenc.Encoder()
        self._encodeur.set_bit_rate(DEBIT_MP3_KBPS)
        self._encodeur.set_in_sample_rate(TAUX_ECHANTILLONNAGE)
        self._encodeur.set_channels(1)
        self._encodeur.set_quality(7)  # 2 = meilleur, 7 = rapide : largement suffisant pour de la voix
        self._mp3 = chemin.open("wb")
        self._octets_segment = 0

        self.manifeste["segments"].append({
            "fichier": chemin.relative_to(self.dossier).as_posix(),
            "debut": _horodatage(debut),
            "duree_s": 0.0,
            "etat": "en_cours",
        })
        self._sauver_manifeste()

    def _vider_tampon(self) -> None:
        if not self._tampon or self._encodeur is None:
            return
        donnees = bytes(self._tampon)
        self._tampon.clear()
        try:
            self._mp3.write(self._encodeur.encode(donnees))
            # Écrit sur le disque tout de suite : en cas de plantage, l'audio déjà reçu est conservé.
            self._mp3.flush()
        except (OSError, RuntimeError) as e:  # un disque plein ne doit pas couper Joi
            log.warning("Écriture de l'enregistrement impossible : %s", e)
            return
        self._octets_segment += len(donnees)

    def _finir_segment(self) -> float:
        """Termine le segment en cours. Renvoie sa durée en secondes (0 s'il était vide)."""
        if self._encodeur is None:
            return 0.0
        try:
            if self._octets_segment:  # lameenc refuse flush() si rien n'a été encodé
                self._mp3.write(self._encodeur.flush())
        except (OSError, RuntimeError) as e:
            log.warning("Fin de segment mal écrite : %s", e)
        finally:
            self._mp3.close()
            self._mp3 = None
            self._encodeur = None

        segment = self.manifeste["segments"][-1]
        if not self._octets_segment:
            # Segment vide (écoute arrêtée juste après une rotation) : rien à transcrire.
            (self.dossier / segment["fichier"]).unlink(missing_ok=True)
            self.manifeste["segments"].pop()
            self._sauver_manifeste()
            return 0.0
        segment["duree_s"] = round(self._octets_segment / OCTETS_PAR_SECONDE, 2)
        segment["etat"] = "enregistre"
        self._sauver_manifeste()
        return segment["duree_s"]

    def _sauver_manifeste(self) -> None:
        sauver_manifeste(self.fichier_manifeste, self.manifeste)


def _chemin_libre(dossier: Path, nom: str, extension: str) -> Path:
    """dossier/nom.ext, ou dossier/nom-2.ext s'il existe déjà (deux onglets lancés la même seconde)."""
    chemin = dossier / f"{nom}{extension}"
    n = 2
    while chemin.exists():
        chemin = dossier / f"{nom}-{n}{extension}"
        n += 1
    return chemin
