import { describe, expect, it } from 'vitest'

import type { Timeline, TimelineSlice } from '../types'
import { bondTogether, fondDeTranche, tranchePorte } from './Band'
import { QUALITY_COLOR } from '../lib/quality'

const tranche = (over: Partial<TimelineSlice> = {}): TimelineSlice => ({
  t: 0, samples: 10, up: 10, carrying: 10, tunnel_known: 10, sinr_worst: 12, rsrp_worst: -88, repair: 0, packets: 40, disabled: 0,
  ...over,
})

/** `wans` donne, pour chaque lien, ses tranches. Une tranche dure 60 s. */
const bande = (wans: Record<string, TimelineSlice[]>): Timeline => ({
  router_id: 'mbx', started_at: '2026-09-11T12:00:00Z', ended_at: '2026-09-11T14:00:00Z', capped: false,
  slice_count: 120, slice_seconds: 60,
  lanes: Object.entries(wans).map(([wan_id, slices]) => ({ wan_id, slices })),
})

const pleine = (t: number) => tranche({ t })
const vide = (t: number) => tranche({ t, carrying: 0 })

/**
 * Trois encodages visuels ont échoué à se faire comprendre : une surface pleine
 * qui dépensait toute son encre à dire « rien à signaler », un dessin en creux
 * invisible quand tout allait bien, un trait dont l'axe n'était écrit nulle
 * part. Le tort était la FORME : l'information est rare, épisodique, et tient en
 * une phrase — à quelle heure, pendant combien de temps, combien de liens.
 */
describe('ce que les liens ont fait ensemble', () => {
  it('ne signale rien quand ils y étaient tous', () => {
    const t = bande({ '4': [pleine(0), pleine(1)], '5': [pleine(0), pleine(1)] })
    expect(bondTogether(t, 2)!.fléchissements).toEqual([])
  })

  it('date le fléchissement, le chronomètre et dit combien il en restait', () => {
    // Trois tranches de 60 s à partir de la deuxième : la durée COUVRE la
    // dernière tranche — d'une tranche à elle-même il s'est écoulé un pas, pas
    // zéro seconde.
    const t = bande({
      '4': [pleine(0), pleine(1), pleine(2), pleine(3), pleine(4)],
      '5': [pleine(0), vide(1), vide(2), vide(3), pleine(4)],
    })
    const f = bondTogether(t, 2)!.fléchissements
    expect(f).toHaveLength(1)
    expect(f[0].at).toBe('2026-09-11T12:01:00.000Z')
    expect(f[0].seconds).toBe(180)
    expect(f[0].worst).toBe(1)
  })

  it('retient le PIRE compte, et non le premier', () => {
    const t = bande({
      '4': [pleine(0), pleine(1), vide(2), pleine(3)],
      '5': [pleine(0), vide(1), vide(2), pleine(3)],
      '6': [pleine(0), pleine(1), pleine(2), pleine(3)],
    })
    const f = bondTogether(t, 3)!.fléchissements
    expect(f).toHaveLength(1)
    expect(f[0].worst).toBe(1)
    expect(f[0].seconds).toBe(120)
  })

  it('sépare deux épisodes au lieu de les souder', () => {
    const t = bande({
      '4': [pleine(0), pleine(1), pleine(2), pleine(3), pleine(4)],
      '5': [vide(0), pleine(1), pleine(2), pleine(3), vide(4)],
    })
    const f = bondTogether(t, 2)!.fléchissements
    expect(f).toHaveLength(2)
    expect(f.map((x) => x.seconds)).toEqual([60, 60])
  })

  it('ne soude pas par-dessus une tranche non relue', () => {
    // Entre les deux, on ne regardait pas. Souder inventerait une durée de trois
    // minutes là où deux épisodes d'une minute ont été observés.
    const t = bande({
      '4': [pleine(0), vide(1), vide(3)],
      '5': [pleine(0), pleine(1), pleine(3)],
    })
    const f = bondTogether(t, 2)!.fléchissements
    expect(f).toHaveLength(2)
    expect(f.every((x) => x.seconds === 60)).toBe(true)
  })

  it('n’invente pas un fléchissement à partir d’un silence', () => {
    // Une tranche que le second lien n'a pas renseignée ne dit RIEN de lui. La
    // compter comme un lien manquant ferait d'un silence du superviseur une
    // perte de redondance.
    const t = bande({ '4': [pleine(0), pleine(1)], '5': [pleine(0)] })
    const r = bondTogether(t, 2)!
    expect(r.fléchissements).toEqual([])
    expect(r.observé).toBeCloseTo(1 / 120, 5)
  })

  it('distingue le bond entièrement tombé', () => {
    const t = bande({ '4': [vide(0)], '5': [vide(0)] })
    expect(bondTogether(t, 2)!.fléchissements[0].worst).toBe(0)
  })

  it('ne rend rien sans bande relue', () => {
    expect(bondTogether(null, 5)).toBeNull()
  })
})

describe('une sonde absente n’est pas un lien écarté', () => {
  it('juge le port sur les relevés où le tunnel a été sondé', () => {
    // 36 % de cycles non sondés mesurés : dix relevés, quatre sondes, quatre
    // portants. Divisé par dix, 0,4 — « hors du bond » pour un lien qui portait
    // à chaque regard.
    expect(tranchePorte(tranche({ samples: 10, tunnel_known: 4, carrying: 4 }))).toBe(true)
    expect(tranchePorte(tranche({ samples: 10, tunnel_known: 4, carrying: 1 }))).toBe(false)
    // Sans une seule sonde : on ne sait pas.
    expect(tranchePorte(tranche({ samples: 10, tunnel_known: 0, carrying: 0 }))).toBeNull()
  })

  it('ne compte pas la tranche mal sondée comme un fléchissement', () => {
    const t = bande({
      '4': [tranche({ t: 0, samples: 10, tunnel_known: 4, carrying: 4 })],
      '5': [tranche({ t: 0, samples: 10, tunnel_known: 4, carrying: 4 })],
    })
    expect(bondTogether(t, 2)!.fléchissements).toEqual([])
  })
})

/**
 * C-20 : un WAN désactivé laisse un relevé d'adieu, `connected = 0`. La bande
 * le peignait en panne — le choix de l'exploitant dessiné comme une défaillance.
 */
describe('le fond d’une tranche', () => {
  it('est une panne quand le lien est tombé', () => {
    expect(fondDeTranche(tranche({ up: 0 }), 'bon')).toEqual({ fond: QUALITY_COLOR.faible, tombe: true })
  })

  it('reste neutre quand les seuls relevés tombés sont des désactivations', () => {
    expect(fondDeTranche(tranche({ samples: 1, up: 0, disabled: 1 }), 'bon'))
      .toEqual({ fond: QUALITY_COLOR.inconnu, tombe: false })
  })

  it('reste une panne si un relevé tombé n’est PAS une désactivation', () => {
    expect(fondDeTranche(tranche({ samples: 3, up: 0, disabled: 1 }), 'bon').tombe).toBe(true)
  })

  it('suit la qualité quand le lien était debout', () => {
    expect(fondDeTranche(tranche({ samples: 3, up: 2, disabled: 1 }), 'moyen'))
      .toEqual({ fond: QUALITY_COLOR.moyen, tombe: false })
  })
})
