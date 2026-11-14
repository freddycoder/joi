# Plan : reconnaissance des voix en mode écoute

Décidé le 2026-11-13, avec l'utilisateur. **Révisé le soir du 13** après examen de l'audio de la
journée (voir « Constats du 13 novembre »). Implémentation prévue le 2026-11-14, à partir de l'audio
enregistré le 13.

## Problème

AssemblyAI étiquette les voix (`A`, `B`, `C`…) **segment par segment**, soit par tranche de 30 min. Le
« A » de 9 h n'est pas forcément le « A » de 14 h. On ne peut donc pas savoir combien de personnes ont
parlé dans la journée, ni lesquelles. Le 12 novembre, on comptait de 1 à 5 voix par segment, avec un
total réel inconnu (l'utilisateur l'estime à environ 10, selon la portée du micro).

La voix de Joi n'est **pas** un problème de reconnaissance : en mode écoute, Joi ne parle pas, et
l'enregistrement s'arrête dès qu'on revient en conversation (voir « Voix de Joi : horodatage »).

## Objectif

Le soir, Joi sait distinguer :

- **toi** : l'utilisateur ;
- **les interlocuteurs** : les autres voix, anonymes (« voix inconnue 1 », « 2 »…), regroupées sur
  toute la journée ;
- **les voix de média** : conférence, vidéo, radio, haut-parleur de téléphone (voir plus bas) ;
- **les voix de passage** : trop brèves ou trop faibles pour être comptées.

Le résumé de la journée peut alors dire : « 4 interlocuteurs en plus de toi, et quelques voix de
passage ».

## Constats du 13 novembre

Audio : 19 mp3 (16 kHz, mono, 32 kbit/s), de 08:15 à 16:32, en 5 séances d'écoute, tous transcrits
et consolidés.

1. **L'enrôlement prévu à partir du journal ne trouve aucun échantillon.** Les 39 répliques
   `toi`/`joi` du journal tombent toutes **en dehors** des fenêtres d'écoute : quand on converse avec
   Joi, l'écoute est arrêtée (07:24–07:28, 08:12–08:15:28 avant l'écoute de 08:15:41 ;
   09:52:52–09:53:36 dans la pause 09:52:47–09:54:11 ; 16:31:25 juste avant 16:31:28). Pour la même
   raison, la voix de Joi n'apparaît jamais dans l'audio d'écoute.
2. **La diarisation d'AssemblyAI s'effondre parfois.** `114303` : une seule utterance de 20 min,
   un seul locuteur, alors que le texte est un dialogue. `124303` : une seule utterance de 30 min,
   avec des silences allant jusqu'à 411 s. `081541` : deux utterances seulement (conférence, puis un
   appel). Une empreinte « par étiquette » mélangerait donc plusieurs voix.
3. **Voix de média.** De 08:15 à 09:45, le micro capte une conférence en anglais : ces voix
   dépasseraient le seuil de 30 s et deviendraient des « interlocuteurs ».
4. **Appels téléphoniques.** On entend surtout un côté de l'appel (bon matériel pour la voix de
   l'utilisateur), sauf en haut-parleur.
5. **Attribution.** Le résumé de Joi met presque tout au « tu », y compris des paroles d'autres
   personnes (ex. un appel qui commence par « c'est Bruno de … Informatique », puis des actions
   réparties entre deux noms). C'est exactement ce que la reconnaissance doit corriger.
6. **Pas de logs sur disque.** Les événements techniques ne vivent qu'en mémoire (`monitoring.py`,
   40 derniers) : ceux du 13 sont perdus.

## Décisions prises

| Sujet | Décision |
|---|---|
| Approche | Empreintes vocales **calculées sur l'ordinateur**, puis comparées et regroupées (option « C ») |
| Modèle d'empreinte | SpeechBrain ECAPA (`speechbrain/spkrec-ecapa-voxceleb`) en premier choix, Resemblyzer en repli. **PyTorch accepté** |
| Unité d'empreinte | **Fenêtres de parole de 2 à 4 s**, découpées à partir des mots (`words[]`), **pas une empreinte par étiquette**. Les étiquettes `A`/`B` d'AssemblyAI ne servent que d'indice (révisé le 13) |
| Enrôlement de toi | **Audio du micro pendant les conversations avec Joi**, gardé le temps de calculer l'empreinte, puis effacé. Même micro, même navigateur. « Joi, apprends ma voix » reste disponible (révisé le 13) |
| Voix de Joi | **Pas d'empreinte ni d'enrôlement.** Joi ne parle pas pendant l'écoute. Si cela change, on étiquette ses passages **par horodatage** des lectures audio, pas par reconnaissance (révisé le 13) |
| Moment du calcul | Pendant la transcription de chaque segment, **avant la suppression du mp3** |
| Interlocuteur | **Au moins 30 s** de parole claire au total dans la journée. En dessous : voix de passage |
| Voix de média | Classées à part, pas comptées comme interlocuteurs (détection à affiner, voir plus bas) |
| Empreintes anonymes | **Effacées le soir**, après la consolidation. Jamais conservées d'un jour à l'autre |
| Autres noms (Denis, etc.) | Prévu, **seulement avec l'accord** de la personne, et avec un registre de consentement (voir plus bas) |
| Micro | Un micro de conférence est à venir. Le seuil devra être revu avec lui |
| Logs | Ajouter un fichier de log tournant (`donnees/logs/`) pour garder la trace des essais |
| Effort | Opus, *high*, **une seule session** de code. Ajustement du seuil ensuite (*medium*) |

## Fonctionnement

### 1. À chaque segment (après la transcription AssemblyAI, avant l'effacement du mp3)

1. **Découper en fenêtres** à partir de `words[]` (`start`/`end` en ms depuis le début du segment) :
   regrouper les mots consécutifs tant que le silence entre deux mots est inférieur à environ 0,7 s
   et que la fenêtre ne dépasse pas environ 4 s. Couper aussi au changement d'étiquette AssemblyAI.
   Ignorer les fenêtres de moins d'environ 1,5 s. Ne jamais se fier à la durée d'une utterance
   (`124303` : une utterance de 30 min dont une bonne partie est du silence).
2. Calculer une **empreinte par fenêtre** et noter sa durée et son étiquette AssemblyAI d'origine.
3. Comparer chaque fenêtre à **toi** (similarité cosinus et seuil). Lisser ensuite :
   une fenêtre isolée qui diffère de ses voisines de la même utterance est reclassée seulement si sa
   similarité est nette.
4. Réétiqueter les entrées du journal « entendu » : `toi` ou `inconnu-<segment>-<n>` (et `joi`
   seulement par horodatage, voir plus bas). Une
   utterance qui contient plusieurs voix est **découpée** en plusieurs entrées. L'apprentissage
   horaire voit donc déjà « toi » plutôt que « A ».
5. Garder les empreintes « inconnu » de la journée (fichier du jour dans `donnees/empreintes/`) pour
   le regroupement du soir.
6. Ensuite seulement, supprimer le mp3 (comportement actuel, `transcription.py:159`).

### 2. Enrôlement

**Toi : audio des conversations.**

- Pendant une conversation, le serveur reçoit déjà le PCM 16 kHz du micro (`serveur.py`,
  `envoyer_audio`) avant de le pousser vers AssemblyAI en temps réel. On garde ce flux dans un
  tampon le temps d'un tour.
- À la fin d'un tour (`end_of_turn`), les `words` du message « Turn » donnent les bornes de la
  parole : on extrait ce passage, on calcule l'empreinte, puis on **efface l'audio**. Rien n'est
  écrit sur le disque, sauf l'empreinte.
- On garde seulement les tours d'au moins environ 2 s, en dehors des lectures de Joi (horodatage,
  voir plus bas), pour qu'aucune voix de Joi ne se mêle au tour.
- **Attention au traitement du micro** : en conversation, le navigateur active l'annulation d'écho,
  la réduction de bruit et le gain automatique. Vérifier ce qui est actif en mode écoute
  (`reglerMicro`) : si les réglages diffèrent, les empreintes peuvent s'éloigner. Le rejeu du 13 le
  dira.
- Plan B, sans attendre les prochaines conversations : à partir des segments du 13, l'utilisateur
  désigne une ou deux plages où il parle seul (un appel téléphonique, par exemple), et on enrôle
  dessus.

**Conservation.**

- L'empreinte de toi est conservée (`donnees/empreintes/`, déjà ignoré par git via `donnees/`) et
  s'améliore au fil des conversations : moyenne glissante, avec un nombre limité d'échantillons
  gardés.
- Si aucun enrôlement n'existe encore, tout reste « inconnu », sans erreur.

### 2 bis. Voix de Joi : horodatage (seulement si Joi parle un jour pendant l'écoute)

Aujourd'hui, ce cas n'existe pas : en écoute, Joi se tait, et l'enregistrement s'arrête dès qu'on
revient en conversation. Rien à coder dans la session du 14, sauf si ce comportement change.

Si Joi doit un jour parler pendant l'écoute (une confirmation vocale, par exemple), on n'a pas besoin
de reconnaître sa voix : c'est le programme qui joue le son, il sait donc exactement quand.

1. Le navigateur signale au serveur le **début et la fin réels de chaque lecture** (événements de
   lecture audio), avec l'heure. Le serveur les note dans un fichier du jour, par exemple
   `donnees/enregistrements/AAAA-MM-JJ/lectures-joi.jsonl` (`debut`, `fin`, texte de la réplique).
2. Lors de l'étape 1, toute fenêtre dont l'heure absolue (`debut` du segment + `start` des mots)
   chevauche une lecture, avec une marge d'environ 0,5 s pour la latence du haut-parleur, est
   étiquetée `joi`. Elle est retirée du regroupement et de l'enrôlement de toi.
3. Bonus : comparer le texte entendu au texte de la réplique confirme l'alignement.

C'est exact, sans modèle, et sans aucun coût de calcul.

### 3. Voix de média

Une conférence, une vidéo ou un haut-parleur donnent des voix qui ne sont pas des interlocuteurs.
Indices possibles, à évaluer sur le 13 (08:15–09:45) :

- langue différente du français (AssemblyAI donne la langue par segment, pas par passage :
  à vérifier) ;
- parole continue et longue d'une seule voix, sans tour de parole avec toi ;
- **commande manuelle en secours** : « Joi, c'est une vidéo » marque la voix dominante des minutes
  qui suivent comme « média ».

On commence simple : un groupe du soir qui parle longtemps sans jamais alterner avec toi est
présenté comme « voix de média probable », pas comme interlocuteur.

### 4. Le soir (consolidation)

1. Regrouper les empreintes de fenêtres « inconnu » de toute la journée : regroupement
   hiérarchique sur la distance cosinus, avec un seuil de distance plutôt qu'un nombre de groupes
   fixé d'avance. Avec des fenêtres de quelques secondes, il y aura des milliers de points par jour :
   regrouper d'abord par segment, puis fusionner les groupes entre segments.
2. Pour chaque groupe, additionner les durées de parole. Au moins 30 s : **interlocuteur**
   numéroté. Sinon : **voix de passage**. Appliquer la règle « média » (section 3).
3. Renommer les entrées de la journée (`inconnu-…` vers `voix inconnue N`) avant la consolidation
   en mémoire, et ajouter le décompte au résumé de la journée.
4. **Effacer** toutes les empreintes anonymes de la journée.

### 5. Noms avec consentement (plus tard, à préparer dans la structure)

- Registre `donnees/empreintes/consentements.json` : nom, date de l'accord, façon dont il a été
  donné.
- Deux façons d'ajouter un nom :
  - **sur le moment** : « Joi, voici Denis, il est d'accord ». La voix inconnue la plus présente
    dans les minutes qui suivent reçoit le nom ;
  - **le soir même** : nommer un interlocuteur anonyme de la journée, avant l'effacement.
- **Retrait de l'accord** : on efface l'empreinte et la ligne du registre.
- Raison : une empreinte vocale est une donnée biométrique. Au Québec (Loi 25 et loi sur le cadre
  juridique des technologies de l'information), il faut le consentement exprès de la personne, et une
  banque de caractéristiques biométriques doit être déclarée à la Commission d'accès à l'information.
  Les clients restent toujours anonymes.

## Fichiers touchés (prévision)

- `joi/empreintes.py` (nouveau) : chargement du modèle, découpage en fenêtres, empreintes,
  comparaison, enrôlement, regroupement, effacement.
- `joi/transcription.py` : appel avant la suppression du mp3, puis réétiquetage et découpage des
  entrées.
- `joi/serveur.py` et `joi/oreilles.py` : tampon PCM pendant les conversations et enrôlement de
  « toi » à la fin de chaque tour.
- La consolidation d'écoute : regroupement du soir, voix de média, décompte dans le résumé,
  effacement.
- Un script de **rejeu** (ex. `python -m joi.empreintes rejouer 2026-11-13`) : relit les mp3 et JSON
  d'une journée, sans AssemblyAI, et affiche les groupes trouvés avec leurs heures et un extrait de
  texte, pour comparer avec le souvenir de l'utilisateur.
- `joi/__main__.py` : fichier de log tournant dans `donnees/logs/`.
- `joi/config.py` et `.env.example` : seuils (similarité, 30 s, longueur des fenêtres) et
  activation.
- `requirements.txt` : `torch`, `torchaudio`, `speechbrain` (version CPU). Aucun n'est installé
  dans `.venv` le 13.
- `README.md`.

## Matériel de test

- **Journée du 2026-11-13** enregistrée avec `JOI_GARDER_AUDIO=oui` : 19 mp3 et leurs transcriptions
  JSON dans `donnees/enregistrements/2026-11-13/` (122 Mo), plus le journal du jour. Segments utiles :
  - `081541` : conférence en anglais, puis un appel (test des voix de média) ;
  - `114303`, `124303` : diarisation effondrée (test du découpage en fenêtres) ;
  - `111304`, `121303`, `134303`, `141303` : 3 ou 4 voix selon AssemblyAI (test du regroupement) ;
  - un appel où l'utilisateur parle seul : plage d'enrôlement de secours, à désigner avec lui.
- **Premier contrôle du modèle** : deux phrases d'un même locuteur (deux répliques de synthèse dans
  `donnees/voix/*.mp3`, par exemple) doivent donner des empreintes proches. Ce contrôle sert
  seulement à valider le modèle, pas à enrôler Joi.
- Le code doit pouvoir **rejouer** une journée à partir des mp3 et JSON existants, sans retranscrire
  chez AssemblyAI. C'est indispensable pour ajuster le seuil sans payer.

## Implémentation du 2026-11-14

Codé dans `joi/empreintes.py` (nouveau), `transcription.py`, `memoire.py`, `bibliothecaire.md`,
`oreilles.py`, `serveur.py`, `frontend/app.js`, `monitoring.py`, `config.py`, `__main__.py` (log
`donnees/logs/joi.log`), `requirements.txt`, `.env.example`, `README.md`. Installé dans `.venv` :
torch 2.15 CPU, speechbrain 1.1, soundfile (décode le MP3), scipy. Modèle dans
`donnees/empreintes/modele/` (chargé au démarrage de Joi).

Écarts avec le plan :

- **Rattachement à la journée au fil de l'eau**, pas seulement le soir : l'apprentissage horaire
  consolide déjà pendant la journée. Les groupes de chaque segment sont rattachés aux voix du jour,
  dans l'ordre chronologique, à chaque consolidation ; une voix garde son numéro. La bibliothécaire
  reçoit « voix inconnue N » et une légende (durée, heures, classe). Le soir (`JOI_FIN_JOURNEE`,
  18:00), le bilan va dans `journees/AAAA-MM-JJ.md` et les empreintes anonymes sont effacées.
- **Deuxième passe par groupe** : un groupe inconnu dont l'empreinte moyenne dépasse « seuil toi +
  0,15 » devient « toi » (les fenêtres seules sont trop bruitées).
- **Lectures de Joi** : le navigateur signale déjà le début et la fin de chaque lecture (message
  `lecture`) ; le serveur s'en sert pour ne pas apprendre un tour où Joi parlait. Rien encore pour
  l'écoute (section 2 bis), Joi n'y parlant pas.
- **Enrôlement à partir du rejeu** (`enroler-voix JOUR N`), ajouté parce qu'une plage où
  l'utilisateur parle seul est difficile à trouver : il se reconnaît dans les extraits d'une voix,
  et la moitié la plus typique de ses fenêtres devient son empreinte. Les voix connues servent
  aussi de point d'attache au rattachement à la journée (sinon les fenêtres de toi restées sous le
  seuil formaient de faux interlocuteurs). Simulé avec la voix 2 du 13 : toi 61 min, 7
  interlocuteurs, 15 voix de passage.
- **Noms avec consentement** (le 14 au matin) : `consentement ajouter|retirer|liste` et
  `enroler-voix JOUR N --nom Prénom` (refusé sans accord au registre). Une personne nommée compte
  parmi les interlocuteurs du bilan. Constat du 14 : l'utilisateur est la voix 3 (une fois la
  voix 2 apprise par erreur comme « toi »), un collègue (« Bruno » ici) la voix 2 (1 h 05, 269 alternances avec toi).
  Un enrôlement de la mauvaise voix avait mélangé 20 échantillons de ce collègue dans « toi » : retirés,
  et `enroler-voix` remplace désormais les échantillons de la même journée.
- **Bilan jamais manqué** (le 14) : le portable est souvent fermé à 18:00. Les journées passées sont
  fermées dès le démarrage de Joi, puis une vérification toutes les 15 min remplace la minuterie
  (qui se décalerait en veille). Commande `bilan AAAA-MM-JJ` pour le forcer.
- **Reconsolidation** (le 14) : `reconsolider AAAA-MM-JJ` calcule les voix d'une journée déjà apprise
  (cache du rejeu), puis la bibliothécaire corrige le résumé et les attributions (mode
  « correction »), puis bilan et effacement. Faite pour le 13 avec toi et trois collègues consentants
  (mémoire avant la passe : commit `9abe86e` de `donnees/memoire`).
- Pas encore fait : « Joi, apprends ma voix », « Joi, c'est une vidéo », nommer une voix avec
  consentement (seuls le registre et `oublier` existent).

Réglages issus du rejeu du 13 (20 segments, empreintes en cache dans `donnees/empreintes/rejeu/`) :

- Contrôle du modèle : 0,74 entre phrases de la voix de Joi, 0,04 avec des voix humaines.
- Fenêtres de 1,5 à 4 s : même voix 0,35 à 0,6 en médiane, voix différentes 0,05 à 0,1.
  Seuils retenus : segment **0,25**, journée **0,45** (0,30 pour un groupe de moins de 10 s),
  toi **0,40** par fenêtre et 0,55 par groupe (profil simulé : 0,70 en médiane pour la même voix,
  0,51 au plus pour les autres). Avec 0,45 / 0,6 au départ : 781 « voix ».
- Résultat sans empreinte de toi : la conférence de 08:15 en **voix de média probable** (18 min),
  6 interlocuteurs (1 h 07 pour la voix 2, sans doute toi ; 21, 20 et 11 min ; 3 min ; 40 s), et
  12 voix de passage (26 s au total).
- Un segment de 30 min prend environ 1 à 2 min de calcul (la moitié des cœurs).

## Ce qui reste à faire après la session de code

1. Rejouer la journée du 13 et comparer avec le souvenir de l'utilisateur : qui était là ? qui
   parlait dans l'appel de 12:43 ?
2. Ajuster les seuils (similarité, 30 s, longueur des fenêtres).
3. Converser quelques jours avec Joi pour bâtir l'empreinte de « toi », puis vérifier qu'elle
   reconnaît l'utilisateur dans l'écoute.
4. Refaire l'essai avec le **micro de conférence** dès qu'il arrive.
5. **Remettre `JOI_GARDER_AUDIO=non`** dans `.env` et effacer les mp3 de test.
