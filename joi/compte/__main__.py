"""Le compte de Joi en ligne de commande.

  python -m joi.compte              le serveur MCP (stdio), lancé par la session Copilot de Joi
  python -m joi.compte connecter    connexion initiale du compte de Joi, dans le navigateur (une fois)
  python -m joi.compte connecter --code   la même chose, par code d'appareil
  python -m joi.compte etat         connecté ou non, adresses approuvées, agenda partagé
  python -m joi.compte essai        affiche les derniers courriels, l'agenda de la journée et les tâches
  python -m joi.compte deconnecter  efface les jetons
"""

import logging
import logging.handlers
import sys
from datetime import timedelta

from .. import config


def _journal_technique(console: bool) -> None:
    """En mode serveur, stdout est le canal MCP : rien ne doit y être écrit. Le journal va sur stderr
    (gardé par la CLI Copilot) et dans donnees/logs/compte.log. Jamais de jeton dedans."""
    racine = logging.getLogger()
    racine.setLevel(logging.INFO)
    format_ = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    fichier = logging.handlers.RotatingFileHandler(
        config.DOSSIER_LOGS / "compte.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    fichier.setFormatter(format_)
    racine.addHandler(fichier)
    if console:
        erreurs = logging.StreamHandler(sys.stderr)
        erreurs.setFormatter(format_)
        erreurs.setLevel(logging.WARNING)
        racine.addHandler(erreurs)
    # MSAL et httpx sont bavards (et MSAL peut citer des URL d'autorisation) : avertissements seulement.
    for nom in ("msal", "httpx", "httpcore", "mcp"):
        logging.getLogger(nom).setLevel(logging.WARNING)


def _servir() -> None:
    from .serveur_mcp import creer_serveur

    _journal_technique(console=True)
    creer_serveur().run("stdio")


def _etat(fournisseur) -> None:
    from . import reglages

    print(f"Fournisseur      : {config.JOI_COMPTE}")
    print(f"Adresse de Joi   : {reglages.COURRIEL or '(JOI_COURRIEL vide)'}")
    print(f"Ton adresse      : {reglages.PROPRIETAIRE or '(JOI_COURRIEL_PROPRIETAIRE vide)'}")
    print(f"Approuvées       : {', '.join(reglages.adresses_approuvees()) or 'aucune'}")
    print(f"Fuseau           : {reglages.NOM_FUSEAU}")
    connecte = fournisseur.adresse()
    if not connecte:
        print("Connexion        : aucune (python -m joi.compte connecter)")
        return
    print(f"Connexion        : {connecte} (vérification…)")
    print(f"Vérifiée         : {fournisseur.verifier_connexion()}")
    if reglages.COURRIEL and connecte != reglages.COURRIEL:
        print(f"ATTENTION : connecté en {connecte}, mais JOI_COURRIEL vaut {reglages.COURRIEL}.")


def _essai(fournisseur) -> None:
    from .modele import ligne_courriel, ligne_evenement, ligne_tache, maintenant

    print("\nDerniers courriels :")
    for c in fournisseur.courriels_recents(False, 5) or []:
        print("  " + ligne_courriel(c))
    debut = maintenant().replace(hour=0, minute=0, second=0, microsecond=0)
    evenements, remarques = fournisseur.agenda(debut, debut + timedelta(days=2), "tous")
    print("\nAgenda (aujourd'hui et demain) :")
    for e in evenements:
        print(f"  ({'Joi' if e.agenda == 'joi' else 'toi'}) {ligne_evenement(e)}")
    if not evenements:
        print("  rien")
    for r in remarques:
        print(f"  {r}")
    print("\nTâches :")
    for t in fournisseur.taches() or []:
        print("  " + ligne_tache(t))


def main(arguments: list[str]) -> int:
    commande = arguments[0] if arguments else "serveur"
    if commande in ("-h", "--help", "aide"):
        print(__doc__)
        return 0
    if not config.JOI_COMPTE:
        refuse = f" (valeur « {config.JOI_COMPTE_REFUSE} » non prise en charge)" if config.JOI_COMPTE_REFUSE else ""
        print(f"Le compte de Joi n'est pas activé{refuse} : mets JOI_COMPTE=microsoft dans .env.", file=sys.stderr)
        return 2
    if commande == "serveur":
        _servir()
        return 0

    _journal_technique(console=True)
    from .fournisseur import ErreurCompte, choisir

    fournisseur = choisir()
    try:
        if commande == "connecter":
            adresse = fournisseur.connecter(print, par_code="--code" in arguments[1:])
            print(f"\nConnectée : {adresse}. Les jetons sont gardés, chiffrés, dans donnees/compte/jetons.bin.")
        elif commande == "deconnecter":
            fournisseur.deconnecter()
            print("Jetons effacés.")
        elif commande == "etat":
            _etat(fournisseur)
        elif commande == "essai":
            _etat(fournisseur)
            _essai(fournisseur)
        else:
            print(__doc__)
            return 2
    except ErreurCompte as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
