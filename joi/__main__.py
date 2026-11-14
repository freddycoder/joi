"""Démarre Joi : python -m joi"""

import logging
import logging.handlers
import threading
import webbrowser

import uvicorn

from . import config


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # Le journal technique est aussi gardé sur le disque (5 fichiers de 2 Mo au plus), pour retrouver
    # après coup ce qui s'est passé pendant une écoute.
    fichier = logging.handlers.RotatingFileHandler(
        config.DOSSIER_LOGS / "joi.log", maxBytes=2_000_000, backupCount=4, encoding="utf-8"
    )
    fichier.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(fichier)
    url = f"http://localhost:{config.JOI_PORT}"
    print(f"\n  Joi se réveille sur {url}\n")
    threading.Timer(1.5, webbrowser.open, args=[url]).start()
    uvicorn.run("joi.serveur:app", host="127.0.0.1", port=config.JOI_PORT, log_level="warning")


if __name__ == "__main__":
    main()
