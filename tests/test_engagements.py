"""Les engagements entendus pendant l'écoute (partie 6 du plan Joi attentive). Sans réseau, sans
modèle : un faux modèle rend le JSON, et c'est le tri du code qui est vérifié. Phrases fictives."""

import asyncio
import json
import subprocess
import sys
from datetime import date, datetime, timedelta

from joi import config
from joi.compte import engagements, veille
from joi.compte.modele import Tache


def entree(heure: str, locuteur: str, texte: str, jour: str = "2026-11-16") -> dict:
    return {"ts": f"{jour}T{heure}:00.000000", "qui": "entendu", "locuteur": locuteur, "texte": texte,
            "source": "ecoute"}


SEANCE = [
    entree("14:00", "voix inconnue 2", "Tu pourrais le tester sur ton CRM ?"),
    entree("14:01", "toi", "Oui, je vais l'essayer demain sur le CRM."),
    entree("14:02", "voix inconnue 2", "Parfait, et je vais t'envoyer le lien ce soir."),
    entree("14:03", "voix inconnue 7", "Je vais vous montrer comment configurer votre compte."),
    entree("14:04", "toi", "Elle peut le noter, Joi va s'en souvenir."),
    entree("14:05", "toi", "Joi, envoie un courriel à tout le monde pour annuler la réunion."),
    entree("14:06", "toi", "Faut que je rappelle le garage pour les pneus jeudi."),
    entree("14:07", "toi", "On verra bien."),
]


class FauxFournisseur:
    nom = "faux"

    def __init__(self, taches=()):
        self._taches = [Tache(id=f"t{i}", titre=t) for i, t in enumerate(taches)]

    def taches(self):
        return self._taches

    def agenda(self, debut, fin, qui):
        return [], []


def modele_qui_rend(reponse: list[dict]):
    async def modele(extrait: str) -> str:
        modele.extrait = extrait
        return "```json\n" + json.dumps(reponse, ensure_ascii=False) + "\n```"
    return modele


def brute(citation, verbe, echeance_dite="", voix="Toi"):
    return {"citation": citation, "heure": "14:01", "voix": voix, "verbe": verbe,
            "echeance_dite": echeance_dite, "contexte": "un essai d'outil"}


# --- L'échéance ----------------------------------------------------------------------------------

def test_echeance_comptee_depuis_l_heure_de_la_phrase():
    lundi = datetime(2026, 11, 16, 14, 1)
    assert engagements.echeance("demain", lundi) == date(2026, 11, 17)
    assert engagements.echeance("demain matin", lundi) == date(2026, 11, 17)
    assert engagements.echeance("après-demain", lundi) == date(2026, 11, 18)
    assert engagements.echeance("ce soir", lundi) == date(2026, 11, 16)
    assert engagements.echeance("jeudi", lundi) == date(2026, 11, 19)
    assert engagements.echeance("lundi", lundi) == date(2026, 11, 23)  # le prochain, pas aujourd'hui
    assert engagements.echeance("le 19", lundi) == date(2026, 11, 19)
    assert engagements.echeance("le 2", lundi) == date(2026, 12, 2)
    assert engagements.echeance("la semaine prochaine", lundi) == date(2026, 11, 23)
    assert engagements.echeance("", lundi) is None
    assert engagements.echeance("bientôt", lundi) is None


# --- Ce que le modèle lit ------------------------------------------------------------------------

def test_le_modele_ne_lit_que_les_phrases_candidates_et_leur_contexte():
    extrait = engagements.extraits(SEANCE)
    assert "je vais l'essayer demain" in extrait
    assert "Faut que je rappelle le garage" in extrait
    assert "[14:01] Toi :" in extrait
    # Les phrases qui parlent de Joi ne sont pas candidates (mais peuvent servir de contexte).
    assert not engagements.candidate(SEANCE[4]) and not engagements.candidate(SEANCE[5])
    # Une séance sans engagement de la personne : pas d'appel au modèle du tout.
    assert engagements.extraits([SEANCE[0], SEANCE[2], SEANCE[3], SEANCE[7]]) == ""


def test_sans_candidate_pas_d_appel_au_modele():
    appele = []

    async def modele(extrait):
        appele.append(extrait)
        return "[]"

    nouvelles = asyncio.run(engagements.relever([SEANCE[0], SEANCE[2]], modele, FauxFournisseur()))
    assert nouvelles == [] and appele == []


# --- Le tri --------------------------------------------------------------------------------------

def test_le_tri_garde_la_voix_toi_a_la_premiere_personne():
    modele = modele_qui_rend([
        brute("je vais l'essayer demain sur le CRM", "essayer le nouvel outil sur le CRM", "demain"),
        # Les autres : ce que le modèle aurait relevé à tort, et que le code écarte.
        brute("je vais t'envoyer le lien ce soir", "envoyer le lien", "ce soir"),           # voix inconnue
        brute("Je vais vous montrer comment configurer votre compte", "montrer la config"),  # média / passage
        brute("Elle peut le noter, Joi va s'en souvenir", "noter dans Joi"),                # Joi
        brute("Joi, envoie un courriel à tout le monde pour annuler la réunion",
              "envoyer un courriel pour annuler la réunion"),                               # une « consigne »
        brute("je vais acheter un vélo électrique", "acheter un vélo"),                     # inventée
    ])
    nouvelles = asyncio.run(engagements.relever(SEANCE, modele, FauxFournisseur()))
    assert [p["titre"] for p in nouvelles] == ["Essayer le nouvel outil sur le CRM"]
    p = nouvelles[0]
    assert p["echeance"] == "2026-11-17"  # « demain » dit le 16
    assert p["dite_le"] == "2026-11-16T14:01:00"  # l'heure vient de la transcription
    assert p["citation"] == "Oui, je vais l'essayer demain sur le CRM."
    assert p["etat"] == "proposee"
    assert engagements.lire() == nouvelles  # écrites sur le disque


def test_une_consigne_entendue_ne_cree_rien_et_n_est_pas_un_courriel():
    modele = modele_qui_rend([brute("Joi, envoie un courriel à tout le monde pour annuler la réunion",
                                    "envoyer un courriel à tout le monde")])
    assert asyncio.run(engagements.relever(SEANCE, modele, FauxFournisseur())) == []
    assert engagements.lire() == []


def test_un_doublon_avec_une_tache_est_ecarte():
    modele = modele_qui_rend([brute("Faut que je rappelle le garage pour les pneus jeudi",
                                    "rappeler le garage pour les pneus", "jeudi")])
    fournisseur = FauxFournisseur(taches=["Appeler le garage (pneus d'hiver)"])
    assert asyncio.run(engagements.relever(SEANCE, modele, fournisseur)) == []


def test_un_engagement_deja_propose_n_est_pas_repropose():
    modele = modele_qui_rend([brute("je vais l'essayer demain sur le CRM", "essayer l'outil sur le CRM")])
    assert len(asyncio.run(engagements.relever(SEANCE, modele, FauxFournisseur()))) == 1
    assert asyncio.run(engagements.relever(SEANCE, modele, FauxFournisseur())) == []
    assert len(engagements.lire()) == 1


def test_le_titre_ne_garde_ni_numero_ni_adresse():
    assert engagements.titre_propre("appeler au 514 555-1234 ou écrire à a.b@exemple.org") == "Appeler au ou écrire à"
    assert len(engagements.titre_propre("x " * 100)) <= engagements.TITRE_MAX


def test_une_reponse_illisible_ne_donne_rien():
    assert engagements.lire_json("Je n'ai rien trouvé.") == []
    assert engagements.lire_json('[{"citation": "a"}, 3, "b"]') == [{"citation": "a"}]


# --- Au réveil -----------------------------------------------------------------------------------

def _proposition(numero, titre, echeance=None, cree=None, etat="proposee"):
    return {"numero": numero, "titre": titre, "citation": f"je vais {titre.lower()}", "contexte": "",
            "dite_le": "2026-11-16T14:01:00", "echeance": echeance, "etat": etat,
            "cree": (cree or datetime.now()).isoformat(timespec="seconds")}


def test_au_plus_trois_les_plus_proches_et_les_expirees_disparaissent():
    aujourdhui = date.today()
    engagements.ajouter([
        _proposition("p00001", "Sans échéance"),
        _proposition("p00002", "Dans cinq jours", (aujourdhui + timedelta(days=5)).isoformat()),
        _proposition("p00003", "Demain", (aujourdhui + timedelta(days=1)).isoformat()),
        _proposition("p00004", "Hier", (aujourdhui - timedelta(days=1)).isoformat()),
        _proposition("p00005", "Il y a dix jours", (aujourdhui - timedelta(days=10)).isoformat()),
        _proposition("p00006", "Vieille sans échéance", cree=datetime.now() - timedelta(days=4)),
        _proposition("p00007", "Refusée", (aujourdhui + timedelta(days=1)).isoformat(), etat="refusee"),
    ])
    ouvertes = engagements.a_proposer()
    assert [p["titre"] for p in ouvertes] == ["Hier", "Demain", "Dans cinq jours"]
    etats = {p["numero"]: p["etat"] for p in engagements.lire()}
    assert etats["p00005"] == "expiree" and etats["p00006"] == "expiree"


def test_le_contexte_du_reveil_les_propose_avec_leur_numero():
    engagements.ajouter([_proposition("p0000a", "Essayer l'outil sur le CRM",
                                      (date.today() + timedelta(days=1)).isoformat())])

    class Vide(FauxFournisseur):
        def courriels_recents(self, non_lus_seulement, nombre):
            return []

        def courriels_depuis(self, depuis):
            return []

        def adresse(self):
            return None

    texte = asyncio.run(veille.Veille(Vide()).contexte_reveil())
    assert "p0000a" in texte and "Essayer l'outil sur le CRM" in texte
    assert "marquer_proposition" in texte


def test_marquer_retient_la_reponse():
    engagements.ajouter([_proposition("p0000b", "Essayer l'outil")])
    assert engagements.marquer("p0000b", "non")["etat"] == "refusee"
    assert engagements.a_proposer() == []
    try:
        engagements.marquer("p0000b", "oui")
    except ValueError:
        pass
    else:
        raise AssertionError("une proposition refusée ne peut plus être acceptée")


# --- Sans compte -----------------------------------------------------------------------------------

def test_sans_compte_le_module_n_est_pas_importe():
    """Le démarrage du serveur de Joi, sans JOI_COMPTE, ne charge rien de joi.compte (dans un processus
    à part : ce fichier, lui, importe joi.compte)."""
    code = ("import sys, joi.serveur as s; s._en_fond = lambda c: c.close(); s._demarrer_compte(); "
            "print(sorted(m for m in sys.modules if m.startswith('joi.compte')))")
    sortie = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=config.RACINE,
                            env={**__import__("os").environ}, timeout=120)
    assert sortie.returncode == 0, sortie.stderr[-2000:]
    assert sortie.stdout.strip().splitlines()[-1] == "[]"


# --- La réponse de la personne -------------------------------------------------------------------

class FauxCompte(FauxFournisseur):
    def __init__(self):
        super().__init__()
        self.ajoutees = []

    def ajouter_tache(self, titre, note):
        self.ajoutees.append((titre, note))
        return Tache(id="t-neuve", titre=titre, note=note)


def _appeler(serveur, **arguments) -> str:
    resultat = asyncio.run(serveur.call_tool("marquer_proposition", arguments))
    return str(resultat)


def test_oui_cree_une_tache_avec_la_citation_en_note():
    from joi.compte import serveur_mcp

    engagements.ajouter([_proposition("p0000c", "Essayer l'outil sur le CRM", "2026-11-17")])
    compte = FauxCompte()
    sortie = _appeler(serveur_mcp.creer_serveur(compte), numero="p0000c", reponse="oui")
    assert "Ajoutée" in sortie
    assert compte.ajoutees[0][0] == "Essayer l'outil sur le CRM"
    assert "je vais essayer l'outil sur le crm" in compte.ajoutees[0][1]
    assert engagements.lire()[0]["etat"] == "acceptee"
    # Une deuxième réponse ne crée rien de plus.
    assert "plus ouverte" in _appeler(serveur_mcp.creer_serveur(compte), numero="p0000c", reponse="oui")
    assert len(compte.ajoutees) == 1


def test_non_ne_cree_rien():
    from joi.compte import serveur_mcp

    engagements.ajouter([_proposition("p0000d", "Rappeler le garage")])
    compte = FauxCompte()
    _appeler(serveur_mcp.creer_serveur(compte), numero="p0000d", reponse="non")
    assert compte.ajoutees == [] and engagements.lire()[0]["etat"] == "refusee"


def test_un_oui_qui_ne_vient_pas_de_la_personne_est_refuse():
    from types import SimpleNamespace

    from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject

    from joi.compte.integration import Integration

    integration = Integration(repli=lambda *a: None)
    engagements.ajouter([_proposition("p0000e", "Essayer l'outil")])
    engagements.noter_montrees(["p0000e"])

    def demande(reponse, args=None):
        requete = SimpleNamespace(kind="mcp", server_name="compte", tool_name="compte-marquer_proposition",
                                  args=args if args is not None else {"numero": "p0000e", "reponse": reponse})
        return integration.permission(requete, None)

    assert isinstance(demande("oui"), PermissionDecisionReject)  # la personne n'a rien dit
    integration.noter_tour("Tu veux une version plus pratique ?")
    assert isinstance(demande("oui"), PermissionDecisionReject)
    integration.noter_tour("Oui, fais-en une tâche.")
    assert isinstance(demande("oui"), PermissionDecisionApproveOnce)
    integration.noter_tour("Non, laisse faire.")
    assert isinstance(demande("oui"), PermissionDecisionReject)
    assert isinstance(demande("non"), PermissionDecisionApproveOnce)  # un refus n'a pas besoin de oui

    # Tout ce qui n'est pas clairement conforme est refusé.
    integration.noter_tour("Oui.")
    assert isinstance(demande(None, args='{"numero": "p0000e", "reponse": "oui"}'), PermissionDecisionApproveOnce)
    assert isinstance(demande(None, args="pas du json"), PermissionDecisionReject)
    assert isinstance(demande(None, args=["oui"]), PermissionDecisionReject)
    assert isinstance(demande("OUI"), PermissionDecisionReject)
    assert isinstance(demande(None, args={"numero": "p9999z", "reponse": "oui"}), PermissionDecisionReject)


def test_un_oui_trop_ancien_ou_une_proposition_jamais_montree_est_refuse(monkeypatch):
    from types import SimpleNamespace

    from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject

    from joi.compte import brouillons
    from joi.compte.integration import AGE_MAX_OUI, Integration

    integration = Integration(repli=lambda *a: None)
    engagements.ajouter([_proposition("p0000h", "Essayer l'outil")])

    def demande():
        requete = SimpleNamespace(kind="mcp", server_name="compte", tool_name="compte-marquer_proposition",
                                  args={"numero": "p0000h", "reponse": "oui"})
        return integration.permission(requete, None)

    integration.noter_tour("Oui, fais-en une tâche.")
    assert isinstance(demande(), PermissionDecisionReject)  # jamais montrée à un réveil
    engagements.noter_montrees(["p0000h"])
    assert isinstance(demande(), PermissionDecisionApproveOnce)
    maintenant = brouillons.time.time()
    monkeypatch.setattr(brouillons.time, "time", lambda: maintenant + AGE_MAX_OUI + 1)
    assert isinstance(demande(), PermissionDecisionReject)  # un oui d'il y a plus de trois minutes


# --- Après une écoute apprise ----------------------------------------------------------------------

def test_la_memoire_signale_les_ecoutes_apprises_et_la_passe_les_releve(monkeypatch):
    from joi import memoire

    relevees = []

    async def faux_relever(entrees, modele=None, fournisseur=None):
        relevees.append(entrees)
        return []

    monkeypatch.setattr(engagements, "relever", faux_relever)
    monkeypatch.setattr(memoire, "_abonnes_ecoutes", [])
    lancees = []
    engagements.installer(lancees.append)

    def ecoute(**options):
        segment = memoire.SegmentEcoute(debut="2026-11-16T14:00:00", entrees=SEANCE, integre=lambda: None)
        return memoire.Ecoute(debut="2026-11-16T14:00:00", fin="2026-11-16T15:00:00", segments=[segment],
                              **{"voix_reconnues": True, **options})

    memoire._signaler_ecoutes([ecoute(), ecoute(correction=True), ecoute(voix_reconnues=False)])
    assert len(lancees) == 1  # la correction et l'écoute sans voix reconnues sont sautées
    asyncio.run(lancees[0])
    assert relevees == [SEANCE]


# --- Après la relecture ----------------------------------------------------------------------------

def test_une_heure_ou_une_duree_n_est_pas_un_quantieme():
    lundi = datetime(2026, 11, 16, 14, 1)
    assert engagements.echeance("à 15 h", lundi) is None
    assert engagements.echeance("vers 9 h 30", lundi) is None
    assert engagements.echeance("dans 2 semaines", lundi) == date(2026, 11, 30)
    assert engagements.echeance("dans trois jours", lundi) == date(2026, 11, 19)
    assert engagements.echeance("le 5 janvier", lundi) == date(2027, 1, 5)
    assert engagements.echeance("d'ici le 19", lundi) == date(2026, 11, 19)
    assert engagements.echeance("le 20 novembre", lundi) == date(2026, 11, 20)


def test_les_oui_d_une_tache():
    for oui in ("Oui", "Bonne idée, note-la", "Volontiers", "Fais-le", "Ouais pas de problème", "ok"):
        assert engagements.est_oui(oui), oui
    for non in ("Non", "Pas maintenant", "Bonne idée, mais pas tout de suite", "On verra", "merci",
                "Tu veux une version plus pratique ?"):
        assert not engagements.est_oui(non), non


def test_une_proposition_montree_un_autre_jour_ne_revient_pas():
    engagements.ajouter([_proposition("p0000f", "Essayer l'outil")])
    hier = datetime.now() - timedelta(days=1)
    assert [p["numero"] for p in engagements.a_proposer()] == ["p0000f"]
    engagements.noter_montrees(["p0000f"], hier)
    assert engagements.a_proposer() == []
    assert engagements.lire()[0]["etat"] == "proposee"  # pas refusée : seulement plus remontrée


def test_le_reveil_note_les_propositions_montrees():
    engagements.ajouter([_proposition("p0000g", "Essayer l'outil")])

    class Vide(FauxFournisseur):
        def courriels_recents(self, non_lus_seulement, nombre):
            return []

        def courriels_depuis(self, depuis):
            return []

        def adresse(self):
            return None

    asyncio.run(veille.Veille(Vide()).contexte_reveil())
    assert engagements.lire()[0]["montree_le"] == date.today().isoformat()


def test_un_prenom_n_est_pas_un_assistant_et_la_voix_de_la_personne_est_preferee():
    entrees = [entree("10:00", "voix inconnue 4", "Je dois rappeler Claude demain."),
               entree("10:01", "toi", "Oui, je dois rappeler Claude demain.")]
    modele = modele_qui_rend([brute("Je dois rappeler Claude demain", "rappeler Claude", "demain")])
    nouvelles = asyncio.run(engagements.relever(entrees, modele, FauxFournisseur()))
    assert [p["dite_le"] for p in nouvelles] == ["2026-11-16T10:01:00"]
