@echo off
cd /d "%~dp0"
if not exist .venv (
    echo Installation de l'environnement Python...
    py -3.12 -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt
)
if not exist .env (
    copy .env.example .env >nul
    echo Un fichier .env a ete cree. Ajoute ta cle AssemblyAI dedans, puis relance.
    notepad .env
    exit /b
)
.venv\Scripts\python -m joi
