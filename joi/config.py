"""Configuration de Joi, lue depuis le fichier .env à la racine du projet."""

import os
from pathlib import Path

from dotenv import load_dotenv

RACINE = Path(__file__).resolve().parent.parent
load_dotenv(RACINE / ".env")

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY", "").strip()
ASSEMBLYAI_MODELE = os.getenv("ASSEMBLYAI_MODELE", "universal-3-6-pro").strip()

JOI_VOIX = os.getenv("JOI_VOIX", "fr-CA-SylvieNeural").strip()
JOI_DEBIT = os.getenv("JOI_DEBIT", "+0%").strip()
JOI_MODELE = os.getenv("JOI_MODELE", "").strip() or None
JOI_PORT = int(os.getenv("JOI_PORT", "8049"))

DOSSIER_DONNEES = RACINE / "donnees"
DOSSIER_VOIX = DOSSIER_DONNEES / "voix"
DOSSIER_FRONTEND = RACINE / "frontend"
FICHIER_PERSONNALITE = Path(__file__).resolve().parent / "personnalite.md"

DOSSIER_VOIX.mkdir(parents=True, exist_ok=True)
