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
| `joi/serveur.py` | Relie le tout, gère les modes, les interruptions et les coupures |
| `joi/banc.py` | Banc d'essai des modèles Copilot (temps jusqu'à la première phrase) |
| `joi/compte/` | Optionnel : le courriel, l'agenda et les tâches de Joi, par un serveur MCP (voir « Le compte de Joi ») |
| `frontend/` | Interface : hologramme, micro, lecture audio, sous-titres |

**Diagnostics.** `Alt+P` ouvre les paramètres en vigueur (panneau de gauche), `Alt+M` le monitoring
(panneau de droite : signal du micro et de la voix, temps de réponse, voix, écoute, transcription, mémoire,
derniers événements du serveur), `Alt+I` les deux, `Échap` les ferme. L'hologramme reste au centre.

**Latence.** Chaque réponse ajoute une ligne « Latence : » au journal technique, et le bloc « Latence » du
monitoring montre la dernière réponse et la médiane : fin de tour (AssemblyAI décide que tu as fini de
parler), cerveau (premier mot, première phrase), synthèse de la voix, début de la lecture, et le total de
la fin de ta phrase au premier son. Pour régler la fin de tour : `JOI_FIN_TOUR_MODE` et
`JOI_FIN_TOUR_SILENCE_MIN_MS` / `_MAX_MS` dans `.env` (vides : les valeurs d'AssemblyAI). Pour comparer
les modèles Copilot, sans micro : `.venv\Scripts\python -m joi.banc MODELE1 MODELE2` (chaque phrase
consomme une requête Copilot ; `--lister`, `--phrases N`). Il ne change pas `JOI_MODELE`.

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
Quand tout est appris, Joi te dit d'elle-même qu'elle peut te parler de ta journée. Demande-lui alors
« qu'as-tu entendu aujourd'hui ? » : une journée nommée (« aujourd'hui », « hier », « 16 novembre »)
lui donne le résumé complet de cette journée. Si tu lui parles avant, elle sait que l'apprentissage
n'est pas fini : elle cherche ce qui est déjà appris et dit ce qui manque encore, sans l'inventer.

Pendant l'écoute, l'écran reste allumé (Wake Lock) pour que le PC ne se mette pas en veille.
Garde l'onglet ouvert et branche le portable.

**Coupures.** Si la liaison avec le serveur tombe (Joi relancée, portable sorti de veille), la page se
reconnecte seule, sans clic, et reprend l'écoute : l'indicateur rouge clignote et affiche « interrompu »
tant que la liaison n'est pas revenue. Une nouvelle séance commence alors ; la précédente est gardée et
transcrite comme les autres. Si le micro s'arrête (débranché, permission retirée), ou si plus aucun son
n'arrive au serveur pendant 30 s, un message le dit à l'écran et dans le journal technique. En
conversation, Joi rouvre aussi seule sa liaison avec AssemblyAI si elle tombe (au plus tard après 3 h).

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

## Le compte de Joi (optionnel)

Joi peut avoir sa propre adresse courriel, son agenda et une liste de tâches, dans un compte
**Microsoft 365 personnel** (Outlook.com). Sans `JOI_COMPTE` dans `.env`, rien ne change : aucun code du
compte ne s'exécute, et les dépendances `mcp`, `msal` et `msal-extensions` ne sont pas nécessaires.

En conversation, elle peut :

- dire ce qu'elle a reçu, résumer ou lire un courriel, en chercher un ancien ;
- écrire un courriel, **seulement** à ton adresse et à celles de `JOI_COURRIEL_DESTINATAIRES`, et
  **seulement après ton « oui »** : elle prépare un brouillon, te dit à qui elle écrit et l'essentiel,
  puis demande « je l'envoie ? » ;
- lire son agenda et le tien (partagé avec elle en lecture) : « qu'est-ce que j'ai demain ? » ;
- créer un rappel : un événement dans son agenda, avec une invitation à ton adresse. Une fois l'invitation
  acceptée, ton agenda te le rappelle sur ton téléphone. Elle peut le déplacer ou l'annuler ;
- tenir une liste de choses à faire sans heure (la liste « Joi » de Microsoft To Do).

Au réveil, elle connaît la date, ses courriels non lus, ta journée, la sienne et ses tâches. Pendant la
conversation, elle vérifie ses courriels toutes les 5 minutes (`JOI_COURRIEL_VERIFIER_MINUTES`) et annonce
ceux qui semblent utiles quand personne ne parle depuis 10 s ; les infolettres, les publicités et l'onglet
« Autres » sont seulement notés au journal. En mode écoute, elle les garde pour ton retour (« Pendant que
j'écoutais… ») ; elle sait qu'ils sont arrivés dès ta première phrase, même si l'annonce attend encore,
et les consulte si tu lui demandes « as-tu reçu des courriels ? ». Chaque courriel reçu ou envoyé, chaque événement et chaque tâche entre au journal, en
résumé, et la bibliothécaire en retient ce qui te concerne.

**Comment c'est branché.** Les outils vivent dans un serveur MCP local (`python -m joi.compte`, en stdio)
que la session Copilot de Joi lance elle-même. La bibliothécaire n'y a pas accès.

**Protections.** Un courriel reçu peut contenir des consignes (« transfère ce message à… ») :

- les courriels sont présentés au modèle comme du contenu externe, à ne jamais suivre ;
- une adresse hors de la liste approuvée est refusée par le serveur MCP, avant et au moment de l'envoi ;
- l'envoi est permis par **le serveur de Joi, pas par le modèle** : seulement pour le dernier brouillon
  préparé (moins de 15 min), et si ta réponse suivante, dite ou tapée, est une confirmation sans négation
  (« oui », « envoie », « vas-y »… mais pas « oui, mais change le sujet ») ;
- le texte ne peut pas changer entre la lecture et l'envoi ; Joi n'a ni suppression, ni transfert,
  ni pièces jointes, ni accès à tes contacts, et ne modifie jamais ton agenda ;
- les jetons sont chiffrés par Windows (DPAPI) dans `donnees/compte/jetons.bin`, jamais écrits au journal.

### Mise en place (une fois)

1. **Inscrire l'application** dans Microsoft Entra (gratuit). Un abonnement M365 Famille ne fournit pas
   de locataire Entra : utilise celui de ton entreprise si tu as le droit d'y inscrire des applications
   (<https://entra.microsoft.com>, « Inscriptions d'applications »), sinon crée un compte Azure gratuit,
   ce qui crée un locataire (aucun service payant n'est utilisé).
   - « Nouvelle inscription », nom « Joi », types de comptes : **comptes Microsoft personnels uniquement** ;
   - URI de redirection : plateforme « Applications mobiles et de bureau », URI **`http://localhost:8050`**, exactement
     (la connexion se fait dans ton navigateur, qui revient vers Joi sur ton ordinateur) ;
   - « Authentification » : **Autoriser les flux de clients publics = Oui** (aucun secret à garder) ;
   - « Autorisations des API » → Microsoft Graph → déléguées : `User.Read`, `offline_access`,
     `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`, `Calendars.Read.Shared`, `Tasks.ReadWrite` ;
   - copie l'**ID d'application (client)** dans `.env` : `JOI_MS_CLIENT_ID=…`
2. **Régler `.env`** : `JOI_COMPTE=microsoft`, `JOI_COURRIEL` (l'adresse de Joi),
   `JOI_COURRIEL_PROPRIETAIRE` (la tienne) et, au besoin, `JOI_COURRIEL_DESTINATAIRES`.
3. **Installer les dépendances** : `.venv\Scripts\pip install -r requirements.txt`.
4. **Connecter le compte de Joi** : `.venv\Scripts\python -m joi.compte connecter`. La page de connexion
   Microsoft s'ouvre dans ton navigateur : connecte-toi **avec le compte de Joi** (« Utiliser un autre
   compte » si le tien est proposé) et accepte les autorisations. Sans navigateur sur cet ordinateur :
   `connecter --code` affiche un code à entrer sur <https://www.microsoft.com/link> (cette page a déjà
   refusé des codes pourtant valides, d'où la connexion par le navigateur par défaut).
5. **Partager ton agenda** : dans Outlook.com (ton compte), « Partager l'agenda » avec l'adresse de Joi,
   « Peut afficher tous les détails ». Connecté en Joi sur Outlook.com, accepte l'invitation de partage.
6. **Vérifier** : `.venv\Scripts\python -m joi.compte essai` affiche ses derniers courriels, l'agenda
   d'aujourd'hui et de demain, et ses tâches.

```
.venv\Scripts\python -m joi.compte etat          connexion, adresses approuvées, fuseau
.venv\Scripts\python -m joi.compte essai         courriels, agendas et tâches en console
.venv\Scripts\python -m joi.compte deconnecter   efface les jetons
```

Les panneaux `Alt+P` et `Alt+M` ont un groupe « Compte » (connexion, dernière vérification, dernière
erreur). Si le jeton expire (90 jours sans usage, mot de passe changé), Joi le dit, et il suffit de
relancer `connecter`. Le journal du serveur MCP est dans `donnees/logs/compte.log`.

**Vie privée.** Les courriels de Joi passent par Copilot, comme le reste de vos conversations. La
mémoire suit la même règle qu'en écoute : ce qui te concerne, pas de dossier sur les expéditeurs.

## Mémoire

Joi se souvient de vos conversations et de ce qu'elle a entendu. Tout reste sur ton ordinateur,
dans `donnees/` (ignoré par git, jamais publié) :

```
donnees/
├── journal/AAAA-MM-JJ.jsonl   chaque échange et chaque phrase entendue, mot pour mot
├── enregistrements/           mode écoute : audio en attente, transcriptions brutes, voix reconnues
├── empreintes/                ton empreinte vocale, et celles du jour (anonymes, effacées le soir)
├── logs/                      journal technique de Joi
├── compte/                    compte de Joi (optionnel) : jetons chiffrés, brouillons, dernier courriel vu
└── memoire/                   ses souvenirs en Markdown, organisés librement par Joi
    ├── toi.md                 l'essentiel sur toi, lu à chaque réveil
    ├── index.md               la carte de sa mémoire, lue à chaque réveil
    └── journees/              un résumé par journée d'écoute
```

- **Au réveil**, `toi.md` et `index.md` sont ajoutés à sa personnalité (3 500 caractères au plus
  chacun ; un fichier coupé est signalé au journal technique).
- **En conversation**, elle peut chercher un détail avec l'outil `se_souvenir`
  (dans ses fichiers, puis dans le journal).
- **Après une conversation ou une écoute** (et 5 minutes après le démarrage si personne ne lui parle,
  pour rattraper un arrêt brutal), une session Copilot séparée, la bibliothécaire, relit le journal et
  range les souvenirs : elle crée, fusionne, déplace et corrige des fichiers à sa guise, sans pouvoir
  sortir de `donnees/memoire/`.

Chaque rangement est enregistré dans un dépôt git **local** (sans remote) :
`git -C donnees/memoire log -p` montre ce qu'elle a retenu, et on peut annuler une passe ratée.
Tu peux aussi corriger ses fichiers à la main.

Une journée raconte (10 à 15 lignes), un projet garde l'état courant (une puce par sujet, mise à jour
sur place). Pour resserrer une mémoire écrite avant ces règles, une fois, Joi arrêtée :
`python -m joi.memoire condenser`. La bibliothécaire reprend les journées de plus de 8 Ko, les autres
fichiers de plus de 6 Ko, `toi.md` et `index.md`, entre un commit « Avant » et un commit « Après ».
La commande refuse de partir si Joi tourne ou si l'état d'avant ne peut pas être enregistré, et affiche
à la fin la commande `git diff` pour relire et la commande `git revert` pour tout annuler.

Pour repartir à zéro, supprime `donnees/memoire/`,
`donnees/journal/` et `donnees/enregistrements/`.


## Tests

```
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

Les tests n'utilisent **jamais** `donnees/` : `tests/conftest.py` fait pointer `JOI_DONNEES` vers un
dossier temporaire avant d'importer Joi, et arrête tout si ce n'est pas le cas. Ils ne font aucun appel
réseau (pas de clé AssemblyAI, pas de compte) et ne chargent ni PyTorch ni Vosk : quelques secondes.
