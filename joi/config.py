"""Configuration de Joi, lue depuis le fichier .env à la racine du projet."""

import os
from pathlib import Path

from dotenv import load_dotenv

RACINE = Path(__file__).resolve().parent.parent
load_dotenv(RACINE / ".env")

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY", "").strip()
ASSEMBLYAI_MODELE = os.getenv("ASSEMBLYAI_MODELE", "universal-3-6-pro").strip()
# Noms propres et mots inventés que la reconnaissance vocale doit privilégier, séparés par des virgules.
MOTS_CLES = ["Joi"] + [m.strip() for m in os.getenv("JOI_MOTS_CLES", "").split(",") if m.strip()]

JOI_VOIX = os.getenv("JOI_VOIX", "fr-CA-SylvieNeural").strip()
JOI_DEBIT = os.getenv("JOI_DEBIT", "+0%").strip()
JOI_MODELE = os.getenv("JOI_MODELE", "").strip() or None
JOI_PORT = int(os.getenv("JOI_PORT", "8049"))
# Mode écoute : durée des segments audio enregistrés (en minutes).
JOI_SEGMENT_MINUTES = float(os.getenv("JOI_SEGMENT_MINUTES", "30"))
# Mode écoute : modèle(s) de la transcription différée, par ordre de préférence (séparés par des virgules).
ASSEMBLYAI_MODELES_DIFFERES = [
    m.strip() for m in os.getenv("ASSEMBLYAI_MODELES_DIFFERES", "universal-3-5-pro").split(",") if m.strip()
] or ["universal-3-5-pro"]
# Mode écoute : garder l'audio après sa transcription (par défaut, il est supprimé pour la vie privée).
JOI_GARDER_AUDIO = os.getenv("JOI_GARDER_AUDIO", "non").strip().lower() in ("oui", "o", "vrai", "true", "1", "yes")
# Commandes vocales « Joi, écoute seulement » et « Joi, reviens » (la seconde télécharge un modèle Vosk).
JOI_COMMANDE_VOCALE = os.getenv("JOI_COMMANDE_VOCALE", "oui").strip().lower() in ("oui", "o", "vrai", "true", "1", "yes")

DOSSIER_DONNEES = RACINE / "donnees"
DOSSIER_VOIX = DOSSIER_DONNEES / "voix"
DOSSIER_JOURNAL = DOSSIER_DONNEES / "journal"
DOSSIER_MEMOIRE = DOSSIER_DONNEES / "memoire"
DOSSIER_ENREGISTREMENTS = DOSSIER_DONNEES / "enregistrements"
DOSSIER_VOSK = DOSSIER_DONNEES / "vosk"
DOSSIER_FRONTEND = RACINE / "frontend"
FICHIER_PERSONNALITE = Path(__file__).resolve().parent / "personnalite.md"
FICHIER_BIBLIOTHECAIRE = Path(__file__).resolve().parent / "bibliothecaire.md"

for _dossier in (DOSSIER_VOIX, DOSSIER_JOURNAL, DOSSIER_MEMOIRE, DOSSIER_ENREGISTREMENTS):
    _dossier.mkdir(parents=True, exist_ok=True)
