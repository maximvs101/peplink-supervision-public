import { describe, expect, it } from 'vitest'

import { causeDuTunnel, echeanceLicence, motDuPair } from './bondProfile'
import type { BondProfile } from '../types'

/** R-1.5 : ce qui tient le tunnel à terre, dit avec les mots du routeur. */

const profil = (over: Partial<BondProfile> = {}): BondProfile => ({
  id: '4', name: 'PROFIL-B', status: 'START', peers_connected: 0, cloud: false, ...over,
})
const licence = (over: { expires?: string | null; suspended?: boolean | null } = {}) => ({
  sfc_licensed: true, sfc_suspended: over.suspended ?? false,
  sfc_expires_at: over.expires === undefined ? '2027-01-01T00:00:00Z' : over.expires,
})
const MAINTENANT = Date.parse('2026-09-24T12:00:00Z')

describe('le mot du pair', () => {
  it('traduit ce qui a été observé, et rend le reste tel quel', () => {
    expect(motDuPair('START')).toBe('tentative en cours (START)')
    expect(motDuPair('CONNECTED')).toBe('connecté')
    expect(motDuPair('AUTH_FAIL')).toBe('AUTH_FAIL')
    expect(motDuPair(null)).toBe('aucun pair connecté')
  })
})

describe('la cause d’un tunnel à terre', () => {
  it('nomme le profil et ce que fait le routeur', () => {
    expect(causeDuTunnel([profil()], null, MAINTENANT))
      .toBe('profil PROFIL-B : tentative en cours (START), 0 pair connecté')
  })

  it('se tait dès qu’un profil tient : la panne est alors ailleurs (C-16)', () => {
    // Ce test attendait l'inverse : profil PROFIL-A connecté, ses liens tous en
    // WAN_DOWN, et un profil secondaire PROFIL-B en START. Le bandeau accusait
    // « profil PROFIL-B : tentative en cours (START) » — un profil
    // étranger à la panne — au lieu de la cause unanime des liens, qu'il
    // écrasait. Un profil connecté prouve que le pair est joignable : la
    // cause se lit sur les liens.
    const cause = causeDuTunnel(
      [profil({ id: '1', name: 'PROFIL-A', status: 'CONNECTED', peers_connected: 1 }), profil()],
      null, MAINTENANT)
    expect(cause).toBeNull()
  })

  it('accuse la licence Cloud d’un profil Cloud, jamais d’un autre', () => {
    const cloud = profil({ id: '60000', name: 'PROFIL-CLOUD', cloud: true })
    expect(causeDuTunnel([cloud], licence({ expires: '2023-01-19T00:00:00Z' }), MAINTENANT))
      .toBe('profil PROFIL-CLOUD : licence SpeedFusion Cloud échue le 19/01/2023')
    expect(causeDuTunnel([cloud], licence({ suspended: true }), MAINTENANT))
      .toBe('profil PROFIL-CLOUD : licence SpeedFusion Cloud suspendue')
    // Le FusionHub d'un exploitant ne dépend pas de la licence Cloud.
    expect(causeDuTunnel([profil()], licence({ expires: '2023-01-19T00:00:00Z' }), MAINTENANT))
      .toBe('profil PROFIL-B : tentative en cours (START), 0 pair connecté')
    // Une licence non souscrite n'est pas « échue » : rien à accuser.
    expect(causeDuTunnel([cloud], { ...licence({ expires: '2023-01-19T00:00:00Z' }), sfc_licensed: false }, MAINTENANT))
      .toBe('profil PROFIL-CLOUD : tentative en cours (START), 0 pair connecté')
    // Une licence valide ne fait pas une cause : on revient au mot du routeur.
    expect(causeDuTunnel([cloud], licence(), MAINTENANT))
      .toBe('profil PROFIL-CLOUD : tentative en cours (START), 0 pair connecté')
  })

  it('rend un mot inconnu tel quel, et rien sans profil', () => {
    expect(causeDuTunnel([profil({ status: 'STOPPED', peers_connected: null, name: null })], null, MAINTENANT))
      .toBe('profil 4 : état STOPPED')
    expect(causeDuTunnel([], null, MAINTENANT)).toBeNull()
    expect(causeDuTunnel([profil({ status: 'CONNECTED' })], null, MAINTENANT)).toBeNull()
  })
})

describe('l’échéance de la licence', () => {
  it('ne prévient que dans les 30 derniers jours', () => {
    const a = (jours: number) => new Date(MAINTENANT + jours * 86_400_000).toISOString()
    expect(echeanceLicence(licence({ expires: a(31) }), MAINTENANT)).toBeNull()
    expect(echeanceLicence(licence({ expires: a(30) }), MAINTENANT)).toEqual({ etat: 'proche', jours: 30 })
    expect(echeanceLicence(licence({ expires: a(0.5) }), MAINTENANT)).toEqual({ etat: 'proche', jours: 1 })
    expect(echeanceLicence(licence({ expires: a(-1) }), MAINTENANT)).toEqual({ etat: 'echue', jours: 0 })
    expect(echeanceLicence(licence({ expires: a(0) }), MAINTENANT)).toEqual({ etat: 'echue', jours: 0 })
  })

  it('ne dit rien sans licence ni échéance', () => {
    expect(echeanceLicence(null, MAINTENANT)).toBeNull()
    expect(echeanceLicence(licence({ expires: null }), MAINTENANT)).toBeNull()
    expect(echeanceLicence({ ...licence({ expires: '2023-01-19T00:00:00Z' }), sfc_licensed: false }, MAINTENANT))
      .toBeNull()
  })
})
