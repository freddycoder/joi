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
                                                     empreintes.py (voix, sur l'ordinateur)
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
| `joi/transcription.py` | Mode écoute : transcription différée, avec séparation des voix par AssemblyAI |
| `joi/empreintes.py` | Mode écoute : reconnaissance des voix sur toute la journée (empreintes vocales locales) |
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
(le `.bat` n'installe les dépendances qu'à la création de `.venv`). La reconnaissance des voix ajoute
PyTorch (version CPU, environ 1 Go installé) : sans lui, Joi fonctionne, avec les étiquettes A/B d'AssemblyAI.

Le journal technique est gardé dans `donnees/logs/joi.log` (5 fichiers de 2 Mo au plus).

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

### Reconnaissance des voix

AssemblyAI sépare les voix (A, B, C…) segment par segment : le « A » de 9 h n'est pas forcément le « A »
de 14 h. Joi reconnaît donc elle-même les voix sur toute la journée, **sans rien envoyer ailleurs**
(`joi/empreintes.py`, modèle SpeechBrain ECAPA, ~80 Mo téléchargés une fois) :

- **Ta voix** s'apprend pendant vos conversations : à la fin de chaque tour d'au moins 2 s, l'audio du
  tour (gardé en mémoire, jamais écrit) donne une empreinte qui affine la tienne (`donnees/empreintes/toi.npz`).
  Les tours où Joi parle en même temps sont ignorés.
- **En écoute**, avant que l'audio d'un segment soit effacé, la parole est découpée en fenêtres de 1,5 à 4 s.
  Chaque fenêtre est comparée à ta voix ; les autres sont regroupées en « voix inconnue 1 », « 2 »…, les
  mêmes toute la journée. Une phrase où deux voix se suivent est découpée.
- **Le soir** (`JOI_FIN_JOURNEE`, 18:00 par défaut, ou à la fin de l'écoute si elle déborde), le résumé
  de la journée reçoit un bilan : « 4 interlocuteurs en plus de toi, et quelques voix de passage ».
  Une voix qui parle longtemps sans jamais te répondre est une « voix de média probable » (vidéo,
  conférence) ; moins de 30 s dans la journée (`JOI_VOIX_INTERLOCUTEUR_S`), une « voix de passage ».
  Ensuite, **toutes les empreintes anonymes du jour sont effacées** : seules les étiquettes restent.
  Si le portable était fermé à 18:00, ou Joi arrêtée, le bilan est fait dès le prochain démarrage
  (et Joi revérifie toutes les 15 min) : les empreintes anonymes restent donc au plus jusque-là.
  Pour le faire tout de suite, sans attendre le soir : `python -m joi.empreintes bilan AAAA-MM-JJ`.
- **Une journée apprise avant** la reconnaissance des voix (ou avec de mauvaises empreintes) peut être
  reprise tant que ses MP3 existent : `python -m joi.empreintes reconsolider AAAA-MM-JJ` calcule ses voix,
  demande à la bibliothécaire de **corriger** le résumé et les attributions (une session Copilot), puis
  fait le bilan. Le journal brut n'est pas réécrit. Arrête Joi pendant ce temps.

Une empreinte vocale est une donnée biométrique : par défaut, Joi ne garde que la tienne. Pour qu'elle
reconnaisse un collègue par son nom, il faut **son accord exprès**, inscrit au registre
`donnees/empreintes/consentements.json` ; le retrait de l'accord efface son empreinte. Les clients restent anonymes.

```
.venv\Scripts\python -m joi.empreintes consentement ajouter Bruno --facon "accord verbal, en personne"
.venv\Scripts\python -m joi.empreintes enroler-voix 2026-11-13 2 --nom Bruno --ecouter
.venv\Scripts\python -m joi.empreintes consentement liste
.venv\Scripts\python -m joi.empreintes consentement retirer Bruno     efface aussi son empreinte
```

Refaire `enroler-voix` sur la même journée remplace les échantillons déjà tirés de cette journée : si tu
t'es trompé de numéro, recommence avec le bon. Attention, les numéros du rejeu changent quand une voix
devient connue : relance `rejouer` avant de choisir le suivant.

Régler les seuils sur une journée gardée avec `JOI_GARDER_AUDIO=oui`, sans repayer AssemblyAI :

```
.venv\Scripts\python -m joi.empreintes controle               le modèle reconnaît-il une même voix ?
.venv\Scripts\python -m joi.empreintes rejouer 2026-11-13     les voix trouvées, avec heures et extraits
.venv\Scripts\python -m joi.empreintes rejouer 2026-11-13 --seuil-journee 0.55 --details
.venv\Scripts\python -m joi.empreintes enroler-voix 2026-11-13 2 --ecouter   ta voix = la « voix inconnue 2 » du rejeu : extraits joués, puis confirmation
.venv\Scripts\python -m joi.empreintes enroler 2026-11-13 12:45:10-12:46:30   ta voix, sur une plage où tu parles seul
.venv\Scripts\python -m joi.empreintes oublier toi            efface ton empreinte
.venv\Scripts\python -m joi.empreintes effacer-rejeu          efface le cache du rejeu
```

Le rejeu n'écrit ni dans le journal ni dans la mémoire ; il garde les empreintes de chaque fenêtre dans
`donnees/empreintes/rejeu/` pour que les essais suivants soient instantanés (à effacer après les réglages).

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
├── enregistrements/           mode écoute : audio en attente, transcriptions brutes, voix reconnues
├── empreintes/                ton empreinte vocale, et celles du jour (anonymes, effacées le soir)
├── logs/                      journal technique de Joi
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

P1
- MCP avec icloud ou outlook avec un adresse courriel appartenant à Joi.
- Données structurées (SQLite) : rappels, agenda (est-ce nécessaire si on relie avec un courriel cloud?)
- Compétences sous forme d'outils : nouvelles, rappels, agenda, tutorat (à inclure dans le MCP)

P3
- Nommer une voix avec l'accord de la personne (« Joi, voici Denis, il est d'accord »)
- Avatar 2D/3D avec synchronisation des lèvres
- Application de bureau (Tauri)
