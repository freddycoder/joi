"""se_souvenir ne fige pas la boucle asyncio du serveur (audio, WebSocket, annonces)."""

import asyncio
import json
import logging
import time
from datetime import date, timedelta

from copilot.tools import ToolInvocation

from joi import config, memoire


def _gros_journal(jours: int = 20, entrees_par_jour: int = 1500) -> int:
    """Un journal synthétique (quelques Mo) : de quoi rendre la recherche lente. Renvoie sa taille."""
    taille = 0
    debut = date.today() - timedelta(days=jours)
    for j in range(jours):
        jour = (debut + timedelta(days=j)).isoformat()
        lignes = [
            json.dumps({"ts": f"{jour}T09:{i // 60 % 60:02d}:{i % 60:02d}.{i:06d}", "qui": "entendu",
                        "locuteur": "A", "source": "ecoute",
                        "texte": f"On parle de la planche numéro {i} et du vernis de l'établi, encore et encore."},
                       ensure_ascii=False)
            for i in range(entrees_par_jour)
        ]
        octets = ("\n".join(lignes) + "\n").encode("utf-8")
        (config.DOSSIER_JOURNAL / f"{jour}.jsonl").write_bytes(octets)  # sans \r\n de Windows : taille exacte
        taille += len(octets)
    return taille


async def _compter_ticks_pendant(coroutine) -> tuple[object, int, float]:
    ticks = 0
    fini = asyncio.Event()

    async def compter():
        nonlocal ticks
        while not fini.is_set():
            await asyncio.sleep(0.005)
            ticks += 1

    compteur = asyncio.create_task(compter())
    await asyncio.sleep(0)  # le compteur démarre
    debut = time.perf_counter()
    try:
        resultat = await coroutine
    finally:
        duree = time.perf_counter() - debut
        fini.set()
        await compteur
    return resultat, ticks, duree


def test_la_boucle_reste_libre_pendant_une_recherche(monkeypatch):
    lente = memoire.souvenir

    def souvenir_lent(*args, **kwargs):
        time.sleep(0.3)  # bloquant, comme une relecture de plusieurs Mo de journal
        return lente(*args, **kwargs)

    monkeypatch.setattr(memoire, "souvenir", souvenir_lent)
    invocation = ToolInvocation(tool_name="se_souvenir", arguments={"recherche": "vernis"})
    resultat, ticks, duree = asyncio.run(_compter_ticks_pendant(memoire.outil_se_souvenir.handler(invocation)))
    assert resultat.result_type == "success"
    assert duree >= 0.3
    # Boucle figée : 0 ou 1 tick. Libre : un tick toutes les ~5 à 16 ms (selon l'horloge de Windows).
    assert ticks >= 10, ticks


def test_recherche_sur_un_gros_journal(caplog):
    taille = _gros_journal()
    invocation = ToolInvocation(tool_name="se_souvenir", arguments={"recherche": "planche 1234 vernis"})
    with caplog.at_level(logging.INFO, logger="joi.memoire"):
        resultat, ticks, duree = asyncio.run(_compter_ticks_pendant(memoire.outil_se_souvenir.handler(invocation)))
    assert "planche numéro 1234" in resultat.text_result_for_llm
    assert ticks >= 2, (ticks, duree)
    # La durée et le volume lu sont notés au journal technique.
    ligne, = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Recherche ")]
    assert "20 jour(s) de journal" in ligne
    assert f"({taille // 1024} Ko)" in ligne
