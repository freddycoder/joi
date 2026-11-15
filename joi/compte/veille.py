"""La veille du compte, dans le serveur de Joi (pas dans le serveur MCP, qui ne peut pas lui parler).

- contexte_reveil() : la section « Ton compte » du prompt au réveil (date du jour, courriels non lus,
  ta journée et celle de Joi, ses tâches). 5 s au plus : sinon, Joi se réveille sans.
- verifier() : les courriels arrivés depuis la dernière fois. Chacun entre au journal (en résumé) une
  seule fois ; le serveur de Joi annonce ceux qui sont pertinents (voir joi/serveur.py).
- etat() : pour le monitoring (connecté, dernière vérification, non lus, dernière erreur).

L'état local (dernier courriel vu) est dans donnees/compte/etat.json. Le fournisseur est le même que
celui du serveur MCP, appelé en direct ; le cache des jetons est partagé (verrou de fichier).
"""

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timedelta

from . import journal_compte, reglages
from .fournisseur import CompteDeconnecte, ErreurCompte, choisir
from .modele import (Courriel, date_complete, externe, ligne_courriel, ligne_evenement, ligne_tache,
                     maintenant, numero_court)

log = logging.getLogger("joi.compte")

DELAI_REVEIL = 5  # secondes
PREMIERE_FOIS = timedelta(days=1)  # sans état : les courriels des dernières 24 h
MARGE = timedelta(minutes=5)  # on redemande un peu avant le dernier vu (les doublons sont écartés)
VUS_GARDES = 300


class Veille:
    def __init__(self, fournisseur=None):
        self.fournisseur = fournisseur or choisir()
        self._verrou = threading.Lock()  # une vérification à la fois (réveil et vérification régulière)
        self._etat = {
            "connecte": None,
            "adresse": None,
            "derniere_verification": None,
            "non_lus": None,
            "derniere_erreur": None,
        }

    # --- État local ---------------------------------------------------------------------------

    @staticmethod
    def _fichier():
        return reglages.DOSSIER / "etat.json"

    def _lire_etat(self) -> dict:
        try:
            return json.loads(self._fichier().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _ecrire_etat(self, etat: dict) -> None:
        fichier = reglages.dossier() / "etat.json"
        temporaire = fichier.with_suffix(".tmp")
        temporaire.write_text(json.dumps(etat, ensure_ascii=False), encoding="utf-8")
        os.replace(temporaire, fichier)

    def _erreur(self, e: Exception) -> None:
        self._etat["derniere_erreur"] = f"{datetime.now():%H:%M} {e}"[:200]
        if isinstance(e, CompteDeconnecte):
            self._etat["connecte"] = False

    def etat(self) -> dict:
        return {"actif": True, "fournisseur": self.fournisseur.nom, **self._etat}

    # --- Nouveaux courriels -------------------------------------------------------------------

    def _nouveaux(self) -> list[Courriel]:
        """Les courriels arrivés depuis la dernière vérification, notés au journal (du plus ancien au
        plus récent). Lève ErreurCompte."""
        with self._verrou:
            etat = self._lire_etat()
            try:
                depuis = datetime.fromisoformat(etat["dernier_recu"]) - MARGE
            except (KeyError, TypeError, ValueError):
                depuis = maintenant() - PREMIERE_FOIS
            courriels = self.fournisseur.courriels_depuis(depuis)
            vus = list(etat.get("vus", []))
            nouveaux = sorted((c for c in courriels if c.id not in vus), key=lambda c: c.recu or depuis)
            for c in nouveaux:
                journal_compte.courriel_recu(c)
                vus.append(c.id)
            recus = [c.recu for c in courriels if c.recu]
            if recus:
                dernier = max(recus)
                ancien = etat.get("dernier_recu")
                if not ancien or dernier > datetime.fromisoformat(ancien):
                    etat["dernier_recu"] = dernier.isoformat()
            etat["vus"] = vus[-VUS_GARDES:]
            etat["verifie"] = datetime.now().isoformat(timespec="seconds")
            self._ecrire_etat(etat)
        self._etat.update(connecte=True, derniere_verification=etat["verifie"], adresse=self.fournisseur.adresse())
        if nouveaux:
            log.info("%d nouveau(x) courriel(s) dans la boîte de Joi.", len(nouveaux))
        return nouveaux

    async def verifier(self) -> list[Courriel]:
        """Les nouveaux courriels (liste vide si le compte ne répond pas : l'erreur va au monitoring)."""
        try:
            return await asyncio.to_thread(self._nouveaux)
        except ErreurCompte as e:
            log.warning("Vérification des courriels impossible : %s", e)
            self._erreur(e)
        except Exception as e:
            log.exception("Vérification des courriels impossible")
            self._erreur(e)
        return []

    # --- Réveil -------------------------------------------------------------------------------

    async def contexte_reveil(self) -> str:
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._contexte), DELAI_REVEIL)
        except TimeoutError:
            log.warning("Le compte n'a pas répondu en %d s : Joi se réveille sans lui.", DELAI_REVEIL)
            self._etat["derniere_erreur"] = f"{datetime.now():%H:%M} trop lent au réveil (> {DELAI_REVEIL} s)"
            return ""
        except Exception:
            log.exception("Contexte du compte impossible au réveil")
            return ""

    def _contexte(self) -> str:
        lignes = ["## Ton compte (au réveil)", f"Nous sommes le {date_complete()} (heure de {reglages.NOM_FUSEAU})."]
        try:
            self._nouveaux()  # au journal, ceux arrivés depuis la dernière fois
        except CompteDeconnecte as e:
            self._erreur(e)
            lignes.append(f"Ton compte ne fonctionne pas pour l'instant. {e} Si la personne te parle de tes "
                          "courriels ou de l'agenda, dis-le-lui simplement.")
            return "\n\n" + "\n".join(lignes) + "\n"
        except ErreurCompte as e:
            self._erreur(e)

        f = self.fournisseur
        try:
            non_lus = f.courriels_recents(True, 10)
            self._etat["non_lus"] = len(non_lus)
            if non_lus:
                lignes.append(f"Courriels non lus dans ta boîte ({len(non_lus)}{' ou plus' if len(non_lus) == 10 else ''}), "
                              "à mentionner au moment opportun si c'est utile (le numéro sert à lire_courriel) :")
                lignes.append(externe("\n".join(f"- {ligne_courriel(c, numero_court('c', c.id))}" for c in non_lus)))
            else:
                lignes.append("Aucun courriel non lu.")
        except ErreurCompte as e:
            self._erreur(e)
            lignes.append("Tes courriels ne sont pas disponibles pour l'instant.")

        debut = maintenant().replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            evenements, remarques = f.agenda(debut, debut + timedelta(days=1), "tous")
            for qui, titre in (("toi", "La journée de la personne (son agenda)"), ("joi", "Ton agenda")):
                siens = [e for e in evenements if e.agenda == qui and not e.annule]
                if siens:
                    lignes.append(f"{titre}, aujourd'hui :")
                    lignes.append(externe("\n".join(
                        f"- {ligne_evenement(e, numero_court('e', e.id), avec_jour=False)}" for e in siens)))
                else:
                    lignes.append(f"{titre} : rien aujourd'hui.")
            lignes += remarques
        except ErreurCompte as e:
            self._erreur(e)
            lignes.append("L'agenda n'est pas disponible pour l'instant.")

        try:
            taches = f.taches()
            if taches:
                lignes.append("Tes tâches (ta liste de choses à faire, à rappeler à la personne si c'est le moment) :")
                lignes += [f"- {ligne_tache(t, numero_court('t', t.id))}" for t in taches]
            else:
                lignes.append("Ta liste de tâches est vide.")
        except ErreurCompte as e:
            self._erreur(e)
        return "\n\n" + "\n".join(lignes) + "\n"


_instance: Veille | None = None


def instance() -> Veille:
    global _instance
    if _instance is None:
        _instance = Veille()
    return _instance
