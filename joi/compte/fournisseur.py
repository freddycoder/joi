"""L'interface commune des fournisseurs de compte (Microsoft d'abord, iCloud ensuite), et le choix
selon JOI_COMPTE. Les outils MCP et la veille ne parlent qu'à cette interface.

Les méthodes sont synchrones : le serveur MCP les exécute dans un fil à part, la veille les appelle
avec asyncio.to_thread.
"""

from datetime import datetime
from typing import Callable, Protocol

from .. import config
from .modele import Courriel, Evenement, Tache


class ErreurCompte(Exception):
    """Le service a refusé ou échoué : le message est montré tel quel au modèle et au monitoring."""


class CompteDeconnecte(ErreurCompte):
    def __init__(self, raison: str = ""):
        super().__init__(
            "Le compte de Joi est déconnecté"
            + (f" ({raison})" if raison else "")
            + " : relance « python -m joi.compte connecter »."
        )


class Fournisseur(Protocol):
    nom: str

    # Connexion
    def connecter(self, afficher: Callable[[str], None], par_code: bool = False) -> str: ...  # renvoie l'adresse
    def deconnecter(self) -> None: ...
    def adresse(self) -> str | None: ...  # None : pas connecté (sans appel réseau)
    def verifier_connexion(self) -> str: ...  # appel réseau ; renvoie l'adresse ou lève une erreur

    # Courriels
    def courriels_recents(self, non_lus_seulement: bool, nombre: int) -> list[Courriel]: ...
    def lire_courriel(self, id: str) -> Courriel: ...
    def chercher_courriels(self, recherche: str, nombre: int) -> list[Courriel]: ...
    def courriels_depuis(self, depuis: datetime) -> list[Courriel]: ...  # reçus après cet instant
    def envoyer_courriel(self, a: list[str], sujet: str, texte: str, en_reponse_a: str | None) -> None: ...

    # Agenda
    def agenda(self, debut: datetime, fin: datetime, qui: str) -> tuple[list[Evenement], list[str]]: ...
    def creer_evenement(self, titre: str, debut: datetime, fin: datetime, lieu: str, note: str,
                        rappel_minutes: int | None, invites: list[str]) -> Evenement: ...
    def modifier_evenement(self, id: str, titre: str | None, debut: datetime | None, fin: datetime | None,
                           lieu: str | None, note: str | None) -> Evenement: ...
    def annuler_evenement(self, id: str) -> Evenement: ...

    # Tâches (sans heure)
    def taches(self) -> list[Tache]: ...
    def ajouter_tache(self, titre: str, note: str) -> Tache: ...
    def terminer_tache(self, id: str) -> Tache: ...


def choisir() -> Fournisseur:
    """Le fournisseur de JOI_COMPTE. À n'appeler que si le compte est actif."""
    if config.JOI_COMPTE == "microsoft":
        from .microsoft import Microsoft
        return Microsoft()
    raise ErreurCompte(
        "JOI_COMPTE n'est pas réglé (ou pas pris en charge) dans .env : mets JOI_COMPTE=microsoft."
    )
