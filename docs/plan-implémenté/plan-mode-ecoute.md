# Plan : mode écoute (Joi écoute la journée et apprend le soir)

Décidé le 2026-11-11, avec l'utilisateur. Implémentation prévue à partir du 2026-11-12.

## Objectif

Joi a deux modes :

- **Conversation** : le mode actuel, en direct.
- **Écoute** : Joi se tait et enregistre ce qu'elle entend. Le contexte : l'atelier et la boutique
  d'informatique, avec plusieurs voix et du bruit de fond. Ce ne doit pas être codé en dur, c'est 
juste pour indiquer dans quelle environnement elle sera à court terme. Pas de réunions virtuelles pour l'instant.

L'apprentissage peut attendre. En fin d'écoute, l'audio est transcrit en différé, avec la reconnaissance
des voix, puis consolidé dans la mémoire.

**Ce qu'on retient** : seulement ce qui concerne l'utilisateur, ses projets, ou les sujets qui le
touchent. On ne fait pas de dossier sur les collègues ni sur les clients.

## Décisions prises

| Sujet | Décision |
|---|---|
| Transcription | **Différée** (async AssemblyAI) en fin d'écoute, pas en streaming : plus précise, moins chère, on ne paie pas les silences |
| Voix multiples | `speaker_labels` d'AssemblyAI (async, environ 0,02 $/h) |
| Audio pendant la journée | Enregistré **localement**, dans `donnees/enregistrements/` (ignoré par git) |
| Bascule | Bouton discret dans l'interface **et** commande vocale (« Joi, écoute seulement » / « Joi, reviens ») |
| Visuel | Hologramme atténué, avec un indicateur d'enregistrement, en mode écoute |
| Modèle pour coder | Opus. Effort à choisir (suggestion : *medium* pour les sessions 1 et 3, *high* pour la session 2) |
| Nombre de sessions de code | 4 au maximum, idéalement 2 ou 3 |

## À vérifier au début de l'implémentation (ne pas se fier à la mémoire)

- L'API async d'AssemblyAI : `/v2/upload`, `/v2/transcript`, le nom du modèle Pro pour le français
  en différé, `speaker_labels`, `keyterms_prompt` (réutiliser `config.MOTS_CLES`), la taille maximale
  d'un fichier, et les formats acceptés.
- Les prix actuels sur assemblyai.com/pricing (estimation en streaming : environ 4,50 $ pour 8 h ;
  en différé, ce devrait être moins cher).
- Le choix d'un encodeur MP3 ou Opus côté Python, léger, sans ffmpeg si possible (ex. `lameenc`).
  Repère de taille : du PCM 16 kHz brut fait environ 115 Mo/h ; en MP3 32 kb/s mono, environ 14 Mo/h.

## Session 1 : le mode écoute et l'enregistrement local (sans API)

Testable entièrement hors ligne.

1. **Protocole WebSocket** : ajouter `{"type": "mode", "mode": "conversation" | "ecoute"}` dans les deux
   sens. Le serveur confirme le changement de mode. Documenter le tout dans l'en-tête de `serveur.py`.
2. **Serveur** (`Conversation`) :
   - En mode écoute, l'audio n'est plus envoyé à AssemblyAI : fermer la session de streaming pour ne pas
     payer. Il est écrit dans un enregistreur.
   - Interrompre Joi si elle parlait au moment de la bascule.
3. **Nouveau module `joi/enregistreur.py`** :
   - Il écrit l'audio en **segments** de 30 min environ, dans
     `donnees/enregistrements/AAAA-MM-JJ/HHMMSS.mp3`. Les segments ont deux buts : survivre à un
     plantage, et rester sous les limites d'envoi.
   - Il écrit à côté un manifeste JSON par séance d'écoute : heure de début de chaque segment, état
     (`enregistre` → `transcrit` → `consolide`).
   - Il garde l'heure réelle du début de chaque segment, pour dater les phrases plus tard.
4. **Frontend** :
   - Le bouton de mode, et le visuel atténué avec l'indicateur d'enregistrement.
   - La **Wake Lock API** (`navigator.wakeLock`) en mode écoute, pour que l'écran ou le PC ne se mette
     pas en veille et ne coupe pas l'enregistrement.
   - Le micro est déjà configuré avec `noiseSuppression`, `echoCancellation` et `autoGainControl`
     (`frontend/app.js:116`). Pour capter des voix éloignées dans l'atelier, envisager de désactiver
     `echoCancellation` en mode écoute (Joi ne parle pas, donc il n'y a pas d'écho à annuler). À tester.
5. **Fermer l'onglet en mode écoute** doit finaliser le segment en cours proprement.

## Session 2 : la transcription différée et la consolidation

1. **Nouveau module `joi/transcription.py`** :
   - Il envoie chaque segment (upload, création de la transcription, attente du résultat). Paramètres :
     français, `speaker_labels`, mots-clés.
   - Il convertit les `utterances` en entrées de journal :
     `{"ts": début_segment + start_ms, "qui": "entendu", "locuteur": "A", "texte": ..., "source": "ecoute"}`.
     Le `ts` doit rester croissant, car la consolidation repère les nouvelles entrées par horodatage :
     **attention**, des entrées d'écoute datées dans le passé pourraient tomber *avant* le marqueur
     `.consolide.json` et être ignorées. Prévoir un marqueur séparé pour l'écoute, ou consolider chaque
     séance d'écoute directement à partir du manifeste.
   - Il conserve la transcription brute (JSON) à côté de l'audio.
   - **Supprimer l'audio** une fois la transcription réussie, pour la vie privée. Garder seulement le
     texte. Rendre ce comportement configurable dans `.env` (`JOI_GARDER_AUDIO=non` par défaut).
2. **Déclenchement** : au retour en mode conversation, ou à la fermeture de l'onglet en mode écoute,
   lancer en fond : transcription, puis consolidation. **Rattrapage au démarrage** des segments non
   transcrits, sur le modèle de `_rattraper_plus_tard` dans `serveur.py`.
3. **La bibliothécaire** (`joi/bibliothecaire.md`) : ajouter une section sur les transcriptions d'écoute :
   - Les étiquettes A, B, C ne sont pas des noms. Déduire laquelle est l'utilisateur à partir du contexte,
     et dans le doute ne pas lui attribuer de propos.
   - Ne jamais attribuer à « toi » ce que disent d'autres personnes.
   - Retenir seulement ce qui concerne l'utilisateur, ses projets, ou les sujets qui le touchent :
     décisions, choses à faire, problèmes réglés, apprentissages. Ignorer les conversations des autres
     qui ne le concernent pas.
   - Écrire un résumé par journée dans `journees/AAAA-MM-JJ.md`, et ne mettre dans `toi.md` que ce qui est
     durable.
   - Le message envoyé à la bibliothécaire doit indiquer qu'il s'agit d'une écoute (et non d'une
     conversation avec Joi).
4. **`se_souvenir` et le journal** : afficher les entrées « entendu » sous la forme
   `[entendu, voix A]` dans les résultats (`memoire.souvenir`, `memoire._transcrire`).
5. **Coût et suivi** : journaliser dans la console le nombre d'heures transcrites et une estimation du
   coût.

## Session 3 : la commande vocale et la finition

1. **« Joi, écoute seulement »** (passer en écoute) : facile, car le streaming est actif en mode
   conversation. Détecter la phrase dans un tour final, avant de l'envoyer au cerveau. Être tolérant
   aux erreurs de transcription (normaliser, accepter des variantes).
2. **« Joi, reviens »** (revenir en conversation) : en mode écoute, le streaming est fermé. Option
   recommandée : **Vosk** avec un petit modèle français (environ 40 Mo, hors ligne, gratuit), en
   reconnaissance restreinte par une grammaire (`["joi reviens", "[unk]"]`). Ça demande peu de
   processeur et ça ne coûte rien. Valider la fiabilité dans le bruit de l'atelier. Si c'est trop
   compliqué, garder seulement le bouton : c'est acceptable.
3. Mettre à jour le README (mode écoute, coûts, vie privée, comment fermer), `.env.example` et
   `requirements.txt`.
4. **Test réel** : une demi-journée dans l'atelier, puis relire `journees/…md` avec l'utilisateur.

## Vie privée (rappel convenu)

- Les collègues et les clients devraient savoir qu'ils sont enregistrés (Loi 25, attente de vie privée
  au travail).
- L'audio et le texte passent par AssemblyAI et Copilot.
- Ce qu'on fait pour limiter l'exposition : l'audio est supprimé après la transcription, et la mémoire ne
  retient que ce qui concerne l'utilisateur.

## Hors de portée pour l'instant

- Les réunions virtuelles et la capture du son de l'ordinateur.
- L'identification des voix par leur empreinte vocale (savoir que la voix A est l'utilisateur d'une
  journée à l'autre). On s'en remet au contexte.
