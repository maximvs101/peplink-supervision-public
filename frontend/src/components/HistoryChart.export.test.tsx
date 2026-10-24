import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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

import { fetchHistory } from '../api/client'
import type { HistorySeries } from '../types'
import { HistoryChart } from './HistoryChart'

/* « Exporter en CSV » : exactement la courbe affichée, fabriquée dans le
 * navigateur. */

const T0 = Date.parse('2026-08-30T14:00:00Z')

const serie = (metric: string, valeurs: (number | null)[], available = valeurs.length): HistorySeries => ({
  router_id: 'mbx', wan_id: '4', metric, available,
  points: valeurs.map((value, i) => ({ ts: new Date(T0 + i * 5_300).toISOString(), value, denominator: 12000 })),
})

/** Ce que le navigateur reçoit : le contenu du Blob et le nom proposé. */
let fichiers: { nom: string; blob: Blob }[] = []
let dernierBlob: Blob | null = null

beforeEach(() => {
  fichiers = []
  vi.stubGlobal('URL', Object.assign(URL, {
    createObjectURL: vi.fn((b: Blob) => { dernierBlob = b; return 'blob:courbe' }),
    revokeObjectURL: vi.fn(),
  }))
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    fichiers.push({ nom: this.download, blob: dernierBlob! })
  })
})

afterEach(() => { vi.restoreAllMocks() })

const monter = (equipement: string | null = 'MBX Atelier') =>
  render(
    <MemoryRouter>
      <HistoryChart routerId="opaque-7f3a" wanId="4" wanName="Cellulaire 4" kind="cellular" hasTunnel
        equipement={equipement} />
    </MemoryRouter>,
  )

const exporter = async () => {
  const bouton = await screen.findByRole('button', { name: 'Exporter en CSV' })
  await act(async () => { bouton.click() })
  expect(fichiers).toHaveLength(1)
  // Les octets, décodés SANS ôter le BOM : `Blob.text()` l'escamote, et
  // c'est précisément lui qui fait ouvrir le fichier en UTF-8 par le tableur.
  const octets = await fichiers[0].blob.arrayBuffer()
  return { nom: fichiers[0].nom, texte: new TextDecoder('utf-8', { ignoreBOM: true }).decode(octets) }
}

describe('exporter la courbe affichée', () => {
  it('rend la grandeur et la fenêtre affichées, une absence restant une cellule vide', async () => {
    vi.mocked(fetchHistory).mockResolvedValue(serie('sinr', [12.5, null, 0]))
    monter()
    const { nom, texte } = await exporter()
    // Le NOM de l'équipement, pas son identifiant opaque.
    expect(nom).toBe('courbe-MBX-Atelier-Cellulaire-4-sinr-30min.csv')
    expect(nom).not.toMatch(/opaque/)
    expect(texte.charCodeAt(0)).toBe(0xfeff)
    const lignes = texte.slice(1).split('\r\n')
    expect(lignes).toContain('# grandeur;SINR (dB)')
    expect(lignes).toContain('# fenêtre;30 min')
    expect(lignes).toContain('# série;complète')
    const corps = lignes.filter((l) => /^\d{4}-/.test(l))
    expect(corps.map((l) => l.split(';')[1])).toEqual(['12,5', '', '0'])
  })

  it('suit la grandeur choisie, dans l’unité tracée', async () => {
    vi.mocked(fetchHistory).mockImplementation(async (_r, _w, metric) =>
      metric === 'tunnel_rx_kbps' ? serie('tunnel_rx_kbps', [1500, 2250]) : serie('sinr', [10, 11]))
    monter()
    await screen.findByRole('button', { name: 'Exporter en CSV' })
    await act(async () => { screen.getByRole('button', { name: 'Bond ↓' }).click() })
    await waitFor(() => expect(fetchHistory).toHaveBeenCalledWith(
      'opaque-7f3a', '4', 'tunnel_rx_kbps', 30, undefined, undefined))
    await waitFor(async () => {
      fichiers = []
      const { nom, texte } = await exporter()
      expect(nom).toBe('courbe-MBX-Atelier-Cellulaire-4-tunnel-rx-kbps-30min.csv')
      expect(texte).toContain('instant;valeur (Mbit/s);source;remarque')
      expect(texte).toMatch(/;1,5;superviseur;\r\n.*;2,25;superviseur;/)
    })
  })

  it('laisse vide un taux retiré de la courbe, et dit pourquoi', async () => {
    vi.mocked(fetchHistory).mockResolvedValue({
      router_id: 'mbx', wan_id: '4', metric: 'loss_percent', available: 2,
      points: [
        { ts: new Date(T0).toISOString(), value: 0, denominator: 12000 },
        { ts: new Date(T0 + 5300).toISOString(), value: 33.33, denominator: 1 },
      ],
    })
    monter()
    await screen.findByRole('button', { name: 'Exporter en CSV' })
    await act(async () => { screen.getByRole('button', { name: 'Perte' }).click() })
    await screen.findByText(/Un point a été retiré/)
    const { texte } = await exporter()
    expect(texte).not.toContain('33,33')
    expect(texte).toMatch(/;;superviseur;retiré : moins de 200 paquets comptés/)
  })

  it('dit qu’une enveloppe n’est pas la série complète', async () => {
    vi.mocked(fetchHistory).mockResolvedValue(serie('sinr', [3, 9], 4147))
    monter()
    const { nom, texte } = await exporter()
    expect(nom).toMatch(/-enveloppe\.csv$/)
    expect(texte).toContain('# série;enveloppe : minimum et maximum par tranche, 2 points pour 4147 mesures')
  })

  it('ne propose rien tant qu’aucune courbe n’est tracée', async () => {
    vi.mocked(fetchHistory).mockResolvedValue(serie('sinr', []))
    monter()
    await screen.findByText(/Collecte en cours/)
    expect(screen.queryByRole('button', { name: 'Exporter en CSV' })).toBeNull()
  })

  it('n’emprunte jamais l’identifiant opaque faute de nom', async () => {
    vi.mocked(fetchHistory).mockResolvedValue(serie('sinr', [1]))
    monter(null)
    const { nom, texte } = await exporter()
    expect(nom).toBe('courbe-equipement-Cellulaire-4-sinr-30min.csv')
    expect(texte).not.toMatch(/opaque/)
  })
})
