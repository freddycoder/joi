"""La transcription différée du mode écoute : l'audio enregistré est envoyé à AssemblyAI en fin
d'écoute, avec la reconnaissance des voix, puis ce qui a été entendu est confié à la mémoire.

Le parcours d'un segment (voir le manifeste décrit dans joi/enregistreur.py) :

    enregistre ──► transcrit ──► consolide
        │
        └──► echec   (après ESSAIS_MAX refus d'AssemblyAI ; l'audio est gardé pour qu'on puisse l'examiner)

1. Envoi du MP3 (POST /v2/upload), création de la transcription (POST /v2/transcript), puis
   attente du résultat (GET /v2/transcript/{id}). L'identifiant est noté dans le manifeste :
   si Joi est arrêtée pendant l'attente, on reprend la même transcription au lieu de payer deux fois.
2. Le résultat brut est gardé à côté de l'audio (HHMMSS.json). C'est la source de vérité pour la
   suite. La transcription est ensuite effacée des serveurs d'AssemblyAI (avec l'audio envoyé).
3. Les phrases (« utterances ») deviennent des entrées du journal, datées de l'heure réelle où
   elles ont été dites : début du segment + start (ms). Le segment passe à « transcrit » et,
   sauf si JOI_GARDER_AUDIO=oui, le MP3 est supprimé.
4. La bibliothécaire reçoit les segments transcrits, séance par séance (memoire.consolider_ecoute).
   Chaque segment passe à « consolide » une fois intégré. On ne passe pas par le marqueur
   .consolide.json du journal : les phrases d'une écoute sont datées dans le passé et seraient
   ignorées si une conversation plus récente avait déjà été consolidée.

Tout ce qui échoue (réseau, Copilot, arrêt de Joi) est repris au prochain appel d'apprendre().
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from . import config, enregistreur, journal, memoire

log = logging.getLogger("joi.transcription")

URL_API = "https://api.assemblyai.com/v2"
LANGUE = "fr"

# Prix en dollars US par heure d'audio, relevés sur assemblyai.com/pricing le 2026-11-12.
PRIX_HEURE = {"universal-3-5-pro": 0.21, "universal-2": 0.15}
PRIX_VOIX_HEURE = 0.02  # speaker_labels, en différé

TRANSCRIPTIONS_SIMULTANEES = 3
INTERVALLE_SONDAGE = 5  # secondes entre deux demandes d'état
DELAI_MAX_TRANSCRIPTION = 30 * 60  # au-delà, on abandonne pour cette fois (l'id est gardé pour reprendre)
ESSAIS_MAX = 3  # refus d'AssemblyAI avant de classer un segment en échec

_verrou = asyncio.Lock()


async def apprendre() -> None:
    """Transcrit les segments d'écoute en attente, puis les intègre à la mémoire."""
    if _verrou.locked():
        log.info("Transcription de l'écoute déjà en cours : on attend la fin.")
    async with _verrou:
        try:
            await transcrire_en_attente()
        except Exception:
            log.exception("Transcription de l'écoute interrompue")
        await consolider()


# --- Transcription -----------------------------------------------------------------------------

async def transcrire_en_attente() -> None:
    a_faire = [
        (chemin, manifeste, segment)
        for chemin, manifeste in enregistreur.seances_terminees()
        for segment in manifeste["segments"]
        if segment.get("etat") in ("enregistre", "en_cours")  # « en_cours » : séance coupée par un plantage
    ]
    if not a_faire:
        return
    if not config.ASSEMBLYAI_API_KEY:
        log.warning("ASSEMBLYAI_API_KEY manquante : %d segment(s) d'écoute attendent leur transcription.",
                    len(a_faire))
        return

    log.info("Transcription de %d segment(s) d'écoute…", len(a_faire))
    debut = time.perf_counter()
    limite = asyncio.Semaphore(TRANSCRIPTIONS_SIMULTANEES)

    async with httpx.AsyncClient(
        base_url=URL_API,
        headers={"authorization": config.ASSEMBLYAI_API_KEY},
        timeout=httpx.Timeout(60, write=600),  # l'envoi d'un segment de 30 min (~7 Mo) peut être lent
    ) as client:

        async def une(chemin: Path, manifeste: dict, segment: dict) -> tuple[float, float]:
            async with limite:
                return await _transcrire_segment(client, chemin, manifeste, segment)

        resultats = await asyncio.gather(*(une(*a) for a in a_faire))

    secondes = sum(s for s, _ in resultats)
    cout = sum(c for _, c in resultats)
    reussis = sum(1 for chemin, manifeste, segment in a_faire if segment.get("etat") == "transcrit")
    log.info(
        "Écoute transcrite : %d segment(s) sur %d, %.2f h d'audio facturées, environ %.2f $ US (%.0f s)",
        reussis, len(a_faire), secondes / 3600, cout, time.perf_counter() - debut,
    )


async def _transcrire_segment(
    client: httpx.AsyncClient, chemin_manifeste: Path, manifeste: dict, segment: dict
) -> tuple[float, float]:
    """Transcrit un segment. Renvoie les secondes d'audio facturées et leur coût estimé."""
    dossier = config.DOSSIER_ENREGISTREMENTS
    mp3 = dossier / segment["fichier"]
    brut = mp3.with_suffix(".json")

    def sauver() -> None:
        enregistreur.sauver_manifeste(chemin_manifeste, manifeste)

    facture = (0.0, 0.0)
    try:
        if brut.exists():
            # Déjà transcrit avant un plantage : on ne repaie pas.
            resultat = json.loads(brut.read_text(encoding="utf-8"))
        else:
            if not mp3.exists() or mp3.stat().st_size == 0:
                segment.update(etat="echec", erreur="audio introuvable ou vide")
                sauver()
                return facture
            resultat = await _demander(client, mp3, segment, sauver)
            if resultat.get("status") == "error":
                _noter_refus(segment, resultat.get("error") or "erreur inconnue")
                sauver()
                await _effacer_chez_assemblyai(client, resultat.get("id"))
                return facture
            brut.write_text(json.dumps(resultat, ensure_ascii=False), encoding="utf-8")
            await _effacer_chez_assemblyai(client, resultat.get("id"))
            facture = _facture(resultat, segment)

        entrees = entrees_du_segment(segment, resultat)
        journal.noter_entendu(entrees)

        segment["etat"] = "transcrit"
        segment["transcription"] = brut.relative_to(dossier).as_posix()
        if not segment.get("duree_s") and resultat.get("audio_duration"):
            segment["duree_s"] = float(resultat["audio_duration"])  # segment coupé par un plantage
        for champ in ("id_transcription", "erreur"):
            segment.pop(champ, None)
        sauver()
        log.info("Segment %s transcrit : %d phrase(s), %d voix",
                 segment["fichier"], len(entrees), len({e["locuteur"] for e in entrees}))

        if not config.JOI_GARDER_AUDIO:
            mp3.unlink(missing_ok=True)  # vie privée : on ne garde que le texte
    except (httpx.HTTPError, OSError, TimeoutError, json.JSONDecodeError, ValueError) as e:
        # Réseau, disque, délai dépassé : le segment reste « enregistre » et sera repris plus tard.
        log.warning("Transcription du segment %s reportée : %s", segment["fichier"], _decrire(e))
    return facture


async def _demander(client: httpx.AsyncClient, mp3: Path, segment: dict, sauver) -> dict:
    """Envoie le segment et attend sa transcription (ou reprend celle qui avait été demandée)."""
    identifiant = segment.get("id_transcription")
    if identifiant:
        reponse = await client.get(f"/transcript/{identifiant}")
        if reponse.status_code in (400, 404):
            log.info("Transcription %s introuvable chez AssemblyAI : on recommence.", identifiant)
            identifiant = None
        else:
            reponse.raise_for_status()

    if not identifiant:
        audio = await asyncio.to_thread(mp3.read_bytes)
        reponse = await client.post(
            "/upload", content=audio, headers={"content-type": "application/octet-stream"}
        )
        reponse.raise_for_status()
        reponse = await client.post("/transcript", json={
            "audio_url": reponse.json()["upload_url"],
            "speech_models": config.ASSEMBLYAI_MODELES_DIFFERES,
            "language_code": LANGUE,
            "speaker_labels": True,
            "keyterms_prompt": config.MOTS_CLES,
        })
        reponse.raise_for_status()
        identifiant = reponse.json()["id"]
        segment["id_transcription"] = identifiant
        if segment.get("etat") == "en_cours":
            segment["etat"] = "enregistre"
        sauver()

    limite = time.monotonic() + DELAI_MAX_TRANSCRIPTION
    while True:
        reponse = await client.get(f"/transcript/{identifiant}")
        reponse.raise_for_status()
        resultat = reponse.json()
        if resultat.get("status") in ("completed", "error"):
            return resultat
        if time.monotonic() > limite:
            raise TimeoutError(f"transcription {identifiant} toujours « {resultat.get('status')} »")
        await asyncio.sleep(INTERVALLE_SONDAGE)


def _noter_refus(segment: dict, erreur: str) -> None:
    segment["essais"] = segment.get("essais", 0) + 1
    segment["erreur"] = erreur
    segment.pop("id_transcription", None)
    if segment["essais"] >= ESSAIS_MAX:
        segment["etat"] = "echec"
        log.error("Segment %s abandonné après %d essais : %s", segment["fichier"], segment["essais"], erreur)
    else:
        log.warning("AssemblyAI a refusé le segment %s (essai %d sur %d) : %s",
                    segment["fichier"], segment["essais"], ESSAIS_MAX, erreur)


async def _effacer_chez_assemblyai(client: httpx.AsyncClient, identifiant: str | None) -> None:
    """Efface la transcription et l'audio envoyé des serveurs d'AssemblyAI (on garde notre copie)."""
    if not identifiant:
        return
    try:
        (await client.delete(f"/transcript/{identifiant}")).raise_for_status()
    except httpx.HTTPError as e:
        log.warning("Transcription %s non effacée chez AssemblyAI : %s", identifiant, _decrire(e))


def _facture(resultat: dict, segment: dict) -> tuple[float, float]:
    secondes = float(resultat.get("audio_duration") or segment.get("duree_s") or 0)
    modele = resultat.get("speech_model_used") or config.ASSEMBLYAI_MODELES_DIFFERES[0]
    prix = PRIX_HEURE.get(modele, max(PRIX_HEURE.values())) + PRIX_VOIX_HEURE
    return secondes, secondes / 3600 * prix


def _decrire(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"{e.response.status_code} {e.response.text[:200]}"
    return str(e) or type(e).__name__


def entrees_du_segment(segment: dict, resultat: dict) -> list[dict]:
    """Les phrases d'une transcription, en entrées de journal datées de l'heure où elles ont été dites."""
    debut = datetime.fromisoformat(segment["debut"])
    phrases = resultat.get("utterances")
    if phrases is None and (resultat.get("text") or "").strip():
        # Sans reconnaissance des voix, AssemblyAI ne renvoie que le texte complet.
        phrases = [{"start": 0, "speaker": "?", "text": resultat["text"]}]

    entrees: list[dict] = []
    precedent: datetime | None = None
    for phrase in phrases or []:
        texte = (phrase.get("text") or "").strip()
        if not texte:
            continue
        instant = debut + timedelta(milliseconds=phrase.get("start") or 0)
        if precedent and instant <= precedent:  # des horodatages strictement croissants, comme le journal
            instant = precedent + timedelta(microseconds=1)
        precedent = instant
        entrees.append({
            "ts": instant.isoformat(timespec="microseconds"),
            "qui": "entendu",
            "locuteur": str(phrase.get("speaker") or "?"),
            "texte": texte,
            "source": "ecoute",
        })
    return entrees


# --- Consolidation -----------------------------------------------------------------------------

async def consolider() -> None:
    """Confie à la bibliothécaire les segments transcrits mais pas encore intégrés à la mémoire."""
    dossier = config.DOSSIER_ENREGISTREMENTS
    ecoutes = []
    for chemin, manifeste in enregistreur.seances_terminees():
        segments = []
        for segment in manifeste["segments"]:
            if segment.get("etat") != "transcrit":
                continue
            try:
                resultat = json.loads((dossier / segment["transcription"]).read_text(encoding="utf-8"))
            except (KeyError, OSError, json.JSONDecodeError) as e:
                log.warning("Transcription du segment %s illisible : %s", segment["fichier"], e)
                continue
            segments.append(memoire.SegmentEcoute(
                debut=segment["debut"],
                entrees=entrees_du_segment(segment, resultat),
                integre=_marqueur(chemin, manifeste, segment),
            ))
        if segments:
            ecoutes.append(memoire.Ecoute(debut=manifeste["debut"], fin=_fin(manifeste), segments=segments))
    if ecoutes:
        await memoire.consolider_ecoute(ecoutes)


def _marqueur(chemin: Path, manifeste: dict, segment: dict):
    def marquer() -> None:
        segment["etat"] = "consolide"
        enregistreur.sauver_manifeste(chemin, manifeste)
    return marquer


def _fin(manifeste: dict) -> str | None:
    """L'heure de fin de la séance ; pour une séance coupée par un plantage, la fin du dernier segment."""
    if manifeste.get("fin"):
        return manifeste["fin"]
    segments = manifeste["segments"]
    if not segments or not segments[-1].get("duree_s"):
        return None
    dernier = segments[-1]
    fin = datetime.fromisoformat(dernier["debut"]) + timedelta(seconds=dernier["duree_s"])
    return fin.isoformat(timespec="seconds")
