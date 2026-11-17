"""Le journal : horodatages, phrases entendues, recherche."""

import json
from datetime import datetime

from joi import config, journal


def _lignes(jour: str) -> list[dict]:
    fichier = config.DOSSIER_JOURNAL / f"{jour}.jsonl"
    return [json.loads(l) for l in fichier.read_text(encoding="utf-8").splitlines()]


def test_maintenant_strictement_croissant_meme_si_l_horloge_se_repete(monkeypatch):
    class HorlogeFigee(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 17, 9, 0, 0)

    monkeypatch.setattr(journal, "datetime", HorlogeFigee)
    monkeypatch.setattr(journal, "_dernier_instant", None)
    instants = [journal._maintenant() for _ in range(50)]
    assert instants == sorted(instants)
    assert len(set(instants)) == len(instants)


def test_maintenant_croissant_en_vrai():
    instants = [journal._maintenant() for _ in range(2000)]
    assert all(a < b for a, b in zip(instants, instants[1:]))


def _entendue(ts: str, texte: str) -> dict:
    return {"ts": ts, "qui": "entendu", "locuteur": "A", "texte": texte, "source": "ecoute"}


def test_noter_entendu_sans_doublon_a_la_reprise():
    entrees = [
        _entendue("2026-11-16T23:59:58.000000", "Il est presque minuit."),
        _entendue("2026-11-17T00:00:02.000000", "C'est demain."),
    ]
    journal.noter_entendu(entrees)
    journal.noter_entendu(entrees)  # la transcription du segment reprise après un plantage
    assert [e["texte"] for e in _lignes("2026-11-16")] == ["Il est presque minuit."]
    assert [e["texte"] for e in _lignes("2026-11-17")] == ["C'est demain."]


def test_noter_entendu_garde_une_phrase_redite_a_un_autre_instant():
    journal.noter_entendu([_entendue("2026-11-16T10:00:00.000000", "Oui.")])
    journal.noter_entendu([_entendue("2026-11-16T10:00:05.000000", "Oui.")])
    assert len(_lignes("2026-11-16")) == 2


def test_entrees_depuis_sans_les_entrees_d_ecoute():
    journal.noter("toi", "Bonjour Joi.")
    journal.noter("joi", "Bonjour !")
    jour = datetime.now().strftime("%Y-%m-%d")
    journal.noter_entendu([_entendue(f"{jour}T00:00:01.000000", "Une phrase entendue.")])

    toutes = journal.entrees_depuis(None)
    assert [e["texte"] for e in toutes] == ["Bonjour Joi.", "Bonjour !"]
    # Strictement après : la première n'y est plus.
    assert [e["texte"] for e in journal.entrees_depuis(toutes[0]["ts"])] == ["Bonjour !"]
    assert journal.entrees_depuis(toutes[1]["ts"]) == []


def test_noter_ignore_un_texte_vide():
    journal.noter("toi", "   ")
    assert list(config.DOSSIER_JOURNAL.glob("*.jsonl")) == []


def test_marquer_consolide():
    assert journal.derniere_consolidation() is None
    journal.marquer_consolide("2026-11-17T09:00:00.000000")
    assert journal.derniere_consolidation() == "2026-11-17T09:00:00.000000"


def test_normaliser():
    assert journal.normaliser("Été, SŒUR et cæcum") == "ete, soeur et caecum"
    assert journal.normaliser("l’atelier") == "l'atelier"


def test_racines():
    assert journal.racines("déménagé déménagement") == ["demena"]
    assert journal.racines("Où est le chat Pixel ?") == ["chat", "pixel"]
    # Rien que des mots vides : on les garde plutôt que de chercher rien.
    assert journal.racines("dans les") == ["dans", "les"]


def test_chercher_les_plus_pertinentes_d_abord():
    journal.noter("toi", "Le chat dort.")
    journal.noter("toi", "Le chat Pixel dort sur le chantier.")
    journal.noter("toi", "Rien à voir.")
    trouvees = journal.chercher(journal.racines("chat Pixel"), limite=5)
    assert [e["texte"] for e in trouvees] == ["Le chat Pixel dort sur le chantier.", "Le chat dort."]
