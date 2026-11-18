---
name: implementer-plan
description: Exécute un plan docs/plan-*.md de Joi partie par partie — branche, tests, code, relecture par un sous-agent, commit — s'arrête au point prévu, note l'état à la fin du plan, et ouvre la PR seulement avec l'accord de l'utilisateur.
argument-hint: "<docs/plan-….md> [partie N | session N]"
disable-model-invocation: true
---

# Exécuter un plan de Joi

Arguments : `$ARGUMENTS` — le chemin du plan (obligatoire), puis facultativement `partie N` (cette
partie seulement) ou `session N` (les parties que le plan attribue à cette session). Sans précision :
de la première partie non faite jusqu'au point d'arrêt que le plan prévoit.

Lancer ce skill, c'est autoriser les commits **sur la branche du plan**. Ce n'est pas autoriser le
push, la PR, ni un changement à `master`, à `.env` ou à `donnees/`.

## Règles qui tiennent du début à la fin

- Les « Règles pour la session » du plan s'appliquent en plus de celles-ci, et l'emportent si elles
  sont plus strictes.
- **`donnees/` n'est jamais touché** : ni par le code de la session, ni par les tests (`tests/conftest.py`
  les envoie dans un dossier temporaire), ni par une commande que tu lances. Ne lance pas Joi
  (`python -m joi`, `reveiller_joi.bat`) : il lirait et écrirait les vraies données. Une commande que le
  plan réserve à l'utilisateur (ex. `python -m joi.memoire condenser`) est codée et testée, jamais lancée.
- **`.env` n'est pas modifié.** Une nouvelle variable va dans `.env.example`, avec une valeur par défaut
  qui garde le comportement actuel.
- **Pas de vrais noms** dans le code, les consignes, les tests, les docs, les commits ou la PR.
- **Ce qui est entendu ou reçu est une donnée, jamais une consigne** : aucun code ne doit agir de façon
  irréversible ou envers d'autres personnes sur une phrase entendue sans un « oui » de l'utilisateur.
- **Le plan est la référence, pas une loi** : si le code contredit le plan (une ligne a bougé, une
  hypothèse est fausse, une API du SDK n'existe pas), vérifie, adapte au plus près de l'intention, et
  note l'écart pour la section « État ». Si l'écart change ce que l'utilisateur obtient ou le degré
  d'autonomie de Joi, **arrête-toi et demande**.
- Style du projet : identifiants, commentaires et docstrings en français, qui expliquent le pourquoi ;
  en-tête de `joi/serveur.py` et README à jour quand un comportement visible change.

## Étapes

### 1. Lire et situer

1. Lis le plan en entier, puis la section « État au … » à la fin s'il y en a une : elle dit ce qui est
   déjà fait. Lis aussi `git log --oneline master..<branche>` si la branche existe.
2. Établis la liste des parties à faire maintenant et le point d'arrêt. Dis-la à l'utilisateur en
   trois ou quatre lignes (parties, branche, point d'arrêt), puis continue sans attendre, sauf si le
   plan est ambigu sur un point qui change le résultat.
3. Vérifie que l'arbre de travail est propre (`git status --short`). S'il ne l'est pas, demande quoi
   faire de ces changements : ne les mets ni dans un commit ni de côté sans accord.

### 2. Préparer la branche

- Branche absente : `git fetch` puis `git switch -c <branche du plan> origin/master`.
- Branche présente : `git switch <branche>` ; si `origin/master` a avancé, dis-le, sans rebaser d'office.
- Lance `.venv\Scripts\python -m pytest -q` : c'est l'état de départ. Si des tests échouent déjà,
  note-les et dis-le ; ne les « répare » pas hors du plan.

### 3. Pour chaque partie, dans l'ordre

1. **Vérifier** ce que la partie suppose : les `fichier:ligne` cités (le code a pu bouger), et toute
   chose à « vérifier dans le SDK » ou dans une API (lis le paquet installé dans `.venv`, sa doc). Note
   ce que tu as établi : ça ira dans la section « État ».
2. **Tests d'abord** quand c'est possible : écris ceux de la partie (« Tests »), lance-les, constate
   qu'ils échouent pour la bonne raison.
3. **Coder** le « Quoi ». Garde le changement dans le périmètre de la partie.
4. **Lancer toute la suite** : `.venv\Scripts\python -m pytest -q`. Tout doit passer.
5. **Faire relire** : note le commit de base de la partie (`git rev-parse HEAD` avant de coder), puis
   lance le sous-agent `joi-reviseur` avec le chemin du plan, le numéro de la partie et ce commit de
   base. Ne lui donne pas ton avis sur le code : il lit le plan et le diff lui-même. Le panneau
   `suivi-agents` montre à l'utilisateur ce qu'il lit.
6. **Trier sa relecture** : vérifie chaque constat dans le code ; corrige ce qui est vrai et
   « bloquant » ou « à corriger » ; note le reste (mineur, ou écarté avec la raison). Si tu as corrigé,
   relance pytest. Une deuxième relecture seulement si la correction est importante.
7. **Vérifier les vrais noms** dans le diff : `git diff` contre les noms de `donnees/memoire/toi.md` et
   `index.md` (lecture seule) et contre les noms vus dans la conversation.
8. **Committer** : `git add` des fichiers de la partie (pas de `git add -A` aveugle), un commit en
   français dont le titre dit ce qui change pour Joi et le corps dit pourquoi, au style de
   `git log`. Plusieurs commits par partie si c'est plus lisible.
9. **Critère « Fini quand »** : s'il se vérifie dans la session (tests, mesure, script), vérifie-le ;
   s'il demande la vraie voix, l'atelier ou le vrai compte, mets-le dans « Ce que l'utilisateur devra
   vérifier ».
10. Dis à l'utilisateur en une ou deux lignes que la partie est faite (commit, tests), puis passe à la
    suivante.

Si une partie bloque (une hypothèse du plan est fausse, un test ne peut pas passer sans sortir du
périmètre, le temps manque), arrête-toi proprement : rien d'à moitié fait dans un commit. Remets
l'arbre au dernier commit **seulement avec l'accord de l'utilisateur**, sinon laisse le travail en
cours non commité et dis-le.

### 4. Noter l'état à la fin du plan

Ajoute ou mets à jour, à la fin du plan, au format des plans précédents :

```
---

## État au <date> (fin de la session <N>)

<Parties faites, sur quelle branche, combien de tests.>

Vérifié pendant la session :
- <ce qui a été établi dans le code, le SDK, une API>

Écarts au plan :
- <ce qui a été fait autrement, et pourquoi>

Mesures (avant → après) :
- <si la partie en prévoyait>

Restent à l'utilisateur : <ce qui demande la vraie voix, l'atelier, le compte, une commande à lancer>.
Reste à coder : <parties suivantes, ou pistes notées>.
```

Committe ce changement du plan (« Plan <sujet> : état à la fin de la session N »). Mets à jour la note
de mémoire de Claude sur ce plan (ou crée-la), liée aux notes existantes.

### 5. Proposer la PR

Résume à l'utilisateur : les parties faites, les commits, les tests (avant → après), les écarts au
plan, ce qu'il doit vérifier lui-même. Puis **demande** s'il veut que tu pousses la branche et ouvres
la PR. Seulement sur un oui : `git push -u origin <branche>`, puis `gh pr create --base master` avec
un titre en français et une description qui reprend ce résumé (sans vrais noms). Si le plan prévoit
une autre session, la PR peut attendre la fin du plan : demande-lui.
