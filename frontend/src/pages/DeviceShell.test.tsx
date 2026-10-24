import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Outlet, Route, Routes, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  fetchRouter: vi.fn(),
  fetchSessions: vi.fn(),
  fetchTimeline: vi.fn(),
  fetchEvents: vi.fn(),
  httpStatus: (e: unknown) => (e instanceof Error ? ((e as Error & { status?: number }).status ?? null) : null),
  openLiveSocket: vi.fn(() => () => {}),
}))

import {
  fetchEvents, fetchRouter, fetchSessions, fetchTimeline, openLiveSocket,
} from '../api/client'
import type { RadioSample, RouterSummary, Timeline, TunnelLink, WanRadioStatus } from '../types'
import { DeviceShell, useDevice } from './DeviceShell'

const T0 = Date.parse('2026-09-11T14:00:00Z')

const sample = (wanId: string, cycle = 0, over: Partial<WanRadioStatus> = {}): RadioSample => ({
  router_id: 'mbx', router_name: 'MBX', ts: new Date(T0 + cycle * 5_300).toISOString(),
  site_lat: null, site_lon: null, cell_changes: 0, disconnects: 0,
  wan: {
    kind: 'cellular', wan_id: wanId, wan_name: `SIM ${wanId}`, connected: true,
    operator: 'Orange', operator_name: 'Orange', plmn: '208-01', rat: 'LTE', band: 'B3',
    earfcn: '1300', pci: null, cell_id: null, tac: null, enb_id: null, sector_id: null,
    rsrp: -88, rsrq: -11, sinr: 15, rssi: null, signal_bars: null,
    bands: [], sim_slots: [], wifi: null, path_latency_ms: null, path_latency_recent_ms: null, path_latency_at: null, modem_temperature_c: null, modem_temperature_at: null, ip: null, priority: 1, tunnel: null,
    tx_kbps: 300, rx_kbps: 1500, uptime_s: 3600, status_message: null, disabled: false,
    roaming_status: null, modem_vendor: null, modem_model: null, modem_firmware: null,
    mtu: null, carrier_aggregation: null, network_mode: null, allowance: null,
    period_rx_mb: null, period_tx_mb: null,
    ...over,
  } satisfies WanRadioStatus,
})

const summary = (wans: RadioSample[], over: Partial<RouterSummary> = {}): RouterSummary => ({
  id: 'mbx', name: 'MBX', mode: 'router', site_lat: null, site_lon: null,
  wans, device: null,
  capabilities: {
    gps: false, pepvpn: true, wan_types: {}, lan_repurposed: [], lan_networks: [],
    usb_modem_slots: 0, sim_slots: 2, per_wan_throughput: true, provisional: false, source: 'declared',
    sfc_licensed: null, sfc_suspended: null,
    sfc_throughput_limit_mbps: null, pepvpn_max_active_profiles: null,
  sfc_expires_at: null, sfc_quota_remaining_mb: null, sfc_quota_limit_mb: null,
  },
  status: 'connecté', error: null, age_seconds: 1, period_seconds: 5.3, cycle_seconds: 2.3, bond_routes: [], bond_profiles: [], lan_clients: null, latency_threshold_ms: 1000, tunnel_uptime_s: null, tunnel_attendu: null, never_connected: [], alerts: [],
  ...over,
})

/** Espion : rend le contexte que `DeviceShell` publie à ses écrans. */
function Espion() {
  const { samples, hasTunnel, periodSeconds } = useDevice()
  return (
    <div>
      <span data-testid="liens">{samples.map((s) => s.wan.wan_id).join(',')}</span>
      <span data-testid="tunnel">{String(hasTunnel)}</span>
      <span data-testid="periode">{String(periodSeconds)}</span>
      <span data-testid="rsrp">{String(samples[0]?.wan.rsrp ?? '')}</span>
      <span data-testid="age">{String(useDevice().ageSeconds)}</span>
      <span data-testid="sessions">{useDevice().sessions.map((s) => s.id).join(',')}</span>
      <span data-testid="session">{String(useDevice().session?.id ?? '')}</span>
    </div>
  )
}

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

/** `DeviceShell` lit l'état de santé et la flotte dans le contexte de son parent. */
const Outil = () => <Outlet context={{ health: null, routers: [] }} />

beforeEach(() => {
  vi.mocked(openLiveSocket).mockImplementation(() => () => {})
  // La bande de stationnement se relit à part : son échec ne doit jamais priver
  // l'écran de son état courant, et ces tests ne portent pas sur elle.
  vi.mocked(fetchSessions).mockResolvedValue([])
  vi.mocked(fetchTimeline).mockRejectedValue(new Error('hors sujet'))
  vi.mocked(fetchEvents).mockRejectedValue(new Error('hors sujet'))
})

afterEach(() => {
  vi.useRealTimers()
})

describe('premier cycle', () => {
  it('ne conclut pas sur un relevé partiel avant que le serveur ait publié les liens', async () => {
    // Vu sur un HD2 au redémarrage du serveur : « connecté », liste de liens
    // encore vide, puis le flux livre le WAN 1 seul (sans câble) — l'écran
    // titrait « Liaison rompue » alors que la SIM 5 portait le trafic.
    let pousser: ((s: RadioSample) => void) | undefined
    vi.mocked(openLiveSocket).mockImplementation((_id, onSample) => {
      pousser = onSample as (s: RadioSample) => void
      return () => {}
    })
    vi.useFakeTimers()
    vi.mocked(fetchRouter).mockResolvedValue(summary([], { age_seconds: null }))
    monter()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    await act(async () => {
      pousser!(sample('1', 0, { kind: 'ethernet', connected: false, status_message: 'No Cable Detected' }))
    })
    expect(screen.queryByText(/Liaison rompue/)).toBeNull()
    expect(screen.getByText('En attente du premier relevé')).toBeTruthy()
  })
})

describe('liste des liens', () => {
  it('oublie un WAN que le serveur ne liste plus', async () => {
    // Le serveur fait ce qu'il faut : `_prune_absent_wans` retire le WAN de son
    // état, l'omet de `/routers` et ne publie surtout pas l'adieu aux abonnés.
    // Mais `setSamples` n'était appelé qu'au montage et le flux ne sait que
    // fusionner : l'onglet ouvert gardait le dernier bon relevé — tuile
    // connectée, signal plein — pour un lien qui n'existait plus, et le bandeau
    // annonçait « 4 liens sur 4 connectés » quand il en restait trois.
    vi.useFakeTimers()
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4'), sample('7')]))
    monter()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByTestId('liens').textContent).toBe('4,7')

    // Le WAN 7 a été désactivé : le serveur ne le liste plus.
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4', 1)]))
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(screen.getByTestId('liens').textContent).toBe('4')
  })

  it('garde le relevé du flux quand il est plus récent que le résumé', async () => {
    // Le résumé fait autorité sur la LISTE, pas sur les VALEURS : il est lu
    // toutes les dix secondes là où le flux arrive à chaque cycle, et le laisser
    // écraser un relevé plus frais ferait reculer l'écran de deux cycles.
    let pousser: ((s: RadioSample) => void) | undefined
    vi.mocked(openLiveSocket).mockImplementation((_id, onSample) => {
      pousser = onSample as (s: RadioSample) => void
      return () => {}
    })
    vi.useFakeTimers()
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4', 0, { rsrp: -88 })]))
    monter()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    // Un relevé du cycle suivant arrive par le flux…
    await act(async () => { pousser!(sample('4', 5, { rsrp: -101 })) })
    // …puis le rafraîchissement périodique rend un résumé plus ANCIEN.
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(screen.getByTestId('liens').textContent).toBe('4')
    expect(screen.getByTestId('rsrp').textContent).toBe('-101')
  })
})

describe('grandeurs du tunnel', () => {
  it('ne les propose pas sur un châssis SANS profil configuré', async () => {
    // `capabilities.pepvpn` dit « ce châssis SAIT faire du SpeedFusion », ce qui
    // reste vrai sans aucun profil : `metricsFor` ajoutait alors « Latence » et
    // « Perte », et l'historique rendait des points datés à valeur nulle — une
    // courbe vide sur un axe gradué 0-100, le défaut même qu'il devait corriger.
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4')]))
    monter()
    await waitFor(() => expect(screen.getByTestId('tunnel').textContent).toBe('false'))
  })

  it('les propose dès qu’un lien porte un objet tunnel', async () => {
    const tunnel: TunnelLink = {
      state: 'ACTIVE', in_bond: true, rtt_ms: 30,
      loss_percent: null, out_of_order_percent: null,
      loss_percent_up: null, loss_percent_down: null,
      out_of_order_percent_up: null, out_of_order_percent_down: null,
      packets_up: null, packets_down: null, tx_kbps: 250, rx_kbps: 1400,
      repair_sent: null, repair_used: null,
      cutoff_ms: null, beyond_cutoff: false, peer_state: null,
      cause_scope: null,
    }
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4', 0, { tunnel })]))
    monter()
    await waitFor(() => expect(screen.getByTestId('tunnel').textContent).toBe('true'))
  })
})

describe('la cause d’un tunnel à terre (R-1.5)', () => {
  it('arrive jusqu’au bandeau, lue sur les profils du routeur', async () => {
    const tunnel: TunnelLink = {
      state: 'PEER_DOWN', in_bond: false, rtt_ms: null,
      loss_percent: null, out_of_order_percent: null,
      loss_percent_up: null, loss_percent_down: null,
      out_of_order_percent_up: null, out_of_order_percent_down: null,
      packets_up: null, packets_down: null, tx_kbps: null, rx_kbps: null,
      repair_sent: null, repair_used: null,
      cutoff_ms: null, beyond_cutoff: false, peer_state: 'START',
      cause_scope: 'pair',
    }
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4', 0, { tunnel })], {
      tunnel_attendu: false,
      bond_profiles: [{ id: '4', name: 'PROFIL-B', status: 'START', peers_connected: 0, cloud: false }],
    }))
    monter()
    expect(await screen.findByText(
      /jamais établi dans cette session \(profil PROFIL-B : tentative en cours \(START\), 0 pair connecté\)/,
    )).toBeInTheDocument()
  })
})

describe('seuil de péremption', () => {
  it('suit la période de CET équipement, pas celle de la flotte', async () => {
    // Chaque équipement a sa propre boucle de scrutation : la vue de flotte
    // retient le pire, et un équipement mort relâchait ainsi le seuil de tous
    // ses voisins sains de toute la durée de son budget.
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4')], { period_seconds: 5.3 }))
    monter()
    await waitFor(() => expect(screen.getByTestId('periode').textContent).toBe('5.3'))
  })
})

describe('deux rafraîchissements qui se doublent', () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }) })
  afterEach(() => { vi.useRealTimers() })

  it('ne fait pas rajeunir l’âge d’une mesure périmée', async () => {
    // Une requête lente partie AVANT une rapide arrivait après elle, et l'écran
    // estampillait l'instant de réception sur le résumé arrivé en dernier, quel
    // que soit son ordre de départ : l'âge passait de quatre-vingt-dix secondes
    // à deux, et le bandeau repassait au vert sur une mesure périmée.
    const lente = summary([sample('4')], { age_seconds: 90 })
    const rapide = summary([sample('4')], { age_seconds: 2 })
    let resoudreLente: (v: RouterSummary) => void = () => {}
    vi.mocked(fetchRouter)
      .mockImplementationOnce(() => new Promise<RouterSummary>((r) => { resoudreLente = r }))
      .mockResolvedValue(rapide)

    monter()
    // La lente part la première et n'a pas répondu ; la rapide la double.
    await act(async () => { vi.advanceTimersByTime(10_000) })
    await waitFor(() => expect(screen.getByTestId('age').textContent).toBe('2'))

    // La lente arrive enfin. Elle décrit un instant que l'écran a dépassé.
    await act(async () => { resoudreLente(lente) })
    expect(screen.getByTestId('age').textContent).toBe('2')
  })
})

describe('la fenêtre du stationnement', () => {
  it('commence au premier relevé, pas à la mise sous tension', async () => {
    // Sur le MAX HD2 du 19 septembre, l'équipement était sous tension depuis
    // 08:35 et le superviseur lancé à 10:53. La bande et le journal se
    // demandaient « depuis 08:35 » : deux heures dix-huit de vide en tête
    // d'axe — quarante pour cent de sa largeur — pour une absence qui n'est
    // pas une mesure. La borne gauche est le premier relevé rattaché.
    vi.useFakeTimers()
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4')]))
    vi.mocked(fetchSessions).mockResolvedValue([{
      id: 6, router_id: 'mbx', started_at: '2026-09-19T08:35:41Z',
      first_seen: '2026-09-19T10:53:25Z', last_seen: '2026-09-19T13:45:33Z',
      samples: 5322, label: null,
    }])
    monter()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(fetchTimeline).toHaveBeenCalledWith('mbx', { since: '2026-09-19T10:53:25Z' })
    expect(fetchEvents).toHaveBeenCalledWith('mbx', { since: '2026-09-19T10:53:25Z' })
  })

  it('retombe sur la mise sous tension tant qu’aucun relevé n’est rattaché', async () => {
    vi.useFakeTimers()
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4')]))
    vi.mocked(fetchSessions).mockResolvedValue([{
      id: 7, router_id: 'mbx', started_at: '2026-09-19T08:35:41Z',
      first_seen: null, last_seen: '2026-09-19T08:35:41Z', samples: 0, label: null,
    }])
    monter()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(fetchTimeline).toHaveBeenCalledWith('mbx', { since: '2026-09-19T08:35:41Z' })
  })
})

describe('un échec de /sessions', () => {
  it('garde la dernière liste lue au lieu de la vider', async () => {
    // Le `catch` posait `[]` : la session choisie disparaissait du sélecteur,
    // la courbe repassait en fenêtre glissante pendant que la fiche restait, et
    // le pied écrivait « session du Invalid Date ». Un hoquet n'est pas une
    // liste vide.
    vi.useFakeTimers()
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4')]))
    const liste = [{
      id: 6, router_id: 'mbx', started_at: '2026-09-19T08:35:41Z',
      first_seen: '2026-09-19T10:53:25Z', last_seen: '2026-09-19T13:45:33Z',
      samples: 5322, label: null,
    }]
    vi.mocked(fetchSessions).mockResolvedValue(liste)
    vi.mocked(fetchTimeline).mockClear()
    monter()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByTestId('sessions').textContent).toBe('6')
    expect(screen.getByTestId('session').textContent).toBe('6')

    vi.mocked(fetchSessions).mockRejectedValueOnce(new Error('ECONNRESET'))
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(screen.getByTestId('sessions').textContent).toBe('6')
    expect(screen.getByTestId('session').textContent).toBe('6')
    // Et la bande a bien été redemandée sur la MÊME borne, pas sur trois heures.
    const fenetres = vi.mocked(fetchTimeline).mock.calls.map((c) => c[1])
    expect(fenetres.length).toBeGreaterThanOrEqual(2)
    expect(fenetres.every((f) => f.since === '2026-09-19T10:53:25Z')).toBe(true)
  })
})

describe('un écran qui tombe', () => {
  it('ne démonte ni le bandeau ni la navigation', async () => {
    // Seul le module d'historique avait un filet. Partout ailleurs, une erreur
    // de rendu remontait jusqu'à la racine : la page noire du 19 septembre
    // était une classe, pas un cas.
    vi.useFakeTimers()
    const silence = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4')]))
    const Casse = () => { throw new Error('champ absent') }
    render(
      <MemoryRouter initialEntries={['/e/mbx']}>
        <Routes>
          <Route element={<Outil />}>
            <Route path="/e/:routerId" element={<DeviceShell />}>
              <Route index element={<Casse />} />
            </Route>
          </Route>
        </Routes>
      </MemoryRouter>,
    )
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    // Le bandeau est là : il porte le verdict.
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument()
    // Et l'écran dit ce qui lui est arrivé, sans mot technique inventé.
    expect(screen.getByRole('alert').textContent).toMatch(/Écran non affiché/)
    expect(screen.getByRole('alert').textContent).toMatch(/champ absent/)
    silence.mockRestore()
  })
})

describe('un équipement retiré', () => {
  it('cesse aussi de relire la bande et le journal', async () => {
    // `stopped` n'existait que dans l'effet du flux principal : après le 404,
    // sessions, bande et journal repartaient toutes les trente secondes — trois
    // requêtes sur un équipement qui n'existe plus, tant que l'onglet vivait.
    vi.useFakeTimers()
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4')]))
    monter()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const retire = Object.assign(new Error('Not Found'), { status: 404 })
    vi.mocked(fetchRouter).mockRejectedValue(retire)
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000) })
    expect(screen.getByText(/Équipement retiré de la supervision/)).toBeInTheDocument()

    const avant = vi.mocked(fetchSessions).mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(90_000) })
    expect(vi.mocked(fetchSessions).mock.calls.length).toBe(avant)
  })
})

describe('un premier chargement en échec', () => {
  it('se lève dès qu’un résumé arrive', async () => {
    // Le rafraîchissement continuait après un premier échec non-404, mais
    // `store` ne remettait jamais `fatal` à null : l'écran restait sur
    // « Impossible de charger l'équipement » alors que le service était revenu.
    vi.useFakeTimers()
    vi.mocked(fetchRouter).mockRejectedValue(new Error('Service indisponible'))
    monter()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByText(/Impossible de charger l’équipement/)).toBeInTheDocument()

    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4')]))
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(screen.queryByText(/Impossible de charger l’équipement/)).toBeNull()
    expect(screen.getByTestId('liens').textContent).toBe('4')
  })
})

describe('passer d’un identifiant inconnu à un équipement supervisé', () => {
  it('ne garde ni le verdict « inconnu » ni l’arrêt des relectures', async () => {
    // `retire` et `inconnu` n'étaient jamais remis à zéro au changement
    // d'équipement : après un 404, l'équipement suivant restait « inconnu »
    // et sa bande, son journal et ses sessions n'étaient plus jamais relus.
    vi.useFakeTimers()
    const absent = Object.assign(new Error('Not Found'), { status: 404 })
    vi.mocked(fetchRouter).mockImplementation((id: string) =>
      id === 'inconnu' ? Promise.reject(absent) : Promise.resolve(summary([sample('4')])))
    const Aller = () => {
      const navigate = useNavigate()
      return <><button onClick={() => navigate('/e/mbx')}>aller</button><Outlet context={{ health: null, routers: [] }} /></>
    }
    render(
      <MemoryRouter initialEntries={['/e/inconnu']}>
        <Routes>
          <Route element={<Aller />}>
            <Route path="/e/:routerId" element={<DeviceShell />}>
              <Route index element={<Espion />} />
            </Route>
          </Route>
        </Routes>
      </MemoryRouter>,
    )
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByText(/Aucun équipement supervisé/)).toBeInTheDocument()
    const avant = vi.mocked(fetchSessions).mock.calls.length
    await act(async () => { screen.getByRole('button', { name: 'aller' }).click() })
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.queryByText(/Aucun équipement supervisé/)).toBeNull()
    expect(screen.getByRole('heading', { level: 1 }).textContent).not.toMatch(/inconnu/i)
    expect(vi.mocked(fetchSessions).mock.calls.length).toBeGreaterThan(avant)
  })
})

describe('les avis de l’écran', () => {
  it('sont annoncés aux technologies d’assistance', async () => {
    let fermer: ((c: { code: number; reason: string }) => void) | undefined
    vi.mocked(openLiveSocket).mockImplementation((_id, _s, _st, onClosed) => {
      fermer = onClosed as (c: { code: number; reason: string }) => void
      return () => {}
    })
    vi.useFakeTimers()
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4')]))
    monter()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    vi.mocked(fetchRouter).mockRejectedValue(new Error('délai dépassé'))
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    await act(async () => { fermer!({ code: 4404, reason: 'équipement inconnu' }) })
    const alertes = screen.getAllByRole('alert').map((a) => a.textContent)
    expect(alertes.some((t) => /non rafraîchi/.test(t ?? ''))).toBe(true)
    expect(alertes.some((t) => /Flux temps réel refermé/.test(t ?? ''))).toBe(true)
  })
})

describe('un identifiant qui ne désigne rien', () => {
  it('ne prétend ni attendre un relevé ni avoir vu un retrait', async () => {
    // `#/e/inconnu` : le bandeau disait « En attente du premier relevé » et
    // l'écran « retiré depuis un autre onglet » — une attente et une cause
    // inventées.
    vi.useFakeTimers()
    const absent = Object.assign(new Error('Not Found'), { status: 404 })
    vi.mocked(fetchRouter).mockRejectedValue(absent)
    render(
      <MemoryRouter initialEntries={['/e/eq-0123456789ab']}>
        <Routes>
          <Route element={<Outlet context={{ health: null, routers: [summary([]), { ...summary([]), id: 'dome', name: 'DOME' }] }} />}>
            <Route path="/e/:routerId" element={<DeviceShell />}>
              <Route index element={<Espion />} />
            </Route>
          </Route>
        </Routes>
      </MemoryRouter>,
    )
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByRole('heading', { level: 1 }).textContent).toMatch(/inconnu/i)
    expect(screen.queryByText(/En attente du premier relevé/)).toBeNull()
    expect(screen.getByText(/Aucun équipement supervisé sous cet identifiant/)).toBeInTheDocument()
    // L'identifiant est une empreinte opaque : le répéter n'apprend rien à
    // l'exploitant, ni dans le message, ni dans le bandeau, ni dans le sélecteur.
    expect(document.body.textContent).not.toContain('eq-0123456789ab')
    expect(screen.queryByText(/autre onglet/)).toBeNull()
    // Le sélecteur ne montre pas le premier équipement à la place.
    const select = screen.getByRole('combobox', { name: /Équipement supervisé/ }) as HTMLSelectElement
    expect(select.value).toBe('eq-0123456789ab')
  })
})

describe('le journal suit les changements d’état', () => {
  it('se relit dès qu’un lien tombe ou revient, sans attendre trente secondes', async () => {
    // Scénarios 1 et 2 de la revue : la table montrait SIM 3 revenue pendant
    // que le journal affichait sa coupure « en cours » — jusqu'à 30 s.
    let pousser: ((s: RadioSample) => void) | undefined
    vi.mocked(openLiveSocket).mockImplementation((_id, onSample) => {
      pousser = onSample as (s: RadioSample) => void
      return () => {}
    })
    vi.useFakeTimers()
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('3', 0)]))
    vi.mocked(fetchEvents).mockClear()
    monter()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const avant = vi.mocked(fetchEvents).mock.calls.length

    // Un relevé sans changement d'état ne relance rien.
    await act(async () => { pousser!(sample('3', 1)) })
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000) })
    expect(vi.mocked(fetchEvents).mock.calls.length).toBe(avant)

    // Le lien tombe : le journal est relu tout de suite.
    await act(async () => { pousser!(sample('3', 2, { connected: false })) })
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000) })
    expect(vi.mocked(fetchEvents).mock.calls.length).toBe(avant + 1)

    // Il revient : relu de nouveau.
    await act(async () => { pousser!(sample('3', 3, { connected: true })) })
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000) })
    expect(vi.mocked(fetchEvents).mock.calls.length).toBe(avant + 2)
  })
})

describe('le rôle du bond quand le débit du tunnel s’éteint (C-2)', () => {
  const Role = () => <span data-testid="role">{JSON.stringify(useDevice().role)}</span>
  const monterRole = () =>
    render(
      <MemoryRouter initialEntries={['/e/mbx']}>
        <Routes>
          <Route element={<Outil />}>
            <Route path="/e/:routerId" element={<DeviceShell />}>
              <Route index element={<Role />} />
            </Route>
          </Route>
        </Routes>
      </MemoryRouter>,
    )
  const ROUTES = ['10.1.0.0/16']
  const bande = (packets: number | null): Timeline => ({
    router_id: 'mbx', started_at: '2026-09-11T13:00:00Z', ended_at: '2026-09-11T14:00:00Z',
    capped: false, slice_count: 10, slice_seconds: 60,
    lanes: [{ wan_id: '4', slices: Array.from({ length: 10 }, (_, t) => ({
      t, samples: 10, up: 10, carrying: 0, tunnel_known: 10, sinr_worst: 12, rsrp_worst: -88,
      repair: null, packets, disabled: 0,
    })) }],
  })

  it('ne nie pas que le bond porte faute de bande relue', async () => {
    // Timeline absente (échec de lecture, ou page tout juste chargée) : le rôle
    // tombait sur `routes`, et le bandeau disait « Liaison OK » tunnel à terre.
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4', 0)], { bond_routes: ROUTES }))
    monterRole()
    await waitFor(() => expect(screen.getByTestId('role').textContent)
      .toBe(JSON.stringify({ kind: 'inconnu', routes: ROUTES })))
  })

  it('garde le rôle mesuré tunnel debout quand la bande ne mesure plus rien', async () => {
    vi.useFakeTimers()
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4', 0)], { bond_routes: ROUTES }))
    // 7 380 paquets par tranche de 60 s : 123 paquets/s, le bond porte.
    vi.mocked(fetchTimeline).mockResolvedValue(bande(7_380))
    monterRole()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByTestId('role').textContent).toBe(JSON.stringify({ kind: 'porte', routes: ROUTES }))

    // Le concentrateur tombe : plus aucun compteur dans la fenêtre du témoin.
    vi.mocked(fetchTimeline).mockResolvedValue(bande(null))
    // BAND_MS : la bande se relit toutes les trente secondes.
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(vi.mocked(fetchTimeline).mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(screen.getByTestId('role').textContent).toBe(JSON.stringify({ kind: 'porte', routes: ROUTES }))
  })
})

/**
 * C-31 : le journal coûte plus d'une seconde de SQL, relu toutes les trente
 * secondes — y compris pour un onglet que personne ne regarde, le cas courant
 * d'un stationnement de plusieurs heures. Onglet caché, la relecture attend ;
 * elle reprend dès qu'il revient au premier plan.
 */
describe('la bande et le journal ne se relisent pas pour personne', () => {
  const visibilite = (etat: 'visible' | 'hidden') => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => etat })
    document.dispatchEvent(new Event('visibilitychange'))
  }
  afterEach(() => visibilite('visible'))

  it('suspend la relecture onglet caché, et la reprend au retour', async () => {
    vi.useFakeTimers()
    vi.mocked(fetchRouter).mockResolvedValue(summary([sample('4')]))
    vi.mocked(fetchEvents).mockClear()
    monter()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(fetchEvents).toHaveBeenCalledTimes(1)

    await act(async () => { visibilite('hidden') })
    await act(async () => { await vi.advanceTimersByTimeAsync(5 * 30_000) })
    expect(fetchEvents).toHaveBeenCalledTimes(1)

    await act(async () => { visibilite('visible') })
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(fetchEvents).toHaveBeenCalledTimes(2)

    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(fetchEvents).toHaveBeenCalledTimes(3)
  })
})
