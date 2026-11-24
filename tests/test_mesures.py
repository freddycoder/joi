"""Des mesures justes pour la prochaine révision : les voix du jour comptées depuis voix-journee.json
(pas depuis les étiquettes de segment), le modèle dans la ligne « Latence », le coût de transcription
du jour."""

import asyncio
import importlib.util
import json
import logging
import time
from datetime import date
from pathlib import Path

import pytest

from joi import config, serveur, transcription
from joi.oreilles import Tour

RACINE = Path(__file__).resolve().parents[1]


@pytest.fixture
def extraire():
    spec = importlib.util.spec_from_file_location(
        "extraire", RACINE / ".claude" / "skills" / "proposer-ameliorations" / "extraire.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _voix_journee(jour: str, contenu: dict) -> None:
    dossier = config.DOSSIER_ENREGISTREMENTS / jour
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "voix-journee.json").write_text(json.dumps(contenu), encoding="utf-8")


# --- extraire.py : les voix du jour -----------------------------------------------------------------

def test_voix_du_jour_suit_les_affectations(extraire):
    # Trois segments, des étiquettes de groupe qui recommencent à chaque segment : 3 voix seulement.
    _voix_journee("2026-11-16", {"affectations": {
        "090000": {"0": "toi", "1": 1, "2": 2},
        "093000": {"0": 1, "1": "toi"},
        "100000": {"0": 3, "1": 2, "2": "toi"},
    }})
    assert extraire.voix_du_jour(config.DOSSIER_DONNEES, date(2026, 11, 16)) == "4"


def test_voix_du_jour_avec_le_bilan_du_soir(extraire):
    _voix_journee("2026-11-17", {
        "affectations": {"090000": {"0": "toi", "1": 1}},
        "bilans": [
            {"le": "2026-11-17T12:00:00", "voix": [{"nom": "toi", "classe": "connue"}]},
            {"le": "2026-11-17T18:00:00", "voix": [
                {"nom": "toi", "classe": "connue"}, {"nom": "Collègue", "classe": "connue"},
                {"nom": "voix inconnue 1", "classe": "interlocuteur"},
                {"nom": "voix inconnue 2", "classe": "passage"},
                {"nom": "voix inconnue 3", "classe": "passage"},
                {"nom": "voix inconnue 4", "classe": "media"},
            ]},
        ],
    })
    texte = extraire.voix_du_jour(config.DOSSIER_DONNEES, date(2026, 11, 17))
    assert texte.startswith("6 ")  # le dernier bilan, pas le premier
    assert "1 interlocuteur" in texte and "2 de passage" in texte and "1 média" in texte


def test_voix_du_jour_sans_fichier(extraire):
    assert extraire.voix_du_jour(config.DOSSIER_DONNEES, date(2026, 11, 12)) == "—"


def test_cout_du_jour_lu_dans_les_logs(extraire):
    lignes = [
        "2026-11-16 10:00:00 INFO joi.transcription Écoute transcrite : 2 segment(s) sur 2, 1.00 h "
        "d'audio facturées, environ 0.23 $ US (40 s)",
        "2026-11-16 12:00:00 INFO joi.transcription Écoute transcrite : 1 segment(s) sur 1, 0.50 h "
        "d'audio facturées, environ 0.12 $ US (20 s)",
        "2026-11-17 10:00:00 INFO joi.transcription Coût de transcription du 2026-11-17 : 0.40 $ US",
        "2026-11-17 18:00:00 INFO joi.transcription Coût de transcription du 2026-11-17 : 1.74 $ US",
    ]
    couts = extraire.couts_par_jour(lignes)
    assert couts["2026-11-16"] == pytest.approx(0.35)  # sans ligne du jour : la somme des lots
    assert couts["2026-11-17"] == pytest.approx(1.74)  # avec : la dernière, qui cumule déjà


# --- La ligne « Latence » -------------------------------------------------------------------------

def test_ligne_latence_dit_le_modele():
    maintenant = time.monotonic()
    mesure = serveur.MesureReponse(Tour("Salut.", True, 0, [], recu=maintenant, fin_parole=maintenant - 0.2),
                                   modele="modele-essai")
    assert "modèle modele-essai" in mesure.ligne()
    sans = serveur.MesureReponse(None, modele=None)
    assert "modèle défaut" in sans.ligne()


def test_ligne_latence_avec_consultation_et_phrase_d_attente():
    mesure = serveur.MesureReponse(None, modele=None)
    mesure.envoi = 10.0
    mesure.consultations = 2
    mesure.attente = True
    mesure.son_reponse = 13.5
    ligne = mesure.ligne()
    assert "2 consultation(s) du compte" in ligne
    assert "vraie réponse 3.5 s" in ligne


# --- Le coût de transcription du jour ---------------------------------------------------------------

def test_cout_du_jour_additionne_les_segments(caplog):
    from joi import enregistreur

    dossier = config.DOSSIER_ENREGISTREMENTS / "2026-11-17"
    dossier.mkdir(parents=True, exist_ok=True)
    for i, couts in enumerate(([0.10, 0.20], [0.05, None])):
        manifeste = {"debut": f"2026-11-17T0{8 + i}:00:00", "fin": f"2026-11-17T0{8 + i}:30:00", "segments": [
            {"fichier": f"2026-11-17/0{8 + i}000{j}.mp3", "debut": f"2026-11-17T0{8 + i}:00:0{j}",
             "etat": "consolide", **({"cout": c} if c is not None else {})}
            for j, c in enumerate(couts)]}
        (dossier / f"seance-0{8 + i}0000.json").write_text(json.dumps(manifeste), encoding="utf-8")
    assert transcription.cout_du_jour("2026-11-17") == pytest.approx(0.35)
    with caplog.at_level(logging.INFO, logger="joi.transcription"):
        transcription.noter_cout_des_jours({"2026-11-17"})
    assert any("Coût de transcription du 2026-11-17 : 0.35 $ US" in r.getMessage() for r in caplog.records)


def test_vraie_reponse_mesuree_apres_la_phrase_d_attente(monkeypatch):
    """Joi consulte son compte : la phrase d'attente part d'abord (le total s'y arrête), la ligne dit
    aussi quand le son de la vraie réponse est parti, plus tard."""
    import asyncio

    from joi import cerveau, monitoring, voix

    monkeypatch.setattr(serveur, "PHRASES_DE_CONSULTATION", ["Je regarde…"])
    monkeypatch.setattr(serveur, "SANS_PHRASE_D_ATTENTE", set())

    async def synthetiser(texte, infos=None):
        if infos is not None:
            infos.update(cache=False, duree_s=0.0)
        return texte.encode()

    monkeypatch.setattr(voix, "synthetiser", synthetiser)

    class Navigateur:
        async def send_json(self, message):
            pass

        async def send_bytes(self, audio):
            pass

    class CerveauQuiConsulte:
        modele = None

        async def repondre(self, texte, systeme=False, note=None):
            self.envoi, self.premier_token = time.monotonic(), None
            yield cerveau.CONSULTATION("agenda")
            await asyncio.sleep(0.3)
            self.premier_token = time.monotonic()
            yield "Demain, tu n'as rien."

        async def interrompre(self):
            pass

    async def scenario():
        conversation = serveur.Conversation(Navigateur())
        conversation.cerveau = CerveauQuiConsulte()
        await conversation.repondre("Qu'est-ce que j'ai demain ?")
        return conversation._mesure

    mesure = asyncio.run(scenario())
    r = mesure.resume()
    assert mesure.attente and r["consultations"] == 1
    assert r["vraie_reponse"] >= r["total"] + 0.25  # pas la même valeur que la phrase d'attente
    assert "vraie réponse" in mesure.ligne()
    monitoring.latences.clear()


def test_cout_d_un_lot_du_lendemain_pas_compte_deux_fois(extraire):
    lignes = [
        "2026-11-18 10:00:00 INFO joi.transcription Coût de transcription du 2026-11-18 : 1.00 $ US",
        # Les segments du 18 transcrits le matin du 19 : le lot est daté du 19, le cumul du 18.
        "2026-11-19 08:00:00 INFO joi.transcription Écoute transcrite : 1 segment(s) sur 1, 0.50 h "
        "d'audio facturées, environ 0.12 $ US (20 s)",
        "2026-11-19 08:00:01 INFO joi.transcription Coût de transcription du 2026-11-18 : 1.12 $ US",
    ]
    assert extraire.couts_par_jour(lignes) == {"2026-11-18": pytest.approx(1.12)}


def test_cout_du_jour_estime_un_segment_sans_cout():
    dossier = config.DOSSIER_ENREGISTREMENTS / "2026-11-18"
    dossier.mkdir(parents=True, exist_ok=True)
    manifeste = {"debut": "2026-11-18T08:00:00", "fin": "2026-11-18T09:00:00", "segments": [
        {"fichier": "2026-11-18/080000.mp3", "debut": "2026-11-18T08:00:00", "etat": "consolide",
         "duree_s": 3600}]}
    (dossier / "seance-080000.json").write_text(json.dumps(manifeste), encoding="utf-8")
    assert transcription.cout_du_jour("2026-11-18") == pytest.approx(transcription._facture({}, manifeste["segments"][0])[1])
    assert transcription.cout_du_jour("2026-11-18") > 0


# --- Plan Joi fiable, partie 4 : des mesures exactes ------------------------------------------------

def test_usage_du_sdk_dans_la_ligne_latence():
    from datetime import timedelta
    from types import SimpleNamespace

    from copilot.session_events import SessionEventType as T

    from joi import cerveau

    c = cerveau.Cerveau(modele=None, avec_compte=False)
    c.usage = {}
    c._file = asyncio.Queue()  # un tour en cours
    for donnees in (SimpleNamespace(model="gpt-essai", response_model="gpt-essai-2026", input_tokens=7000,
                                    output_tokens=40, reasoning_tokens=300, cache_read_tokens=None,
                                    time_to_first_token=timedelta(seconds=4.2), reasoning_effort="medium"),
                    SimpleNamespace(model="gpt-essai", response_model=None, input_tokens=7100, output_tokens=30,
                                    reasoning_tokens=None, cache_read_tokens=5000,
                                    time_to_first_token=timedelta(seconds=1.0), reasoning_effort=None)):
        c._traiter_evenement(SimpleNamespace(type=T.ASSISTANT_USAGE, data=donnees))
    assert c.usage["modele"] == "gpt-essai"  # le dernier appel
    assert c.usage["entree"] == 14100 and c.usage["sortie"] == 70 and c.usage["reflexion"] == 300
    assert c.usage["appels"] == 2 and c.usage["premier_jeton_s"] == 4.2

    maintenant = time.monotonic()
    mesure = serveur.MesureReponse(Tour("Salut.", True, 0, [], recu=maintenant, fin_parole=maintenant - 0.2))
    mesure.envoi = maintenant
    mesure.usage = dict(c.usage)
    ligne = mesure.ligne()
    assert "modèle défaut (gpt-essai, medium)" in ligne
    assert "jetons entrée 14100, sortie 70, réflexion 300, appels 2" in ligne
    assert mesure.resume()["jetons"]["entree"] == 14100


def test_usage_hors_d_un_tour_ignore():
    from types import SimpleNamespace

    from copilot.session_events import SessionEventType as T

    from joi import cerveau

    c = cerveau.Cerveau(modele=None, avec_compte=False)
    c.usage = {}
    c._traiter_evenement(SimpleNamespace(type=T.ASSISTANT_USAGE, data=SimpleNamespace(model="m", input_tokens=5)))
    assert c.usage == {}


def test_effort_dit_meme_si_le_modele_est_celui_demande():
    maintenant = time.monotonic()
    mesure = serveur.MesureReponse(Tour("Salut.", True, 0, [], recu=maintenant), modele="gpt-x")
    mesure.usage = {"modele": "gpt-x", "effort": "low"}
    assert "modèle gpt-x (low)" in mesure.ligne()


def test_ligne_latence_sans_usage_reste_comme_avant():
    maintenant = time.monotonic()
    mesure = serveur.MesureReponse(Tour("Salut.", True, 0, [], recu=maintenant), modele="modele-essai")
    ligne = mesure.ligne()
    assert ligne.endswith("modèle modele-essai") and "jetons" not in ligne


def test_extraire_modele_jetons_et_reveils_sans_suite(extraire):
    lignes = [
        "2026-11-22 07:30:00 INFO joi.serveur Latence : fin de tour 0.1 s · cerveau : 1er mot 4.0 s, 1re phrase "
        "4.0 s · synthèse 0.3 s · lecture 0.0 s · total 4.5 s (fin de parole → premier son) · modèle défaut "
        "(gpt-essai) · jetons entrée 7000, sortie 40, réflexion 300, appels 1",
        "2026-11-22 07:31:00 INFO joi.serveur Latence : fin de tour 0.1 s · cerveau : 1er mot 2.0 s, 1re phrase "
        "2.0 s · synthèse 0.3 s · lecture 0.0 s · total 2.5 s (fin de parole → premier son) · modèle défaut "
        "(gpt-essai) · jetons entrée 9000, sortie 60, appels 1 · 1 recherche(s)",
        "2026-11-22 07:32:00 INFO joi.serveur Latence : fin de tour 0.1 s · total 2.5 s (fin de parole → premier "
        "son) · modèle défaut",
    ]
    assert extraire.modeles_du_jour(lignes) == "défaut (gpt-essai) ×2, défaut ×1"
    assert extraire.jetons_du_jour(lignes) == "8000 / 50 / 150"
    assert extraire.jetons_du_jour([]) == "—"
    assert extraire.RE_REVEIL_SANS_SUITE.search(
        "Retour en écoute (bouton) : conversation ouverte par « Joi, reviens », 0 tour(s) de la personne.")
    assert not extraire.RE_REVEIL_SANS_SUITE.search(
        "Retour en écoute (bouton) : conversation ouverte par « Joi, reviens », 3 tour(s) de la personne.")


def test_ligne_ecoute_transcrite_dit_le_modele_facture(monkeypatch, caplog):
    import asyncio

    dossier = config.DOSSIER_ENREGISTREMENTS / "2026-11-22"
    dossier.mkdir(parents=True, exist_ok=True)
    manifeste = {"debut": "2026-11-22T09:00:00", "fin": "2026-11-22T09:30:00",
                 "segments": [{"fichier": "2026-11-22/a.mp3", "debut": "2026-11-22T09:00:00", "etat": "enregistre",
                               "duree_s": 1800}]}
    (dossier / "seance-090000.json").write_text(json.dumps(manifeste), encoding="utf-8")

    async def faux_segment(client, chemin, manifeste, segment):
        segment.update(etat="transcrit", modele="universal-essai", cout=0.11)
        return 1800.0, 0.11

    monkeypatch.setattr(config, "ASSEMBLYAI_API_KEY", "cle-de-test")
    monkeypatch.setattr(transcription, "_transcrire_segment", faux_segment)
    with caplog.at_level(logging.INFO, logger="joi.transcription"):
        asyncio.run(transcription.transcrire_en_attente())
    ligne = next(r.getMessage() for r in caplog.records if "Écoute transcrite" in r.getMessage())
    assert ligne.endswith(", modèle universal-essai")
