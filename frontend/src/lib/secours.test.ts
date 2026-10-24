import { describe, expect, it } from 'vitest'

import { wanRealiste } from '../test/factories'
import { enAttente, etiquettePriorite } from './secours'

/**
 * Sur un HD2 réel, la SIM 5 (priorité 2) portait TOUT le trafic — le WAN 1 de
 * priorité 1 n'avait pas de câble — et l'écran l'étiquetait « secours · P2 ».
 * La priorité seule ne dit pas qu'un lien attend : il faut que le routeur le
 * dise, ou qu'un lien de priorité plus haute soit effectivement connecté.
 */
describe('un lien en attente', () => {
  const sim5 = wanRealiste({ wan_id: '5', priority: 2, connected: true, status_message: 'Connected' })

  it('ne l’est pas quand le lien de priorité 1 n’est pas connecté', () => {
    const wan1 = wanRealiste({ wan_id: '1', priority: 1, connected: false, status_message: 'No Cable Detected' })
    expect(enAttente(sim5, [wan1, sim5])).toBe(false)
    expect(etiquettePriorite(sim5, [wan1, sim5])).toBe('P2')
  })

  it('l’est quand un lien de priorité plus haute est connecté', () => {
    const wan1 = wanRealiste({ wan_id: '1', priority: 1, connected: true })
    expect(enAttente(sim5, [wan1, sim5])).toBe(true)
    expect(etiquettePriorite(sim5, [wan1, sim5])).toBe('secours · P2')
  })

  it('l’est quand le routeur le dit (« Standby »), quel que soit le reste', () => {
    const attente = wanRealiste({ wan_id: '5', priority: 2, connected: true, status_message: ' Standby ' })
    expect(enAttente(attente, [attente])).toBe(true)
  })

  it('ne compte pas un lien désactivé comme lien de priorité plus haute', () => {
    const wan1 = wanRealiste({ wan_id: '1', priority: 1, connected: true, disabled: true })
    expect(enAttente(sim5, [wan1, sim5])).toBe(false)
  })

  it('n’étiquette rien en priorité 1 ou inconnue', () => {
    const p1 = wanRealiste({ priority: 1 })
    const nulle = wanRealiste({ priority: null })
    expect(etiquettePriorite(p1, [p1])).toBeNull()
    expect(etiquettePriorite(nulle, [nulle])).toBeNull()
  })
})
