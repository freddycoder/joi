"""Le compte Microsoft (Outlook.com, Microsoft 365 personnel) de Joi, par Microsoft Graph.

Connexion : MSAL, par le navigateur (python -m joi.compte connecter), ou par code d'appareil
(connecter --code), sur un compte personnel.
Le cache des jetons est chiffré par Windows (DPAPI, via msal-extensions) dans donnees/compte/jetons.bin,
avec un verrou de fichier : le serveur MCP et la veille de Joi (deux processus) le partagent.
Les jetons ne sont jamais écrits dans un journal.

Les heures sont demandées et envoyées en UTC, puis converties en heure locale (JOI_FUSEAU) : on ne
dépend pas des noms de fuseaux acceptés par l'en-tête Prefer: outlook.timezone.
"""

import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from . import reglages
from .fournisseur import CompteDeconnecte, ErreurCompte
from .modele import Courriel, Evenement, Tache

log = logging.getLogger("joi.compte.microsoft")

GRAPH = "https://graph.microsoft.com/v1.0"
AUTORITE = "https://login.microsoftonline.com/consumers"  # comptes Microsoft personnels
# offline_access est ajouté d'office par MSAL (le jeton de rafraîchissement).
PORTEES = ["User.Read", "Mail.ReadWrite", "Mail.Send", "Calendars.ReadWrite", "Calendars.Read.Shared",
           "Tasks.ReadWrite"]
# Un refus net de Microsoft (connexion révoquée, consentement à redonner) : réessayer ne sert à rien.
REFUS_NETS = ("invalid_grant", "interaction_required", "consent_required", "login_required")
PAUSE_AVANT_REESSAI = 2  # secondes, avant le second essai d'un rafraîchissement de jeton raté
# Un échec tout récent vaut pour les autres fils : les blocs du contexte du réveil partent ensemble, et
# chacun aurait refait ses deux essais (8 s en tout, au-delà du délai du réveil) et ses avertissements.
ECHEC_RETENU_S = 5
CHAMPS_COURRIEL = "id,subject,from,toRecipients,receivedDateTime,bodyPreview,isRead,inferenceClassification"
CHAMPS_EVENEMENT = ("id,subject,start,end,isAllDay,location,bodyPreview,attendees,isReminderOn,"
                    "reminderMinutesBeforeStart,isCancelled")
# Connexion dans le navigateur : Microsoft revient vers cette adresse, à inscrire telle quelle dans Entra
# (plateforme « Applications mobiles et de bureau ») : les comptes personnels exigent le port exact.
PORT_CONNEXION = 8050
REDIRECTION = f"http://localhost:{PORT_CONNEXION}"
LISTE_TACHES = "Joi"  # la liste de Joi dans Microsoft To Do (créée au besoin)
DUREE_CACHE_AGENDAS = 600  # secondes
TAILLE_MAX_COURRIEL = 8000  # caractères d'un courriel lu en entier
TAILLE_APERCU = 255
UTC = timezone.utc


def _q(identifiant: str) -> str:
    return quote(identifiant, safe="")


def _net(texte: str, taille: int = TAILLE_APERCU) -> str:
    texte = re.sub(r"\s+", " ", texte or "").strip()
    return texte if len(texte) <= taille else texte[: taille - 1] + "…"


def _instant_utc(texte: str | None) -> datetime | None:
    if not texte:
        return None
    return datetime.fromisoformat(texte.replace("Z", "+00:00"))


def _instant_evenement(valeur: dict, journee_entiere: bool) -> datetime:
    """{"dateTime": "2026-11-15T19:00:00.0000000", "timeZone": "UTC"}. Un événement d'une journée
    entière est « flottant » : minuit, quel que soit le fuseau, donc minuit en heure locale."""
    brut = datetime.fromisoformat(valeur["dateTime"].split(".")[0])
    if journee_entiere:
        return brut.replace(tzinfo=reglages.FUSEAU)
    try:
        fuseau = ZoneInfo(valeur.get("timeZone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        fuseau = UTC
    return brut.replace(tzinfo=fuseau)


def _vers_graph(instant: datetime) -> dict:
    return {"dateTime": instant.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds"),
            "timeZone": "UTC"}


# La ligne qui sépare un message de ce qu'il cite ou transfère (Outlook, Gmail, Apple Mail).
_SEPARATEUR = re.compile(
    r"^(-{2,}\s*(original message|message d'origine|forwarded message|message transféré)|_{5,}"
    r"|begin forwarded message|début du message (réexpédié|transféré))", re.I)
_SUJET_TRANSFERT = re.compile(r"^\s*(tr|fwd?)\s*:", re.I)
# Les en-têtes d'un courriel transféré : ceux qu'on garde, et ceux qu'on saute (destinataires).
_ENTETE_GARDE = re.compile(r"^(de|from|envoyé|sent|date|objet|subject)\s*:", re.I)
_ENTETE_SAUTE = re.compile(r"^(à|a|to|cc|cci|bcc|répondre à|reply-to)\s*:", re.I)


def _lignes(texte: str) -> list[str]:
    return texte.replace("\r\n", "\n").split("\n")


def _sans_citations(texte: str) -> str:
    """Retire l'historique cité sous une réponse (« Le … a écrit : », « De : … », lignes « > »)."""
    lignes = []
    for ligne in _lignes(texte):
        nette = ligne.strip()
        if (_SEPARATEUR.match(nette)
                or re.match(r"^(le .+ a écrit|on .+ wrote)\s*:$", nette, re.I)
                or (re.match(r"^(de|from)\s*:", nette, re.I) and lignes)):
            break
        if nette.startswith(">"):
            continue
        lignes.append(ligne.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lignes)).strip()


_LIEN = re.compile(r"<?(https?://[^\s<>]+)>?")
_IMAGE = re.compile(r"[ \t]*\[https?://[^\s\]]+\.(png|jpe?g|gif|webp|svg)\][ \t]*", re.I)
_INVISIBLES = re.compile("[­͏​-‏⁠﻿]")
TAILLE_MAX_LIEN = 100  # au-delà, un lien (souvent de suivi) n'apprend rien à Joi et noie le texte


def _liens_courts(texte: str) -> str:
    """Les liens d'un courriel en texte : Safelinks d'Outlook déballés, liens trop longs retirés
    (« [lien] » s'il n'y a pas de texte autour), caractères invisibles des infolettres effacés."""
    def raccourcir(m: re.Match) -> str:
        lien = m.group(1)
        if "safelinks.protection.outlook.com" in lien:
            lien = (parse_qs(urlparse(lien).query).get("url") or [lien])[0]
        if len(lien) <= TAILLE_MAX_LIEN:
            return f"<{lien}>" if m.group(0).startswith("<") else lien
        return "" if m.group(0).startswith("<") else "[lien]"
    texte = _IMAGE.sub("", texte)  # « [https://…/icone.png] » : une image sans texte de remplacement
    texte = _INVISIBLES.sub("", _LIEN.sub(raccourcir, texte))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+\n", "\n", texte))


def _tronquer(texte: str) -> str:
    return texte if len(texte) <= TAILLE_MAX_COURRIEL else texte[:TAILLE_MAX_COURRIEL] + "\n[…coupé]"


def _texte_transfere(corps: str) -> str:
    """Un courriel transféré : la note de celui qui transfère, puis le courriel transféré (expéditeur,
    date, sujet et texte, sans son propre historique). Vide si le corps ne contient pas de transfert."""
    lignes = _lignes(corps)
    debut = next((i for i, l in enumerate(lignes)
                  if _SEPARATEUR.match(l.strip()) or re.match(r"^(de|from)\s*:", l.strip(), re.I)), None)
    if debut is None:
        return ""
    note = _sans_citations("\n".join(lignes[:debut]))
    i = debut + 1 if _SEPARATEUR.match(lignes[debut].strip()) else debut
    while i < len(lignes) and not lignes[i].strip():
        i += 1
    entetes = []
    while i < len(lignes) and lignes[i].strip():
        nette = lignes[i].strip()
        if _ENTETE_GARDE.match(nette):
            entetes.append(nette)
        elif not _ENTETE_SAUTE.match(nette):
            break
        i += 1
    if not entetes:
        return ""
    texte = _sans_citations("\n".join(lignes[i:]))
    morceaux = [note] if note else []
    morceaux.append("--- Courriel transféré ---\n" + "\n".join(entetes))
    morceaux.append(texte or "(courriel transféré vide)")
    return "\n\n".join(morceaux)


def _texte_courriel(sujet: str, nouveau: str, corps: str) -> str:
    """Le texte d'un courriel lu en entier. `nouveau` (uniqueBody) est ce que l'expéditeur a écrit
    lui-même, sans l'historique ; `corps` (body) est le message complet. Un transfert garde le
    courriel transféré, que l'historique d'une réponse perdrait."""
    nouveau = nouveau if nouveau.strip() else ""
    texte = _sans_citations(nouveau or corps)
    if _SUJET_TRANSFERT.match(sujet or "") or not texte:
        texte = _texte_transfere(corps) or texte
    return _tronquer(_liens_courts(texte))


class Microsoft:
    nom = "microsoft"

    def __init__(self, http: httpx.Client | None = None, application=None):
        self._http = http or httpx.Client(base_url=GRAPH, timeout=20)
        self._application = application
        self._agendas: list[dict] | None = None
        self._agendas_quand = 0.0
        self._liste_taches: str | None = None
        # La veille appelle Graph depuis plusieurs fils à la fois (contexte du réveil) : le client httpx
        # et le cache MSAL sont sûrs entre fils, mais pas la création paresseuse de l'application ni deux
        # rafraîchissements du même jeton en même temps.
        self._verrou_jeton = threading.Lock()
        # (time.monotonic(), raison, déconnecté) : déconnecté faux pour une coupure du réseau
        self._dernier_echec: tuple[float, str, bool] | None = None

    # --- Connexion ----------------------------------------------------------------------------

    def _app(self):
        if self._application is None:
            if not reglages.MS_CLIENT_ID:
                raise ErreurCompte(
                    "JOI_MS_CLIENT_ID manque dans .env : inscris d'abord l'application dans Microsoft "
                    "Entra (voir le README, « Le compte de Joi »)."
                )
            import msal
            import msal_extensions

            chemin = reglages.dossier() / "jetons.bin"
            persistance = msal_extensions.build_encrypted_persistence(str(chemin))
            self._application = msal.PublicClientApplication(
                reglages.MS_CLIENT_ID,
                authority=AUTORITE,
                token_cache=msal_extensions.PersistedTokenCache(persistance),
            )
        return self._application

    def _compte(self) -> dict | None:
        comptes = self._app().get_accounts()
        if reglages.COURRIEL:
            for compte in comptes:
                if (compte.get("username") or "").lower() == reglages.COURRIEL:
                    return compte
        return comptes[0] if comptes else None

    def _jeton(self, forcer: bool = False) -> str:
        """Le jeton d'accès, rafraîchi au besoin. acquire_token_silent rendait None pour toute erreur, et
        on écrivait « jeton expiré » : le 20 novembre, une coupure d'une minute a été prise pour une
        déconnexion. On garde donc la vraie raison, et on réessaie une fois, sauf sur un refus net."""
        with self._verrou_jeton:
            if self._dernier_echec and time.monotonic() - self._dernier_echec[0] < ECHEC_RETENU_S:
                _, raison, deconnecte = self._dernier_echec
                raise CompteDeconnecte(raison) if deconnecte else ErreurCompte(raison)
            compte = self._compte()
            if not compte:
                raise CompteDeconnecte("aucune connexion enregistrée")
            for essai in range(2):
                try:
                    resultat = self._app().acquire_token_silent_with_error(PORTEES, account=compte,
                                                                           force_refresh=forcer)
                except Exception as e:
                    # MSAL relance l'erreur réseau du rafraîchissement : une coupure, pas une
                    # déconnexion. Un second essai, puis « Microsoft ne répond pas ».
                    log.warning("Jeton du compte pas rafraîchi (essai %d) : %s", essai + 1, type(e).__name__)
                    if essai == 0:
                        time.sleep(PAUSE_AVANT_REESSAI)
                        continue
                    raison = "Microsoft ne répond pas (rafraîchissement du jeton)"
                    self._dernier_echec = (time.monotonic(), raison, False)
                    raise ErreurCompte(raison) from e
                if resultat and "access_token" in resultat:
                    self._dernier_echec = None
                    return resultat["access_token"]
                erreur = (resultat or {}).get("error") or "aucun jeton dans le cache"
                detail = ((resultat or {}).get("error_description") or "").splitlines()
                log.warning("Jeton du compte refusé (essai %d) : %s%s", essai + 1, erreur,
                            f" ({detail[0][:200]})" if detail else "")
                if erreur in REFUS_NETS or not resultat:
                    break  # réessayer ne changera rien : il faut se reconnecter
                if essai == 0:
                    time.sleep(PAUSE_AVANT_REESSAI)
            raison = erreur if erreur != "aucun jeton dans le cache" else "jeton expiré"
            self._dernier_echec = (time.monotonic(), raison, True)
        raise CompteDeconnecte(raison)

    def connecter(self, afficher: Callable[[str], None] = print, par_code: bool = False) -> str:
        """Par défaut, la page de connexion Microsoft s'ouvre dans le navigateur (redirection vers
        http://localhost, à inscrire dans Entra). par_code : connexion par code d'appareil."""
        app = self._app()
        joi = f" ({reglages.COURRIEL})" if reglages.COURRIEL else ""
        if par_code:
            resultat = self._connecter_par_code(app, afficher, joi)
        else:
            afficher(
                f"La page de connexion Microsoft s'ouvre dans ton navigateur. Connecte-toi avec le compte de "
                f"Joi{joi}, pas le tien (« Utiliser un autre compte » au besoin), et accepte les "
                "autorisations demandées. J'attends (5 minutes au plus)…"
            )
            resultat = app.acquire_token_interactive(
                PORTEES, login_hint=reglages.COURRIEL or None, prompt="select_account", timeout=300,
                port=PORT_CONNEXION,
            )
        if "access_token" not in resultat:
            description = resultat.get("error_description") or resultat.get("error") or "abandon"
            if "redirect" in description.lower() or "AADSTS50011" in description:
                description += (f"\nDans Entra, « Authentification » de l'application Joi, plateforme "
                                f"« Applications mobiles et de bureau » : ajoute l'URI {REDIRECTION}, exactement.")
            raise ErreurCompte(f"Connexion refusée : {description}")
        return self.verifier_connexion()

    @staticmethod
    def _connecter_par_code(app, afficher: Callable[[str], None], joi: str) -> dict:
        flux = app.initiate_device_flow(scopes=PORTEES)
        if "user_code" not in flux:
            raise ErreurCompte(f"Connexion impossible : {flux.get('error_description') or flux}")
        minutes = int(flux.get("expires_in", 900)) // 60
        afficher(
            f"1. Ouvre {flux['verification_uri']} dans une fenêtre de navigation privée (pour qu'aucun "
            "autre compte Microsoft déjà connecté ne s'en mêle).\n"
            f"2. Entre le code {flux['user_code']} dans les {minutes} minutes. Seul le code de la commande en "
            "cours compte : laisse cette fenêtre ouverte jusqu'à la fin.\n"
            f"3. Connecte-toi avec le compte de Joi{joi}, pas le tien, et accepte les autorisations demandées.\n"
            "J'attends…"
        )
        return app.acquire_token_by_device_flow(flux)

    def deconnecter(self) -> None:
        app = self._app()
        for compte in app.get_accounts():
            app.remove_account(compte)
        for nom in ("jetons.bin", "jetons.bin.lockfile"):
            (reglages.DOSSIER / nom).unlink(missing_ok=True)

    def adresse(self) -> str | None:
        compte = self._compte()
        return (compte.get("username") or "").lower() if compte else None

    def verifier_connexion(self) -> str:
        moi = self._requete("GET", "/me", params={"$select": "userPrincipalName,mail"})
        return (moi.get("mail") or moi.get("userPrincipalName") or "").lower()

    # --- Requêtes -----------------------------------------------------------------------------

    def _requete(self, methode: str, chemin: str, *, params: dict | None = None, json: dict | None = None,
                 entetes: dict | None = None) -> dict:
        for essai in (0, 1):
            jeton = self._jeton(forcer=essai == 1)
            try:
                reponse = self._http.request(
                    methode, chemin, params=params, json=json,
                    headers={"Authorization": f"Bearer {jeton}", **(entetes or {})},
                )
            except httpx.HTTPError as e:
                raise ErreurCompte(f"Microsoft ne répond pas ({type(e).__name__})") from None
            if reponse.status_code != 401:
                break
        if reponse.status_code == 401:
            raise CompteDeconnecte("jeton refusé par Microsoft")
        if reponse.status_code == 429:
            raise ErreurCompte("Microsoft demande de ralentir (trop de requêtes) : réessaie dans une minute.")
        if reponse.status_code >= 400:
            try:
                message = reponse.json()["error"]["message"]
            except Exception:
                message = reponse.text[:200]
            log.warning("Graph %s %s : %s %s", methode, chemin.split("?")[0][:80], reponse.status_code, message)
            raise ErreurCompte(f"Microsoft a refusé ({reponse.status_code}) : {message}")
        if not reponse.content:
            return {}
        return reponse.json()

    def _liste(self, chemin: str, params: dict, pages: int = 3, entetes: dict | None = None) -> list[dict]:
        elements, page = [], self._requete("GET", chemin, params=params, entetes=entetes)
        elements.extend(page.get("value", []))
        for _ in range(pages - 1):
            suite = page.get("@odata.nextLink")
            if not suite:
                break
            page = self._requete("GET", suite, entetes=entetes)
            elements.extend(page.get("value", []))
        return elements

    # --- Courriels ----------------------------------------------------------------------------

    @staticmethod
    def _courriel(m: dict) -> Courriel:
        de = (m.get("from") or {}).get("emailAddress") or {}
        entetes = {h.get("name", "").lower(): h.get("value", "") for h in m.get("internetMessageHeaders") or []}
        infolettre = ("list-unsubscribe" in entetes or "list-id" in entetes
                      or entetes.get("precedence", "").lower() in ("bulk", "list", "junk"))
        return Courriel(
            id=m["id"],
            de_nom=de.get("name") or "",
            de_adresse=(de.get("address") or "").lower(),
            sujet=m.get("subject") or "",
            recu=_instant_utc(m.get("receivedDateTime")),
            apercu=_net(m.get("bodyPreview") or ""),
            lu=bool(m.get("isRead")),
            a=[(r.get("emailAddress") or {}).get("address", "").lower() for r in m.get("toRecipients") or []],
            prioritaire=m.get("inferenceClassification", "focused") != "other",
            infolettre=infolettre,
        )

    def courriels_recents(self, non_lus_seulement: bool, nombre: int) -> list[Courriel]:
        params = {"$select": CHAMPS_COURRIEL, "$top": max(1, min(nombre, 25)),
                  "$orderby": "receivedDateTime desc"}
        if non_lus_seulement:
            # Graph exige que la propriété du tri apparaisse d'abord dans le filtre.
            params["$filter"] = "receivedDateTime ge 1900-01-01T00:00:00Z and isRead eq false"
        page = self._requete("GET", "/me/mailFolders/inbox/messages", params=params)
        return [self._courriel(m) for m in page.get("value", [])]

    def lire_courriel(self, id: str) -> Courriel:
        m = self._requete(
            "GET", f"/me/messages/{_q(id)}",
            params={"$select": CHAMPS_COURRIEL + ",body,uniqueBody"},
            entetes={"Prefer": 'outlook.body-content-type="text"'},
        )
        courriel = self._courriel(m)
        courriel.texte = _texte_courriel(
            courriel.sujet, (m.get("uniqueBody") or {}).get("content") or "", (m.get("body") or {}).get("content") or "")
        return courriel

    def chercher_courriels(self, recherche: str, nombre: int) -> list[Courriel]:
        recherche = recherche.replace('"', " ").strip()
        if not recherche:
            return []
        page = self._requete("GET", "/me/messages", params={
            "$search": f'"{recherche}"', "$top": max(1, min(nombre, 25)), "$select": CHAMPS_COURRIEL,
        })
        return [self._courriel(m) for m in page.get("value", [])]

    def courriels_depuis(self, depuis: datetime) -> list[Courriel]:
        borne = depuis.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        messages = self._liste("/me/mailFolders/inbox/messages", {
            "$filter": f"receivedDateTime gt {borne}",
            "$orderby": "receivedDateTime desc",
            "$top": 50,
            "$select": CHAMPS_COURRIEL + ",internetMessageHeaders",
        }, pages=2)
        return [self._courriel(m) for m in messages]

    def envoyer_courriel(self, a: list[str], sujet: str, texte: str, en_reponse_a: str | None) -> None:
        destinataires = [{"emailAddress": {"address": adresse}} for adresse in a]
        corps = {"contentType": "Text", "content": texte}
        if not en_reponse_a:
            self._requete("POST", "/me/sendMail", json={
                "message": {"subject": sujet, "body": corps, "toRecipients": destinataires},
                "saveToSentItems": True,
            })
            return
        # Une réponse : le brouillon créé par Graph garde le fil de la conversation, mais ses
        # destinataires sont remplacés par ceux qui ont été approuvés (jamais l'expéditeur d'origine
        # d'office, ni de copie).
        brouillon = self._requete("POST", f"/me/messages/{_q(en_reponse_a)}/createReply", json={})
        chemin = f"/me/messages/{_q(brouillon['id'])}"
        try:
            modifs = {"toRecipients": destinataires, "ccRecipients": [], "bccRecipients": [], "body": corps}
            if sujet:
                modifs["subject"] = sujet
            self._requete("PATCH", chemin, json=modifs)
            self._requete("POST", f"{chemin}/send")
        except ErreurCompte:
            try:
                self._requete("DELETE", chemin)
            except ErreurCompte:
                pass
            raise

    # --- Agenda -------------------------------------------------------------------------------

    def _tous_les_agendas(self) -> list[dict]:
        if self._agendas is None or time.monotonic() - self._agendas_quand > DUREE_CACHE_AGENDAS:
            self._agendas = self._liste("/me/calendars", {"$select": "id,name,canEdit,owner,isDefaultCalendar"})
            self._agendas_quand = time.monotonic()
        return self._agendas

    def _agendas_de_toi(self) -> list[dict]:
        if not reglages.PROPRIETAIRE:
            return []
        return [
            a for a in self._tous_les_agendas()
            if not a.get("isDefaultCalendar")
            and ((a.get("owner") or {}).get("address") or "").lower() == reglages.PROPRIETAIRE
        ]

    @staticmethod
    def _evenement(d: dict, agenda: str = "joi") -> Evenement:
        journee = bool(d.get("isAllDay"))
        return Evenement(
            id=d["id"],
            titre=d.get("subject") or "",
            debut=_instant_evenement(d["start"], journee),
            fin=_instant_evenement(d["end"], journee),
            journee_entiere=journee,
            lieu=((d.get("location") or {}).get("displayName") or "").strip(),
            note=_net(d.get("bodyPreview") or ""),
            agenda=agenda,
            invites=[((p.get("emailAddress") or {}).get("address") or "").lower() for p in d.get("attendees") or []],
            rappel_minutes=d.get("reminderMinutesBeforeStart") if d.get("isReminderOn") else None,
            annule=bool(d.get("isCancelled")),
        )

    def _vue(self, chemin: str, debut: datetime, fin: datetime, agenda: str) -> list[Evenement]:
        evenements = self._liste(chemin, {
            "startDateTime": debut.astimezone(UTC).isoformat(),
            "endDateTime": fin.astimezone(UTC).isoformat(),
            "$select": CHAMPS_EVENEMENT,
            "$orderby": "start/dateTime",
            "$top": 100,
        })
        return [self._evenement(d, agenda) for d in evenements]

    def agenda(self, debut: datetime, fin: datetime, qui: str) -> tuple[list[Evenement], list[str]]:
        evenements, remarques = [], []
        if qui in ("joi", "tous"):
            evenements += self._vue("/me/calendar/calendarView", debut, fin, "joi")
        if qui in ("toi", "tous"):
            agendas = self._agendas_de_toi()
            if not agendas:
                remarques.append(
                    "L'agenda de la personne n'est pas visible : il n'est pas encore partagé avec Joi, ou "
                    "l'invitation de partage n'a pas été acceptée, ou JOI_COURRIEL_PROPRIETAIRE est vide."
                )
            for a in agendas:
                evenements += self._vue(f"/me/calendars/{_q(a['id'])}/calendarView", debut, fin, "toi")
        evenements.sort(key=lambda e: e.debut)
        return evenements, remarques

    def _evenement_de_joi(self, id: str) -> dict:
        """L'événement, s'il est dans l'agenda de Joi (sinon, une erreur : on ne touche pas au tien)."""
        try:
            return self._requete("GET", f"/me/calendar/events/{_q(id)}", params={"$select": CHAMPS_EVENEMENT})
        except CompteDeconnecte:
            raise
        except ErreurCompte:
            raise ErreurCompte("Cet événement n'est pas dans l'agenda de Joi : elle ne peut pas le modifier.")

    def creer_evenement(self, titre: str, debut: datetime, fin: datetime, lieu: str, note: str,
                        rappel_minutes: int | None, invites: list[str]) -> Evenement:
        corps = {
            "subject": titre,
            "start": _vers_graph(debut),
            "end": _vers_graph(fin),
            "isReminderOn": rappel_minutes is not None,
            "reminderMinutesBeforeStart": rappel_minutes if rappel_minutes is not None else 0,
            "attendees": [{"emailAddress": {"address": a}, "type": "required"} for a in invites],
        }
        if lieu:
            corps["location"] = {"displayName": lieu}
        if note:
            corps["body"] = {"contentType": "Text", "content": note}
        return self._evenement(self._requete("POST", "/me/calendar/events", json=corps))

    def modifier_evenement(self, id: str, titre: str | None, debut: datetime | None, fin: datetime | None,
                           lieu: str | None, note: str | None) -> Evenement:
        actuel = self._evenement(self._evenement_de_joi(id))
        modifs: dict = {}
        if titre:
            modifs["subject"] = titre
        if debut and not fin:
            fin = debut + (actuel.fin - actuel.debut)  # même durée
        if debut:
            modifs["start"] = _vers_graph(debut)
        if fin:
            modifs["end"] = _vers_graph(fin)
        if lieu is not None:
            modifs["location"] = {"displayName": lieu}
        if note is not None:
            modifs["body"] = {"contentType": "Text", "content": note}
        if not modifs:
            return actuel
        return self._evenement(self._requete("PATCH", f"/me/events/{_q(id)}", json=modifs))

    def annuler_evenement(self, id: str) -> Evenement:
        actuel = self._evenement(self._evenement_de_joi(id))
        if actuel.invites:
            # Les invités reçoivent l'annulation.
            self._requete("POST", f"/me/events/{_q(id)}/cancel", json={"comment": "Annulé par Joi."})
        else:
            self._requete("DELETE", f"/me/events/{_q(id)}")
        actuel.annule = True
        return actuel

    # --- Tâches (Microsoft To Do) -------------------------------------------------------------

    def _liste_de_joi(self) -> str:
        if not self._liste_taches:
            for liste in self._liste("/me/todo/lists", {}):
                if liste.get("displayName") == LISTE_TACHES:
                    self._liste_taches = liste["id"]
                    break
            else:
                self._liste_taches = self._requete("POST", "/me/todo/lists",
                                                   json={"displayName": LISTE_TACHES})["id"]
        return self._liste_taches

    @staticmethod
    def _tache(d: dict) -> Tache:
        return Tache(
            id=d["id"],
            titre=d.get("title") or "",
            note=_net((d.get("body") or {}).get("content") or ""),
            terminee=d.get("status") == "completed",
            creee=_instant_utc(d.get("createdDateTime")),
        )

    def taches(self) -> list[Tache]:
        taches = self._liste(f"/me/todo/lists/{_q(self._liste_de_joi())}/tasks",
                             {"$filter": "status ne 'completed'", "$top": 100})
        return sorted((self._tache(d) for d in taches), key=lambda t: t.creee or datetime.min.replace(tzinfo=UTC))

    def ajouter_tache(self, titre: str, note: str) -> Tache:
        corps: dict = {"title": titre}
        if note:
            corps["body"] = {"content": note, "contentType": "text"}
        return self._tache(self._requete("POST", f"/me/todo/lists/{_q(self._liste_de_joi())}/tasks", json=corps))

    def terminer_tache(self, id: str) -> Tache:
        return self._tache(self._requete(
            "PATCH", f"/me/todo/lists/{_q(self._liste_de_joi())}/tasks/{_q(id)}", json={"status": "completed"},
        ))
