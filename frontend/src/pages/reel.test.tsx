/**
 * Le tableau de bord monté sur ce que trois équipements RÉELS ont répondu.
 *
 * `src/test/reel/*.json` n'est pas une fabrique : c'est la réponse des routes
 * du serveur, obtenue en rejouant chaque capture à travers le client, le
 * collecteur, la base et les routes réels (`backend/tools/exporter_rejeu.py`).
 * Aucune hypothèse de l'auteur ne s'est glissée entre le routeur et ce rendu —
 * là où la galerie (`scenarios.test.tsx`) et la confrontation
 * (`confrontation.test.tsx`) montent des relevés écrits à la main.
 *
 * Ce qui est vérifié est le même balayage que la confrontation — aucun mot
 * qu'une interface ne doit jamais écrire, dans le texte ni dans les attributs
 * du dessin — plus, par équipement, le fait que ce châssis-là a montré sur
 * place et que l'écran doit continuer de dire.
 */

import { act, render, screen } from '@testing-library/react'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  fetchRouter: vi.fn(),
  fetchSessions: vi.fn(),
  fetchTimeline: vi.fn(),
  fetchEvents: vi.fn(),
  reauthDevice: vi.fn(),
  httpStatus: () => null,
  openLiveSocket: vi.fn(() => () => {}),
}))

import { fetchEvents, fetchRouter, fetchSessions, fetchTimeline } from '../api/client'
import { WanCard } from '../components/WanCard'
import type { EventLog, RouterSummary, Session, Timeline } from '../types'
import { DeviceShell } from './DeviceShell'
import { Overview } from './Overview'
import { WanDetail } from './WanDetail'

import dome from '../test/reel/dome.json'
import hd2 from '../test/reel/hd2.json'
import mbx from '../test/reel/mbx.json'

interface Reel {
  capture: string
  router: RouterSummary
  sessions: Session[]
  timeline: Timeline
  events: EventLog
}

const REELS: Record<string, Reel> = {
  'MAX HD4 MBX, quatre SIM, tunnel monté': mbx as unknown as Reel,
  'Dome Pro Duo, fiche sondée, sans débit par WAN': dome as unknown as Reel,
  'MAX HD2, deux opérateurs, pair injoignable': hd2 as unknown as Reel,
}

const INTERDITS = ['NaN', 'undefined', 'Infinity', 'Invalid Date', '[object Object]']
const NULL_ENTIER = /\bnull\b/

function balayer(container: HTMLElement, nom: string, auMoins = 200) {
  const texte = container.textContent ?? ''
  // Sans ce garde, le balayage passerait sur un écran vide.
  expect(texte.length).toBeGreaterThan(auMoins)
  for (const mot of INTERDITS) expect(texte, `« ${nom} » affiche « ${mot} »`).not.toContain(mot)
  expect(texte, `« ${nom} » affiche « null »`).not.toMatch(NULL_ENTIER)
  for (const el of container.querySelectorAll('[stroke-width], [d], [opacity], [x], [y], [cx], [cy], [r], [width], [height]')) {
    for (const attr of ['stroke-width', 'd', 'opacity', 'x', 'y', 'cx', 'cy', 'r', 'width', 'height']) {
      const v = el.getAttribute(attr)
      if (v !== null) expect(v, `« ${nom} » : ${el.tagName} ${attr}="${v}"`).not.toMatch(/NaN|Infinity|undefined/)
    }
  }
}

const Outil = () => <Outlet context={{ health: null, routers: [] }} />

const monterTableauDeBord = () =>
  render(
    <MemoryRouter initialEntries={['/e/reel']}>
      <Routes>
        <Route element={<Outil />}>
          <Route path="/e/:routerId" element={<DeviceShell />}>
            <Route index element={<Overview />} />
          </Route>
        </Route>
      </Routes>
    </MemoryRouter>,
  )

beforeEach(() => {
  vi.useFakeTimers()
})

describe('le tableau de bord sur un équipement réel', () => {
  for (const [nom, reel] of Object.entries(REELS)) {
    it(`« ${nom} » se rend entier, sans un mot technique`, async () => {
      vi.mocked(fetchRouter).mockResolvedValue(reel.router)
      vi.mocked(fetchSessions).mockResolvedValue(reel.sessions)
      vi.mocked(fetchTimeline).mockResolvedValue(reel.timeline)
      vi.mocked(fetchEvents).mockResolvedValue(reel.events)
      const { container, unmount } = monterTableauDeBord()
      await act(async () => { await vi.advanceTimersByTimeAsync(50) })

      // Chaque lien a son rail dans le plan et sa ligne dans la table.
      expect(container.querySelectorAll('.plan-rail')).toHaveLength(reel.router.wans.length)
      expect(container.querySelectorAll('.ltable tbody tr')).toHaveLength(reel.router.wans.length)
      // La fiche d'identité est celle du châssis capté.
      expect(container.textContent).toContain(reel.router.device!.serial!)
      balayer(container, nom)
      unmount()
    })

    it(`« ${nom} » : chaque fiche de lien se rend sans un mot technique`, () => {
      for (const sample of reel.router.wans) {
        const { container, unmount } = render(
          <MemoryRouter>
            <WanCard sample={sample} showThroughput={reel.router.capabilities?.per_wan_throughput ?? false}
              churn={null} windowLabel="Depuis la mise sous tension" />
          </MemoryRouter>,
        )
        // Une fiche de lien déconnecté est courte, et c'est juste : elle ne
        // dit que ce qu'elle sait.
        balayer(container, `${nom} · ${sample.wan.wan_name}`, 40)
        unmount()
      }
    })
  }

  it('sur le HD2, le bandeau dit un tunnel jamais établi — ni rompue, ni tombé', async () => {
    // Le défaut vu par l'exploitant le 19 septembre : trois liens debout,
    // le pair injoignable, et « Liaison rompue » en rouge. Puis « Tunnel
    // SpeedFusion tombé — 2 h dans cet état » en ambre : un bond qui n'a
    // jamais porté dans la session n'est pas tombé, c'est un état, et le
    // serveur le publie (`tunnel_attendu: false`) sans ouvrir d'alerte.
    const reel = REELS['MAX HD2, deux opérateurs, pair injoignable']
    expect(reel.router.tunnel_attendu).toBe(false)
    vi.mocked(fetchRouter).mockResolvedValue(reel.router)
    vi.mocked(fetchSessions).mockResolvedValue(reel.sessions)
    vi.mocked(fetchTimeline).mockResolvedValue(reel.timeline)
    vi.mocked(fetchEvents).mockResolvedValue(reel.events)
    const { container, unmount } = monterTableauDeBord()
    await act(async () => { await vi.advanceTimersByTimeAsync(50) })
    const titre = screen.getByRole('heading', { level: 1 }).textContent ?? ''
    expect(titre).not.toMatch(/rompue/i)
    expect(titre).not.toMatch(/tombé/i)
    expect(container.textContent).toMatch(/jamais établi/)
    unmount()
  })

  it('ne dit rien du tunnel auprès du bond sur un relevé périmé', async () => {
    // `Overview` appelait `linkVerdict` SANS l'âge du relevé : les griefs du
    // tunnel restaient affichés auprès du bond pendant que le bandeau, lui,
    // déclarait la mesure périmée. Deux verdicts sur un même relevé.
    const reel = REELS['MAX HD4 MBX, quatre SIM, tunnel monté']
    const wans = reel.router.wans.map((s, i) => i === 0
      ? { ...s, wan: { ...s.wan, tunnel: { ...s.wan.tunnel!, state: 'WAN_DOWN', in_bond: false } } }
      : s)
    const perime = { ...reel.router, wans, bond_routes: ['10.0.0.0/24'], age_seconds: 600 }
    vi.mocked(fetchRouter).mockResolvedValue(perime)
    vi.mocked(fetchSessions).mockResolvedValue(reel.sessions)
    vi.mocked(fetchTimeline).mockResolvedValue(reel.timeline)
    vi.mocked(fetchEvents).mockResolvedValue(reel.events)
    const { container, unmount } = monterTableauDeBord()
    await act(async () => { await vi.advanceTimersByTimeAsync(50) })
    expect(screen.getByRole('heading', { level: 1 }).textContent).toMatch(/périmées/)
    expect(container.textContent).not.toMatch(/Dans le tunnel/)
    unmount()

    // Le même relevé, frais : le grief est dit auprès du bond.
    vi.mocked(fetchRouter).mockResolvedValue({ ...perime, age_seconds: 1 })
    const frais = monterTableauDeBord()
    await act(async () => { await vi.advanceTimersByTimeAsync(50) })
    expect(frais.container.textContent).toMatch(/Dans le tunnel : 1 hors du bond/)
    frais.unmount()
  })

  it('dit un refus d’identifiants comme tel, pas comme un silence (C-42)', async () => {
    // « l'équipement ne répond pas » pour des identifiants refusés, et
    // « identité en cours de lecture » alors qu'aucune lecture n'aura lieu.
    const reel = REELS['MAX HD4 MBX, quatre SIM, tunnel monté']
    vi.mocked(fetchRouter).mockResolvedValue({
      ...reel.router, wans: [], device: null, status: 'identifiants refusés', error: 'login refusé',
    })
    vi.mocked(fetchSessions).mockResolvedValue(reel.sessions)
    vi.mocked(fetchTimeline).mockResolvedValue(reel.timeline)
    vi.mocked(fetchEvents).mockResolvedValue(reel.events)
    const { container, unmount } = monterTableauDeBord()
    await act(async () => { await vi.advanceTimersByTimeAsync(50) })
    expect(container.textContent).not.toMatch(/ne répond pas/)
    expect(container.textContent).toMatch(/Aucune mesure : les identifiants ont été refusés/)
    expect(container.textContent).not.toMatch(/identité en cours de lecture/)
    unmount()
  })

  it('ne promet pas une première mesure à un équipement injoignable', async () => {
    // Le libellé ne dépendait que de `samples.length` : « première mesure en
    // cours… » sous un bandeau « Équipement injoignable ». Vu sur un MBX réel.
    const reel = REELS['MAX HD4 MBX, quatre SIM, tunnel monté']
    vi.mocked(fetchRouter).mockResolvedValue({
      ...reel.router, wans: [], status: 'injoignable', error: 'connexion refusée',
    })
    vi.mocked(fetchSessions).mockResolvedValue(reel.sessions)
    vi.mocked(fetchTimeline).mockResolvedValue(reel.timeline)
    vi.mocked(fetchEvents).mockResolvedValue(reel.events)
    const { container, unmount } = monterTableauDeBord()
    await act(async () => { await vi.advanceTimersByTimeAsync(50) })
    expect(container.textContent).not.toMatch(/première mesure en cours/)
    expect(container.textContent).toMatch(/ne répond pas/)
    unmount()
  })

  it('garde ses stationnements passés lisibles quand il ne répond plus', async () => {
    // Vu le 30/09 : deux stationnements en base pour un MBX éteint, et aucun
    // chemin vers eux — le seul accès aux rapports est l'historique d'un lien,
    // qu'on n'atteint pas sans relevé en cours.
    const reel = REELS['MAX HD4 MBX, quatre SIM, tunnel monté']
    vi.mocked(fetchRouter).mockResolvedValue({
      ...reel.router, wans: [], status: 'injoignable', error: 'connexion refusée',
    })
    vi.mocked(fetchSessions).mockResolvedValue(reel.sessions)
    vi.mocked(fetchTimeline).mockResolvedValue(reel.timeline)
    vi.mocked(fetchEvents).mockResolvedValue(reel.events)
    const { container, unmount } = monterTableauDeBord()
    await act(async () => { await vi.advanceTimersByTimeAsync(50) })
    const liens = [...container.querySelectorAll('a')].map((a) => a.getAttribute('href'))
    expect(liens).toContain(`/rapport/${reel.sessions[0].id}`)
    expect(container.textContent).not.toMatch(/Invalid Date/)
    unmount()
  })

  it('ne promet pas non plus de première mesure dans le détail d’un lien', async () => {
    const reel = REELS['MAX HD4 MBX, quatre SIM, tunnel monté']
    vi.mocked(fetchRouter).mockResolvedValue({
      ...reel.router, wans: [], status: 'injoignable', error: 'connexion refusée',
    })
    vi.mocked(fetchSessions).mockResolvedValue(reel.sessions)
    vi.mocked(fetchTimeline).mockResolvedValue(reel.timeline)
    vi.mocked(fetchEvents).mockResolvedValue(reel.events)
    const { container, unmount } = render(
      <MemoryRouter initialEntries={['/e/reel/wan/1']}>
        <Routes>
          <Route element={<Outil />}>
            <Route path="/e/:routerId" element={<DeviceShell />}>
              <Route path="wan/:wanId" element={<WanDetail />} />
            </Route>
          </Route>
        </Routes>
      </MemoryRouter>,
    )
    await act(async () => { await vi.advanceTimersByTimeAsync(50) })
    expect(container.textContent).not.toMatch(/première mesure en cours/i)
    expect(container.textContent).toMatch(/ne répond pas/)
    const liens = [...container.querySelectorAll('a')].map((a) => a.getAttribute('href'))
    expect(liens).toContain(`/rapport/${reel.sessions[0].id}`)
    unmount()
  })

  it('sur le Dome, aucune colonne de débit : le châssis ne le publie pas', async () => {
    const reel = REELS['Dome Pro Duo, fiche sondée, sans débit par WAN']
    expect(reel.router.capabilities?.per_wan_throughput).toBe(false)
    vi.mocked(fetchRouter).mockResolvedValue(reel.router)
    vi.mocked(fetchSessions).mockResolvedValue(reel.sessions)
    vi.mocked(fetchTimeline).mockResolvedValue(reel.timeline)
    vi.mocked(fetchEvents).mockResolvedValue(reel.events)
    const { container, unmount } = monterTableauDeBord()
    await act(async () => { await vi.advanceTimersByTimeAsync(50) })
    const entetes = [...container.querySelectorAll('.ltable thead th')].map((e) => e.textContent ?? '')
    expect(entetes.some((e) => /Mbit/.test(e))).toBe(false)
    unmount()
  })
})
