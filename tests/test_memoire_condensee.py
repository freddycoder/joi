"""Une mémoire condensée et à jour : le réveil garde toi.md et l'index, une longue journée garde son
soir, une tranche d'écoute trop courte n'ouvre pas la bibliothécaire, et la condensation unique
(lancée par l'utilisateur) se relit avec git diff."""

import asyncio
import logging
import shutil
from contextlib import asynccontextmanager
from datetime import date

import pytest

from joi import config, memoire


def _ecrire(relatif: str, contenu: str) -> None:
    fichier = memoire.RACINE / relatif
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text(contenu, encoding="utf-8")


# --- contexte_reveil : une limite par fichier ---------------------------------------------------

def test_reveil_garde_toi_et_l_index_chacun_coupe(caplog):
    _ecrire("toi.md", "# Toi\n" + "".join(f"- fait numéro {i} sur la personne\n" for i in range(250)))
    _ecrire("index.md", "# Index\n" + "".join(f"- projets/p{i}.md : un projet\n" for i in range(250)))
    with caplog.at_level(logging.WARNING, logger="joi.memoire"):
        texte = memoire.contexte_reveil()
    assert "### toi.md" in texte and "### index.md" in texte
    assert "fait numéro 0 " in texte and "projets/p0.md" in texte
    assert "fait numéro 249" not in texte and "projets/p249.md" not in texte
    assert len(texte) < 2 * memoire.TAILLE_MAX_REVEIL_FICHIER + 500
    coupes = [r.getMessage() for r in caplog.records if "coupé" in r.getMessage()]
    assert any("toi.md" in m for m in coupes) and any("index.md" in m for m in coupes)


def test_reveil_sans_coupe_quand_tout_tient(caplog):
    _ecrire("toi.md", "# Toi\n- prénom inconnu\n")
    _ecrire("index.md", "# Index\n- toi.md : la personne\n")
    with caplog.at_level(logging.WARNING, logger="joi.memoire"):
        texte = memoire.contexte_reveil()
    assert "[…" not in texte
    assert not caplog.records


# --- Une longue journée garde son début et sa fin -------------------------------------------------

def test_couper_tete_et_queue_garde_la_premiere_et_la_derniere_ligne():
    lignes = ["Matin : arrivée à l'atelier."] + [f"Ligne {i} du milieu de la journée." for i in range(700)] + \
             ["Soir : départ vers dix-huit heures."]
    texte = "\n".join(lignes)
    assert len(texte) > 20000
    coupe = memoire._couper_tete_queue(texte, 16000)
    assert len(coupe) <= 16000 + 20
    assert coupe.startswith("Matin : arrivée")
    assert coupe.endswith("Soir : départ vers dix-huit heures.")
    assert "[…]" in coupe


def test_souvenir_d_une_longue_journee_garde_le_soir():
    jour = date(2026, 11, 13)
    lignes = ["# Journée", "Matin : réunion du projet principal."] + \
             [f"- Détail {i} de la journée, sans grande importance." for i in range(600)] + \
             ["Soir : la licence est commandée."]
    _ecrire(f"journees/{jour}.md", "\n".join(lignes))
    resultat = memoire.souvenir("13 novembre", aujourdhui=date(2026, 11, 18))
    assert "Matin : réunion" in resultat and "Soir : la licence est commandée." in resultat


# --- consolider_ecoute : une tranche trop courte n'ouvre pas la bibliothécaire -------------------

@pytest.fixture
def bibliothecaire(monkeypatch):
    """Une fausse bibliothécaire : compte les ouvertures et les messages."""
    appels = {"ouvertures": 0, "messages": []}

    class FausseSession:
        async def send_and_wait(self, message, timeout=None):
            appels["messages"].append(message)

    @asynccontextmanager
    async def fausse():
        appels["ouvertures"] += 1
        yield FausseSession()

    monkeypatch.setattr(memoire, "_bibliothecaire", fausse)
    monkeypatch.setattr(memoire, "_enregistrer_version", lambda message: None)
    return appels


def _ecoute(phrases: int, en_cours: bool, marques: list) -> memoire.Ecoute:
    entrees = [{"ts": f"2026-11-17T12:58:{i:02d}.000000", "qui": "entendu", "locuteur": "toi",
                "texte": f"Phrase {i}."} for i in range(phrases)]
    segment = memoire.SegmentEcoute(debut="2026-11-17T12:58:00.000000", entrees=entrees,
                                    integre=lambda: marques.append("intégré"))
    return memoire.Ecoute(debut="2026-11-17T12:30:00", fin="2026-11-17T12:59:00",
                          segments=[segment], en_cours=en_cours)


def test_tranche_sans_phrase_marquee_sans_bibliothecaire(bibliothecaire):
    marques: list = []
    asyncio.run(memoire.consolider_ecoute([_ecoute(0, en_cours=True, marques=marques)]))
    assert bibliothecaire["ouvertures"] == 0
    assert marques == ["intégré"]  # rien à apprendre : elle ne repassera pas


def test_tranche_trop_courte_gardee_pour_la_suivante(bibliothecaire, caplog):
    marques: list = []
    with caplog.at_level(logging.INFO, logger="joi.memoire"):
        asyncio.run(memoire.consolider_ecoute([_ecoute(3, en_cours=True, marques=marques)]))
    assert bibliothecaire["ouvertures"] == 0
    assert marques == []  # pas marquée : la passe suivante (ou celle du retour) la reprendra
    assert any("trop courte" in r.getMessage() for r in caplog.records)


def test_fin_d_ecoute_courte_quand_meme_apprise(bibliothecaire):
    marques: list = []
    asyncio.run(memoire.consolider_ecoute([_ecoute(3, en_cours=False, marques=marques)]))
    assert bibliothecaire["ouvertures"] == 1 and len(bibliothecaire["messages"]) == 1
    assert marques == ["intégré"]


def test_tranche_assez_longue_apprise(bibliothecaire):
    marques: list = []
    asyncio.run(memoire.consolider_ecoute([_ecoute(6, en_cours=True, marques=marques)]))
    assert bibliothecaire["ouvertures"] == 1
    assert marques == ["intégré"]


# --- condenser : la condensation unique, entre deux commits ---------------------------------------

@pytest.mark.skipif(shutil.which("git") is None, reason="git absent")
def test_condenser_entre_deux_commits(monkeypatch):
    memoire.initialiser()
    gros = "\n".join(f"- {i} : une ligne de la journée, assez longue pour peser." for i in range(200))
    _ecrire("journees/2026-11-13.md", gros)
    _ecrire("journees/2026-11-14.md", "# Courte\n- rien\n")
    _ecrire("projets/principal.md", gros)
    messages: list[str] = []

    class FausseSession:
        async def send_and_wait(self, message, timeout=None):
            messages.append(message)
            # Elle réécrit avec ses outils : ici, chaque gros fichier devient une ligne.
            for chemin in ("journees/2026-11-13.md", "projets/principal.md"):
                if chemin in message:
                    memoire.ecrire(chemin, "# Condensé\n- l'essentiel\n")

    @asynccontextmanager
    async def fausse():
        yield FausseSession()

    monkeypatch.setattr(memoire, "_bibliothecaire", fausse)
    monkeypatch.setattr(memoire, "_joi_en_marche", lambda: False)
    avant = memoire._git("rev-list", "--count", "HEAD").stdout.strip()
    assert memoire.main(["condenser"]) == 0
    apres = memoire._git("rev-list", "--count", "HEAD").stdout.strip()
    assert int(apres) == int(avant) + 2
    titres = memoire._git("log", "-2", "--format=%s").stdout.splitlines()
    assert "Après" in titres[0] and "Avant" in titres[1]
    # Les grosses journées, le gros projet et toi.md sont confiés ; la petite journée, non.
    import re

    confies = set(re.findall(r"CONDENSER : (\S+)", "\n".join(messages)))
    assert {"journees/2026-11-13.md", "projets/principal.md", "toi.md", "index.md"} <= confies
    assert "journees/2026-11-14.md" not in confies
    # Tout reste dans le dossier temporaire des tests.
    assert memoire.RACINE.resolve().is_relative_to(config.DOSSIER_DONNEES.resolve())
    assert (memoire.RACINE / "journees/2026-11-13.md").read_text(encoding="utf-8").startswith("# Condensé")


def test_condenser_refuse_sans_etat_d_avant(monkeypatch):
    """Sans commit « Avant » (pas de dépôt git, dépôt verrouillé…), rien n'est réécrit."""
    ouvertures = []

    @asynccontextmanager
    async def fausse():
        ouvertures.append(1)
        yield None

    monkeypatch.setattr(memoire, "_bibliothecaire", fausse)
    monkeypatch.setattr(memoire, "_version_avant", lambda message: None)
    monkeypatch.setattr(memoire, "_joi_en_marche", lambda: False)
    assert memoire.main(["condenser"]) == 1
    assert ouvertures == []


def test_condenser_refuse_si_joi_tourne(monkeypatch):
    lancee = []
    monkeypatch.setattr(memoire, "_joi_en_marche", lambda: True)
    monkeypatch.setattr(memoire, "condenser", lambda: lancee.append(1))
    assert memoire.main(["condenser"]) == 1
    assert lancee == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git absent")
def test_condenser_un_echec_n_arrete_pas_les_suivants(monkeypatch):
    memoire.initialiser()
    _ecrire("projets/principal.md", "x" * 7000)

    class FausseSession:
        async def send_and_wait(self, message, timeout=None):
            if "CONDENSER : projets/principal.md" in message:
                raise TimeoutError("délai dépassé")
            if "CONDENSER : toi.md" in message:
                memoire.ecrire("toi.md", "# Toi\n- condensé\n")

    @asynccontextmanager
    async def fausse():
        yield FausseSession()

    monkeypatch.setattr(memoire, "_bibliothecaire", fausse)
    resultat = asyncio.run(memoire.condenser())
    assert resultat.echecs == ["projets/principal.md"]
    assert resultat.confies == ["toi.md", "index.md"]
    titre = memoire._git("log", "-1", "--format=%s").stdout.strip()
    assert "2 fichier(s) sur 3" in titre and "échec : projets/principal.md" in titre


def test_consignes_de_la_bibliothecaire_avec_l_adresse_de_la_personne(monkeypatch):
    from joi.compte import reglages

    monkeypatch.setattr(config, "JOI_COMPTE", "microsoft")
    monkeypatch.setattr(reglages, "PROPRIETAIRE", "personne@exemple.org")
    consignes = memoire._consignes_bibliothecaire()
    assert "personne@exemple.org" in consignes and "{adresse_toi}" not in consignes


# --- Plan Joi fiable, partie 1 : une mémoire juste ------------------------------------------------

def _voix(nom: str, classe: str, secondes: float) -> dict:
    from datetime import datetime

    debut = datetime(2026, 11, 19, 9, 0)
    return {"nom": nom, "classe": classe, "duree_s": secondes, "plages": [["09:00", "09:10"]],
            "debut": debut}


def test_voix_entendues_une_ligne_pour_toutes_les_inconnues():
    from joi import empreintes

    voix = [_voix("toi", "connue", 960), _voix("Bruno", "connue", 1620), _voix("Denis", "connue", 47)]
    voix += [_voix(f"voix inconnue {i}", "interlocuteur", 120) for i in range(6)]
    voix += [_voix(f"voix inconnue {i}", "passage", 5) for i in range(10, 24)]
    texte = empreintes.texte_bilan({"voix": voix})
    puces = [ligne for ligne in texte.splitlines() if ligne.startswith("- ")]
    assert len(puces) == 4, puces
    assert puces[-1].startswith("- 20 voix inconnues, ") and "dont 14 de passage" in puces[-1]
    assert "voix inconnue 3" not in texte.lower()


def test_voix_entendues_sans_toi_avec_deux_inconnues():
    from joi import empreintes

    voix = [_voix("voix inconnue 1", "interlocuteur", 300), _voix("voix inconnue 2", "interlocuteur", 120)]
    texte = empreintes.texte_bilan({"voix": voix})
    assert "- Toi : voix pas reconnue" in texte and "ci-dessous" in texte
    assert "- 2 voix inconnues, 7 min en tout." in texte
    assert "voix inconnue 1" not in texte.lower()


def test_voix_entendues_sans_toi_ni_inconnue_pas_de_ci_dessous():
    from joi import empreintes

    texte = empreintes.texte_bilan({"voix": [_voix("Bruno", "connue", 600)]})
    assert "ci-dessous" not in texte and "Bruno" in texte


def test_index_signale_ligne_trop_longue_et_journee_pas_regroupee(caplog):
    _ecrire("index.md", "# Index\n"
            "- toi.md : la personne\n"
            f"- projets/x.md : {'un état très détaillé ' * 9}\n"
            "- journees/2026-11-08.md : une vieille journée\n"
            "- journees/2026-11-17.md : une journée récente\n"
            "- journees/ semaine du 2 novembre : regroupée\n")
    memoire._index_deja_signale = set()
    with caplog.at_level(logging.WARNING, logger="joi.memoire"):
        problemes = memoire.verifier_index(date(2026, 11, 20))
    assert len(problemes) == 2
    assert any("caractères" in p for p in problemes)
    assert any("2026-11-08" in p for p in problemes)
    assert sum("Index de la mémoire" in r.getMessage() for r in caplog.records) == 2


def test_index_conforme_rien_a_dire(caplog):
    _ecrire("index.md", "# Index\n- toi.md : la personne\n- journees/2026-11-19.md : courte\n")
    with caplog.at_level(logging.WARNING, logger="joi.memoire"):
        assert memoire.verifier_index(date(2026, 11, 20)) == []
    assert not caplog.records


def test_index_absent_rien_a_dire():
    assert memoire.verifier_index(date(2026, 11, 20)) == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git absent")
def test_condenser_met_les_faits_deplaces_dans_le_commit(monkeypatch):
    from types import SimpleNamespace

    memoire.initialiser()
    _ecrire("journees/2026-11-13.md", "\n".join(f"- {i} : une ligne assez longue pour peser." for i in range(300)))

    class FausseSession:
        async def send_and_wait(self, message, timeout=None):
            if "journees/2026-11-13.md\n" in message:
                memoire.ecrire("journees/2026-11-13.md", "# Condensé\n- l'essentiel\n")
                texte = ("Voici :\n- déplacé vers projets/principal.md : fin d'exercice au 30 septembre\n"
                         "- retiré : un dépannage d'imprimante")
            else:
                texte = "Rien de déplacé"
            return SimpleNamespace(data=SimpleNamespace(content=texte))

    @asynccontextmanager
    async def fausse():
        yield FausseSession()

    monkeypatch.setattr(memoire, "_bibliothecaire", fausse)
    asyncio.run(memoire.condenser())
    corps = memoire._git("log", "-1", "--format=%B").stdout
    assert corps.startswith("Après la condensation")
    assert "Faits déplacés ou retirés :" in corps
    assert "- journees/2026-11-13.md : déplacé vers projets/principal.md : fin d'exercice" in corps
    assert "- journees/2026-11-13.md : retiré : un dépannage" in corps
    assert "Rien de déplacé" not in corps


def test_faits_deplaces_sans_reponse():
    assert memoire._faits_deplaces(None) == []


def test_consignes_d_une_memoire_juste():
    from pathlib import Path

    consignes = (Path(memoire.__file__).parent / "bibliothecaire.md").read_text(encoding="utf-8")
    assert "Une voix inconnue ne devient jamais « tu »" in consignes
    assert "Un projet ne recopie pas une journée" in consignes
    assert "300 caractères au plus par puce" in consignes
    assert "Ne supprime jamais une décision" in memoire.CONSIGNE_CONDENSER
    assert "faits déplacés ou retirés" in memoire.CONSIGNE_CONDENSER


def test_consignes_de_la_bibliothecaire_grandissent_de_1_2_ko_au_plus():
    """Règle du plan Joi fiable : les consignes de la bibliothécaire (bibliothecaire.md, la consigne de
    condensation, la consigne d'une écoute) grandissent de 1,2 Ko au plus. Tailles d'avant le plan."""
    import inspect
    import re
    from pathlib import Path

    avant = 6206 + 776 + 302
    fichier = (Path(memoire.__file__).parent / "bibliothecaire.md").read_bytes()
    source = inspect.getsource(memoire.consolider_ecoute)
    bloc = source[source.index('consigne = (f"Le résumé'):source.index("message = (")]
    ecoute = "".join(re.findall(r'"([^"]*)"', bloc))  # les morceaux de la consigne (sans guillemets droits)
    apres = len(fichier) + len(memoire.CONSIGNE_CONDENSER.encode()) + len(ecoute.encode())
    assert apres - avant <= 1200, apres - avant


def test_index_redit_seulement_ce_qui_change(caplog):
    _ecrire("index.md", "# Index\n- journees/2026-11-08.md : vieille\n")
    memoire._index_deja_signale = set()
    with caplog.at_level(logging.WARNING, logger="joi.memoire"):
        memoire.verifier_index(date(2026, 11, 20))
        memoire.verifier_index(date(2026, 11, 20))
    assert sum("Index de la mémoire" in r.getMessage() for r in caplog.records) == 1
