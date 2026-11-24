"""Ce que Joi manipule dans son compte, quel que soit le fournisseur : courriels, événements, tâches.

Les heures sont des datetime « aware » ; elles sont présentées en heure locale (JOI_FUSEAU).
Ce module donne aussi les formes écrites en français (dates, heures) que lisent le modèle et la console.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..ecrit import DEBUT_EXTERNE, FIN_EXTERNE, JOURS, MOIS, externe  # noqa: F401 (réexportés)
from ..ecrit import date_ecrite as _date_ecrite
from . import reglages


@dataclass
class Courriel:
    id: str
    de_nom: str
    de_adresse: str
    sujet: str
    recu: datetime | None
    apercu: str = ""
    lu: bool = False
    a: list[str] = field(default_factory=list)
    texte: str | None = None  # le contenu complet, seulement quand on lit le courriel
    prioritaire: bool = True  # « Autres » dans la boîte de réception Prioritaire : False
    infolettre: bool = False  # liste de diffusion, envoi en nombre

    @property
    def expediteur(self) -> str:
        if self.de_nom and self.de_adresse and self.de_nom.lower() != self.de_adresse.lower():
            return f"{self.de_nom} <{self.de_adresse}>"
        return self.de_nom or self.de_adresse or "expéditeur inconnu"

    @property
    def pertinent(self) -> bool:
        """Mérite d'être annoncé (sinon, seulement noté au journal)."""
        return self.prioritaire and not self.infolettre


@dataclass
class Evenement:
    id: str
    titre: str
    debut: datetime
    fin: datetime
    journee_entiere: bool = False
    lieu: str = ""
    note: str = ""
    agenda: str = "joi"  # « joi » ou « toi » (l'agenda de la personne, partagé en lecture)
    invites: list[str] = field(default_factory=list)
    rappel_minutes: int | None = None
    annule: bool = False


@dataclass
class Tache:
    id: str
    titre: str
    note: str = ""
    terminee: bool = False
    creee: datetime | None = None


# --- Dates et heures ----------------------------------------------------------------------------

def maintenant() -> datetime:
    return datetime.now(reglages.FUSEAU)


def local(instant: datetime) -> datetime:
    return instant.astimezone(reglages.FUSEAU)


def heure(instant: datetime) -> str:
    """« 9 h », « 14 h 05 »."""
    instant = local(instant)
    return f"{instant.hour} h" + (f" {instant.minute:02d}" if instant.minute else "")


def jour(d: date | datetime) -> str:
    """« aujourd'hui », « demain », « hier », ou « vendredi 21 novembre » (avec l'année si ce n'est
    pas celle-ci)."""
    if isinstance(d, datetime):
        d = local(d).date()
    aujourdhui = maintenant().date()
    relatif = {0: "aujourd'hui", 1: "demain", -1: "hier"}.get((d - aujourdhui).days)
    return relatif or _date_ecrite(d, annee=d.year != aujourdhui.year)


def quand(instant: datetime) -> str:
    """« aujourd'hui à 14 h 05 », « jeudi 13 novembre à 9 h »."""
    return f"{jour(instant)} à {heure(instant)}"


def date_complete(instant: datetime | None = None) -> str:
    """« samedi 15 novembre 2026, 14 h 05 »."""
    instant = local(instant or maintenant())
    return f"{_date_ecrite(instant.date(), annee=True)}, {heure(instant)}"


def lire_instant(texte: str, fin_de_journee: bool = False) -> datetime:
    """Une date (AAAA-MM-JJ) ou une date-heure ISO donnée par le modèle, en heure locale si elle n'a
    pas de fuseau. Une date seule vaut minuit, ou le minuit suivant si fin_de_journee."""
    texte = texte.strip()
    try:
        instant = datetime.fromisoformat(texte)
    except ValueError:
        raise ValueError(f"Date ou heure illisible : {texte!r} (attendu : AAAA-MM-JJ ou AAAA-MM-JJTHH:MM)")
    if len(texte) == 10 and fin_de_journee:
        instant += timedelta(days=1)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=reglages.FUSEAU)
    return instant


# --- Formes écrites pour le modèle --------------------------------------------------------------

def numero_court(prefixe: str, identifiant: str, longueur: int = 5) -> str:
    """Un numéro court et stable pour un identifiant du service (le même dans tous les processus) :
    « c » + 5 caractères pour un courriel, « e » pour un événement, « t » pour une tâche."""
    return prefixe + hashlib.sha1(identifiant.encode("utf-8")).hexdigest()[:longueur]


def ligne_courriel(c: Courriel, numero: str | None = None) -> str:
    morceaux = [f"[{numero}] " if numero else "", "" if c.lu else "(non lu) ",
                f"de {c.expediteur}"]
    if c.recu:
        morceaux.append(f", {quand(c.recu)}")
    morceaux.append(f" — sujet : « {c.sujet or '(sans sujet)'} »")
    if c.apercu:
        morceaux.append(f" — aperçu : {c.apercu}")
    return "".join(morceaux)


def ligne_evenement(e: Evenement, numero: str | None = None, avec_jour: bool = True) -> str:
    if e.journee_entiere:
        moment = "toute la journée"
    else:
        moment = f"de {heure(e.debut)} à {heure(e.fin)}"
    if avec_jour:
        moment = f"{jour(e.debut)}, {moment}"
    texte = f"[{numero}] " if numero else ""
    texte += f"{moment} : {e.titre or '(sans titre)'}"
    if e.lieu:
        texte += f" ({e.lieu})"
    if e.invites:
        texte += f" — invités : {', '.join(e.invites)}"
    if e.annule:
        texte += " — ANNULÉ"
    return texte


def ligne_tache(t: Tache, numero: str | None = None) -> str:
    texte = f"[{numero}] " if numero else ""
    texte += t.titre
    if t.note:
        texte += f" — {t.note}"
    if t.terminee:
        texte += " (terminée)"
    return texte
