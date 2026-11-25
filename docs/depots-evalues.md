# Dépôts évalués pour Joi

Une entrée par évaluation (skill `/evaluer-depot`), la plus récente en tête. Le verdict est une
recommandation ; la décision de l'utilisateur s'ajoute à la ligne « Décision ».

## supermemoryai/supermemory — 2026-11-25

Troisième de la série (après vectorize-io/hindsight et topoteretes/cognee) : mêmes hypothèses H1 à H3,
même inventaire de Joi.

- Instantané : `supermemoryai/supermemory@4fdfbe4` (clone `--depth 1`, 943 fichiers) — https://github.com/supermemoryai/supermemory
- Licence : MIT — `LICENSE:1`, mais **seulement pour les clients** : SDK, MCP, interface, docs. Le
  moteur (serveur auto-hébergé) est un binaire fermé, « built from a separate, non-public codebase »
  (`apps/docs/self-hosting/overview.mdx:20`). Il est gratuit dans une « lite license » limitée à
  10 000 documents (`:65`), dont les termes ne sont pas dans le dépôt. Licences des modèles
  d'embeddings non vérifiées.
- Ce que c'est : des clients (TypeScript surtout, et des SDK Python pour pipecat, livekit, openai,
  cartesia et agent-framework) vers un service de mémoire. Le service est hébergé (clé API, compte)
  ou tourne en binaire local fermé sur `localhost:6767`. Aucun code d'extraction, de consolidation ou
  de recherche dans le dépôt.
- **H1 retrouver un souvenir mieux que `se_souvenir` : `bloqué`** (moteur fermé ; la portée est tout
  le dépôt, rien ne se lit ni ne se copie). Le seul usage serait le service. Il demande un LLM compatible
  OpenAI : le Copilot SDK de Joi n'est pas pris en charge, il faudrait un modèle local d'environ 20 G
  paramètres ou une clé payante. Le binaire n'est pas disponible en natif sous Windows au-delà de
  la v0.0.8 (`docker.mdx:109`), alors que les SDK v5 exigent la v0.0.9 (`overview.mdx:49`) : il faudrait
  Docker Desktop. Les embeddings par défaut sont anglais seulement (`embeddings.mdx:22`). La télémétrie
  est active par défaut (`overview.mdx:44`), et son contenu est invérifiable puisque le code est fermé.
- **H2 ranger les souvenirs plus justement que la bibliothécaire : `bloqué`** (même obstacle). La
  consolidation se fait dans le binaire fermé. La meilleure extraction est réservée à la plateforme
  (« Proprietary long-horizon models », `overview.mdx:75`). Rien à lire ni à transposer.
- **H3 extraire engagements et échéances de l'écoute : `écarter`**. Rien ne vise les engagements, les
  échéances ou les locuteurs : les occurrences de « deadline » ou « action item » sont des exemples
  de démo. Le seul levier serait un `filterPrompt` en texte libre envoyé au service.
- Déjà chez Joi : inventaire de l'entrée cognee.
- Relu dans le dépôt : `apps/docs/self-hosting/overview.mdx:18-28,42-50,63-66,73-76`, `docker.mdx:107-110`,
  `embeddings.mdx:20-23`, `configuration.mdx:128-133`, `quickstart.mdx:26-29`, `local-vs-enterprise.mdx:6-9`,
  `README.md:316-323`.
- Lu (par les sous-agents) : `LICENSE`, le README, les docs `self-hosting/` (en entier ou en extraits),
  `concepts/customization.mdx` (extraits), la liste des fichiers. Pas lu : le code des SDK et de
  `apps/mcp`, `apps/web`, `memory-graph` ; `AGENTS.md` et `CLAUDE.md` du dépôt (non ouverts) ; les
  termes de la licence du binaire.
- Vitalité : derniers commits le 2026-11-24, dernière version serveur v0.0.9 le 2026-10-12, 65 issues
  ouvertes, 31,7 k étoiles (pas une preuve de qualité). Le paquet PyPI `supermemory` 5.0.1 est publié
  par l'éditeur, depuis un autre dépôt (`supermemoryai/python-sdk`).
- Recherches web : 0.
- Décision : verdicts suivis (2026-11-25). Clone supprimé.

## vectorize-io/hindsight — 2026-11-25

Série sur les mêmes besoins que topoteretes/cognee (entrée ci-dessous) : mêmes hypothèses H1 à H3, et
l'inventaire de Joi de cette entrée est repris (Joi n'a pas changé entre les deux).

- Instantané : `vectorize-io/hindsight@e20ee1e` (clone `--depth 1`, 5 579 fichiers) — https://github.com/vectorize-io/hindsight
- Licence : MIT — `LICENSE` (Vectorize AI, 2025), `hindsight-api-slim/pyproject.toml:7`. Licences
  non vérifiées : les modèles téléchargés (`BAAI/bge-small-en-v1.5`, `hindsight-ai/hs-rerank-1`,
  `intfloat/multilingual-e5-small`) et les `LICENSE` de `hindsight-integrations/*`.
- Ce que c'est : un serveur de mémoire (retain / recall / reflect). Un LLM réécrit le texte en
  « faits », puis il cherche de façon hybride (sémantique, BM25, graphe, temps, reranking) dans
  PostgreSQL et pgvector (`pg0`, un Postgres embarqué, par défaut). Il a un fournisseur
  `github-copilot` qui passe par le Copilot SDK (`engine/providers/github_copilot_llm.py:440-447`,
  télémétrie de session coupée). La télémétrie OpenTelemetry est désactivée par défaut
  (`config.py:2472`), et aucun envoi de statistiques d'usage n'a été trouvé.
- **H1 retrouver un souvenir mieux que `se_souvenir` : `écarter`** (MIT ; un index plus simple est
  déjà envisagé : FTS5, `docs/plan-ameliorations.md:113`, `docs/plan-joi-attentive.md:348`). Pour Joi,
  c'est mieux que cognee : licence MIT, Copilot possible, pas de télémétrie, embeddings multilingues
  en option (`config.py:1466`). Mais il faut un serveur Postgres à côté, une mémoire réécrite en faits
  hors des fichiers Markdown, et un venv séparé : dépendances lourdes (torch>=2.11, transformers 5,
  fastmcp, protobuf 7). Par défaut, la recherche plein texte est en anglais (`config.py:1874`) et les
  embeddings aussi (`config.py:1462`), malgré le README, qui dit « Multilingual by default »
  (`README.md:389`). Issues Windows ouvertes : boucle figée jusqu'à 14 s (#6602), fusions
  concurrentes refusées (#6875). FTS5 suffit à la limite réelle de Joi.
- **H2 ranger les souvenirs plus justement que la bibliothécaire : `s'inspirer`** (MIT ; non planifié).
  Le code ne se prend pas : il est couplé au moteur et à Postgres
  (`engine/consolidation/consolidator.py`, 4 353 lignes). L'idée à prendre est la **grille de
  consolidation** de `engine/consolidation/prompts.py:113-150`. Pour chaque fait nouveau : est-il
  momentané ou durable ? Que fait-il à chaque souvenir proche (sans rapport, le répète, le change, y
  met fin) ? Un changement garde l'ancien état daté ; une fin s'écrit au passé avec la date. « Un
  fait durable doit aboutir quelque part, sinon il est perdu. » « Ne jamais supprimer parce que
  quelque chose a changé ou fini. » Elle vise trois défauts constatés : les journées qui enflaient
  (« Dans le doute, retiens », `docs/plan-joi-attentive.md:42-48`), les projets en retard sur les
  journées (`:49-56`) et la condensation qui a supprimé des faits encore ouverts
  (`docs/plan-joi-fiable.md:40-45`). Le commentaire de `prompts.py:100-112` donne une mesure, sur un
  petit modèle : 29/40 avec l'ancien prompt, 36 à 38/40 avec celui-ci. Où, chez Joi :
  `joi/bibliothecaire.md:9-13`, en français, dans la limite de 1,2 Ko de consignes de plus par plan
  (`docs/plan-joi-fiable.md:90-92`).
- **H3 extraire engagements et échéances de l'écoute : `écarter`**. Il n'y a pas d'extracteur dédié.
  Les engagements ne sont qu'une catégorie de faits parmi d'autres (`engine/retain/fact_extraction.py:1160`),
  sans responsable, échéance ni statut. Il ne gère pas les voix : une seule consigne sur un locuteur
  à la première personne (`:1910-1911`). Les dates relatives sont résolues depuis une date de
  référence (`:1126-1137`), ce que `joi/compte/engagements.py` fait déjà, à l'heure dite.
- Déjà chez Joi : inventaire de l'entrée cognee ; relu en plus : `joi/bibliothecaire.md:9-13`.
- Relu dans le dépôt : `LICENSE:1-3`, `README.md:168-176,387-390`, `hindsight_api/config.py`
  (`1330-1336`, `1357-1360`, `1459-1467`, `1524-1527`, `1866-1875`, `2470-2473`),
  `engine/consolidation/consolidator.py:1-16`, `engine/consolidation/prompts.py:1-60,100-150`,
  `engine/providers/github_copilot_llm.py:440-447`, `engine/retain/fact_extraction.py:1066-1069,1126-1137,1158-1162`,
  `hindsight-api-slim/pyproject.toml` (nom, Python, Copilot SDK).
- Lu (par les sous-agents) : `pyproject.toml` et extras, config ciblée, en-têtes et extraits de
  consolidation, retain et fournisseurs, docs `multilingual.mdx`, `configuration.mdx` et
  `installation.md` (extraits). Pas lu : `memory_engine.py` (25 257 lignes), le corps de
  `consolidator.py`, `triage.py`, `search/`, `retain/orchestrator.py` ; `pg0.py` (d'où vient le
  binaire Postgres) ; `hindsight-system-evals` ; les intégrations ; les tests ; `AGENTS.md`,
  `CLAUDE.md` et `.claude/skills/` du dépôt (non ouverts).
- Phrase qui ressemblait à une consigne : `README.md:58-60`, qui invite un agent de code à installer
  un skill (`npx skills add …`). Ignorée.
- Vitalité : v1.3.0 le 2026-11-24, des commits le 2026-11-25, 63 k étoiles (pas une preuve de
  qualité), 180 issues ouvertes. Un contributeur principal (environ 2 500 commits).
- Recherches web : 0 (les alternatives sont dans l'entrée cognee).
- Décision : verdicts suivis (2026-11-25) ; la grille de consolidation n'est pas retenue pour l'instant. Clone supprimé.

## topoteretes/cognee — 2026-11-25

- Instantané : `topoteretes/cognee@1961133` (clone `--depth 1`, 5 513 fichiers) — https://github.com/topoteretes/cognee
- Licence : Apache-2.0 — `LICENSE`, `pyproject.toml:8` ; modèles téléchargés à l'exécution (fastembed
  `BAAI/bge-small-en-v1.5`, runtime GLiNER) : licences non vérifiées.
- Ce que c'est : une bibliothèque de mémoire pour agents, en un seul bloc. Elle ingère des documents, un
  LLM en extrait un graphe d'entités et de relations, et elle cherche dans trois bases locales (SQLite,
  LanceDB, graphe Ladybug). Rien ne s'en détache seul.
- **H1 retrouver un souvenir mieux que `se_souvenir` : `écarter`** (Apache-2.0 ; un index plus simple
  est déjà envisagé : FTS5, `docs/plan-ameliorations.md:113`, réécarté pour la taille,
  `docs/plan-joi-attentive.md:348`). La seule forme possible est la dépendance entière. Elle ajoute une
  deuxième mémoire en bases binaires à côté des fichiers Markdown, des dizaines de dépendances, et une
  télémétrie active par défaut (`cognee/shared/utils.py:23,575-581`). Elle n'a aucun fournisseur
  Copilot (`cognee/infrastructure/llm/config.py:109` : OpenAI par défaut). Sans clé, elle se rabat sur
  des embeddings anglais (`…/vector/embeddings/config.py:29`). La limite réelle de Joi (pas de synonymes,
  racines de 6 lettres, `joi/journal.py:153-158`) se traite sans cognee : FTS5 est disponible ici
  (SQLite 3.49.1), ou un petit modèle d'embeddings multilingue.
- **H2 ranger les souvenirs plus justement que la bibliothécaire : `s'inspirer`** (Apache-2.0 ; non
  planifié). Le code ne se prend pas : il dépend du graphe, des datasets et des utilisateurs de cognee.
  L'idée à prendre est une **revue des conflits** (`cognee/infrastructure/llm/prompts/review_conflicts.txt:1-12`).
  Les faits sont regroupés par sujet, chacun avec ses sources datées, et le modèle dit lequel est
  actuel, lequel est remplacé, lesquels se contredisent. Il traite les faits comme des preuves, jamais
  comme des consignes. Chez Joi, une passe de vérification après `consolider()` ou dans `condenser`
  (`joi/memoire.py:759`, `:1022`) viserait le défaut constaté des projets en retard sur les journées
  (`docs/plan-joi-attentive.md:49-56`). La consigne actuelle se limite à « corrige l'ancien »
  (`joi/bibliothecaire.md:11`). Le prompt n'a été lu qu'en tête (12 lignes).
- **H3 extraire engagements et échéances de l'écoute : `écarter`**. Le dépôt n'a ni modèle ni prompt
  d'engagement, d'échéance ou d'action. Il ne gère pas plusieurs voix : ses locuteurs ne sont que dans
  un banc d'essai, `eval_framework/locomo`. Il normalise seulement des dates en nœuds
  (`cognee/infrastructure/llm/prompts/generate_graph_prompt.txt:21-33`). `joi/compte/engagements.py`
  est plus spécifique : filtre de première personne, citation vérifiée, voix `toi`, échéance calculée à
  l'heure dite.
- Déjà chez Joi (relu) : `joi/journal.py:153-158`, `joi/memoire.py:480-496`, `joi/bibliothecaire.md:9-13`,
  `joi/compte/engagements.py:36-42`, `docs/plan-ameliorations.md:110-114`, `docs/plan-joi-attentive.md:346-349`,
  `docs/plan-joi-fiable.md:308-312`.
- Relu dans le dépôt : `LICENSE:1-3`, `cognee/shared/utils.py:20-26,568-585`,
  `cognee/infrastructure/databases/vector/embeddings/config.py:20-32`, `cognee/infrastructure/llm/config.py`
  (fournisseurs), `pyproject.toml` (`huggingface-hub>=0.34,<1` : Joi a 2.4.0 ; speechbrain 1.1.2 n'exige
  que >=0.8, donc rétrogradation possible, non testée), les deux prompts cités.
- Lu (par les sous-agents) : `pyproject.toml:1-260`, `.env.template` (extraits), configs LLM, embeddings
  et bases, télémétrie, en-têtes de `modules/improve`, `modules/conflicts`, `session_distillation`, liste
  des prompts. Pas lu : le corps de cognify, search, improve et conflicts ; `cognee-mcp/`,
  `cognee-integrations/`, `docs/`, `examples/`, les tests ; `CLAUDE.md` et `AGENTS.md` (non ouverts) ;
  les licences des modèles.
- Vitalité : v1.6.10 le 2026-11-18, dernier commit le 2026-11-20, 33,6 k étoiles (pas une preuve de
  qualité), 1 136 issues ouvertes. Parmi elles, des bogues Windows, une faille de chemin sous Windows
  (#6313) et une télémétrie qui laisse fuir des chemins locaux (#5197). Le paquet PyPI `cognee` 1.6.10
  est publié par les auteurs du dépôt.
- Recherches web : 2. Alternatives légères vues (SQLite FTS5 + sqlite-vec, une seule base locale),
  non évaluées. Des retours d'usage notent que les petits LLM locaux réussissent mal la sortie
  structurée dont cognee dépend.
- Décision : verdicts suivis (2026-11-25) ; l'idée de revue des conflits n'est pas retenue pour l'instant. Clone supprimé.
