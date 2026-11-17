"""La confirmation d'envoi des courriels : c'est la sécurité du compte de Joi.

Un courriel ne part que si le premier tour de parole de la personne après la préparation du
brouillon est une confirmation sans négation. Ce fichier est le seul des tests à importer joi.compte.
"""

from types import SimpleNamespace

import pytest

from joi.compte import brouillons, reglages


# --- est_confirmation -------------------------------------------------------------------------

@pytest.mark.parametrize("texte", [
    "oui, envoie",
    "Oui vas-y",
    "ok c'est bon",
    "Ouais, envoie-le.",
    "D'accord.",
    "Parfait, envoie !",
    "Oui.",
])
def test_confirmation(texte):
    assert brouillons.est_confirmation(texte)


@pytest.mark.parametrize("texte", [
    "non",
    "Non, attends.",
    "oui mais change le sujet",
    "Oui, mais ajoute ma signature.",
    "n'envoie pas",
    "Ne l'envoie pas.",
    "Je n’ai pas dit oui.",  # apostrophe typographique
    "oui… attends une minute",
    "pas tout de suite",
    "Oui, plutôt à Paul.",
    "Annule.",
    "Stop.",
    "jamais de la vie",
    "euh",  # ni oui ni non : pas une confirmation
    "",
])
def test_pas_une_confirmation(texte):
    assert not brouillons.est_confirmation(texte)


# --- SuiviConfirmation.verifier ---------------------------------------------------------------

ADRESSE = "toi@exemple.org"


@pytest.fixture
def horloge(monkeypatch):
    """Une horloge qu'on avance à la main : brouillons et tours de parole datés sans ambiguïté."""
    instant = [1_000_000.0]
    monkeypatch.setattr(brouillons, "time", SimpleNamespace(time=lambda: instant[0]))
    monkeypatch.setattr(reglages, "PROPRIETAIRE", ADRESSE)
    monkeypatch.setattr(reglages, "DESTINATAIRES", [])

    def avancer(secondes: float = 1.0) -> None:
        instant[0] += secondes

    return avancer


def _preparer() -> brouillons.Brouillon:
    return brouillons.preparer([ADRESSE], "Sujet", "Bonjour, ceci est un test.", None)


def test_confirmation_acceptee(horloge):
    suivi = brouillons.SuiviConfirmation()
    b = _preparer()
    horloge()
    suivi.noter_tour("oui, envoie")
    assert suivi.verifier(b.numero) is None
    assert suivi.verifier(str(b.numero)) is None  # le modèle peut l'envoyer en texte


def test_pas_de_reponse(horloge):
    suivi = brouillons.SuiviConfirmation()
    suivi.noter_tour("oui, envoie")  # avant la préparation : ne compte pas
    horloge()
    b = _preparer()
    raison = suivi.verifier(b.numero)
    assert raison and "rien répondu" in raison


@pytest.mark.parametrize("reponse", ["non", "oui mais change le sujet", "n'envoie pas"])
def test_refus(horloge, reponse):
    suivi = brouillons.SuiviConfirmation()
    b = _preparer()
    horloge()
    suivi.noter_tour(reponse)
    raison = suivi.verifier(b.numero)
    assert raison and "Pas de confirmation claire" in raison


def test_seul_le_premier_tour_compte(horloge):
    suivi = brouillons.SuiviConfirmation()
    b = _preparer()
    horloge()
    suivi.noter_tour("non, attends")
    horloge()
    suivi.noter_tour("oui, envoie")
    assert suivi.verifier(b.numero) is not None


def test_brouillon_expire(horloge):
    suivi = brouillons.SuiviConfirmation()
    b = _preparer()
    horloge()
    suivi.noter_tour("oui, envoie")
    horloge(reglages.DUREE_BROUILLON + 1)
    raison = suivi.verifier(b.numero)
    assert raison and "15 minutes" in raison


def test_mauvais_numero(horloge):
    suivi = brouillons.SuiviConfirmation()
    premier = _preparer()
    horloge()
    second = _preparer()
    horloge()
    suivi.noter_tour("oui, envoie")
    # Seul le dernier brouillon préparé peut partir.
    raison = suivi.verifier(premier.numero)
    assert raison and "pas le dernier brouillon" in raison
    assert suivi.verifier(second.numero) is None
    assert suivi.verifier(second.numero + 1) is not None
    assert suivi.verifier("abc") == "Numéro de brouillon invalide."
    assert suivi.verifier(None) == "Numéro de brouillon invalide."


def test_adresse_non_approuvee_refusee(horloge):
    with pytest.raises(brouillons.ErreurCompte):
        brouillons.preparer(["inconnu@exemple.org"], "Sujet", "Texte", None)
