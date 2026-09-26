import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { SessionSummary } from '../types'
import { SessionSheet } from './SessionSheet'

/**
 * C-12 : les taux voyagent avec leur dénominateur, en TEXTE. Un `title` ne
 * s'imprime pas, et le pied du rapport promet que « les latences et les taux
 * voyagent avec leur nombre de mesures ».
 */

const lien = {
  wan_id: '4', kind: 'cellular', operator: 'Orange', operators: ['Orange'], maneuvers: 0, samples: 720,
  mb_up: 12.5, mb_down: 80.2, rtt_samples: 412, rtt_p50_ms: 31, rtt_p90_ms: 44, rtt_max_ms: 90,
  path_latency_samples: 179, path_latency_p50_ms: 28, path_latency_p90_ms: 33, path_latency_max_ms: 61,
  rsrp_median: -98, rsrp_filled_samples: 0, outages: 1, cells: 2, cell_changes: 7,
  cell_changes_per_hour: 0.5,
}

const fiche = (diversite: object | null): SessionSummary => ({
  session: { id: 9, router_id: 'dome', started_at: '2026-09-24T13:11:41Z', first_seen: '2026-09-24T13:11:50Z',
             last_seen: '2026-09-24T16:20:47Z', samples: 3451, label: null },
  links: [lien], mb_up: 12.5, mb_down: 80.2, lat: null, lon: null, radius_m: null, sites: 1,
  cells: [], availability: null, diversity: diversite, partial: false, window_start: null, window_end: null,
} as unknown as SessionSummary)

describe('les dénominateurs se lisent, et s’impriment', () => {
  it('donne à chaque part de diversité son propre nombre de cycles', () => {
    const { container } = render(<SessionSheet fiche={fiche({
      cycles: 5912, same_site_pct: 100, same_cell_pct: 20, same_earfcn_pct: 60, operators: 1,
      site_cycles: 1999, cell_cycles: 5912, earfcn_cycles: 5000,
    })} />)
    const texte = container.textContent ?? ''
    expect(texte).toMatch(/même station 100 % du temps sur 1[\s ]999 cycles/)
    expect(texte).toMatch(/même cellule 20 % sur 5[\s ]912/)
    expect(texte).toMatch(/même fréquence 60 % sur 5[\s ]000/)
  })

  it('écrit le nombre de mesures des latences et le compte des sauts de cellule', () => {
    const { container } = render(<SessionSheet fiche={fiche(null)} />)
    const texte = container.textContent ?? ''
    expect(texte).toContain('sur 412')
    expect(texte).toContain('sur 179')
    expect(texte).toMatch(/0\.5\s*·\s*7 sauts/)
  })
})

/**
 * C-28 (F5, F7, F8) : tous les tests passaient `availability: null`, et le
 * texte de disponibilité pouvait changer de sens sans qu'aucun ne rougisse.
 */
const avecDispo = (dispo: object, liens = [lien, { ...lien, wan_id: '5' }]) => ({
  ...fiche(null), links: liens, availability: dispo,
} as unknown as SessionSummary)

describe('la disponibilité dit sur quoi elle porte', () => {
  const base = { threshold_ms: 150, at_least_one_pct: 99.9, at_least_two_pct: 97.1 }

  it('annonce les cycles EXCLUS faute de lecture de tunnel (F5)', () => {
    const { container } = render(<SessionSheet fiche={avecDispo({
      ...base, observed_cycles: 800, total_cycles: 1000, cycles_in_bond: 800,
    })} />)
    expect(container.textContent).toMatch(/Mesuré sur 800 cycles\s+sur 1[\s\u202f]000/)
    expect(container.textContent).toContain('EXCLUS du compte')
  })

  it('ne parle pas de « propre » quand le tunnel n’a porté aucun lien (F7)', () => {
    const { container } = render(<SessionSheet fiche={avecDispo({
      ...base, observed_cycles: 597, total_cycles: 597, cycles_in_bond: 0,
    })} />)
    expect(container.textContent).toContain('Le tunnel n’a porté aucun lien')
    expect(container.textContent).not.toContain('Au moins un lien propre')
  })

  it('ne pose pas « au moins deux » à un châssis d’un seul lien (F8)', () => {
    const { container } = render(<SessionSheet fiche={avecDispo({
      ...base, observed_cycles: 10, total_cycles: 10, cycles_in_bond: 10,
    }, [lien])} />)
    expect(container.textContent).toContain('Au moins un lien propre 99.9 %')
    expect(container.textContent).not.toContain('au moins deux')
    expect(container.textContent).toContain('tous observés')
  })
})
