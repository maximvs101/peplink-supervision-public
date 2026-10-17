import { fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PrevenirToggle } from './PrevenirToggle'

afterEach(() => {
  vi.unstubAllGlobals()
  try { localStorage.clear() } catch { /* stockage refusé */ }
})

describe('le son retenu, après un rechargement (revue du 17/10)', () => {
  it('se réarme au premier geste sur la page', () => {
    // Le mode « + son » était retenu, mais le contexte audio ne naissait qu'au
    // clic dans la liste : après un rechargement, plus aucun son, sans le dire.
    const crees: unknown[] = []
    class FauxAudio {
      constructor() { crees.push(this) }
      resume() { return Promise.resolve() }
    }
    vi.stubGlobal('AudioContext', FauxAudio)
    localStorage.setItem('prevenir', 'son')
    render(<PrevenirToggle />)
    expect(crees).toHaveLength(0)
    fireEvent.pointerDown(document.body)
    expect(crees).toHaveLength(1)
  })
})
