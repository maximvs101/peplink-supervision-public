import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ reauthDevice: vi.fn() }))
import { reauthDevice } from '../api/client'
import { RepriseIdentifiants, VERDICT_REPRISE_MS } from './RepriseIdentifiants'

/**
 * C-21 : après une reprise ratée, l'écran annonçait encore une tentative « au
 * prochain cycle » et laissait le bouton actif. Chaque clic rejoue les MÊMES
 * identifiants et consomme l'un des cinq échecs qui suspendent l'accès admin
 * cinq minutes. C-26 (F9) : aucun test ne vérifiait qu'aucune reprise ne part
 * sans clic.
 */

const monter = (status: string) =>
  render(<MemoryRouter><RepriseIdentifiants routerId="dome" status={status} /></MemoryRouter>)

beforeEach(() => {
  vi.mocked(reauthDevice).mockReset()
  vi.mocked(reauthDevice).mockResolvedValue({ device: 'dome', status: 'en attente' } as never)
})

describe('la reprise des identifiants', () => {
  it('ne part jamais sans clic', async () => {
    const { rerender } = monter('identifiants refusés')
    rerender(<MemoryRouter><RepriseIdentifiants routerId="dome" status="identifiants refusés" /></MemoryRouter>)
    await act(async () => {})
    expect(reauthDevice).not.toHaveBeenCalled()
  })

  it('désactive le bouton pendant la tentative, puis dit qu’elle a échoué', async () => {
    const { rerender } = monter('identifiants refusés')
    const bouton = screen.getByRole('button', { name: /Réessayer la connexion/ })
    await act(async () => { fireEvent.click(bouton) })
    expect(reauthDevice).toHaveBeenCalledTimes(1)
    expect(bouton).toBeDisabled()
    fireEvent.click(bouton)
    expect(reauthDevice).toHaveBeenCalledTimes(1)

    // Le serveur passe l'équipement « en attente », puis le cycle suivant
    // retombe sur un refus : la tentative a eu lieu, et elle a échoué.
    rerender(<MemoryRouter><RepriseIdentifiants routerId="dome" status="en attente" /></MemoryRouter>)
    rerender(<MemoryRouter><RepriseIdentifiants routerId="dome" status="identifiants refusés" /></MemoryRouter>)
    expect(screen.queryByText(/aura lieu au prochain cycle/)).toBeNull()
    expect(screen.getByText(/Nouvel essai refusé/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /découverte/ })).toHaveAttribute('href', '/decouverte')
  })

  it('conclut à l’échec même si l’écran n’a jamais vu « en attente »', async () => {
    vi.useFakeTimers()
    try {
      monter('identifiants refusés')
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Réessayer/ })) })
      expect(screen.getByText(/aura lieu au prochain cycle/)).toBeInTheDocument()
      await act(async () => { vi.advanceTimersByTime(VERDICT_REPRISE_MS) })
      expect(screen.getByText(/Nouvel essai refusé/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /Réessayer/ })).not.toBeDisabled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('ne s’affiche pas hors d’un refus d’identifiants', () => {
    const { container } = monter('connecté')
    expect(container.textContent).toBe('')
  })
})
