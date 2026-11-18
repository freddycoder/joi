import { atom, read, update } from 'claude-code'
import type { AgentStatus, EngineInterface, Register, Timer } from 'claude-code'

import type { Geste, Suivi } from '../types'

const PANNEAU = 'suivi-agents'
const TITRE = 'Sous-agents'
// Changer l'étiquette quand la forme d'un Suivi change : l'ancienne valeur survit au rechargement.
const agents = atom({ plugin: 'suivi-agents', key: 'agents' } as const, [], { shape: 'suivi-1' })

const GESTES_GARDES = 4
const AGENTS_GARDES = 12

const STATUTS: Record<AgentStatus, Suivi['statut']> = {
  pending: 'actif',
  running: 'actif',
  waiting: 'attend',
  idle: 'attend',
  completed: 'fini',
  failed: 'echec',
  killed: 'arrete',
}

const PASTILLES: Record<Suivi['statut'], { signe: string; couleur: string }> = {
  actif: { signe: '●', couleur: 'text-accent' },
  attend: { signe: '◐', couleur: 'text-warning' },
  fini: { signe: '✓', couleur: 'text-success' },
  echec: { signe: '✗', couleur: 'text-danger' },
  arrete: { signe: '■', couleur: 'text-muted' },
}

/** Ce sur quoi porte un appel d'outil, en quelques mots : un nom de fichier, un motif, une commande. */
function cible(e: Record<string, unknown>): string {
  const texte = (v: unknown) => (typeof v === 'string' ? v : '')
  const chemin = texte(e.file_path) || texte(e.notebook_path)
  if (chemin) {
    const morceaux = chemin.split(/[\\/]/)
    const nom = morceaux.slice(-2).join('/')
    const plage = typeof e.offset === 'number' ? ` (dès la ligne ${e.offset})` : ''
    return nom + plage
  }
  const motif = texte(e.pattern)
  if (motif) return `« ${motif} »` + (texte(e.path) ? ` dans ${texte(e.path).split(/[\\/]/).pop()}` : '')
  const brut =
    texte(e.command) || texte(e.description) || texte(e.query) || texte(e.url) || texte(e.prompt) || ''
  return brut.replace(/\s+/g, ' ')
}

// Caractères de contrôle et de direction (bidi) : venus d'une commande ou d'une consigne d'agent, ils
// pourraient brouiller le terminal ou réordonner le texte affiché.
const CONTROLE = /[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁦-⁩﻿]/g

/** Une ligne affichable : sans caractères de contrôle, espaces resserrés. */
function propre(texte: string): string {
  return texte.replace(CONTROLE, ' ').replace(/\s+/g, ' ').trim()
}

function couper(brut: string, largeur: number): string {
  const texte = propre(brut)
  return texte.length <= largeur ? texte : texte.slice(0, Math.max(1, largeur - 1)) + '…'
}

function duree(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')}`
}

function milliers(n: number): string {
  return n < 1000 ? String(n) : `${Math.round(n / 100) / 10} k`
}

let sondage: Timer | undefined

/** Tant qu'un agent travaille, relit leur état (statut, jetons) toutes les deux secondes. */
async function sonder($: EngineInterface): Promise<void> {
  if (sondage) return
  sondage = $.clock.every(2000, async () => {
    const liste = await $.agent.list()
    const maintenant = await $.clock.now()
    let encoreActif = false
    await update($, agents, suivis =>
      suivis.map(s => {
        const info = liste.find(a => a.id === s.id)
        if (!info) {
          // Le moteur a oublié l'agent : il est fini depuis un moment.
          return s.statut === 'actif' || s.statut === 'attend'
            ? { ...s, statut: 'fini' as const, fin: s.fin ?? maintenant }
            : s
        }
        const statut = STATUTS[info.status]
        const termine = statut === 'fini' || statut === 'echec' || statut === 'arrete'
        if (!termine) encoreActif = true
        return { ...s, statut, jetons: info.tokenCount, fin: termine ? (s.fin ?? maintenant) : undefined }
      }),
    )
    if (!encoreActif && sondage) {
      sondage.cancel()
      sondage = undefined
    }
  })
}

export const register: Register = on => {
  sondage = undefined

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'suivi-agents',
      description: 'Ouvre le panneau qui montre ce que font les sous-agents',
    })
    return next(e)
  })

  on('command.run', { command: 'suivi-agents' }, async $ => {
    await $.ui.open({ id: PANNEAU, title: TITRE })
    return { text: 'Panneau des sous-agents ouvert.' }
  })

  on('agent.spawn', async ($, e, next) => {
    const lance = await next(e)
    if (lance.deny !== undefined || !lance.agentId) return lance

    const suivi: Suivi = {
      id: lance.agentId,
      type: e.subagentType,
      description: e.description,
      debut: await $.clock.now(),
      statut: 'actif',
      nbOutils: 0,
      jetons: 0,
      gestes: [],
    }
    await update($, agents, suivis => [...suivis.filter(s => s.id !== suivi.id), suivi].slice(-AGENTS_GARDES))
    void $.ui.open({ id: PANNEAU, title: TITRE })
    void sonder($)
    return lance
  })

  on('tool.call', async ($, e, next) => {
    const id = e.agentId
    if (!id) return next(e)

    const geste: Geste = {
      outil: e.tool.replace(/^mcp__[^_]+__/, ''),
      cible: cible(e as unknown as Record<string, unknown>),
      a: await $.clock.now(),
      fini: false,
    }
    const marquer = (fini: boolean) =>
      update($, agents, suivis =>
        suivis.map(s => {
          if (s.id !== id) return s
          if (!fini) {
            return { ...s, statut: 'actif', nbOutils: s.nbOutils + 1, gestes: [...s.gestes, geste].slice(-GESTES_GARDES) }
          }
          return { ...s, gestes: s.gestes.map(g => (g.a === geste.a && g.outil === geste.outil ? { ...g, fini: true } : g)) }
        }),
      )

    await marquer(false)
    try {
      return await next(e)
    } finally {
      await marquer(true)
    }
  })

  on('ui.render', { component: 'Pane', requestId: PANNEAU }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const suivis = await read($, agents)
    const maintenant = await $.clock.now()
    const largeur = Math.max(30, (e.viewport?.columns ?? 60) - 4)

    if (suivis.length === 0) {
      return (
        <Box flexDirection="column">
          <Text dimColor>Aucun sous-agent lancé dans cette session.</Text>
          <Text dimColor>Le panneau se remplit dès qu'un agent démarre.</Text>
        </Box>
      )
    }

    const actifs = suivis.filter(s => s.statut === 'actif' || s.statut === 'attend').length
    return (
      <Box flexDirection="column">
        <Text dimColor>
          {actifs} en cours · {suivis.length - actifs} terminé(s)
        </Text>
        {[...suivis].reverse().map(s => {
          const p = PASTILLES[s.statut]
          const temps = duree((s.fin ?? maintenant) - s.debut)
          const chiffres = `${temps} · ${s.nbOutils} outil(s) · ${milliers(s.jetons)} jetons`
          const termine = s.statut !== 'actif' && s.statut !== 'attend'
          const gestes = termine ? s.gestes.slice(-1) : s.gestes
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text>
                <Text color={p.couleur}>{p.signe} </Text>
                <Text bold>{couper(s.type, 40)}</Text>
                <Text dimColor> · {chiffres}</Text>
              </Text>
              <Text dimColor={termine}>  {couper(s.description, largeur - 2)}</Text>
              {gestes.map(g => (
                <Text dimColor={g.fini}>
                  {'    '}
                  {g.fini ? '·' : '›'} {couper(`${g.outil} ${g.cible}`, largeur - 6)}
                </Text>
              ))}
            </Box>
          )
        })}
      </Box>
    )
  })
}
