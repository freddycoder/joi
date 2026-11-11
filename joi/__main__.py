"""Démarre Joi : python -m joi"""

import logging
import threading
import webbrowser

import uvicorn

from . import config


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    url = f"http://localhost:{config.JOI_PORT}"
    print(f"\n  Joi se réveille sur {url}\n")
    threading.Timer(1.5, webbrowser.open, args=[url]).start()
    uvicorn.run("joi.serveur:app", host="127.0.0.1", port=config.JOI_PORT, log_level="warning")


if __name__ == "__main__":
    main()
