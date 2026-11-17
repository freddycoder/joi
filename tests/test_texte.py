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


# --- cerveau.couper_premiere : la première phrase, coupée plus tôt ----------------------------

@pytest.mark.parametrize("tampon, morceau, reste", [
    # Moins de 8 mots : on attend encore une fin de phrase.
    ("Oui, bien sûr, ", None, "Oui, bien sûr, "),
    # 8 mots ou plus : coupure à la dernière virgule suivie d'une espace.
    ("Alors, je pense que la meilleure chose à faire, c'est",
     "Alors, je pense que la meilleure chose à faire,", "c'est"),
    ("Pour la teinture du chêne ; il faut compter huit heures",
     "Pour la teinture du chêne ;", "il faut compter huit heures"),
    ("Voici ce que je te propose pour ce soir : des pâtes",
     "Voici ce que je te propose pour ce soir :", "des pâtes"),
    # La virgule décimale n'est pas suivie d'une espace : jamais coupée.
    ("Il faut compter 3,5 litres de vernis pour toute la table", None,
     "Il faut compter 3,5 litres de vernis pour toute la table"),
    # Une virgule pas encore suivie de son espace : on attend.
    ("Il faut compter trois litres de vernis pour la table,", None,
     "Il faut compter trois litres de vernis pour la table,"),
    ("Il est 10:30 et le vernis sèche encore sur la table", None,
     "Il est 10:30 et le vernis sèche encore sur la table"),
])
def test_couper_premiere(tampon, morceau, reste):
    assert cerveau.couper_premiere(tampon) == (morceau, reste)


def test_cerveau_coupe_seulement_la_premiere_phrase():
    import asyncio

    deltas = ["Alors, je pense que la meilleure chose à faire, ", "c'est de poncer d'abord. ",
              "Ensuite, tu passes une couche de teinture, puis tu attends, ", "et tu vernis. "]

    class FausseSession:
        def __init__(self, c):
            self.c = c

        async def send(self, texte, **_):
            async def produire():
                for delta in deltas:
                    await asyncio.sleep(0)
                    self.c._file.put_nowait(delta)
                self.c._file.put_nowait(cerveau.FIN_DE_REPONSE)
            asyncio.get_running_loop().create_task(produire())

    async def scenario():
        c = cerveau.Cerveau()
        c._session = FausseSession(c)
        return [m async for m in c.repondre("Comment je finis ma table ?")]

    assert asyncio.run(scenario()) == [
        "Alors, je pense que la meilleure chose à faire,",  # coupée tôt : la première seulement
        "c'est de poncer d'abord.",
        "Ensuite, tu passes une couche de teinture, puis tu attends, et tu vernis.",
    ]
