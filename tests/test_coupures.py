"""Une journée d'écoute à l'épreuve des coupures : oreilles qui tombent, reconnexion, silence total,
et une réception d'AssemblyAI qui n'attend plus l'interruption du cerveau.

Un faux serveur AssemblyAI tourne en local (websockets) : aucun appel réseau."""

import asyncio
import json
import time

import pytest
from websockets.asyncio.server import serve

from joi import oreilles as module_oreilles
from joi import serveur
from joi.oreilles import Oreilles, Tour, coupure_recuperable


class FauxServeur:
    """Un faux AssemblyAI. scenarios : une coroutine par connexion, dans l'ordre (la dernière se répète)."""

    def __init__(self, *scenarios):
        self.scenarios = list(scenarios)
        self.connexions = 0
        self.recus: list = []

    async def _accueillir(self, ws):
        scenario = self.scenarios[min(self.connexions, len(self.scenarios) - 1)]
        self.connexions += 1
        await scenario(ws, self)

    async def __aenter__(self):
        self._serveur = await serve(self._accueillir, "127.0.0.1", 0)
        port = self._serveur.sockets[0].getsockname()[1]
        self._url = module_oreilles.URL_STREAMING
        module_oreilles.URL_STREAMING = f"ws://127.0.0.1:{port}"
        return self

    async def __aexit__(self, *_):
        module_oreilles.URL_STREAMING = self._url
        self._serveur.close()
        await self._serveur.wait_closed()


async def _begin(ws):
    await ws.send(json.dumps({"type": "Begin", "id": "faux", "configuration": {}}))


def _tour(ordre: int, texte: str) -> str:
    return json.dumps({"type": "Turn", "turn_order": ordre, "end_of_turn": True, "transcript": texte,
                       "words": [{"text": texte, "start": 0, "end": 500}]})


async def coupe_apres_un_tour(ws, serveur_):
    await _begin(ws)
    await ws.send(_tour(0, "Bonjour Joi."))
    await ws.close(3005, "Session Cancelled: An error occurred")


async def trois_heures(ws, serveur_):
    await _begin(ws)
    await ws.send(json.dumps({"type": "Error", "error": "Session exceeded maximum duration"}))
    await ws.close(3008, "See Error message for details")


async def cle_refusee(ws, serveur_):
    await ws.close(1008, "Invalid API key")


async def ferme_aussitot(ws, serveur_):
    await ws.close(1011, "Internal error")


async def reste_ouvert(ws, serveur_):
    """Une session normale : elle répond à Terminate par Termination, comme AssemblyAI."""
    await _begin(ws)
    async for message in ws:
        serveur_.recus.append(message)
        if isinstance(message, str) and json.loads(message).get("type") == "Terminate":
            await ws.send(json.dumps({"type": "Termination", "audio_duration_seconds": 1}))
            await ws.close()
            return


class Rappels:
    def __init__(self):
        self.tours: list[Tour] = []
        self.erreurs: list[str] = []
        self.coupures: list[tuple] = []
        self.coupee = asyncio.Event()

    async def sur_tour(self, tour):
        self.tours.append(tour)

    async def sur_erreur(self, message):
        self.erreurs.append(message)

    async def sur_coupure(self, code, raison):
        self.coupures.append((code, raison))
        self.coupee.set()

    def oreilles(self) -> Oreilles:
        return Oreilles(self.sur_tour, self.sur_erreur, self.sur_coupure)


# --- Oreilles ---------------------------------------------------------------------------------

def test_fermeture_inattendue_signalee():
    async def scenario():
        rappels = Rappels()
        async with FauxServeur(coupe_apres_un_tour):
            o = rappels.oreilles()
            await o.ouvrir()
            await asyncio.wait_for(rappels.coupee.wait(), 5)
            await o.envoyer_audio(b"\0" * 3200)  # plus d'envoi, sans exception
            await o.fermer()
        return rappels, o

    rappels, o = asyncio.run(scenario())
    assert [t.texte for t in rappels.tours] == ["Bonjour Joi."]
    assert rappels.tours[0].recu is not None
    assert rappels.coupures == [(3005, "Session Cancelled: An error occurred")]
    assert o.coupee
    assert rappels.erreurs == ["AssemblyAI a fermé la connexion (3005) : Session Cancelled: An error occurred"]


def test_session_de_trois_heures():
    async def scenario():
        rappels = Rappels()
        async with FauxServeur(trois_heures):
            o = rappels.oreilles()
            await o.ouvrir()
            await asyncio.wait_for(rappels.coupee.wait(), 5)
            await o.fermer()
        return rappels

    rappels = asyncio.run(scenario())
    # Le message d'erreur d'AssemblyAI est plus parlant que la raison de fermeture.
    assert rappels.coupures == [(3008, "Session exceeded maximum duration")]
    assert rappels.erreurs == ["AssemblyAI : Session exceeded maximum duration"]
    assert coupure_recuperable(*rappels.coupures[0])


def test_coupure_signalee_aussi_par_l_envoi():
    """Si l'envoi voit la coupure avant la réception, elle n'est quand même signalée qu'une fois."""
    async def scenario():
        rappels = Rappels()
        async with FauxServeur(coupe_apres_un_tour):
            o = rappels.oreilles()
            await o.ouvrir()
            await asyncio.sleep(0.2)
            for _ in range(5):
                await o.envoyer_audio(b"\0" * 3200)
            await asyncio.wait_for(rappels.coupee.wait(), 5)
            await asyncio.sleep(0.1)
            await o.fermer()
        return rappels

    assert len(asyncio.run(scenario()).coupures) == 1


def test_fermeture_voulue_pas_de_coupure():
    async def scenario():
        rappels = Rappels()
        async with FauxServeur(reste_ouvert) as faux:
            o = rappels.oreilles()
            await o.ouvrir()
            await o.envoyer_audio(b"\1" * 3200)
            await asyncio.sleep(0.1)
            await o.fermer()
            await asyncio.sleep(0.1)
        return rappels, faux

    rappels, faux = asyncio.run(scenario())
    assert rappels.coupures == [] and rappels.erreurs == []
    assert faux.recus[0] == b"\1" * 3200
    assert json.loads(faux.recus[-1]) == {"type": "Terminate"}


@pytest.mark.parametrize("code, raison, attendu", [
    (3008, "", True),
    (3005, "Session Cancelled", True),
    (1006, "", True),
    (1011, "", True),
    (1000, "", True),
    (None, "", True),
    (1008, "Invalid API key", False),
    (1008, "Too many concurrent sessions", True),
    (3006, "Invalid Message Type", False),
    (3006, "Session terminated due to inactivity", True),
    (3007, "Input duration violation", False),
])
def test_coupure_recuperable(code, raison, attendu):
    assert coupure_recuperable(code, raison) is attendu


# --- La conversation ---------------------------------------------------------------------------

class FauxNavigateur:
    """Le WebSocket du navigateur, vu du serveur : on garde ce qui lui est envoyé."""

    def __init__(self):
        self.messages: list[dict] = []

    async def send_json(self, message):
        self.messages.append(message)

    async def send_bytes(self, _audio):
        pass

    def erreurs(self) -> list[str]:
        return [m["message"] for m in self.messages if m["type"] == "erreur"]


async def _attendre(condition, delai=5.0):
    fin = time.monotonic() + delai
    while not condition():
        if time.monotonic() > fin:
            raise TimeoutError
        await asyncio.sleep(0.01)


def test_conversation_reconnecte_ses_oreilles(monkeypatch):
    monkeypatch.setattr(serveur, "DELAIS_RECONNEXION", (0.01, 0.02))

    async def scenario():
        navigateur = FauxNavigateur()
        conversation = serveur.Conversation(navigateur)
        tours: list[str] = []

        async def sur_tour(tour):
            tours.append(tour.texte)

        monkeypatch.setattr(conversation, "sur_tour", sur_tour)
        # 1re connexion : coupée par AssemblyAI ; 2e : ouverte puis aussitôt fermée ; 3e : tient.
        async with FauxServeur(coupe_apres_un_tour, ferme_aussitot, reste_ouvert) as faux:
            premieres = conversation.oreilles = conversation._nouvelles_oreilles()
            await premieres.ouvrir()
            await _attendre(lambda: faux.connexions >= 3)
            await asyncio.wait_for(conversation.tache_reconnexion, 5)
            assert conversation.oreilles is not premieres
            assert not conversation.oreilles.coupee
            assert faux.connexions == 3
            await conversation.oreilles.fermer()
            await asyncio.sleep(0.05)
        return navigateur, conversation, tours

    navigateur, conversation, tours = asyncio.run(scenario())
    assert tours == ["Bonjour Joi."]
    assert navigateur.erreurs()[-2:] == ["Je ne t'entends plus : je me reconnecte…", "Je t'entends de nouveau."]
    assert navigateur.messages[-1] == {"type": "etat", "etat": "ecoute"}


def test_reconnexion_abandonnee_si_la_cle_est_refusee(monkeypatch):
    monkeypatch.setattr(serveur, "DELAIS_RECONNEXION", (0.01,))

    async def scenario():
        navigateur = FauxNavigateur()
        conversation = serveur.Conversation(navigateur)
        async with FauxServeur(coupe_apres_un_tour, cle_refusee, reste_ouvert) as faux:
            conversation.oreilles = conversation._nouvelles_oreilles()
            await conversation.oreilles.ouvrir()
            await _attendre(lambda: faux.connexions >= 2)
            await asyncio.sleep(0.3)
            await conversation.oreilles.fermer()
        return navigateur, faux

    navigateur, faux = asyncio.run(scenario())
    assert faux.connexions == 2
    assert "Invalid API key" in navigateur.erreurs()[-1]


def _couper(o: Oreilles, code, raison) -> Oreilles:
    o.coupee, o.recuperable = True, coupure_recuperable(code, raison)
    return o


def test_pas_de_reconnexion_en_ecoute(monkeypatch):
    monkeypatch.setattr(serveur, "DELAIS_RECONNEXION", (0.01,))

    async def scenario():
        navigateur = FauxNavigateur()
        conversation = serveur.Conversation(navigateur)
        conversation.mode = "ecoute"
        conversation._oreilles_coupees(_couper(conversation.oreilles, 3005, ""), 3005, "")
        await asyncio.sleep(0.05)
        return conversation, navigateur

    conversation, navigateur = asyncio.run(scenario())
    assert conversation.tache_reconnexion is None and navigateur.messages == []


def test_pas_de_reconnexion_si_la_cle_est_refusee():
    async def scenario():
        navigateur = FauxNavigateur()
        conversation = serveur.Conversation(navigateur)
        conversation._oreilles_coupees(_couper(conversation.oreilles, 1008, "Invalid API key"), 1008,
                                       "Invalid API key")
        await asyncio.sleep(0.05)
        return conversation, navigateur

    conversation, navigateur = asyncio.run(scenario())
    assert conversation.tache_reconnexion is None
    assert "Invalid API key" in navigateur.erreurs()[0]


def test_anciennes_oreilles_ignorees():
    """Une coupure d'oreilles déjà remplacées (retour du mode écoute) ne relance rien."""
    async def scenario():
        conversation = serveur.Conversation(FauxNavigateur())
        anciennes = conversation.oreilles
        conversation.oreilles = conversation._nouvelles_oreilles()
        conversation._oreilles_coupees(_couper(anciennes, 3005, ""), 3005, "")
        return conversation

    assert asyncio.run(scenario()).tache_reconnexion is None


# --- Garde-fou de silence ---------------------------------------------------------------------

def test_garde_fou_silence():
    instant = [0.0]
    garde = serveur.GardeFouSilence(seuil_s=30, horloge=lambda: instant[0])
    instant[0] = 29
    assert garde.verifier() is None
    instant[0] = 31
    assert garde.verifier() == 31
    instant[0] = 60
    assert garde.verifier() is None  # signalé une seule fois
    assert garde.audio_recu() == 60  # l'audio revient : on sait combien de temps il a manqué
    assert garde.audio_recu() is None
    instant[0] = 80
    assert garde.verifier() is None
    instant[0] = 100
    garde.reprendre()
    instant[0] = 125
    assert garde.verifier() is None


def test_conversation_signale_le_silence_en_ecoute(caplog):
    instant = [1000.0]

    class FauxEnregistreur:
        def __init__(self):
            self.trous = []

        def ouvrir_trou(self, debut):
            self.trous.append(debut)

    async def scenario():
        navigateur = FauxNavigateur()
        conversation = serveur.Conversation(navigateur, horloge=lambda: instant[0])
        await conversation._verifier_silence()  # en conversation : rien
        instant[0] += 100
        await conversation._verifier_silence()
        assert navigateur.messages == []

        conversation.mode = "ecoute"
        conversation.enregistreur = FauxEnregistreur()
        conversation.garde_silence.reprendre()
        instant[0] += 20
        await conversation._verifier_silence()
        assert navigateur.messages == []
        instant[0] += 15
        await conversation._verifier_silence()
        await conversation._verifier_silence()
        return navigateur

    navigateur = asyncio.run(scenario())
    assert navigateur.erreurs() == ["Je n'entends plus rien depuis 35 s : le micro est-il toujours branché ?"]
    assert any("aucun audio reçu depuis 35 s" in r.getMessage() for r in caplog.records)


# --- La réception n'attend plus l'interruption ------------------------------------------------

def test_tour_final_ne_bloque_pas_la_reception(monkeypatch):
    evenements: list[str] = []

    async def scenario():
        conversation = serveur.Conversation(FauxNavigateur())
        monkeypatch.setattr(conversation, "_apprendre_ma_voix", lambda tour: None)

        occupe = [False]

        async def abort_lent():  # comme Cerveau.interrompre : rien à faire si le cerveau est inactif
            if not occupe[0]:
                return
            evenements.append("abort début")
            await asyncio.sleep(0.4)
            occupe[0] = False
            evenements.append("abort fin")

        async def repondre(texte, systeme=False, tour=None):
            evenements.append(f"réponse {texte}")
            occupe[0] = True
            await asyncio.sleep(10)

        monkeypatch.setattr(conversation.cerveau, "interrompre", abort_lent)
        monkeypatch.setattr(conversation, "repondre", repondre)

        await conversation.sur_tour(Tour("Première question.", True, 0))
        await asyncio.sleep(0.05)
        debut = time.perf_counter()
        await conversation.sur_tour(Tour("Deuxième question.", True, 1))
        await conversation.sur_tour(Tour("Troisième question.", True, 2))
        duree = time.perf_counter() - debut
        await asyncio.sleep(1)
        await conversation.interrompre()
        return duree

    duree = asyncio.run(scenario())
    assert duree < 0.1  # la réception n'a pas attendu les 0,4 s d'abort
    # L'ordre est gardé : la réponse suivante ne part qu'après l'arrêt de la précédente, et la
    # deuxième, interrompue avant d'avoir commencé, ne part jamais.
    assert evenements[:4] == ["réponse Première question.", "abort début", "abort fin", "réponse Troisième question."]
    assert "réponse Deuxième question." not in evenements


# --- Réglages de la fin de tour ---------------------------------------------------------------

def test_fin_de_tour_envoyee_seulement_si_reglee(monkeypatch):
    from joi import config

    chemins: list[str] = []

    async def noter_chemin(ws, serveur_):
        chemins.append(ws.request.path)
        await reste_ouvert(ws, serveur_)

    async def ouvrir_et_fermer():
        async with FauxServeur(noter_chemin):
            o = Rappels().oreilles()
            await o.ouvrir()
            await o.fermer()

    asyncio.run(ouvrir_et_fermer())
    monkeypatch.setattr(config, "ASSEMBLYAI_FIN_TOUR_MODE", "min_latency")
    monkeypatch.setattr(config, "ASSEMBLYAI_SILENCE_MIN_MS", 160)
    monkeypatch.setattr(config, "ASSEMBLYAI_SILENCE_MAX_MS", 900)
    asyncio.run(ouvrir_et_fermer())

    sans, avec = chemins
    assert "mode=" not in sans and "turn_silence" not in sans  # comme avant : les valeurs d'AssemblyAI
    assert "mode=min_latency" in avec and "min_turn_silence=160" in avec and "max_turn_silence=900" in avec


# --- Plan Joi fiable, partie 3 : une écoute muette qui se voit -------------------------------------

def test_garde_fou_alertes_repetees():
    """À 35 s, à 5 min, puis toutes les 15 min (20 et 35 min) ; plus rien après le retour de l'audio."""
    instant = [0.0]
    garde = serveur.GardeFouSilence(seuil_s=30, horloge=lambda: instant[0])
    alertes = []
    for t in range(0, 2200, 5):
        instant[0] = t
        if garde.verifier() is not None:
            alertes.append(t)
    assert alertes == [35, 305, 1205, 2105]
    instant[0] = 2200
    assert garde.audio_recu() == 2200
    instant[0] = 2220
    assert garde.verifier() is None
    instant[0] = 2235
    assert garde.verifier() == 35  # un nouveau trou : le calendrier repart de zéro


def test_page_cachee_ecrite_au_journal_en_ecoute(caplog):
    import json
    import logging

    async def scenario():
        conversation = serveur.Conversation(FauxNavigateur())
        avant = conversation._derniere_activite
        with caplog.at_level(logging.INFO, logger="joi.serveur"):
            await conversation.traiter_commande(json.dumps({"type": "onglet", "etat": "cachee"}))
            conversation.mode = "ecoute"
            await conversation.traiter_commande(json.dumps({"type": "onglet", "etat": "verrou d'éveil relâché"}))
            await conversation.traiter_commande(json.dumps({"type": "onglet", "etat": "<script>\nfaux"}))
        return conversation, avant

    conversation, avant = asyncio.run(scenario())
    messages = [r.getMessage() for r in caplog.records if "Écoute : page" in r.getMessage()]
    assert messages == ["Écoute : page verrou d'éveil relâché.", "Écoute : page scriptfaux."]
    assert conversation._derniere_activite == avant  # l'état de la page n'est pas une activité
