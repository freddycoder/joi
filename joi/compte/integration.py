"""Ce que la session Copilot de Joi reçoit quand le compte est actif (voir joi/cerveau.py) :

- le serveur MCP du compte (python -m joi.compte), avec la liste explicite de ses outils ;
- les consignes « Ton compte » (joi/compte/personnalite.md), ajoutées au prompt système ;
- le contexte du réveil (courriels non lus, agendas, tâches ; voir veille.py) ;
- le gestionnaire de permissions : les outils du compte sont permis, sauf envoyer_courriel, permis
  seulement si la personne a confirmé (voir brouillons.py). Tout le reste est refusé, comme sans compte.

Copilot approuve de lui-même les outils MCP marqués « lecture seule » : seuls les outils d'écriture
passent par le gestionnaire (vérifié avec le Copilot SDK 1.0.31).
"""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Callable

from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject

from .. import config
from . import OUTILS, OUTILS_ECRITURE, SERVEUR, brouillons, engagements, est_echeance, reglages, veille

log = logging.getLogger("joi.compte")

FICHIER_CONSIGNES = Path(__file__).resolve().parent / "personnalite.md"
DELAI_OUTIL_MS = 30_000
AGE_MAX_OUI = 180  # secondes : le oui à une proposition (engagement, échéance) doit être tout récent


# « Mets-le à l'agenda », « ajoute-le » : un oui à « je le note dans mon agenda ? » que est_oui (réglé
# pour les tâches, où « ajoute » marque un changement) refuserait.
OUI_AGENDA = re.compile(r"\b(mets|ajoute|inscris|note)[- ](le|la)\b")
NON_AGENDA = re.compile(r"\b(non|pas|jamais|attends?|annule|plutot|change|modifie)\b|n'")


def est_oui_agenda(texte: str) -> bool:
    """La personne accepte-t-elle qu'on mette l'échéance à l'agenda ? Un oui, ou « mets-le » sans négation."""
    if engagements.est_oui(texte):
        return True
    normal = engagements._sans_accents(engagements._normal(texte))
    return bool(OUI_AGENDA.search(normal)) and not NON_AGENDA.search(normal)


def _arguments(brut):
    """Les arguments d'un appel d'outil : un dict, ou du JSON à lire. None s'ils sont illisibles."""
    if isinstance(brut, str):
        try:
            return json.loads(brut)
        except json.JSONDecodeError:
            return None
    return brut


def outil_du_compte(nom: str | None) -> str | None:
    """« compte-agenda » → « agenda » ; None si ce n'est pas un outil du compte."""
    if nom and nom.startswith(f"{SERVEUR}-"):
        outil = nom.removeprefix(f"{SERVEUR}-")
        return outil if outil in OUTILS else None
    return None


class Integration:
    def __init__(self, repli: Callable):
        self._repli = repli  # le gestionnaire de permissions sans compte (tout refuser)
        self.confirmation = brouillons.SuiviConfirmation()

    def serveurs_mcp(self) -> dict:
        return {
            SERVEUR: {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", "joi.compte"],
                "working_directory": str(config.RACINE),
                "tools": list(OUTILS),  # liste explicite, jamais « * »
                "timeout": DELAI_OUTIL_MS,
            }
        }

    def outils_permis(self) -> list[str]:
        return [f"mcp:{SERVEUR}-{outil}" for outil in OUTILS]

    def consignes(self) -> str:
        approuvees = reglages.adresses_approuvees()
        texte = FICHIER_CONSIGNES.read_text(encoding="utf-8")
        return "\n\n" + (
            texte.replace("{adresse_joi}", reglages.COURRIEL or "(inconnue)")
            .replace("{adresse_toi}", reglages.PROPRIETAIRE or "(pas encore réglée)")
            .replace("{adresses_approuvees}", ", ".join(approuvees) or "aucune pour l'instant")
        ).strip() + "\n"

    async def contexte_reveil(self) -> str:
        return await veille.instance().contexte_reveil()

    def noter_dites(self) -> None:
        """La session est créée : ce que le réveil annonçait (déconnexion, coupure finie) est dit."""
        veille.instance().noter_dites()

    def noter_tour(self, texte: str) -> None:
        self.confirmation.noter_tour(texte)

    def _verifier_evenement(self, brut) -> str | None:
        """None si creer_evenement peut passer, sinon la raison du refus (plan Joi fiable, partie 5).
        Jamais d'invité autre que la personne. Un événement né d'une échéance de la mémoire (ce qui a été
        entendu) exige en plus un oui de la personne, à l'instant, et aucun invité du tout."""
        arguments = _arguments(brut)
        if not isinstance(arguments, dict):
            log.warning("creer_evenement refusé : arguments illisibles.")
            return "Arguments invalides."
        # L'outil n'invite que la personne (inviter_toi) ; ce garde-fou tient même si une liste d'invités
        # apparaît un jour dans ses paramètres, ou si le modèle en invente une.
        invites = arguments.get("invites") or []
        if isinstance(invites, str):
            invites = [invites]
        tiers = [a for a in invites if not isinstance(a, str) or a.strip().lower() != reglages.PROPRIETAIRE]
        if tiers or (invites and not reglages.PROPRIETAIRE):
            log.info("creer_evenement refusé : invité autre que la personne.")
            return "Tu ne peux inviter personne d'autre que la personne elle-même."
        if not est_echeance(arguments.get("source")):
            return None
        if invites or arguments.get("inviter_toi") is True:
            log.info("Événement d'échéance refusé : avec invitation.")
            return "Un événement tiré de « À venir » se crée sans invitation : remets inviter_toi à faux."
        tour = self.confirmation.dernier_tour(AGE_MAX_OUI)
        if not tour or not est_oui_agenda(tour):
            log.info("Événement d'échéance refusé : pas de oui clair.")
            return ("La personne n'a pas dit oui clairement : l'événement n'est pas créé. Dis-lui que tu ne "
                    "l'as pas noté, et redemande simplement « je le note dans mon agenda ? ».")
        log.info("Événement d'échéance confirmé par la personne.")
        return None

    def _verifier_proposition(self, brut) -> str | None:
        """None si la réponse à un engagement proposé peut passer, sinon la raison du refus. Tout ce qui
        n'est pas clairement conforme est refusé (arguments illisibles, réponse inconnue)."""
        arguments = _arguments(brut)
        if not isinstance(arguments, dict) or arguments.get("reponse") not in ("oui", "non") \
                or not isinstance(arguments.get("numero"), str):
            log.warning("marquer_proposition refusé : arguments illisibles.")
            return "Arguments invalides : un numéro de proposition, et « oui » ou « non »."
        if arguments["reponse"] == "non":
            return None  # un refus n'a pas besoin de confirmation : il ne crée rien
        numero = arguments["numero"].strip().lower().strip("[]")
        proposition = next((p for p in engagements.lire() if p.get("numero") == numero), None)
        if proposition is None or proposition.get("etat") != "proposee" or not proposition.get("montree_le"):
            log.info("Proposition %s : pas une proposition ouverte montrée au réveil, tâche refusée.", numero)
            return "Ce n'est pas une proposition ouverte de ton réveil : reprends le numéro du réveil."
        # Un engagement entendu ne devient une tâche que sur un oui de la personne elle-même, à l'instant
        # (le tour auquel Joi répond, de moins de AGE_MAX_OUI secondes), pas sur un oui d'il y a longtemps.
        tour = self.confirmation.dernier_tour(AGE_MAX_OUI)
        if not tour or not engagements.est_oui(tour):
            log.info("Proposition %s : pas de oui clair, tâche refusée.", numero)
            return ("La personne n'a pas dit oui clairement : la tâche n'est pas créée. Dis-lui que tu ne "
                    "l'as pas notée et redemande simplement « je t'en fais une tâche ? ».")
        return None

    def permission(self, requete, invocation):
        if getattr(requete, "kind", None) != "mcp" or getattr(requete, "server_name", None) != SERVEUR:
            return self._repli(requete, invocation)
        outil = outil_du_compte(getattr(requete, "tool_name", None))
        if outil is None:
            return self._repli(requete, invocation)
        if outil == "envoyer_courriel":
            arguments = requete.args if isinstance(requete.args, dict) else {}
            raison = self.confirmation.verifier(arguments.get("brouillon"))
            if raison:
                log.info("Envoi du brouillon %s refusé : %s", arguments.get("brouillon"), raison)
                return PermissionDecisionReject(feedback=raison)
            log.info("Envoi du brouillon %s confirmé par la personne.", arguments.get("brouillon"))
        if outil == "marquer_proposition":
            raison = self._verifier_proposition(requete.args)
            if raison:
                return PermissionDecisionReject(feedback=raison)
        if outil == "creer_evenement":
            raison = self._verifier_evenement(requete.args)
            if raison:
                return PermissionDecisionReject(feedback=raison)
        if outil in OUTILS_ECRITURE:
            # Le contexte du réveil gardé en mémoire ne dit plus vrai (tâche cochée, événement créé).
            veille.invalider()
        return PermissionDecisionApproveOnce()
