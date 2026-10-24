import { act, render, screen } from '@testing-library/react'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  fetchRouter: vi.fn(),
  fetchSessions: vi.fn(),
  fetchTimeline: vi.fn(),
  fetchEvents: vi.fn(),
  fetchSessionSummary: vi.fn(),
  httpStatus: (e: unknown) => (e instanceof Error ? ((e as Error & { status?: number }).status ?? null) : null),
  openLiveSocket: vi.fn(() => () => {}),
}))

import {
  fetchEvents, fetchRouter, fetchSessions, fetchTimeline, openLiveSocket,
} from '../api/client'
import type { RouterSummary, Session, Timeline } from '../types'
import { sampleRealiste } from '../test/factories'
import { Fraicheur } from '../components/Fraicheur'
import { DeviceShell, useDevice } from './DeviceShell'

/* Fraîcheur par partie d'écran, et « Réessayer maintenant » sur les bandeaux
 * d'erreur de l'enveloppe d'un équipement. */

const summary = (over: Partial<RouterSummary> = {}): RouterSummary => ({
  id: 'mbx', name: 'MBX', mode: 'router', site_lat: null, site_lon: null,
  wans: [sampleRealiste({ wan_id: '4' })], device: null,
  capabilities: {
    gps: false, pepvpn: false, wan_types: {}, lan_repurposed: [], lan_networks: [],
    usb_modem_slots: 0, sim_slots: 2, per_wan_throughput: true, provisional: false, source: 'declared',
    sfc_licensed: null, sfc_suspended: null,
    sfc_throughput_limit_mbps: null, pepvpn_max_active_profiles: null,
    sfc_expires_at: null, sfc_quota_remaining_mb: null, sfc_quota_limit_mb: null,
  },
  status: 'connecté', error: null, age_seconds: 1, period_seconds: 5.3, cycle_seconds: 2.3,
  bond_routes: [], bond_profiles: [], lan_clients: null, latency_threshold_ms: 1000,
  tunnel_uptime_s: null, tunnel_attendu: null, never_connected: [], alerts: [],
  ...over,
})

const bande = (): Timeline => ({
  router_id: 'mbx', started_at: '2026-09-11T11:00:00Z', ended_at: '2026-09-11T14:00:00Z',
  capped: false, slice_count: 0, slice_seconds: 60, lanes: [],
})

const session = (id: number, depuis: string): Session => ({
  id, router_id: 'mbx', started_at: depuis, first_seen: depuis, last_seen: depuis, samples: 1, label: null,
})

/** Ce qu'un écran montrerait : la donnée, et la mention de fraîcheur. */
function Espion() {
  const { timeline, fraicheur } = useDevice()
  return (
    <div>
      <span data-testid="bande">{timeline ? 'présente' : 'absente'}</span>
      <Fraicheur nom="Bande" etat={fraicheur.bande} />
      <Fraicheur nom="Journal" etat={fraicheur.journal} />
    </div>
  )
}

const Outil = () => <Outlet context={{ health: null, routers: [] }} />

const monter = () =>
  render(
    <MemoryRouter initialEntries={['/e/mbx']}>
      <Routes>
        <Route element={<Outil />}>
          <Route path="/e/:routerId" element={<DeviceShell />}>
            <Route index element={<Espion />} />
          </Route>
        </Route>
      </Routes>
    </MemoryRouter>,
  )

const avancer = async (ms: number) => {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(openLiveSocket).mockImplementation(() => () => {})
  vi.mocked(fetchRouter).mockResolvedValue(summary())
  vi.mocked(fetchSessions).mockResolvedValue([])
  vi.mocked(fetchTimeline).mockResolvedValue(bande())
  vi.mocked(fetchEvents).mockResolvedValue({
    router_id: 'mbx', started_at: '', ended_at: '', capped: false, episodes: [], churn: [],
    omitted: 0, latency_threshold_ms: 1000,
  } as never)
})

afterEach(() => {
  vi.useRealTimers()
  vi.mocked(fetchRouter).mockReset()
  vi.mocked(fetchTimeline).mockReset()
  vi.mocked(openLiveSocket).mockReset()
})

describe('une relecture de la bande qui échoue', () => {
  it('garde la dernière bande valide et dit son âge', async () => {
    // `setTimeline(null)` effaçait la bande au premier hoquet, sans un mot :
    // l'écran perdait une bande valide pour une réponse manquée, et rien ne
    // disait pourquoi elle avait disparu.
    monter()
    await avancer(0)
    expect(screen.getByTestId('bande').textContent).toBe('présente')
    // Fraîche : rien à dire, pas de compteur qui tourne.
    expect(screen.queryByText(/Bande :/)).toBeNull()

    vi.mocked(fetchTimeline).mockRejectedValue(new Error('503 Service Unavailable'))
    await avancer(30_000)
    expect(screen.getByTestId('bande').textContent).toBe('présente')
    expect(screen.getByText(/Bande : dernière réponse il y a 30 s — dernière relecture en échec : 503/))
      .toBeInTheDocument()
    // Le journal, lui, répond : sa partie reste muette.
    expect(screen.queryByText(/Journal :/)).toBeNull()
  })

  it('se tait de nouveau dès qu’une relecture aboutit', async () => {
    vi.mocked(fetchTimeline).mockRejectedValueOnce(new Error('ECONNRESET'))
    monter()
    await avancer(0)
    // Aucune réponse encore : la mention le dit sans inventer d'âge.
    expect(screen.getByText(/Bande : aucune réponse obtenue — dernière relecture en échec : ECONNRESET/))
      .toBeInTheDocument()
    await avancer(30_000)
    expect(screen.queryByText(/Bande :/)).toBeNull()
  })

  it('signale une réponse qui n’arrive plus, sans échec déclaré', async () => {
    // Le serveur accepte la connexion puis ne répond plus : aucune promesse ne
    // rejette, et seule l'horloge peut dire que la bande vieillit.
    vi.mocked(fetchTimeline).mockResolvedValueOnce(bande())
      .mockImplementation(() => new Promise<Timeline>(() => {}))
    monter()
    await avancer(0)
    // Moins de deux périodes : ce n'est encore qu'une réponse lente.
    await avancer(55_000)
    expect(screen.queryByText(/Bande :/)).toBeNull()
    await avancer(10_000)
    expect(screen.getByText(/Bande : dernière réponse il y a 6\d s\./)).toBeInTheDocument()
  })

  it('efface pourtant une bande qui décrivait une AUTRE fenêtre', async () => {
    // Garder la dernière bande n'a de sens que pour la même fenêtre : celle de
    // la session précédente, posée sous le libellé de la nouvelle, mentirait.
    vi.mocked(fetchSessions).mockResolvedValue([session(1, '2026-09-11T08:00:00Z')])
    monter()
    await avancer(0)
    expect(screen.getByTestId('bande').textContent).toBe('présente')

    vi.mocked(fetchSessions).mockResolvedValue([
      session(2, '2026-09-11T13:00:00Z'), session(1, '2026-09-11T08:00:00Z'),
    ])
    vi.mocked(fetchTimeline).mockRejectedValue(new Error('ECONNRESET'))
    await avancer(30_000)
    expect(screen.getByTestId('bande').textContent).toBe('absente')
  })
})

describe('réessayer maintenant', () => {
  it('relit l’état de l’équipement aussitôt, une seule fois', async () => {
    monter()
    await avancer(0)
    vi.mocked(fetchRouter).mockRejectedValue(new Error('délai dépassé'))
    await avancer(10_000)
    expect(screen.getByText(/non rafraîchi/)).toBeInTheDocument()
    // L'âge de la dernière réponse est dit dans le bandeau même.
    expect(screen.getByText(/Dernière réponse il y a 10 s/)).toBeInTheDocument()

    // La relance part sans attendre la période, et reste en vol : un second
    // clic ne la double pas.
    let repondre: (v: RouterSummary) => void = () => {}
    vi.mocked(fetchRouter).mockImplementation(() => new Promise((r) => { repondre = r }))
    const avant = vi.mocked(fetchRouter).mock.calls.length
    await act(async () => { screen.getByRole('button', { name: 'Réessayer maintenant' }).click() })
    const bouton = screen.getByRole('button', { name: /Lecture en cours/ })
    expect(bouton).toBeDisabled()
    await act(async () => { bouton.click() })
    expect(vi.mocked(fetchRouter).mock.calls.length).toBe(avant + 1)

    await act(async () => { repondre(summary()) })
    expect(screen.queryByText(/non rafraîchi/)).toBeNull()
  })

  it('dit qu’une lecture est déjà en cours au lieu de ne rien faire', async () => {
    monter()
    await avancer(0)
    vi.mocked(fetchRouter).mockRejectedValue(new Error('délai dépassé'))
    await avancer(10_000)
    // La relecture périodique part, et reste en vol.
    let repondre: (v: RouterSummary) => void = () => {}
    vi.mocked(fetchRouter).mockImplementation(() => new Promise((r) => { repondre = r }))
    await avancer(10_000)
    const avant = vi.mocked(fetchRouter).mock.calls.length
    await act(async () => { screen.getByRole('button', { name: 'Réessayer maintenant' }).click() })
    // Le clic ne double pas la lecture, mais il le DIT.
    expect(vi.mocked(fetchRouter).mock.calls.length).toBe(avant)
    const bouton = screen.getByRole('button', { name: /Lecture déjà en cours/ })
    expect(bouton).toBeDisabled()
    await act(async () => { repondre(summary()) })
    expect(screen.queryByText(/non rafraîchi/)).toBeNull()
  })

  it('repousse la relecture périodique au lieu de la superposer', async () => {
    monter()
    await avancer(0)
    vi.mocked(fetchRouter).mockRejectedValue(new Error('délai dépassé'))
    await avancer(10_000)
    await avancer(8_000)
    const avant = vi.mocked(fetchRouter).mock.calls.length
    await act(async () => { screen.getByRole('button', { name: 'Réessayer maintenant' }).click() })
    await avancer(0)
    expect(vi.mocked(fetchRouter).mock.calls.length).toBe(avant + 1)
    // Deux secondes plus tard, l'ancienne échéance serait tombée : elle a été
    // repoussée d'une période entière.
    await avancer(5_000)
    expect(vi.mocked(fetchRouter).mock.calls.length).toBe(avant + 1)
    await avancer(5_000)
    expect(vi.mocked(fetchRouter).mock.calls.length).toBe(avant + 2)
  })

  it('relance un premier chargement en échec', async () => {
    vi.mocked(fetchRouter).mockRejectedValue(new Error('Service indisponible'))
    monter()
    await avancer(0)
    expect(screen.getByText(/Impossible de charger l’équipement/)).toBeInTheDocument()
    vi.mocked(fetchRouter).mockResolvedValue(summary())
    await act(async () => { screen.getByRole('button', { name: 'Réessayer maintenant' }).click() })
    await avancer(0)
    expect(screen.queryByText(/Impossible de charger l’équipement/)).toBeNull()
  })

  it('ne propose rien sur un identifiant qui ne désigne aucun équipement', async () => {
    // Un 404 est définitif : redemander ne changerait rien.
    vi.mocked(fetchRouter).mockRejectedValue(Object.assign(new Error('Not Found'), { status: 404 }))
    monter()
    await avancer(0)
    expect(screen.getByText(/Aucun équipement supervisé/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Réessayer maintenant' })).toBeNull()
  })

  it('rouvre le flux temps réel refermé', async () => {
    let fermer: ((c: { code: number; reason: string }) => void) | undefined
    vi.mocked(openLiveSocket).mockImplementation((_id, _s, _st, onClosed) => {
      fermer = onClosed as (c: { code: number; reason: string }) => void
      return () => {}
    })
    monter()
    await avancer(0)
    await act(async () => { fermer!({ code: 4000, reason: 'serveur en arrêt' }) })
    expect(screen.getByText(/Flux temps réel refermé/)).toBeInTheDocument()
    const avant = vi.mocked(openLiveSocket).mock.calls.length
    await act(async () => { screen.getByRole('button', { name: 'Réessayer maintenant' }).click() })
    expect(vi.mocked(openLiveSocket).mock.calls.length).toBe(avant + 1)
    expect(screen.queryByText(/Flux temps réel refermé/)).toBeNull()
  })
})
