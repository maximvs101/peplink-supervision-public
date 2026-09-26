import { describe, expect, it } from 'vitest'

import type { Session } from '../types'
import { debutObserve, libelleFenetre } from './session'

const session = (over: Partial<Session> = {}): Session => ({
  id: 6, router_id: 'hd2', started_at: '2026-09-19T08:35:41Z',
  first_seen: '2026-09-19T10:53:25Z', last_seen: '2026-09-19T13:45:33Z',
  samples: 5322, label: null, ...over,
})

describe('la borne gauche de ce qui se dessine', () => {
  it('est le premier relevé, et l’allumage seulement faute de relevé', () => {
    expect(debutObserve(session())).toBe('2026-09-19T10:53:25Z')
    expect(debutObserve(session({ first_seen: null }))).toBe('2026-09-19T08:35:41Z')
  })
})

describe('le libellé de la fenêtre', () => {
  it('dit les deux heures quand la supervision a commencé bien après l’allumage', () => {
    // L'information que l'ancien axe portait en pixels vides — le véhicule
    // était sous tension depuis 08:35 — passe dans les mots.
    const libelle = libelleFenetre(session())
    expect(libelle).toMatch(/^Depuis \d{2}:\d{2} — sous tension depuis \d{2}:\d{2}$/)
    expect(libelle).not.toBe('Depuis la mise sous tension')
  })
  it('reste « depuis la mise sous tension » à un cycle près, ou sans relevé', () => {
    expect(libelleFenetre(session({ first_seen: '2026-09-19T08:36:30Z' })))
      .toBe('Depuis la mise sous tension')
    expect(libelleFenetre(session({ first_seen: null }))).toBe('Depuis la mise sous tension')
  })
})
