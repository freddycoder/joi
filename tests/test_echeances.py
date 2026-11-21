"""Les échéances (partie 5 du plan Joi fiable) : la bibliothécaire tient echeances.md, le code n'en garde
que les lignes bien formées des sept prochains jours pour le réveil, la consigne d'en parler ne vient
qu'une fois par jour, et un événement né d'une échéance n'est créé que sur un oui, sans invité.
Phrases fictives, sans réseau, jamais donnees/."""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject

from joi import memoire
from joi.compte.modele import DEBUT_EXTERNE, FIN_EXTERNE, Evenement

AUJOURDHUI = date(2026, 11, 21)
CONSIGNE = "mentionne au plus une chose d'aujourd'hui ou de demain"


def _jour(decalage: int) -> str:
    return (AUJOURDHUI + timedelta(days=decalage)).isoformat()


def _echeances(*lignes: str) -> None:
    fichier = memoire.RACINE / memoire.FICHIER_ECHEANCES
    fichier.parent.mkdir(parents=True, exist_ok=True)
    entete = "---\ndescription: les dates à venir\nmis_a_jour: 2026-11-21\n---\n# Échéances\n"
    fichier.write_text(entete + "".join(l + "\n" for l in lignes), encoding="utf-8")


def _reveil(jour: date = AUJOURDHUI) -> str:
    """Un vrai réveil : le prompt, puis la session créée (le cerveau note alors les échéances dites)."""
    texte = memoire.contexte_reveil(aujourdhui=jour)
    memoire.noter_echeances_dites()
    return texte


@pytest.fixture(autouse=True)
def propre():
    """Pas d'échéances ni d'état « déjà dites » d'un test à l'autre."""
    for chemin in (memoire.RACINE / memoire.FICHIER_ECHEANCES, memoire.FICHIER_ECHEANCES_DITES):
        chemin.unlink(missing_ok=True)
    yield
    for chemin in (memoire.RACINE / memoire.FICHIER_ECHEANCES, memoire.FICHIER_ECHEANCES_DITES):
        chemin.unlink(missing_ok=True)


# --- Ce que le code garde ------------------------------------------------------------------------

def test_reveil_garde_les_sept_prochains_jours_dans_externe(caplog):
    _echeances(
        f"- {_jour(-1)} : rendre le livre (journees/2026-11-15.md)",            # passée
        f"- {_jour(3)} 14:00 : rendez-vous chez le dentiste (journees/2026-11-18.md)",
        f"- {_jour(0)} : finir la soumission (journees/2026-11-19.md)",
        "- bientôt : appeler Bruno",                                             # mal formée
        f"- {_jour(8)} : fermer le vieux compte (journees/2026-11-18.md)",       # trop loin
        f"- {_jour(1)} 10:00 : enregistrer la batterie (journees/2026-11-20.md)",
        f"- {_jour(7)} : payer l'abonnement (journees/2026-11-20.md)",
        f"- {_jour(2)} 25:00 : heure impossible (journees/2026-11-20.md)",       # mal formée
    )
    with caplog.at_level(logging.INFO, logger="joi.memoire"):
        texte = memoire.contexte_reveil(aujourdhui=AUJOURDHUI)
    assert "## À venir" in texte
    bloc = texte[texte.index("## À venir"):]
    assert DEBUT_EXTERNE in bloc and FIN_EXTERNE in bloc
    donnees = bloc[bloc.index(DEBUT_EXTERNE):bloc.index(FIN_EXTERNE)]
    # Les plus proches d'abord, avec le jour dit comme on le dit.
    ordre = ["finir la soumission", "enregistrer la batterie", "dentiste", "payer l'abonnement"]
    assert [donnees.index(m) for m in ordre] == sorted(donnees.index(m) for m in ordre)
    assert "aujourd'hui" in donnees and "demain, dimanche 22 novembre, 10:00" in donnees
    for absent in ("rendre le livre", "fermer le vieux compte", "appeler Bruno", "heure impossible", "journees/"):
        assert absent not in texte
    # Les lignes écartées sont comptées au journal.
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "2 mal formée(s)" in messages and "1 passée(s)" in messages


def test_reveil_garde_cinq_echeances_au_plus():
    _echeances(*(f"- {_jour(i % 7)} : chose numéro {i} (journees/2026-11-20.md)" for i in range(9)))
    texte = memoire.contexte_reveil(aujourdhui=AUJOURDHUI)
    assert sum(f"chose numéro {i}" in texte for i in range(9)) == memoire.ECHEANCES_MAX == 5


def test_sans_fichier_ni_echeance_rien_n_est_ajoute():
    assert "À venir" not in memoire.contexte_reveil(aujourdhui=AUJOURDHUI)
    _echeances(f"- {_jour(-3)} : déjà passé (journees/2026-11-18.md)")
    assert "À venir" not in memoire.contexte_reveil(aujourdhui=AUJOURDHUI)


# --- La consigne d'en parler, une fois par jour ----------------------------------------------------

def test_consigne_au_premier_reveil_du_jour_seulement():
    _echeances(f"- {_jour(1)} 10:00 : enregistrer la batterie (journees/2026-11-20.md)")
    premier = _reveil()
    second = _reveil()
    assert "## À venir" in premier and CONSIGNE in premier
    assert premier.count(CONSIGNE) == 1
    assert "## À venir" in second and CONSIGNE not in second  # l'échéance reste, la consigne part
    # Le lendemain, c'est « aujourd'hui » : la consigne revient une fois.
    lendemain = AUJOURDHUI + timedelta(days=1)
    assert CONSIGNE in _reveil(lendemain)
    assert CONSIGNE not in _reveil(lendemain)
    assert set(json.loads(memoire.FICHIER_ECHEANCES_DITES.read_text(encoding="utf-8"))) == {lendemain.isoformat()}


def test_pas_de_consigne_sans_echeance_d_aujourd_hui_ou_de_demain():
    _echeances(f"- {_jour(3)} : rendez-vous chez le dentiste (journees/2026-11-18.md)")
    texte = memoire.contexte_reveil(aujourdhui=AUJOURDHUI)
    assert "dentiste" in texte and CONSIGNE not in texte


def test_une_echeance_nouvelle_du_jour_ramene_la_consigne():
    _echeances(f"- {_jour(1)} : enregistrer la batterie (journees/2026-11-20.md)")
    assert CONSIGNE in _reveil()
    _echeances(f"- {_jour(1)} : enregistrer la batterie (journees/2026-11-20.md)",
               f"- {_jour(0)} 17:00 : passer chercher la guitare (journees/2026-11-21.md)")
    assert CONSIGNE in _reveil()
    assert CONSIGNE not in _reveil()


def test_une_echeance_reformulee_ne_ramene_pas_la_consigne():
    """La bibliothécaire réécrit ses fichiers à chaque passe : même jour, même heure, même échéance."""
    _echeances(f"- {_jour(1)} 10:00 : enregistrer la batterie (journees/2026-11-20.md)")
    assert CONSIGNE in _reveil()
    _echeances(f"- {_jour(1)} 10:00 : enregistrement de la batterie au studio (journees/2026-11-21.md)")
    texte = _reveil()
    assert "au studio" in texte and CONSIGNE not in texte


def test_un_reveil_sans_session_ou_le_banc_n_epuise_pas_la_consigne():
    """Le banc d'essai (et un réveil raté) construit le prompt sans noter : la consigne reste au vrai réveil."""
    _echeances(f"- {_jour(1)} 10:00 : enregistrer la batterie (journees/2026-11-20.md)")
    assert CONSIGNE in memoire.contexte_reveil(aujourdhui=AUJOURDHUI)  # banc : rien de noté
    assert CONSIGNE in _reveil()
    assert CONSIGNE not in _reveil()


def test_le_banc_ne_note_pas_les_echeances():
    import inspect

    from joi import banc, cerveau

    assert "noter_echeances=False" in inspect.getsource(banc.mesurer)
    assert "memoire.noter_echeances_dites()" in inspect.getsource(cerveau.Cerveau.demarrer)


def test_etat_illisible_ne_bloque_pas_le_reveil():
    _echeances(f"- {_jour(0)} : finir la soumission (journees/2026-11-19.md)")
    memoire.FICHIER_ECHEANCES_DITES.parent.mkdir(parents=True, exist_ok=True)
    memoire.FICHIER_ECHEANCES_DITES.write_text("pas du json", encoding="utf-8")
    assert CONSIGNE in _reveil()


# --- Les consignes de la bibliothécaire ------------------------------------------------------------

def test_la_bibliothecaire_connait_le_format_de_echeances():
    consignes = (memoire.Path(memoire.__file__).parent / "bibliothecaire.md").read_text(encoding="utf-8")
    assert "echeances.md" in consignes
    assert "- AAAA-MM-JJ[ HH:MM] : quoi (journees/AAAA-MM-JJ.md)" in consignes


def test_le_format_de_la_bibliothecaire_est_celui_que_le_code_lit():
    """La ligne d'exemple des consignes, remplie, doit passer le filtre du code."""
    ligne = f"- {_jour(1)} 09:30 : déposer la batterie à l'atelier (journees/2026-11-20.md)"
    _echeances(ligne)
    assert "déposer la batterie" in memoire.contexte_reveil(aujourdhui=AUJOURDHUI)


# --- L'événement proposé : seulement sur un oui, jamais d'invité -----------------------------------

class FauxCompte:
    nom = "faux"

    def __init__(self):
        self.crees = []

    def creer_evenement(self, titre, debut, fin, lieu, note, rappel_minutes, invites):
        self.crees.append({"titre": titre, "invites": list(invites)})
        return Evenement(id="ev1", titre=titre, debut=debut, fin=fin, invites=list(invites))


def _permission(integration, **arguments):
    requete = SimpleNamespace(kind="mcp", server_name="compte", tool_name="compte-creer_evenement",
                              args=arguments)
    return integration.permission(requete, None)


def test_invite_tiers_toujours_refuse(monkeypatch):
    from joi.compte import reglages
    from joi.compte.integration import Integration

    monkeypatch.setattr(reglages, "PROPRIETAIRE", "toi@exemple.org")
    integration = Integration(repli=lambda *a: None)
    integration.noter_tour("Oui, mets-le à l'agenda.")
    debut = "2026-11-22T10:00"
    assert isinstance(_permission(integration, titre="Réunion", debut=debut, invites=["denis@exemple.org"]),
                      PermissionDecisionReject)
    assert isinstance(_permission(integration, titre="Réunion", debut=debut, invites=["Toi@Exemple.org"]),
                      PermissionDecisionApproveOnce)
    # Une demande ordinaire (sans source) reste comme avant.
    assert isinstance(_permission(integration, titre="Rappel", debut=debut, inviter_toi=True),
                      PermissionDecisionApproveOnce)


def test_evenement_d_echeance_exige_un_oui_recent_et_aucun_invite(monkeypatch):
    from joi.compte import brouillons
    from joi.compte.integration import AGE_MAX_OUI, Integration

    integration = Integration(repli=lambda *a: None)
    echeance = {"titre": "Enregistrer la batterie", "debut": "2026-11-22T10:00", "source": "echeance",
                "inviter_toi": False}
    assert isinstance(_permission(integration, **echeance), PermissionDecisionReject)  # rien dit
    integration.noter_tour("Je ne sais pas encore.")
    assert isinstance(_permission(integration, **echeance), PermissionDecisionReject)
    integration.noter_tour("Oui, vas-y.")
    assert isinstance(_permission(integration, **echeance), PermissionDecisionApproveOnce)
    assert isinstance(_permission(integration, **{**echeance, "inviter_toi": True}), PermissionDecisionReject)
    assert isinstance(_permission(integration, **{**echeance, "invites": ["toi@exemple.org"]}),
                      PermissionDecisionReject)
    # Des arguments en JSON (comme le SDK peut les passer) sont lus pareil.
    requete = SimpleNamespace(kind="mcp", server_name="compte", tool_name="compte-creer_evenement",
                              args=json.dumps(echeance))
    assert isinstance(integration.permission(requete, None), PermissionDecisionApproveOnce)
    maintenant = brouillons.time.time()
    monkeypatch.setattr(brouillons.time, "time", lambda: maintenant + AGE_MAX_OUI + 1)
    assert isinstance(_permission(integration, **echeance), PermissionDecisionReject)  # oui trop vieux


def test_oui_naturels_pour_l_agenda():
    from joi.compte.integration import est_oui_agenda

    for oui in ("Oui.", "Vas-y.", "Mets-le à l'agenda.", "Parfait, ajoute-le.", "D'accord, note-le."):
        assert est_oui_agenda(oui), oui
    for non in ("Non, ne le mets pas.", "Attends, mets-le plutôt jeudi.", "Je ne sais pas.", "Pas besoin."):
        assert not est_oui_agenda(non), non


def test_l_outil_ne_met_jamais_d_invite_sur_une_echeance(monkeypatch):
    from joi.compte import reglages, serveur_mcp

    monkeypatch.setattr(reglages, "PROPRIETAIRE", "toi@exemple.org")
    compte = FauxCompte()
    serveur = serveur_mcp.creer_serveur(compte)
    debut = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT10:00")
    asyncio.run(serveur.call_tool("creer_evenement", {"titre": "Enregistrer la batterie", "debut": debut,
                                                       "source": "echeance", "inviter_toi": True}))
    asyncio.run(serveur.call_tool("creer_evenement", {"titre": "Rappel", "debut": debut}))
    assert compte.crees[0]["invites"] == []                  # échéance : jamais d'invité
    assert compte.crees[1]["invites"] == ["toi@exemple.org"]  # demande ordinaire : comme avant
