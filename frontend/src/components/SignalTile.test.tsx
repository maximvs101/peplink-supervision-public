import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { SignalTile } from './SignalTile'

/**
 * Une donnée absente n'est pas une donnée mauvaise, et une grandeur sans seuils
 * ne reçoit pas de note. Ces deux règles ont été enfreintes deux fois : la tuile
 * de débit affichait « Moyen » sous un tiret, puis « Excellent » sous 0,0 Mbit/s.
 */
describe('tuile de mesure', () => {
  it('ne note pas une mesure absente', () => {
    render(<SignalTile label="Débit descendant" value={null} unit="Mbit/s" quality="moyen" />)
    expect(screen.getByText('non fourni')).toBeInTheDocument()
    expect(screen.queryByText('Moyen')).not.toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('ne note pas une grandeur sans seuils', () => {
    // Un débit de 0 Mbit/s sur un lien au repos est normal, pas excellent.
    render(<SignalTile label="Débit descendant" value={0} unit="Mbit/s" />)
    expect(screen.queryByText('Excellent')).not.toBeInTheDocument()
    expect(screen.getByText('0,0')).toBeInTheDocument()
  })

  it('note une grandeur qui a des seuils', () => {
    render(<SignalTile label="RSRP" value={-88} unit="dBm" quality="bon" />)
    expect(screen.getByText('Bon')).toBeInTheDocument()
    expect(screen.getByText('-88,0')).toBeInTheDocument()
  })

  it('masque l’unité quand il n’y a pas de valeur', () => {
    const { container } = render(<SignalTile label="RSRP" value={null} unit="dBm" />)
    expect(container.querySelector('.unit')).toBeNull()
  })

  it('reste neutre visuellement sans note', () => {
    const { container } = render(<SignalTile label="Débit" value={12} unit="Mbit/s" />)
    const tile = container.querySelector('.tile') as HTMLElement
    expect(tile.style.getPropertyValue('--q')).toBe('var(--muted)')
  })
})
