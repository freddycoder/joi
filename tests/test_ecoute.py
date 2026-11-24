"""Le mode écoute : enregistreur, manifestes, et ce que la transcription en tire."""

import json

import pytest

from joi import config, enregistreur, transcription
from joi.enregistreur import OCTETS_PAR_SECONDE, Enregistreur

DEMI_SECONDE = bytes(OCTETS_PAR_SECONDE // 2)  # du silence : PCM 16 bits à zéro


# --- Enregistreur -----------------------------------------------------------------------------

@pytest.fixture
def dossier(tmp_path):
    return tmp_path / "enregistrements"


def test_segments_qui_tournent(dossier):
    finis: list[float] = []
    e = Enregistreur(dossier=dossier, duree_segment_s=1.0, sur_segment_fini=finis.append)
    e.ouvrir()
    assert [s["etat"] for s in e.manifeste["segments"]] == ["en_cours"]

    for _ in range(5):  # 2,5 s d'audio
        e.ecrire(DEMI_SECONDE)

    assert finis == [1.0, 1.0]
    segments = e.manifeste["segments"]
    assert [s["etat"] for s in segments] == ["enregistre", "enregistre", "en_cours"]
    assert [s["duree_s"] for s in segments[:2]] == [1.0, 1.0]
    assert len({s["fichier"] for s in segments}) == 3  # deux segments dans la même seconde : -2, -3
    for s in segments[:2]:
        assert (dossier / s["fichier"]).stat().st_size > 0

    # Pendant l'écoute, seances() renvoie le manifeste vivant, marqué en cours.
    (chemin, manifeste, active), = enregistreur.seances(dossier)
    assert manifeste is e.manifeste and active
    assert enregistreur.seances_terminees(dossier) == []

    e.fermer()
    assert e.manifeste["fin"] is not None
    assert [s["etat"] for s in e.manifeste["segments"]] == ["enregistre"] * 3
    assert e.manifeste["segments"][-1]["duree_s"] == 0.5
    (chemin, manifeste, active), = enregistreur.seances(dossier)
    assert not active
    assert manifeste == e.manifeste  # relu sur le disque
    assert finis == [1.0, 1.0]  # la fermeture n'appelle pas sur_segment_fini


def test_segment_vide_retire(dossier):
    e = Enregistreur(dossier=dossier, duree_segment_s=1.0)
    e.ouvrir()
    for _ in range(2):  # exactement 1 s : le segment tourne, le suivant reste vide
        e.ecrire(DEMI_SECONDE)
    vide = e.manifeste["segments"][-1]["fichier"]
    assert (dossier / vide).exists()
    e.fermer()
    assert len(e.manifeste["segments"]) == 1
    assert not (dossier / vide).exists()
    manifeste = json.loads(next(dossier.glob("*/seance-*.json")).read_text(encoding="utf-8"))
    assert len(manifeste["segments"]) == 1


def test_fermer_sans_audio(dossier):
    e = Enregistreur(dossier=dossier, duree_segment_s=1.0)
    e.ouvrir()
    e.fermer()
    assert e.manifeste["segments"] == [] and e.manifeste["fin"]
    assert not e.ouvert
    e.ecrire(DEMI_SECONDE)  # après la fermeture : ignoré
    e.fermer()


# --- transcription.entrees_du_segment ---------------------------------------------------------

SEGMENT = {"fichier": "2026-11-16/090000.mp3", "debut": "2026-11-16T09:00:00.000000"}


def test_entrees_du_segment_horodatages_strictement_croissants():
    resultat = {"utterances": [
        {"start": 1000, "speaker": "A", "text": "Bonjour."},
        {"start": 1000, "speaker": "B", "text": "Salut."},  # même instant
        {"start": 500, "speaker": "A", "text": "Avant ?"},  # plus tôt (ne devrait pas arriver)
        {"start": 2000, "speaker": "B", "text": "  "},  # vide : ignorée
        {"start": 61_000, "speaker": "A", "text": "Une minute plus tard."},
    ]}
    entrees = transcription.entrees_du_segment(SEGMENT, resultat)
    assert [e["texte"] for e in entrees] == ["Bonjour.", "Salut.", "Avant ?", "Une minute plus tard."]
    horodatages = [e["ts"] for e in entrees]
    assert all(a < b for a, b in zip(horodatages, horodatages[1:]))
    assert horodatages[0] == "2026-11-16T09:00:01.000000"
    assert horodatages[-1] == "2026-11-16T09:01:01.000000"
    assert {e["source"] for e in entrees} == {"ecoute"} and {e["qui"] for e in entrees} == {"entendu"}
    assert [e["locuteur"] for e in entrees][:2] == ["A", "B"]


def test_entrees_du_segment_repli_sans_utterances():
    entrees = transcription.entrees_du_segment(SEGMENT, {"text": "Tout le texte d'un coup."})
    assert len(entrees) == 1
    assert entrees[0]["locuteur"] == "?" and entrees[0]["ts"] == "2026-11-16T09:00:00.000000"
    assert transcription.entrees_du_segment(SEGMENT, {"text": ""}) == []
    assert transcription.entrees_du_segment(SEGMENT, {}) == []


# --- _fin_segment et _journee_terminee --------------------------------------------------------

def test_fin_segment():
    assert transcription._fin_segment({"debut": "2026-11-16T09:00:00.5", "duree_s": 90}) == "2026-11-16T09:01:30"
    assert transcription._fin_segment({"debut": "2026-11-16T09:00:00", "duree_s": 0}) is None
    assert transcription._fin_segment({"debut": "2026-11-16T09:00:00"}) is None
    assert transcription._fin_segment(None) is None


def _manifeste(jour: str, nom: str, etats: list[str], fin: str | None = "x") -> dict:
    manifeste = {
        "debut": f"{jour}T{nom[:2]}:00:00",
        "fin": fin,
        "segments": [{"fichier": f"{jour}/{nom}{i}.mp3", "debut": f"{jour}T09:00:00", "duree_s": 60.0, "etat": e}
                     for i, e in enumerate(etats)],
    }
    dossier = config.DOSSIER_ENREGISTREMENTS / jour
    dossier.mkdir(parents=True, exist_ok=True)
    enregistreur.sauver_manifeste(dossier / f"seance-{nom}.json", manifeste)
    return manifeste


def test_journee_terminee():
    _manifeste("2026-11-15", "0900", ["consolide", "transcrit"])
    _manifeste("2026-11-16", "0900", ["consolide", "echec"])
    _manifeste("2026-11-16", "1400", ["consolide"])
    assert not transcription._journee_terminee("2026-11-15")  # un segment pas encore appris
    assert transcription._journee_terminee("2026-11-16")
    assert transcription._journee_terminee("2026-11-14")  # aucune écoute ce jour-là


def test_journee_pas_terminee_pendant_une_ecoute():
    _manifeste("2026-11-16", "0900", ["consolide"])
    vivant = _manifeste("2026-11-16", "1400", ["consolide", "en_cours"], fin=None)
    chemin = (config.DOSSIER_ENREGISTREMENTS / "2026-11-16" / "seance-1400.json").resolve()
    enregistreur._seances_actives[chemin] = vivant
    assert not transcription._journee_terminee("2026-11-16")
    del enregistreur._seances_actives[chemin]
    vivant["segments"][-1]["etat"] = "consolide"  # le manifeste vivant n'est plus sur le disque…
    assert not transcription._journee_terminee("2026-11-16")  # … celui du disque a encore « en_cours »


# --- Plan Joi fiable, partie 3 : les trous d'écoute ----------------------------------------------

def test_trou_dans_le_manifeste_et_la_ligne_de_fin(dossier, caplog):
    import logging
    from datetime import datetime, timedelta

    e = Enregistreur(dossier=dossier, duree_segment_s=60.0)
    e.ouvrir()
    debut = datetime.fromisoformat(e.manifeste["debut"])
    e.manifeste["debut"] = (debut - timedelta(minutes=187)).isoformat(timespec="seconds")
    e.ouvrir_trou(datetime(2026, 11, 20, 13, 10, 22))
    e.ouvrir_trou(datetime(2026, 11, 20, 13, 11, 0))  # déjà ouvert : rien ne change
    assert len(e.manifeste["trous"]) == 1
    assert e.fermer_trou(datetime(2026, 11, 20, 14, 46, 3)) == (datetime(2026, 11, 20, 13, 10, 22),
                                                                 datetime(2026, 11, 20, 14, 46, 3))
    assert e.fermer_trou(datetime(2026, 11, 20, 14, 50)) is None  # rien d'ouvert
    e.ouvrir_trou(datetime.now() - timedelta(seconds=40))  # un trou encore ouvert à la fin
    with caplog.at_level(logging.INFO, logger="joi.enregistreur"):
        e.fermer()
    trous = e.manifeste["trous"]
    assert trous[0]["duree_s"] == 5741 and trous[1]["fin"] is not None
    ligne = next(r.getMessage() for r in caplog.records if "Écoute terminée" in r.getMessage())
    assert "sur 187 min de séance" in ligne and "96 min sans audio" in ligne and "13:10 → 14:46" in ligne
    relu = json.loads(next(dossier.glob("*/seance-*.json")).read_text(encoding="utf-8"))
    assert relu["trous"][0]["fin"] == "2026-11-20T14:46:03"
    assert transcription.trous_a_dire(relu) == [("13:10", "14:46")]  # le trou de 40 s n'est pas dit


def test_ligne_de_fin_sans_trou(dossier, caplog):
    import logging

    e = Enregistreur(dossier=dossier, duree_segment_s=1.0)
    e.ouvrir()
    with caplog.at_level(logging.INFO, logger="joi.enregistreur"):
        e.fermer()
    ligne = next(r.getMessage() for r in caplog.records if "Écoute terminée" in r.getMessage())
    assert "min de séance" in ligne and "sans audio" not in ligne
