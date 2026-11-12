"""La voix de Joi : synthèse vocale avec edge-tts (voix neuronales Microsoft, sans clé API)
ou, au choix, avec une voix d'AssemblyAI (par exemple une voix créée pour Joi dans le tableau de bord).

Comme dans la toute première version de Joi, les phrases courtes déjà prononcées sont
gardées en cache sur le disque pour ne pas les régénérer.
"""

import hashlib
import logging
import re
import time

import edge_tts
import httpx

from . import config, monitoring

log = logging.getLogger("joi.voix")

LONGUEUR_MAX_CACHE = 120
URL_TTS_ASSEMBLYAI = "https://tts.us.assemblyai.com/v1/speech"

EMOJIS = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
MARKDOWN = re.compile(r"[*_#`>|~]+")

_client: httpx.AsyncClient | None = None


def nettoyer(texte: str) -> str:
    """Retire ce qui ne se prononce pas (emojis, symboles markdown)."""
    texte = EMOJIS.sub("", texte)
    texte = MARKDOWN.sub("", texte)
    return re.sub(r"\s+", " ", texte).strip()


def _signature() -> str:
    """Ce qui distingue une voix d'une autre, pour ne pas mélanger leurs caches."""
    if config.JOI_TTS == "assemblyai":
        return f"assemblyai|{config.JOI_VOIX_ASSEMBLYAI}|{config.JOI_LANGUE_ASSEMBLYAI}"
    return f"{config.JOI_VOIX}|{config.JOI_DEBIT}"


async def _edge(texte: str) -> bytes:
    communication = edge_tts.Communicate(texte, config.JOI_VOIX, rate=config.JOI_DEBIT)
    audio = bytearray()
    async for morceau in communication.stream():
        if morceau["type"] == "audio":
            audio += morceau["data"]
    return bytes(audio)


async def _assemblyai(texte: str) -> bytes:
    global _client
    if _client is None:
        # Un seul client : la connexion reste ouverte d'une phrase à l'autre.
        _client = httpx.AsyncClient(
            headers={"Authorization": config.ASSEMBLYAI_API_KEY}, timeout=httpx.Timeout(30)
        )
    corps = {
        "text": texte,
        "voice_id": config.JOI_VOIX_ASSEMBLYAI,
        "encoding": "mp3",
        "sample_rate": 24000,
        "stream": False,
    }
    if config.JOI_LANGUE_ASSEMBLYAI:
        corps["language"] = config.JOI_LANGUE_ASSEMBLYAI
    reponse = await _client.post(URL_TTS_ASSEMBLYAI, json=corps)
    if reponse.status_code != 200:
        raise RuntimeError(f"AssemblyAI TTS {reponse.status_code} : {reponse.text[:300]}")
    return reponse.content


async def synthetiser(texte: str) -> bytes:
    """Retourne l'audio MP3 de la phrase, ou b"" s'il n'y a rien à dire."""
    texte = nettoyer(texte)
    if not re.search(r"\w", texte):
        return b""

    cle = hashlib.sha1(f"{_signature()}|{texte}".encode()).hexdigest()
    fichier = config.DOSSIER_VOIX / f"{cle}.mp3"
    if fichier.exists():
        monitoring.voix["cache"] += 1
        return fichier.read_bytes()

    debut = time.perf_counter()
    if config.JOI_TTS == "assemblyai":
        try:
            audio = await _assemblyai(texte)
        except Exception as e:
            # Mieux vaut une autre voix que le silence : on se rabat sur edge-tts, sans mettre en cache.
            log.warning("Voix AssemblyAI indisponible, repli sur edge-tts : %s", e)
            monitoring.voix["replis"] += 1
            audio = await _edge(texte)
            _mesurer(debut, "edge (repli)")
            return audio
    else:
        audio = await _edge(texte)
    _mesurer(debut, config.JOI_TTS)

    if len(texte) <= LONGUEUR_MAX_CACHE and audio:
        fichier.write_bytes(audio)
    return audio


def _mesurer(debut: float, moteur: str) -> None:
    monitoring.voix["syntheses"] += 1
    monitoring.voix["derniere_ms"] = round((time.perf_counter() - debut) * 1000)
    monitoring.voix["moteur"] = moteur
