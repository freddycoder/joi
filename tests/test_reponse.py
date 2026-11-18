"""Une réponse de Joi, avec un faux cerveau et une fausse voix : l'ordre de ce qui part vers le
navigateur, et la mesure de la latence."""

import asyncio
import logging
import time

import pytest

from joi import monitoring, serveur, voix
from joi.oreilles import Tour


class FauxNavigateur:
    def __init__(self):
        self.envois: list = []  # messages JSON (dict) et audio (bytes), dans l'ordre

    async def send_json(self, message):
        self.envois.append(message)

    async def send_bytes(self, audio):
        self.envois.append(audio)

    def audio(self) -> list[bytes]:
        return [e for e in self.envois if isinstance(e, bytes)]

    def sous_titres(self) -> list[str]:
        return [e["texte"] for e in self.envois if isinstance(e, dict) and e["type"] == "reponse"]


class FauxCerveau:
    """Produit des phrases à intervalles donnés, comme Cerveau.repondre."""

    def __init__(self, phrases: list[tuple[float, str]]):
        self.phrases = phrases
        self.envoi = self.premier_token = None

    async def repondre(self, texte, systeme=False, note=None):
        self.envoi, self.premier_token = time.monotonic(), None
        for attente, phrase in self.phrases:
            await asyncio.sleep(attente)
            if self.premier_token is None:
                self.premier_token = time.monotonic()
            yield phrase

    async def interrompre(self):
        pass


@pytest.fixture
def fausse_voix(monkeypatch):
    """Une synthèse qui prend 0,1 s par phrase (0 si la phrase commence par « Vite »)."""
    appels: list[tuple[str, float]] = []

    async def synthetiser(texte, infos=None):
        debut = time.monotonic()
        appels.append((texte, debut))
        await asyncio.sleep(0 if texte.startswith("Vite") else 0.1)
        if infos is not None:
            infos.update(cache=texte.startswith("Vite"), duree_s=time.monotonic() - debut)
        return texte.encode()

    monkeypatch.setattr(voix, "synthetiser", synthetiser)
    return appels


def _conversation(phrases) -> tuple[serveur.Conversation, FauxNavigateur]:
    navigateur = FauxNavigateur()
    conversation = serveur.Conversation(navigateur)
    conversation.cerveau = FauxCerveau(phrases)
    return conversation, navigateur


def test_mesure_de_la_latence(fausse_voix, caplog):
    monitoring.latences.clear()

    async def scenario():
        conversation, navigateur = _conversation([(0.2, "Bonjour."), (0.05, "Ça va ?")])
        maintenant = time.monotonic()
        tour = Tour("Salut Joi.", True, 0, [], recu=maintenant, fin_parole=maintenant - 0.6)
        with caplog.at_level(logging.INFO, logger="joi.serveur"):
            await conversation.repondre(tour.texte, tour=tour)
            assert not monitoring.latences  # on attend que le navigateur commence à jouer
            await asyncio.sleep(0.05)
            conversation._noter_lecture(True)
        return navigateur

    navigateur = asyncio.run(scenario())
    assert navigateur.sous_titres() == ["Bonjour.", "Ça va ?"]
    assert navigateur.audio() == [b"Bonjour.", b"\xc3\x87a va ?"]
    mesure, = monitoring.latences
    assert mesure["fin_de_tour"] == pytest.approx(0.6, abs=0.02)
    assert mesure["premier_mot"] == pytest.approx(0.2, abs=0.05)
    assert mesure["premiere_phrase"] == pytest.approx(0.2, abs=0.05)
    assert mesure["synthese"] == pytest.approx(0.1, abs=0.05) and mesure["cache"] is False
    assert mesure["lecture"] >= 0.05
    assert mesure["total"] >= 0.6 + 0.2 + 0.1
    ligne, = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Latence :")]
    assert "fin de tour 0.6 s" in ligne and "(fin de parole → premier son)" in ligne
    resume = monitoring.resume_latences()
    assert resume["reponses"] == 1 and resume["mediane"]["fin_de_tour"] == mesure["fin_de_tour"]


def test_mesure_sans_lecture_notee_a_la_reponse_suivante(fausse_voix):
    monitoring.latences.clear()

    async def scenario():
        conversation, _ = _conversation([(0, "Vite.")])
        await conversation.repondre("Un.")
        assert not monitoring.latences
        await conversation.repondre("Deux.")  # le navigateur n'a jamais signalé la lecture
        assert len(monitoring.latences) == 1
        conversation._noter_lecture(True)

    asyncio.run(scenario())
    assert len(monitoring.latences) == 2
    assert monitoring.latences[0]["lecture"] is None and monitoring.latences[1]["cache"] is True


def test_message_systeme_pas_mesure(fausse_voix):
    monitoring.latences.clear()

    async def scenario():
        conversation, _ = _conversation([(0, "Tu as reçu un courriel.")])
        await conversation.repondre("[courriel reçu]", systeme=True)
        conversation._noter_lecture(True)

    asyncio.run(scenario())
    assert not monitoring.latences


# --- Synthèse en parallèle --------------------------------------------------------------------

def test_syntheses_en_parallele_envoyees_dans_l_ordre(fausse_voix):
    phrases = [(0, "Première phrase."), (0, "Vite, la deuxième."), (0, "Troisième."), (0, "Quatrième.")]

    async def scenario():
        conversation, navigateur = _conversation(phrases)
        debut = time.monotonic()
        await conversation.repondre("Vas-y.")
        return navigateur, time.monotonic() - debut

    navigateur, duree = asyncio.run(scenario())
    attendu = [p for _, p in phrases]
    assert navigateur.sous_titres() == attendu
    assert navigateur.audio() == [p.encode() for p in attendu]
    # Trois synthèses de 0,1 s : en série, 0,3 s ; en parallèle, à peine plus de 0,1 s.
    assert duree < 0.25, duree
    debuts = [t for _, t in fausse_voix]
    assert max(debuts) - min(debuts) < 0.05  # toutes lancées dès l'arrivée de leur phrase
    # Le sous-titre d'une phrase part juste avant sa voix.
    envois = navigateur.envois
    for phrase in attendu:
        i = envois.index({"type": "reponse", "texte": phrase})
        assert envois[i + 1] == phrase.encode() or envois[i + 1] == {"type": "etat", "etat": "parle"}
    assert envois[-1] == {"type": "fin_reponse"}


def test_interruption_annule_les_syntheses(fausse_voix, monkeypatch):
    annulees: list[str] = []

    async def synthese_lente(texte, infos=None):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            annulees.append(texte)
            raise
        return b""

    monkeypatch.setattr(voix, "synthetiser", synthese_lente)

    async def scenario():
        conversation, navigateur = _conversation([(0, "Une."), (0, "Deux.")])
        conversation.tache_reponse = asyncio.create_task(conversation.repondre("Parle."))
        await asyncio.sleep(0.1)
        await conversation.interrompre()
        return navigateur

    navigateur = asyncio.run(scenario())
    assert sorted(annulees) == ["Deux.", "Une."]
    assert navigateur.audio() == []
    assert {"type": "fin_reponse"} not in navigateur.envois


def test_une_synthese_ratee_ne_perd_pas_la_reponse(monkeypatch):
    async def synthese(texte, infos=None):
        if texte == "Raté.":
            raise RuntimeError("TTS indisponible")
        return texte.encode()

    monkeypatch.setattr(voix, "synthetiser", synthese)

    async def scenario():
        conversation, navigateur = _conversation([(0, "Raté."), (0, "Réussi.")])
        await conversation.repondre("Essaie.")
        return navigateur

    navigateur = asyncio.run(scenario())
    assert navigateur.sous_titres() == ["Raté.", "Réussi."]
    assert navigateur.audio() == ["Réussi.".encode()]
    assert navigateur.envois[-1] == {"type": "fin_reponse"}


def test_journal_seulement_ce_qui_est_parti(monkeypatch):
    """Interrompue pendant la synthèse, Joi ne journalise pas les phrases jamais montrées ni dites."""
    import json

    from joi import config

    async def synthese(texte, infos=None):
        await asyncio.sleep(0 if texte == "Une." else 5)
        return texte.encode()

    monkeypatch.setattr(voix, "synthetiser", synthese)

    async def scenario():
        conversation, navigateur = _conversation([(0, "Une."), (0, "Deux."), (0, "Trois.")])
        conversation.tache_reponse = asyncio.create_task(conversation.repondre("Parle."))
        await asyncio.sleep(0.2)
        await conversation.interrompre()
        return navigateur

    navigateur = asyncio.run(scenario())
    entrees = [json.loads(l) for f in config.DOSSIER_JOURNAL.glob("*.jsonl")
               for l in f.read_text(encoding="utf-8").splitlines()]
    joi, = [e for e in entrees if e["qui"] == "joi"]
    # « Deux. » a son sous-titre (elle attendait sa voix), « Trois. » n'est jamais parti.
    assert navigateur.sous_titres() == ["Une.", "Deux."]
    assert joi["texte"] == "Une. Deux." and joi["interrompu"] is True


def test_phrase_d_attente_hors_du_journal_meme_si_le_cerveau_la_redit(fausse_voix, monkeypatch):
    import json

    from joi import cerveau, config

    monkeypatch.setattr(serveur, "PHRASES_DE_RECHERCHE", ["Laisse-moi me rappeler…"])

    class CerveauQuiCherche(FauxCerveau):
        async def repondre(self, texte, systeme=False, note=None):
            self.envoi = self.premier_token = time.monotonic()
            yield cerveau.RECHERCHE("chat")
            yield "Laisse-moi me rappeler…"  # la même phrase, dite par le cerveau cette fois
            yield "Ton chat s'appelle Pixel."

    async def scenario():
        conversation, navigateur = _conversation([])
        conversation.cerveau = CerveauQuiCherche([])
        await conversation.repondre("Comment s'appelle mon chat ?")
        return navigateur

    navigateur = asyncio.run(scenario())
    assert navigateur.sous_titres() == ["Laisse-moi me rappeler…"] * 2 + ["Ton chat s'appelle Pixel."]
    entrees = [json.loads(l) for f in config.DOSSIER_JOURNAL.glob("*.jsonl")
               for l in f.read_text(encoding="utf-8").splitlines()]
    joi, = [e for e in entrees if e["qui"] == "joi"]
    assert joi["texte"] == "Laisse-moi me rappeler… Ton chat s'appelle Pixel."
