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
        v._cache.quand -= timedelta(minutes=7)  # plus de RAFRAICHIR_MINUTES + 1
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


def test_deconnexion_pendant_le_rafraichissement_remplace_le_cache(faux):
    async def scenario():
        v = veille.Veille(faux)
        await v.preparer()
        faux.panne = CompteDeconnecte("jeton expiré")
        await v.rafraichir()
        faux.panne = None
        return await v.contexte_reveil()

    texte = asyncio.run(scenario())
    assert "ne fonctionne pas pour l'instant" in texte


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
