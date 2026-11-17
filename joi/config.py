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
# Moteur de synthèse vocale : « edge » (edge-tts, par défaut) ou « assemblyai ».
JOI_TTS = os.getenv("JOI_TTS", "edge").strip().lower() or "edge"
if JOI_TTS not in ("edge", "assemblyai"):
    JOI_TTS = "edge"
# Voix AssemblyAI : slug d'une voix de la bibliothèque (ex. aai-donatien) ou d'une voix créée dans le tableau de bord.
JOI_VOIX_ASSEMBLYAI = os.getenv("JOI_VOIX_ASSEMBLYAI", "aai-donatien").strip()
# Langue parlée par la voix AssemblyAI (nom anglais complet : french, english…).
JOI_LANGUE_ASSEMBLYAI = os.getenv("JOI_LANGUE_ASSEMBLYAI", "french").strip().lower()
JOI_MODELE = os.getenv("JOI_MODELE", "").strip() or None
JOI_PORT = int(os.getenv("JOI_PORT", "8049"))
# Mode écoute : durée des segments audio enregistrés (en minutes).
JOI_SEGMENT_MINUTES = float(os.getenv("JOI_SEGMENT_MINUTES", "30"))
# Mode écoute : Joi apprend ce qu'elle a entendu sans attendre la fin de l'écoute, dès que ce volume
# d'audio est enregistré (en minutes, compté en segments terminés). 0 = seulement en fin d'écoute.
JOI_APPRENDRE_MINUTES = float(os.getenv("JOI_APPRENDRE_MINUTES", "60") or 0)
# Mode écoute : modèle(s) de la transcription différée, par ordre de préférence (séparés par des virgules).
ASSEMBLYAI_MODELES_DIFFERES = [
    m.strip() for m in os.getenv("ASSEMBLYAI_MODELES_DIFFERES", "universal-3-5-pro").split(",") if m.strip()
] or ["universal-3-5-pro"]
# Mode écoute : garder l'audio après sa transcription (par défaut, il est supprimé pour la vie privée).
JOI_GARDER_AUDIO = os.getenv("JOI_GARDER_AUDIO", "non").strip().lower() in ("oui", "o", "vrai", "true", "1", "yes")
# Commandes vocales « Joi, écoute seulement » et « Joi, reviens » (la seconde télécharge un modèle Vosk).
JOI_COMMANDE_VOCALE = os.getenv("JOI_COMMANDE_VOCALE", "oui").strip().lower() in ("oui", "o", "vrai", "true", "1", "yes")

# Reconnaissance des voix du mode écoute (empreintes vocales calculées sur l'ordinateur, voir joi/empreintes.py).
JOI_RECONNAISSANCE_VOIX = os.getenv("JOI_RECONNAISSANCE_VOIX", "oui").strip().lower() in ("oui", "o", "vrai", "true", "1", "yes")
# Similarités cosinus (de -1 à 1) : une fenêtre est « toi » au-delà de JOI_VOIX_SEUIL_TOI ; les fenêtres
# inconnues d'un segment se regroupent au-delà de JOI_VOIX_SEUIL_SEGMENT ; les groupes de deux segments
# sont la même voix du jour au-delà de JOI_VOIX_SEUIL_JOURNEE. À régler avec « python -m joi.empreintes rejouer ».
JOI_VOIX_SEUIL_TOI = float(os.getenv("JOI_VOIX_SEUIL_TOI", "0.40"))
JOI_VOIX_SEUIL_SEGMENT = float(os.getenv("JOI_VOIX_SEUIL_SEGMENT", "0.25"))
JOI_VOIX_SEUIL_JOURNEE = float(os.getenv("JOI_VOIX_SEUIL_JOURNEE", "0.45"))
# Parole totale dans la journée (en s) pour compter comme interlocuteur ; en dessous : voix de passage.
JOI_VOIX_INTERLOCUTEUR_S = float(os.getenv("JOI_VOIX_INTERLOCUTEUR_S", "30"))
# Fenêtres de parole (en s) : durée minimale et maximale, et silence entre deux mots qui coupe une fenêtre.
JOI_VOIX_FENETRE_MIN_S = float(os.getenv("JOI_VOIX_FENETRE_MIN_S", "1.5"))
JOI_VOIX_FENETRE_MAX_S = float(os.getenv("JOI_VOIX_FENETRE_MAX_S", "4"))
JOI_VOIX_SILENCE_S = float(os.getenv("JOI_VOIX_SILENCE_S", "0.7"))
# Heure du bilan des voix de la journée ; les empreintes anonymes du jour sont ensuite effacées.
JOI_FIN_JOURNEE = os.getenv("JOI_FIN_JOURNEE", "18:00").strip() or "18:00"

# Compte de Joi (courriel, agenda, tâches ; voir joi/compte/). Optionnel : vide, Joi fonctionne comme
# avant et aucun code du compte n'est importé. C'est le seul interrupteur : les autres réglages du compte
# (JOI_COURRIEL…) ne sont lus que par joi/compte/reglages.py, quand le compte est actif.
COMPTES_PRIS_EN_CHARGE = ("microsoft",)  # « icloud » : phase 2, pas encore écrit
_compte = os.getenv("JOI_COMPTE", "").strip().lower()
JOI_COMPTE = _compte if _compte in COMPTES_PRIS_EN_CHARGE else None
# Une valeur inconnue (faute de frappe, ou icloud pas encore pris en charge) : Joi démarre sans compte
# et l'écrit au journal technique (voir le démarrage de joi/serveur.py).
JOI_COMPTE_REFUSE = _compte if _compte and not JOI_COMPTE else None

# Le dossier des données (mémoire, journal, enregistrements, empreintes, logs). JOI_DONNEES sert aux
# tests (tests/conftest.py), qui ne doivent jamais toucher la vraie mémoire. Plusieurs modules figent
# leurs chemins à l'import (memoire.RACINE, journal.FICHIER_CONSOLIDATION…) : la variable doit donc
# être définie avant le premier import de joi.
_donnees = os.getenv("JOI_DONNEES", "").strip()
DOSSIER_DONNEES = Path(_donnees).resolve() if _donnees else RACINE / "donnees"
DOSSIER_VOIX = DOSSIER_DONNEES / "voix"
DOSSIER_JOURNAL = DOSSIER_DONNEES / "journal"
DOSSIER_MEMOIRE = DOSSIER_DONNEES / "memoire"
DOSSIER_ENREGISTREMENTS = DOSSIER_DONNEES / "enregistrements"
DOSSIER_VOSK = DOSSIER_DONNEES / "vosk"
DOSSIER_EMPREINTES = DOSSIER_DONNEES / "empreintes"
DOSSIER_LOGS = DOSSIER_DONNEES / "logs"
DOSSIER_FRONTEND = RACINE / "frontend"
FICHIER_PERSONNALITE = Path(__file__).resolve().parent / "personnalite.md"
FICHIER_BIBLIOTHECAIRE = Path(__file__).resolve().parent / "bibliothecaire.md"

for _dossier in (DOSSIER_VOIX, DOSSIER_JOURNAL, DOSSIER_MEMOIRE, DOSSIER_ENREGISTREMENTS, DOSSIER_LOGS):
    _dossier.mkdir(parents=True, exist_ok=True)
