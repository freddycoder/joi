"""Le tableau de bord de Joi : les paramètres en vigueur et ce qui se passe en coulisses.

L'interface les affiche dans ses panneaux latéraux (Alt+P, Alt+M, Alt+I). Rien de secret n'en sort :
la clé AssemblyAI est seulement signalée comme présente ou absente.
"""

import logging
import time
from collections import deque
from datetime import datetime

from . import config, empreintes

DEMARRAGE = time.time()

# Compteurs mis à jour par les autres modules.
voix = {"syntheses": 0, "cache": 0, "replis": 0, "derniere_ms": None, "moteur": None}
transcription = {"secondes": 0.0, "cout": 0.0, "segments": 0, "derniere": None}

# Les derniers messages du journal technique de Joi (logs « joi.* »), pour le panneau.
evenements: deque[dict] = deque(maxlen=40)

# Où part le temps des dernières réponses (joi/serveur.py, MesureReponse.resume()), en secondes.
latences: deque[dict] = deque(maxlen=50)
ETAPES_LATENCE = ("fin_de_tour", "premier_mot", "premiere_phrase", "synthese", "lecture", "total")


def resume_latences() -> dict:
    """La dernière réponse, et la médiane de chaque étape sur les dernières réponses sans recherche
    en mémoire (une recherche ajoute plusieurs secondes et brouillerait la médiane)."""
    simples = [m for m in latences if not m.get("recherches")]
    medianes = {}
    for etape in ETAPES_LATENCE:
        valeurs = sorted(m[etape] for m in simples if m.get(etape) is not None)
        medianes[etape] = valeurs[len(valeurs) // 2] if valeurs else None
    return {"derniere": latences[-1] if latences else None, "mediane": medianes, "reponses": len(simples)}


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
            ("Fin de tour", _fin_de_tour()),
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
        "Reconnaissance des voix": [
            ("État", empreintes.etat()),
            ("Ta voix", _ta_voix()),
            ("Seuils", f"toi {config.JOI_VOIX_SEUIL_TOI:g} · segment {config.JOI_VOIX_SEUIL_SEGMENT:g} · "
                       f"journée {config.JOI_VOIX_SEUIL_JOURNEE:g}"),
            ("Interlocuteur", f"au moins {config.JOI_VOIX_INTERLOCUTEUR_S:g} s"),
            ("Bilan du soir", empreintes.heure_fin_journee().strftime("%H:%M")),
        ],
        "Compte": _compte(),
        "Serveur": [("Port", str(config.JOI_PORT))],
    }
    return [{"titre": t, "lignes": [{"nom": n, "valeur": v} for n, v in l]} for t, l in groupes.items()]


def _compte() -> list[tuple[str, str]]:
    """Le compte de Joi : « désactivé » sans JOI_COMPTE (et ses réglages ne sont alors pas lus)."""
    if not config.JOI_COMPTE:
        if config.JOI_COMPTE_REFUSE:
            return [("État", f"désactivé (« {config.JOI_COMPTE_REFUSE} » non pris en charge)")]
        return [("État", "désactivé")]
    from .compte import reglages

    verification = (f"toutes les {reglages.VERIFIER_MINUTES:g} min" if reglages.VERIFIER_MINUTES > 0
                    else "au réveil seulement")
    return [
        ("Fournisseur", {"microsoft": "Microsoft 365"}.get(config.JOI_COMPTE, config.JOI_COMPTE)),
        ("Adresse de Joi", reglages.COURRIEL or "—"),
        ("Ton adresse", reglages.PROPRIETAIRE or "MANQUANTE"),
        ("Adresses approuvées", ", ".join(reglages.adresses_approuvees()) or "aucune"),
        ("Nouveaux courriels", verification),
        ("Fuseau", reglages.NOM_FUSEAU),
        ("Application Entra", "présente" if reglages.MS_CLIENT_ID else "MANQUANTE"),
    ]


def _fin_de_tour() -> str:
    morceaux = [config.ASSEMBLYAI_FIN_TOUR_MODE or "mode d'AssemblyAI"]
    if config.ASSEMBLYAI_SILENCE_MIN_MS is not None:
        morceaux.append(f"silence min {config.ASSEMBLYAI_SILENCE_MIN_MS} ms")
    if config.ASSEMBLYAI_SILENCE_MAX_MS is not None:
        morceaux.append(f"max {config.ASSEMBLYAI_SILENCE_MAX_MS} ms")
    return " · ".join(morceaux)


def _ta_voix() -> str:
    if not empreintes.active():
        return "—"
    profil = next((p for p in empreintes.profils_connus() if p.nom == "toi"), None)
    return f"{profil.echantillons} échantillon(s)" if profil else "pas encore apprise"


def taille_dossier(dossier) -> tuple[int, int]:
    """(nombre de fichiers, octets) d'un dossier, sans descendre dans les sous-dossiers cachés."""
    nombre, octets = 0, 0
    for f in dossier.rglob("*"):
        if f.is_file() and not any(p.startswith(".") for p in f.relative_to(dossier).parts):
            nombre += 1
            octets += f.stat().st_size
    return nombre, octets
