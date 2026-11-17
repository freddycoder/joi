# Plan : fiabilité de l'écoute, réactivité de Joi, et un filet de tests

Décidé le 2026-11-17, après une révision du code avec l'utilisateur. Prévu pour **une session de code
(Opus, effort high)**, sur une branche `ameliorations`, avec une seule PR.

Les parties sont classées par priorité. Si la session manque de temps, s'arrêter proprement **après la
partie 3** (la partie 4 peut faire l'objet d'une deuxième session) et noter l'état à la fin de ce fichier.

## Constats de la révision (2026-11-17)

Ces mesures viennent des vraies données (`donnees/logs/joi.log`, `donnees/journal/`) :

- **Latence** : sur 38 réponses, il faut **4,5 s en médiane** (8,1 s au 90e centile) entre l'envoi au
  cerveau et la première phrase complète, *avant* la synthèse vocale. Avec des recherches en mémoire,
  c'est 6 à 17 s. Le réveil prend 4 à 6 s (un `CopilotClient` est lancé à chaque conversation).
  Dans `serveur.py`, `repondre()` attend la synthèse d'une phrase avant de passer à la suivante.
  La détection de fin de tour d'AssemblyAI et le temps de synthèse ne sont pas mesurés.
- **`se_souvenir` bloque la boucle asyncio** : le handler est synchrone (`memoire.py`,
  `outil_se_souvenir`), et le Copilot SDK 1.0.31 l'appelle directement dans son wrapper async
  (`copilot/tools.py`, `result = fn(*call_args)`). Il relit tous les `.md` et jusqu'à 60 jours de
  journal à chaque appel. Aujourd'hui, environ 170 ms pour 900 Ko de journal. Une journée d'écoute fait
  environ 300 Ko : dans deux mois, plusieurs secondes pendant lesquelles l'audio, le WebSocket et les
  annonces sont figés.
- **Aucune reconnexion** :
  - `oreilles.py`, `envoyer_audio` : un `ConnectionClosed` est avalé sans bruit. Si AssemblyAI coupe,
    Joi devient sourde jusqu'au rechargement de la page.
  - `frontend/app.js`, `ws.onclose` : retour en veille, sans reconnexion. **En mode écoute toute la
    journée, une coupure arrête l'enregistrement sans que personne le voie.**
- **Aucun test automatisé** pour environ 7 800 lignes. Plusieurs parties sont fragiles : les états des
  segments, les dates nommées, le découpage en phrases, la confirmation d'envoi des courriels.
- **Mineur** : `interrompre()` peut attendre jusqu'à 5 s (abort du cerveau) *dans* la boucle de réception
  d'AssemblyAI (`Oreilles._ecouter` attend `sur_tour`) : les transcriptions s'arrêtent pendant ce temps.

## Règles pour la session

- **Les tests ne touchent jamais `donnees/`** (vraie mémoire, vrai journal, empreintes biométriques).
  Voir la partie 1 : un dossier de données temporaire, vérifié par `conftest.py`.
- Pas de vrais noms de personnes dans le code, les docs, les commits ou la PR (noms fictifs seulement).
- Garder le style du projet : identifiants, commentaires et docstrings en français, docstrings qui
  expliquent le *pourquoi*. Mettre à jour l'en-tête de `serveur.py` (protocole WebSocket) et le README
  si un comportement visible change.
- Ne pas changer les choix de l'utilisateur dans `.env` (modèle Copilot, voix, seuils) : mesurer et
  proposer. Une nouvelle variable reçoit une valeur par défaut qui garde le comportement actuel, et va
  dans `.env.example`.
- Le compte de Joi reste optionnel : sans `JOI_COMPTE`, aucun code de `joi/compte/` n'est importé.
- Commits en français, avec un message qui explique le pourquoi.

## À vérifier au début (ne pas se fier à la mémoire)

- **AssemblyAI Streaming v3** : le nom et les valeurs par défaut des paramètres de fin de tour
  (`end_of_turn_confidence_threshold`, `min_end_of_turn_silence_when_confident`, `max_turn_silence`).
  Vérifier aussi la durée maximale d'une session, le délai d'inactivité, et les codes de fermeture
  correspondants : ils décident quand on doit se reconnecter.
- **Copilot SDK** (version installée dans `.venv`) :
  - un même `CopilotClient` peut-il porter plusieurs sessions successives, ou simultanées (deux onglets) ?
  - comment lister les modèles disponibles ?
  - un handler d'outil qui renvoie un awaitable est-il bien attendu ? Lire `copilot/tools.py`,
    `wrapped_handler`.
- **pytest** n'est pas installé dans `.venv`.

---

## Partie 1 : un filet de tests (d'abord, pour protéger les parties 2 à 4)

1. **Dossier de données configurable.** Dans `config.py`, `DOSSIER_DONNEES` vient de la variable
   `JOI_DONNEES` si elle est définie (sinon `RACINE / "donnees"`, comme aujourd'hui). Plusieurs modules
   figent leurs chemins à l'import (`memoire.RACINE`, `journal.FICHIER_CONSOLIDATION`,
   `empreintes.DOSSIER…`) : la variable doit donc être définie **avant** le premier import de `joi`.
2. **`tests/conftest.py`** :
   - définit `JOI_DONNEES` vers un dossier temporaire avant d'importer quoi que ce soit de `joi` ;
   - **vérifie** que `config.DOSSIER_DONNEES` n'est pas le vrai dossier, sinon il arrête tout ;
   - vide `ASSEMBLYAI_API_KEY` et `JOI_COMPTE` : aucun appel réseau, aucun compte ;
   - fournit une fixture qui vide le dossier de données entre deux tests (ou un sous-dossier par test).
3. **`requirements-dev.txt`** avec `pytest` (et `-r requirements.txt`). Les tests async passent par
   `asyncio.run` : pas besoin de plugin. La commande est `.venv\Scripts\python -m pytest`.
4. **Tests**, sur les fonctions pures d'abord, rapides, sans réseau, sans PyTorch :
   - `memoire._dates_nommees` : aujourd'hui, hier, avant-hier, « 16 novembre », un jour sans année encore
     à venir (donc l'an passé), la date ISO, « 31 février » (ignorée), « la journée du 16 novembre ».
   - `memoire.souvenir` sur une mémoire temporaire : une journée nommée renvoie son résumé, une journée
     absente le dit, les mots rares passent avant les mots répandus.
   - `memoire._chemin_sur` : refuse `..`, les chemins absolus, `C:`, les fichiers cachés.
   - `memoire._tranches_ecoute` : chaque rappel `integre` est attaché au message qui contient la fin de
     son segment, et le découpage respecte `TAILLE_MAX_TRANCHE`.
   - `cerveau.couper_phrases`.
   - `commande_vocale.demande_ecoute` : « Joie, écoute seulement ! », « Joi écoute seule » ; refus d'une
     longue phrase qui contient les mêmes mots.
   - `compte.brouillons.est_confirmation` et `SuiviConfirmation.verifier` : « oui, envoie », « non »,
     « oui mais change le sujet », pas de réponse, brouillon expiré, mauvais numéro. **C'est la sécurité
     de l'envoi des courriels** : couvrir les négations. Importer `joi.compte.brouillons` seulement dans
     ce fichier de test.
   - `journal` : `_maintenant` strictement croissant, `noter_entendu` sans doublon à la reprise,
     `entrees_depuis` sans les entrées d'écoute, `racines` et `normaliser` (accents, ligatures).
   - `transcription.entrees_du_segment` : horodatages strictement croissants, et le repli sans
     `utterances`. `_journee_terminee` et `_fin_segment` sur des manifestes construits à la main.
   - `enregistreur.Enregistreur` sur un dossier temporaire, avec une courte `duree_segment_s` et du PCM
     synthétique (silence) :
     - les segments tournent et `sur_segment_fini` est appelé ;
     - les états passent de `en_cours` à `enregistre` ;
     - un segment vide est retiré ;
     - `fermer()` remplit `fin` ;
     - `seances()` renvoie le manifeste vivant pendant l'écoute.
   - `oreilles.TamponAudio` : `extraire` et `instant`, y compris après que le début du tampon a été oublié.
   - `serveur._est_rien`.
5. Ajouter au README une courte section « Tests » (la commande, et le fait qu'ils n'utilisent jamais
   `donnees/`).

## Partie 2 : `se_souvenir` hors de la boucle asyncio

1. Le handler renvoie `asyncio.to_thread(souvenir, p.recherche)`, après vérification que le SDK l'attend.
   Même traitement pour les outils de la bibliothécaire s'ils lisent beaucoup (`lister`, `lire`). C'est
   moins urgent : elle travaille en fond, mais sur la même boucle.
2. Ajouter au log de chaque recherche sa durée et le volume de journal lu, pour savoir quand passer à un
   index. **Hors de portée de cette session** : un index SQLite FTS5 du journal et de la mémoire.
3. Un test qui vérifie que la boucle reste libre pendant une recherche lente (par exemple, une tâche qui
   compte des ticks pendant un `souvenir` sur un gros journal synthétique).

## Partie 3 : une journée d'écoute à l'épreuve des coupures

Le test réel à l'atelier approche : c'est la partie qui compte le plus pour l'utilisateur.

1. **Reconnexion du navigateur** (`frontend/app.js`) :
   - Quand le WebSocket se ferme alors que Joi était réveillée (pas une fermeture voulue), reconnecter
     automatiquement, avec un délai croissant (1, 2, 5, 10, puis 30 s au plus), sans nouveau clic : le
     micro et l'`AudioContext` sont déjà ouverts.
   - Retenir le mode demandé. Si c'était `ecoute`, le redemander dès le message `mode` du serveur. Une
     nouvelle séance commence alors : c'est déjà géré (plusieurs séances par jour).
   - Pendant la coupure, montrer un état visible : un indicateur « reconnexion… », et en écoute, un
     indicateur rouge qui clignote. Garder le Wake Lock.
   - `pisteMicro.onended` (micro débranché, permission retirée) : afficher une erreur claire.
2. **Reconnexion des oreilles** (`oreilles.py`, `serveur.py`) :
   - Une fermeture inattendue (pas venue de `fermer()`, ou un code de fin de session) prévient la
     conversation par un nouveau rappel, par exemple `sur_coupure`.
   - En mode conversation, la conversation recrée les oreilles avec un délai croissant, comme dans
     `_revenir_en_conversation` : nouvelle session, donc nouvelle numérotation des tours. Elle le dit à
     l'interface (`etat` ou `erreur`) et dans les événements du monitoring.
   - `envoyer_audio` ne doit plus avaler la coupure sans rien dire.
3. **Garde-fou côté serveur, en écoute** : retenir l'instant du dernier paquet audio reçu. Après plus de
   30 s sans audio, alors que le WebSocket est ouvert : avertissement dans le log, événement de
   monitoring, message `erreur` au navigateur. Dès que l'audio revient, le noter aussi.
4. **Ne plus bloquer la réception d'AssemblyAI** : sur un tour final, lancer l'interruption et la réponse
   dans une tâche, plutôt que d'attendre l'abort du cerveau dans `Oreilles._ecouter`. Garder l'ordre :
   une nouvelle réponse ne part qu'après l'interruption de la précédente.
5. **Tests** :
   - les oreilles avec un faux serveur WebSocket local (fermeture inattendue, puis reconnexion) ;
   - le garde-fou de silence, avec une horloge injectée ;
   - la reconnexion du navigateur à la main : arrêter puis relancer le serveur pendant une écoute, et
     vérifier qu'une nouvelle séance repart et que l'ancienne est finalisée et transcrite.
6. À considérer, sans l'implémenter sans accord : faire relancer le serveur automatiquement par
   `reveiller_joi.bat` s'il plante.

## Partie 4 : Joi plus réactive (mesurer, puis réduire)

Objectif indicatif : 2 à 3 s en médiane entre la fin de ta phrase et le premier son, sans recherche en
mémoire. **Mesurer avant de changer**, et donner les chiffres avant et après dans la PR.

1. **Mesurer chaque réponse** dans une ligne de log (et dans le monitoring) :
   - **fin de parole → tour final** : `tampon.instant(fin du dernier mot)` donne l'instant d'arrivée de
     l'audio du dernier mot ; la différence avec l'arrivée du tour final est le délai de détection de
     fin de tour ;
   - **envoi → premier token → première phrase** (le cerveau) ;
   - **première phrase → MP3 prêt** (synthèse, en distinguant le cache) ;
   - **MP3 envoyé → début de lecture** : le message `lecture` du navigateur existe déjà.
2. **Synthèse en parallèle** : dans `repondre()`, lancer la synthèse de la phrase suivante pendant
   qu'on envoie la précédente (des tâches dans l'ordre, envoyées dans l'ordre). Une interruption annule
   les synthèses en attente.
3. **Première phrase plus tôt** : pour le *premier* morceau seulement, si aucune fin de phrase n'est
   arrivée après environ 8 mots, couper à une virgule, un point-virgule ou un deux-points **suivi d'une
   espace**. Attention : en français, la virgule décimale (« 3,5 ») n'est pas suivie d'une espace.
   Tester cette coupure dans la partie 1.
4. **Fin de tour d'AssemblyAI** : exposer dans `.env` les paramètres vérifiés au début, avec les valeurs
   par défaut actuelles, puis proposer des valeurs mesurées. Un délai trop court coupe la parole à
   l'utilisateur : rester prudent.
5. **Réveil** : démarrer un `CopilotClient` partagé au lancement du serveur, si le SDK le permet (voir
   « À vérifier »). Chaque conversation crée sa session sur ce client. La bibliothécaire garde son
   propre client.
6. **Modèle** : écrire un petit banc d'essai, par exemple `python -m joi.banc`. Il envoie une dizaine de
   phrases tapées (sans micro, sans AssemblyAI) avec chaque modèle candidat, et donne la médiane jusqu'à
   la première phrase. **Ne pas changer `JOI_MODELE`** : donner le tableau à l'utilisateur, c'est son
   choix (qualité, quotas Copilot).
7. Hors de portée : la synthèse en flux continu (MediaSource), et alléger la personnalité.

---

## Ce que l'utilisateur devra vérifier lui-même

- Une vraie journée (ou demi-journée) d'écoute à l'atelier, avec une coupure provoquée (relancer Joi,
  couper le Wi-Fi) : l'enregistrement reprend-il, et le résumé du soir est-il complet ?
- Dans une conversation à voix haute, la ligne de mesure de la latence : où part le temps ?
- Le banc d'essai des modèles, puis le choix de `JOI_MODELE`.
- Les nouveaux réglages de fin de tour : Joi lui coupe-t-elle la parole ?

## Hors de portée de ce plan

- Découper `empreintes.py` (1 424 lignes) et ne plus lire les attributs privés d'autres modules
  (`transcription._verrou`, `memoire._verrou_consolidation`, `enregistreur._octets_segment`) : des
  remaniements à faire plus tard, une fois les tests en place.
- L'index SQLite FTS5 de la mémoire et du journal (voir partie 2).
- iCloud (voir `plan-compte-joi.md`).
