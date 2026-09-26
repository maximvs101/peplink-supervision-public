/**
 * Les fabriques de relevés et le câblage des panneaux, pour tous les tests
 * d'écran.
 *
 * Elles vivaient en deux exemplaires — `scenarios.test.tsx` et
 * `confrontation.test.tsx` —, plus un troisième câblage de `BondPlan` dans un
 * `PlanSeul` local. `BondPlan` prend quinze props ; le renommage de `sameCell`
 * en `cellGroups` a dû être fait à huit endroits dans deux fichiers, et les
 * deux `monter` avaient déjà divergé en silence (`routerId` « x » d'un côté,
 * « mbx » de l'autre). Un seul endroit.
 *
 * Deux jeux de valeurs par défaut, et c'est voulu :
 *   - `wanRealiste` : une SIM Orange en LTE B3, mesurée, comme sur le matériel
 *     de référence — pour les scénarios qui parlent de ce qui existe ;
 *   - `wanNu` : tout à `null` — pour la confrontation, où l'on vérifie qu'une
 *     absence n'est jamais rendue par un zéro.
 */

import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import { BondPlan } from '../components/BondPlan'
import { LinkTable } from '../components/LinkTable'
import { keyFigures } from '../lib/dashboard'
import { pathDiversity } from '../lib/diversity'
import { compareWanIds } from '../lib/wanOrder'
import type { RadioSample, TunnelLink, WanRadioStatus } from '../types'

export const wanRealiste = (over: Partial<WanRadioStatus> = {}): WanRadioStatus =>
  ({
    kind: 'cellular', wan_id: '4', wan_name: 'SIM', connected: true,
    operator: 'Orange', operator_name: 'Orange', plmn: '208-01', rat: 'LTE', band: 'B3',
    earfcn: '1300', pci: null, cell_id: '25735436', tac: '52641',
    enb_id: '100529', sector_id: '12',
    rsrp: -88, rsrq: -11, sinr: 15, rssi: null, signal_bars: null,
    bands: [], sim_slots: [], wifi: null, path_latency_ms: null, path_latency_recent_ms: null, path_latency_at: null, modem_temperature_c: null, modem_temperature_at: null, ip: null, priority: 1, tunnel: null,
    tx_kbps: 300, rx_kbps: 1500, uptime_s: 3600, status_message: null, disabled: false,
    // Tous les champs du contrat, à `null` comme le fil les envoie : une clé
    // omise vaut `undefined`, ce que le serveur n'écrit jamais, et un test
    // pouvait passer sur une forme que la production ne produit pas.
    roaming_status: null, modem_vendor: null, modem_model: null, modem_firmware: null,
    mtu: null, carrier_aggregation: null, network_mode: null, allowance: null,
    period_rx_mb: null, period_tx_mb: null,
    ...over,
  }) satisfies WanRadioStatus

export const wanNu = (over: Partial<WanRadioStatus> = {}): WanRadioStatus =>
  ({
    kind: 'cellular', wan_id: '1', wan_name: 'SIM', connected: true,
    operator: null, operator_name: null, plmn: null, rat: null, band: null,
    earfcn: null, pci: null, cell_id: null, tac: null,
    enb_id: null, sector_id: null,
    rsrp: null, rsrq: null, sinr: null, rssi: null, signal_bars: null,
    bands: [], sim_slots: [], wifi: null, path_latency_ms: null, path_latency_recent_ms: null, path_latency_at: null, modem_temperature_c: null, modem_temperature_at: null, ip: null,
    priority: null, tunnel: null,
    tx_kbps: null, rx_kbps: null, uptime_s: null, status_message: null, disabled: false,
    roaming_status: null, modem_vendor: null, modem_model: null, modem_firmware: null,
    mtu: null, carrier_aggregation: null, network_mode: null, allowance: null,
    period_rx_mb: null, period_tx_mb: null,
    ...over,
  }) satisfies WanRadioStatus

/** Un relevé autour d'un WAN déjà construit. */
export const releve = (wan: WanRadioStatus): RadioSample => ({
  router_id: 'r', router_name: 'R', ts: '2026-09-11T14:00:00Z',
  site_lat: null, site_lon: null, cell_changes: 0, disconnects: 0,
  wan,
})

export const sampleRealiste = (over: Partial<WanRadioStatus> = {}): RadioSample =>
  releve(wanRealiste(over))
export const sampleNu = (over: Partial<WanRadioStatus> = {}): RadioSample =>
  releve(wanNu(over))

/** La portée de la cause, telle que le serveur la classe : « pair » pour sa
 *  sentinelle PEER_DOWN, « lien » pour un état du routeur qui écarte, rien
 *  quand le lien porte ou que l'état est inconnu. Dérivée ici pour que les
 *  fabriques ne la recopient pas à la main. */
export const causeScope = (state: string | null, inBond: boolean): TunnelLink['cause_scope'] =>
  state === null || inBond ? null : state === 'PEER_DOWN' ? 'pair' : 'lien'

/** Un lien de tunnel tel que le serveur le sérialise. `in_bond` vaut
 *  `state === "ACTIVE"` là-bas ; ici il se passe explicitement, pour pouvoir
 *  écrire les cas limites. `cause_scope` suit `state` et `in_bond`. */
export const tunnelRealiste = (over: Partial<TunnelLink> = {}): TunnelLink => ({
  state: 'ACTIVE', in_bond: true, rtt_ms: 30,
  loss_percent: null, out_of_order_percent: null,
  loss_percent_up: null, loss_percent_down: null,
  out_of_order_percent_up: null, out_of_order_percent_down: null,
  packets_up: null, packets_down: null,
  tx_kbps: 250, rx_kbps: 1400,
  repair_sent: null, repair_used: null,
  cutoff_ms: null, beyond_cutoff: false, peer_state: null,
  ...over,
  cause_scope: causeScope(over.state ?? 'ACTIVE', over.in_bond ?? (over.state ?? 'ACTIVE') === 'ACTIVE'),
})

export const tunnelNu = (over: Partial<TunnelLink> = {}): TunnelLink =>
  tunnelRealiste({ rtt_ms: null, tx_kbps: null, rx_kbps: null, ...over })

/** Le plan SEUL, avec ses dérivations — pour les tests qui le re-rendent avec
 *  un autre jeu de liens. */
export function PlanSeul({ samples, routerId = 'mbx' }: { samples: RadioSample[]; routerId?: string }) {
  const chiffres = keyFigures(samples)
  const diversite = pathDiversity(samples)
  return (
    <BondPlan
      samples={samples} figures={chiffres}
      redundancy={{ state: 'unknown', packets: 0 }}
      cells={diversite.cells} sites={diversite.sites}
      located={diversite.located} attached={diversite.attached}
      role={{ kind: 'inconnu' }} grievances={[]}
      tunnelUptimeS={null} throughputLimitMbps={null} routerId={routerId}
      cellGroups={diversite.cellGroups}
    />
  )
}

/** Le tableau de bord tel que l'écran l'assemble : le plan, puis la table. Ce
 *  sont les deux objets qui portent toute la lecture. */
export function monterTableau(samples: RadioSample[], showThroughput = true) {
  const chiffres = keyFigures(samples)
  const diversite = pathDiversity(samples)
  const ordonnes = [...samples].sort((a, b) => compareWanIds(a.wan.wan_id, b.wan.wan_id))
  return render(
    <MemoryRouter>
      <PlanSeul samples={ordonnes} />
      <LinkTable
        routerId="r"
        samples={ordonnes}
        showThroughput={showThroughput}
        hasTunnel={chiffres.hasTunnel}
        sameCell={diversite.sameCell}
        timeline={null}
        windowLabel="Depuis la mise sous tension"
        churn={[]}
        latencyThresholdMs={1000} radioGrievances={[]}
      />
    </MemoryRouter>,
  )
}
