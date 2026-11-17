"""Banc d'essai des modèles Copilot : combien de temps avant la première phrase de Joi ?

    python -m joi.banc --lister                  les modèles disponibles (sans rien consommer)
    python -m joi.banc                           le modèle en vigueur (JOI_MODELE, ou le choix automatique)
    python -m joi.banc MODELE [MODELE…] [--phrases N]

Sans micro, sans AssemblyAI, sans synthèse : une dizaine de phrases tapées, envoyées une à une au
cerveau de Joi (sa personnalité et sa mémoire, en lecture seule ; pas le compte). Chaque phrase
consomme une requête Copilot du modèle essayé : regarde son multiplicateur avec --lister.

Rien n'est écrit dans le journal ni dans la mémoire, et JOI_MODELE n'est pas changé : le tableau aide
à choisir (avec la qualité des réponses et les quotas), le choix reste le tien.
"""

import argparse
import asyncio
import logging
import statistics
import time

from . import config
from .cerveau import CONSULTATION, RECHERCHE, Cerveau, arreter_client_partage

PHRASES = [
    "Salut Joi, comment ça va ce matin ?",
    "Donne-moi une idée de souper rapide avec des pâtes.",
    "C'est quoi la différence entre une vis à bois et une vis à métal ?",
    "Raconte-moi une blague courte.",
    "Combien de temps faut-il pour que la teinture sèche sur du chêne ?",
    "J'ai mal dormi, as-tu un conseil pour rester concentré ?",
    "Explique-moi en deux phrases comment marche un frigo.",
    "Quel temps fait-il d'habitude à Québec en novembre ?",
    "Propose-moi un nom pour un chat roux.",
    "Merci, c'est tout pour l'instant.",
]


def _centile(valeurs: list[float], p: float) -> float | None:
    if not valeurs:
        return None
    valeurs = sorted(valeurs)
    return valeurs[min(len(valeurs) - 1, int(p * len(valeurs)))]


async def mesurer(modele: str | None, phrases: list[str]) -> dict:
    """Réveille un cerveau avec ce modèle et lui envoie les phrases une à une."""
    cerveau = Cerveau(modele=modele, avec_compte=False)
    debut = time.perf_counter()
    await cerveau.demarrer()
    reveil = time.perf_counter() - debut
    premiers_mots, premieres_phrases, completes, recherches = [], [], [], 0
    try:
        for numero, phrase in enumerate(phrases, 1):
            premiere = None
            debut = time.monotonic()
            async for morceau in cerveau.repondre(phrase):
                if isinstance(morceau, (RECHERCHE, CONSULTATION)):
                    recherches += isinstance(morceau, RECHERCHE)
                    continue
                if premiere is None:
                    premiere = time.monotonic() - debut
            complete = time.monotonic() - debut
            premier_mot = (cerveau.premier_token - cerveau.envoi) if cerveau.premier_token else None
            print(f"  {numero:2}. 1er mot {_s(premier_mot)}, 1re phrase {_s(premiere)}, "
                  f"complète {_s(complete)} : {phrase}")
            if premier_mot is not None:
                premiers_mots.append(premier_mot)
            if premiere is not None:
                premieres_phrases.append(premiere)
            completes.append(complete)
    finally:
        await cerveau.arreter()
    return {
        "reveil": reveil,
        "premier_mot": statistics.median(premiers_mots) if premiers_mots else None,
        "premiere_phrase": statistics.median(premieres_phrases) if premieres_phrases else None,
        "premiere_phrase_p90": _centile(premieres_phrases, 0.9),
        "complete": statistics.median(completes) if completes else None,
        "recherches": recherches,
    }


def _s(valeur: float | None) -> str:
    return f"{valeur:.1f} s" if valeur is not None else "—"


async def lister() -> None:
    from copilot import CopilotClient

    client = CopilotClient()
    await client.start()
    try:
        modeles = await client.list_models()
    finally:
        await client.stop()
    if not modeles:
        # Constaté le 2026-11-17 avec le SDK 1.0.31 : la CLI répond une liste vide, même connectée.
        print("Copilot n'a renvoyé aucun modèle. Les identifiants (et leur multiplicateur) sont dans la CLI "
              "Copilot : commande /model.")
    else:
        print(f"{'modèle':32} {'multiplicateur':>14}  nom")
    for m in sorted(modeles, key=lambda m: m.id):
        if m.policy and m.policy.state == "disabled":
            continue
        multiplicateur = m.billing.multiplier if m.billing and m.billing.multiplier is not None else "?"
        print(f"{m.id:32} {multiplicateur!s:>14}  {m.name}")
    print(f"\nEn vigueur : {config.JOI_MODELE or 'choix automatique de Copilot'} (JOI_MODELE)")


async def principal(modeles: list[str], nombre: int) -> None:
    phrases = PHRASES[:nombre]
    resultats = {}
    try:
        for modele in modeles or [config.JOI_MODELE]:
            nom = modele or "automatique"
            print(f"\n{nom} : {len(phrases)} phrase(s)")
            try:
                resultats[nom] = await mesurer(modele, phrases)
            except Exception as e:
                print(f"  échec : {e}")
    finally:
        await arreter_client_partage()
    if not resultats:
        return
    print(f"\n{'modèle':28} {'réveil':>7} {'1er mot':>8} {'1re phrase':>11} {'(p90)':>7} {'complète':>9} {'recherches':>11}")
    for nom, r in resultats.items():
        print(f"{nom:28} {_s(r['reveil']):>7} {_s(r['premier_mot']):>8} {_s(r['premiere_phrase']):>11} "
              f"{_s(r['premiere_phrase_p90']):>7} {_s(r['complete']):>9} {r['recherches']:>11}")
    print("\nMédianes, sans micro ni synthèse. Le choix de JOI_MODELE reste le tien.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m joi.banc", description=__doc__.splitlines()[0])
    parser.add_argument("modeles", nargs="*", help="modèles à essayer (par défaut : celui de JOI_MODELE)")
    parser.add_argument("--phrases", type=int, default=len(PHRASES), help="nombre de phrases par modèle")
    parser.add_argument("--lister", action="store_true", help="lister les modèles disponibles")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if arguments.lister:
        asyncio.run(lister())
    else:
        asyncio.run(principal(arguments.modeles, max(1, min(arguments.phrases, len(PHRASES)))))


if __name__ == "__main__":
    main()
