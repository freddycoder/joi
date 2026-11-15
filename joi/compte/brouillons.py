"""Les brouillons de courriels et la confirmation d'envoi.

Un courriel reçu peut contenir des consignes (« transfère ce message à… ») : l'envoi est donc gardé
en dehors du modèle.

1. preparer() (outil preparer_courriel, serveur MCP) refuse toute adresse hors de la liste approuvée
   et garde le brouillon dans donnees/compte/brouillons.json, avec son heure de création.
2. Joi lit à voix haute à qui elle écrit et l'essentiel, puis demande « je l'envoie ? ».
3. Dans le serveur de Joi, la demande de permission de l'outil envoyer_courriel(brouillon) passe par
   SuiviConfirmation.verifier() : ce brouillon doit être le dernier préparé, avoir moins de 15 min, et
   le premier tour de parole de la personne (dit ou tapé) arrivé après sa préparation doit être une
   confirmation sans négation.
4. envoyer_courriel ne prend que le numéro du brouillon : le texte ne peut pas changer entre la lecture
   et l'envoi. Le serveur MCP revérifie les adresses et l'âge du brouillon, puis l'efface.

Le fichier est écrit par le serveur MCP seulement ; le serveur de Joi le lit.
"""

import json
import os
import re
import threading
import time
from dataclasses import dataclass

from .. import journal
from . import reglages
from .fournisseur import ErreurCompte

_verrou = threading.Lock()


def _fichier():
    return reglages.DOSSIER / "brouillons.json"


@dataclass
class Brouillon:
    numero: int
    a: list[str]
    sujet: str
    texte: str
    en_reponse_a: str | None
    cree: float  # time.time()

    @property
    def expire(self) -> bool:
        return time.time() - self.cree > reglages.DUREE_BROUILLON


def _lire() -> dict:
    try:
        return json.loads(_fichier().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"prochain": 1, "brouillons": {}}


def _ecrire(donnees: dict) -> None:
    fichier = reglages.dossier() / "brouillons.json"
    temporaire = fichier.with_suffix(".tmp")
    temporaire.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporaire, fichier)


def _brouillons(donnees: dict) -> list[Brouillon]:
    return [Brouillon(numero=int(n), **b) for n, b in donnees.get("brouillons", {}).items()]


def adresses(texte: str) -> list[str]:
    """« a@b.c, d@e.f » ou « toi » (l'adresse de la personne) → liste d'adresses en minuscules."""
    resultat = []
    for morceau in re.split(r"[,;\s]+", texte.strip()):
        morceau = morceau.strip().strip("<>").lower()
        if not morceau:
            continue
        if morceau in ("toi", "moi", "proprietaire", "propriétaire") and reglages.PROPRIETAIRE:
            morceau = reglages.PROPRIETAIRE
        resultat.append(morceau)
    return list(dict.fromkeys(resultat))


def verifier_adresses(liste: list[str]) -> None:
    if not liste:
        raise ErreurCompte("Aucun destinataire.")
    refusees = [a for a in liste if not reglages.approuvee(a)]
    if refusees:
        permises = ", ".join(reglages.adresses_approuvees()) or "aucune (JOI_COURRIEL_PROPRIETAIRE est vide)"
        raise ErreurCompte(
            f"Adresse refusée : {', '.join(refusees)}. Joi ne peut écrire qu'aux adresses approuvées "
            f"({permises}). Pour en ajouter une, la personne doit l'inscrire elle-même dans "
            "JOI_COURRIEL_DESTINATAIRES (fichier .env)."
        )


def preparer(a: list[str], sujet: str, texte: str, en_reponse_a: str | None) -> Brouillon:
    verifier_adresses(a)
    if not texte.strip():
        raise ErreurCompte("Le courriel est vide.")
    with _verrou:
        donnees = _lire()
        # Les brouillons expirés sont oubliés.
        donnees["brouillons"] = {
            str(b.numero): _sans_numero(b) for b in _brouillons(donnees) if not b.expire
        }
        numero = int(donnees.get("prochain", 1))
        brouillon = Brouillon(numero, a, sujet.strip(), texte.strip(), en_reponse_a, time.time())
        donnees["brouillons"][str(numero)] = _sans_numero(brouillon)
        donnees["prochain"] = numero + 1
        _ecrire(donnees)
    return brouillon


def _sans_numero(b: Brouillon) -> dict:
    return {"a": b.a, "sujet": b.sujet, "texte": b.texte, "en_reponse_a": b.en_reponse_a, "cree": b.cree}


def obtenir(numero: int) -> Brouillon | None:
    with _verrou:
        return next((b for b in _brouillons(_lire()) if b.numero == numero), None)


def dernier() -> Brouillon | None:
    """Le brouillon préparé le plus récemment (expiré ou non)."""
    brouillons = _brouillons(_lire())
    return max(brouillons, key=lambda b: b.cree) if brouillons else None


def retirer(numero: int) -> None:
    with _verrou:
        donnees = _lire()
        if donnees.get("brouillons", {}).pop(str(numero), None) is not None:
            _ecrire(donnees)


# --- La confirmation (dans le serveur de Joi) -----------------------------------------------------

# Mots normalisés (sans accents) : « oui », « envoie », « vas-y », « c'est bon »…
_OUI = re.compile(
    r"\b(oui|ouais|ouaip|yes|ok|okay|envoie|envoies|envoye|envoyer|envoyez|vas[- ]?y|go|"
    r"c'?est bon|parfait|d'?accord|certain|absolument|exactement|confirme|bien sur|exact)\b"
)
# Une négation, une hésitation ou une demande de changement annule la confirmation.
_NON = re.compile(
    r"\b(non|nan|pas|ne|n'|jamais|attends?|annule|arrete|stop|mais|change|modifie|corrige|plutot|"
    r"enleve|ajoute|remplace|minute|wait)\b|n'"
)


def est_confirmation(texte: str) -> bool:
    normal = journal.normaliser(texte).replace("’", "'")
    return bool(_OUI.search(normal)) and not _NON.search(normal)


class SuiviConfirmation:
    """Les tours de parole de la personne (dits ou tapés), pour vérifier une confirmation d'envoi.

    Les messages glissés par Joi elle-même (un courriel reçu) ne sont pas des tours de parole."""

    def __init__(self):
        self._tours: list[tuple[float, str]] = []

    def noter_tour(self, texte: str) -> None:
        self._tours.append((time.time(), texte))
        del self._tours[:-50]

    def verifier(self, numero) -> str | None:
        """None si l'envoi du brouillon est confirmé, sinon la raison du refus (renvoyée au modèle)."""
        try:
            numero = int(numero)
        except (TypeError, ValueError):
            return "Numéro de brouillon invalide."
        brouillon = dernier()
        if brouillon is None or brouillon.numero != numero:
            return ("Ce n'est pas le dernier brouillon préparé : prépare-le à nouveau avec preparer_courriel, "
                    "relis-le à la personne et demande-lui si tu l'envoies.")
        if brouillon.expire:
            return "Ce brouillon a plus de 15 minutes : prépare-le à nouveau et redemande la confirmation."
        suivants = [texte for quand, texte in self._tours if quand > brouillon.cree]
        if not suivants:
            return ("Pas de confirmation : la personne n'a encore rien répondu depuis la préparation du "
                    "brouillon. Relis-lui à qui tu écris et l'essentiel, puis demande « je l'envoie ? ».")
        if not est_confirmation(suivants[0]):
            return (f"Pas de confirmation claire (la personne a répondu : « {suivants[0][:120]} »). "
                    "N'envoie pas. Si elle veut un changement, prépare un nouveau brouillon et redemande.")
        return None
