import { act, render, screen, waitFor } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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

import { fetchHistory, fetchSessionSummary } from '../api/client'
import type { Session } from '../types'
import { lireReglage } from '../lib/reglageHistorique'
import { HistoryChart } from './HistoryChart'

/* Grandeur, fenêtre et session de l'historique, gardées dans l'adresse. */

const T0 = Date.parse('2026-08-30T14:00:00Z')

const session = (id: number): Session => ({
  id, router_id: 'mbx', started_at: '2026-08-30T13:00:00Z', first_seen: '2026-08-30T13:00:05Z',
  last_seen: '2026-08-30T16:00:00Z', samples: 2000, label: null,
})

/** Les entrées d'historique traversées, et de quoi revenir en arrière. */
let cles: string[] = []
let recherche = ''
function Temoin() {
  const location = useLocation()
  const navigate = useNavigate()
  recherche = location.search
  useEffect(() => {
    if (cles[cles.length - 1] !== location.key) cles.push(location.key)
  }, [location.key])
  return <button type="button" onClick={() => navigate(-1)}>précédent</button>
}

const monter = (adresse: string, sessions: Session[] = []) =>
  render(
    <MemoryRouter initialEntries={[adresse]}>
      <Routes>
        <Route path="/e/:routerId/wan/:wanId" element={
          <>
            <Temoin />
            <HistoryChart routerId="mbx" wanId="4" wanName="Cellulaire 4" kind="cellular"
              hasTunnel sessions={sessions} />
          </>
        } />
      </Routes>
    </MemoryRouter>,
  )

const presse = (nom: string) => screen.getByRole('button', { name: nom }).getAttribute('aria-pressed')
const derniereGrandeur = () => vi.mocked(fetchHistory).mock.lastCall?.slice(2, 4)

beforeEach(() => {
  cles = []
  recherche = ''
  vi.mocked(fetchHistory).mockReset()
  vi.mocked(fetchHistory).mockResolvedValue({
    router_id: 'mbx', wan_id: '4', metric: 'x', available: 1,
    points: [{ ts: new Date(T0).toISOString(), value: 1 }],
  })
  vi.mocked(fetchSessionSummary).mockReturnValue(new Promise(() => {}))
})

describe('les réglages de l’historique dans l’adresse', () => {
  it('rouvre la grandeur et la fenêtre qu’elle porte', async () => {
    monter('/e/mbx/wan/4?grandeur=rsrp&fenetre=180')
    await waitFor(() => expect(derniereGrandeur()).toEqual(['rsrp', 180]))
    expect(presse('RSRP')).toBe('true')
    expect(presse('3 h')).toBe('true')
  })

  it('retombe sur le défaut pour une valeur inconnue, sans erreur', async () => {
    // Un lien ancien, tronqué ou retapé : une grandeur que ce lien n'a pas
    // (une grandeur filaire, un nom inventé), une fenêtre non proposée, un
    // identifiant de session qui n'en est pas un.
    monter('/e/mbx/wan/4?grandeur=inexistante&fenetre=7&session=12abc')
    await waitFor(() => expect(derniereGrandeur()).toEqual(['sinr', 30]))
    expect(presse('SINR')).toBe('true')
    expect(presse('30 min')).toBe('true')
  })

  it('rouvre une session connue', async () => {
    monter('/e/mbx/wan/4?session=12', [session(12)])
    await waitFor(() => expect(vi.mocked(fetchHistory).mock.lastCall?.[4])
      .toEqual(expect.objectContaining({ since: '2026-08-30T13:00:05Z' })))
    expect((screen.getByLabelText('Session d’alimentation') as HTMLSelectElement).value).toBe('12')
  })

  it('ignore une session absente de la liste', async () => {
    monter('/e/mbx/wan/4?session=99', [session(12)])
    await waitFor(() => expect(fetchHistory).toHaveBeenCalled())
    expect(vi.mocked(fetchHistory).mock.lastCall?.[4]).toBeUndefined()
    expect(presse('30 min')).toBe('true')
  })

  it('écrit chaque choix dans l’adresse, et « Précédent » rend le réglage d’avant', async () => {
    monter('/e/mbx/wan/4')
    await waitFor(() => expect(fetchHistory).toHaveBeenCalled())
    // Une fiche jamais réglée garde une adresse nue.
    expect(recherche).toBe('')

    await act(async () => { screen.getByRole('button', { name: 'RSRP' }).click() })
    expect(recherche).toBe('?grandeur=rsrp')
    await act(async () => { screen.getByRole('button', { name: '3 h' }).click() })
    expect(lireReglage(new URLSearchParams(recherche))).toEqual({ grandeur: 'rsrp', fenetre: 180, session: null })
    expect(cles).toHaveLength(3)

    await act(async () => { screen.getByRole('button', { name: 'précédent' }).click() })
    expect(recherche).toBe('?grandeur=rsrp')
    expect(presse('RSRP')).toBe('true')
    expect(presse('30 min')).toBe('true')
    await waitFor(() => expect(derniereGrandeur()).toEqual(['rsrp', 30]))
  })

  it('n’ajoute pas d’entrée pour un réglage qui ne change rien', async () => {
    monter('/e/mbx/wan/4?grandeur=rsrp')
    await waitFor(() => expect(fetchHistory).toHaveBeenCalled())
    await act(async () => { screen.getByRole('button', { name: 'RSRP' }).click() })
    await act(async () => { screen.getByRole('button', { name: '30 min' }).click() })
    expect(cles).toHaveLength(1)
    expect(recherche).toBe('?grandeur=rsrp')
  })

  it('choisir une session l’écrit, revenir à une fenêtre l’efface', async () => {
    monter('/e/mbx/wan/4', [session(12)])
    await waitFor(() => expect(fetchHistory).toHaveBeenCalled())
    const select = screen.getByLabelText('Session d’alimentation') as HTMLSelectElement
    await act(async () => {
      select.value = '12'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(recherche).toBe('?session=12')
    await act(async () => { screen.getByRole('button', { name: '12 h' }).click() })
    expect(recherche).toBe('?fenetre=720')
  })
})
