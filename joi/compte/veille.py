"""La veille du compte, dans le serveur de Joi (pas dans le serveur MCP, qui ne peut pas lui parler).

- contexte_reveil() : la section « Ton compte » du prompt au réveil (date du jour, courriels non lus,
  ta journée et celle de Joi, ses tâches). Elle vient du dernier contexte gardé en mémoire s'il a moins
  de CACHE_SERVI_MAX, du même jour (préparé au démarrage du serveur, rafraîchi toutes les RAFRAICHIR_MINUTES
  par joi/serveur.py, et en fond après un réveil) : Joi se réveille sans attendre Graph. Sinon, on le
  demande, les blocs en parallèle : 5 s au plus, sinon Joi se réveille sans.
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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import engagements, journal_compte, reglages
from .fournisseur import CompteDeconnecte, ErreurCompte, choisir
from .modele import (Courriel, date_complete, externe, ligne_courriel, ligne_evenement, ligne_tache,
                     maintenant, numero_court)

log = logging.getLogger("joi.compte")

DELAI_REVEIL = 5  # secondes
PREMIERE_FOIS = timedelta(days=1)  # sans état : les courriels des dernières 24 h
MARGE = timedelta(minutes=5)  # on redemande un peu avant le dernier vu (les doublons sont écartés)
VUS_GARDES = 300
# Le contexte du réveil est rafraîchi à ce rythme, à part de la vérification des courriels
# (JOI_COURRIEL_VERIFIER_MINUTES, qui peut être d'une heure).
RAFRAICHIR_MINUTES = 5
# Un contexte gardé du même jour sert tant qu'il a moins de CACHE_SERVI_MAX, avec son heure (« vu à »),
# et se rafraîchit en fond. Avec 6 min, le premier réveil du matin ou un réveil après une mise en veille
# attendait Graph (2,5 à 2,9 s) ; au pire, un courriel ou un rendez-vous de la dernière demi-heure manque
# à la première phrase.
CACHE_SERVI_MAX = timedelta(minutes=30)
# Le compte n'est dit « déconnecté » que si les échecs durent depuis tout ce temps : le 20 novembre,
# une coupure d'une minute (six échecs en rafale) a donné un contexte « déconnecté », puis tout est revenu
# seul. C'est le temps qui compte, pas le nombre d'échecs : un réveil et une fin de conversation
# rafraîchissent aussi le contexte, à quelques secondes d'écart.
DUREE_AVANT_DECONNEXION = timedelta(minutes=RAFRAICHIR_MINUTES - 1)


@dataclass
class _Contexte:
    """Le contexte du compte, sans son en-tête (l'heure du réveil et la date sont mises au moment de
    servir). `complet` : aucun bloc n'a échoué ; `deconnecte` : le compte ne fonctionne plus du tout."""

    lignes: list[str]
    quand: datetime
    complet: bool = True
    deconnecte: bool = False
    propositions: list[str] = field(default_factory=list)  # les numéros des engagements proposés


class Veille:
    def __init__(self, fournisseur=None):
        self.fournisseur = fournisseur or choisir()
        self._verrou = threading.Lock()  # une vérification à la fois (réveil et vérification régulière)
        self._cache: _Contexte | None = None
        self._rafraichissement: asyncio.Task | None = None
        self._coupure_debut: datetime | None = None  # le premier échec d'une déconnexion en cours
        self._deconnexion_confirmee = False  # un contexte « déconnecté » a été gardé (donc dit)
        self._coupure_a_dire: tuple[datetime, datetime] | None = None  # une coupure finie, à dire une fois
        self._deconnexion_dite_le: str | None = None  # le jour où Joi a déjà dit qu'elle est déconnectée
        # Ce que le dernier réveil a confié à Joi, noté par noter_dites() une fois la session créée :
        # un réveil raté ne doit pas faire perdre l'annonce du jour.
        self._a_noter: dict | None = None
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

    def _cache_valable(self) -> _Contexte | None:
        """Le contexte gardé, s'il est assez récent pour être servi tel quel."""
        cache = self._cache
        if cache is None:
            return None
        instant = maintenant()
        if cache.quand.date() != instant.date():  # « aujourd'hui » a changé
            return None
        if instant - cache.quand > CACHE_SERVI_MAX:
            return None
        return cache

    def _texte(self, contexte: _Contexte, depuis_cache: bool) -> str:
        titre = f"## Ton compte (vu à {contexte.quand:%H:%M})" if depuis_cache else "## Ton compte (au réveil)"
        lignes = [titre, f"Nous sommes le {date_complete()} (heure de {reglages.NOM_FUSEAU})."]
        return "\n\n" + "\n".join(lignes + contexte.lignes + self._a_dire(contexte)) + "\n"

    def _a_dire(self, contexte: _Contexte) -> list[str]:
        """Ce que Joi dit d'elle-même sur son compte, une fois : une déconnexion (le premier réveil du
        jour), une coupure finie (au réveil qui suit). Le code tient le « une fois », pas le modèle :
        c'est noté par noter_dites(), une fois la session créée."""
        lignes = []
        self._a_noter = None
        aujourdhui = maintenant().date().isoformat()
        if contexte.deconnecte:
            if self._deconnexion_dite_le != aujourdhui:
                self._a_noter = {"deconnexion": aujourdhui}
                lignes.append("Dis-le à la personne dès ta première réplique, en une phrase (« mon compte "
                              "est déconnecté, il faudra relancer la connexion »), puis n'y reviens que si "
                              "elle t'en parle.")
            else:
                lignes.append("Si la personne te parle de tes courriels ou de l'agenda, dis-le-lui simplement.")
        elif self._coupure_a_dire:
            debut, fin = self._coupure_a_dire
            if debut.date() != maintenant().date():  # une coupure d'un autre jour ne sert plus à rien
                self._coupure_a_dire = None
            else:
                self._a_noter = {"coupure": self._coupure_a_dire}
                lignes.append(f"Ton compte a été coupé de {debut:%H:%M} à {fin:%H:%M}. Dis-le en une phrase "
                              "au début de la conversation.")
        return lignes

    def noter_dites(self) -> None:
        """Le dernier réveil est devenu une vraie session : ce qu'il annonçait ne reviendra pas."""
        a_noter, self._a_noter = self._a_noter, None
        if not a_noter:
            return
        if "deconnexion" in a_noter:
            self._deconnexion_dite_le = a_noter["deconnexion"]
        elif self._coupure_a_dire == a_noter.get("coupure"):
            self._coupure_a_dire = None

    async def contexte_reveil(self) -> str:
        self._a_noter = None  # rien d'un réveil précédent : seul ce réveil-ci peut être noté
        cache = self._cache_valable()
        if cache is not None:
            log.info("Contexte du compte servi depuis celui de %s ; rafraîchi en fond.", f"{cache.quand:%H:%M}")
            self._lancer_rafraichissement()
            await self._noter_montrees(cache)
            return self._texte(cache, depuis_cache=True)
        try:
            contexte = await asyncio.wait_for(asyncio.to_thread(self._contexte), DELAI_REVEIL)
        except TimeoutError:
            log.warning("Le compte n'a pas répondu en %d s : Joi se réveille sans lui.", DELAI_REVEIL)
            self._etat["derniere_erreur"] = f"{datetime.now():%H:%M} trop lent au réveil (> {DELAI_REVEIL} s)"
            return ""
        except Exception:
            log.exception("Contexte du compte impossible au réveil")
            return ""
        servi = self._garder(contexte)
        # Un contexte d'avant (il dit son heure), pas la phrase neutre d'une coupure qui commence.
        depuis_cache = servi is not contexte and servi is self._cache
        await self._noter_montrees(servi)
        return self._texte(servi, depuis_cache=depuis_cache)

    @staticmethod
    async def _noter_montrees(contexte: _Contexte) -> None:
        """Les engagements de ce contexte sont donnés à Joi : ils ne reviendront pas un autre jour."""
        try:
            await asyncio.to_thread(engagements.noter_montrees, contexte.propositions)
        except Exception:
            log.exception("Propositions montrées impossibles à noter")

    # --- Le contexte gardé --------------------------------------------------------------------

    def _garder(self, contexte: _Contexte) -> _Contexte:
        """Un contexte incomplet (un bloc en erreur passagère) ne remplace pas un contexte complet : on
        garde l'ancien, qui dit son heure. Une déconnexion le remplace, mais seulement si elle dure
        depuis DUREE_AVANT_DECONNEXION : une coupure d'une minute passe seule. Rend le contexte à servir
        à un réveil qui attend : l'ancien, quand c'est lui qui reste à cause d'une déconnexion récente."""
        ancien = self._cache
        if ancien is not None and contexte.quand < ancien.quand:
            return ancien  # deux rafraîchissements se sont croisés : le plus récent reste, et il sert
        if contexte.deconnecte:
            if self._coupure_debut is None:
                self._coupure_debut = contexte.quand
            if contexte.quand - self._coupure_debut < DUREE_AVANT_DECONNEXION:
                if ancien is not None and not ancien.deconnecte and ancien.quand.date() == contexte.quand.date():
                    log.warning("Compte déconnecté à %s : je garde le contexte de %s et je réessaie au "
                                "prochain rafraîchissement.", f"{contexte.quand:%H:%M}", f"{ancien.quand:%H:%M}")
                    return ancien
                # Rien d'utilisable à garder (démarrage, action de Joi, minuit) : une phrase neutre, ni
                # gardée ni annoncée, tant que la coupure peut encore passer seule.
                log.warning("Compte déconnecté à %s, sans contexte d'avant : je réessaie au prochain "
                            "rafraîchissement.", f"{contexte.quand:%H:%M}")
                return _Contexte(["Ton compte ne répond pas pour l'instant."], contexte.quand, complet=False)
            self._deconnexion_confirmee = True
        elif contexte.complet or self._coupure_debut is None:
            if self._deconnexion_confirmee and self._coupure_debut:
                self._coupure_a_dire = (self._coupure_debut, contexte.quand)
                log.info("Compte de nouveau joignable, après une coupure depuis %s.",
                         f"{self._coupure_debut:%H:%M}")
            self._deconnexion_confirmee = False
            self._coupure_debut = None
        elif self._deconnexion_confirmee and ancien is not None and ancien.deconnecte:
            # Une erreur passagère pendant une déconnexion ne dit pas que le compte est revenu : la
            # déconnexion reste, et elle reste dite.
            log.warning("Contexte du compte pas rafraîchi (%s) pendant la déconnexion.",
                        self._etat.get("derniere_erreur") or "erreur")
            return ancien
        if not contexte.complet and not contexte.deconnecte and ancien is not None and ancien.complet:
            log.warning("Contexte du compte pas rafraîchi (%s) : je garde celui de %s.",
                        self._etat.get("derniere_erreur") or "erreur", f"{ancien.quand:%H:%M}")
            return contexte
        self._cache = contexte
        return contexte

    def invalider(self) -> None:
        """Joi vient d'agir sur son compte (tâche cochée, événement créé…) : le contexte gardé ne dit
        plus vrai. Le prochain réveil le redemande, à moins qu'un rafraîchissement ne l'ait précédé."""
        self._cache = None

    def rafraichir_en_fond(self) -> None:
        """Lance un rafraîchissement sans l'attendre (fin de conversation : le prochain réveil sera prêt)."""
        self._lancer_rafraichissement()

    async def preparer(self, avec_nouveaux: bool = True) -> None:
        """Prépare (ou rafraîchit) le contexte gardé, sans délai : personne n'attend. avec_nouveaux :
        prendre aussi les courriels arrivés depuis la dernière fois (au démarrage du serveur)."""
        try:
            contexte = await asyncio.to_thread(self._contexte, avec_nouveaux)
        except Exception as e:
            log.exception("Contexte du compte impossible à préparer")
            self._erreur(e)
            return
        self._garder(contexte)

    async def rafraichir(self) -> None:
        """Rafraîchit le contexte gardé, sans prendre les nouveaux courriels : pendant une conversation,
        la vérification régulière s'en charge, et c'est elle qui les annonce."""
        await self.preparer(avec_nouveaux=False)

    def _lancer_rafraichissement(self) -> None:
        if self._rafraichissement is None or self._rafraichissement.done():
            self._rafraichissement = asyncio.get_running_loop().create_task(self.rafraichir())

    async def attendre_rafraichissement(self) -> None:
        """Attend la fin du rafraîchissement en fond, s'il y en a un (pour les tests)."""
        if self._rafraichissement is not None:
            await self._rafraichissement

    # --- Les blocs du contexte ----------------------------------------------------------------

    def _bloc_courriels(self) -> list[str]:
        non_lus = self.fournisseur.courriels_recents(True, 10)
        self._etat["non_lus"] = len(non_lus)
        if not non_lus:
            return ["Aucun courriel non lu."]
        return [f"Courriels non lus dans ta boîte ({len(non_lus)}{' ou plus' if len(non_lus) == 10 else ''}), "
                "à mentionner au moment opportun si c'est utile (le numéro sert à lire_courriel) :",
                externe("\n".join(f"- {ligne_courriel(c, numero_court('c', c.id))}" for c in non_lus))]

    def _bloc_agenda(self) -> list[str]:
        debut = maintenant().replace(hour=0, minute=0, second=0, microsecond=0)
        evenements, remarques = self.fournisseur.agenda(debut, debut + timedelta(days=1), "tous")
        lignes = []
        for qui, titre in (("toi", "La journée de la personne (son agenda)"), ("joi", "Ton agenda")):
            siens = [e for e in evenements if e.agenda == qui and not e.annule]
            if siens:
                lignes.append(f"{titre}, aujourd'hui :")
                lignes.append(externe("\n".join(
                    f"- {ligne_evenement(e, numero_court('e', e.id), avec_jour=False)}" for e in siens)))
            else:
                lignes.append(f"{titre} : rien aujourd'hui.")
        return lignes + remarques

    def _bloc_taches(self) -> list[str]:
        taches = self.fournisseur.taches()
        if not taches:
            return ["Ta liste de tâches est vide."]
        return (["Tes tâches (ta liste de choses à faire, à rappeler à la personne si c'est le moment) :"]
                + [f"- {ligne_tache(t, numero_court('t', t.id))}" for t in taches])

    @staticmethod
    def _bloc_engagements() -> tuple[list[str], list[str]]:
        """Ce que la personne a dit qu'elle ferait, entendu pendant l'écoute (fichier local, pas de
        réseau). La consigne n'entre dans le prompt que s'il y a quelque chose à proposer."""
        try:
            ouvertes = engagements.a_proposer()
        except Exception:
            log.exception("Propositions d'engagements illisibles")
            return [], []
        if not ouvertes:
            return [], []
        lignes = []
        for p in ouvertes:
            dite = datetime.fromisoformat(p["dite_le"])
            pour = f", pour le {date_courte(p['echeance'])}" if p.get("echeance") else ""
            lignes.append(f"- {p['numero']} : {p['titre']} (dit le {date_courte(dite.date().isoformat())} "
                          f"à {dite:%H:%M}{pour}) : « {p['citation']} »")
        return ["Ce que la personne a dit qu'elle ferait, entendu pendant l'écoute. Propose-lui chacun une "
                "fois, en une phrase, au moment opportun (« hier tu as dit que…, je t'en fais une tâche ? »), "
                "puis marquer_proposition avec sa réponse :", externe("\n".join(lignes))], \
            [p["numero"] for p in ouvertes]

    def _contexte(self, avec_nouveaux: bool = True) -> _Contexte:
        """Les blocs (nouveaux courriels au journal, non lus, agenda, tâches) sont indépendants : ils
        partent en parallèle, chacun dans son fil (le client HTTP et le cache des jetons sont sûrs entre
        fils). Un bloc en erreur est remplacé par une phrase qui le dit."""
        quand = maintenant()
        blocs = {"courriels": self._bloc_courriels, "agenda": self._bloc_agenda, "taches": self._bloc_taches,
                 "engagements": self._bloc_engagements}
        if avec_nouveaux:
            blocs["nouveaux"] = self._nouveaux  # au journal, ceux arrivés depuis la dernière fois
        with ThreadPoolExecutor(max_workers=len(blocs), thread_name_prefix="veille") as fils:
            futurs = {nom: fils.submit(bloc) for nom, bloc in blocs.items()}
        resultats, erreurs = {}, {}
        for nom, futur in futurs.items():
            try:
                resultats[nom] = futur.result()
            except ErreurCompte as e:
                erreurs[nom] = e
        for nom, e in erreurs.items():
            log.warning("Contexte du compte, bloc %s : %s", nom, e)
            self._erreur(e)
        deconnexion = next((e for e in erreurs.values() if isinstance(e, CompteDeconnecte)), None)
        if deconnexion:
            # Quoi en faire (le dire tout de suite, ou si on en parle) est ajouté au moment de servir.
            return _Contexte([f"Ton compte ne fonctionne pas pour l'instant. {deconnexion}"],
                             quand, complet=False, deconnecte=True)
        repli = {"courriels": ["Tes courriels ne sont pas disponibles pour l'instant."],
                 "agenda": ["L'agenda n'est pas disponible pour l'instant."], "taches": [], "engagements": []}
        lignes_engagements, propositions = resultats.get("engagements", ([], []))
        resultats["engagements"] = lignes_engagements
        lignes = []
        for nom in ("courriels", "agenda", "taches", "engagements"):
            lignes += resultats[nom] if nom in resultats else repli[nom]
        # Les nouveaux courriels ne s'affichent pas : leur échec ne rend pas le contexte incomplet.
        return _Contexte(lignes, quand, complet=not (erreurs.keys() & {"courriels", "agenda", "taches"}),
                         propositions=propositions)


def date_courte(jour: str) -> str:
    """« 2026-11-17 » -> « mardi 17 »."""
    d = datetime.fromisoformat(jour)
    return f"{('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')[d.weekday()]} {d.day}"


_instance: Veille | None = None


def instance() -> Veille:
    global _instance
    if _instance is None:
        _instance = Veille()
    return _instance


def invalider() -> None:
    """Invalide le contexte gardé, s'il y a une veille (sinon, rien à invalider)."""
    if _instance is not None:
        _instance.invalider()
