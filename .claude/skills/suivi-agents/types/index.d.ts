/** Une action d'un sous-agent : l'outil et ce sur quoi il porte, en quelques mots. */
export type Geste = { outil: string; cible: string; a: number; fini: boolean }

/** Un sous-agent suivi depuis son lancement. */
export type Suivi = {
  id: string
  type: string
  description: string
  debut: number
  fin?: number
  statut: 'actif' | 'attend' | 'fini' | 'echec' | 'arrete'
  nbOutils: number
  jetons: number
  gestes: Geste[]
}

declare module 'claude-code' {
  interface PluginState {
    'suivi-agents': { agents: Shaped<Suivi[]> }
  }
}
