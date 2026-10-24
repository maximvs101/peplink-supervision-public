import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { EventLog as Log, HistorySeries, RouterSummary, SessionSummary } from '../types'

vi.mock('../api/client', () => ({
  fetchSessionSummary: vi.fn(),
  fetchRouter: vi.fn(),
  fetchEvents: vi.fn(),
  fetchHistory: vi.fn(),
}))
import { fetchEvents, fetchHistory, fetchRouter, fetchSessionSummary } from '../api/client'
import { Rapport } from './Rapport'

/**
 * R-3.7 : emporter une session. Un rapport imprimable — le PDF sort de
 * l'impression du navigateur —, « à partager » par défaut : ni nom ni série de
 * l'équipement, position arrondie, aucun SSID ; le complet sur demande.
 */

const lien = (wan_id: string, kind: string, operators: string[]) => ({
  wan_id, kind, operator: operators[0] ?? null, operators, maneuvers: 0, samples: 720,
  mb_up: 12.5, mb_down: 80.2, rtt_p50_ms: null, rtt_p90_ms: null, rtt_max_ms: null,
  path_latency_samples: 179, path_latency_p50_ms: 28, path_latency_p90_ms: 33, path_latency_max_ms: 61,
  rsrp_median: -98, rsrp_filled_samples: 25, outages: 1, cells: 2, cell_changes: 1,
  cell_changes_per_hour: 0.5,
})

const fiche = (): SessionSummary => ({
  session: { id: 9, router_id: 'dome', started_at: '2026-09-24T13:11:41Z', first_seen: '2026-09-24T13:11:50Z',
             last_seen: '2026-09-24T16:20:47Z', samples: 3451, label: 'Parking nord' },
  links: [lien('2', 'cellular', ['Orange']), lien('3', 'cellular', ['Orange', 'SFR']),
          lien('6', 'wifi', ['MonReseau'])],
  mb_up: 200, mb_down: 202, lat: 48.856614, lon: 2.352222, radius_m: 12, sites: 3,
  cells: [], availability: null, diversity: null, partial: false, window_start: null, window_end: null,
} as unknown as SessionSummary)

const equipement = {
  id: 'dome', name: 'DOME-CLIENT-X', device: { model: 'Dome Pro Duo', firmware: '8.5.1 build 5714',
    serial: '0000-0000-0042', name: 'DOME-CLIENT-X' },
} as unknown as RouterSummary

const journal: Log = {
  router_id: 'dome', started_at: '2026-09-24T13:11:41Z', ended_at: '2026-09-24T16:20:47Z',
  capped: false, omitted: 0, latency_threshold_ms: 1000, churn: [],
  episodes: [
    { kind: 'coupure', wan_id: '6', link_kind: 'wifi', at: '2026-09-24T14:00:00Z', until: null, samples: 1,
      peak_ms: null, detail: 'Connected to MonReseau', after_handover_s: null },
    { kind: "changement d'opérateur", wan_id: '3', link_kind: 'cellular', at: '2026-09-24T16:07:21Z',
      until: null, samples: 1, peak_ms: null, detail: 'Orange (208-01) → SFR (208-10)', after_handover_s: null },
  ],
} as unknown as Log

const serie = (): HistorySeries => ({ router_id: 'dome', wan_id: '2', metric: 'rsrp', available: 3, points: [
  { ts: '2026-09-24T13:12:00Z', value: -98 }, { ts: '2026-09-24T13:12:05Z', value: -97 },
  { ts: '2026-09-24T13:14:00Z', value: -101, source: 'routeur' },
] })

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchSessionSummary).mockResolvedValue(fiche())
  vi.mocked(fetchRouter).mockResolvedValue(equipement)
  vi.mocked(fetchEvents).mockResolvedValue(journal)
  vi.mocked(fetchHistory).mockResolvedValue(serie())
})

const monter = (url = '/rapport/9') =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes><Route path="/rapport/:sessionId" element={<Rapport />} /></Routes>
    </MemoryRouter>,
  )

describe('rapport de session, à partager par défaut', () => {
  it('dit la session et l’équipement par son modèle, sans son nom ni sa série', async () => {
    const { container } = monter()
    expect(await screen.findByText('Rapport de session')).toBeInTheDocument()
    await waitFor(() => expect(container.textContent).toContain('Dome Pro Duo'))
    // C-38 : le libellé nomme souvent le lieu (« Parking nord », l'adresse du
    // client) : la version à partager le tait, sous « position arrondie ».
    expect(container.textContent).not.toContain('Parking nord')
    expect(container.textContent).toContain('8.5.1 build 5714')
    // Aucune position, pas même arrondie (A-12) : à ~100 m, elle désignait encore
    // le lieu d'intervention, et ce rapport sort du poste.
    for (const trace of ['DOME-CLIENT-X', '0000-0000-0042', 'MonReseau', '48.85661', '48.857', '2.352']) {
      expect(container.textContent).not.toContain(trace)
    }
  })

  it('porte la fiche, le journal et une courbe par lien', async () => {
    const { container } = monter()
    await screen.findByText('Lien médian')
    expect(container.textContent).toContain('Orange (208-01) → SFR (208-10)')
    expect(container.textContent).toContain('dont 4 min comblées')
    // RSRP et latence pour chaque SIM, latence seule pour le Wi-Fi.
    await waitFor(() => expect(screen.getAllByRole('heading', { name: /^Courbes — / })).toHaveLength(5))
    // La courbe de radio est demandée sur toute la session, pour les liens cellulaires.
    const radio = vi.mocked(fetchHistory).mock.calls.filter((c) => c[2] === 'rsrp').map((c) => c[1])
    expect(radio.sort()).toEqual(['2', '3'])
    expect(vi.mocked(fetchHistory).mock.calls[0][4]).toEqual({
      since: '2026-09-24T13:11:41Z', until: '2026-09-24T16:20:47Z' })
  })

  it('offre l’impression et les deux CSV, dans le même mode', async () => {
    monter()
    await screen.findByText('Lien médian')
    const imprimer = vi.spyOn(window, 'print').mockImplementation(() => {})
    await waitFor(() => expect(screen.getByRole('button', { name: /Imprimer/ })).not.toBeDisabled())
    screen.getByRole('button', { name: /Imprimer/ }).click()
    expect(imprimer).toHaveBeenCalled()
    expect(screen.getByRole('link', { name: /Relevés \(CSV\)/ }).getAttribute('href'))
      .toBe('/api/sessions/9/export.csv')
    expect(screen.getByRole('link', { name: /Heure du routeur \(CSV\)/ }).getAttribute('href'))
      .toBe('/api/sessions/9/export-routeur.csv')
  })
})

describe('rapport complet, sur demande', () => {
  it('ajoute le nom, la série, la position exacte et le SSID', async () => {
    const { container } = monter('/rapport/9?complet=1')
    await screen.findByText('Lien médian')
    await waitFor(() => expect(container.textContent).toContain('0000-0000-0042'))
    expect(container.textContent).toContain('DOME-CLIENT-X')
    expect(container.textContent).toContain('Parking nord')
    expect(container.textContent).toContain('48.85661')
    expect(container.textContent).toContain('Connected to MonReseau')
    expect(screen.getByRole('link', { name: /Relevés \(CSV\)/ }).getAttribute('href'))
      .toBe('/api/sessions/9/export.csv?complet=true')
  })
})

describe('un échec de lecture n’est pas une absence de mesure (C-22)', () => {
  it('dit qu’une courbe n’a pas pu être lue, au lieu de « aucune mesure »', async () => {
    // Une requête /history qui expire sur une longue session : le PDF remis à
    // un tiers affirmait « Aucune mesure sur la session » pour ce lien.
    vi.mocked(fetchHistory).mockRejectedValue(new Error('délai dépassé'))
    const { container } = monter()
    await waitFor(() => expect(container.textContent).toContain('Lecture impossible'))
    expect(container.textContent).not.toContain('Aucune mesure sur la session')
  })

  it('dit que le journal n’a pas pu être relu, au lieu d’une relecture sans fin', async () => {
    vi.mocked(fetchEvents).mockRejectedValue(new Error('500'))
    const { container } = monter()
    await waitFor(() => expect(container.textContent).toContain('Journal non relu'))
    expect(container.textContent).not.toContain('Relecture de la fenêtre en cours')
  })

  it('n’offre l’impression qu’une fois toutes les lectures terminées', async () => {
    let finir: (j: Log) => void = () => {}
    vi.mocked(fetchEvents).mockReturnValue(new Promise<Log>((r) => { finir = r }))
    monter()
    await screen.findByText('Lien médian')
    expect(screen.getByRole('button', { name: /Imprimer/ })).toBeDisabled()
    finir(journal)
    await waitFor(() => expect(screen.getByRole('button', { name: /Imprimer/ })).not.toBeDisabled())
  })
})
