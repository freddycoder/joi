"""La voix de Joi : synthèse vocale avec edge-tts (voix neuronales Microsoft, sans clé API).

Comme dans la toute première version de Joi, les phrases courtes déjà prononcées sont
gardées en cache sur le disque pour ne pas les régénérer.
"""

import hashlib
import re

import edge_tts

from . import config

LONGUEUR_MAX_CACHE = 120

EMOJIS = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
MARKDOWN = re.compile(r"[*_#`>|~]+")


def nettoyer(texte: str) -> str:
    """Retire ce qui ne se prononce pas (emojis, symboles markdown)."""
    texte = EMOJIS.sub("", texte)
    texte = MARKDOWN.sub("", texte)
    return re.sub(r"\s+", " ", texte).strip()


async def synthetiser(texte: str) -> bytes:
    """Retourne l'audio MP3 de la phrase, ou b"" s'il n'y a rien à dire."""
    texte = nettoyer(texte)
    if not re.search(r"\w", texte):
        return b""

    cle = hashlib.sha1(f"{config.JOI_VOIX}|{config.JOI_DEBIT}|{texte}".encode()).hexdigest()
    fichier = config.DOSSIER_VOIX / f"{cle}.mp3"
    if fichier.exists():
        return fichier.read_bytes()

    communication = edge_tts.Communicate(texte, config.JOI_VOIX, rate=config.JOI_DEBIT)
    audio = bytearray()
    async for morceau in communication.stream():
        if morceau["type"] == "audio":
            audio += morceau["data"]

    if len(texte) <= LONGUEUR_MAX_CACHE and audio:
        fichier.write_bytes(audio)
    return bytes(audio)
