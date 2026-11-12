"""La mémoire à long terme de Joi : un dossier de fichiers Markdown qu'elle organise librement.

- Au réveil, index.md et toi.md sont ajoutés à son prompt système (aucune latence).
- Pendant la conversation, l'outil se_souvenir cherche dans la mémoire et le journal.
- Après une conversation, une passe de consolidation (session Copilot séparée, « la bibliothécaire »)
  relit le journal et réorganise les fichiers. Chaque passe est enregistrée dans un dépôt git
  local (donnees/memoire/.git, sans remote) : on peut voir ou annuler ce qu'elle a changé.
"""

import asyncio
import logging
import shutil
import subprocess
from datetime import date
from pathlib import Path, PurePosixPath

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

    # 2. Journal : les échanges récents mot pour mot.
    for entree in journal.chercher(mots, limite=8):
        qui = "toi" if entree.get("qui") == "toi" else "Joi"
        resultats.append(f"[journal {entree['ts'][:16].replace('T', ' ')}] {qui} : {entree['texte']}")

    if not resultats:
        return "Aucun souvenir trouvé pour cette recherche."
    texte = "\n\n".join(resultats)
    return texte[:TAILLE_MAX_SOUVENIR] + ("\n[…]" if len(texte) > TAILLE_MAX_SOUVENIR else "")


outil_se_souvenir = define_tool(
    "se_souvenir",
    description=(
        "Cherche dans ta mémoire à long terme et dans le journal de vos conversations passées. "
        "Chaque recherche fait attendre la personne : utilise-le seulement pour un souvenir précis "
        "du passé qui n'est pas dans ce que tu sais déjà, jamais pour du bavardage."
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


def _transcrire(entrees: list[dict]) -> str:
    lignes = []
    for e in entrees:
        qui = "Toi" if e.get("qui") == "toi" else "Joi"
        coupe = " (interrompue)" if e.get("interrompu") else ""
        lignes.append(f"[{e['ts'][:16].replace('T', ' ')}] {qui}{coupe} : {e['texte']}")
    return "\n".join(lignes)


async def consolider() -> None:
    """Intègre à la mémoire les échanges du journal qui ne l'ont pas encore été."""
    if _verrou_consolidation.locked():
        log.info("Consolidation déjà en cours.")
    async with _verrou_consolidation:
        entrees = journal.entrees_depuis(journal.derniere_consolidation())
        if not entrees:
            return
        log.info("Consolidation de %d échanges dans la mémoire…", len(entrees))
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
            for tranche in _tranches(entrees):
                message = (
                    f"Nous sommes le {date.today().isoformat()}.\n\n"
                    f"Fichiers actuels de la mémoire :\n{lister()}\n\n"
                    f"Contenu actuel de index.md :\n{lire('index.md')}\n\n"
                    f"Nouveaux échanges à intégrer :\n{_transcrire(tranche)}"
                )
                await session.send_and_wait(message, timeout=DELAI_CONSOLIDATION)
                journal.marquer_consolide(tranche[-1]["ts"])
            log.info("Mémoire consolidée.")
        except Exception:
            log.exception("Consolidation de la mémoire interrompue")
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
            await asyncio.to_thread(
                _enregistrer_version, f"Consolidation jusqu'à {journal.derniere_consolidation()}"
            )
