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
    expect(texte).toMatch(/0,5\s*·\s*7 sauts/)
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
    expect(container.textContent).toContain('exclus du compte')
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

describe('les colonnes sans objet (A-7)', () => {
  const entetes = (c: HTMLElement) => [...c.querySelectorAll('thead th')].map((t) => t.textContent)

  it('tait le bond quand aucun lien n’en a de mesure', () => {
    // Vu le 17/10 sur un HD2 sans tunnel : trois colonnes « Bond » vides, et
    // un rapport si serré que « 87.2 » et « 1549.9 » se touchaient.
    const sansBond = { ...lien, rtt_samples: 0, rtt_p50_ms: null, rtt_p90_ms: null, rtt_max_ms: null }
    const { container } = render(<SessionSheet fiche={{ ...fiche(null), links: [sansBond] }} />)
    expect(entetes(container).some((t) => /Bond/.test(t ?? ''))).toBe(false)
    expect(entetes(container)).toContain('Lien médian')
  })

  it('tait la radio quand aucun lien n’est cellulaire', () => {
    const filaire = { ...lien, kind: 'ethernet', rsrp_median: null, cells: 0, cell_changes: 0,
                      cell_changes_per_hour: null }
    const { container } = render(<SessionSheet fiche={{ ...fiche(null), links: [filaire] }} />)
    for (const absente of ['RSRP médian', 'Cellules', 'Sauts de cellule / h']) {
      expect(entetes(container)).not.toContain(absente)
    }
    expect(entetes(container)).toContain('Coupures')
  })

  it('garde tout quand les mesures existent', () => {
    const { container } = render(<SessionSheet fiche={fiche(null)} />)
    expect(entetes(container)).toEqual(expect.arrayContaining(['Bond médian', 'RSRP médian', 'Cellules']))
  })
})

describe('les grandeurs s’écrivent avec leur unité, à la française', () => {
  const cellules = (c: HTMLElement) => [...[...c.querySelectorAll('tbody tr')][0].querySelectorAll('td')]
    .map((td) => td.textContent ?? '')

  it('écrit les latences et la médiane RSRP comme les colonnes voisines', () => {
    // « 33.5 » sans unité au milieu de « 12,5 » : deux conventions sur une
    // même ligne, et un nombre dont on ne sait pas ce qu'il mesure.
    const l = { ...lien, rtt_p50_ms: 33.5, rtt_p90_ms: 44.25, rtt_max_ms: 90,
                path_latency_p50_ms: 28.4, path_latency_p90_ms: 33.5, path_latency_max_ms: 61,
                rsrp_median: -98.5 }
    const { container } = render(<SessionSheet fiche={{ ...fiche(null), links: [l] }} />)
    const td = cellules(container)
    expect(td.join(' | ')).not.toMatch(/\d\.\d/)
    expect(td[3]).toMatch(/^34\sms/)
    expect(td[4]).toMatch(/^44\sms$/)
    expect(td[5]).toMatch(/^90\sms$/)
    expect(td[6]).toMatch(/^28\sms/)
    expect(td[9]).toMatch(/\d\sdBm$/)
  })

  it('n’écrit une position que si latitude ET longitude sont connues', () => {
    // `fiche.lon?.toFixed(5)` donnait « 48.85661, » quand lon manquait.
    const { container } = render(<SessionSheet fiche={{ ...fiche(null), lat: 48.85661, lon: null }} />)
    expect(container.textContent).not.toMatch(/48\.85661/)
  })
})

describe('une grandeur inconnue n’est pas un zéro', () => {
  it('ne compte pas de station quand le serveur ne sait pas', () => {
    const { container } = render(<SessionSheet fiche={{ ...fiche(null), sites: null }} />)
    expect(container.textContent).toMatch(/stations inconnues/)
  })

  it('dit des manœuvres inconnues, sans les compter pour zéro', () => {
    const l = { ...lien, maneuvers: null }
    const { container } = render(<SessionSheet fiche={{ ...fiche(null), links: [l] }} />)
    expect(container.textContent).toMatch(/manœuvres inconnues/)
  })
})
