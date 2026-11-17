"""La mémoire à long terme de Joi : un dossier de fichiers Markdown qu'elle organise librement.

- Au réveil, index.md et toi.md sont ajoutés à son prompt système (aucune latence).
- Pendant la conversation, l'outil se_souvenir cherche dans la mémoire et le journal ; une journée
  nommée (« aujourd'hui », « 16 novembre ») donne son résumé complet.
- Après une conversation, une passe de consolidation (session Copilot séparée, « la bibliothécaire »)
  relit le journal et réorganise les fichiers. Après une écoute, la même bibliothécaire reçoit la
  transcription de ce qui a été entendu (voir joi/transcription.py et consolider_ecoute). Le soir,
  le bilan des voix entendues s'ajoute au résumé de la journée (noter_voix_du_jour).
  Une fois une écoute apprise, les conversations ouvertes sont prévenues (abonner).
- Chaque passe est enregistrée dans un dépôt git local (donnees/memoire/.git, sans remote) :
  on peut voir ou annuler ce qu'elle a changé.
"""

import asyncio
import logging
import re
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path, PurePosixPath
from typing import Callable

from pydantic import BaseModel, Field
from copilot import CopilotClient, define_tool
from copilot.generated.rpc import PermissionDecisionReject

from . import config, journal

log = logging.getLogger("joi.memoire")

RACINE = config.DOSSIER_MEMOIRE
TAILLE_MAX_REVEIL = 8000  # caractères de mémoire injectés dans le prompt au réveil
TAILLE_MAX_SOUVENIR = 3000  # caractères renvoyés par se_souvenir (fichiers trouvés et journal)
TAILLE_MAX_JOURNEE = 16000  # en plus : le résumé des journées nommées (« aujourd'hui », « 16 novembre »)
TAILLE_MAX_TRANCHE = 20000  # caractères de journal envoyés par message à la bibliothécaire
DELAI_CONSOLIDATION = 600  # secondes accordées à une tranche de consolidation

INDEX_DEPART = """---
description: Carte de la mémoire de Joi, une ligne par fichier.
---
# Index de la mémoire

- toi.md : l'essentiel sur la personne avec qui je parle.
"""

TOI_DEPART = """---
description: L'essentiel sur la personne avec qui je parle (injecté à chaque réveil).
---
# Toi

Je ne sais encore rien de toi.
"""

_verrou_consolidation = asyncio.Lock()

# Prévenus quand une écoute vient d'être apprise, avec les journées touchées (AAAA-MM-JJ) : les
# conversations ouvertes (joi/serveur.py), pour que Joi sache que le résumé est prêt.
_abonnes: list[Callable[[list[str]], None]] = []


def abonner(rappel: Callable[[list[str]], None]) -> None:
    _abonnes.append(rappel)


def desabonner(rappel: Callable[[list[str]], None]) -> None:
    if rappel in _abonnes:
        _abonnes.remove(rappel)


def _signaler_apprises(jours: list[str]) -> None:
    for rappel in list(_abonnes):
        try:
            rappel(jours)
        except Exception:
            log.exception("Signalement d'une journée apprise impossible")


# --- Fichiers ---------------------------------------------------------------------------------

def initialiser() -> None:
    """Crée la mémoire de départ et son dépôt git local s'ils n'existent pas."""
    RACINE.mkdir(parents=True, exist_ok=True)
    for nom, contenu in (("index.md", INDEX_DEPART), ("toi.md", TOI_DEPART)):
        fichier = RACINE / nom
        if not fichier.exists():
            fichier.write_text(contenu, encoding="utf-8")
    if not (RACINE / ".git").exists() and shutil.which("git"):
        _git("init", "--quiet")
        # Configuration locale à ce dépôt seulement : les commits de mémoire sont signés « Joi ».
        _git("config", "user.name", "Joi")
        _git("config", "user.email", "joi@localhost")
        _enregistrer_version("Mémoire de départ")


def _git(*arguments: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", "-C", str(RACINE), *arguments],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("git indisponible pour la mémoire : %s", e)
        return None


def _enregistrer_version(message: str) -> None:
    if not (RACINE / ".git").exists():
        return
    _git("add", "-A")
    statut = _git("status", "--porcelain")
    if statut and statut.stdout.strip():
        _git("commit", "--quiet", "-m", message)


def _chemin_sur(relatif: str) -> Path:
    """Résout un chemin de la mémoire, en refusant tout ce qui sort du dossier memoire/."""
    relatif = relatif.strip().replace("\\", "/")
    morceaux = PurePosixPath(relatif).parts
    if (
        not relatif
        or relatif.startswith("/")
        or ":" in relatif
        or ".." in morceaux
        or any(m.startswith(".") for m in morceaux)
    ):
        raise ValueError(f"Chemin refusé : {relatif!r}")
    chemin = (RACINE / relatif).resolve()
    if not chemin.is_relative_to(RACINE.resolve()):
        raise ValueError(f"Chemin refusé : {relatif!r}")
    return chemin


def _chemin_md(relatif: str) -> Path:
    chemin = _chemin_sur(relatif)
    if chemin.suffix.lower() != ".md":
        raise ValueError("Seuls les fichiers .md sont permis dans la mémoire.")
    return chemin


def _relatif(chemin: Path) -> str:
    return chemin.relative_to(RACINE.resolve()).as_posix()


def _fichiers_md() -> list[Path]:
    racine = RACINE.resolve()
    return sorted(
        p for p in racine.rglob("*.md")
        if not any(m.startswith(".") for m in p.relative_to(racine).parts)
    )


def contexte_reveil() -> str:
    """Ce que Joi sait déjà, à ajouter à son prompt système."""
    morceaux = []
    for nom in ("toi.md", "index.md"):
        fichier = RACINE / nom
        if fichier.exists():
            morceaux.append(f"### {nom}\n{fichier.read_text(encoding='utf-8').strip()}")
    texte = "\n\n".join(morceaux)
    if len(texte) > TAILLE_MAX_REVEIL:
        texte = texte[:TAILLE_MAX_REVEIL] + "\n[…tronqué]"
    return f"\n\n## Ce que tu sais déjà (ta mémoire)\n{texte}\n" if texte else ""


# --- Outil de la conversation -----------------------------------------------------------------

class ParametresSouvenir(BaseModel):
    recherche: str = Field(description=(
        "Mots-clés à retrouver, ex. « chat Pixel » ou « voyage Gaspésie ». Pour une journée, nomme-la : "
        "« aujourd'hui », « hier », « 16 novembre » ou « 2026-11-16 » donnent son résumé complet."
    ))


MOIS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
    "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
}
# Sur une recherche normalisée (minuscules, sans accents) : voir journal.normaliser.
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_TEXTE = re.compile(r"\b(\d{1,2})(?:er)?\s+(" + "|".join(MOIS) + r")(?:\s+(\d{4}))?\b")
_DATES_RELATIVES = [  # dans cet ordre : « avant-hier » avant « hier »
    (re.compile(r"\bavant[\s-]*hier\b"), 2),
    (re.compile(r"\bhier\b"), 1),
    (re.compile(r"\baujourd\W*hui\b|\bce matin\b|\bcet apres[\s-]*midi\b|\bce soir\b"), 0),
]
_MOT_JOURNEE = re.compile(r"\bjournees?\b")


def _dates_nommees(recherche: str, aujourdhui: date) -> tuple[list[str], str]:
    """Les journées nommées dans une recherche (« aujourd'hui », « hier », « 16 novembre »,
    « 2026-11-16 »), en AAAA-MM-JJ, et le reste de la recherche sans elles. Un jour sans année est
    celui de l'année en cours, ou de l'an passé s'il n'est pas encore arrivé."""
    texte = journal.normaliser(recherche)
    jours: list[str] = []

    def ajouter(annee: int, mois: int, jour: int) -> None:
        try:
            iso = date(annee, mois, jour).isoformat()
        except ValueError:
            return
        if iso not in jours:
            jours.append(iso)

    def iso(m: re.Match) -> str:
        ajouter(int(m[1]), int(m[2]), int(m[3]))
        return " "

    def en_toutes_lettres(m: re.Match) -> str:
        mois, jour = MOIS[m[2]], int(m[1])
        annee = int(m[3]) if m[3] else aujourdhui.year
        if not m[3]:
            try:
                if date(annee, mois, jour) > aujourdhui:
                    annee -= 1
            except ValueError:
                return m[0]
        ajouter(annee, mois, jour)
        return " "

    texte = _DATE_ISO.sub(iso, texte)
    texte = _DATE_TEXTE.sub(en_toutes_lettres, texte)
    for motif, decalage in _DATES_RELATIVES:
        if motif.search(texte):
            texte = motif.sub(" ", texte)
            jour = aujourdhui - timedelta(days=decalage)
            ajouter(jour.year, jour.month, jour.day)
    if jours:
        texte = _MOT_JOURNEE.sub(" ", texte)  # « la journée du 16 » : le mot n'apprend plus rien
    return jours, texte


def _couper(texte: str, taille: int) -> str:
    """Coupe un texte à une fin de ligne, sous la taille donnée."""
    if len(texte) <= taille:
        return texte
    coupe = texte.rfind("\n", 0, taille)
    return texte[: coupe if coupe > taille // 2 else taille].rstrip() + "\n[…]"


def _extrait(lignes: list[str], normalisees: list[str], poids: dict[str, float], taille: int) -> str:
    """Les lignes d'un fichier qui contiennent les mots les plus rares, avec leurs voisines, dans
    l'ordre du fichier et sous la taille donnée : une ligne qui ne contient qu'un mot répandu
    (« novembre ») ne prend pas la place d'une ligne qui contient le mot cherché."""
    scores = [(sum(p for m, p in poids.items() if m in l), i) for i, l in enumerate(normalisees)]
    retenues: set[int] = set()
    longueur = 0
    for score, i in sorted(scores, key=lambda s: (-s[0], s[1])):
        if score <= 0:
            break
        voisines = {j for j in range(max(0, i - 1), min(len(lignes), i + 2)) if lignes[j].strip()}
        ajout = sum(len(lignes[j]) + 1 for j in voisines - retenues)
        if retenues and longueur + ajout > taille:
            continue
        retenues |= voisines
        longueur += ajout
    return _couper("\n".join(lignes[j] for j in sorted(retenues)), taille)


def souvenir(recherche: str, aujourdhui: date | None = None, stats: dict | None = None) -> str:
    """Cherche dans les fichiers de mémoire, puis dans le journal des conversations.

    Une journée nommée (« aujourd'hui », « hier », « 16 novembre ») donne d'abord son résumé complet
    (journees/AAAA-MM-JJ.md) : une recherche par mots-clés ne le retrouve pas, car « novembre » ou
    « 2026 » se trouvent dans tous les résumés. Le reste de la recherche sert de mots-clés.

    Tout est relu à chaque appel (aucun index) : c'est lent sur un gros journal, d'où l'appel dans
    un fil à part (voir chercher_souvenir). stats reçoit le volume lu."""
    stats = {} if stats is None else stats
    jours, reste = _dates_nommees(recherche, aujourdhui or date.today())
    resultats = []

    # 1. Les journées nommées : leur résumé, en entier (ou presque).
    deja_montres: set[Path] = set()
    for jour in jours:
        fichier = _chemin_md(f"journees/{jour}.md")
        if fichier.exists():
            deja_montres.add(fichier.resolve())
            contenu = fichier.read_text(encoding="utf-8").strip()
            resultats.append(f"[mémoire : journees/{jour}.md, le résumé de cette journée]\n"
                             f"{_couper(contenu, TAILLE_MAX_JOURNEE // len(jours))}")
        else:
            resultats.append(f"[mémoire : pas de résumé de la journée du {jour} (rien d'appris ce jour-là, "
                             "ou une écoute pas encore transcrite et apprise)]")

    if jours:  # sans la date, ne garder que les vrais mots-clés (pas de repli sur « du », « la »)
        mots = [m for m in journal.racines(reste) if len(m) >= 3 and m not in journal.MOTS_VIDES]
    else:
        mots = journal.racines(reste)

    # 2. Mémoire : les fichiers qui contiennent les mots les plus rares, avec les lignes concernées.
    fichiers = []
    for fichier in _fichiers_md():
        if fichier.resolve() in deja_montres:
            continue
        contenu = fichier.read_text(encoding="utf-8")
        stats["memoire_octets"] = stats.get("memoire_octets", 0) + len(contenu.encode("utf-8"))
        lignes = contenu.splitlines()
        normalisees = [journal.normaliser(l) for l in lignes]
        fichiers.append((fichier, lignes, normalisees, "\n".join(normalisees)))
    stats["memoire_fichiers"] = len(fichiers)
    # Un mot présent dans un seul fichier compte pour 1, dans quatre fichiers pour 1/4.
    presences = {m: sum(m in texte for *_, texte in fichiers) for m in mots}
    poids = {m: 1 / n for m, n in presences.items() if n}
    classement = []
    for fichier, lignes, normalisees, texte in fichiers:
        score = sum(p for m, p in poids.items() if m in texte)
        if score:
            classement.append((score, fichier.stat().st_mtime, fichier, lignes, normalisees))
    classement.sort(key=lambda x: (-x[0], -x[1]))  # à égalité, le plus récent d'abord
    retenus = classement[:5]

    # 3. Journal : les échanges mot pour mot (et ce qui a été entendu en mode écoute), seulement
    # des journées nommées s'il y en a.
    entrees = journal.chercher(mots, limite=8, dates=set(jours) or None, stats=stats) if mots else []

    # Les fichiers et le journal se partagent l'extrait : un seul fichier ne le remplit plus.
    sections = len(retenus) + (1 if entrees else 0)
    part = TAILLE_MAX_SOUVENIR // sections if sections else 0
    for _, _, fichier, lignes, normalisees in retenus:
        resultats.append(f"[mémoire : {_relatif(fichier)}]\n{_extrait(lignes, normalisees, poids, part)}")
    if entrees:
        lignes_journal = [f"[journal {_quand(e)}] {_qui(e)} : {e['texte']}" for e in entrees]
        resultats.append(_couper("\n".join(lignes_journal), part))

    if not resultats:
        return "Aucun souvenir trouvé pour cette recherche."
    return "\n\n".join(resultats)


def _quand(entree: dict) -> str:
    return entree.get("ts", "")[:16].replace("T", " ")


def _qui(entree: dict) -> str:
    """« toi », « Joi », ou « [entendu, …] » pour une phrase captée en mode écoute. Le locuteur est
    une étiquette d'AssemblyAI (« voix A » : pas un nom, et pas forcément la personne), ou une voix
    reconnue sur l'ordinateur : « toi », « voix inconnue 3 » (voir joi/empreintes.py)."""
    if entree.get("qui") == "entendu":
        locuteur = entree.get("locuteur") or "?"
        if locuteur == "toi":
            return "[entendu, toi (voix reconnue)]"
        if locuteur.startswith("voix"):
            return f"[entendu, {locuteur}]"
        if locuteur.startswith("inconnu-"):
            return "[entendu, voix inconnue]"
        if len(locuteur) <= 2:  # étiquette d'AssemblyAI : A, B, ?
            return f"[entendu, voix {locuteur}]"
        return f"[entendu, {locuteur} (voix reconnue)]"  # une personne qui a donné son accord
    # Les entrées du compte de Joi (joi/compte/), lisibles même si le compte a été désactivé depuis.
    if entree.get("qui") == "courriel":
        return "[courriel envoyé]" if entree.get("sens") == "envoye" else "[courriel reçu]"
    if entree.get("qui") == "agenda":
        return "[agenda]"
    if entree.get("qui") == "tache":
        return "[tâche]"
    return "toi" if entree.get("qui") == "toi" else "Joi"


def chercher_souvenir(recherche: str) -> str:
    """souvenir(), avec sa durée et le volume lu dans le journal technique : pour savoir quand la
    relecture complète devient trop lente et qu'il faut un index (SQLite FTS5, par exemple)."""
    debut = time.perf_counter()
    stats: dict = {}
    try:
        return souvenir(recherche, stats=stats)
    finally:
        log.info(
            "Recherche %r : %.0f ms, %d fichier(s) de mémoire (%d Ko), %d jour(s) de journal (%d Ko)",
            recherche, (time.perf_counter() - debut) * 1000,
            stats.get("memoire_fichiers", 0), stats.get("memoire_octets", 0) // 1024,
            stats.get("journal_jours", 0), stats.get("journal_octets", 0) // 1024,
        )


# Le SDK Copilot appelle les handlers sur la boucle asyncio du serveur et attend leur résultat s'il
# est awaitable (copilot/tools.py, wrapped_handler). Une recherche relit toute la mémoire et jusqu'à
# 60 jours de journal : dans un fil à part, l'audio, le WebSocket et les annonces ne se figent pas.
outil_se_souvenir = define_tool(
    "se_souvenir",
    description=(
        "Cherche dans ta mémoire à long terme et dans le journal de vos conversations passées. "
        "Chaque recherche fait attendre la personne : utilise-le seulement pour un souvenir précis "
        "du passé qui n'est pas dans ce que tu sais déjà, jamais pour du bavardage. "
        "Les lignes « [entendu, …] » ont été captées pendant que tu écoutais en silence, ce n'était pas "
        "une conversation avec toi. « [entendu, toi (voix reconnue)] » : la personne, reconnue à sa voix. "
        "« voix A » ou « voix inconnue » : quelqu'un d'autre, ou la personne si sa voix n'a pas été reconnue."
    ),
    handler=lambda p, _: asyncio.to_thread(chercher_souvenir, p.recherche),
    params_type=ParametresSouvenir,
    skip_permission=True,
    idempotent=True,
)


# --- Outils de la bibliothécaire --------------------------------------------------------------

class ParametresChemin(BaseModel):
    chemin: str = Field(description="Chemin relatif dans la mémoire, ex. « proches/marie.md ».")


class ParametresEcrire(BaseModel):
    chemin: str = Field(description="Chemin relatif du fichier .md à créer ou remplacer.")
    contenu: str = Field(description="Contenu complet du fichier (remplace l'ancien).")


class ParametresDeplacer(BaseModel):
    source: str = Field(description="Chemin relatif actuel.")
    destination: str = Field(description="Nouveau chemin relatif.")


def lister() -> str:
    fichiers = _fichiers_md()
    if not fichiers:
        return "La mémoire est vide."
    return "\n".join(f"{_relatif(f)} ({f.stat().st_size} octets)" for f in fichiers)


def lire(chemin: str) -> str:
    fichier = _chemin_md(chemin)
    if not fichier.exists():
        return f"Le fichier {chemin} n'existe pas."
    return fichier.read_text(encoding="utf-8")


def ecrire(chemin: str, contenu: str) -> str:
    fichier = _chemin_md(chemin)
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text(contenu.rstrip() + "\n", encoding="utf-8")
    return f"{chemin} enregistré."


def deplacer(source: str, destination: str) -> str:
    depart, arrivee = _chemin_md(source), _chemin_md(destination)
    if not depart.exists():
        return f"Le fichier {source} n'existe pas."
    if arrivee.exists():
        return f"{destination} existe déjà : fusionne le contenu avec ecrire, puis supprime l'ancien."
    arrivee.parent.mkdir(parents=True, exist_ok=True)
    depart.rename(arrivee)
    _nettoyer_dossiers_vides(depart.parent)
    return f"{source} déplacé vers {destination}."


def supprimer(chemin: str) -> str:
    fichier = _chemin_md(chemin)
    if fichier.name in ("index.md", "toi.md") and fichier.parent == RACINE.resolve():
        return "index.md et toi.md sont essentiels : modifie-les plutôt que de les supprimer."
    if not fichier.exists():
        return f"Le fichier {chemin} n'existe pas."
    fichier.unlink()
    _nettoyer_dossiers_vides(fichier.parent)
    return f"{chemin} supprimé."


def _nettoyer_dossiers_vides(dossier: Path) -> None:
    racine = RACINE.resolve()
    while dossier != racine and dossier.is_relative_to(racine) and not any(dossier.iterdir()):
        dossier.rmdir()
        dossier = dossier.parent


OUTILS_BIBLIOTHECAIRE = [
    # La bibliothécaire travaille en fond, mais sur la même boucle que la conversation : ses lectures
    # passent aussi par un fil à part. Les écritures, courtes, restent sur la boucle : deux d'entre
    # elles ne peuvent pas se croiser sur un même fichier.
    define_tool("lister", description="Liste tous les fichiers de la mémoire.",
                handler=lambda _: asyncio.to_thread(lister), skip_permission=True, idempotent=True),
    define_tool("lire", description="Lit un fichier de la mémoire.",
                handler=lambda p, _: asyncio.to_thread(lire, p.chemin), params_type=ParametresChemin,
                skip_permission=True, idempotent=True),
    define_tool("ecrire", description="Crée ou remplace entièrement un fichier .md de la mémoire.",
                handler=lambda p, _: ecrire(p.chemin, p.contenu), params_type=ParametresEcrire,
                skip_permission=True),
    define_tool("deplacer", description="Renomme ou déplace un fichier de la mémoire.",
                handler=lambda p, _: deplacer(p.source, p.destination),
                params_type=ParametresDeplacer, skip_permission=True),
    define_tool("supprimer", description="Supprime un fichier de la mémoire.",
                handler=lambda p, _: supprimer(p.chemin), params_type=ParametresChemin,
                skip_permission=True),
]


# --- Consolidation ----------------------------------------------------------------------------

def _refuser_permission(requete, invocation):
    log.warning("Permission refusée à la bibliothécaire : %s", getattr(requete, "kind", requete))
    return PermissionDecisionReject(feedback="Seuls les outils de mémoire sont permis.")


def _tranches(entrees: list[dict]) -> list[list[dict]]:
    tranches, courante, taille = [], [], 0
    for entree in entrees:
        longueur = len(entree.get("texte", "")) + 40
        if courante and taille + longueur > TAILLE_MAX_TRANCHE:
            tranches.append(courante)
            courante, taille = [], 0
        courante.append(entree)
        taille += longueur
    if courante:
        tranches.append(courante)
    return tranches


def _ligne(e: dict) -> str:
    qui = "Toi" if e.get("qui") == "toi" else _qui(e)
    coupe = " (interrompue)" if e.get("interrompu") else ""
    return f"[{_quand(e)}] {qui}{coupe} : {e['texte']}"


def _transcrire(entrees: list[dict]) -> str:
    return "\n".join(_ligne(e) for e in entrees)


def _consignes_bibliothecaire() -> str:
    """Ses consignes, plus celles du compte de Joi s'il est actif (sans compte : inchangées)."""
    consignes = config.FICHIER_BIBLIOTHECAIRE.read_text(encoding="utf-8")
    if config.JOI_COMPTE:
        compte = Path(__file__).resolve().parent / "compte" / "bibliothecaire.md"
        consignes = consignes.rstrip() + "\n\n" + compte.read_text(encoding="utf-8")
    return consignes


@asynccontextmanager
async def _bibliothecaire():
    """Une session Copilot avec les seuls outils de la mémoire et les consignes de la bibliothécaire."""
    client = CopilotClient()
    session = None
    try:
        await client.start()
        session = await client.create_session(
            tools=OUTILS_BIBLIOTHECAIRE,
            available_tools=[o.name for o in OUTILS_BIBLIOTHECAIRE],
            on_permission_request=_refuser_permission,
            system_message={"mode": "replace", "content": _consignes_bibliothecaire()},
            model=config.JOI_MODELE,
            skip_custom_instructions=True,
            working_directory=str(config.DOSSIER_DONNEES),
        )
        yield session
    finally:
        if session:
            try:
                await session.disconnect()
            except Exception:
                pass
        try:
            await client.stop()
        except Exception:
            pass


def _etat_memoire() -> str:
    return (
        f"Nous sommes le {date.today().isoformat()}.\n\n"
        f"Fichiers actuels de la mémoire :\n{lister()}\n\n"
        f"Contenu actuel de index.md :\n{lire('index.md')}"
    )


async def consolider() -> None:
    """Intègre à la mémoire les échanges du journal qui ne l'ont pas encore été."""
    if _verrou_consolidation.locked():
        log.info("Consolidation déjà en cours.")
    async with _verrou_consolidation:
        entrees = journal.entrees_depuis(journal.derniere_consolidation())
        if not entrees:
            return
        log.info("Consolidation de %d échanges dans la mémoire…", len(entrees))
        try:
            async with _bibliothecaire() as session:
                for tranche in _tranches(entrees):
                    message = f"{_etat_memoire()}\n\nNouveaux échanges à intégrer :\n{_transcrire(tranche)}"
                    await session.send_and_wait(message, timeout=DELAI_CONSOLIDATION)
                    journal.marquer_consolide(tranche[-1]["ts"])
            log.info("Mémoire consolidée.")
        except Exception:
            log.exception("Consolidation de la mémoire interrompue")
        finally:
            await asyncio.to_thread(
                _enregistrer_version, f"Consolidation jusqu'à {journal.derniere_consolidation()}"
            )


# --- Consolidation d'une écoute ---------------------------------------------------------------

@dataclass
class SegmentEcoute:
    """Un segment d'écoute transcrit : ses phrases, et quoi faire une fois qu'il est intégré."""

    debut: str  # heure réelle du début de l'enregistrement
    entrees: list[dict]
    integre: Callable[[], None]


@dataclass
class Ecoute:
    """Une séance d'écoute (du passage en mode écoute au retour en conversation)."""

    debut: str
    fin: str | None  # pour une séance en cours : la fin de la partie transmise
    segments: list[SegmentEcoute]
    en_cours: bool = False  # l'écoute continue : d'autres parties suivront
    deja_en_partie: bool = False  # des parties précédentes ont déjà été transmises
    # Voix reconnues sur l'ordinateur (joi/empreintes.py) : « toi » et « voix inconnue N » valent pour
    # toute la journée. Sinon, ce sont les étiquettes A/B d'AssemblyAI, qui recommencent à chaque segment.
    voix_reconnues: bool = False
    legende_voix: str = ""  # les voix de la journée jusqu'ici (durée, heures, classe)
    # Une écoute déjà transmise (avec les étiquettes A/B), transmise à nouveau avec les voix
    # reconnues : la bibliothécaire corrige ce qu'elle en avait tiré au lieu de l'ajouter.
    correction: bool = False


def _tranches_ecoute(ecoute: Ecoute) -> list[tuple[str, list[Callable[[], None]]]]:
    """Découpe la transcription d'une séance en messages, et associe à chacun les segments dont
    il contient la fin : on les marque intégrés une fois le message traité par la bibliothécaire."""
    lignes: list[tuple[str, Callable[[], None] | None]] = []
    for segment in ecoute.segments:
        heure = segment.debut[11:16]
        if ecoute.voix_reconnues:
            lignes.append((f"--- Nouveau segment, à partir de {heure} ---", None))
        else:
            lignes.append((f"--- Nouveau segment, à partir de {heure} : les étiquettes de voix recommencent ---", None))
        lignes.extend((_ligne(e), None) for e in segment.entrees)
        if not segment.entrees:
            lignes.append(("(rien d'intelligible)", None))
        lignes[-1] = (lignes[-1][0], segment.integre)

    tranches, courante, rappels, taille = [], [], [], 0
    for texte, rappel in lignes:
        if courante and taille + len(texte) + 1 > TAILLE_MAX_TRANCHE:
            tranches.append(("\n".join(courante), rappels))
            courante, rappels, taille = [], [], 0
        courante.append(texte)
        taille += len(texte) + 1
        if rappel:
            rappels.append(rappel)
    if courante:
        tranches.append(("\n".join(courante), rappels))
    return tranches


async def noter_voix_du_jour(jour: str, bilan: str) -> None:
    """Ajoute le bilan des voix entendues (joi/empreintes.py) à la fin du résumé de la journée."""
    async with _verrou_consolidation:
        chemin = f"journees/{jour}.md"
        fichier = _chemin_md(chemin)
        if fichier.exists():
            contenu = fichier.read_text(encoding="utf-8").rstrip()
            if "\n## Voix entendues" in contenu:  # une écoute après le bilan : on ajoute la suite
                bilan = bilan.replace("## Voix entendues", "## Voix entendues (suite)", 1)
        else:
            contenu = (f"---\ndescription: Résumé de la journée d'écoute du {jour}.\n"
                       f"mis_a_jour: {jour}\n---\n# Journée du {jour}")
        await asyncio.to_thread(ecrire, chemin, f"{contenu}\n\n{bilan}")
        await asyncio.to_thread(_enregistrer_version, f"Voix entendues le {jour}")


async def consolider_ecoute(ecoutes: list[Ecoute]) -> None:
    """Intègre à la mémoire ce qui a été entendu pendant des séances d'écoute déjà transcrites."""
    ecoutes = [e for e in ecoutes if e.segments]
    if not ecoutes:
        return
    if _verrou_consolidation.locked():
        log.info("Consolidation en cours : l'écoute attendra son tour.")
    async with _verrou_consolidation:
        phrases = sum(len(s.entrees) for e in ecoutes for s in e.segments)
        log.info("Consolidation de %d séance(s) d'écoute (%d phrases) dans la mémoire…",
                 len(ecoutes), phrases)
        apprises: list[str] = []  # les journées dont au moins une partie est entrée dans la mémoire
        try:
            async with _bibliothecaire() as session:
                for ecoute in ecoutes:
                    jour = ecoute.debut[:10]
                    fin = ecoute.fin[11:16] if ecoute.fin else "?"
                    suite = ""
                    if ecoute.deja_en_partie:
                        suite += (" Le début de cette écoute t'a déjà été transmis : il est sans doute "
                                  "déjà dans le résumé de la journée, ne le répète pas.")
                    if ecoute.en_cours:
                        suite += " L'écoute continue : la suite te sera transmise plus tard."
                    if ecoute.voix_reconnues:
                        suite += (" Les voix ont été reconnues à leur empreinte vocale (voir « Voix reconnues » "
                                  "dans tes consignes).")
                        if ecoute.legende_voix:
                            suite += f"\n\nVoix de la journée jusqu'ici :\n{ecoute.legende_voix}\n"
                    tranches = _tranches_ecoute(ecoute)
                    for numero, (texte, rappels) in enumerate(tranches, 1):
                        if ecoute.correction:
                            consigne = (
                                "Cette écoute t'a DÉJÀ été transmise, mais avec des étiquettes A/B qui "
                                "recommençaient à chaque segment : tu as pu attribuer à « toi » des propos "
                                "d'autres personnes. La voici à nouveau, avec les voix reconnues. Ton travail "
                                f"est de CORRIGER, pas d'ajouter une deuxième fois : relis journees/{jour}.md "
                                "et réécris-le avec les bonnes attributions (qui a dit, proposé, décidé, "
                                "appelé). Si tu as mis dans d'autres fichiers (toi.md, projets…) des propos "
                                "ou des décisions qui venaient en fait d'une autre personne, corrige-les "
                                "aussi. N'ajoute que ce qui manquait vraiment. Garde la section "
                                "« Voix entendues » si elle existe. "
                            )
                        else:
                            consigne = (f"Le résumé de cette journée va dans journees/{jour}.md "
                                        "(lis-le d'abord s'il existe, puis complète-le). ")
                        message = (
                            f"{_etat_memoire()}\n\n"
                            "ATTENTION : ceci est une ÉCOUTE, pas une conversation avec Joi. Joi écoutait "
                            "en silence ce qui se disait autour de la personne (« toi »), "
                            f"le {jour} de {ecoute.debut[11:16]} à {fin}.{suite} "
                            "Suis la section « Transcriptions d'écoute » de tes consignes. "
                            f"{consigne}\n\n"
                            f"Transcription (partie {numero} de {len(tranches)}) :\n{texte}"
                        )
                        await session.send_and_wait(message, timeout=DELAI_CONSOLIDATION)
                        for rappel in rappels:
                            rappel()
                        if jour not in apprises:
                            apprises.append(jour)
            log.info("Écoute consolidée dans la mémoire.")
        except Exception:
            log.exception("Consolidation de l'écoute interrompue")
        finally:
            debuts = ", ".join(e.debut[:16].replace("T", " ") for e in ecoutes)
            await asyncio.to_thread(_enregistrer_version, f"Écoute du {debuts}")
    if apprises:
        _signaler_apprises(apprises)


def description(chemin: str) -> str:
    """La ligne « description: » d'un fichier de la mémoire (vide s'il n'en a pas)."""
    try:
        contenu = _chemin_md(chemin).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""
    for ligne in contenu.splitlines()[:6]:
        if ligne.startswith("description:"):
            return ligne.removeprefix("description:").strip()
    return ""
