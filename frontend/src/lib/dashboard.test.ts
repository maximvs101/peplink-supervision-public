import { describe, expect, it } from 'vitest'

import { keyFigures } from './dashboard'
import type { RadioSample, TunnelLink } from '../types'

/**
 * Ce module n'avait AUCUN test — les huit autres modules de `lib/` en ont un.
 * C'est pourtant lui qui produit les quatre chiffres qu'on lit en arrivant, et
 * la règle qu'il applique est celle qui gouverne tout l'outil : une grandeur
 * non mesurée vaut `null` et s'affiche « — », jamais zéro.
 */

const tunnel = (over: Partial<TunnelLink> = {}): TunnelLink => ({
  state: 'ACTIVE', in_bond: true, rtt_ms: 30,
  loss_percent: null, out_of_order_percent: null,
  loss_percent_up: null, loss_percent_down: null,
  out_of_order_percent_up: null, out_of_order_percent_down: null,
  packets_up: null, packets_down: null, tx_kbps: 100, rx_kbps: 400,
  repair_sent: null, repair_used: null, cutoff_ms: null, beyond_cutoff: false, peer_state: null, cause_scope: null,
  ...over,
})

const lien = (
  wan_id: string,
  over: { connected?: boolean; tx?: number | null; rx?: number | null;
          tunnel?: TunnelLink | null; path?: number | null } = {},
): RadioSample =>
  ({
    wan: {
      wan_id, kind: 'cellular',
      connected: over.connected ?? true,
      tx_kbps: over.tx === undefined ? 200 : over.tx,
      rx_kbps: over.rx === undefined ? 800 : over.rx,
      tunnel: over.tunnel === undefined ? tunnel() : over.tunnel,
      path_latency_recent_ms: over.path ?? null,
    },
  }) as unknown as RadioSample

describe('une absence n’est jamais un zéro', () => {
  it('rend null — et non 0 — quand aucun lien ne mesure de débit', () => {
    const k = keyFigures([lien('4', { tx: null, rx: null })])
    expect(k.txKbps).toBeNull()
    expect(k.rxKbps).toBeNull()
  })

  it('somme ce qui EST mesuré, sans compter les absents pour zéro', () => {
    const k = keyFigures([lien('4', { tx: 200 }), lien('5', { tx: null })])
    expect(k.txKbps).toBe(200)
  })

  it('rend null pour le bond quand aucun tunnel n’existe', () => {
    const k = keyFigures([lien('4', { tunnel: null })])
    expect(k.hasTunnel).toBe(false)
    expect(k.bondTxKbps).toBeNull()
    expect(k.bondRxKbps).toBeNull()
    expect(k.rttMedianMs).toBeNull()
    expect(k.rttWorstMs).toBeNull()
  })

  it('ne conclut pas « hors du bond » sur un châssis sans profil', () => {
    // Un lien sans objet tunnel n'est pas écarté : l'équipement n'a peut-être
    // aucun profil SpeedFusion. Les liens connectés portent alors le flux.
    const k = keyFigures([lien('4', { tunnel: null }), lien('5', { tunnel: null })])
    expect(k.carrying).toBe(2)
    expect(k.excludedByLatency).toBe(0)
  })
})

describe('ce qui porte le flux', () => {
  it('retire du compte les liens que le ROUTEUR a écartés', () => {
    // L'écran annonçait « 5 liens portent » pendant que la table marquait l'un
    // d'eux « au-delà du seuil » — exactement l'inverse, à un coup d'œil d'écart.
    const k = keyFigures([
      lien('4'),
      lien('5', { tunnel: tunnel({ beyond_cutoff: true }) }),
      lien('6'),
    ])
    expect(k.carrying).toBe(2)
    expect(k.excludedByLatency).toBe(1)
    expect(k.total).toBe(3)
  })

  it('ne compte pas un lien tombé', () => {
    const k = keyFigures([lien('4'), lien('5', { connected: false })])
    expect(k.carrying).toBe(1)
    expect(k.total).toBe(2), 'le total reste le nombre de liens supervisés'
  })

  it('ne compte pas dans le bond un lien que le routeur en a sorti', () => {
    const k = keyFigures([lien('4'), lien('5', { tunnel: tunnel({ in_bond: false }) })])
    expect(k.carrying).toBe(1)
    expect(k.bondTxKbps).toBe(100)
  })
})

describe('un état de tunnel inconnu n’est pas « ne porte pas » (C-18)', () => {
  it('rend null quand aucun lien connecté n’a d’état connu', () => {
    // Deux liens sans état de tunnel : le bandeau disait « Liaison OK — 2
    // liens sur 2 connectés » et le plan « 0 / 2 liens qui portent ». Le
    // verdict pose qu'un état null n'est ni porteur ni écarté.
    const inconnu = tunnel({ state: null, in_bond: false })
    const k = keyFigures([lien('4', { tunnel: inconnu }), lien('5', { tunnel: inconnu })])
    expect(k.carrying).toBeNull()
  })

  it('compte sur les liens dont l’état est connu', () => {
    const inconnu = tunnel({ state: null, in_bond: false })
    const k = keyFigures([lien('4'), lien('5', { tunnel: inconnu })])
    expect(k.carrying).toBe(1)
  })
})

describe('la latence', () => {
  it('prend la MÉDIANE, que la moyenne trahirait', () => {
    // Un lien à 1 929 ms au milieu de trois à 30 ms déplacerait la moyenne de
    // 470 ms. C'est le relevé le plus instructif de toute la base.
    const k = keyFigures([
      lien('4', { tunnel: tunnel({ rtt_ms: 30 }) }),
      lien('5', { tunnel: tunnel({ rtt_ms: 30 }) }),
      lien('6', { tunnel: tunnel({ rtt_ms: 30 }) }),
      lien('7', { tunnel: tunnel({ rtt_ms: 1929 }) }),
    ])
    expect(k.rttMedianMs).toBe(30)
    // …et le pire est dit à côté, pour que la médiane ne le cache pas.
    expect(k.rttWorstMs).toBe(1929)
  })

  it('interpole sur un nombre PAIR de liens', () => {
    const k = keyFigures([
      lien('4', { tunnel: tunnel({ rtt_ms: 20 }) }),
      lien('5', { tunnel: tunnel({ rtt_ms: 40 }) }),
    ])
    expect(k.rttMedianMs).toBe(30)
  })

  it('ignore un lien du bond dont la latence n’est pas mesurée', () => {
    const k = keyFigures([
      lien('4', { tunnel: tunnel({ rtt_ms: 50 }) }),
      lien('5', { tunnel: tunnel({ rtt_ms: null }) }),
    ])
    expect(k.rttMedianMs).toBe(50)
    expect(k.rttWorstMs).toBe(50)
  })
})

describe('la latence du lien, quand le tunnel n’en donne aucune (R-1.3)', () => {
  it('reste sur le tunnel dès qu’un lien du bond en mesure une', () => {
    const k = keyFigures([
      lien('4', { tunnel: tunnel({ rtt_ms: 40 }), path: 20 }),
      lien('5', { tunnel: tunnel({ rtt_ms: null }), path: 22 }),
    ])
    expect([k.rttSource, k.rttMedianMs]).toEqual(['bond', 40])
  })

  it('passe sur la latence des liens CONNECTÉS, sans mélanger les deux', () => {
    // Le Dome dont le tunnel n'a jamais tenu : « — » alors que le routeur
    // mesure 30 ms sur chaque SIM.
    const k = keyFigures([
      lien('3', { tunnel: tunnel({ rtt_ms: null, in_bond: false }), path: 30 }),
      lien('5', { tunnel: null, path: 34 }),
      lien('6', { tunnel: null, path: 80 }),
      lien('7', { connected: false, tunnel: null, path: 900 }),
    ])
    expect([k.rttSource, k.rttMedianMs, k.rttWorstMs]).toEqual(['lien', 34, 80])
  })

  it('ne fabrique rien sans aucune des deux mesures', () => {
    const k = keyFigures([lien('4', { tunnel: tunnel({ rtt_ms: null }) })])
    expect([k.rttSource, k.rttMedianMs, k.rttWorstMs]).toEqual([null, null, null])
  })
})

describe('aucun relevé', () => {
  it('ne fabrique rien', () => {
    const k = keyFigures([])
    expect(k).toMatchObject({
      txKbps: null, rxKbps: null, bondTxKbps: null, bondRxKbps: null,
      carrying: 0, excludedByLatency: 0, total: 0,
      rttMedianMs: null, rttWorstMs: null, rttSource: null, hasTunnel: false,
    })
  })
})
