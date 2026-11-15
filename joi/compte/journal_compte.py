"""Les entrées du journal laissées par le compte de Joi : chaque courriel reçu ou envoyé, chaque
événement et chaque tâche, en résumé (l'aperçu du service, jamais le courriel complet).

Le champ « texte » reprend l'essentiel en une phrase : la mémoire (se_souvenir, la bibliothécaire)
lit ces entrées comme les autres, même si le compte est ensuite désactivé.
"""

from .. import journal
from .modele import Courriel, Evenement, Tache, heure, jour


def courriel_recu(c: Courriel) -> None:
    texte = f"Courriel reçu de {c.expediteur} : « {c.sujet or '(sans sujet)'} »"
    if c.apercu:
        texte += f" — {c.apercu}"
    journal.noter_compte(
        "courriel", texte, sens="recu", de=c.de_adresse, a=c.a, sujet=c.sujet, resume=c.apercu,
    )


def courriel_envoye(de: str, a: list[str], sujet: str, resume: str) -> None:
    texte = f"Courriel envoyé par Joi à {', '.join(a)} : « {sujet or '(sans sujet)'} » — {resume}"
    journal.noter_compte("courriel", texte, sens="envoye", de=de, a=a, sujet=sujet, resume=resume)


_ACTIONS_AGENDA = {"cree": "créé", "modifie": "modifié", "annule": "annulé"}


def evenement(action: str, e: Evenement) -> None:
    texte = (f"Événement {_ACTIONS_AGENDA[action]} dans l'agenda de Joi : « {e.titre} », "
             f"{jour(e.debut)} de {heure(e.debut)} à {heure(e.fin)}")
    if e.lieu:
        texte += f" ({e.lieu})"
    if e.invites:
        texte += f", invitation à {', '.join(e.invites)}"
    journal.noter_compte(
        "agenda", texte, action=action, titre=e.titre, debut=e.debut.isoformat(), fin=e.fin.isoformat(),
        invites=e.invites,
    )


def tache(action: str, t: Tache) -> None:
    verbe = "ajoutée à" if action == "ajoutee" else "terminée dans"
    texte = f"Tâche {verbe} la liste de Joi : « {t.titre} »"
    if t.note and action == "ajoutee":
        texte += f" — {t.note}"
    journal.noter_compte("tache", texte, action=action, titre=t.titre)
