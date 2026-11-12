"""Le journal de Joi : chaque échange, mot pour mot, un fichier JSON Lines par jour.

Le journal n'est jamais modifié après coup : c'est la matière première à partir de laquelle
la mémoire (dossier Markdown) est consolidée, et il permet de tout reconstruire au besoin.

Deux sortes d'entrées :
  {"ts", "qui": "toi" | "joi", "texte", "interrompu"?}      une conversation avec Joi
  {"ts", "qui": "entendu", "locuteur": "A", "texte", "source": "ecoute"}
      une phrase entendue en mode écoute (voir joi/transcription.py). Elle est ajoutée après coup,
      à la transcription différée, mais datée du moment où elle a été dite : elle arrive donc dans
      le fichier après des entrées plus récentes. Ces entrées ne passent pas par le marqueur
      .consolide.json : leur consolidation suit l'état des segments dans les manifestes d'écoute.
"""

import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta

from . import config

log = logging.getLogger("joi.journal")

FICHIER_CONSOLIDATION = config.DOSSIER_JOURNAL / ".consolide.json"


_dernier_instant: datetime | None = None


def _maintenant() -> str:
    """Horodatage strictement croissant : l'horloge Windows peut renvoyer deux fois le même instant,
    et la consolidation repère les nouvelles entrées en comparant les horodatages."""
    global _dernier_instant
    instant = datetime.now()
    if _dernier_instant and instant <= _dernier_instant:
        instant = _dernier_instant + timedelta(microseconds=1)
    _dernier_instant = instant
    return instant.isoformat(timespec="microseconds")


def noter(qui: str, texte: str, interrompu: bool = False) -> None:
    """Ajoute un échange au journal du jour. qui : « toi » ou « joi »."""
    texte = texte.strip()
    if not texte:
        return
    entree = {"ts": _maintenant(), "qui": qui, "texte": texte}
    if interrompu:
        entree["interrompu"] = True
    fichier = config.DOSSIER_JOURNAL / f"{entree['ts'][:10]}.jsonl"
    try:
        with fichier.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entree, ensure_ascii=False) + "\n")
    except OSError as e:  # un journal raté ne doit pas couper la conversation
        log.warning("Impossible d'écrire dans le journal : %s", e)


def noter_entendu(entrees: list[dict]) -> None:
    """Ajoute au journal des phrases entendues en mode écoute, chacune dans le fichier du jour où
    elle a été dite. Une entrée déjà présente (même instant, même texte) n'est pas ajoutée deux fois :
    la transcription d'un segment peut être reprise après un plantage."""
    par_jour: dict[str, list[dict]] = {}
    for entree in entrees:
        par_jour.setdefault(entree["ts"][:10], []).append(entree)
    for jour, du_jour in par_jour.items():
        fichier = config.DOSSIER_JOURNAL / f"{jour}.jsonl"
        deja = {
            (e.get("ts"), e.get("texte"))
            for e in (_lire(fichier) if fichier.exists() else [])
            if e.get("source") == "ecoute"
        }
        nouvelles = [e for e in du_jour if (e["ts"], e["texte"]) not in deja]
        if not nouvelles:
            continue
        with fichier.open("a", encoding="utf-8") as f:
            f.write("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in nouvelles))


def _lire(fichier) -> list[dict]:
    entrees = []
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        try:
            entrees.append(json.loads(ligne))
        except json.JSONDecodeError:
            continue
    return entrees


def _fichiers() -> list:
    return sorted(config.DOSSIER_JOURNAL.glob("????-??-??.jsonl"))


def entrees_depuis(ts: str | None) -> list[dict]:
    """Les échanges avec Joi strictement postérieurs à ts (tous si ts est None).
    Les phrases entendues en mode écoute n'en font pas partie."""
    jour = ts[:10] if ts else ""
    entrees = []
    for fichier in _fichiers():
        if fichier.stem < jour:
            continue
        entrees.extend(
            e for e in _lire(fichier)
            if e.get("source") != "ecoute" and (not ts or e.get("ts", "") > ts)
        )
    return entrees


def derniere_consolidation() -> str | None:
    try:
        return json.loads(FICHIER_CONSOLIDATION.read_text(encoding="utf-8")).get("jusqua")
    except (OSError, json.JSONDecodeError):
        return None


def marquer_consolide(ts: str) -> None:
    FICHIER_CONSOLIDATION.write_text(json.dumps({"jusqua": ts}), encoding="utf-8")


LIGATURES = str.maketrans({"œ": "oe", "æ": "ae", "’": "'"})
MOTS_VIDES = {
    "les", "des", "une", "est", "que", "qui", "quoi", "dans", "pour", "par", "sur", "avec", "mon", "mes",
    "ton", "tes", "son", "ses", "elle", "lui", "moi", "toi", "tu", "pas", "plus", "rappelles",
    "souviens", "te", "ce", "cette", "aux", "du", "de", "la", "le", "et", "ou",
}


def normaliser(texte: str) -> str:
    """Minuscules, sans accents ni ligatures : « Été », « ete », « sœur » et « soeur » se retrouvent."""
    decompose = unicodedata.normalize("NFD", texte.lower().translate(LIGATURES))
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn")


def racines(recherche: str) -> list[str]:
    """Les mots significatifs d'une recherche, réduits à leur racine (6 lettres) :
    « déménagé » et « déménagement » donnent tous deux « demena »."""
    mots = re.findall(r"\w+", normaliser(recherche))
    retenus = [m[:6] for m in mots if len(m) >= 3 and m not in MOTS_VIDES]
    return list(dict.fromkeys(retenus)) or [m[:6] for m in mots]


def pertinence(texte_normalise: str, racines_: list[str]) -> int:
    return sum(r in texte_normalise for r in racines_)


def chercher(racines_: list[str], limite: int = 10, jours: int = 60) -> list[dict]:
    """Les entrées des derniers jours qui contiennent le plus de racines, les plus récentes d'abord."""
    if not racines_:
        return []
    trouvees = []
    for fichier in _fichiers()[-jours:]:
        for entree in _lire(fichier):
            score = pertinence(normaliser(entree.get("texte", "")), racines_)
            if score:
                trouvees.append((score, entree.get("ts", ""), entree))
    trouvees.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [entree for _, _, entree in trouvees[:limite]]
