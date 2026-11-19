"""Le texte d'un courriel lu en entier : réponses sans leur historique, transferts avec le courriel
transféré, liens de suivi raccourcis. Les courriels sont fictifs."""

from joi.compte.microsoft import TAILLE_MAX_COURRIEL, _texte_courriel

# Un transfert sans note, comme Outlook l'envoie en texte : uniqueBody ne contient qu'un saut de ligne.
TRANSFERT_OUTLOOK = (
    "\r\n________________________________\r\n"
    "De : nouvelles@exemple.com <nouvelles@exemple.com> de la part de Équipe Exemple <nouvelles@exemple.com>\r\n"
    "Envoyé : jeudi 19 novembre 2026 00:10\r\n"
    "À : alex.tremblay@exemple.org <alex.tremblay@exemple.org>\r\n"
    "Objet : Trois nouveaux modèles\r\n"
    "\r\n"
    "Trois nouveaux modèles de transcription, en 32 langues.\r\n"
    "\r\n"
    "Lire l'annonce <https://exemple.com/annonce>\r\n"
)


def test_un_transfert_sans_note_garde_le_courriel_transfere():
    texte = _texte_courriel("TR : Trois nouveaux modèles", "\r\n", TRANSFERT_OUTLOOK)
    assert texte.startswith("--- Courriel transféré ---\nDe : nouvelles@exemple.com")
    assert "Envoyé : jeudi 19 novembre 2026 00:10" in texte
    assert "Objet : Trois nouveaux modèles" in texte
    assert "Trois nouveaux modèles de transcription, en 32 langues." in texte
    assert "Lire l'annonce <https://exemple.com/annonce>" in texte
    assert "alex.tremblay" not in texte  # les destinataires ne disent rien à Joi


def test_un_transfert_avec_note_garde_les_deux():
    corps = "Regarde ça, Joi.\r\n" + TRANSFERT_OUTLOOK
    texte = _texte_courriel("TR : Trois nouveaux modèles", "Regarde ça, Joi.\r\n", corps)
    assert texte.startswith("Regarde ça, Joi.\n\n--- Courriel transféré ---")
    assert "en 32 langues" in texte


def test_un_transfert_gmail_coupe_l_historique_du_courriel_transfere():
    corps = (
        "---------- Forwarded message ---------\n"
        "From: Sam Gagnon <sam@exemple.com>\n"
        "Date: Wed, Nov 18, 2026 at 9:00 AM\n"
        "Subject: Réunion\n"
        "To: Alex <alex@exemple.org>\n"
        "\n"
        "On se voit jeudi.\n"
        "\n"
        "Le mar. 17 nov. 2026, Alex a écrit :\n"
        "> Quand est la réunion ?\n"
    )
    texte = _texte_courriel("Fwd: Réunion", "", corps)
    assert "From: Sam Gagnon <sam@exemple.com>" in texte
    assert "On se voit jeudi." in texte
    assert "Quand est la réunion" not in texte


def test_un_transfert_sans_prefixe_est_reconnu_quand_rien_d_autre_n_est_ecrit():
    texte = _texte_courriel("Trois nouveaux modèles", "\r\n", TRANSFERT_OUTLOOK)
    assert "en 32 langues" in texte


def test_une_reponse_perd_toujours_son_historique():
    nouveau = "Parfait, à jeudi.\r\n"
    corps = nouveau + "\r\n________________________________\r\nDe : Sam <sam@exemple.com>\r\nObjet : RE: Réunion\r\n\r\nJeudi ?\r\n"
    assert _texte_courriel("RE: Réunion", nouveau, corps) == "Parfait, à jeudi."
    assert _texte_courriel("RE: Réunion", "", corps) == "Parfait, à jeudi."


def test_les_liens_de_suivi_et_les_images_sont_retires():
    suivi = "https://na01.safelinks.protection.outlook.com/?url=https%3A%2F%2Flinks.exemple.com%2Fs%2F" + "x" * 300
    court = "https://na01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fexemple.com%2Fprix&data=05"
    corps = (
        f"[Logo] <{suivi}>\n"
        f"Voir les prix <{court}>\n"
        "[https://images.exemple.com/icone-micro.png]   Le modèle temps réel​​\n"
        f"Lien nu : {suivi}\n"
    )
    texte = _texte_courriel("Nouveautés", corps, corps)
    assert texte == ("[Logo]\nVoir les prix <https://exemple.com/prix>\nLe modèle temps réel\nLien nu : [lien]")


def test_un_long_courriel_est_coupe():
    texte = _texte_courriel("Long", "mot " * 5000, "")
    assert texte.endswith("[…coupé]")
    assert len(texte) <= TAILLE_MAX_COURRIEL + len("\n[…coupé]")
