"""Les outils du compte de Joi, servis en MCP (stdio) : python -m joi.compte

La session Copilot de Joi lance ce serveur (voir integration.py). Il peut aussi servir à un autre
client MCP (Claude Desktop, VS Code) pour essayer les outils sans passer par la voix.

Les identifiants des services (très longs) sont remplacés par des numéros courts et stables, dérivés de
l'identifiant : c3f2a1 (courriel), e… (événement), t… (tâche). Le contexte du réveil donne les mêmes.

Tout texte écrit par quelqu'un d'autre (courriel, aperçu) est entouré des balises de contenu externe.
L'envoi d'un courriel est gardé par la confirmation (voir brouillons.py), vérifiée par le serveur de
Joi au moment où Copilot demande la permission d'appeler envoyer_courriel.
"""

import functools
import logging
import threading
from datetime import timedelta
from typing import Annotated, Callable

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import SERVEUR, brouillons, journal_compte, reglages
from .fournisseur import ErreurCompte, Fournisseur, choisir
from .modele import (date_complete, externe, ligne_courriel, ligne_evenement, ligne_tache, lire_instant,
                     maintenant, numero_court, quand)

log = logging.getLogger("joi.compte.mcp")

DUREE_PAR_DEFAUT = timedelta(minutes=30)  # un événement sans heure de fin


class Registre:
    """Les numéros courts (c3f2a1, e…, t…) et les identifiants du service qu'ils désignent.

    Un numéro est dérivé de l'identifiant (modele.numero_court) : le contexte du réveil, écrit par le
    serveur de Joi, donne les mêmes. Un numéro jamais vu par ce processus est cherché à nouveau
    (courriels récents, agenda des jours proches, tâches) avant d'être déclaré inconnu."""

    GENRES = {"c": "courriel", "e": "événement", "t": "tâche"}

    def __init__(self, recharger: dict[str, Callable[[], None]] | None = None):
        self._verrou = threading.Lock()
        self._numeros: dict[str, tuple[str, str]] = {}  # numéro -> (identifiant, détail)
        self._recharger = recharger or {}

    def numero(self, prefixe: str, identifiant: str, detail: str = "") -> str:
        with self._verrou:
            longueur = 5
            numero = numero_court(prefixe, identifiant, longueur)
            # Deux identifiants qui donneraient le même numéro : le second en prend un plus long.
            while numero in self._numeros and self._numeros[numero][0] != identifiant:
                longueur += 1
                numero = numero_court(prefixe, identifiant, longueur)
            self._numeros[numero] = (identifiant, detail)
            return numero

    def resoudre(self, numero: str, prefixe: str) -> tuple[str, str]:
        numero = numero.strip().lower().strip("[]")
        if numero.startswith(prefixe) and numero not in self._numeros and prefixe in self._recharger:
            self._recharger[prefixe]()
        if not numero.startswith(prefixe) or numero not in self._numeros:
            raise ErreurCompte(f"Numéro de {self.GENRES[prefixe]} inconnu ({numero}) : refais la liste "
                               "pour avoir les numéros.")
        return self._numeros[numero]


def creer_serveur(fournisseur: Fournisseur | None = None) -> MCPServer:
    compte = fournisseur or choisir()

    def recharger_courriels() -> None:
        for c in compte.courriels_recents(False, 25):
            registre.numero("c", c.id)

    def recharger_evenements() -> None:
        aujourdhui = maintenant().replace(hour=0, minute=0, second=0, microsecond=0)
        evenements, _ = compte.agenda(aujourdhui - timedelta(days=1), aujourdhui + timedelta(days=15), "tous")
        for e in evenements:
            registre.numero("e", e.id, e.agenda)

    def recharger_taches() -> None:
        for t in compte.taches():
            registre.numero("t", t.id)

    registre = Registre({"c": recharger_courriels, "e": recharger_evenements, "t": recharger_taches})
    serveur = MCPServer(
        SERVEUR,
        instructions="Le compte de Joi : ses courriels, son agenda et celui de la personne, ses tâches.",
    )
    lecture = ToolAnnotations(read_only_hint=True)
    ecriture = ToolAnnotations(read_only_hint=False, destructive_hint=False)

    def outil(annotations: ToolAnnotations):
        """Enregistre un outil : les erreurs du service deviennent une réponse lisible par le modèle."""
        def decorer(fonction):
            @functools.wraps(fonction)
            def appel(*args, **kwargs):
                log.info("Outil %s", fonction.__name__)
                try:
                    return fonction(*args, **kwargs)
                except (ErreurCompte, ValueError) as e:
                    log.warning("Outil %s : %s", fonction.__name__, e)
                    return f"Erreur : {e}"
                except Exception as e:  # le serveur MCP doit survivre à tout
                    log.exception("Outil %s", fonction.__name__)
                    return f"Erreur inattendue : {type(e).__name__}"
            serveur.tool(annotations=annotations)(appel)
            return fonction
        return decorer

    def liste_courriels(courriels, vide: str) -> str:
        if not courriels:
            return vide
        lignes = [ligne_courriel(c, registre.numero("c", c.id)) for c in courriels]
        return externe("\n".join(lignes))

    # --- Courriels ----------------------------------------------------------------------------

    @outil(lecture)
    def courriels_recents(
        non_lus_seulement: Annotated[bool, Field(description="Seulement les courriels non lus.")] = False,
        nombre: Annotated[int, Field(description="Combien de courriels (au plus 25).", ge=1, le=25)] = 10,
    ) -> str:
        """Les derniers courriels reçus dans ta boîte (à toi, Joi) : expéditeur, sujet, date, aperçu.
        Chaque courriel a un numéro (ex. c3f2a1) pour lire_courriel ou y répondre."""
        courriels = compte.courriels_recents(non_lus_seulement, nombre)
        return liste_courriels(courriels, "Aucun courriel non lu." if non_lus_seulement else "Ta boîte est vide.")

    @outil(lecture)
    def lire_courriel(
        id: Annotated[str, Field(description="Le numéro du courriel, ex. c3f2a1.")],
    ) -> str:
        """Le texte complet d'un courriel (sans l'historique cité)."""
        identifiant, _ = registre.resoudre(id, "c")
        c = compte.lire_courriel(identifiant)
        return externe(f"{ligne_courriel(c, id.strip().lower())}\n\n{c.texte or '(courriel vide)'}")

    @outil(lecture)
    def chercher_courriels(
        recherche: Annotated[str, Field(description="Mots à chercher (expéditeur, sujet, contenu).")],
    ) -> str:
        """Cherche dans tous tes courriels, même anciens."""
        return liste_courriels(compte.chercher_courriels(recherche, 10), "Aucun courriel trouvé.")

    @outil(lecture)
    def preparer_courriel(
        a: Annotated[str, Field(description="Adresse(s) du destinataire, séparées par des virgules ; "
                                            "« toi » pour la personne. Seules les adresses approuvées passent.")],
        sujet: Annotated[str, Field(description="Le sujet.")],
        texte: Annotated[str, Field(description="Le texte complet du courriel, signé Joi.")],
        en_reponse_a: Annotated[str, Field(description="Numéro du courriel auquel tu réponds (ex. c3f2a1), "
                                                       "ou vide.")] = "",
    ) -> str:
        """Prépare un courriel SANS l'envoyer et renvoie son numéro de brouillon. Ensuite, dis à la
        personne à qui tu écris et l'essentiel, demande « je l'envoie ? », et n'appelle envoyer_courriel
        qu'après sa réponse."""
        destinataires = brouillons.adresses(a)
        reponse = registre.resoudre(en_reponse_a, "c")[0] if en_reponse_a.strip() else None
        b = brouillons.preparer(destinataires, sujet, texte, reponse)
        return (
            f"Brouillon {b.numero} prêt, PAS encore envoyé. À : {', '.join(b.a)}. Sujet : « {b.sujet} ». "
            f"Texte : {b.texte}\n"
            "Dis maintenant à la personne à qui tu écris et l'essentiel du message, puis demande-lui si tu "
            f"l'envoies. Appelle envoyer_courriel({b.numero}) seulement après sa réponse."
        )

    @outil(ecriture)
    def envoyer_courriel(
        brouillon: Annotated[int, Field(description="Le numéro du brouillon préparé.")],
    ) -> str:
        """Envoie un brouillon préparé, après que la personne a dit oui. Le texte ne peut plus changer."""
        b = brouillons.obtenir(brouillon)
        if b is None:
            raise ErreurCompte("Brouillon inconnu : prépare le courriel avec preparer_courriel.")
        if b.expire:
            brouillons.retirer(b.numero)
            raise ErreurCompte("Ce brouillon a plus de 15 minutes : prépare-le à nouveau et redemande.")
        brouillons.verifier_adresses(b.a)
        compte.envoyer_courriel(b.a, b.sujet, b.texte, b.en_reponse_a)
        brouillons.retirer(b.numero)
        journal_compte.courriel_envoye(compte.adresse() or reglages.COURRIEL, b.a, b.sujet,
                                       b.texte[:255])
        return f"Courriel envoyé à {', '.join(b.a)}."

    # --- Agenda -------------------------------------------------------------------------------

    def maintenant_dit() -> str:
        return f"Maintenant : {date_complete()} (heure de {reglages.NOM_FUSEAU})."

    @outil(lecture)
    def agenda(
        debut: Annotated[str, Field(description="Début, en heure locale : AAAA-MM-JJ ou AAAA-MM-JJTHH:MM.")],
        fin: Annotated[str, Field(description="Fin (exclue), même format ; vide = la fin du jour de début.")] = "",
        qui: Annotated[str, Field(description="« joi » (ton agenda), « toi » (celui de la personne, en "
                                              "lecture seule) ou « tous ».")] = "tous",
    ) -> str:
        """Les événements de ton agenda, de celui de la personne, ou des deux, sur une période."""
        qui = qui.strip().lower()
        if qui not in ("joi", "toi", "tous"):
            raise ErreurCompte("qui doit être « joi », « toi » ou « tous ».")
        t0 = lire_instant(debut)
        t1 = lire_instant(fin, fin_de_journee=True) if fin.strip() else lire_instant(debut[:10], fin_de_journee=True)
        if t1 <= t0:
            raise ErreurCompte("La fin doit être après le début.")
        evenements, remarques = compte.agenda(t0, t1, qui)
        lignes = [maintenant_dit()]
        for e in evenements:
            numero = registre.numero("e", e.id, e.agenda)
            proprietaire = "ton agenda" if e.agenda == "joi" else "agenda de la personne"
            lignes.append(f"- ({proprietaire}) {ligne_evenement(e, numero)}")
        if not evenements:
            lignes.append("Aucun événement sur cette période.")
        lignes += remarques
        texte = "\n".join(lignes)
        # Les titres et lieux d'une invitation sont écrits par d'autres.
        return externe(texte) if evenements else texte

    @outil(ecriture)
    def creer_evenement(
        titre: Annotated[str, Field(description="Le titre (ce que la personne verra dans l'invitation).")],
        debut: Annotated[str, Field(description="Début en heure locale : AAAA-MM-JJTHH:MM.")],
        fin: Annotated[str, Field(description="Fin, même format ; vide = 30 minutes après le début.")] = "",
        lieu: Annotated[str, Field(description="Le lieu, ou vide.")] = "",
        note: Annotated[str, Field(description="Une note, ou vide.")] = "",
        rappel_minutes: Annotated[int, Field(description="Rappel, en minutes avant le début.", ge=0)] = 15,
        inviter_toi: Annotated[bool, Field(description="Inviter la personne : son agenda le lui rappellera "
                                                       "une fois l'invitation acceptée.")] = True,
    ) -> str:
        """Crée un événement dans TON agenda (Joi). Pour un rappel, garde inviter_toi : la personne
        reçoit une invitation, et son téléphone le lui rappelle quand elle l'a acceptée."""
        t0 = lire_instant(debut)
        t1 = lire_instant(fin) if fin.strip() else t0 + DUREE_PAR_DEFAUT
        if t1 <= t0:
            raise ErreurCompte("La fin doit être après le début.")
        invites, remarque = [], ""
        if inviter_toi:
            if reglages.PROPRIETAIRE:
                invites = [reglages.PROPRIETAIRE]
            else:
                remarque = " Aucune invitation envoyée : JOI_COURRIEL_PROPRIETAIRE est vide dans .env."
        if invites:
            brouillons.verifier_adresses(invites)
        e = compte.creer_evenement(titre.strip(), t0, t1, lieu.strip(), note.strip(), rappel_minutes, invites)
        journal_compte.evenement("cree", e)
        numero = registre.numero("e", e.id, "joi")
        invitation = f" Invitation envoyée à {', '.join(e.invites)} : elle doit l'accepter." if e.invites else ""
        if t0 < maintenant():
            remarque += " Attention : ce moment est déjà passé."
        return f"{maintenant_dit()}\nCréé dans ton agenda : {ligne_evenement(e, numero)}.{invitation}{remarque}"

    def evenement_de_joi(id: str) -> str:
        identifiant, agenda_ = registre.resoudre(id, "e")
        if agenda_ != "joi":
            raise ErreurCompte("Cet événement est dans l'agenda de la personne : tu ne peux que le lire.")
        return identifiant

    @outil(ecriture)
    def modifier_evenement(
        id: Annotated[str, Field(description="Le numéro de l'événement, ex. e8b41c.")],
        titre: Annotated[str, Field(description="Nouveau titre, ou vide pour le garder.")] = "",
        debut: Annotated[str, Field(description="Nouveau début (AAAA-MM-JJTHH:MM), ou vide. "
                                                "Sans nouvelle fin, la durée est gardée.")] = "",
        fin: Annotated[str, Field(description="Nouvelle fin, ou vide.")] = "",
        lieu: Annotated[str, Field(description="Nouveau lieu, ou vide pour le garder.")] = "",
        note: Annotated[str, Field(description="Nouvelle note, ou vide pour la garder.")] = "",
    ) -> str:
        """Modifie un événement de TON agenda (jamais celui de la personne). Les invités sont prévenus."""
        identifiant = evenement_de_joi(id)
        e = compte.modifier_evenement(
            identifiant, titre.strip() or None,
            lire_instant(debut) if debut.strip() else None,
            lire_instant(fin) if fin.strip() else None,
            lieu.strip() or None, note.strip() or None,
        )
        journal_compte.evenement("modifie", e)
        return f"Modifié : {ligne_evenement(e, id.strip().lower())}."

    @outil(ecriture)
    def annuler_evenement(
        id: Annotated[str, Field(description="Le numéro de l'événement, ex. e8b41c.")],
    ) -> str:
        """Annule un événement de TON agenda. Les invités reçoivent l'annulation."""
        e = compte.annuler_evenement(evenement_de_joi(id))
        journal_compte.evenement("annule", e)
        return f"Annulé : « {e.titre} », {quand(e.debut)}."

    # --- Tâches -------------------------------------------------------------------------------

    @outil(lecture)
    def taches() -> str:
        """Ta liste de choses à faire (sans heure), ouvertes."""
        liste = compte.taches()
        if not liste:
            return "Ta liste de tâches est vide."
        return "\n".join(f"- {ligne_tache(t, registre.numero('t', t.id))}" for t in liste)

    @outil(ecriture)
    def ajouter_tache(
        titre: Annotated[str, Field(description="La chose à faire, en quelques mots.")],
        note: Annotated[str, Field(description="Un détail, ou vide.")] = "",
    ) -> str:
        """Ajoute une chose à faire, sans heure, à ta liste (pour une heure précise : creer_evenement)."""
        t = compte.ajouter_tache(titre.strip(), note.strip())
        journal_compte.tache("ajoutee", t)
        return f"Ajoutée : {ligne_tache(t, registre.numero('t', t.id))}."

    @outil(ecriture)
    def terminer_tache(
        id: Annotated[str, Field(description="Le numéro de la tâche, ex. t07d2e.")],
    ) -> str:
        """Coche une tâche de ta liste comme faite."""
        t = compte.terminer_tache(registre.resoudre(id, "t")[0])
        journal_compte.tache("terminee", t)
        return f"Terminée : {t.titre}."

    return serveur
