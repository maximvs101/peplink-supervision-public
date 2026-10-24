import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./api/client', () => ({
  fetchRouters: vi.fn(),
  fetchHealth: vi.fn(),
  fetchRouter: vi.fn(),
  fetchSessions: vi.fn(),
  fetchTimeline: vi.fn(),
  fetchEvents: vi.fn(),
  fetchDevices: vi.fn(),
  fetchDiscovery: vi.fn(),
  fetchKnownDevices: vi.fn(),
  forgetDevice: vi.fn(),
  pairDevice: vi.fn(),
  reauthDevice: vi.fn(),
  httpStatus: () => null,
  openLiveSocket: vi.fn(() => () => {}),
}))

import App from './App'
import {
  fetchDevices, fetchDiscovery, fetchHealth, fetchKnownDevices, fetchRouter, fetchRouters,
  fetchSessions, fetchTimeline, fetchEvents, openLiveSocket,
} from './api/client'
import type { RouterSummary } from './types'

const mbx = (): RouterSummary => ({
  id: 'mbx', name: 'MBX', mode: 'router', site_lat: null, site_lon: null, wans: [], device: null,
  capabilities: null, status: 'connecté', error: null, age_seconds: 1, period_seconds: 5,
  cycle_seconds: 2, bond_routes: [], bond_profiles: [], lan_clients: null, latency_threshold_ms: 1000, tunnel_uptime_s: null, tunnel_attendu: null, never_connected: [], alerts: [],
})

beforeEach(() => {
  vi.useFakeTimers()
  window.location.hash = ''
  vi.mocked(fetchHealth).mockResolvedValue({
    status: 'ok', mode: 'router', routers: 0, poll_interval_seconds: 3,
    cycle_period_seconds: null, cycle_seconds: null,
  })
  vi.mocked(fetchDevices).mockResolvedValue([])
  vi.mocked(fetchDiscovery).mockResolvedValue([])
  vi.mocked(fetchKnownDevices).mockResolvedValue([])
  vi.mocked(fetchRouter).mockResolvedValue(mbx())
  vi.mocked(fetchSessions).mockResolvedValue([])
  vi.mocked(fetchTimeline).mockRejectedValue(new Error('hors sujet'))
  vi.mocked(fetchEvents).mockRejectedValue(new Error('hors sujet'))
  vi.mocked(openLiveSocket).mockImplementation(() => () => {})
})

afterEach(() => {
  vi.useRealTimers()
})

describe('une flotte vide', () => {
  it('mène à l’écran de découverte au lieu d’attendre pour toujours', async () => {
    // L'exécutable sans routers.yaml — le cas normal — affichait « Chargement
    // de la flotte… » à vie, avant le routeur : #/decouverte n'existait pas,
    // rien à appairer. Une liste vide est une réponse, pas une attente.
    vi.mocked(fetchRouters).mockResolvedValue([])
    render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(20) })
    expect(window.location.hash).toBe('#/decouverte')
    expect(screen.queryByText(/Chargement de la flotte/)).toBeNull()
  })

  it('attend tant que la flotte n’a pas été lue', async () => {
    let repondre: (liste: RouterSummary[]) => void = () => {}
    vi.mocked(fetchRouters).mockReturnValue(new Promise((r) => { repondre = r }))
    render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(20) })
    expect(screen.getByText(/Chargement de la flotte/)).toBeInTheDocument()
    await act(async () => { repondre([mbx()]); await vi.advanceTimersByTimeAsync(20) })
    expect(window.location.hash).toBe('#/e/mbx')
  })
})

describe('l’équipement ouvert à l’entrée', () => {
  it('est celui qui répond, pas le premier du fichier de flotte', async () => {
    // Vu le 30/09 : le MBX de routers.yaml, éteint, passait avant le HD2 qu'on
    // venait d'appairer et qui répondait — l'ouverture tombait sur « Équipement
    // injoignable », et l'équipement vivant était à chercher dans le sélecteur.
    const eteint = { ...mbx(), status: 'injoignable' as const, age_seconds: null }
    const vivant = { ...mbx(), id: 'hd2', name: 'HD2' }
    vi.mocked(fetchRouters).mockResolvedValue([eteint, vivant])
    render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(20) })
    expect(window.location.hash).toBe('#/e/hd2')
  })

  it('attend la première réponse au lieu d’ouvrir un équipement encore en attente', async () => {
    // Au démarrage du superviseur, tous les équipements sont « en attente » :
    // l'entrée retenait le premier du fichier de flotte — absent de ce réseau —
    // et y restait (`replace`), alors que le HD2 répondait au cycle suivant.
    const absent = { ...mbx(), status: 'en attente' as const, age_seconds: null }
    const hd2 = { ...mbx(), id: 'hd2', name: 'HD2', status: 'en attente' as const }
    vi.mocked(fetchRouters).mockResolvedValue([absent, hd2])
    render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(20) })
    expect(window.location.hash).toBe('')
    vi.mocked(fetchRouters).mockResolvedValue([
      { ...absent, status: 'injoignable' as const }, { ...hd2, status: 'connecté' as const },
    ])
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000) })
    expect(window.location.hash).toBe('#/e/hd2')
  })

  it('n’attend pas indéfiniment un équipement qui reste en attente', async () => {
    const a = { ...mbx(), status: 'en attente' as const }
    vi.mocked(fetchRouters).mockResolvedValue([a])
    render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(20) })
    expect(window.location.hash).toBe('')
    await act(async () => { await vi.advanceTimersByTimeAsync(31_000) })
    expect(window.location.hash).toBe('#/e/mbx')
  })

  it('reste le premier réel quand aucun ne répond', async () => {
    const a = { ...mbx(), status: 'injoignable' as const }
    const b = { ...mbx(), id: 'b', status: 'injoignable' as const }
    vi.mocked(fetchRouters).mockResolvedValue([a, b])
    render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(20) })
    expect(window.location.hash).toBe('#/e/mbx')
  })
})

describe('une panne du service', () => {
  it('se dit aussi à l’entrée, quand la flotte n’a jamais été lue', async () => {
    // Le bandeau ne vivait que sous le cadre : à `#/` avec le service
    // arrêté au premier chargement, « Chargement de la flotte… » à vie, sans
    // un mot — l'ancien écran affichait l'erreur.
    vi.mocked(fetchRouters).mockRejectedValue(new Error('ECONNREFUSED'))
    render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(20) })
    expect(screen.getByRole('alert').textContent).toMatch(/ne répond pas/)
    expect(screen.queryByText(/Chargement de la flotte/)).toBeNull()
    // Le service revient : l'entrée mène à l'équipement.
    vi.mocked(fetchRouters).mockResolvedValue([mbx()])
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000) })
    expect(window.location.hash).toBe('#/e/mbx')
  })

  it('se dit en bandeau sans démonter l’écran, et s’efface au retour', async () => {
    // Le message remplaçait tout l'arbre ; au cycle suivant qui réussissait,
    // tout était remonté à neuf — WebSocket, session, métrique perdues — pour
    // un hoquet. Et il disait « rechargez » pour un rétablissement automatique.
    vi.mocked(fetchRouters).mockResolvedValue([mbx()])
    render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(20) })
    expect(window.location.hash).toBe('#/e/mbx')
    const sockets = vi.mocked(openLiveSocket).mock.calls.length
    expect(sockets).toBeGreaterThan(0)

    vi.mocked(fetchRouters).mockRejectedValueOnce(new Error('ECONNREFUSED'))
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000) })
    expect(screen.getByRole('alert').textContent).toMatch(/ne répond pas/)
    expect(screen.getByRole('alert').textContent).not.toMatch(/rechargez/)
    // L'écran de l'équipement est toujours monté : aucune socket rouverte.
    expect(vi.mocked(openLiveSocket).mock.calls.length).toBe(sockets)

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000) })
    expect(screen.queryByRole('alert')).toBeNull()
    expect(vi.mocked(openLiveSocket).mock.calls.length).toBe(sockets)
  })

  it('ne prétend pas avoir lu quoi que ce soit sur la découverte', async () => {
    // `premiere={false}` codé en dur : sur `#/decouverte` sans aucune flotte
    // lue, le bandeau disait « ce qui est affiché date de la dernière réponse ».
    window.location.hash = '#/decouverte'
    vi.mocked(fetchRouters).mockRejectedValue(new Error('ECONNREFUSED'))
    render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(20) })
    const alerte = screen.getAllByRole('alert').find((a) => /ne répond pas/.test(a.textContent ?? ''))
    expect(alerte?.textContent).toMatch(/aucune réponse reçue/)
    expect(alerte?.textContent).not.toMatch(/dernière réponse/)
  })
})

describe('deux lectures de la flotte qui se doublent', () => {
  it('ne laissent pas une réponse lente écraser une plus récente', async () => {
    const dome = { ...mbx(), id: 'dome', name: 'DOME' }
    const br1 = { ...mbx(), id: 'br1', name: 'BR1' }
    vi.mocked(fetchRouters)
      .mockResolvedValueOnce([mbx(), dome])
      // Partie à 15 s, rendue à 35 s : après celle de 30 s.
      .mockImplementationOnce(() => new Promise((r) => setTimeout(() => r([mbx(), dome]), 20_000)))
      .mockResolvedValue([mbx(), dome, br1])
    render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(20) })
    expect(window.location.hash).toBe('#/e/mbx')
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    const options = () => [...(screen.getByRole('combobox', { name: /Équipement supervisé/ }) as HTMLSelectElement).options]
      .map((o) => o.value)
    expect(options()).toEqual(['mbx', 'dome', 'br1'])
    await act(async () => { await vi.advanceTimersByTimeAsync(6_000) })
    expect(options()).toEqual(['mbx', 'dome', 'br1'])
  })
})

describe('l’état de santé illisible', () => {
  it('se dit, au lieu de garder en silence la dernière période', async () => {
    vi.mocked(fetchRouters).mockResolvedValue([mbx()])
    vi.mocked(fetchHealth).mockRejectedValue(new Error('délai dépassé'))
    render(<App />)
    await act(async () => { await vi.advanceTimersByTimeAsync(20) })
    expect(screen.getByRole('status').textContent).toMatch(/État de santé du service illisible : délai dépassé/)
  })
})
