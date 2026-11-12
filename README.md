# Joi

Une compagne vocale holographique, inspirée du personnage de *Blade Runner 2049*.
On lui parle au micro, elle répond à voix haute, et un hologramme réagit à l'écran.
Elle peut aussi écouter en silence toute une journée et en retenir l'essentiel.

![Joi en train de parler](docs/capture.png)

## Architecture

```
Navigateur                          Serveur Python (FastAPI)                   Services
──────────                          ────────────────────────                   ────────
micro ──PCM 16 kHz──► /ws ──► oreilles.py ──WebSocket──────────────► AssemblyAI (streaming)
                                   │  tour de parole terminé
                                   ▼
                              cerveau.py ──Copilot SDK──────────────► GitHub Copilot (LLM)
                                   │  réponse, phrase par phrase
                                   ▼
haut-parleur ◄──MP3──────────  voix.py ──────────────────────────────► edge-tts (voix fr-CA) ou AssemblyAI
hologramme (réagit au son)

Mode écoute :  micro ──► enregistreur.py (MP3 local) ──► transcription.py ──► AssemblyAI (différé)
                                                                │
                                                                ▼
                                                      memoire.py (bibliothécaire Copilot)
```

| Fichier | Rôle |
|---|---|
| `joi/oreilles.py` | Transcription en temps réel avec l'API Streaming v3 d'AssemblyAI |
| `joi/cerveau.py` | Conversation avec Copilot, réponse diffusée et découpée en phrases |
| `joi/personnalite.md` | La personnalité de Joi (prompt système) : modifie-la librement |
| `joi/voix.py` | Synthèse vocale (edge-tts ou une voix AssemblyAI), avec cache des phrases déjà prononcées |
| `joi/journal.py` | Journal brut des conversations et des écoutes, mot pour mot, un fichier par jour |
| `joi/memoire.py` | Mémoire à long terme : contexte au réveil, outil `se_souvenir`, consolidation |
| `joi/bibliothecaire.md` | Consignes de la passe qui range les souvenirs |
| `joi/enregistreur.py` | Mode écoute : enregistre le micro en segments MP3 |
| `joi/transcription.py` | Mode écoute : transcription différée, avec reconnaissance des voix |
| `joi/commande_vocale.py` | « Joi, écoute seulement » et « Joi, reviens » |
| `joi/serveur.py` | Relie le tout, gère les modes et les interruptions |
| `frontend/` | Interface : hologramme, micro, lecture audio, sous-titres |

**Diagnostics.** `Alt+P` ouvre les paramètres en vigueur (panneau de gauche), `Alt+M` le monitoring
(panneau de droite : signal du micro et de la voix, temps de réponse, voix, écoute, transcription, mémoire,
derniers événements du serveur), `Alt+I` les deux, `Échap` les ferme. L'hologramme reste au centre.

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

Après une mise à jour du projet, relance `.venv\Scripts\pip install -r requirements.txt`
(le `.bat` n'installe les dépendances qu'à la création de `.venv`).

## Mode écoute

Pour la journée à l'atelier : Joi se tait, enregistre ce qu'elle entend, et l'apprend plus tard.

- **Passer en écoute** : le bouton « écoute seule », ou dire « Joi, écoute seulement ».
  L'hologramme s'atténue et un indicateur rouge affiche la durée de l'enregistrement.
- **Revenir** : le bouton « revenir », ou dire « Joi, reviens » (elle répond « Je suis là. »).
  Cette phrase est reconnue hors ligne par [Vosk](https://alphacephei.com/vosk/), avec un petit modèle
  français (~40 Mo) téléchargé une seule fois dans `donnees/vosk/`. `JOI_COMMANDE_VOCALE=non` la désactive.
- **Terminer la journée** : revenir en conversation, ou simplement fermer l'onglet. Le segment en cours
  est finalisé proprement. Laisse le serveur tourner quelques minutes, le temps de la transcription.

L'audio est enregistré localement, en segments de 30 minutes, dans `donnees/enregistrements/`.
Au retour, chaque segment est transcrit par AssemblyAI (en différé, avec la séparation des voix A, B, C…),
puis la bibliothécaire en retient ce qui te concerne et écrit un résumé dans `journees/AAAA-MM-JJ.md`.
Pendant une longue écoute, Joi n'attend pas la fin : après chaque heure d'audio (`JOI_APPRENDRE_MINUTES`,
arrondi aux segments terminés), elle transcrit et apprend la dernière heure pendant que l'enregistrement
continue. Au retour, il ne reste que la fin à traiter. `JOI_APPRENDRE_MINUTES=0` revient à tout faire à la fin.
Si Joi est arrêtée en cours de route, la suite est reprise au prochain démarrage.

Pendant l'écoute, l'écran reste allumé (Wake Lock) pour que le PC ne se mette pas en veille.
Garde l'onglet ouvert et branche le portable.

**Coûts.** Le streaming n'est pas utilisé en écoute : on ne paie que la transcription différée,
environ 0,23 $ US de l'heure (modèle Pro et séparation des voix). Une journée de 8 h revient à moins de 2 $.
La console affiche les heures transcrites et une estimation du coût après chaque écoute.

**Vie privée.** Les personnes autour (collègues, clients) devraient savoir qu'elles sont enregistrées.
L'audio passe par AssemblyAI, puis le texte par Copilot. Pour limiter l'exposition :

- l'audio est supprimé de ton disque après sa transcription (`JOI_GARDER_AUDIO=oui` pour le garder),
  et la transcription est effacée des serveurs d'AssemblyAI ;
- la mémoire ne retient que ce qui te concerne, toi et tes projets : pas de dossier sur les autres.

## Mémoire

Joi se souvient de vos conversations et de ce qu'elle a entendu. Tout reste sur ton ordinateur,
dans `donnees/` (ignoré par git, jamais publié) :

```
donnees/
├── journal/AAAA-MM-JJ.jsonl   chaque échange et chaque phrase entendue, mot pour mot
├── enregistrements/           mode écoute : audio en attente et transcriptions brutes
└── memoire/                   ses souvenirs en Markdown, organisés librement par Joi
    ├── toi.md                 l'essentiel sur toi, lu à chaque réveil
    ├── index.md               la carte de sa mémoire, lue à chaque réveil
    └── journees/              un résumé par journée d'écoute
```

- **Au réveil**, `toi.md` et `index.md` sont ajoutés à sa personnalité.
- **En conversation**, elle peut chercher un détail avec l'outil `se_souvenir`
  (dans ses fichiers, puis dans le journal).
- **Après une conversation ou une écoute** (et 5 minutes après le démarrage si personne ne lui parle,
  pour rattraper un arrêt brutal), une session Copilot séparée, la bibliothécaire, relit le journal et
  range les souvenirs : elle crée, fusionne, déplace et corrige des fichiers à sa guise, sans pouvoir
  sortir de `donnees/memoire/`.

Chaque rangement est enregistré dans un dépôt git **local** (sans remote) :
`git -C donnees/memoire log -p` montre ce qu'elle a retenu, et on peut annuler une passe ratée.
Tu peux aussi corriger ses fichiers à la main. Pour repartir à zéro, supprime `donnees/memoire/`,
`donnees/journal/` et `donnees/enregistrements/`.

## Prochaines étapes envisagées

- Données structurées (SQLite) : rappels, agenda
- Compétences sous forme d'outils : nouvelles, rappels, agenda, tutorat
- Reconnaître ta voix d'une journée à l'autre (empreinte vocale)
- Avatar 2D/3D avec synchronisation des lèvres
- Application de bureau (Tauri)
