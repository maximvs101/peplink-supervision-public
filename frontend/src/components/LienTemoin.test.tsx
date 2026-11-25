import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { LienTemoin } from './LienTemoin'

describe('le lien vers le témoin Ethernet', () => {
  it('mène à l’adresse publiée par le serveur', () => {
    render(<LienTemoin adresse="http://127.0.0.1:8010" />)
    expect(screen.getByRole('link', { name: 'Témoin Ethernet' }).getAttribute('href'))
      .toBe('http://127.0.0.1:8010')
  })

  it('n’existe pas quand le témoin ne tourne pas à côté', () => {
    for (const adresse of [null, undefined, '']) {
      const { container } = render(<LienTemoin adresse={adresse} />)
      expect(container.innerHTML).toBe('')
    }
  })
})
