"""Ce que la session Copilot de Joi reçoit quand le compte est actif (voir joi/cerveau.py) :

- le serveur MCP du compte (python -m joi.compte), avec la liste explicite de ses outils ;
- les consignes « Ton compte » (joi/compte/personnalite.md), ajoutées au prompt système ;
- le contexte du réveil (courriels non lus, agendas, tâches ; voir veille.py) ;
- le gestionnaire de permissions : les outils du compte sont permis, sauf envoyer_courriel, permis
  seulement si la personne a confirmé (voir brouillons.py). Tout le reste est refusé, comme sans compte.

Copilot approuve de lui-même les outils MCP marqués « lecture seule » : seuls les outils d'écriture
passent par le gestionnaire (vérifié avec le Copilot SDK 1.0.31).
"""

import logging
import sys
from pathlib import Path
from typing import Callable

from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject

from .. import config
from . import OUTILS, OUTILS_ECRITURE, SERVEUR, brouillons, reglages, veille

log = logging.getLogger("joi.compte")

FICHIER_CONSIGNES = Path(__file__).resolve().parent / "personnalite.md"
DELAI_OUTIL_MS = 30_000


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

    def noter_tour(self, texte: str) -> None:
        self.confirmation.noter_tour(texte)

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
        if outil in OUTILS_ECRITURE:
            # Le contexte du réveil gardé en mémoire ne dit plus vrai (tâche cochée, événement créé).
            veille.instance().invalider()
        return PermissionDecisionApproveOnce()
