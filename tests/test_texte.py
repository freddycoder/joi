"""Le texte : découpage des phrases du cerveau, commande vocale, réponse [rien]."""

import pytest

from joi import cerveau, commande_vocale, serveur


# --- cerveau.couper_phrases -------------------------------------------------------------------

@pytest.mark.parametrize("tampon, phrases, reste", [
    ("Bonjour. Ça va ? Oui", ["Bonjour.", "Ça va ?"], "Oui"),
    ("Bonjour.", [], "Bonjour."),  # sans espace après, la phrase est peut-être encore en cours
    ("Wow ! Et alors… La suite", ["Wow !", "Et alors…"], "La suite"),
    ("Première ligne\nDeuxième", ["Première ligne"], "Deuxième"),
    ("Il fait 3.5 degrés", [], "Il fait 3.5 degrés"),
    ("Une liste :\n\n- un\n- deux", ["Une liste :", "- un"], "- deux"),
    ("", [], ""),
])
def test_couper_phrases(tampon, phrases, reste):
    assert cerveau.couper_phrases(tampon) == (phrases, reste)


# --- commande_vocale.demande_ecoute -----------------------------------------------------------

@pytest.mark.parametrize("texte", [
    "Joi, écoute seulement.",
    "Joie, écoute seulement !",
    "Joi écoute seule",
    "joy écoute juste",
    "Ok Joi, tu écoutes seulement.",
    "JOIE ÉCOUTE SEULE",
])
def test_demande_ecoute_reconnue(texte):
    assert commande_vocale.demande_ecoute(texte)


@pytest.mark.parametrize("texte", [
    "Je lui ai dit hier soir que Joi écoute seulement quand on le lui demande gentiment.",
    "Écoute seulement ce que je dis.",
    "Joi, reviens.",
    "",
])
def test_demande_ecoute_refusee(texte):
    assert not commande_vocale.demande_ecoute(texte)


# --- serveur._est_rien ------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", ["[rien]", "[RIEN]", "Rien.", "  rien ! ", "[rien — rien à dire]"])
def test_est_rien(phrase):
    assert serveur._est_rien(phrase)


@pytest.mark.parametrize("phrase", ["Rien de neuf, sauf un courriel de ta sœur.", "Tu as reçu un courriel.", ""])
def test_n_est_pas_rien(phrase):
    assert not serveur._est_rien(phrase)
