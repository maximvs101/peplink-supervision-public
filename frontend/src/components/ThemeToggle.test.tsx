import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { appliquerThemeMemorise } from '../lib/themeChoice'
import { ThemeToggle } from './ThemeToggle'

/**
 * R-0.1 : le thème suit le système, et une bascule le force sur ce poste —
 * en plein soleil, on veut le clair sans aller dans les réglages du système.
 */

afterEach(() => {
  delete document.documentElement.dataset.theme
  localStorage.clear()
  vi.restoreAllMocks()
})

const bouton = (nom: string) => screen.getByRole('button', { name: nom })

describe('la bascule de thème', () => {
  it('suit le système par défaut : aucun thème posé', () => {
    render(<ThemeToggle />)
    expect(document.documentElement.dataset.theme).toBeUndefined()
    expect(bouton('Auto').getAttribute('aria-pressed')).toBe('true')
  })

  it('force le clair ou le sombre, et le retient sur ce poste', () => {
    render(<ThemeToggle />)
    fireEvent.click(bouton('Clair'))
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(localStorage.getItem('theme')).toBe('light')
    expect(bouton('Clair').getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(bouton('Sombre'))
    expect(document.documentElement.dataset.theme).toBe('dark')
    fireEvent.click(bouton('Auto'))
    expect(document.documentElement.dataset.theme).toBeUndefined()
    expect(localStorage.getItem('theme')).toBeNull()
  })

  it('reprend au démarrage le choix retenu, avant tout rendu', () => {
    localStorage.setItem('theme', 'light')
    appliquerThemeMemorise()
    expect(document.documentElement.dataset.theme).toBe('light')
    render(<ThemeToggle />)
    expect(bouton('Clair').getAttribute('aria-pressed')).toBe('true')
  })

  it('ignore une valeur retenue qui n’est pas un thème', () => {
    localStorage.setItem('theme', 'violet')
    appliquerThemeMemorise()
    expect(document.documentElement.dataset.theme).toBeUndefined()
  })

  it('survit à un stockage refusé (navigation privée, stockage bloqué)', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('refusé') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('refusé') })
    appliquerThemeMemorise()
    render(<ThemeToggle />)
    fireEvent.click(bouton('Clair'))
    expect(document.documentElement.dataset.theme).toBe('light')
  })
})

describe('le thème choisi se voit (C-19)', () => {
  it('porte la classe que la feuille de style met en évidence', () => {
    // La bascule posait « active », qu'aucune règle ne stylisait : le thème
    // choisi ne se distinguait des deux autres que pour un lecteur d'écran.
    const { container } = render(<ThemeToggle />)
    const choisi = container.querySelector('button[aria-pressed="true"]')
    expect(choisi).not.toBeNull()
    expect(choisi!.classList.contains('is-active')).toBe(true)
    expect(container.querySelectorAll('.active').length).toBe(0)
  })
})
