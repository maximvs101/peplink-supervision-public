import { describe, expect, it } from 'vitest'

import { aSignaler, duree, fraicheurNeuve, phraseFraicheur } from './fraicheur'

describe('fraîcheur d’une partie', () => {
  const lue = (lueA: number, echec: string | null = null) => ({ ...fraicheurNeuve(30_000), lueA, echec })

  it('se tait tant que la donnée a moins de deux périodes et qu’aucun échec n’est dit', () => {
    expect(aSignaler(lue(0), 60_000)).toBe(false)
    expect(phraseFraicheur('Bande', lue(0), 60_000)).toBeNull()
    // Pas même pendant la toute première lecture : rien d'ancien n'est affiché.
    expect(aSignaler(fraicheurNeuve(30_000), 10 * 60_000)).toBe(false)
  })

  it('parle au-delà de deux périodes, ou dès un échec', () => {
    expect(phraseFraicheur('Bande', lue(0), 61_000))
      .toBe('Bande : dernière réponse il y a 61 s. Ce qui est affiché date de cette réponse.')
    expect(phraseFraicheur('Journal', lue(0, '503'), 5_000))
      .toBe('Journal : dernière réponse il y a 5 s — dernière relecture en échec : 503. '
        + 'Ce qui est affiché date de cette réponse.')
    expect(phraseFraicheur('Journal', { ...fraicheurNeuve(30_000), echec: 'ECONNRESET' }, 0))
      .toBe('Journal : aucune réponse obtenue — dernière relecture en échec : ECONNRESET.')
  })

  it('dit l’âge à l’ordre de grandeur utile', () => {
    expect(duree(45_400)).toBe('45 s')
    expect(duree(119_000)).toBe('119 s')
    expect(duree(3 * 60_000 + 20_000)).toBe('3 min')
    expect(duree(125 * 60_000)).toBe('2 h 05')
    // Une horloge qui recule ne produit pas d'âge négatif.
    expect(duree(-5_000)).toBe('0 s')
  })
})
