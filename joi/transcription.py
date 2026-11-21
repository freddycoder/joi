"""La transcription différée du mode écoute : l'audio enregistré est envoyé à AssemblyAI, avec la
reconnaissance des voix, puis ce qui a été entendu est confié à la mémoire. Cela se fait en fin
d'écoute, et aussi pendant une longue écoute (toutes les JOI_APPRENDRE_MINUTES) pour les segments
déjà terminés : Joi sait ce qui s'est passé dans la dernière heure sans attendre la fin de la journée.

Le parcours d'un segment (voir le manifeste décrit dans joi/enregistreur.py) :

    enregistre ──► transcrit ──► consolide
        │
        └──► echec   (après ESSAIS_MAX refus d'AssemblyAI ; l'audio est gardé pour qu'on puisse l'examiner)

1. Envoi du MP3 (POST /v2/upload), création de la transcription (POST /v2/transcript), puis
   attente du résultat (GET /v2/transcript/{id}). L'identifiant est noté dans le manifeste :
   si Joi est arrêtée pendant l'attente, on reprend la même transcription au lieu de payer deux fois.
2. Le résultat brut est gardé à côté de l'audio (HHMMSS.json). C'est la source de vérité pour la
   suite. La transcription est ensuite effacée des serveurs d'AssemblyAI (avec l'audio envoyé).
3. Les voix sont reconnues sur l'ordinateur (joi/empreintes.py), tant que le MP3 existe encore :
   « toi », ou un groupe « inconnu-HHMMSS-n » (HHMMSS.voix.json). Sans PyTorch, ou avec
   JOI_RECONNAISSANCE_VOIX=non, on garde les étiquettes A/B d'AssemblyAI.
4. Les phrases (« utterances ») deviennent des entrées du journal, datées de l'heure réelle où
   elles ont été dites : début du segment + start (ms). Une phrase où l'on reconnaît plusieurs
   voix est découpée. Le segment passe à « transcrit » et, sauf si JOI_GARDER_AUDIO=oui, le MP3
   est supprimé.
5. La bibliothécaire reçoit les segments transcrits, séance par séance (memoire.consolider_ecoute).
   Juste avant, les groupes inconnus de chaque segment sont rattachés aux voix de la journée
   (« voix inconnue N »). Chaque segment passe à « consolide » une fois intégré. On ne passe pas
   par le marqueur .consolide.json du journal : les phrases d'une écoute sont datées dans le passé
   et seraient ignorées si une conversation plus récente avait déjà été consolidée.
6. Le soir (JOI_FIN_JOURNEE), une fois toute la journée consolidée : le bilan des voix va dans le
   résumé de la journée, et les empreintes anonymes du jour sont effacées (fermer_journees). Si
   l'ordinateur était fermé ou Joi arrêtée, c'est fait au prochain démarrage (joi/serveur.py).

Une journée apprise avant la reconnaissance des voix (ou avec de mauvaises empreintes) peut être
reprise tant que ses MP3 existent : reconsolider_journee() recalcule ses voix et demande à la
bibliothécaire de CORRIGER ce qu'elle en avait tiré (python -m joi.empreintes reconsolider AAAA-MM-JJ).

Tout ce qui échoue (réseau, Copilot, arrêt de Joi) est repris au prochain appel d'apprendre().
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from . import config, empreintes, enregistreur, journal, memoire, monitoring

log = logging.getLogger("joi.transcription")

URL_API = "https://api.assemblyai.com/v2"
LANGUE = "fr"

# Prix en dollars US par heure d'audio, relevés sur assemblyai.com/pricing le 2026-11-12
# (assemblyai-stt-3-7 le 2026-11-19).
PRIX_HEURE = {"assemblyai-stt-3-7": 0.21, "universal-3-5-pro": 0.21, "universal-2": 0.15}
PRIX_VOIX_HEURE = 0.02  # speaker_labels, en différé

TRANSCRIPTIONS_SIMULTANEES = 3
INTERVALLE_SONDAGE = 5  # secondes entre deux demandes d'état
DELAI_MAX_TRANSCRIPTION = 30 * 60  # au-delà, on abandonne pour cette fois (l'id est gardé pour reprendre)
ESSAIS_MAX = 3  # refus d'AssemblyAI avant de classer un segment en échec

_verrou = asyncio.Lock()
_verrou_bilan = asyncio.Lock()  # le bilan d'une journée ne doit pas s'écrire deux fois


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
        try:
            await fermer_journees()
        except Exception:
            log.exception("Bilan des voix de la journée impossible")


# --- Transcription -----------------------------------------------------------------------------

async def transcrire_en_attente() -> None:
    a_faire = [
        (chemin, manifeste, segment)
        for chemin, manifeste, active in enregistreur.seances()
        for segment in manifeste["segments"]
        # « en_cours » : dans une séance active, le segment qui s'écrit (on n'y touche pas) ;
        # dans une séance terminée, un segment coupé par un plantage.
        if segment.get("etat") == "enregistre" or (segment.get("etat") == "en_cours" and not active)
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
    monitoring.transcription["secondes"] += secondes
    monitoring.transcription["cout"] += cout
    monitoring.transcription["segments"] += reussis
    monitoring.transcription["derniere"] = datetime.now().isoformat(timespec="seconds")
    log.info(
        "Écoute transcrite : %d segment(s) sur %d, %.2f h d'audio facturées, environ %.2f $ US (%.0f s)",
        reussis, len(a_faire), secondes / 3600, cout, time.perf_counter() - debut,
    )
    noter_cout_des_jours({segment["debut"][:10] for _, _, segment in a_faire if segment.get("debut")})


def cout_du_jour(jour: str) -> float:
    """Le coût estimé (dollars US) des segments transcrits ce jour-là, gardé dans les manifestes : il
    tient même si Joi a été relancée entre deux écoutes (le lot, lui, ne compte que sa part). Un
    segment transcrit avant que le coût soit gardé est estimé d'après sa durée."""
    total = 0.0
    for _, manifeste, _ in enregistreur.seances():
        for segment in manifeste.get("segments", []):
            if not str(segment.get("debut", "")).startswith(jour):
                continue
            if segment.get("cout") is not None:
                total += segment["cout"]
            elif segment.get("etat") in ("transcrit", "consolide"):
                total += _facture({}, segment)[1]
    return total


def noter_cout_des_jours(jours: set[str]) -> None:
    """Le cumul du jour au journal, après chaque lot : la révision des données le reprend tel quel."""
    for jour in sorted(jours):
        log.info("Coût de transcription du %s : %.2f $ US", jour, cout_du_jour(jour))


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
            segment["cout"] = round(facture[1], 4)  # enregistré avec le manifeste, pour le coût du jour

        voix = await _reconnaitre_voix(mp3, segment, resultat)
        entrees = entrees_du_segment(segment, resultat, voix)
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


async def _reconnaitre_voix(mp3: Path, segment: dict, resultat: dict) -> dict | None:
    """Les voix reconnues dans un segment : déjà calculées (reprise après un plantage), ou calculées
    maintenant, tant que l'audio existe. None si la reconnaissance n'est pas possible : on garde
    alors les étiquettes d'AssemblyAI. Un échec ne bloque jamais la transcription."""
    voix = empreintes.lire_voix(segment)
    if voix is not None or not empreintes.active() or not mp3.exists():
        return voix
    try:
        return await asyncio.to_thread(empreintes.analyser_segment, mp3, segment, resultat)
    except Exception:
        log.exception("Reconnaissance des voix du segment %s impossible", segment["fichier"])
        return None


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


def entrees_du_segment(segment: dict, resultat: dict, voix: dict | None = None) -> list[dict]:
    """Les phrases d'une transcription, en entrées de journal datées de l'heure où elles ont été dites.
    Avec les voix reconnues, le locuteur est « toi » ou « inconnu-HHMMSS-n » (sinon l'étiquette A/B
    d'AssemblyAI), et une phrase qui contient plusieurs voix est découpée."""
    debut = datetime.fromisoformat(segment["debut"])
    phrases = resultat.get("utterances")
    if voix is not None:
        phrases = empreintes.phrases_par_voix(resultat, voix)
    elif phrases is None and (resultat.get("text") or "").strip():
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
    affectations: dict[str, dict] = {}  # par jour : les groupes de chaque segment -> voix du jour
    for chemin, manifeste, active in enregistreur.seances():
        segments = []
        voix_reconnues = True
        for segment in manifeste["segments"]:
            if segment.get("etat") != "transcrit":
                continue
            try:
                resultat = json.loads((dossier / segment["transcription"]).read_text(encoding="utf-8"))
            except (KeyError, OSError, json.JSONDecodeError) as e:
                log.warning("Transcription du segment %s illisible : %s", segment["fichier"], e)
                continue
            voix = empreintes.lire_voix(segment)
            if voix is None:
                voix_reconnues = False
            entrees = await _entrees_nommees(segment, resultat, voix, affectations)
            segments.append(memoire.SegmentEcoute(
                debut=segment["debut"],
                entrees=entrees,
                integre=_marqueur(chemin, manifeste, segment),
            ))
        if not segments:
            continue
        legende = ""
        if voix_reconnues:
            try:
                legende = await asyncio.to_thread(empreintes.legende, manifeste["debut"][:10])
            except Exception:
                log.exception("Légende des voix de la journée impossible")
        if active:
            fin = _fin_segment(_dernier_transcrit(manifeste))
        else:
            fin = manifeste.get("fin") or _fin_segment(manifeste["segments"][-1])
        ecoutes.append(memoire.Ecoute(
            debut=manifeste["debut"],
            fin=fin,
            segments=segments,
            en_cours=active,
            deja_en_partie=any(s.get("etat") == "consolide" for s in manifeste["segments"]),
            voix_reconnues=voix_reconnues,
            legende_voix=legende,
            trous=trous_a_dire(manifeste),
        ))
    if ecoutes:
        await memoire.consolider_ecoute(ecoutes)


async def _entrees_nommees(segment: dict, resultat: dict, voix: dict | None,
                           affectations: dict[str, dict]) -> list[dict]:
    """Les entrées d'un segment, avec les voix de la journée : « toi », un prénom (accord au
    registre), « voix inconnue N ». affectations sert de cache, par jour."""
    entrees = entrees_du_segment(segment, resultat, voix)
    if voix is not None:
        jour = segment["fichier"][:10]
        if jour not in affectations:
            affectations[jour] = await asyncio.to_thread(empreintes.rattacher_jour, jour)
        for entree in entrees:
            entree["locuteur"] = empreintes.nom_affiche(entree["locuteur"], affectations[jour])
    return entrees


async def fermer_journees(forcer: list[str] | None = None) -> list[str]:
    """Pour chaque journée finie (après JOI_FIN_JOURNEE, ou passée) et entièrement consolidée : le
    bilan des voix va dans le résumé de la journée, puis les empreintes anonymes du jour sont
    effacées. forcer : ces journées-là sont fermées sans attendre l'heure. Renvoie les jours fermés."""
    fermes = []
    async with _verrou_bilan:
        for jour in empreintes.journees_ouvertes():
            if forcer is not None and jour not in forcer:
                continue
            if forcer is None and not empreintes.fin_de_journee_passee(jour):
                continue
            if not _journee_terminee(jour):
                if forcer is not None:
                    log.warning("Le %s n'est pas entièrement transcrit et consolidé : bilan reporté.", jour)
                continue
            texte = await asyncio.to_thread(empreintes.preparer_bilan, jour)
            if texte:
                await memoire.noter_voix_du_jour(jour, texte)
                log.info("Bilan des voix du %s ajouté au résumé de la journée.", jour)
            await asyncio.to_thread(empreintes.effacer_journee, jour)
            fermes.append(jour)
    return fermes


async def reconsolider_journee(jour: str) -> None:
    """Reprend une journée d'écoute déjà apprise, avec les voix reconnues : calcule les voix de ses
    segments (il faut encore leurs MP3, ou le cache du rejeu), puis la bibliothécaire CORRIGE le
    résumé et les attributions qu'elle avait tirés des étiquettes A/B. Le journal brut n'est pas
    réécrit. Le bilan des voix suit, puis les empreintes anonymes du jour sont effacées."""
    if not empreintes.active():
        raise RuntimeError(f"reconnaissance des voix indisponible : {empreintes.etat()}")
    dossier = config.DOSSIER_ENREGISTREMENTS
    seances = [
        (chemin, manifeste, [s for s in manifeste["segments"] if s["fichier"].startswith(jour + "/")
                             and s.get("etat") in ("transcrit", "consolide")])
        for chemin, manifeste, active in enregistreur.seances() if not active
    ]
    seances = [(c, m, segs) for c, m, segs in seances if segs]
    if not seances:
        raise RuntimeError(f"aucune écoute transcrite le {jour}")

    # 1. Les voix de chaque segment (avant tout rattachement : l'ordre chronologique compte).
    resultats: dict[str, dict] = {}
    for _chemin, _manifeste, segments in seances:
        for segment in segments:
            resultat = json.loads((dossier / segment["transcription"]).read_text(encoding="utf-8"))
            resultats[segment["fichier"]] = resultat
            if empreintes.lire_voix(segment) is not None:
                continue
            mp3 = dossier / segment["fichier"]
            if not mp3.exists():
                log.warning("Segment %s : plus d'audio, ses voix ne peuvent pas être reconnues.", segment["fichier"])
                continue
            await asyncio.to_thread(empreintes.analyser_segment, mp3, segment, resultat, True)

    # 2. Les séances, avec les voix de la journée, en mode correction.
    affectations: dict[str, dict] = {}
    legende = await asyncio.to_thread(empreintes.legende, jour)
    ecoutes = []
    for chemin, manifeste, segments in seances:
        segments_ecoute = []
        for segment in segments:
            voix = empreintes.lire_voix(segment)
            entrees = await _entrees_nommees(segment, resultats[segment["fichier"]], voix, affectations)
            segments_ecoute.append(memoire.SegmentEcoute(
                debut=segment["debut"],
                entrees=entrees,
                # Un segment pas encore consolidé l'est maintenant ; les autres restent « consolide ».
                integre=_marqueur(chemin, manifeste, segment) if segment.get("etat") == "transcrit" else (lambda: None),
            ))
        ecoutes.append(memoire.Ecoute(
            debut=manifeste["debut"],
            fin=manifeste.get("fin") or _fin_segment(manifeste["segments"][-1]),
            segments=segments_ecoute,
            voix_reconnues=True,
            legende_voix=legende,
            correction=True,
        ))
    await memoire.consolider_ecoute(ecoutes)


def trous_a_dire(manifeste: dict, minimum_s: float = enregistreur.TROU_A_DIRE_S) -> list[tuple[str, str]]:
    """Les plages sans audio d'une séance (garde-fou du serveur), d'au moins `minimum_s`, en « HH:MM »."""
    return [(t["debut"][11:16], t["fin"][11:16]) for t in manifeste.get("trous") or []
            if t.get("fin") and (t.get("duree_s") or 0) >= minimum_s]


def _journee_terminee(jour: str) -> bool:
    """Vrai si aucune écoute de ce jour n'est en cours ni en attente de transcription ou de
    consolidation (le bilan arrive après la dernière consolidation)."""
    for _chemin, manifeste, active in enregistreur.seances():
        du_jour = [s for s in manifeste["segments"] if s["fichier"].startswith(jour + "/")]
        if du_jour and (active or any(s.get("etat") not in ("consolide", "echec") for s in du_jour)):
            return False
    return True


def _marqueur(chemin: Path, manifeste: dict, segment: dict):
    def marquer() -> None:
        segment["etat"] = "consolide"
        enregistreur.sauver_manifeste(chemin, manifeste)
    return marquer


def _dernier_transcrit(manifeste: dict) -> dict | None:
    """Le dernier segment déjà transcrit (ou consolidé) d'une séance."""
    faits = [s for s in manifeste["segments"] if s.get("etat") in ("transcrit", "consolide")]
    return faits[-1] if faits else None


def _fin_segment(segment: dict | None) -> str | None:
    """L'heure de fin d'un segment. Sert de fin de séance pour une séance coupée par un plantage,
    ou pour la partie déjà apprise d'une séance toujours en cours."""
    if not segment or not segment.get("duree_s"):
        return None
    fin = datetime.fromisoformat(segment["debut"]) + timedelta(seconds=segment["duree_s"])
    return fin.isoformat(timespec="seconds")
