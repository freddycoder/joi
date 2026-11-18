"""Le compte de Joi (optionnel) : son courriel, son agenda (et le tien, en lecture) et ses tâches.

Importé seulement quand JOI_COMPTE est réglé dans .env (voir docs/plan-compte-joi.md).

- serveur_mcp.py : les outils, servis en MCP (stdio) à la session Copilot de Joi : python -m joi.compte
- integration.py : ce que la session Copilot de Joi reçoit (serveur MCP, consignes, permissions)
- veille.py      : dans le serveur de Joi, les courriels qui arrivent et le contexte du réveil
- engagements.py : dans le serveur de Joi, les engagements de la personne entendus pendant l'écoute,
                   proposés au réveil (une tâche sur un oui, rien d'autre)
"""

# Nom du serveur MCP dans la session Copilot : les outils y deviennent « compte-<outil> ».
SERVEUR = "compte"

# preparer_courriel ne touche qu'un fichier local : il est de lecture pour Copilot.
OUTILS_LECTURE = ("courriels_recents", "lire_courriel", "chercher_courriels", "preparer_courriel", "agenda",
                  "taches")
OUTILS_ECRITURE = ("envoyer_courriel", "creer_evenement", "modifier_evenement", "annuler_evenement",
                   "ajouter_tache", "terminer_tache", "marquer_proposition")
OUTILS = OUTILS_LECTURE + OUTILS_ECRITURE
