"""La mémoire à long terme de Joi : un dossier de fichiers Markdown qu'elle organise librement.

- Au réveil, index.md et toi.md sont ajoutés à son prompt système (aucune latence).
- Pendant la conversation, l'outil se_souvenir cherche dans la mémoire et le journal.
- Après une conversation, une passe de consolidation (session Copilot séparée, « la bibliothécaire »)
  relit le journal et réorganise les fichiers. Après une écoute, la même bibliothécaire reçoit la
  transcription de ce qui a été entendu (voir joi/transcription.py et consolider_ecoute).
- Chaque passe est enregistrée dans un dépôt git local (donnees/memoire/.git, sans remote) :
  on peut voir ou annuler ce qu'elle a changé.
"""

import asyncio
import logging
import shutil
import subprocess
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Callable

from pydantic import BaseModel, Field
from copilot import CopilotClient, define_tool
from copilot.generated.rpc import PermissionDecisionReject

from . import config, journal

log = logging.getLogger("joi.memoire")

RACINE = config.DOSSIER_MEMOIRE
TAILLE_MAX_REVEIL = 8000  # caractères de mémoire injectés dans le prompt au réveil
TAILLE_MAX_SOUVENIR = 3000  # caractères renvoyés par se_souvenir
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
    recherche: str = Field(description="Mots-clés à retrouver, ex. « chat Pixel » ou « voyage Gaspésie ».")


def souvenir(recherche: str) -> str:
    """Cherche dans les fichiers de mémoire, puis dans le journal des conversations."""
    mots = journal.racines(recherche)
    resultats = []

    # 1. Mémoire : les fichiers qui contiennent le plus de mots, avec les lignes concernées.
    classement = []
    for fichier in _fichiers_md():
        lignes = fichier.read_text(encoding="utf-8").splitlines()
        normalisees = [journal.normaliser(l) for l in lignes]
        score = journal.pertinence("\n".join(normalisees), mots)
        if score:
            classement.append((score, fichier, lignes, normalisees))
    classement.sort(key=lambda x: -x[0])
    for _, fichier, lignes, normalisees in classement[:5]:
        retenues = sorted({
            j
            for i, l in enumerate(normalisees) if any(m in l for m in mots)
            for j in range(max(0, i - 1), min(len(lignes), i + 2))
        })
        extrait = "\n".join(lignes[j] for j in retenues if lignes[j].strip())
        resultats.append(f"[mémoire : {_relatif(fichier)}]\n{extrait}")

    # 2. Journal : les échanges récents mot pour mot (et ce qui a été entendu en mode écoute).
    for entree in journal.chercher(mots, limite=8):
        resultats.append(f"[journal {_quand(entree)}] {_qui(entree)} : {entree['texte']}")

    if not resultats:
        return "Aucun souvenir trouvé pour cette recherche."
    texte = "\n\n".join(resultats)
    return texte[:TAILLE_MAX_SOUVENIR] + ("\n[…]" if len(texte) > TAILLE_MAX_SOUVENIR else "")


def _quand(entree: dict) -> str:
    return entree.get("ts", "")[:16].replace("T", " ")


def _qui(entree: dict) -> str:
    """« toi », « Joi », ou « [entendu, voix A] » pour une phrase captée en mode écoute :
    l'étiquette de voix n'est pas un nom, et ce n'est pas forcément la personne qui parle."""
    if entree.get("qui") == "entendu":
        return f"[entendu, voix {entree.get('locuteur') or '?'}]"
    return "toi" if entree.get("qui") == "toi" else "Joi"


outil_se_souvenir = define_tool(
    "se_souvenir",
    description=(
        "Cherche dans ta mémoire à long terme et dans le journal de vos conversations passées. "
        "Chaque recherche fait attendre la personne : utilise-le seulement pour un souvenir précis "
        "du passé qui n'est pas dans ce que tu sais déjà, jamais pour du bavardage. "
        "Les lignes « [entendu, voix A] » ont été captées pendant que tu écoutais en silence : "
        "la voix A n'est pas forcément la personne, et ce n'était pas une conversation avec toi."
    ),
    handler=lambda p, _: souvenir(p.recherche),
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
    define_tool("lister", description="Liste tous les fichiers de la mémoire.",
                handler=lambda _: lister(), skip_permission=True, idempotent=True),
    define_tool("lire", description="Lit un fichier de la mémoire.",
                handler=lambda p, _: lire(p.chemin), params_type=ParametresChemin,
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
            system_message={
                "mode": "replace",
                "content": config.FICHIER_BIBLIOTHECAIRE.read_text(encoding="utf-8"),
            },
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


def _tranches_ecoute(ecoute: Ecoute) -> list[tuple[str, list[Callable[[], None]]]]:
    """Découpe la transcription d'une séance en messages, et associe à chacun les segments dont
    il contient la fin : on les marque intégrés une fois le message traité par la bibliothécaire."""
    lignes: list[tuple[str, Callable[[], None] | None]] = []
    for segment in ecoute.segments:
        heure = segment.debut[11:16]
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
                    tranches = _tranches_ecoute(ecoute)
                    for numero, (texte, rappels) in enumerate(tranches, 1):
                        message = (
                            f"{_etat_memoire()}\n\n"
                            "ATTENTION : ceci est une ÉCOUTE, pas une conversation avec Joi. Joi écoutait "
                            "en silence ce qui se disait autour de la personne (« toi »), "
                            f"le {jour} de {ecoute.debut[11:16]} à {fin}.{suite} "
                            "Suis la section « Transcriptions d'écoute » de tes consignes. "
                            f"Le résumé de cette journée va dans journees/{jour}.md "
                            "(lis-le d'abord s'il existe, puis complète-le).\n\n"
                            f"Transcription (partie {numero} de {len(tranches)}) :\n{texte}"
                        )
                        await session.send_and_wait(message, timeout=DELAI_CONSOLIDATION)
                        for rappel in rappels:
                            rappel()
            log.info("Écoute consolidée dans la mémoire.")
        except Exception:
            log.exception("Consolidation de l'écoute interrompue")
        finally:
            debuts = ", ".join(e.debut[:16].replace("T", " ") for e in ecoutes)
            await asyncio.to_thread(_enregistrer_version, f"Écoute du {debuts}")
