# Joi

Une compagne vocale holographique, inspirée du personnage de *Blade Runner 2049*.
On lui parle au micro, elle répond à voix haute, et un hologramme réagit à l'écran.

![Joi en train de parler](docs/capture.png)

## Architecture

```
Navigateur                          Serveur Python (FastAPI)                   Services
──────────                          ────────────────────────                   ────────
micro ──PCM 16 kHz──► /ws ──► oreilles.py ──WebSocket──────────────► AssemblyAI (speech-to-text)
                                   │  tour de parole terminé
                                   ▼
                              cerveau.py ──Copilot SDK──────────────► GitHub Copilot (LLM)
                                   │  réponse, phrase par phrase
                                   ▼
haut-parleur ◄──MP3──────────  voix.py ──────────────────────────────► edge-tts (voix fr-CA)
hologramme (réagit au son)
```

| Fichier | Rôle |
|---|---|
| `joi/oreilles.py` | Transcription en temps réel avec l'API Streaming v3 d'AssemblyAI |
| `joi/cerveau.py` | Conversation avec Copilot, réponse diffusée et découpée en phrases |
| `joi/personnalite.md` | La personnalité de Joi (prompt système) : modifie-la librement |
| `joi/journal.py` | Journal brut des conversations, mot pour mot, un fichier par jour |
| `joi/memoire.py` | Mémoire à long terme : contexte au réveil, outil `se_souvenir`, consolidation |
| `joi/bibliothecaire.md` | Consignes de la passe qui range les souvenirs après chaque conversation |
| `joi/voix.py` | Synthèse vocale, avec cache des phrases déjà prononcées |
| `joi/serveur.py` | Relie le tout et gère les interruptions |
| `frontend/` | Interface : hologramme, micro, lecture audio, sous-titres |

On peut interrompre Joi en lui parlant par-dessus : elle se tait et écoute.
On peut aussi lui écrire au clavier (champ en bas de l'écran).

Si Joi comprend mal un nom propre ou un mot inventé (« list d'or » au lieu de « Lysdor »),
ajoute-le à `JOI_MOTS_CLES` dans `.env`, séparé des autres par des virgules.

## Prérequis

- Windows, Python 3.12, un navigateur récent (Firefox, Chrome ou Edge)
- Un abonnement GitHub Copilot (la CLI Copilot doit être connectée à ton compte)
- Une clé API [AssemblyAI](https://www.assemblyai.com/dashboard)

## Démarrer

Double-clique sur `reveiller_joi.bat`. Au premier lancement, il installe l'environnement,
crée le fichier `.env` et l'ouvre : colle ta clé AssemblyAI, enregistre, puis relance.

À la main :

```
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      (puis ajoute ta clé)
.venv\Scripts\python -m joi
```

L'interface s'ouvre sur http://localhost:8049. Clique sur « Réveiller Joi » et autorise le micro.
Des écouteurs aident si Joi s'entend elle-même et s'interrompt.

## Mémoire

Joi se souvient de vos conversations. Tout reste sur ton ordinateur, dans `donnees/`
(ignoré par git, jamais publié) :

```
donnees/
├── journal/AAAA-MM-JJ.jsonl   chaque échange, mot pour mot (jamais modifié)
└── memoire/                   ses souvenirs en Markdown, organisés librement par Joi
    ├── toi.md                 l'essentiel sur toi, lu à chaque réveil
    └── index.md               la carte de sa mémoire, lue à chaque réveil
```

- **Au réveil**, `toi.md` et `index.md` sont ajoutés à sa personnalité.
- **En conversation**, elle peut chercher un détail avec l'outil `se_souvenir`
  (dans ses fichiers, puis dans le journal).
- **Après la conversation** (et 5 minutes après le démarrage si personne ne lui parle, pour rattraper un arrêt brutal), une session
  Copilot séparée relit le journal et range les souvenirs : elle crée, fusionne, déplace et
  corrige des fichiers à sa guise, sans pouvoir sortir de `donnees/memoire/`.

Chaque rangement est enregistré dans un dépôt git **local** (sans remote) :
`git -C donnees/memoire log -p` montre ce qu'elle a retenu, et on peut annuler une passe ratée.
Tu peux aussi corriger ses fichiers à la main. Pour repartir à zéro, supprime `donnees/memoire/`
et `donnees/journal/`.

## Prochaines étapes envisagées

- Données structurées (SQLite) : rappels, agenda
- Compétences sous forme d'outils : nouvelles, rappels, agenda, tutorat
- Avatar 2D/3D avec synchronisation des lèvres
- Application de bureau (Tauri)
