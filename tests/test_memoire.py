"""La mémoire : journées nommées, recherche, chemins sûrs, découpage d'une écoute."""

from datetime import date

import pytest

from joi import config, memoire

AUJOURDHUI = date(2026, 11, 17)


def _ecrire(relatif: str, contenu: str) -> None:
    fichier = config.DOSSIER_MEMOIRE / relatif
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text(contenu, encoding="utf-8")


# --- _dates_nommees ---------------------------------------------------------------------------

@pytest.mark.parametrize("recherche, attendu", [
    ("aujourd'hui", ["2026-11-17"]),
    ("Aujourd’hui", ["2026-11-17"]),
    ("ce matin", ["2026-11-17"]),
    ("hier", ["2026-11-16"]),
    ("avant-hier", ["2026-11-15"]),
    ("avant hier", ["2026-11-15"]),
    ("16 novembre", ["2026-11-16"]),
    ("1er novembre", ["2026-11-01"]),
    ("le 3 août 2025", ["2025-08-03"]),
    ("2026-11-16", ["2026-11-16"]),
    # Un jour sans année pas encore arrivé cette année : c'est celui de l'an passé.
    ("25 décembre", ["2025-12-25"]),
    ("17 novembre", ["2026-11-17"]),
    ("31 février", []),
    ("2026-02-31", []),
    ("chat Pixel", []),
])
def test_dates_nommees(recherche, attendu):
    jours, _reste = memoire._dates_nommees(recherche, AUJOURDHUI)
    assert jours == attendu


def test_dates_nommees_le_reste_sert_de_mots_cles():
    jours, reste = memoire._dates_nommees("la journée du 16 novembre, le chantier", AUJOURDHUI)
    assert jours == ["2026-11-16"]
    assert "journee" not in reste and "novembre" not in reste
    assert "chantier" in reste


def test_dates_nommees_avant_hier_n_est_pas_aussi_hier():
    jours, _ = memoire._dates_nommees("avant-hier", AUJOURDHUI)
    assert jours == ["2026-11-15"]


def test_dates_nommees_sans_doublon():
    jours, _ = memoire._dates_nommees("hier, le 16 novembre (2026-11-16)", AUJOURDHUI)
    assert jours == ["2026-11-16"]


# --- souvenir ---------------------------------------------------------------------------------

def test_souvenir_journee_nommee_donne_son_resume():
    _ecrire("journees/2026-11-16.md", "---\ndescription: x\n---\n# Journée du 2026-11-16\n\nOn a réparé la scie.")
    resultat = memoire.souvenir("la journée du 16 novembre", AUJOURDHUI)
    assert "journees/2026-11-16.md" in resultat
    assert "On a réparé la scie." in resultat


def test_souvenir_journee_absente_le_dit():
    resultat = memoire.souvenir("hier", AUJOURDHUI)
    assert "pas de résumé de la journée du 2026-11-16" in resultat


def test_souvenir_rien_trouve():
    assert memoire.souvenir("zygomatique", AUJOURDHUI) == "Aucun souvenir trouvé pour cette recherche."


def test_souvenir_les_mots_rares_passent_avant():
    # « projet » est partout ; « Gaspésie » dans un seul fichier : celui-là doit sortir en premier.
    for i in range(4):
        _ecrire(f"projets/p{i}.md", f"# Projet {i}\n\nUn projet parmi d'autres, projet numéro {i}.")
    _ecrire("voyages/ete.md", "# Voyage\n\nLe projet de voyage en Gaspésie, en juillet.")
    resultat = memoire.souvenir("projet Gaspésie", AUJOURDHUI)
    premier = resultat.split("\n\n")[0]
    assert premier.startswith("[mémoire : voyages/ete.md]")
    assert "Gaspésie" in premier


def test_souvenir_l_extrait_garde_la_ligne_du_mot_rare():
    # Un long fichier où « novembre » est partout : la ligne qui contient « Pixel » doit rester.
    lignes = [f"- {i} novembre : rien de spécial, une journée ordinaire de novembre." for i in range(1, 200)]
    lignes.insert(150, "- Le chat Pixel a vu le vétérinaire.")
    _ecrire("chronique.md", "\n".join(lignes))
    _ecrire("autre.md", "Encore novembre.")
    resultat = memoire.souvenir("Pixel novembre", AUJOURDHUI)
    assert "Le chat Pixel a vu le vétérinaire." in resultat
    assert len(resultat) <= memoire.TAILLE_MAX_SOUVENIR + 200


def test_souvenir_cherche_aussi_dans_le_journal():
    from joi import journal

    journal.noter("toi", "Mon chat s'appelle Pixel.")
    resultat = memoire.souvenir("Pixel", AUJOURDHUI)
    assert "toi : Mon chat s'appelle Pixel." in resultat


# --- _chemin_sur ------------------------------------------------------------------------------

@pytest.mark.parametrize("chemin", [
    "", "   ", "../secret.md", "proches/../../x.md", "/etc/passwd", "\\Windows\\x.md", "C:/x.md", "C:x.md",
    ".git/config", "proches/.cache/x.md", ".cache.md",
])
def test_chemin_sur_refuse(chemin):
    with pytest.raises(ValueError):
        memoire._chemin_sur(chemin)


def test_chemin_sur_accepte_un_chemin_de_la_memoire():
    chemin = memoire._chemin_sur("proches/marie.md")
    assert chemin == (config.DOSSIER_MEMOIRE / "proches" / "marie.md").resolve()
    assert memoire._chemin_sur("proches\\marie.md") == chemin


def test_chemin_md_refuse_un_autre_type():
    with pytest.raises(ValueError):
        memoire._chemin_md("notes.txt")


# --- _tranches_ecoute -------------------------------------------------------------------------

def _segment(heure: str, n: int, journal_rappels: list) -> memoire.SegmentEcoute:
    entrees = [
        {"ts": f"2026-11-16T{heure}:{i:02d}.000000", "qui": "entendu", "locuteur": "A",
         "texte": f"phrase {heure} numéro {i}, " + "bla " * 10}
        for i in range(n)
    ]
    return memoire.SegmentEcoute(debut=f"2026-11-16T{heure}:00", entrees=entrees,
                                 integre=lambda: journal_rappels.append(heure))


def test_tranches_ecoute_rappels_et_taille(monkeypatch):
    monkeypatch.setattr(memoire, "TAILLE_MAX_TRANCHE", 400)
    appels: list[str] = []
    segments = [_segment("09:00", 7, appels), _segment("09:30", 0, appels), _segment("10:00", 3, appels)]
    ecoute = memoire.Ecoute(debut="2026-11-16T09:00:00", fin="2026-11-16T10:30:00", segments=segments)
    tranches = memoire._tranches_ecoute(ecoute)

    assert len(tranches) > 1
    for texte, _rappels in tranches:
        assert len(texte) <= 400
    # Chaque rappel est attaché au message qui contient la dernière ligne de son segment.
    derniere_ligne = {
        "09:00": segments[0].entrees[-1]["texte"],
        "09:30": "(rien d'intelligible)",
        "10:00": segments[2].entrees[-1]["texte"],
    }
    for texte, rappels in tranches:
        for rappel in rappels:
            rappel()
            heure = appels[-1]
            assert derniere_ligne[heure] in texte
    assert appels == ["09:00", "09:30", "10:00"]  # chacun une fois, dans l'ordre


def test_tranches_ecoute_une_seule_tranche():
    appels: list[str] = []
    ecoute = memoire.Ecoute(debut="2026-11-16T09:00:00", fin=None, segments=[_segment("09:00", 2, appels)],
                            voix_reconnues=True)
    tranches = memoire._tranches_ecoute(ecoute)
    assert len(tranches) == 1
    texte, rappels = tranches[0]
    assert texte.startswith("--- Nouveau segment, à partir de 09:00 ---")
    assert len(rappels) == 1
