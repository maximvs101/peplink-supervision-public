import { describe, expect, it } from 'vitest'

import { hoteCanonique, lienWan, sansTls } from './routes'

describe('l’identité d’un hôte', () => {
  it('ne dépend ni du schéma, ni de la casse, ni du slash final', () => {
    // `10.10.1.1`, `http://10.10.1.1` et `http://10.10.1.1/` désignent le même
    // équipement — et trois comparaisons sur l'hôte brut disaient le contraire.
    expect(hoteCanonique('10.10.1.1')).toBe('10.10.1.1')
    expect(hoteCanonique('http://10.10.1.1')).toBe('10.10.1.1')
    expect(hoteCanonique('HTTPS://Routeur.Local/')).toBe('routeur.local')
    expect(hoteCanonique(' http://10.10.1.1// ')).toBe('10.10.1.1')
  })

  it('rend null pour rien, jamais une chaîne vide', () => {
    expect(hoteCanonique(null)).toBeNull()
    expect(hoteCanonique(undefined)).toBeNull()
    expect(hoteCanonique('')).toBeNull()
    expect(hoteCanonique('http://')).toBeNull()
  })
})

describe('un hôte en clair', () => {
  it('n’est reconnu que sur http://, pas sur https:// ni sur un hôte nu', () => {
    // `sansTls` inversé — l'avertissement « mot de passe en clair » affiché
    // pour HTTPS — laissait 395 tests verts.
    expect(sansTls('http://10.10.1.1')).toBe(true)
    expect(sansTls(' HTTP://10.10.1.1 ')).toBe(true)
    expect(sansTls('https://10.10.1.1')).toBe(false)
    expect(sansTls('10.10.1.1')).toBe(false)
    expect(sansTls(null)).toBe(false)
  })
})

describe('le lien d’un WAN', () => {
  it('encode les identifiants tels quels', () => {
    expect(lienWan('mbx', '4')).toBe('/e/mbx/wan/4')
    expect(lienWan('a/b', 'cellular 1')).toBe('/e/a%2Fb/wan/cellular%201')
  })
})
