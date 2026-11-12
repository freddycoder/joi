"""Le tableau de bord de Joi : les paramètres en vigueur et ce qui se passe en coulisses.

L'interface les affiche dans ses panneaux latéraux (Alt+P, Alt+M, Alt+I). Rien de secret n'en sort :
la clé AssemblyAI est seulement signalée comme présente ou absente.
"""

import logging
import time
from collections import deque
from datetime import datetime

from . import config

DEMARRAGE = time.time()

# Compteurs mis à jour par les autres modules.
voix = {"syntheses": 0, "cache": 0, "replis": 0, "derniere_ms": None, "moteur": None}
transcription = {"secondes": 0.0, "cout": 0.0, "segments": 0, "derniere": None}

# Les derniers messages du journal technique de Joi (logs « joi.* »), pour le panneau.
evenements: deque[dict] = deque(maxlen=40)


class _Capteur(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            evenements.append({
                "heure": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                "niveau": record.levelname.lower(),
                "source": record.name.removeprefix("joi."),
                "message": record.getMessage()[:240],
            })
        except Exception:
            pass


def installer() -> None:
    capteur = _Capteur(level=logging.INFO)
    journal = logging.getLogger("joi")
    if not any(isinstance(h, _Capteur) for h in journal.handlers):
        journal.addHandler(capteur)
        if journal.level == logging.NOTSET or journal.level > logging.INFO:
            journal.setLevel(logging.INFO)


def parametres() -> list[dict]:
    """Les réglages du fichier .env, regroupés pour l'affichage."""
    oui_non = lambda v: "oui" if v else "non"  # noqa: E731
    if config.JOI_TTS == "assemblyai":
        voix_ = [
            ("Moteur", "AssemblyAI"),
            ("Voix", config.JOI_VOIX_ASSEMBLYAI),
            ("Langue", config.JOI_LANGUE_ASSEMBLYAI or "celle de la voix"),
            ("Repli", f"edge-tts · {config.JOI_VOIX}"),
        ]
    else:
        voix_ = [("Moteur", "edge-tts"), ("Voix", config.JOI_VOIX), ("Débit", config.JOI_DEBIT)]
    groupes = {
        "Voix": voix_,
        "Oreilles": [
            ("Clé AssemblyAI", "présente" if config.ASSEMBLYAI_API_KEY else "MANQUANTE"),
            ("Modèle temps réel", config.ASSEMBLYAI_MODELE),
            ("Mots-clés", ", ".join(config.MOTS_CLES)),
        ],
        "Cerveau": [("Modèle Copilot", config.JOI_MODELE or "automatique")],
        "Mode écoute": [
            ("Segments", f"{config.JOI_SEGMENT_MINUTES:g} min"),
            ("Apprendre toutes les",
             f"{config.JOI_APPRENDRE_MINUTES:g} min" if config.JOI_APPRENDRE_MINUTES > 0 else "fin d'écoute"),
            ("Modèle différé", ", ".join(config.ASSEMBLYAI_MODELES_DIFFERES)),
            ("Garder l'audio", oui_non(config.JOI_GARDER_AUDIO)),
            ("Commandes vocales", oui_non(config.JOI_COMMANDE_VOCALE)),
        ],
        "Serveur": [("Port", str(config.JOI_PORT))],
    }
    return [{"titre": t, "lignes": [{"nom": n, "valeur": v} for n, v in l]} for t, l in groupes.items()]


def taille_dossier(dossier) -> tuple[int, int]:
    """(nombre de fichiers, octets) d'un dossier, sans descendre dans les sous-dossiers cachés."""
    nombre, octets = 0, 0
    for f in dossier.rglob("*"):
        if f.is_file() and not any(p.startswith(".") for p in f.relative_to(dossier).parts):
            nombre += 1
            octets += f.stat().st_size
    return nombre, octets
