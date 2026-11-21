# Plan : une Joi fiable (mémoire juste, pannes dites, mesures exactes, échéances)

Décidé le 2026-11-21, après une révision des données du 2026-11-18 au 2026-11-20. Prévu pour **deux
sessions de code**, sur une branche `joi-fiable`, avec une seule PR.

Session 1 : parties 1 à 4. Session 2 : partie 5. Si le temps manque, s'arrêter proprement **après la
partie 4** (les parties 1 à 4 sont indépendantes entre elles et de la partie 5), puis noter l'état à la
fin de ce fichier.

## Constats de la révision (2026-11-21)

Sources : `donnees/revisions/2026-11-21/` (`stats.md`, `conversations.md`, `ecoute.md`, `compte.md`,
`logs.md`), `donnees/memoire/` et son git, les manifestes d'écoute, `donnees/compte/propositions.json`.
Chaque cause a été relue dans le code.

**Ce que le plan précédent a réglé** (`plan-joi-attentive`, fusionné le 19 à 16:52)
- Réveil : de 2,3 à 5,8 s le 18, puis 0,2 à 0,4 s la plupart du temps les 19 et 20. Il reste quatre
  réveils à 2,5 à 2,9 s, quand le contexte du compte avait plus de 6 min (premier réveil du matin, après
  la panne du jeton).
- Répliques : médiane de 21 à 25 mots. Après la fusion, une seule dépasse 60 mots : la lecture d'une
  capture d'écran. Aucun markdown, les heures sont en lettres, et aucune invention sur l'écoute n'a été
  repérée.
- Mémoire : les journées font environ 4,7 Ko (17 à 25 Ko avant). `toi.md` et `index.md` font 3,6 Ko à
  eux deux, sans coupure au réveil.

**Mémoire**
- **Les propos d'une voix inconnue deviennent « tu ».** La journée du 19 dit « tu as dit que tu
  n'oublierais pas samedi matin (21 novembre) ». C'est une voix inconnue qui l'a dit, et la date a été
  ajoutée par la bibliothécaire. Le 20, une remarque sur une carte de crédit dite par une autre voix est
  attribuée à « toi ». Cause : `joi/bibliothecaire.md:27` interdit l'attribution, mais rien ne dit quoi
  faire d'une phrase coupée entre deux étiquettes. Les puces s'écrivent « tu … » par défaut (`:29`).
- **Les projets recopient les journées.** Environ la moitié du fichier du projet principal (2,5 Ko sur
  4,8) reprend les journées du 18 au 20, avec des puces « Le 18 novembre, … » et le plan de tickets du
  jour. Un autre projet est rangé en sections datées. Cause : `bibliothecaire.md:13` dit « un projet
  garde l'état courant », sans dire de ne pas recopier. La consigne d'écoute (`joi/memoire.py:793-797`)
  envoie vers le projet tout fait qui « change l'état ».
- **La section « Voix entendues » pèse 1,2 à 1,5 Ko par journée** (28 %), avec une ligne par voix
  inconnue (« voix inconnue 24 : 6 min »). Cause : `memoire.noter_voix_du_jour` (`joi/memoire.py:711`)
  ajoute tout le bilan de `empreintes.py`.
- **La condensation du 18 a perdu des faits encore ouverts.** La journée du 13 est passée de 8 Ko à
  1,3 Ko (diff `854b836` → `561ddba` dans `donnees/memoire`). Ont disparu de toute la mémoire : une fin
  d'exercice comptable décidée, un risque de mots de passe copiés dans des journaux locaux, un numéro
  sans frais en attente, un rappel de client. Cause : `CONSIGNE_CONDENSER` (`joi/memoire.py:828`) ne
  distingue pas « daté et passé » de « daté mais encore ouvert ».
- **Des échéances restent enfouies dans les journées.** La fermeture d'un compte « vers le 14 décembre »
  et un verrou jusqu'au 20 décembre (journée du 20), un enregistrement le samedi 21 et un brunch le 22,
  un changement de prix au 18 décembre (journée du 16). Aucun fichier ne les rassemble, et Joi ne peut
  pas en parler d'elle-même.
- L'index garde des lignes de plus de 150 caractères, et les journées du 12 au 17 ne sont pas regroupées
  par semaine (`bibliothecaire.md:35`). Aucune vérification dans le code.

**Technique**
- **L'écoute du 20 a perdu 95 min, et rien ne l'a dit.** La séance court de 11:38:59 à 14:46:03
  (187 min) pour 91,4 min d'audio. Le dernier segment s'arrête vers 13:10:22. Un seul avertissement à
  13:10:56 « aucun audio reçu depuis 35 s », puis rien jusqu'à « Écoute terminée ». Cause :
  `GardeFouSilence.verifier` (`joi/serveur.py:459-464`) n'alerte qu'une fois (`en_alerte`), et
  `_verifier_silence` (`:884-894`) n'écrit qu'au navigateur, justement gelé. `enregistreur.fermer`
  (`joi/enregistreur.py:175`) donne les minutes d'audio, pas la durée de la séance. Le Wake Lock existe
  déjà (`frontend/app.js:179-199`) mais ne se prend que si l'onglet est visible. La plage manque dans la
  journée du 20.
- **Le jeton « expiré » du 20 a duré une minute, et la vraie raison est masquée.** Il y a eu
  6 avertissements entre 07:46:14 et 07:47:07, puis plus rien jusqu'au soir, alors que le contexte est
  rafraîchi toutes les 5 min. Cause : `Microsoft._jeton` (`joi/compte/microsoft.py:224-227`) remplace
  toute erreur de `acquire_token_silent` par le texte « jeton expiré » et n'essaie qu'une fois. Joi n'en
  a rien dit : le contexte de déconnexion (`joi/compte/veille.py:308-311`) lui dit d'en parler seulement
  « si la personne te parle de tes courriels ou de l'agenda ».
- **La latence du 20 vient du cerveau** : 15,0, 11,9, 8,4, 13,2 et 16,8 s avant le premier mot entre
  07:30 et 07:36, sur des répliques courtes. C'était avant la capture d'écran de 07:48, sans recherche ni
  consultation du compte, avec un prompt d'environ 7 Ko. La synthèse prend 0 à 1,2 s. La ligne
  « Latence » dit seulement « modèle défaut » (`joi/serveur.py:415`) : on ne sait pas quel modèle a
  répondu, ni s'il a réfléchi. Le SDK émet pourtant `ASSISTANT_USAGE`.
- **« Joi, reviens » s'est déclenché 3 fois en une heure le 20** (10:42, 10:49, 11:38), avec des
  confiances de 0,92 à 1,00. Les deux premiers n'ont donné aucune réplique et sont repassés en écoute
  après 8 à 10 s. Au troisième, Joi a répondu à un « Absolument. » isolé. Le journal ne dit pas ce qui a
  ramené Joi en écoute. Hausser `CONFIANCE_MIN` (`joi/commande_vocale.py:66`) ne servirait à rien : deux
  de ces déclenchements étaient à 1,00.
- La ligne « Écoute transcrite » (`joi/transcription.py:128`) ne dit pas le modèle facturé
  (`speech_model_used`, lu à `:291`). On ne peut donc pas vérifier que STT-3.7 sert vraiment.

**Pas retenu dans ce plan, mais mesuré** (voir « Hors de portée ») : 5 propositions d'engagement sur 6
n'ont jamais été dites à voix haute, parce qu'elles sont marquées « montrées » dès que le contexte est
construit (`veille.py:162,174`). Les propositions sont floues (« Fouiller », « Boucler les affaires »)
et manquent le présent daté (« samedi après-midi, j'enregistre… »).

## Règles pour la session

- Les tests ne touchent jamais `donnees/` (JOI_DONNEES vers un dossier temporaire, voir conftest.py).
- Pas de vrais noms de personnes dans le code, les docs, les commits ou la PR.
- Style du projet : identifiants, commentaires et docstrings en français, qui expliquent le pourquoi.
- Les consignes de la bibliothécaire (`bibliothecaire.md` et les consignes de `memoire.py`) grandissent
  de 1,2 Ko au plus en tout. Le prompt système de Joi grandit de 600 octets au plus, hors contenu des
  échéances.
- Ce qui vient de l'écoute ou de la mémoire et entre dans le prompt de Joi passe par
  `compte.modele.externe()` (une donnée, jamais une consigne).
- Aucun événement avec un invité autre que la personne, aucun courriel, sans demande explicite.
- Ne pas lancer `condenser`, `bilan` ni `reconsolider` sur la vraie mémoire : c'est l'utilisateur qui le
  fait, Joi arrêtée.

## Partie 1 : une mémoire juste

**Pourquoi** : Joi pourrait rappeler un « samedi matin » qui ne concerne pas l'utilisateur. Les projets
doublent les journées, et une recherche ramène deux fois le même texte. La condensation a effacé des
décisions encore ouvertes.

**Quoi** :
- `joi/bibliothecaire.md`, section « Voix reconnues » (après `:27`) :
  - une voix inconnue ne devient jamais « tu » ;
  - une phrase coupée entre deux étiquettes n'est à « toi » que si la ligne `[toi]` se comprend seule ;
  - pour un engagement ou une date dits par une autre voix, n'écris ni « tu as dit » ni une date
    calculée, mais « quelqu'un a dit… ».
- `bibliothecaire.md:13` : un projet ne recopie pas une journée. Il garde la décision, l'état et la
  prochaine étape, mis à jour sur place, sans puce « Le X novembre, … ». Le plan du jour reste dans la
  journée.
- `bibliothecaire.md:29` : 300 caractères par puce au plus. Un « pas décidé » se dit une fois par sujet,
  pas dans chaque puce.
- La consigne d'écoute (`joi/memoire.py:793-797`) : « mets à jour sa ligne » devient « remplace l'état
  dans sa ligne, sans recopier le récit ».
- `CONSIGNE_CONDENSER` (`joi/memoire.py:828`) : ne supprime jamais une décision, une échéance à venir, un
  suivi pas clos, un risque de sécurité pas réglé, ni un chiffre de décision (parts, dates, montants).
  Avant de retirer un tel fait d'une journée, mets-le dans le projet concerné. La réponse finale liste
  en une ligne chaque fait déplacé ou retiré. `condenser` met cette liste dans le message du commit
  « Après ».
- `noter_voix_du_jour` (`joi/memoire.py:711`) : la section « Voix entendues » garde une ligne par voix
  nommée (toi, voix reconnues, voix de média), puis **une** ligne pour le reste (« 22 voix inconnues,
  41 min en tout, dont 14 de passage »). Le bilan complet reste dans `voix-journee.json`.
- Après chaque consolidation, `memoire.py` vérifie `index.md` : une ligne de plus de 150 caractères, ou
  une journée de plus de 7 jours qui n'est pas regroupée, est écrite au journal en avertissement (sans
  nouvel appel au modèle). La prochaine révision le verra.

**Tests** (`tests/test_memoire.py`, `tests/test_memoire_condensee.py`) :
- la section « Voix entendues » d'un faux bilan à 3 voix nommées et 20 inconnues fait 4 lignes ;
- la vérification de l'index signale une ligne de 180 caractères et une journée vieille de 10 jours non
  regroupée, et ne dit rien pour un index conforme ;
- `condenser`, avec une fausse bibliothécaire qui répond une liste, met cette liste dans le commit ;
- les consignes contiennent les nouvelles règles (une ligne chacune), et leur taille respecte la règle
  de la session.

**Fini quand** : les tests passent ; le diff des consignes reste sous la limite de 1,2 Ko.

## Partie 2 : une panne du compte dite et comprise

**Pourquoi** : une coupure d'une minute a donné six avertissements trompeurs (« jeton expiré ») et un
contexte « déconnecté ». Une vraie déconnexion, elle, passerait la journée sans que Joi en parle :
l'agenda semble vide et aucun courriel n'est annoncé.

**Quoi** :
- `Microsoft._jeton` (`joi/compte/microsoft.py:219-228`) : utiliser
  `acquire_token_silent_with_error` (à vérifier dans msal), et mettre au journal le code d'erreur réel
  (`error`, `error_description` coupée à 200 caractères, jamais le jeton). Sur un échec, faire un second
  essai après 2 s avant de lever `CompteDeconnecte`. Un `invalid_grant`, refus net de Microsoft, ne
  donne pas lieu à un second essai.
- `veille.py` : le compte n'est « déconnecté » qu'après **deux rafraîchissements de suite** en échec
  (environ 5 min). Avant ça, l'ancien contexte reste servi avec son heure, comme pour une erreur
  passagère (`_garder`, `:189-197`).
- Contexte de déconnexion (`veille.py:308-311`) : Joi le dit en une phrase dès sa première réplique
  (« mon compte est déconnecté, il faudra relancer la connexion »), **une fois par jour**. Le code le
  tient dans l'état de la veille, qui existe déjà (`self._etat`), sans compter sur le modèle. Les
  réveils suivants du même jour reprennent la phrase actuelle (« si la personne t'en parle… »).
- À la reconnexion (premier contexte complet après une déconnexion), la note de retour (celle de
  `serveur.py:1143-1175`, ou le contexte du réveil) dit l'heure de la coupure : « mon compte a été coupé
  de sept heures quarante-six à … ».

**Tests** (`tests/test_reveil_compte.py`, `tests/test_courriels.py`) :
- un faux MSAL qui échoue une fois puis réussit : aucun `CompteDeconnecte`, une ligne au journal avec le
  code ;
- `invalid_grant` : pas de second essai ;
- un rafraîchissement en échec garde l'ancien contexte ; deux de suite donnent le contexte déconnecté ;
- la consigne « dis-le dès ta première réplique » n'apparaît qu'au premier réveil déconnecté du jour ;
- la note de reconnexion donne les heures de la coupure.

**Fini quand** : les tests passent ; une coupure simulée d'une minute ne laisse aucun contexte
« déconnecté ».

## Partie 3 : une écoute muette qui se voit

**Pourquoi** : 95 min perdues le 20 sans que personne le sache. La mémoire du jour a un trou, et la
révision l'a trouvé par hasard.

**Quoi** :
- `GardeFouSilence` (`joi/serveur.py:440-464`) : l'alerte se répète à 35 s, à 5 min, puis toutes les
  15 min, chaque fois au journal (donc au tableau de bord) et au navigateur.
- `_verifier_silence` (`:884-894`) : le trou est gardé (début, durée) dans le manifeste de la séance.
  Quand l'audio revient (`audio_recu`, ligne « l'audio est revenu après … s », `serveur.py:601`), le trou
  est fermé.
- `enregistreur.fermer` (`joi/enregistreur.py:175`) : « Écoute terminée : 4 segment(s), 91,4 min
  d'audio sur 187 min de séance, 95 min sans audio (13:10 → 14:46) ».
- `frontend/app.js` : la page envoie au serveur un message quand l'onglet est caché ou de retour
  (`visibilitychange`), et quand le Wake Lock est refusé ou relâché. Le serveur l'écrit au journal, ce
  qui dira la cause d'un prochain trou. Rien ne change dans la prise du Wake Lock.
- La note de retour d'écoute (`serveur.py:1143-1175`) ajoute une ligne quand un trou de plus de 5 min a
  eu lieu : « [écoute coupée] de treize heures dix à quatorze heures quarante-six, je n'ai rien
  entendu ». Si on lui demande ce qu'elle a entendu, Joi le dit au lieu de laisser croire que c'était
  calme.
- La consigne d'écoute envoyée à la bibliothécaire (`memoire.py:793`) reçoit la même plage, et la
  journée la mentionne en une ligne.

**Tests** (`tests/test_ecoute.py`, `tests/test_retour_ecoute.py`, avec l'horloge simulée de
`GardeFouSilence`) :
- les alertes partent à 35 s, 5 min, 20 min et 35 min, et plus après le retour de l'audio ;
- le manifeste garde le trou ; la ligne de fin donne la durée de la séance et du trou ;
- la note de retour contient « [écoute coupée] » pour un trou de 10 min, et rien pour un trou de 1 min ;
- un message `visibilitychange` du navigateur est écrit au journal.

**Fini quand** : les tests passent ; à la main, une écoute dans un onglet caché 6 min donne deux alertes
et la ligne de fin avec le trou.

## Partie 4 : des mesures exactes et un réveil sans attente

**Pourquoi** : sans le nom du modèle qui répond, la latence de 15 s du 20 ne peut pas s'expliquer, et
le choix du modèle (qui reste à l'utilisateur) se fait à l'aveugle. Les réveils à 2,5 s viennent d'un
contexte du compte âgé de plus de 6 min. On ne sait pas ce qui fait revenir Joi en écoute.

**Quoi** :
- `joi/cerveau.py` : lire `ASSISTANT_USAGE` (et `SESSION_MODEL_CHANGE`) pour garder le modèle réellement
  utilisé, les jetons d'entrée et de sortie, et les jetons de réflexion s'ils y sont. La mesure de
  latence (`joi/serveur.py:405-416`) les met dans `modele` (« défaut (gpt-…) ») et dans un nouveau champ
  `jetons`. `extraire.py` (skill proposer-ameliorations) les reprend dans `stats.md` : modèle par jour,
  jetons médians. Vérifier d'abord les champs de l'événement dans le SDK.
- `veille._cache_valable` (`joi/compte/veille.py:139-149`) : un contexte du même jour de moins de
  **30 min** est servi tout de suite, avec son heure (« vu à 07:12 »), et rafraîchi en fond. Un contexte
  d'un autre jour ou invalidé par une écriture se calcule encore en direct.
- Retour en écoute : chaque passage conversation → écoute écrit sa raison au journal (bouton, commande
  « écoute seulement », fin de session sans parole, etc.), avec le nombre de tours de la conversation.
  Une conversation ouverte par « Joi, reviens » qui se ferme sans aucun tour est comptée par
  `extraire.py` (« réveils vocaux sans suite »). Ne pas changer `CONFIANCE_MIN` : décider après une
  semaine de mesures.
- `transcription.py:128` : la ligne « Écoute transcrite » donne le modèle facturé (`speech_model_used`
  des segments, le plus fréquent).

**Tests** (`tests/test_mesures.py`, `tests/test_reveil_compte.py`) :
- un faux événement `ASSISTANT_USAGE` donne le modèle et les jetons dans la ligne « Latence » ;
- un contexte de 20 min est servi sans attente ; un contexte de 40 min ou de la veille est recalculé ;
- un retour en écoute écrit sa raison ; `extraire.py` sur un faux journal compte un réveil vocal sans
  suite ;
- la ligne « Écoute transcrite » contient le nom du modèle.

**Fini quand** : les tests passent ; à la main, la ligne « Latence » donne un nom de modèle.

## Partie 5 : les échéances, et Joi qui en parle d'elle-même

**Pourquoi** : des dates qui engagent l'utilisateur (un compte à fermer au 14 décembre, un
enregistrement le samedi) dorment dans les journées. Joi ne peut pas dire « demain, tu enregistres la
batterie » ni proposer de mettre un rendez-vous à l'agenda.

**Décision de l'utilisateur (autonomie)** : Joi **propose** de vive voix ; un « oui » de la personne
crée l'événement dans l'agenda de Joi, **sans invités ni courriel**. Rien n'est créé sans ce oui.

**Quoi** :
- `joi/bibliothecaire.md`, « Fichiers essentiels » : un fichier `echeances.md` tenu à chaque passe, une
  ligne par date, au format strict `- AAAA-MM-JJ[ HH:MM] : quoi (journees/AAAA-MM-JJ.md)`, 15 lignes au
  plus. Seulement ce qui engage la personne (sa voix reconnue, ou une conversation avec Joi), jamais ce
  qu'une autre voix a dit pour elle-même (partie 1). Une date relative se calcule à partir du jour où
  elle a été dite, ou ne s'écrit pas. Les dates passées sont retirées. Préciser à `:28` que suivre un
  chantier daté qui engage la personne (aider un proche, un fournisseur) est permis, sans détails
  personnels sur les autres.
- `memoire.contexte_reveil` (`joi/memoire.py:179`) : lit `echeances.md`, en garde **dans le code** les
  lignes bien formées datées d'aujourd'hui à dans 7 jours (5 au plus, les plus proches d'abord), et les
  ajoute sous « À venir » dans `externe()`. Les lignes mal formées ou passées sont ignorées et comptées au
  journal.
- Consigne de réveil (avec la section, seulement s'il y a une échéance du jour ou du lendemain) : « Au
  début de la conversation, mentionne au plus une chose d'aujourd'hui ou de demain, en une phrase, une
  fois. » « Une fois par jour » est tenu par le code : un petit état dans `donnees/` (par exemple
  `donnees/echeances-dites.json` : date → lignes déjà données) retire la consigne aux réveils suivants
  du même jour. L'échéance reste dans « À venir ».
- Événement proposé : si une échéance a une heure et n'est pas déjà dans l'agenda du contexte, Joi peut
  demander « je le mets à ton agenda ? ». Sur un oui, elle appelle `creer_evenement`. Le gestionnaire de
  permissions (`joi/compte/integration.py:116-123`) refuse **toujours** un `creer_evenement` dont les
  invités contiennent une autre adresse que celle de la personne (`JOI_COURRIEL_PROPRIETAIRE`). Pour un
  événement né d'une échéance (paramètre `source="echeance"` à ajouter à l'outil,
  `joi/compte/serveur_mcp.py:220`), il exige aussi un oui récent (`confirmation.dernier_tour(AGE_MAX_OUI)`
  et `engagements.est_oui`), comme `marquer_proposition` (`integration.py:94-100`), et aucun invité.
- `joi/compte/personnalite.md` : une ligne sur ce cas (proposer, créer sur un oui, jamais d'invité).

**Tests** (nouveau `tests/test_echeances.py`, `tests/test_reveil_compte.py`) :
- `contexte_reveil` garde les lignes de 0 à 7 jours, écarte une date passée, une ligne mal formée et une
  9e ligne, et entoure le bloc de `externe()` ;
- la consigne « mentionne au plus une chose » n'apparaît qu'au premier réveil du jour, et seulement pour
  une échéance du jour ou du lendemain ;
- `creer_evenement` avec un invité tiers est refusé ; `source="echeance"` sans oui récent est refusé, et
  accepté avec un oui de moins de 180 s et aucun invité ;
- les consignes de la bibliothécaire contiennent le format de `echeances.md`.

**Fini quand** : les tests passent ; avec une fausse mémoire contenant une échéance de demain, le prompt
du réveil contient « À venir » et la consigne, une seule fois dans la journée.

## Ce que l'utilisateur devra vérifier lui-même

- Après la partie 1 : lire les journées d'une vraie journée d'écoute. Plus de « tu as dit » pour une
  voix inconnue ? Les projets gardent-ils l'état sans recopier ? Ensuite, relancer
  `python -m joi.memoire condenser`, Joi arrêtée, et relire la liste des faits déplacés dans le commit.
  Penser à remettre à la main ce que la condensation du 18 a perdu (comparer `854b836` et `561ddba` dans
  `donnees/memoire`).
- Partie 2 : relancer `python -m joi.compte connecter` si une vraie déconnexion arrive ; vérifier que Joi
  le dit à sa première réplique, une seule fois.
- Partie 3 : une écoute avec l'onglet caché ou le portable en veille. La ligne de fin et la note de
  retour doivent donner le trou.
- Partie 4 : après quelques jours, lire le modèle dans les lignes « Latence ». Choisir `JOI_MODELE`
  (banc `python -m joi.banc` vers 07:30, l'heure des lenteurs) reste ta décision. Regarder les « réveils
  vocaux sans suite » avant de toucher à la commande vocale.
- Partie 5 : la veille d'un rendez-vous dit pendant l'écoute, Joi en parle-t-elle au réveil ? Propose-
  t-elle l'agenda, et l'événement créé n'a-t-il aucun invité ?
- Toujours en attente des plans précédents : la fin de tour (`JOI_FIN_TOUR_*`, 5 tours sur 19 à 1,3 s
  le 20 et une réplique coupée sur une phrase dictée en trois morceaux), et le rejeu des seuils de voix
  (« toi » reconnu dans 10 % des fenêtres le 20, contre 34 % le 18).

## Hors de portée

- **Propositions d'engagement jamais dites** (5 sur 6) et **passe d'engagements plus juste** (titres
  flous, discours rapporté, « Joy/Joie », présent daté) : proposées, pas retenues pour ce plan. Le
  défaut des propositions « montrées » sans avoir été dites (`veille.py:162,174`,
  `engagements.py:336`) reste un bogue connu, à reprendre en premier dans le prochain plan sur la
  pro-activité.
- **Répliques** (capture d'écran lue en entier avec onze codes de tickets, avis de plus de trois
  phrases, six questions de suite le 20) : pas retenu cette fois.
- Hausser `CONFIANCE_MIN` de « Joi, reviens » : inutile d'après les chiffres (1,00 sur deux faux
  déclenchements). On mesure d'abord (partie 4).
- Préchauffer la session Copilot au réveil : à décider une fois le modèle connu (partie 4).
- Fermer automatiquement une écoute muette : risque de couper un vrai silence. On la signale seulement.
- Le bilan des voix le soir même (Joi est arrêtée avant 18 h) et le coût du streaming dans le coût du
  jour : utiles, mais pas prioritaires.
- Pistes déjà notées : `compte.modele.externe()` n'échappe pas sa balise de fin ; `propositions.json`
  sans verrou entre processus ; un « plus tard » pour `marquer_proposition`.

---

## État au 2026-11-21 (fin de la session 1)

Les parties 1 à 4 sont codées sur la branche `joi-fiable`, en 4 commits (plus celui du plan), avec
285 tests (244 au départ ; `.venv\Scripts\python -m pytest`, une trentaine de secondes, jamais
`donnees/`). La partie 5 (échéances) attend la session 2. Chaque partie a été relue par le
sous-agent `joi-reviseur`, et la partie 2 l'a été deux fois.

Vérifié pendant la session :
- msal 1.39 : `acquire_token_silent_with_error` rend `None` (pas de jeton dans le cache) ou un dict
  avec `error` et `error_description` ; `acquire_token_silent` fondait les deux en `None`. MSAL
  relance l'erreur réseau d'un rafraîchissement.
- Copilot SDK : `ASSISTANT_USAGE` porte `model`, `response_model`, `input_tokens`, `output_tokens`,
  `reasoning_tokens`, `cache_read_tokens`, `time_to_first_token` et `reasoning_effort` ;
  `SESSION_MODEL_CHANGE` porte `new_model` et `reasoning_effort`.
- Le Wake Lock existait déjà (`frontend/app.js`), seulement quand l'onglet est visible.
- La section « Voix entendues » est écrite par `empreintes.texte_bilan`, pas par
  `memoire.noter_voix_du_jour`, qui ne fait que l'ajouter.

Écarts au plan :
- Partie 1 : la compaction de « Voix entendues » est faite dans `empreintes.texte_bilan`. La
  vérification de l'index ne redit ses avertissements que s'ils changent (une écoute se consolide
  plusieurs fois par séance). Le test de taille des consignes mesure `bibliothecaire.md`,
  `CONSIGNE_CONDENSER` et la consigne d'écoute : environ 1 050 octets de plus, sous la limite de 1,2 Ko.
- Partie 2 : la relecture a montré qu'un nombre d'échecs ne suffit pas. Un réveil et un
  rafraîchissement à quelques secondes d'écart faisaient deux échecs. Le compte n'est donc dit
  « déconnecté » que si les échecs durent depuis 4 min (`DUREE_AVANT_DECONNEXION`). Sans contexte
  d'avant utilisable (démarrage, action de Joi, minuit), une coupure courte donne une phrase neutre,
  ni gardée ni annoncée. Un échec de jeton récent vaut 5 s pour les autres fils, sans quoi les blocs
  parallèles du réveil dépassaient 5 s. Une erreur réseau donne « Microsoft ne répond pas » (pas une
  déconnexion). Le « une fois par jour » est tenu par un attribut de la veille, en mémoire (il repart
  au redémarrage du serveur). La note de coupure passe par le contexte du réveil, ne dit que les heures
  (les courriels sont rattrapés ensuite) et est oubliée le lendemain.
- Partie 3 : les trous à dire vivent au niveau du module de `serveur.py`. Une écoute qui finit par
  une déconnexion de la page est donc dite à la conversation qui suit la reconnexion. Le seuil de
  5 min est `enregistreur.TROU_A_DIRE_S`. La note donne les heures en chiffres ; Joi les dit en lettres.
- Partie 4 : la raison du retour en écoute vient de la page (`"raison"` dans le message `mode`) ou
  du serveur (commandes vocales). L'usage est copié dans la mesure à la fin de chaque réponse ;
  celui qui arrive hors d'un tour est ignoré. Le modèle facturé est gardé par segment
  (`segment["modele"]`). Pas de test de `extraire.py` de bout en bout sur un faux journal : les
  fonctions de lecture sont testées, pas la boucle de `main()`.

Mesures (avant → après) :
- Prompt système de Joi : `personnalite.md` inchangé. Le contexte du compte gagne une phrase
  seulement quand il y a quelque chose à dire (déconnexion ou coupure, environ 200 octets).
- Consignes de la bibliothécaire : +1 050 octets environ.

Restent à l'utilisateur : les vérifications des parties 1 à 4 (« Ce que l'utilisateur devra vérifier
lui-même »), en particulier `python -m joi.memoire condenser` Joi arrêtée, et le rattrapage à la main
de ce que la condensation du 18 a perdu ; une écoute avec l'onglet caché 6 min ; un nom de modèle dans
la ligne « Latence ».
Reste à coder : la partie 5 (échéances), en session 2. Piste notée : un test de bout en bout de
`extraire.py` sur un faux journal.
