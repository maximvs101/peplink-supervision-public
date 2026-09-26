import { describe, expect, it } from 'vitest'

import { compareWanIds, wanOrder } from './wanOrder'

describe('tri des identifiants de WAN', () => {
  it('trie les identifiants numériques par valeur', () => {
    // Un châssis MAX HD4 MBX en déclare onze : « 10 » ne doit pas précéder « 2 ».
    expect(['10', '2', '1', '11', '9'].sort(compareWanIds)).toEqual(['1', '2', '9', '10', '11'])
  })

  it('garde l’ordre alphabétique pour les identifiants du simulateur', () => {
    expect(['wifi-1', 'cellular-2', 'cellular-1'].sort(compareWanIds)).toEqual([
      'cellular-1', 'cellular-2', 'wifi-1',
    ])
  })

  it('place les identifiants numériques avant les autres', () => {
    expect(['wifi-1', '4'].sort(compareWanIds)).toEqual(['4', 'wifi-1'])
  })

  it('classe chaque identifiant dans le bon groupe', () => {
    expect(wanOrder('7')).toEqual([0, 7, ''])
    expect(wanOrder('cellular-1')).toEqual([1, 0, 'cellular-1'])
  })
})
