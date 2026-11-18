"""Le filet des tests : ils ne touchent jamais donnees/ (vraie mémoire, vrai journal, empreintes).

Les chemins des données sont figés à l'import de joi (memoire.RACINE, journal.FICHIER_CONSOLIDATION,
empreintes.DOSSIER…) : JOI_DONNEES doit donc pointer vers un dossier temporaire AVANT le premier
import de joi. C'est pourquoi tout se règle ici, au chargement de ce fichier, que pytest lit avant
les fichiers de tests. Si le dossier en vigueur est quand même le vrai, on arrête tout.
"""

import os
import shutil
import stat
import tempfile
from pathlib import Path

import pytest

_TEMPORAIRE = Path(tempfile.mkdtemp(prefix="joi-tests-"))

os.environ["JOI_DONNEES"] = str(_TEMPORAIRE / "donnees")
# Aucun appel réseau (AssemblyAI), aucun compte, rien de lourd (PyTorch, Vosk). load_dotenv ne
# remplace pas une variable déjà définie, même vide : le .env de l'utilisateur ne passe pas par-dessus.
os.environ["ASSEMBLYAI_API_KEY"] = ""
os.environ["JOI_COMPTE"] = ""
os.environ["JOI_RECONNAISSANCE_VOIX"] = "non"
os.environ["JOI_COMMANDE_VOCALE"] = "non"

from joi import config  # noqa: E402  (après les variables d'environnement, exprès)

_VRAI_DOSSIER = (config.RACINE / "donnees").resolve()
_dossier = config.DOSSIER_DONNEES.resolve()
if _dossier == _VRAI_DOSSIER or _dossier.is_relative_to(_VRAI_DOSSIER) or not _dossier.is_relative_to(_TEMPORAIRE):
    pytest.exit(f"Les tests utiliseraient le vrai dossier de données ({_dossier}) : arrêt.", returncode=3)

_SOUS_DOSSIERS = (config.DOSSIER_VOIX, config.DOSSIER_JOURNAL, config.DOSSIER_MEMOIRE,
                  config.DOSSIER_ENREGISTREMENTS, config.DOSSIER_LOGS)


def _rendre_inscriptible(fonction, chemin, _erreur) -> None:
    """Sous Windows, git écrit ses objets en lecture seule : on les rend inscriptibles pour les effacer."""
    os.chmod(chemin, stat.S_IWRITE)
    fonction(chemin)


def _vider_donnees() -> None:
    if config.DOSSIER_DONNEES.exists():
        shutil.rmtree(config.DOSSIER_DONNEES, onexc=_rendre_inscriptible)
    for dossier in _SOUS_DOSSIERS:
        dossier.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def donnees():
    """Chaque test part d'un dossier de données vide (temporaire), et le laisse vide derrière lui."""
    from joi import enregistreur

    _vider_donnees()
    enregistreur._seances_actives.clear()
    yield config.DOSSIER_DONNEES
    enregistreur._seances_actives.clear()
    _vider_donnees()


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEMPORAIRE, ignore_errors=True)
