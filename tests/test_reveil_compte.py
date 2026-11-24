"""Le réveil du compte (partie 5 du plan Joi attentive) : un contexte gardé en mémoire et rafraîchi en
fond, et des appels au fournisseur en parallèle. Un faux fournisseur lent (0,3 s par appel), sans réseau.
"""

import asyncio
import time
from datetime import datetime, timedelta

import pytest

from joi.compte import reglages, veille
from joi.compte.fournisseur import CompteDeconnecte, ErreurCompte
from joi.compte.modele import Courriel, Tache, maintenant

PAUSE = 0.3


class FauxLent:
    """Chaque appel prend PAUSE secondes. `panne` : l'exception à lever (pour tous les appels)."""

    nom = "faux"

    def __init__(self):
        self.appels: list[str] = []
        self.panne: Exception | None = None
        self.sujet = "Bonjour"

    def _attendre(self, nom: str) -> None:
        self.appels.append(nom)
        time.sleep(PAUSE)
        if self.panne:
            raise self.panne

    def adresse(self):
        return "joi@exemple.org"

    def courriels_depuis(self, depuis):
        self._attendre("depuis")
        return []

    def courriels_recents(self, non_lus_seulement, nombre):
        self._attendre("non_lus")
        return [Courriel(id="m1", de_nom="Alice Martin", de_adresse="alice@exemple.org", sujet=self.sujet,
                         recu=maintenant())]

    def agenda(self, debut, fin, qui):
        self._attendre("agenda")
        return [], []

    def taches(self):
        self._attendre("taches")
        return [Tache(id="t1", titre="Rappeler le garage")]


@pytest.fixture
def faux():
    return FauxLent()


@pytest.fixture(autouse=True)
def midi(monkeypatch):
    """Midi, toujours : reculer le contexte gardé de 20 ou 31 min ne doit pas le faire tomber la veille
    quand les tests tournent juste après minuit."""
    instant = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    debut = datetime.now().astimezone()

    def maintenant_fige():
        return instant + (datetime.now().astimezone() - debut)

    monkeypatch.setattr(veille, "maintenant", maintenant_fige)


def test_premier_contexte_en_parallele(faux):
    v = veille.Veille(faux)
    debut = time.monotonic()
    texte = asyncio.run(v.contexte_reveil())
    duree = time.monotonic() - debut
    # Quatre appels de 0,3 s : en série, 1,2 s ; en parallèle, à peine plus de 0,3 s.
    assert duree < 2.5 * PAUSE, duree
    assert sorted(faux.appels) == ["agenda", "depuis", "non_lus", "taches"]
    assert "Bonjour" in texte and "Rappeler le garage" in texte
    assert "Nous sommes le" in texte


def test_deuxieme_reveil_depuis_le_cache(faux):
    async def scenario():
        v = veille.Veille(faux)
        await v.preparer()
        faux.appels.clear()
        faux.sujet = "Nouveau sujet"
        debut = time.monotonic()
        texte = await v.contexte_reveil()
        duree = time.monotonic() - debut
        # Le rafraîchissement part en fond : on l'attend pour vérifier qu'il a eu lieu.
        await v.attendre_rafraichissement()
        appels = sorted(faux.appels)
        apres = await v.contexte_reveil()
        await v.attendre_rafraichissement()
        return texte, duree, appels, apres

    texte, duree, appels, apres = asyncio.run(scenario())
    assert duree < PAUSE / 2, duree
    assert "Bonjour" in texte and "Nouveau sujet" not in texte
    assert "(vu à " in texte
    # Le rafraîchissement en fond ne prend pas les nouveaux courriels (la vérification régulière s'en
    # charge et les annonce) : seulement non lus, agenda et tâches.
    assert appels == ["agenda", "non_lus", "taches"]
    assert "Nouveau sujet" in apres


def test_cache_trop_vieux_ou_d_hier(faux):
    async def scenario():
        v = veille.Veille(faux)
        await v.preparer()
        v._cache.quand -= timedelta(minutes=31)  # plus de CACHE_SERVI_MAX
        faux.appels.clear()
        await v.contexte_reveil()
        vieux = sorted(faux.appels)
        await v.preparer()
        v._cache.quand -= timedelta(days=1)  # « aujourd'hui » n'est plus le même jour
        faux.appels.clear()
        await v.contexte_reveil()
        return vieux, sorted(faux.appels)

    vieux, hier = asyncio.run(scenario())
    assert vieux == ["agenda", "depuis", "non_lus", "taches"]
    assert hier == ["agenda", "depuis", "non_lus", "taches"]


def test_cache_independant_de_la_verification_des_courriels(faux, monkeypatch):
    """La vérification des courriels peut être horaire (ou nulle) : le cache, lui, suit son propre
    rythme (RAFRAICHIR_MINUTES), et sert encore au bout de 5 minutes."""
    monkeypatch.setattr(reglages, "VERIFIER_MINUTES", 60.0)

    async def scenario():
        v = veille.Veille(faux)
        await v.preparer()
        v._cache.quand -= timedelta(minutes=veille.RAFRAICHIR_MINUTES)
        faux.appels.clear()
        await v.contexte_reveil()
        await v.attendre_rafraichissement()
        return sorted(faux.appels)

    assert asyncio.run(scenario()) == ["agenda", "non_lus", "taches"]


def test_erreur_pendant_le_rafraichissement_garde_l_ancien(faux, caplog):
    async def scenario():
        v = veille.Veille(faux)
        await v.preparer()
        faux.panne = ErreurCompte("Microsoft ne répond pas")
        faux.sujet = "Perdu"
        await v.rafraichir()
        return v

    v = asyncio.run(scenario())
    faux.panne = None
    texte = asyncio.run(v.contexte_reveil())
    assert "Bonjour" in texte and "pas disponible" not in texte
    assert "je garde celui de" in caplog.text
    assert "Microsoft ne répond pas" in v.etat()["derniere_erreur"]


def test_une_deconnexion_d_un_rafraichissement_garde_l_ancien(faux, caplog):
    """Plan Joi fiable, partie 2 : une coupure d'une minute ne fait plus un contexte « déconnecté »."""
    async def scenario():
        v = veille.Veille(faux)
        await v.preparer()
        faux.panne = CompteDeconnecte("jeton expiré")
        await v.rafraichir()
        faux.panne = None
        faux.appels.clear()
        return await v.contexte_reveil()

    texte = asyncio.run(scenario())
    assert "ne fonctionne pas pour l'instant" not in texte and "Bonjour" in texte
    assert "je garde le contexte de" in caplog.text


def test_deux_echecs_rapproches_gardent_l_ancien(faux):
    """Le 20 novembre : six échecs en une minute (rafraîchissement, réveil, fin de conversation)."""
    async def scenario():
        v = veille.Veille(faux)
        await v.preparer()
        faux.panne = CompteDeconnecte("jeton expiré")
        await v.rafraichir()
        await v.rafraichir()
        await v.rafraichir()
        faux.panne = None
        await v.rafraichir()
        return await v.contexte_reveil()

    texte = asyncio.run(scenario())
    assert "ne fonctionne pas" not in texte and "coupé de" not in texte


def test_une_deconnexion_qui_dure_remplace_le_cache(faux):
    async def scenario():
        v = veille.Veille(faux)
        await v.preparer()
        faux.panne = CompteDeconnecte("jeton expiré")
        await v.rafraichir()
        v._coupure_debut -= veille.DUREE_AVANT_DECONNEXION  # les échecs durent depuis assez longtemps
        await v.rafraichir()
        faux.panne = None
        premier = await v.contexte_reveil()
        await v.attendre_rafraichissement()
        return premier

    premier = asyncio.run(scenario())
    assert "ne fonctionne pas pour l'instant" in premier
    assert "dès ta première réplique" in premier


def test_deconnexion_dite_une_fois_par_jour(faux):
    async def scenario():
        v = veille.Veille(faux)
        faux.panne = CompteDeconnecte("jeton expiré")
        v._coupure_debut = maintenant() - veille.DUREE_AVANT_DECONNEXION  # les échecs durent déjà
        textes = [await v.contexte_reveil()]
        v.noter_dites()  # la session est créée
        textes.append(await v.contexte_reveil())
        v._deconnexion_dite_le = "2000-01-01"  # un autre jour
        textes.append(await v.contexte_reveil())
        return textes

    premier, deuxieme, lendemain = asyncio.run(scenario())
    assert all("ne fonctionne pas pour l'instant" in t for t in (premier, deuxieme, lendemain))
    assert "dès ta première réplique" in premier
    assert "dès ta première réplique" not in deuxieme
    assert "Si la personne te parle de tes courriels" in deuxieme
    assert "Si la personne te parle de tes courriels" not in premier  # une seule consigne à la fois
    assert "dès ta première réplique" in lendemain


def test_la_reconnexion_dit_les_heures_de_la_coupure(faux):
    async def scenario():
        v = veille.Veille(faux)
        await v.preparer()
        faux.panne = CompteDeconnecte("jeton expiré")
        await v.rafraichir()
        v._coupure_debut -= veille.DUREE_AVANT_DECONNEXION
        debut = v._coupure_debut
        await v.rafraichir()
        faux.panne = None
        await v.rafraichir()
        apres = await v.contexte_reveil()
        v.noter_dites()
        encore = await v.contexte_reveil()
        return debut, apres, encore

    debut, apres, encore = asyncio.run(scenario())
    assert f"coupé de {debut:%H:%M} à" in apres
    assert "coupé de" not in encore  # une seule fois


def test_une_coupure_d_une_minute_n_est_pas_annoncee(faux):
    async def scenario():
        v = veille.Veille(faux)
        await v.preparer()
        faux.panne = CompteDeconnecte("jeton expiré")
        await v.rafraichir()
        faux.panne = None
        await v.rafraichir()
        return await v.contexte_reveil()

    assert "coupé de" not in asyncio.run(scenario())


# --- Le jeton : la vraie raison, et un second essai -------------------------------------------------

class FausseAppMsal:
    """Rend, à chaque appel, le résultat suivant de la liste (None, une erreur, ou un jeton)."""

    def __init__(self, resultats):
        self.resultats = list(resultats)
        self.appels = 0

    def get_accounts(self):
        return [{"username": "joi@exemple.org"}]

    def acquire_token_silent_with_error(self, portees, account, force_refresh=False):
        self.appels += 1
        return self.resultats.pop(0)


def _microsoft(app, monkeypatch):
    from joi.compte import microsoft

    monkeypatch.setattr(microsoft, "PAUSE_AVANT_REESSAI", 0)
    return microsoft.Microsoft(http=object(), application=app)


def test_jeton_un_echec_puis_un_succes(monkeypatch, caplog):
    app = FausseAppMsal([{"error": "temporarily_unavailable", "error_description": "Réessaie\nplus tard"},
                         {"access_token": "abc"}])
    assert _microsoft(app, monkeypatch)._jeton() == "abc"
    assert app.appels == 2
    assert "temporarily_unavailable (Réessaie)" in caplog.text


def test_jeton_refus_net_sans_second_essai(monkeypatch):
    app = FausseAppMsal([{"error": "invalid_grant", "error_description": "AADSTS70000"}])
    with pytest.raises(CompteDeconnecte, match="invalid_grant"):
        _microsoft(app, monkeypatch)._jeton()
    assert app.appels == 1


def test_jeton_deux_echecs_donne_la_vraie_raison(monkeypatch):
    app = FausseAppMsal([{"error": "server_error"}, {"error": "server_error"}])
    with pytest.raises(CompteDeconnecte, match="server_error"):
        _microsoft(app, monkeypatch)._jeton()
    assert app.appels == 2


def test_jeton_cache_vide(monkeypatch):
    app = FausseAppMsal([None])
    with pytest.raises(CompteDeconnecte, match="jeton expiré"):
        _microsoft(app, monkeypatch)._jeton()
    assert app.appels == 1


def test_sans_cache_une_erreur_donne_un_contexte_partiel(faux):
    faux.panne = ErreurCompte("Microsoft ne répond pas")
    texte = asyncio.run(veille.Veille(faux).contexte_reveil())
    assert "Tes courriels ne sont pas disponibles" in texte
    assert "L'agenda n'est pas disponible" in texte


def test_une_action_de_joi_invalide_le_cache(faux):
    async def scenario():
        v = veille.Veille(faux)
        await v.preparer()
        v.invalider()
        faux.appels.clear()
        await v.contexte_reveil()
        return sorted(faux.appels)

    # Pas servi depuis le cache : redemandé (avec les nouveaux courriels), comme un premier réveil.
    assert asyncio.run(scenario()) == ["agenda", "depuis", "non_lus", "taches"]


def test_un_vieux_rafraichissement_ne_remplace_pas_un_plus_recent(faux):
    v = veille.Veille(faux)
    recent = veille._Contexte(["récent"], datetime.now().astimezone())
    v._garder(recent)
    v._garder(veille._Contexte(["vieux"], recent.quand - timedelta(seconds=30)))
    assert v._cache is recent


def test_seul_l_echec_des_nouveaux_courriels_laisse_le_contexte_complet(faux):
    class SansNouveaux(FauxLent):
        def courriels_depuis(self, depuis):
            raise ErreurCompte("Microsoft demande de ralentir")

    contexte = veille.Veille(SansNouveaux())._contexte()
    assert contexte.complet


def test_trop_lent_au_reveil(faux, monkeypatch):
    monkeypatch.setattr(veille, "DELAI_REVEIL", PAUSE / 3)
    v = veille.Veille(faux)
    assert asyncio.run(v.contexte_reveil()) == ""
    assert "trop lent" in v.etat()["derniere_erreur"]


def test_jeton_un_echec_vaut_pour_les_autres_fils(monkeypatch):
    """Les blocs du contexte du réveil partent ensemble : un seul fait les deux essais."""
    from concurrent.futures import ThreadPoolExecutor
    from joi.compte import microsoft

    app = FausseAppMsal([{"error": "server_error"}] * 10)
    m = _microsoft(app, monkeypatch)
    monkeypatch.setattr(microsoft, "PAUSE_AVANT_REESSAI", 0.3)

    def un_bloc():
        try:
            m._jeton()
        except CompteDeconnecte as e:
            return str(e)

    debut = time.monotonic()
    with ThreadPoolExecutor(3) as fils:
        raisons = list(fils.map(lambda _: un_bloc(), range(3)))
    assert app.appels == 2
    assert all("server_error" in r for r in raisons)
    assert time.monotonic() - debut < 1.0


def test_coupure_courte_sans_contexte_d_avant_ni_gardee_ni_annoncee(faux):
    """Au démarrage, après une action de Joi ou après minuit, il n'y a rien à garder : une phrase
    neutre, et rien d'annoncé quand le compte revient une minute plus tard."""
    async def scenario():
        v = veille.Veille(faux)
        faux.panne = CompteDeconnecte("jeton expiré")
        pendant = await v.contexte_reveil()
        garde = v._cache
        faux.panne = None
        apres = await v.contexte_reveil()
        return pendant, garde, apres

    pendant, garde, apres = asyncio.run(scenario())
    assert "ne répond pas pour l'instant" in pendant
    assert "dès ta première réplique" not in pendant and "ne fonctionne pas" not in pendant
    assert garde is None
    assert "coupé de" not in apres and "Bonjour" in apres


def test_rafraichissements_croises_le_plus_recent_sert(faux):
    v = veille.Veille(faux)
    recent = veille._Contexte(["récent"], datetime.now().astimezone())
    v._garder(recent)
    vieux = veille._Contexte(["déconnecté"], recent.quand - timedelta(seconds=30), complet=False, deconnecte=True)
    assert v._garder(vieux) is recent


def test_jeton_erreur_reseau_un_second_essai_puis_ne_repond_pas(monkeypatch):
    class AppReseau(FausseAppMsal):
        def acquire_token_silent_with_error(self, portees, account, force_refresh=False):
            self.appels += 1
            raise ConnectionError("réseau coupé")

    app = AppReseau([])
    with pytest.raises(ErreurCompte, match="ne répond pas") as e:
        _microsoft(app, monkeypatch)._jeton()
    assert not isinstance(e.value, CompteDeconnecte)
    assert app.appels == 2


def test_contexte_de_20_min_servi_sans_attente(faux):
    """Plan Joi fiable, partie 4 : moins de CACHE_SERVI_MAX (30 min), le contexte gardé sert tout de suite."""
    async def scenario():
        v = veille.Veille(faux)
        await v.preparer()
        v._cache.quand -= timedelta(minutes=20)
        faux.appels.clear()
        debut = time.monotonic()
        texte = await v.contexte_reveil()
        duree = time.monotonic() - debut
        await v.attendre_rafraichissement()
        return texte, duree

    texte, duree = asyncio.run(scenario())
    assert duree < PAUSE / 2
    assert "## Ton compte (vu à" in texte


def test_une_annonce_d_un_reveil_rate_revient(faux):
    """La déconnexion n'est notée « dite » qu'une fois la session créée (noter_dites)."""
    async def scenario():
        v = veille.Veille(faux)
        faux.panne = CompteDeconnecte("jeton expiré")
        v._coupure_debut = maintenant() - veille.DUREE_AVANT_DECONNEXION
        rate = await v.contexte_reveil()  # la session n'a pas pu être créée : pas de noter_dites()
        return rate, await v.contexte_reveil()

    rate, suivant = asyncio.run(scenario())
    assert "dès ta première réplique" in rate
    assert "dès ta première réplique" in suivant


def test_une_erreur_passagere_pendant_une_deconnexion_n_est_pas_un_retour(faux):
    async def scenario():
        v = veille.Veille(faux)
        await v.preparer()
        faux.panne = CompteDeconnecte("jeton expiré")
        await v.rafraichir()
        v._coupure_debut -= veille.DUREE_AVANT_DECONNEXION
        debut = v._coupure_debut
        await v.rafraichir()
        faux.panne = ErreurCompte("Microsoft ne répond pas")
        await v.rafraichir()
        etat = (v._deconnexion_confirmee, v._coupure_debut, v._coupure_a_dire)
        faux.panne = CompteDeconnecte("jeton expiré")
        return debut, etat, await v.contexte_reveil()

    debut, (confirmee, coupure_debut, a_dire), texte = asyncio.run(scenario())
    assert confirmee and coupure_debut == debut and a_dire is None
    assert "coupé de" not in texte and "ne fonctionne pas pour l'instant" in texte


def test_la_phrase_neutre_d_une_coupure_n_a_pas_l_en_tete_du_cache(faux):
    faux.panne = CompteDeconnecte("jeton expiré")
    texte = asyncio.run(veille.Veille(faux).contexte_reveil())
    assert "ne répond pas pour l'instant" in texte
    assert "## Ton compte (au réveil)" in texte and "(vu à" not in texte


def test_jeton_une_coupure_du_reseau_vaut_pour_les_autres_fils(monkeypatch):
    """Une erreur réseau du rafraîchissement est retenue comme un refus : un seul fil fait les essais."""
    from joi.compte import microsoft

    class AppSansReseau(FausseAppMsal):
        def acquire_token_silent_with_error(self, portees, account=None, force_refresh=False):
            self.appels += 1
            raise ConnectionError("pas de réseau")

    app = AppSansReseau([])
    m = _microsoft(app, monkeypatch)
    for _ in range(3):
        with pytest.raises(ErreurCompte, match="ne répond pas") as e:
            m._jeton()
        assert not isinstance(e.value, CompteDeconnecte)
    assert app.appels == 2
