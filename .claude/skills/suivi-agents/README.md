# suivi-agents

Un mod de Claude Code : un panneau à côté de la conversation qui montre, pour chaque sous-agent lancé
(par exemple les analystes de `/proposer-ameliorations`), son état, son type, sa tâche, sa durée, ses
outils et ses jetons, et ses dernières actions (`› Read revisions/ecoute.md`).

Claude Code le charge tout seul depuis `.claude/skills/suivi-agents/` quand on travaille dans ce dépôt
(`claude plugin list` le montre sous « Skills-directory plugins »). Le panneau s'ouvre dès qu'un agent
démarre, ou avec `/suivi-agents`. Il lui faut un terminal assez large (environ 110 à 144 colonnes).

- `hooks/register.tsx` : les hooks (`agent.spawn`, `tool.call` des sous-agents, le dessin du panneau).
- `types/index.d.ts` : la forme de l'état gardé (`$.state`).
- Vérifier : `claude plugin validate .claude/skills/suivi-agents`.
- `.claude-plugin/types/` est écrit par Claude Code et ignoré par git.
