"""Les réglages du compte de Joi, lus dans .env seulement quand le compte est actif (JOI_COMPTE)."""

import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .. import config

log = logging.getLogger("joi.compte")

# Adresse de Joi : indice de connexion, et pour reconnaître son propre agenda.
COURRIEL = os.getenv("JOI_COURRIEL", "").strip().lower()
# Ton adresse : destinataire toujours permis, invitée aux rappels, propriétaire de l'agenda partagé.
PROPRIETAIRE = os.getenv("JOI_COURRIEL_PROPRIETAIRE", "").strip().lower()
# Autres adresses auxquelles Joi peut écrire (après ta confirmation).
DESTINATAIRES = [
    a.strip().lower() for a in os.getenv("JOI_COURRIEL_DESTINATAIRES", "").split(",") if a.strip()
]
# Vérification des nouveaux courriels pendant la conversation (en minutes, 0 = seulement au réveil).
try:
    VERIFIER_MINUTES = max(0.0, float(os.getenv("JOI_COURRIEL_VERIFIER_MINUTES", "5") or 0))
except ValueError:
    VERIFIER_MINUTES = 5.0

_nom_fuseau = os.getenv("JOI_FUSEAU", "America/Toronto").strip() or "America/Toronto"
try:
    FUSEAU = ZoneInfo(_nom_fuseau)
except (ZoneInfoNotFoundError, ValueError):
    log.warning("Fuseau JOI_FUSEAU inconnu (%r) : America/Toronto à la place.", _nom_fuseau)
    _nom_fuseau = "America/Toronto"
    FUSEAU = ZoneInfo(_nom_fuseau)
NOM_FUSEAU = _nom_fuseau

# Microsoft : ID d'application (client) de l'inscription Entra (voir docs/plan-compte-joi.md, étape 0).
MS_CLIENT_ID = os.getenv("JOI_MS_CLIENT_ID", "").strip()

# Données locales du compte (ignorées par git). Créé seulement quand on y écrit.
DOSSIER = config.DOSSIER_DONNEES / "compte"

# Un brouillon ne peut plus être envoyé après ce délai (en secondes).
DUREE_BROUILLON = 15 * 60


def adresses_approuvees() -> list[str]:
    """Les seules adresses auxquelles Joi peut écrire, ou qu'elle peut inviter."""
    return list(dict.fromkeys(a for a in [PROPRIETAIRE, *DESTINATAIRES] if a))


def approuvee(adresse: str) -> bool:
    return bool(adresse) and adresse.strip().lower() in adresses_approuvees()


def dossier() -> Path:
    DOSSIER.mkdir(parents=True, exist_ok=True)
    return DOSSIER
