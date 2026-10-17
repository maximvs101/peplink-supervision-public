import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Alert } from '../types'
import { Veilleur } from './prevenir'

const alerte = (over: Partial<Alert> = {}): Alert => ({
  kind: 'hors du bond', wan_id: '4', since: '2026-10-17T12:00:00Z', until: null,
  detail: 'FAILURE', sustained_s: 60, ...over,
})

let notifications: { titre: string; corps?: string }[]
let cache = true

beforeEach(() => {
  notifications = []
  cache = true
  class FausseNotification {
    static permission = 'granted'
    constructor(titre: string, options?: { body?: string }) {
      notifications.push({ titre, corps: options?.body })
    }
  }
  vi.stubGlobal('Notification', FausseNotification)
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => (cache ? 'hidden' : 'visible'))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('prévenir d’une alerte (A-2)', () => {
  it('ne dit rien de ce qui était là à l’ouverture', () => {
    // Ouvrir la page sur une alerte déjà connue ne doit pas sonner : on la voit.
    const v = new Veilleur('notification', 'HD2')
    v.observer([alerte()])
    expect(notifications).toEqual([])
  })

  it('prévient d’une alerte NOUVELLE, onglet caché, une seule fois', () => {
    const v = new Veilleur('notification', 'HD2')
    v.observer([])
    v.observer([alerte()])
    v.observer([alerte()])
    expect(notifications).toHaveLength(1)
    expect(notifications[0].titre).toMatch(/HD2/)
    expect(notifications[0].corps).toMatch(/Lien 4 hors du bond/)
  })

  it('se tait onglet visible, en mode muet, et sur une alerte résolue', () => {
    cache = false
    const visible = new Veilleur('notification', 'HD2')
    visible.observer([])
    visible.observer([alerte()])
    cache = true
    const muet = new Veilleur('muet', 'HD2')
    muet.observer([])
    muet.observer([alerte({ since: '2026-10-17T12:05:00Z' })])
    const resolue = new Veilleur('notification', 'HD2')
    resolue.observer([])
    resolue.observer([alerte({ until: '2026-10-17T12:06:00Z' })])
    expect(notifications).toEqual([])
  })

  it('ne prévient pas sans l’autorisation du navigateur', () => {
    (globalThis.Notification as unknown as { permission: string }).permission = 'denied'
    const v = new Veilleur('notification', 'HD2')
    v.observer([])
    v.observer([alerte()])
    expect(notifications).toEqual([])
  })
})
