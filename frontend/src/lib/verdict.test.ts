import { describe, expect, it } from 'vitest'

import type { RadioSample, TunnelLink, WanRadioStatus } from '../types'
import { overallQuality } from './quality'
import { bondRole } from './bondRole'
import { paquetsParSecondeRecents } from './redundancy'
import { causeScope } from '../test/factories'
import {
  LATENCY_ABSOLUTE_MS,
  LOSS_MIN_PACKETS,
  STALE_AFTER_SECONDS,
  accord,
  ecarteDuBond,
  latencyOutliers,
  linkVerdict,
  reportableLoss,
  seuilUtile,
  smoothVerdict,
  staleAfterSeconds,
  type Verdict,
} from './verdict'

function sample(wan: Partial<WanRadioStatus>): RadioSample {
  return {
    router_id: 'r', router_name: 'R',
    ts: '2026-08-30T14:00:00Z',
    site_lat: null, site_lon: null, cell_changes: 0, disconnects: 0,
    wan: {
      kind: 'cellular', wan_id: '4', wan_name: 'SIM', connected: true,
      operator: 'Orange', operator_name: 'Orange', plmn: '208-01', rat: 'LTE-A', band: 'B3',
      earfcn: null, pci: null, cell_id: null, tac: null, enb_id: null, sector_id: null,
      rsrp: -88, rsrq: -11, sinr: 15, rssi: null, signal_bars: 4,
      bands: [], sim_slots: [], wifi: null, path_latency_ms: null, path_latency_recent_ms: null, path_latency_at: null, modem_temperature_c: null, modem_temperature_at: null, ip: null, priority: 1, tunnel: null,
      tx_kbps: null, rx_kbps: null, uptime_s: 1, status_message: null, disabled: false,
      roaming_status: null, modem_vendor: null, modem_model: null, modem_firmware: null,
      mtu: null, carrier_aggregation: null, network_mode: null, allowance: null,
      period_rx_mb: null, period_tx_mb: null,
      ...wan,
    } satisfies WanRadioStatus,
  }
}

/** Lien de tunnel tel que le backend le sérialise : `in_bond` est un champ
 *  calculé, qui vaut `state == "ACTIVE"`. Le construire ici comme là-bas évite
 *  d'écrire des cas de test qui ne peuvent pas se produire sur le matériel.
 *
 *  Les compteurs de paquets sont nuls par défaut, comme au premier relevé d'un
 *  tunnel : sans référence, le backend ne publie ni taux ni dénominateur. */
function tunnel(
  state: string | null,
  rtt_ms: number | null = null,
  extra: Partial<TunnelLink> = {},
): TunnelLink {
  return {
    state,
    in_bond: state === 'ACTIVE',
    cause_scope: causeScope(state, state === 'ACTIVE'),
    rtt_ms,
    loss_percent: null,
    out_of_order_percent: null,
    loss_percent_up: null,
    loss_percent_down: null,
    out_of_order_percent_up: null,
    out_of_order_percent_down: null,
    packets_up: null,
    packets_down: null,
    tx_kbps: null, rx_kbps: null,
    repair_sent: null, repair_used: null,
  cutoff_ms: null, beyond_cutoff: false, peer_state: null,
    ...extra,
  }
}

/** Les quatre SIM du relevé du 30 août à 13:51:42, dans l'ordre des WAN.
 *
 *  Toutes ACTIVE, toutes connectées, bandeau vert à l'écran — et le WAN 6 à
 *  1 929 ms qui empoisonnait la remontée avec le MEILLEUR SINR des quatre. */
const RELEVE_30_AOUT: { wan: string; rtt: number; sinr: number; rsrp: number }[] = [
  { wan: '4', rtt: 34, sinr: 12.0, rsrp: -92 },
  { wan: '5', rtt: 25, sinr: 12.2, rsrp: -91 },
  { wan: '6', rtt: 1929, sinr: 13.8, rsrp: -91 },
  { wan: '7', rtt: 33, sinr: 11.9, rsrp: -92 },
]

const releve30Aout = () =>
  RELEVE_30_AOUT.map(({ wan, rtt, sinr, rsrp }) => {
    const s = sample({ wan_id: wan, sinr, rsrp, rsrq: -11 })
    s.wan.tunnel = tunnel('ACTIVE', rtt)
    return s
  })

describe('verdict de liaison', () => {
  it('annonce OK quand tous les liens tiennent', () => {
    const v = linkVerdict({ samples: [sample({ wan_id: '4' }), sample({ wan_id: '5' })] })
    expect(v.verdict).toBe('ok')
    expect(v.detail).toContain('2 liens sur 2')
  })

  it('compte le lien filaire parmi les liens', () => {
    // Sur l'équipement d'essai, la fibre portait 140 Mbit/s pendant que les
    // quatre SIM étaient à zéro. L'ignorer ferait annoncer « tout va bien »
    // alors que le lien principal est tombé.
    const v = linkVerdict({ samples: [sample({ wan_id: '4' }), sample({ kind: 'ethernet', wan_id: '1', rsrp: null, sinr: null })] })
    expect(v.detail).toContain('2 liens sur 2')
  })

  it('signale une liaison dégradée quand un lien est tombé', () => {
    const v = linkVerdict({ samples: [sample({ wan_id: '4' }), sample({ wan_id: '5', connected: false })] })
    expect(v.verdict).toBe('degrade')
    expect(v.detail).toContain('1 déconnecté')
  })

  it('ne dégrade PAS la liaison sur un signal faible — il le dit ailleurs', () => {
    // `weak.length > 0` suffisait à annoncer « Liaison dégradée ». La mesure ne
    // le soutient pas : sur 4 × 8 800 relevés, la corrélation de rang entre
    // SINR/RSRP/RSRQ/RSSI et la latence ou la perte du tunnel ne dépasse
    // |ρ| = 0,19 ; et avant 1 181 pics isolés, le niveau radio du cycle
    // précédent est à 0,00 dB de la médiane de la même cellule — indiscernable
    // des 22 700 cycles calmes témoins. Une liaison se juge sur ce que le
    // tunnel porte, pas sur ce que l'antenne reçoit.
    const v = linkVerdict({ samples: [sample({ rsrp: -120, rsrq: -19, sinr: 1 })] })
    expect(v.verdict).toBe('ok')
    expect(v.detail).not.toContain('signal faible')

    // Mais le fait n'est pas tu : il ressort par `radioGrievances`, que la table
    // affiche auprès des colonnes radio. L'effacer serait l'erreur inverse —
    // et ce relevé à −120 dBm est justement HORS de la plage mesurée (−75 à
    // −105), donc hors de la portée de l'abstention.
    expect(v.radioGrievances).toEqual(['1 lien en signal faible'])
  })

  it('accorde le grief radio au pluriel', () => {
    const v = linkVerdict({
      samples: [
        sample({ rsrp: -120, rsrq: -19, sinr: 1 }),
        sample({ wan_id: '5', rsrp: -118, rsrq: -18, sinr: 2 }),
      ],
    })
    expect(v.radioGrievances).toEqual(['2 liens en signal faible'])
    expect(v.verdict).toBe('ok')
  })


  it('n’attribue pas de qualité radio à un lien filaire', () => {
    // Un lien filaire n'a ni RSRP ni SINR : il ne peut pas être « faible ».
    const v = linkVerdict({ samples: [sample({ kind: 'ethernet', wan_id: '1', rsrp: null, rsrq: null, sinr: null })] })
    expect(v.verdict).toBe('ok')
  })

  it('annonce la rupture quand plus rien ne tient', () => {
    const v = linkVerdict({ samples: [sample({ connected: false }), sample({ connected: false })] })
    expect(v.verdict).toBe('rompu')
    expect(v.detail).toContain('aucun des 2')
  })

  it('sans aucune mesure, ne prétend pas que ça marche', () => {
    const v = linkVerdict({ samples: [] })
    expect(v.verdict).toBe('rompu')
    expect(v.ageSeconds).toBeNull()
  })

  it('rapporte l’âge que le serveur a mesuré', () => {
    // Le calculer dans le navigateur revenait à comparer deux horloges : sur un
    // portable dont l'horloge avance, l'écart devenait négatif, se retrouvait
    // borné à zéro, et affichait « à l'instant » sur des données mortes.
    const v = linkVerdict({ samples: [sample({})], ageSeconds: 4 })
    expect(v.ageSeconds).toBe(4)
    expect(v.stale).toBe(false)
  })

  it('accorde le singulier quand il n’y a qu’un lien', () => {
    expect(linkVerdict({ samples: [sample({})] }).detail).toContain('1 lien sur 1')
  })

  it('accorde le nom sur le nombre auquel il se rapporte', () => {
    // Le nom était accordé sur le total et l'adjectif sur les connectés, ce qui
    // donnait « 1 liens sur 4 connecté » — sur le cas le plus souvent lu, celui
    // d'une liaison déjà dégradée.
    const samples = [
      sample({ wan_id: '4' }),
      ...['5', '6', '7'].map((id) => sample({ wan_id: id, connected: false })),
    ]
    const v = linkVerdict({ samples })
    expect(v.detail).toContain('1 lien sur 4 connecté')
    expect(v.detail).not.toContain('1 liens')
    expect(v.detail).toContain('3 déconnectés')
  })
})


describe('quand l’équipement lui-même ne répond plus', () => {
  it('n’affiche pas « Liaison OK » sur des relevés d’avant la panne', () => {
    // Mode de panne le plus coûteux : les relevés survivent en mémoire, la
    // WebSocket reste ouverte, et l'écran reste vert sur une liaison morte.
    const v = linkVerdict({ samples: [sample({}), sample({ wan_id: '5' })], status: 'injoignable' })
    expect(v.verdict).toBe('rompu')
    expect(v.headline).toBe('Équipement injoignable')
    expect(v.detail).toContain('mesures affichées antérieures')
  })

  it('ne renvoie pas à des valeurs affichées quand il n’y en a aucune', () => {
    // Vu le 30/09 sur un MBX éteint depuis le lancement : « les valeurs
    // affichées datent d'avant » au-dessus d'un écran qui n'en affichait aucune.
    for (const status of ['injoignable', 'identifiants refusés'] as const) {
      const v = linkVerdict({ samples: [], status })
      expect(v.verdict).toBe('rompu')
      expect(v.detail).not.toContain('antérieures')
    }
  })

  it('distingue un refus d’identifiants d’une injoignabilité', () => {
    // Le remède n'est pas le même : corriger un mot de passe, ou vérifier un câble.
    const v = linkVerdict({ samples: [sample({})], status: 'identifiants refusés' })
    expect(v.headline).toBe('Équipement inaccessible')
    expect(v.detail).toContain('identifiants')
  })

  it('déclare périmé un relevé trop vieux, même connecté', () => {
    const v = linkVerdict({ samples: [sample({})], status: 'connecté', ageSeconds: 300 })
    expect(v.verdict).toBe('rompu')
    expect(v.headline).toBe('Mesures périmées')
    expect(v.stale).toBe(true)
  })

  it('reste normal tant que la mesure est fraîche', () => {
    const v = linkVerdict({ samples: [sample({})], status: 'connecté', ageSeconds: 8 })
    expect(v.verdict).toBe('ok')
    expect(v.stale).toBe(false)
  })

  it('ne peint pas en rouge un équipement qui vient de démarrer', () => {
    // « en attente » est l'état d'avant la première scrutation, pas une panne.
    // Le rendre comme une injoignabilité apprend à ignorer le rouge.
    const v = linkVerdict({ samples: [], status: 'en attente' })
    expect(v.verdict).not.toBe('rompu')
    expect(v.headline).toContain('attente')
  })

  it('envoie corriger la configuration plutôt que vérifier un câble', () => {
    // Rendu « Équipement injoignable — plus aucune mesure ne remonte », alors
    // que le remède est dans la déclaration de l'équipement, pas dans un câble.
    const v = linkVerdict({ samples: [sample({})], status: 'erreur de configuration' })
    expect(v.verdict).toBe('rompu')
    expect(v.headline).not.toBe('Équipement injoignable')
    expect(v.headline).toBe('Erreur de configuration')
  })
})

describe('seuil de péremption', () => {
  it('retombe sur la constante quand la scrutation est inconnue', () => {
    expect(staleAfterSeconds()).toBe(STALE_AFTER_SECONDS)
    expect(staleAfterSeconds(null)).toBe(STALE_AFTER_SECONDS)
    expect(staleAfterSeconds(0)).toBe(STALE_AFTER_SECONDS)
  })

  it('se déduit de la période de scrutation quand elle est connue', () => {
    // `backend/.env` scrute à 10 s et annonce une descente vers 3 s ; le défaut
    // de `config.py` est déjà 3 s. Un seuil figé à 45 s en contredit forcément
    // une : c'est la même raison qui fait déduire son seuil à `withGaps`.
    expect(staleAfterSeconds(10)).toBe(50)
  })

  it('ne descend pas sous le temps d’un aller-retour réseau', () => {
    // À 1 s de scrutation, cinq cycles feraient 5 s, qu'un simple aller-retour
    // vers un équipement distant dépasse.
    expect(staleAfterSeconds(3)).toBe(15)
    expect(staleAfterSeconds(1)).toBe(15)
  })

  it('juge la péremption sur la période OBSERVÉE, pas sur la consigne', () => {
    const scrute = { samples: [sample({})], status: 'connecté', ageSeconds: 20 }
    // Consigne 3 s, période réelle 5,1 s : le serveur scrute PUIS dort, et le
    // cycle coûte 2,11 s pour un seul équipement. Prendre la consigne donnait un
    // seuil de 15 s, franchi par l'âge le plus élevé qu'un relevé sain puisse
    // atteindre (période + 10 s de rafraîchissement) — d'où un bandeau rouge sur
    // une liaison parfaitement saine, dès UN équipement.
    expect(staleAfterSeconds(3)).toBe(15)
    expect(staleAfterSeconds(5.1)).toBeCloseTo(25.5, 1)

    expect(linkVerdict({ ...scrute, periodSeconds: 3 }).stale).toBe(true)
    expect(linkVerdict({ ...scrute, periodSeconds: 5.1 }).stale).toBe(false)
    expect(linkVerdict(scrute).stale).toBe(false)
  })

  it('retombe sur le défaut prudent tant que la période n’est pas mesurée', () => {
    // Le serveur rend `null` avant son deuxième cycle : mieux vaut un seuil large
    // qu'un seuil calculé sur une valeur qui n'existe pas encore.
    expect(staleAfterSeconds(null)).toBe(STALE_AFTER_SECONDS)
    expect(staleAfterSeconds(undefined)).toBe(STALE_AFTER_SECONDS)
  })
})

describe('lissage du verdict', () => {
  it('affiche une rupture sans attendre', () => {
    // Différer une mauvaise nouvelle serait le défaut qu'on cherche à corriger.
    expect(smoothVerdict(['ok', 'ok', 'rompu'], 'ok')).toBe('rompu')
  })

  it('suit la mesure tant qu’il n’y a pas de quoi juger d’une tendance', () => {
    // Amortir dès le premier rendu figeait « Aucune mesure » pendant toute la
    // fenêtre de lissage, alors que les données étaient déjà affichées.
    expect(smoothVerdict(['ok'], 'rompu')).toBe('ok')
    expect(smoothVerdict(['ok', 'ok', 'ok', 'ok'], 'rompu')).toBe('ok')
  })

  it('ne bascule pas en dégradé sur un seul constat', () => {
    const passe = ['ok', 'ok', 'ok', 'ok', 'ok'] as const
    expect(smoothVerdict([...passe, 'degrade'], 'ok')).toBe('ok')
    expect(smoothVerdict([...passe, 'degrade', 'degrade'], 'ok')).toBe('ok')
    expect(smoothVerdict([...passe, 'degrade', 'degrade', 'degrade'], 'ok')).toBe('degrade')
  })

  it('exige davantage pour revenir au vert que pour en sortir', () => {
    // Trois constats pour alerter, cinq pour rassurer : un bandeau qui clignote
    // toutes les trois secondes est un bandeau qu'on cesse de regarder.
    const debut = ['degrade', 'degrade', 'degrade', 'degrade', 'degrade'] as const
    expect(smoothVerdict([...debut, 'ok', 'ok', 'ok', 'ok'], 'degrade')).toBe('degrade')
    expect(smoothVerdict([...debut, 'ok', 'ok', 'ok', 'ok', 'ok'], 'degrade')).toBe('ok')
  })

  it('ne change rien quand l’état constaté est celui affiché', () => {
    expect(smoothVerdict(['degrade'], 'degrade')).toBe('degrade')
  })

  it('conserve l’affichage tant qu’il n’y a rien à constater', () => {
    expect(smoothVerdict([], 'degrade')).toBe('degrade')
  })

  it('descend d’un cran quand plus rien n’est aussi grave, même si ça clignote', () => {
    // Un « rompu » retenu indéfiniment : la désescalade exigeait cinq
    // constats identiques, et un grief qui clignote un cycle sur deux n'y
    // arrive jamais. Cinq constats tous moins graves suffisent — on descend
    // au pire d'entre eux, pas au vert.
    const clignote: Verdict[] = ['ok', 'degrade', 'ok', 'degrade', 'ok']
    expect(smoothVerdict(clignote, 'rompu')).toBe('degrade')
    expect(smoothVerdict(['ok', 'ok', 'ok', 'ok', 'ok'], 'rompu')).toBe('ok')
    // Un seul constat encore aussi grave, et l'on ne bouge pas.
    expect(smoothVerdict(['ok', 'rompu', 'ok', 'degrade', 'ok'], 'rompu')).toBe('rompu')
  })

  it('ne se laisse pas berner par une oscillation', () => {
    // Le défaut de ma première version : comparer au dernier verdict brut au
    // lieu de l'affichage laissait passer une bascule sur deux.
    expect(
      smoothVerdict(['degrade', 'degrade', 'degrade', 'ok', 'degrade', 'ok'], 'degrade'),
    ).toBe('degrade')
  })

  it('affiche une rupture même pendant l’établissement', () => {
    expect(smoothVerdict(['ok', 'rompu'], 'ok')).toBe('rompu')
  })
})

describe('liens écartés du bond', () => {
  it('compte un lien hors du bond comme une dégradation', () => {
    // Un lien connecté mais écarté ne transporte rien : le compter comme
    // porteur annoncerait une redondance qui n'existe pas.
    const dedans = sample({ wan_id: '4' })
    dedans.wan.tunnel = tunnel('ACTIVE', 20)
    const dehors = sample({ wan_id: '5' })
    dehors.wan.tunnel = tunnel('WAN_DOWN')
    const v = linkVerdict({ samples: [dedans, dehors], status: 'connecté' })
    expect(v.verdict).toBe('degrade')
    expect(v.detail).toContain('1 hors du bond')
  })

  it('n’alerte pas quand tous les liens portent le flux', () => {
    const dedans = sample({ wan_id: '4' })
    dedans.wan.tunnel = tunnel('ACTIVE', 20)
    expect(linkVerdict({ samples: [dedans], status: 'connecté' }).verdict).toBe('ok')
  })

  it('reste « tunnel tombé » quand le débit du tunnel s’est éteint avec lui (C-2)', () => {
    // Le pair annonce des sous-réseaux dédiés et le tunnel portait du trafic.
    // Le concentrateur tombe : plus de compteur, donc plus de tranche
    // renseignée dans la fenêtre du témoin. Le rôle retombait sur `routes` et
    // le bandeau affichait « Liaison OK » en vert, tunnel toujours à terre.
    const samples = ['4', '5'].map((id) => {
      const s = sample({ wan_id: id })
      s.wan.tunnel = tunnel('PEER_DOWN')
      return s
    })
    const role = bondRole(['10.1.0.0/16'], paquetsParSecondeRecents(null))
    const v = linkVerdict({ samples, status: 'connecté', role })
    expect(v.verdict).toBe('degrade')
    expect(v.headline).toBe('Tunnel SpeedFusion tombé')
    // Et la conséquence ne prétend pas que le pair ne déclarait rien.
    expect(v.detail).not.toContain('aucune route annoncée')
    expect(v.detail).toContain('10.1.0.0/16')
  })

  it('dit « tunnel tombé » quand tous les liens connectés sont hors du bond', () => {
    // Ce test disait « rompue » : quatre SIM connectées, quatre tunnels
    // tombés, « plus rien ne transitait ». Ce dernier point n'avait jamais été
    // mesuré — c'était l'hypothèse que le tunnel EST la liaison. Le MAX HD2
    // du 19 septembre a montré un tunnel mort sur une connectivité intacte, et
    // rien dans `status.*` ne distingue les deux : un routeur dont le tunnel
    // tombe sort en direct par ses interfaces, sauf configuration contraire
    // que l'outil ne lit pas. Il ne doit donc pas affirmer le plus fort.
    const samples = ['4', '5', '6', '7'].map((id) => {
      const s = sample({ wan_id: id })
      s.wan.tunnel = tunnel('PEER_DOWN')
      return s
    })
    const v = linkVerdict({ samples, status: 'connecté' })
    expect(v.verdict).toBe('degrade')
    expect(v.headline).toBe('Tunnel SpeedFusion tombé')
    // La sentinelle de ce projet n'est pas un mot du routeur (R-1.5).
    expect(v.detail).not.toContain('PEER_DOWN')
    expect(v.detail).toContain('(pair : aucun pair connecté)')
    expect(v.detail).not.toContain('plus rien ne transite')

    // Le mot du routeur, puis la cause lue sur les profils, qui l'emporte.
    for (const s of samples) s.wan.tunnel = { ...s.wan.tunnel!, peer_state: 'START' }
    expect(linkVerdict({ samples, status: 'connecté' }).detail)
      .toContain('(pair : tentative en cours (START))')
    // Deux mots différents ne font pas une cause.
    samples[0].wan.tunnel = { ...samples[0].wan.tunnel!, peer_state: 'STOPPED' }
    expect(linkVerdict({ samples, status: 'connecté' }).detail).not.toContain('pair :')
    const cause = 'profil PROFIL-B : tentative en cours (START), 0 pair connecté'
    expect(linkVerdict({ samples, status: 'connecté', tunnelCause: cause }).detail)
      .toContain(`tous hors du bond (${cause})`)
  })

  it('ne dit pas « tombé » d’un tunnel que le serveur sait n’avoir jamais tenu', () => {
    // Le serveur a tranché (`tunnel_attendu: false`) : un bond qui n'a jamais
    // porté dans cette session n'est pas tombé, c'est un état, et il n'ouvre
    // aucune alerte dessus. L'écran ne recevait pas ce témoin et titrait
    // « Tunnel SpeedFusion tombé — 2 h dans cet état » en ambre sur le HD2.
    const samples = ['4', '5', '6'].map((id) => {
      const s = sample({ wan_id: id })
      s.wan.tunnel = tunnel('PEER_DOWN')
      return s
    })
    const jamais = linkVerdict({ samples, status: 'connecté', tunnelAttendu: false })
    expect(jamais.verdict).toBe('ok')
    expect(jamais.headline).toBe('Liaison OK')
    expect(jamais.detail).toContain('jamais établi')
    // …et ce qui le tient à terre, quand les profils le disent (R-1.5).
    const cause = 'profil PROFIL-B : tentative en cours (START), 0 pair connecté'
    expect(linkVerdict({ samples, status: 'connecté', tunnelAttendu: false, tunnelCause: cause }).detail)
      .toMatch(/jamais établi dans cette session \(profil PROFIL-B : tentative en cours \(START\), 0 pair connecté\)$/)
    // Les griefs du tunnel restent dits — auprès du bond, pas dans le verdict.
    expect(jamais.bondGrievances).toEqual(['3 hors du bond'])

    // Dès que le bond a porté une fois, sa chute redevient une nouvelle.
    const tombe = linkVerdict({ samples, status: 'connecté', tunnelAttendu: true })
    expect(tombe.headline).toBe('Tunnel SpeedFusion tombé')
    // Et `null` — question pas encore posée — garde la règle par défaut.
    expect(linkVerdict({ samples, status: 'connecté', tunnelAttendu: null }).headline)
      .toBe('Tunnel SpeedFusion tombé')
  })

  it('ne se dégrade pas pour un Ethernet jamais raccordé depuis l’allumage', () => {
    // Dome Pro Duo, WAN 1 « No Cable Detected » depuis l'allumage : « Liaison
    // dégradée » en permanence. Le serveur nomme ce lien ; le verdict le dit
    // dans le décompte et ne s'en dégrade pas.
    const filaire = sample({ wan_id: '1', kind: 'ethernet', connected: false, status_message: 'No Cable Detected' })
    const sims = [sample({ wan_id: '2' }), sample({ wan_id: '3' })]
    const jamais = linkVerdict({ samples: [filaire, ...sims], status: 'connecté', neverConnected: ['1'] })
    expect(jamais.verdict).toBe('ok')
    expect(jamais.detail).toMatch(/2 liens sur 3 connectés \(1 lien filaire jamais raccordé\)/)
    // Sans le témoin — ou pour un lien qui a été connecté puis est tombé — la
    // chute reste une nouvelle.
    expect(linkVerdict({ samples: [filaire, ...sims], status: 'connecté' }).verdict).toBe('degrade')
    // Une SIM jamais connectée n'est pas concernée : la règle vise le câble.
    const sim = sample({ wan_id: '4', connected: false })
    expect(linkVerdict({ samples: [sim, ...sims], status: 'connecté', neverConnected: ['4'] }).verdict)
      .toBe('degrade')
  })

  it('ne compte pas comme tombé un lien que l’exploitant a désactivé', () => {
    // Scénario 1 de la revue : WAN 3 désactivé depuis l'interface du Dome. Une
    // décision, pas une panne — le verdict la dit dans le décompte.
    const off = sample({ wan_id: '3', connected: false, disabled: true, status_message: 'désactivé' })
    const v = linkVerdict({ samples: [sample({ wan_id: '2' }), off], status: 'connecté' })
    expect(v.verdict).toBe('ok')
    expect(v.detail).toMatch(/1 lien sur 2 connecté \(1 désactivé\)/)
  })

  it('ne prend pas un état de tunnel inconnu pour un lien écarté', () => {
    // `!state?.startsWith('ACTIVE')` valait `true` sur un état null : un champ
    // vide déclenchait « 1 hors du bond » pendant que WanCard affichait « — »
    // pour le même champ. Un état inconnu ne dit rien, ni dans un sens ni dans
    // l'autre.
    const inconnu = sample({ wan_id: '4' })
    inconnu.wan.tunnel = tunnel(null, 12)
    const v = linkVerdict({ samples: [inconnu], status: 'connecté' })
    expect(v.verdict).toBe('ok')
    expect(v.detail).not.toContain('hors du bond')
  })

  it('n’invente pas de tunnel sur un équipement qui n’en a pas', () => {
    // Sans SpeedFusion, `tunnel` est nul sur tous les liens : il n'y a pas de
    // bond dont on puisse être écarté.
    expect(linkVerdict({ samples: [sample({ wan_id: '4' })], status: 'connecté' }).verdict)
      .toBe('ok')
  })

  it('ne conclut pas à la rupture tant qu’un état inconnu subsiste', () => {
    // Un lien écarté, un lien d'état inconnu : rien ne prouve que plus rien ne
    // passe, et une rupture annoncée à tort se paie aussi cher que l'inverse.
    const dehors = sample({ wan_id: '4' })
    dehors.wan.tunnel = tunnel('WAN_DOWN')
    const inconnu = sample({ wan_id: '5' })
    inconnu.wan.tunnel = tunnel(null)
    const v = linkVerdict({ samples: [dehors, inconnu], status: 'connecté' })
    expect(v.verdict).toBe('degrade')
    expect(v.detail).toContain('1 hors du bond')
  })
})

describe('latence du tunnel', () => {
  it('le 30 août à 13:51:42, ne laisse plus passer un lien à 1 929 ms', () => {
    // Le cas qui a motivé tout ceci. Quatre SIM ACTIVE, quatre liens connectés,
    // bandeau vert : le verdict ne lisait ni `rtt_ms` ni `loss_percent`, alors
    // que le dépôt affirme partout que ce sont ces grandeurs-là qui font geler
    // une image. Le WAN 6 empoisonnait la remontée sans qu'aucune des règles
    // d'alors n'ait de quoi le voir.
    const v = linkVerdict({ samples: releve30Aout(), status: 'connecté' })
    expect(v.verdict).toBe('degrade')
    expect(v.detail).toContain('latence excessive')
    expect(v.detail).toMatch(/1\s?929 ms/)
    // Le plus RAPIDE du bond, et non la médiane des autres : c'est à lui que le
    // firmware compare, donc c'est à lui qu'il faut opposer le chiffre.
    expect(v.detail).toContain('contre 25 ms')
  })

  it('ne se fie pas au niveau radio pour juger le tunnel', () => {
    // Le lien qui ruinait la remontée avait le MEILLEUR SINR des quatre : sur ce
    // matériel, le niveau radio ne prédit pas l'état du tunnel. Si le grief
    // annoncé était « signal faible », on enverrait chercher la panne du mauvais
    // côté — et sur ce relevé, aucun des quatre liens n'est faible.
    const poison = releve30Aout()[2]
    expect(overallQuality(poison.wan.rsrp, poison.wan.rsrq, poison.wan.sinr)).not.toBe('faible')
    const v = linkVerdict({ samples: releve30Aout(), status: 'connecté' })
    expect(v.detail).not.toContain('signal faible')
  })

  it('suit le ROUTEUR, et non une approximation de sa règle', () => {
    // Ce module comparait chaque lien à la médiane des autres, faute de pouvoir
    // lire la décision du firmware. Elle est lisible depuis : le routeur publie
    // son `latencyDiffCutoff` et le serveur en tire `beyond_cutoff`.
    //
    // Mesuré sur 33 679 relevés du matériel, les deux règles divergeaient sur
    // 2 585 — 7,7 % — et toujours dans le même sens : l'approximation dénonçait
    // des liens que le routeur employait sans broncher.
    const liens = [
      { rtt: 19, beyondCutoff: false },
      { rtt: 353, beyondCutoff: false },  // l'approximation criait ici
      { rtt: 18, beyondCutoff: false },
    ]
    expect(latencyOutliers(liens)).toEqual([])
  })

  it('dénonce celui que le routeur a écarté, et lui oppose le plus rapide', () => {
    const ecarte = latencyOutliers([
      { rtt: 19, beyondCutoff: false },
      { rtt: 620, beyondCutoff: true },
      { rtt: 22, beyondCutoff: false },
    ])
    expect(ecarte).toHaveLength(1)
    expect(ecarte[0].index).toBe(1)
    // Le plus RAPIDE du bond : c'est à lui que le firmware compare.
    expect(ecarte[0].reference).toBe(19)
  })

  it('signale quand même un bond entièrement effondré', () => {
    // L'écart du routeur est aveugle ici : quatre liens à deux secondes se
    // ressemblent parfaitement, aucun ne dépasse personne. Seul le garde-fou
    // absolu les voit — c'est sa première raison d'être.
    const liens = [2000, 2100, 1950, 2050].map((rtt) => ({ rtt, beyondCutoff: false }))
    expect(latencyOutliers(liens)).toHaveLength(4)
  })

  it('signale un lien seul dans le bond, qui n’a personne à dépasser', () => {
    // Seconde raison d'être du garde-fou : un lien unique ne peut pas franchir
    // un écart, et passerait pour normal à 1 929 ms.
    const seul = latencyOutliers([{ rtt: 1929, beyondCutoff: false }])
    expect(seul).toHaveLength(1)
    expect(seul[0].reference).toBeNull()

    const s = sample({ wan_id: '4' })
    s.wan.tunnel = tunnel('ACTIVE', 1929)
    const v = linkVerdict({ samples: [s], status: 'connecté' })
    expect(v.verdict).toBe('degrade')
    // Aucune comparaison n'est prétendue, puisqu'il n'y avait personne à qui
    // comparer.
    expect(v.detail).not.toContain('contre')
  })

  it('emploie le seuil PUBLIÉ par le serveur, et non le repli', () => {
    // Le seuil vivait en double de part et d'autre de la couture. Un serveur
    // qui abaisse le sien doit être suivi sans qu'on touche à l'interface.
    const liens = [{ rtt: 600, beyondCutoff: false }, { rtt: 20, beyondCutoff: false }]
    expect(latencyOutliers(liens)).toEqual([])
    expect(latencyOutliers(liens, 500)).toHaveLength(1)
  })

  it('ne juge pas un lien dont la latence n’est pas mesurée, ni ne s’en sert', () => {
    // Un `rtt_ms` absent ne vaut ni bon ni mauvais. Le compter pour zéro
    // abaisserait la médiane au point de dénoncer des liens sains.
    const mesures = releve30Aout().slice(0, 2)
    const muet = sample({ wan_id: '8' })
    muet.wan.tunnel = tunnel('ACTIVE', null)
    const v = linkVerdict({ samples: [...mesures, muet], status: 'connecté' })
    expect(v.verdict).toBe('ok')
  })

  it('ne juge pas sur sa latence un lien écarté du bond', () => {
    // Il a déjà son propre grief, et ce qu'il mesure ne concerne plus le flux.
    const dedans = sample({ wan_id: '4' })
    dedans.wan.tunnel = tunnel('ACTIVE', 30)
    const dehors = sample({ wan_id: '5' })
    dehors.wan.tunnel = tunnel('WAN_DOWN', 3000)
    const v = linkVerdict({ samples: [dedans, dehors], status: 'connecté' })
    expect(v.detail).toContain('1 hors du bond')
    expect(v.detail).not.toContain('latence')
  })
})

describe('perte du tunnel', () => {
  const porteur = (extra: Partial<TunnelLink>) => {
    const s = sample({ wan_id: '4' })
    s.wan.tunnel = tunnel('ACTIVE', 30, extra)
    return s
  }

  it('ignore un taux établi sur trois paquets', () => {
    // Mesuré : la seule valeur de perte non nulle jamais enregistrée est
    // 33,33 % — un paquet sur trois, c'est-à-dire un keepalive au dénominateur.
    // Un taux calculé sur trois paquets n'est pas une mesure.
    const s = porteur({ loss_percent_up: 33.33, packets_up: 3 })
    expect(reportableLoss(s.wan.tunnel!)).toBeNull()
    expect(linkVerdict({ samples: [s], status: 'connecté' }).verdict).toBe('ok')
  })

  it('retient une perte établie sur un dénominateur significatif', () => {
    // Relevé chargé : 5 000 paquets perdus sur 105 000 émis.
    const s = porteur({ loss_percent_up: 4.76, packets_up: 105_000 })
    const v = linkVerdict({ samples: [s], status: 'connecté' })
    expect(v.verdict).toBe('degrade')
    expect(v.detail).toContain('perte montante')
    expect(v.detail).toContain('4,8 %')
  })

  it('ne dilue pas la perte montante dans le volume descendant', () => {
    // 4,76 % à la montée contre rien à la descente sur neuf fois plus de
    // paquets : le montant est la ressource rare, les porteuses étant asymétriques.
    const s = porteur({
      loss_percent_up: 4.76, packets_up: 105_000,
      loss_percent_down: 0, packets_down: 900_000,
    })
    expect(reportableLoss(s.wan.tunnel!)?.direction).toBe('montante')
  })

  it('nomme le sens descendant quand c’est lui qui décroche', () => {
    // Le sens désigne le remède : à la descente, c'est la couverture ou la
    // charge de la cellule, pas l'émission du véhicule.
    const s = porteur({
      loss_percent_up: 0, packets_up: 100_000,
      loss_percent_down: 6, packets_down: 100_000,
    })
    expect(linkVerdict({ samples: [s], status: 'connecté' }).detail)
      .toContain('perte descendante')
  })

  it('ne lit pas le taux tous sens confondus', () => {
    // `loss_percent` porte le PIRE des deux sens, dont on ignore le
    // dénominateur : il est inqualifiable au regard de la règle des 200 paquets.
    const s = porteur({ loss_percent: 33.33 })
    expect(reportableLoss(s.wan.tunnel!)).toBeNull()
    expect(linkVerdict({ samples: [s], status: 'connecté' }).verdict).toBe('ok')
  })

  it('place la frontière à 200 paquets, pas un de moins', () => {
    const juste = porteur({ loss_percent_up: 3, packets_up: LOSS_MIN_PACKETS })
    const trop_peu = porteur({ loss_percent_up: 3, packets_up: LOSS_MIN_PACKETS - 1 })
    expect(reportableLoss(juste.wan.tunnel!)?.packets).toBe(LOSS_MIN_PACKETS)
    expect(reportableLoss(trop_peu.wan.tunnel!)).toBeNull()
  })

  it('laisse tranquille une perte résiduelle sous le seuil', () => {
    const s = porteur({ loss_percent_up: 0.4, packets_up: 50_000 })
    expect(linkVerdict({ samples: [s], status: 'connecté' }).verdict).toBe('ok')
  })

  it('ne juge pas sur sa perte un lien écarté du bond', () => {
    const dedans = sample({ wan_id: '4' })
    dedans.wan.tunnel = tunnel('ACTIVE', 30)
    const dehors = sample({ wan_id: '5' })
    dehors.wan.tunnel = tunnel('WAN_DOWN', null, { loss_percent_up: 40, packets_up: 10_000 })
    const v = linkVerdict({ samples: [dedans, dehors], status: 'connecté' })
    expect(v.detail).toContain('1 hors du bond')
    expect(v.detail).not.toContain('perte')
  })

  it('cumule les griefs sans en taire aucun', () => {
    // Un relevé peut porter plusieurs défauts à la fois ; le bandeau les énumère
    // du plus structurel au plus circonstanciel.
    const samples = releve30Aout()
    samples[0].wan.tunnel = tunnel('ACTIVE', 34, { loss_percent_up: 9, packets_up: 40_000 })
    samples[3].wan.connected = false
    const v = linkVerdict({ samples, status: 'connecté' })
    expect(v.detail).toContain('1 déconnecté')
    expect(v.detail).toContain('latence excessive')
    expect(v.detail).toContain('perte montante')
  })
})

describe('un bond qui n’est pas le chemin du trafic', () => {
  // Le pair de ce déploiement n'achemine que 169.254.131.0/24 et
  // 203.0.113.61/32 : aucun profil n'est sélectionné dans « Send All Traffic
  // To », et le trafic général sort par les interfaces. Les grandeurs du tunnel
  // restent mesurées, mais elles ne décrivent plus ce que fait l'exploitant.
  // Le second terme est le verdict DU ROUTEUR — `beyond_cutoff` —, seul juge de
  // la latence depuis que l'approximation a été retirée.
  const quatre = (liens: [number, boolean][]) =>
    liens.map(([rtt, ecarte], i) =>
      sample({
        wan_id: String(4 + i),
        tunnel: tunnel('ACTIVE', rtt, { beyond_cutoff: ecarte }),
      }),
    )

  it('ne déclare pas la liaison dégradée pour une latence qui ne la concerne pas', () => {
    // Le cas vu à l'écran : « Liaison dégradée — 1 en latence excessive
    // (353 ms contre 19 ms) », alors que les 353 ms sont le trajet vers le
    // concentrateur et que rien du trafic ne l'emprunte.
    const v = linkVerdict({
      samples: quatre([[353, true], [19, false], [18, false], [20, false]]), status: 'connecté', role: { kind: 'routes', routes: ['10.0.0.0/24'] },
    })
    expect(v.verdict).toBe('ok')
    expect(v.detail).toBe('4 liens sur 4 connectés')
    // Écarté du verdict, pas effacé.
    expect(v.bondGrievances).toEqual(['1 en latence excessive (353 ms contre 18 ms)'])
  })

  it('contre-épreuve : le même relevé dégrade bien un bond ordinaire', () => {
    // Sans elle, l'assertion ci-dessus passerait même si la détection de latence
    // avait cessé de fonctionner.
    const v = linkVerdict({ samples: quatre([[353, true], [19, false], [18, false], [20, false]]), status: 'connecté' })
    expect(v.verdict).toBe('degrade')
    expect(v.detail).toContain('1 en latence excessive')
    expect(v.bondGrievances).toEqual([])
  })

  it('garde les griefs qui décrivent le lien lui-même', () => {
    // Un lien débranché est débranché, quel que soit l'usage du tunnel : c'est
    // un fait de l'interface, pas du bond. Mesuré sur le matériel le 11
    // septembre, câble WAN 1 retiré : `No Cable Detected`, tunnel `WAN_DOWN`.
    const samples = [
      sample({ wan_id: '1', kind: 'ethernet', connected: false, tunnel: tunnel('WAN_DOWN') }),
      ...quatre([[30, false], [18, false], [39, false], [19, false]]),
    ]
    const v = linkVerdict({ samples, status: 'connecté', role: { kind: 'routes', routes: ['10.0.0.0/24'] } })
    expect(v.verdict).toBe('degrade')
    expect(v.detail).toBe('4 liens sur 5 connectés — 1 déconnecté')
  })

  it('ne dégrade pas un lien AU REPOS', () => {
    // La tentation, en retirant les grandeurs du tunnel, est de les remplacer
    // par le débit d'interface. Ce serait pire : un lien à zéro kbit/s est un
    // lien inemployé, et quatre SIM d'un véhicule à l'arrêt allumeraient le
    // bandeau en permanence — le « papier peint » que tout ce projet combat.
    const samples = quatre([[30, false], [18, false], [39, false], [19, false]]).map((s) => ({
      ...s, wan: { ...s.wan, tx_kbps: 0, rx_kbps: 0 },
    }))
    expect(linkVerdict({ samples, status: 'connecté', role: { kind: 'routes', routes: ['10.0.0.0/24'] } }).verdict)
      .toBe('ok')
  })

  it('ne parle pas de rupture quand tout le bond tombe', () => {
    // `PEER_DOWN` sur tous les liens vaut « Tunnel tombé » sur un bond
    // ordinaire : plus rien ne transite. Ici les liens sont debout et le trafic
    // continue de sortir par les interfaces — l'annoncer rompue serait le
    // contraire de la vérité. Le bond tombé reste dit, auprès du bond.
    const samples = quatre([[30, false], [18, false], [39, false], [19, false]]).map((s) => ({
      ...s, wan: { ...s.wan, tunnel: tunnel('PEER_DOWN') },
    }))
    const v = linkVerdict({ samples, status: 'connecté', role: { kind: 'routes', routes: ['10.0.0.0/24'] } })
    expect(v.verdict).toBe('ok')
    expect(v.bondGrievances).toEqual(['4 hors du bond'])
  })

  it('dit « tunnel tombé », pas « rompue », quand les liens sont debout et tous hors du bond', () => {
    // Le cas du MAX HD2 rencontré le 19 septembre : concentrateur SpeedFusion
    // injoignable, pair muet, rôle `inconnu` traduit en « le bond porte
    // tout ». Les trois WAN sont debout. L'écran titrait « Liaison rompue » ;
    // c'est l'exploitant qui a posé la question juste. Un état de tunnel seul
    // ne fonde pas « plus rien ne transite ».
    const samples = quatre([[30, false], [18, false], [39, false]]).map((s, i) => ({
      ...s, wan: { ...s.wan, tunnel: tunnel('PEER_DOWN'), tx_kbps: i === 0 ? 64 : 0, rx_kbps: i === 0 ? 21 : 0 },
    }))
    const v = linkVerdict({ samples, status: 'connecté', role: { kind: 'inconnu' } })
    expect(v.verdict).toBe('degrade')
    expect(v.headline).toBe('Tunnel SpeedFusion tombé')
    expect(v.detail).toContain('pair : aucun pair connecté')
    expect(v.detail).not.toContain('plus rien ne transite')
  })

  it('ne clignote pas au gré du débit instantané', () => {
    // Une première correction départageait sur le débit MESURÉ aux
    // interfaces. Sur un réseau calme il vaut 0,0 la plupart des relevés, et le
    // bandeau passait d'ambre à rouge au gré des paquets. Le verdict doit être
    // le MÊME avec et sans débit à l'instant : l'état du tunnel, lui, est stable.
    const avecTrafic = quatre([[30, false], [18, false]]).map((s) => ({
      ...s, wan: { ...s.wan, tunnel: tunnel('PEER_DOWN'), tx_kbps: 500, rx_kbps: 900 },
    }))
    const sansTrafic = quatre([[30, false], [18, false]]).map((s) => ({
      ...s, wan: { ...s.wan, tunnel: tunnel('PEER_DOWN'), tx_kbps: 0, rx_kbps: 0 },
    }))
    const a = linkVerdict({ samples: avecTrafic, status: 'connecté', role: { kind: 'inconnu' } })
    const b = linkVerdict({ samples: sansTrafic, status: 'connecté', role: { kind: 'inconnu' } })
    expect(a.verdict).toBe(b.verdict)
    expect(a.headline).toBe(b.headline)
    expect(a.verdict).toBe('degrade')
  })

  it('ne nomme la cause du tunnel que si tous les liens la partagent', () => {
    // WAN_DOWN sur l'un, COLD sur l'autre : le bandeau attribuait WAN_DOWN
    // aux deux et envoyait chercher une panne de câble sur un lien qui
    // refroidissait — pendant que le serveur, exigeant l'unanimité, émettait
    // deux alertes distinctes. Même règle que `describeCause` : unanime ou tue.
    const samples = [
      { ...sample({ wan_id: '4' }), wan: { ...sample({ wan_id: '4' }).wan, tunnel: tunnel('WAN_DOWN') } },
      { ...sample({ wan_id: '5' }), wan: { ...sample({ wan_id: '5' }).wan, tunnel: tunnel('COLD') } },
    ]
    const v = linkVerdict({ samples, status: 'connecté', role: { kind: 'inconnu' } })
    expect(v.headline).toBe('Tunnel SpeedFusion tombé')
    expect(v.detail).not.toContain('WAN_DOWN')
    expect(v.detail).not.toContain('COLD')
  })

  it('ne tait ni le signal faible ni le lien tombé sous un tunnel tombé', () => {
    // La branche sortait avant que ces faits soient calculés, et rendait
    // `radioGrievances: []` : sur le HD2, où le tunnel n'est jamais attendu,
    // une SIM à −118 dBm et une SIM sans carte n'étaient plus dites nulle
    // part par le bandeau — définitivement.
    const samples = [
      { ...sample({ wan_id: '1', kind: 'ethernet', rsrp: null, sinr: null, rsrq: null }),
        wan: { ...sample({ wan_id: '1', kind: 'ethernet', rsrp: null, sinr: null, rsrq: null }).wan, tunnel: tunnel('PEER_DOWN') } },
      { ...sample({ wan_id: '4', rsrp: -118, rsrq: -19, sinr: 1 }),
        wan: { ...sample({ wan_id: '4', rsrp: -118, rsrq: -19, sinr: 1 }).wan, tunnel: tunnel('PEER_DOWN') } },
      sample({ wan_id: '5', connected: false, status_message: 'No SIM detected' }),
    ]
    const v = linkVerdict({ samples, status: 'connecté', role: { kind: 'inconnu' } })
    expect(v.headline).toBe('Tunnel SpeedFusion tombé')
    expect(v.radioGrievances).toEqual(['1 lien en signal faible'])
    expect(v.detail).toContain('1 déconnecté (No SIM detected)')
    expect(v.detail).toContain('2 liens sur 3 connectés')
    // Et il n'affirme plus mesurer où sort le trafic.
    expect(v.detail).not.toContain('sort en direct')
  })

  it('dit ce que le tunnel portait, selon le rôle — et rien de plus', () => {
    // Le verdict recevait un booléen « le bond porte le trafic » : `tout`,
    // `porte` et `inconnu` tombaient sous une même phrase, alors que tout
    // passait par le tunnel dans un cas, des routes dédiées dans l'autre, et
    // qu'on ne sait rien dans le troisième.
    const tombe = quatre([[30, false], [18, false]]).map((s) => ({
      ...s, wan: { ...s.wan, tunnel: tunnel('PEER_DOWN') },
    }))
    const verdictPour = (role: Parameters<typeof linkVerdict>[0]['role']) =>
      linkVerdict({ samples: tombe, status: 'connecté', role })
    expect(verdictPour({ kind: 'tout' }).detail).toContain('route par défaut annoncée par le pair')
    expect(verdictPour({ kind: 'porte', routes: ['10.8.0.0/16'] }).detail).toContain('10.8.0.0/16')
    expect(verdictPour({ kind: 'inconnu' }).detail).toContain('aucune route annoncée')
    // `routes` ne passe pas par cette branche : le tunnel est un grief du bond.
    const r = verdictPour({ kind: 'routes', routes: ['10.8.0.0/16'] })
    expect(r.verdict).toBe('ok')
    expect(r.bondGrievances).toEqual(['2 hors du bond'])
  })

  it('garde « rompue » pour ce qui l’est : aucun lien connecté', () => {
    // La rupture existe toujours — elle se lit sur les LIENS, pas sur le tunnel.
    const samples = quatre([[30, false], [18, false]]).map((s) => ({
      ...s, wan: { ...s.wan, connected: false, tunnel: tunnel('WAN_DOWN') },
    }))
    const v = linkVerdict({ samples, status: 'connecté', role: { kind: 'inconnu' } })
    expect(v.verdict).toBe('rompu')
  })

  it('reste muet sur le tunnel quand l’équipement ne répond plus', () => {
    // Les relevés en mémoire sont ceux d'AVANT la panne : en tirer « 1 en
    // latence excessive » ferait chercher un incident de tunnel là où
    // l'équipement lui-même est injoignable.
    const v = linkVerdict({
      samples: quatre([[353, true], [19, false], [18, false], [20, false]]), status: 'injoignable', role: { kind: 'routes', routes: ['10.0.0.0/24'] },
    })
    expect(v.verdict).toBe('rompu')
    expect(v.bondGrievances).toEqual([])
  })
})

describe('la cause du routeur, quand un lien tombe', () => {
  const tombe = (wan_id: string, message: string | null) =>
    sample({ wan_id, connected: false, status_message: message })

  it('nomme ce que le routeur a dit', () => {
    // Relevé sur le matériel le 13 septembre : WAN 1 tombé seize minutes sur un
    // échec DNS. « 1 déconnecté » ne dit pas où chercher ; « WAN failed DNS
    // test » et « No Cable Detected » désignent deux interventions entièrement
    // différentes — l'une se règle dans la configuration, l'autre au bout du
    // câble.
    const v = linkVerdict({
      samples: [
        tombe('1', 'WAN failed DNS test'),
        sample({ wan_id: '4', tunnel: tunnel('ACTIVE', 30) }),
      ],
      status: 'connecté',
    })
    expect(v.verdict).toBe('degrade')
    expect(v.detail).toBe('1 lien sur 2 connecté — 1 déconnecté (WAN failed DNS test)')
  })

  it('se tait quand les liens tombés ne disent pas la même chose', () => {
    // Deux liens peuvent tomber pour deux raisons. Coller au total la cause de
    // l'un ferait chercher la panne du mauvais côté — c'est la règle que le
    // grief de perte applique déjà au sens.
    const v = linkVerdict({
      samples: [
        tombe('1', 'No Cable Detected'),
        tombe('4', 'WAN failed DNS test'),
        sample({ wan_id: '5', tunnel: tunnel('ACTIVE', 30) }),
      ],
      status: 'connecté',
    })
    expect(v.detail).toContain('2 déconnectés')
    expect(v.detail).not.toContain('DNS')
    expect(v.detail).not.toContain('Cable')
  })

  it('nomme la cause commune à plusieurs liens', () => {
    // Contre-épreuve : deux liens tombés POUR LA MÊME RAISON se nomment. Sans
    // elle, la règle ci-dessus passerait même si la cause n'était jamais dite.
    const v = linkVerdict({
      samples: [
        tombe('1', 'No Cable Detected'),
        tombe('4', 'No Cable Detected'),
        sample({ wan_id: '5', tunnel: tunnel('ACTIVE', 30) }),
      ],
      status: 'connecté',
    })
    expect(v.detail).toContain('2 déconnectés (No Cable Detected)')
  })

  it('se tait quand le routeur n’a rien dit', () => {
    // Un message absent n'est pas une cause vide : on n'ouvre pas une
    // parenthèse pour y écrire rien.
    const v = linkVerdict({
      samples: [tombe('1', null), sample({ wan_id: '4', tunnel: tunnel('ACTIVE', 30) })],
      status: 'connecté',
    })
    expect(v.detail).toBe('1 lien sur 2 connecté — 1 déconnecté')
  })

  it('ne nomme pas la cause d’un seul quand un autre s’est tu', () => {
    // Deux liens tombés, un seul message : l'attribuer aux deux serait faux.
    const v = linkVerdict({
      samples: [
        tombe('1', 'No Cable Detected'),
        tombe('4', null),
        sample({ wan_id: '5', tunnel: tunnel('ACTIVE', 30) }),
      ],
      status: 'connecté',
    })
    expect(v.detail).toContain('2 déconnectés')
    expect(v.detail).not.toContain('Cable')
  })

  it('la porte aussi sur une liaison rompue', () => {
    // C'est là qu'on a le plus besoin de savoir où chercher : plus rien ne
    // passe, et l'écran n'a plus que cette phrase à offrir.
    const v = linkVerdict({
      samples: [tombe('1', 'No Cable Detected'), tombe('4', 'No Cable Detected')],
      status: 'connecté',
    })
    expect(v.verdict).toBe('rompu')
    expect(v.detail).toBe("aucun des 2 liens n'est connecté (No Cable Detected)")
  })
})

describe('la table et le bandeau jugent la perte avec la MÊME règle', () => {
  it('se tait sur un taux dont le sens n’atteint pas le dénominateur', () => {
    // Relevé réel du 13 septembre à 14 h 00, WAN 6 : le taux de 4,71 % vient du
    // sens descendant, établi sur 191 paquets ; le sens montant en compte 249
    // mais ne perd rien. La table publiait « 4,71 % » en rouge sous le
    // dénominateur de l'autre sens, pendant que le bandeau disait « Liaison OK ».
    const t = tunnel('ACTIVE', 30, {
      loss_percent: 4.71,
      loss_percent_up: 0, packets_up: 249,
      loss_percent_down: 4.71, packets_down: 191,
    })
    expect(reportableLoss(t)).toEqual({ percent: 0, packets: 249, direction: 'montante' })
  })

  it('retient le sens qui porte le taux quand il pèse assez', () => {
    const t = tunnel('ACTIVE', 30, {
      loss_percent: 4.71,
      loss_percent_up: 0, packets_up: 249,
      loss_percent_down: 4.71, packets_down: 400,
    })
    expect(reportableLoss(t)).toEqual({ percent: 4.71, packets: 400, direction: 'descendante' })
  })
})

describe('petites règles qui n’avaient pas de test', () => {
  it('seuilUtile ne laisse pas passer zéro ni une absence', () => {
    // Le modèle du journal déclare `latency_threshold_ms: float = 0` : un
    // seuil de zéro rendrait tout lien « en latence excessive ». Laisser
    // passer 0 donnait 195 verts.
    expect(seuilUtile(0)).toBe(LATENCY_ABSOLUTE_MS)
    expect(seuilUtile(null)).toBe(LATENCY_ABSOLUTE_MS)
    expect(seuilUtile(undefined)).toBe(LATENCY_ABSOLUTE_MS)
    expect(seuilUtile(-5)).toBe(LATENCY_ABSOLUTE_MS)
    expect(seuilUtile(500)).toBe(500)
  })

  it('accord met au pluriel à partir de deux', () => {
    expect(accord(0, 'lien')).toBe('lien')
    expect(accord(1, 'lien')).toBe('lien')
    expect(accord(2, 'lien')).toBe('liens')
    expect(accord(3, 'déconnecté', 'déconnectés')).toBe('déconnectés')
  })

  it('ecarteDuBond exige un état CONNU : ni tunnel absent, ni état null', () => {
    const base = sample({ wan_id: '4' }).wan
    expect(ecarteDuBond({ ...base, tunnel: null })).toBe(false)
    expect(ecarteDuBond({ ...base, tunnel: tunnel('ACTIVE') })).toBe(false)
    expect(ecarteDuBond({ ...base, tunnel: { ...tunnel('ACTIVE'), state: null, in_bond: false } })).toBe(false)
    expect(ecarteDuBond({ ...base, tunnel: tunnel('WAN_DOWN') })).toBe(true)
  })

  it('le plafond de péremption tient, quelle que soit la période', () => {
    // `STALE_CEILING_SECONDS = Infinity` laissait 195 verts : une période de
    // scrutation aberrante (un équipement à 90 s) rendait le seuil illimité,
    // et une mesure de dix minutes passait pour fraîche.
    expect(staleAfterSeconds(90)).toBe(120)
    expect(staleAfterSeconds(1000)).toBe(120)
    const samples = [sample({ wan_id: '4' })]
    expect(linkVerdict({ samples, status: 'connecté', ageSeconds: 600, periodSeconds: 1000 }).stale).toBe(true)
  })

  it('un lien sous le seuil du routeur n’est pas écarté, quel que soit son écart', () => {
    // La règle est celle du ROUTEUR (`beyond_cutoff`), pas une approximation
    // de l'écran : 353 ms contre 19 ms sous un seuil de 500 ms ne dit rien.
    const samples = [
      sample({ wan_id: '4', tunnel: tunnel('ACTIVE', 19) }),
      sample({ wan_id: '5', tunnel: tunnel('ACTIVE', 353, { cutoff_ms: 500, beyond_cutoff: false }) }),
    ]
    const v = linkVerdict({ samples, status: 'connecté', latencyThresholdMs: 1000 })
    expect(v.verdict).toBe('ok')
    const ecarte = [
      samples[0],
      sample({ wan_id: '5', tunnel: tunnel('ACTIVE', 620, { cutoff_ms: 500, beyond_cutoff: true }) }),
    ]
    expect(linkVerdict({ samples: ecarte, status: 'connecté', latencyThresholdMs: 1000 }).verdict).toBe('degrade')
  })
})
