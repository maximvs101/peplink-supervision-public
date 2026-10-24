import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// jsdom n'implémente pas `ResizeObserver`, dont dépend `useElementSize`. Le
// graphe lui-même ne se dessine donc pas ici — ce qui est vérifié est ce qui
// l'entoure : la mention des mesures hors cadre et la fiche de session.
class FauxObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', FauxObserver)

vi.mock('../api/client', () => ({
  fetchHistory: vi.fn(),
  nameSession: vi.fn(),
  fetchSessionSummary: vi.fn(),
  fetchEvents: vi.fn(),
}))

import { fetchEvents, fetchHistory, fetchSessionSummary, nameSession } from '../api/client'
import type { HistorySeries, Session, SessionSummary } from '../types'
import { LOSS_MIN_PACKETS } from '../lib/verdict'
import { RESUME_MS } from '../lib/session'
import { HistoryChart } from './HistoryChart'

const T0 = Date.parse('2026-08-30T14:00:00Z')

/** La série réelle du 30 août : trente minutes entre 25 et 35 ms, plus un pic
 *  isolé à 1 929 ms. C'est le relevé le plus instructif de toute la base. */
const serie = (): HistorySeries => ({
  router_id: 'mbx',
  wan_id: '4',
  metric: 'rtt_ms',
  points: Array.from({ length: 340 }, (_, i) => ({
    ts: new Date(T0 + i * 5_300).toISOString(),
    value: i === 170 ? 1929 : 25 + (i % 11),
  })),
  available: 340,
})

const session = (over: Partial<Session> = {}): Session => ({
  id: 1, router_id: 'mbx', started_at: '2026-08-30T13:00:00Z', first_seen: '2026-08-30T13:00:05Z',
  last_seen: '2026-08-30T16:00:00Z', samples: 2000, label: null, ...over,
})

const fiche = (over: Partial<SessionSummary> = {}): SessionSummary => ({
  session: session(),
  cells: [], availability: null, diversity: null,
  links: [{
    wan_id: '4', kind: 'cellular', operator: 'Orange', operators: ['Orange'], maneuvers: 0, samples: 720,
    mb_up: null, mb_down: null,
    rtt_p50_ms: null, rtt_p90_ms: null, rtt_max_ms: null, rsrp_median: -88,
    path_latency_samples: 0, path_latency_p50_ms: null, path_latency_p90_ms: null, path_latency_max_ms: null,
    rsrp_filled_samples: 0,
    outages: 0, cells: 1, cell_changes: 0, cell_changes_per_hour: null,
  }],
  mb_up: null, mb_down: null, lat: null, lon: null, radius_m: null, sites: 1,
  partial: false, window_start: null, window_end: null,
  ...over,
})

beforeEach(() => {
  vi.mocked(fetchHistory).mockResolvedValue(serie())
  vi.mocked(fetchSessionSummary).mockClear()
  vi.mocked(fetchSessionSummary).mockResolvedValue(fiche())
})

// Sous un routeur : grandeur, fenêtre et session se lisent dans l'adresse.
const chart = (props: Partial<Parameters<typeof HistoryChart>[0]> = {}) => (
  <MemoryRouter>
    <HistoryChart routerId="mbx" wanId="4" wanName="SIM 4" kind="cellular" hasTunnel {...props} />
  </MemoryRouter>
)

describe('cadre vertical', () => {
  it('signale les mesures laissées hors du cadre', async () => {
    // `framedDomain` rognait l'axe pour protéger la bande utile, et
    // `outOfFrameNote` n'avait AUCUN appelant : le pic disparaissait du graphe
    // sans qu'un mot le dise. « Signaler, jamais masquer » n'était pas tenu.
    render(chart())
    // La latence est la dernière métrique ; la première d'un lien cellulaire est
    // le SINR. On passe donc par la série de latence en la rendant par défaut.
    await waitFor(() => expect(fetchHistory).toHaveBeenCalled())
    screen.getByRole('button', { name: 'Latence du bond' }).click()
    await waitFor(() =>
      expect(screen.getByText(/au-dessus du cadre, jusqu’à 1 929/)).toBeInTheDocument(),
    )
  })
})

describe('règle des 200 paquets sur la courbe', () => {
  it('retire de la courbe un taux calculé sur trop peu de paquets', async () => {
    // Le défaut mesuré sur le matériel : l'unique point de perte non nulle des
    // trois dernières heures valait 33,33 % — obtenu sur UN paquet manquant, sur
    // un lien parfaitement sain. La tuile et la table refusaient déjà de le
    // publier ; la courbe le traçait comme un incident, et l'axe se graduait
    // jusqu'à 33 pour lui.
    vi.mocked(fetchHistory).mockResolvedValue({
      router_id: 'mbx', wan_id: '4', metric: 'loss_percent',
      points: [
        { ts: new Date(T0).toISOString(), value: 0, denominator: 12000 },
        { ts: new Date(T0 + 5300).toISOString(), value: 33.33, denominator: 1 },
        { ts: new Date(T0 + 10600).toISOString(), value: 0, denominator: 12000 },
      ],
      available: 3,
    })
    render(chart())
    await waitFor(() => expect(fetchHistory).toHaveBeenCalled())
    // La règle des deux cents paquets ne concerne QUE les taux : il faut donc
    // sélectionner « Perte ». Le test assertait jusqu'ici sur la série affichée
    // par défaut — le SINR — parce que le filtre s'appliquait indistinctement à
    // toutes les grandeurs, y compris à celles qui n'ont pas de dénominateur.
    screen.getByRole('button', { name: 'Perte' }).click()
    await waitFor(() =>
      expect(screen.getByText(/Un point a été retiré de la courbe/)).toBeInTheDocument(),
    )
    expect(screen.getByText(/paquets comptés sur l’intervalle/)).toBeInTheDocument()
  })

  it('retire aussi un taux dont on ignore le dénominateur (C-28, F4)', async () => {
    // Huit mille relevés de la base portent un taux sans aucun compteur. Le
    // graphe le traitait comme « publiable sans condition » ; la mutation qui
    // le rétablit survivait, faute de test.
    vi.mocked(fetchHistory).mockResolvedValue({
      router_id: 'mbx', wan_id: '4', metric: 'loss_percent',
      points: [
        { ts: new Date(T0).toISOString(), value: 0, denominator: 12000 },
        { ts: new Date(T0 + 5300).toISOString(), value: 4.2, denominator: null },
      ],
      available: 2,
    })
    render(chart())
    await waitFor(() => expect(fetchHistory).toHaveBeenCalled())
    screen.getByRole('button', { name: 'Perte' }).click()
    await waitFor(() =>
      expect(screen.getByText(/Un point a été retiré de la courbe/)).toBeInTheDocument(),
    )
  })

  it('envoie sa règle au serveur avec la perte, et pas avec un niveau radio', async () => {
    // L'enveloppe du serveur élisait le pic AVANT la règle : le 33,33 % sur 3
    // paquets — jeté ici — éclipsait le 4,7 % réel sur 300, disparu sans
    // mention. La règle reste celle de l'écran ; le serveur l'exécute en amont.
    render(chart())
    await waitFor(() => expect(fetchHistory).toHaveBeenCalled())
    const dernier = () => { const c = vi.mocked(fetchHistory).mock.calls; return c[c.length - 1] }
    expect(dernier()[2]).toBe('sinr')
    expect(dernier()[5]).toBeUndefined()
    screen.getByRole('button', { name: 'Perte' }).click()
    await waitFor(() => expect(dernier()[2]).toBe('loss_percent'))
    expect(dernier()[5]).toBe(LOSS_MIN_PACKETS)
  })

  it('garde un taux qui pèse, et ne dit rien alors', async () => {
    // Contre-épreuve : sans elle, l'assertion ci-dessus passerait même si le
    // composant retirait TOUS les points.
    vi.mocked(fetchHistory).mockResolvedValue({
      router_id: 'mbx', wan_id: '4', metric: 'loss_percent',
      points: [
        { ts: new Date(T0).toISOString(), value: 4.2, denominator: 105000 },
        { ts: new Date(T0 + 5300).toISOString(), value: 0, denominator: 98000 },
      ],
      available: 2,
    })
    render(chart())
    await waitFor(() => expect(fetchHistory).toHaveBeenCalled())
    expect(screen.queryByText(/retiré de la courbe/)).not.toBeInTheDocument()
  })

  it('ne pondère pas une grandeur qui n’est pas un taux', async () => {
    // Un niveau radio n'a pas de dénominateur : lui en inventer un laisserait
    // croire qu'il se pondère.
    vi.mocked(fetchHistory).mockResolvedValue({
      router_id: 'mbx', wan_id: '4', metric: 'sinr',
      points: [
        { ts: new Date(T0).toISOString(), value: 12, denominator: null },
        { ts: new Date(T0 + 5300).toISOString(), value: 3, denominator: null },
      ],
      available: 2,
    })
    render(chart())
    await waitFor(() => expect(fetchHistory).toHaveBeenCalled())
    expect(screen.queryByText(/retiré de la courbe/)).not.toBeInTheDocument()
  })
})

describe('fiche de session', () => {
  it('n’écrit pas zéro à la place d’un volume non mesuré', async () => {
    // Le Dome Pro Duo ne publie aucun débit par WAN : la fiche affichait
    // « 0.0 Mo montants · 0.0 Mo descendants » en gras sur sept cent vingt
    // relevés où rien n'avait jamais été mesuré.
    // La liste des sessions vient de `DeviceShell`, en prop : ce composant ne
    // la relit plus de son côté.
    const { container } = render(chart({ sessions: [session()] }))

    const select = container.querySelector('select') as HTMLSelectElement
    select.value = '1'
    select.dispatchEvent(new Event('change', { bubbles: true }))

    await waitFor(() =>
      expect(screen.getByText('Volumes non mesurés sur cet équipement')).toBeInTheDocument(),
    )
    expect(screen.queryByText(/0\.0 Mo montants/)).not.toBeInTheDocument()
  })

  it('résume la latence du lien à côté de celle du bond, avec son compte (R-3.3)', async () => {
    const base = fiche().links![0]
    vi.mocked(fetchSessionSummary).mockResolvedValue(fiche({ links: [{
      ...base, path_latency_samples: 179,
      path_latency_p50_ms: 28, path_latency_p90_ms: 33, path_latency_max_ms: 61,
    }] }))
    const { container } = render(chart({ sessions: [session()] }))
    const select = container.querySelector('select') as HTMLSelectElement
    select.value = '1'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await waitFor(() => expect(screen.getByText('Lien médian')).toBeInTheDocument())
    const table = [...container.querySelectorAll('table')].find((t) => t.textContent?.includes('Lien médian'))!
    const entetes = [...table.querySelectorAll('thead th')].map((th) => th.textContent)
    const cellules = [...table.querySelectorAll('tbody tr td')].map((td) => td.textContent)
    const valeur = (nom: string) => cellules[entetes.indexOf(nom)]
    // Le compte s'écrit à côté de la médiane : un `title` ne s'imprime pas,
    // et c'est là que ce test le cherchait (C-12).
    // Sans tunnel mesuré, la colonne du bond n'existe pas (A-7) : trois « — »
    // ne disaient rien et serraient le reste.
    expect(entetes).not.toContain('Bond médian')
    expect([valeur('Lien médian'), valeur('Lien p90'), valeur('Lien max')])
      .toEqual(['28 ms sur 179', '33 ms', '61 ms'])
  })

  it('dit ce que la médiane de RSRP doit à l’historique du routeur (R-3.1)', async () => {
    const base = fiche().links![0]
    vi.mocked(fetchSessionSummary).mockResolvedValue(fiche({ links: [{ ...base, rsrp_filled_samples: 25 }] }))
    const { container } = render(chart({ sessions: [session()] }))
    const select = container.querySelector('select') as HTMLSelectElement
    select.value = '1'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    // 25 points à 10 s : 250 s, soit « 4 min ».
    const note = await screen.findByText(/dont 4 min comblées/)
    expect(note.getAttribute('title')).toBe('25 points de l’historique du routeur, pris dans les trous de supervision')
  })

  it('ne dit rien quand la médiane ne doit rien au routeur', async () => {
    const { container } = render(chart({ sessions: [session()] }))
    const select = container.querySelector('select') as HTMLSelectElement
    select.value = '1'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await screen.findByText('Lien médian')
    expect(screen.queryByText(/comblées/)).not.toBeInTheDocument()
  })

  it('mène au rapport imprimable de la session (R-3.7)', async () => {
    const { container } = render(chart({ sessions: [session()] }))
    const select = container.querySelector('select') as HTMLSelectElement
    select.value = '1'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    const lien = await screen.findByRole('link', { name: /Rapport/ })
    expect(lien.getAttribute('href')).toBe('#/rapport/1')
  })

  it('donne un en-tête à chaque colonne de la table des liens (R-3.8)', async () => {
    const { container } = render(chart({ sessions: [session()] }))
    const select = container.querySelector('select') as HTMLSelectElement
    select.value = '1'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await waitFor(() => expect(screen.getByText('Lien médian')).toBeInTheDocument())
    const table = [...container.querySelectorAll('table')].find((t) => t.textContent?.includes('Lien médian'))!
    const entetes = [...table.querySelectorAll('thead th')].map((th) => th.textContent)
    expect(entetes).toHaveLength(table.querySelector('tbody tr')!.children.length)
    expect(entetes[entetes.length - 1]).toBe('Sauts de cellule / h')
  })

  it('déclare la fenêtre quand la session n’a pas été relue en entier', async () => {
    // Volumes, coupures et barycentre sont des TOTAUX : une fiche muette sur sa
    // fenêtre se lit comme une fiche complète.
    vi.mocked(fetchSessionSummary).mockResolvedValue(
      fiche({
        mb_up: 120.5, mb_down: 840.2,
        partial: true,
        window_start: '2026-08-30T15:00:00Z',
        window_end: '2026-08-30T16:00:00Z',
      }),
    )
    const { container } = render(chart({ sessions: [session()] }))

    const select = container.querySelector('select') as HTMLSelectElement
    select.value = '1'
    select.dispatchEvent(new Event('change', { bubbles: true }))

    await waitFor(() =>
      expect(screen.getByText(/Fiche limitée à la fenêtre/)).toBeInTheDocument(),
    )
  })
})

describe('nommer un stationnement', () => {
  it('envoie le nom saisi, et le retire sur une saisie vide', async () => {
    // La route existait depuis le début et n'avait AUCUN appelant : nommer une
    // session — présenté comme ce qui rend deux stationnements comparables —
    // était inatteignable depuis l'interface.
    const sept = [
      { id: 7, router_id: 'mbx', started_at: new Date(T0).toISOString(), first_seen: null,
        last_seen: new Date(T0 + 3600_000).toISOString(), samples: 100, label: null },
    ]
    vi.mocked(fetchSessionSummary).mockResolvedValue({
      session: { id: 7, router_id: 'mbx', started_at: new Date(T0).toISOString(), first_seen: null,
                 last_seen: new Date(T0 + 3600_000).toISOString(), samples: 100, label: null },
      links: [], mb_up: null, mb_down: null, lat: null, lon: null, radius_m: null,
      sites: 0, partial: false, window_start: null, window_end: null,
      cells: [], availability: null, diversity: null,
    })
    vi.mocked(nameSession).mockResolvedValue(undefined)
    const rafraichir = vi.fn().mockResolvedValue(undefined)

    render(chart({ sessions: sept, onRafraichirSessions: rafraichir }))

    const selecteur = await screen.findByLabelText('Session d’alimentation')
    fireEvent.change(selecteur, { target: { value: '7' } })

    const champ = await screen.findByLabelText('Nom de la session')
    fireEvent.change(champ, { target: { value: '  Parking de la mairie  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enregistrer' }))

    // Le nom est ébarbé : les espaces de saisie ne font pas partie du nom.
    await waitFor(() => expect(nameSession).toHaveBeenCalledWith(7, '  Parking de la mairie  '))
    // Et la liste est relue par DeviceShell, pas par ce composant.
    await waitFor(() => expect(rafraichir).toHaveBeenCalled())
  })
})

describe('la fiche de session illisible', () => {
  it('le dit au lieu de faire disparaître la fiche en silence', async () => {
    // `.catch(() => setFiche(null))` : la fiche disparaissait sans un mot, et
    // l'absence se lisait comme « rien à dire de cette session ».
    vi.mocked(fetchSessionSummary).mockRejectedValue(new Error('délai dépassé'))
    const { container } = render(chart({ sessions: [session()] }))
    const select = container.querySelector('select') as HTMLSelectElement
    select.value = '1'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    expect(await screen.findByText(/Fiche de la session illisible : délai dépassé/)).toBeInTheDocument()
  })
})

describe('la fiche de la session EN COURS', () => {
  it('se relit au rythme de la fiche du tableau de bord, sans chevauchement', async () => {
    // Lue une fois au choix de la session, elle restait figée : le volume et
    // les médianes d'un stationnement en cours n'avançaient plus.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const { container } = render(chart({ sessions: [session()] }))
      const select = container.querySelector('select') as HTMLSelectElement
      select.value = '1'
      select.dispatchEvent(new Event('change', { bubbles: true }))
      await waitFor(() => expect(fetchSessionSummary).toHaveBeenCalledTimes(1))

      // Une relecture qui ne répond pas : la suivante ne part pas par-dessus.
      vi.mocked(fetchSessionSummary).mockImplementation(() => new Promise(() => {}))
      await act(async () => { await vi.advanceTimersByTimeAsync(RESUME_MS) })
      expect(fetchSessionSummary).toHaveBeenCalledTimes(2)
      await act(async () => { await vi.advanceTimersByTimeAsync(RESUME_MS) })
      expect(fetchSessionSummary).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('ne relit pas la fiche d’une session passée', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const courante = session({ id: 2, started_at: '2026-08-31T09:00:00Z', first_seen: '2026-08-31T09:00:05Z', last_seen: '2026-08-31T10:00:00Z' })
      const { container } = render(chart({ sessions: [courante, session({ id: 1 })] }))
      const select = container.querySelector('select') as HTMLSelectElement
      select.value = '1'
      select.dispatchEvent(new Event('change', { bubbles: true }))
      await waitFor(() => expect(fetchSessionSummary).toHaveBeenCalledTimes(1))
      await act(async () => { await vi.advanceTimersByTimeAsync(3 * RESUME_MS) })
      expect(fetchSessionSummary).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('une série sans aucune valeur', () => {
  it('dit l’absence de mesure au lieu d’un graphe vide', async () => {
    // La garde `data.length === 0` ne couvrait pas une série de points tous
    // nuls : un axe gradué sans courbe, qui se lit comme une mesure.
    vi.mocked(fetchHistory).mockResolvedValue({
      router_id: 'mbx', wan_id: '4', metric: 'sinr', available: 3,
      points: [0, 1, 2].map((i) => ({ ts: new Date(T0 + i * 5_300).toISOString(), value: null })),
    })
    render(chart())
    expect(await screen.findByText(/Aucune mesure de cette grandeur/)).toBeInTheDocument()
  })
})

describe('une session passée', () => {
  it('n’est pas rejouée toutes les quinze secondes, et ne promet aucune collecte', async () => {
    // 4 147 mesures rechargées pour une courbe qui ne peut pas changer ; et
    // une session passée sans point affichait « Collecte en cours ».
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const courante = session({ id: 2, started_at: '2026-08-31T09:00:00Z', first_seen: '2026-08-31T09:00:05Z', last_seen: '2026-08-31T10:00:00Z' })
      const passee = session({ id: 1 })
      const { container } = render(chart({ sessions: [courante, passee] }))
      await waitFor(() => expect(fetchHistory).toHaveBeenCalled())

      vi.mocked(fetchHistory).mockResolvedValue({ router_id: 'mbx', wan_id: '4', metric: 'sinr', points: [], available: 0 })
      const select = container.querySelector('select') as HTMLSelectElement
      select.value = '1'
      select.dispatchEvent(new Event('change', { bubbles: true }))
      await waitFor(() => expect(screen.getByText(/Aucune mesure de cette grandeur/)).toBeInTheDocument())
      expect(screen.queryByText(/Collecte en cours/)).toBeNull()
      expect(screen.getByText(/courbe figée/)).toBeInTheDocument()

      const appels = vi.mocked(fetchHistory).mock.calls.length
      await vi.advanceTimersByTimeAsync(45_000)
      expect(vi.mocked(fetchHistory).mock.calls.length).toBe(appels)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('changement de technologie', () => {
  it('le dit sous la courbe, avec l’heure', async () => {
    vi.mocked(fetchHistory).mockResolvedValue({
      router_id: 'mbx', wan_id: '2', metric: 'sinr', available: 4,
      points: [
        { ts: new Date(T0).toISOString(), value: 28, rat: '5G NSA' },
        { ts: new Date(T0 + 5_300).toISOString(), value: 28, rat: '5G NSA' },
        { ts: new Date(T0 + 10_600).toISOString(), value: 13, rat: 'LTE-A' },
        { ts: new Date(T0 + 15_900).toISOString(), value: 13, rat: 'LTE-A' },
      ],
    })
    render(chart())
    await waitFor(() => expect(screen.getByText(/Technologie changée une fois — 5G → LTE à/)).toBeInTheDocument())
    expect(screen.getByText(/porteuses\s+différentes/)).toBeInTheDocument()
  })
})

describe('un lien qui a changé d’opérateur', () => {
  it('le dit sur la fiche de session', async () => {
    vi.mocked(fetchSessionSummary).mockResolvedValue(fiche({
      links: [{ ...fiche().links[0], wan_id: '3', operator: 'Orange F', operators: ['Orange F', 'Mobile S Mobile'], maneuvers: 2 }],
    }))
    const { container } = render(chart({ sessions: [session()] }))
    const select = container.querySelector('select') as HTMLSelectElement
    select.value = '1'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await waitFor(() => expect(screen.getByText(/Orange F → Mobile S Mobile/)).toBeInTheDocument())
    expect(screen.getByText(/\+ 2 manœuvres/)).toBeInTheDocument()
  })
})

describe('les trous comblés par le routeur (R-3.1)', () => {
  const avecTrou = (): HistorySeries => ({
    router_id: 'mbx', wan_id: '4', metric: 'rsrp',
    points: [
      ...Array.from({ length: 20 }, (_, i) => ({ ts: new Date(T0 + i * 5_000).toISOString(), value: -90 })),
      ...Array.from({ length: 12 }, (_, i) => ({
        ts: new Date(T0 + 120_000 + i * 10_000).toISOString(), value: -105, source: 'routeur',
      })),
      ...Array.from({ length: 20 }, (_, i) => ({ ts: new Date(T0 + 300_000 + i * 5_000).toISOString(), value: -90 })),
    ],
    available: 52,
  })

  it('dit que la courbe a été comblée, et par qui', async () => {
    vi.mocked(fetchHistory).mockResolvedValue(avecTrou())
    render(chart({ kind: 'cellular' }))
    expect(await screen.findByText(/12 points comblés par l’historique du routeur/)).toBeInTheDocument()
  })

  it('ne dit rien quand rien n’a été comblé', async () => {
    vi.mocked(fetchHistory).mockResolvedValue(serie())
    render(chart())
    await screen.findByText(/Historique/)
    expect(screen.queryByText(/comblés? par l’historique du routeur/)).not.toBeInTheDocument()
  })
})

describe('une série appartient à sa grandeur (C-41)', () => {
  it('ne juge pas l’ancienne série avec les règles de la nouvelle', async () => {
    // Passage de SINR à Perte pendant que la requête est en vol : les points
    // du SINR, sans dénominateur, étaient jugés comme des taux — le pied
    // annonçait des points « retirés » qui n'existaient pas.
    vi.mocked(fetchHistory).mockReset()
    vi.mocked(fetchHistory)
      .mockResolvedValueOnce({
        router_id: 'mbx', wan_id: '4', metric: 'sinr',
        points: [
          { ts: new Date(T0).toISOString(), value: 12, denominator: null },
          { ts: new Date(T0 + 5300).toISOString(), value: 13, denominator: null },
        ],
        available: 2,
      })
      .mockReturnValue(new Promise(() => {}))
    render(chart())
    await waitFor(() => expect(fetchHistory).toHaveBeenCalledTimes(1))
    await act(async () => {})
    screen.getByRole('button', { name: 'Perte' }).click()
    await waitFor(() => expect(fetchHistory).toHaveBeenCalledTimes(2))
    expect(screen.queryByText(/retirés? de la courbe/)).toBeNull()
  })
})

describe('ce qui est choisi se voit (C-19)', () => {
  it('marque la grandeur et la fenêtre choisies, pour l’œil et pour le lecteur d’écran', async () => {
    // Les boutons posaient la classe « active », qu'aucune feuille ne
    // stylise : la seule règle est « .segmented button.is-active ». On
    // cliquait sur « RSRP » puis sur « 3 h » et rien ne changeait d'aspect.
    const { container } = render(chart())
    await waitFor(() => expect(container.querySelector('.segmented button')).not.toBeNull())
    const choisis = [...container.querySelectorAll('.segmented button[aria-pressed="true"]')]
    expect(choisis.length).toBe(2)
    for (const b of choisis) expect(b.classList.contains('is-active')).toBe(true)
    expect(container.querySelectorAll('.segmented .active').length).toBe(0)
  })

  it('la feuille de style met en évidence exactement cette classe', async () => {
    const css = (await import('../index.css?raw')).default as string
    expect(css).toMatch(/\.segmented button\.is-active/)
  })
})

describe('la courbe explique ses creux (A-4)', () => {
  it('dit le trou de supervision et la désactivation qu’elle traverse', async () => {
    // Vu le 17/10 : la courbe SINR de la SIM 4 s'interrompait à 13:44, 13:51,
    // 13:58 sans dire qu'il s'agissait de redémarrages du superviseur et d'une
    // désactivation par l'exploitant.
    const t = (s: number) => new Date(T0 + s * 1000).toISOString()
    vi.mocked(fetchHistory).mockResolvedValue({
      router_id: 'mbx', wan_id: '4', metric: 'sinr', available: 8,
      points: [0, 6, 12, 18, 138, 144, 150, 156].map((s) => ({ ts: t(s), value: 5 })),
    })
    render(chart({
      episodes: [
        { kind: 'désactivation', wan_id: '4', link_kind: 'cellular', at: t(140), until: null,
          samples: 1, peak_ms: null, detail: 'désactivé', after_handover_s: null },
        { kind: 'retour', wan_id: '4', link_kind: 'cellular', at: t(150), until: null,
          samples: 1, peak_ms: null, detail: null, after_handover_s: null },
      ],
    }))
    await waitFor(() => expect(screen.getByText(/Sur la courbe/)).toBeInTheDocument())
    const note = screen.getByText(/Sur la courbe/).textContent ?? ''
    expect(note).toMatch(/1 trou de supervision/)
    expect(note).toMatch(/désactivé de/)
  })
})

describe('une session passée garde ses propres repères (revue du 17/10)', () => {
  it('lit le journal de CETTE session, pas celui de la fenêtre courante', async () => {
    const t = (s: number) => new Date(T0 + s * 1000).toISOString()
    vi.mocked(fetchHistory).mockResolvedValue({
      router_id: 'mbx', wan_id: '4', metric: 'sinr', available: 4,
      points: [0, 6, 12, 18].map((s) => ({ ts: t(s), value: 5 })),
    })
    vi.mocked(fetchSessionSummary).mockResolvedValue(fiche())
    vi.mocked(fetchEvents).mockResolvedValue({
      router_id: 'mbx', started_at: t(0), ended_at: t(18), capped: false, churn: [], omitted: 0,
      latency_threshold_ms: 1000,
      episodes: [{ kind: 'désactivation', wan_id: '4', link_kind: 'cellular', at: t(6), until: null,
                   samples: 1, peak_ms: null, detail: 'désactivé', after_handover_s: null }],
    } as never)
    const passee = session({ id: 1 })
    const courante = session({ id: 2, started_at: '2026-08-31T08:00:00Z', first_seen: '2026-08-31T08:00:05Z',
                               last_seen: '2026-08-31T09:00:00Z' })
    const { container } = render(chart({ sessions: [courante, passee], episodes: [] }))
    const select = container.querySelector('select') as HTMLSelectElement
    select.value = '1'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await waitFor(() => expect(fetchEvents).toHaveBeenCalledWith('mbx', { since: passee.first_seen, until: passee.last_seen }))
    await waitFor(() => expect(screen.getByText(/désactivé de/)).toBeInTheDocument())
  })
})
