import { describe, expect, it } from 'vitest'

import type { Timeline, TimelineSlice } from '../types'
import { FENETRE_TEMOIN_S, bondRedundancy, paquetsParSecondeRecents } from './redundancy'

const tranche = (over: Partial<TimelineSlice> = {}): TimelineSlice => ({
  t: 0, samples: 1, up: 1, carrying: 1, tunnel_known: 1, sinr_worst: 12, rsrp_worst: -88,
  repair: 0, packets: 0, disabled: 0,
  ...over,
})

const bande = (slices: TimelineSlice[]): Timeline => ({
  router_id: 'mbx',
  started_at: '2026-09-11T11:34:22Z',
  ended_at: '2026-09-11T19:34:22Z', capped: false,
  slice_count: 200,
  slice_seconds: 144,
  lanes: [{ wan_id: '4', slices }],
})

describe('redondance du bond', () => {
  it('conclut à une redondance ÉTEINTE quand du trafic est passé sans réparation', () => {
    // Mesuré sur la base complète du matériel de référence : 26 629 paquets
    // montants comptés, zéro paquet de réparation — y compris sur un intervalle
    // où 0,51 % de perte montante a été relevée. Si la correction d'erreur était
    // active elle émettrait en permanence ; si la duplication l'était, la perte
    // aurait été reconstituée.
    const r = bondRedundancy(bande([
      tranche({ t: 0, packets: 12000, repair: 0 }),
      tranche({ t: 1, packets: 14629, repair: 0 }),
    ]))
    expect(r.state).toBe('off')
    expect(r.packets).toBe(26629)
  })

  it('n’affirme rien tant que le trafic ne l’établit pas', () => {
    // Un tunnel au repos n'échange qu'un keepalive de quelques paquets : n'y voir
    // aucune réparation ne dit RIEN du profil. C'est la règle de tout ce projet —
    // une absence de mesure n'est pas une mesure.
    const r = bondRedundancy(bande([tranche({ packets: 40, repair: 0 })]))
    expect(r.state).toBe('unknown')
  })

  it('n’affirme rien non plus sans bande relue', () => {
    expect(bondRedundancy(null).state).toBe('unknown')
  })

  it('chiffre la part de capacité dépensée quand la redondance tourne', () => {
    // 13 % est le surcoût de la correction d'erreur à son réglage le plus bas :
    // c'est l'ordre de grandeur qu'on doit retrouver.
    const r = bondRedundancy(bande([tranche({ packets: 87000, repair: 13000 })]))
    expect(r.state).toBe('on')
    if (r.state !== 'on') return
    expect(r.share).toBeCloseTo(0.13, 2)
    expect(r.repair).toBe(13000)
  })

  it('additionne tous les liens, pas seulement le premier', () => {
    const t = bande([tranche({ packets: 600, repair: 0 })])
    t.lanes.push({ wan_id: '5', slices: [tranche({ packets: 600, repair: 0 })] })
    // 1 200 paquets au total : au-dessus du seuil, donc concluant — alors qu'un
    // seul lien ne l'aurait pas été.
    expect(bondRedundancy(t).state).toBe('off')
  })
})

describe('un silence ne vaut que si quelqu’un a parlé', () => {
  it('ne conclut RIEN quand le compteur de réparation n’a jamais été renseigné', () => {
    // Le serveur sommait `COALESCE(repair_sent, 0)` : une colonne que le
    // firmware n'a jamais remplie rendait « zéro paquet réparé », et c'est de
    // cette somme que sortait « la redondance du profil est éteinte ». Mesuré :
    // 720 relevés du 11 septembre portent un `packets_up` mesuré et un
    // `repair_sent` absent.
    const t = bande([
      tranche({ t: 0, packets: 12000, repair: null }),
      tranche({ t: 1, packets: 14629, repair: null }),
    ])
    expect(bondRedundancy(t).state).toBe('unknown')
    expect(bondRedundancy(t).packets).toBe(26629)
  })

  it('conclut dès qu’un seul relevé a renseigné le compteur', () => {
    // Contre-épreuve : le compteur a parlé, il a dit zéro, et ce zéro-là est un
    // fait — c'est toute la différence.
    const t = bande([
      tranche({ t: 0, packets: 12000, repair: 0 }),
      tranche({ t: 1, packets: 14629, repair: null }),
    ])
    expect(bondRedundancy(t).state).toBe('off')
  })
})

describe('le témoin de trafic regarde les dernières minutes', () => {
  // Fenêtre de huit heures en 200 tranches de 144 s : dix minutes = 5 tranches,
  // soit les indices 195 à 199.
  it('voit le trafic arriver sans attendre que la moyenne de la session le rattrape', () => {
    // Σ paquets / durée totale : six heures de repos puis du trafic, et il
    // fallait vingt-huit minutes pour franchir dix paquets par seconde.
    const repos = Array.from({ length: 195 }, (_, t) => tranche({ t, packets: 190 }))  // 1,3 pps
    const charge = Array.from({ length: 5 }, (_, i) => tranche({ t: 195 + i, packets: 17_600 }))  // 122 pps
    const pps = paquetsParSecondeRecents(bande([...repos, ...charge]))
    expect(pps).toBeCloseTo(122.2, 0)
    // La moyenne de toute la fenêtre, elle, n'y était pas encore.
    expect(bondRedundancy(bande([...repos, ...charge])).packets / (200 * 144)).toBeLessThan(10)
  })

  it('voit le trafic partir', () => {
    const charge = Array.from({ length: 195 }, (_, t) => tranche({ t, packets: 17_600 }))
    const repos = Array.from({ length: 5 }, (_, i) => tranche({ t: 195 + i, packets: 190 }))
    expect(paquetsParSecondeRecents(bande([...charge, ...repos]))).toBeCloseTo(1.3, 1)
  })

  it('divise par le temps OBSERVÉ, pas par la fenêtre', () => {
    // Deux tranches renseignées sur cinq : le superviseur ne regardait pas les
    // trois autres, et elles ne comptent pas comme du silence.
    const deux = [tranche({ t: 198, packets: 1440 }), tranche({ t: 199, packets: 1440 })]
    expect(paquetsParSecondeRecents(bande(deux))).toBeCloseTo(10, 5)
    // Un compteur jamais renseigné ne dit rien — et « rien » n'est pas zéro
    // (C-2) : un tunnel tombé n'a plus de compteur, et le zéro qu'on en tirait
    // faisait dire au bandeau que le bond ne porte pas le trafic.
    expect(paquetsParSecondeRecents(bande([tranche({ t: 199, packets: null })]))).toBeNull()
    expect(paquetsParSecondeRecents(null)).toBeNull()
    // Un zéro MESURÉ, lui, reste un zéro.
    expect(paquetsParSecondeRecents(bande([tranche({ t: 199, packets: 0 })]))).toBe(0)
  })

  it('additionne les liens d’une même tranche sans compter le temps deux fois', () => {
    const bande2: Timeline = {
      ...bande([tranche({ t: 199, packets: 720 })]),
      lanes: [
        { wan_id: '4', slices: [tranche({ t: 199, packets: 720 })] },
        { wan_id: '5', slices: [tranche({ t: 199, packets: 720 })] },
      ],
    }
    expect(paquetsParSecondeRecents(bande2)).toBeCloseTo(10, 5)
  })

  it('couvre au moins une tranche, même plus large que la fenêtre', () => {
    const large: Timeline = { ...bande([tranche({ t: 9, packets: 36_000 })]), slice_count: 10, slice_seconds: 3600 }
    expect(FENETRE_TEMOIN_S).toBeLessThan(3600)
    expect(paquetsParSecondeRecents(large)).toBeCloseTo(10, 5)
  })
})

describe('la dernière tranche compte pour sa durée écoulée (C-39)', () => {
  // Cent secondes de fenêtre, une tranche au plancher de la cadence (5,4 s) :
  // dix-neuf tranches, dont la dernière ne dure que 100 − 18 × 5,4 = 2,8 s.
  const courte = (slices: TimelineSlice[]): Timeline => ({
    router_id: 'mbx', started_at: '2026-09-11T12:00:00Z', ended_at: '2026-09-11T12:01:40Z',
    capped: false, slice_count: 19, slice_seconds: 5.4, lanes: [{ wan_id: '4', slices }],
  })

  it('ne dilue pas ses paquets sur une tranche entière', () => {
    // 28 paquets en 2,8 s : dix par seconde, pas 5,2 — juste sous le seuil.
    expect(paquetsParSecondeRecents(courte([tranche({ t: 18, packets: 28 })]))).toBeCloseTo(10, 5)
  })

  it('laisse entières les tranches déjà écoulées', () => {
    const deux = [tranche({ t: 17, packets: 54 }), tranche({ t: 18, packets: 28 })]
    expect(paquetsParSecondeRecents(courte(deux))).toBeCloseTo(82 / 8.2, 5)
  })
})

describe('une redondance active se juge sur assez de trafic (C-39)', () => {
  it('ne publie pas une part calculée sur huit paquets', () => {
    // 3 paquets de réparation pour 5 transportés : l'écran titrait « 37,5 % de
    // la capacité part en redondance » — un keepalive, pas un fait.
    const t = bande([tranche({ t: 0, packets: 5, repair: 3 })])
    expect(bondRedundancy(t).state).toBe('unknown')
  })

  it('la publie dès que le trafic est concluant', () => {
    const t = bande([tranche({ t: 0, packets: 900, repair: 150 })])
    const r = bondRedundancy(t)
    expect(r.state).toBe('on')
  })
})
