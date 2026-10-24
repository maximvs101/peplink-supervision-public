import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { DeviceCapabilities, DeviceInfo, LanClients } from '../types'
import { DeviceCard } from './DeviceCard'

const device = (over: Partial<DeviceInfo> = {}): DeviceInfo => ({
  model: 'MAX BR1 Pro 5G', product_code: 'MAX-BR1-PRO-5GH', serial: '0000-0000-0011',
  firmware: '8.5.1', hardware_revision: '1', name: 'Camion 12',
  uptime_s: null, cpu_load_percent: null, power_watts: null, power_sources: [], mac_lan: null,
  temperature_c: null, power_load_percent: null, care_plan: null, care_plan_active: null,
  pepvpn_version: null, timezone: null, config_activating: false, remote_assistance: null, clock_offset_s: null,
  ...over,
})

/**
 * La position GPS est une mesure de l'équipement, au même titre que sa charge
 * CPU. La carte géographique en était l'unique lecteur ; elle est retirée, et
 * un champ que plus personne n'affiche est le défaut qu'on corrige ici.
 */
describe('position GPS de l’équipement', () => {
  it('affiche la position en degrés décimaux', () => {
    render(<DeviceCard device={device()} capabilities={null} siteLat={48.85641} siteLon={2.35234} />)
    expect(screen.getByText('Position GPS')).toBeInTheDocument()
    expect(screen.getByText('48.85641, 2.35234')).toBeInTheDocument()
  })

  it('n’affiche rien quand la position manque', () => {
    // GPS non fixé ou modèle sans récepteur : la ligne disparaît entièrement
    // plutôt que d'ajouter un tiret de plus à la carte d'identité.
    render(<DeviceCard device={device()} capabilities={null} siteLat={null} siteLon={null} />)
    expect(screen.queryByText('Position GPS')).not.toBeInTheDocument()
  })

  it('n’affiche rien quand une seule des deux coordonnées est connue', () => {
    // Une latitude sans longitude ne désigne aucun point : la demi-mesure serait
    // plus trompeuse que l'absence.
    render(<DeviceCard device={device()} capabilities={null} siteLat={48.85641} siteLon={null} />)
    expect(screen.queryByText('Position GPS')).not.toBeInTheDocument()
  })
})


const capabilities = (over: Partial<DeviceCapabilities> = {}): DeviceCapabilities => ({
  gps: false, pepvpn: true, wan_types: { '1': 'ethernet', '4': 'cellular' }, lan_repurposed: [],
  lan_networks: [], usb_modem_slots: 0, sim_slots: 2, per_wan_throughput: true,
  provisional: false, source: 'declared', sfc_licensed: null, sfc_suspended: null,
  sfc_throughput_limit_mbps: null, pepvpn_max_active_profiles: null,
  sfc_expires_at: null, sfc_quota_remaining_mb: null, sfc_quota_limit_mb: null, ...over,
})

describe('réseaux LAN du châssis', () => {
  it('nomme le LAN natif et les VLAN, tels que status.port les publie', () => {
    // « C'est quoi cette adresse en 10.10.1.1 ? je pensais que le routeur était
    // sur 192.168.56.1. » Les deux : un LAN natif et un VLAN pour le Wi-Fi. La
    // réponse était dans une charge utile déjà téléchargée ; les adresses, non —
    // `config.lan` reste interdit, et la carte ne prétend pas les connaître.
    render(<DeviceCard device={device()} siteLat={null} siteLon={null}
      capabilities={capabilities({ lan_networks: [
        { vlan_id: '0', name: 'RESEAU-DEMO_EXT1' }, { vlan_id: '1', name: 'WIFI Range' },
      ] })} />)
    expect(screen.getByText('Réseaux LAN')).toBeInTheDocument()
    expect(screen.getByText('RESEAU-DEMO_EXT1 (natif) · WIFI Range (VLAN 1)')).toBeInTheDocument()
  })

  it('ne fait pas une page blanche d’un serveur qui ne publie pas encore le champ', () => {
    // Un serveur relancé après l'écran, ou l'inverse : le champ manque, et
    // `undefined.length` effaçait TOUT l'écran de l'équipement — vu le 19
    // septembre, sur le HD2, juste après la construction du bundle.
    const anciennes = capabilities()
    delete (anciennes as Partial<DeviceCapabilities>).lan_networks
    render(<DeviceCard device={device()} siteLat={null} siteLon={null} capabilities={anciennes} />)
    expect(screen.getByText('Numéro de série')).toBeInTheDocument()
    expect(screen.queryByText(/Réseaux? LAN/)).toBeNull()
  })

  it('se tait quand la fiche n’a pas lu status.port', () => {
    // Une fiche sondée ne connaît aucun réseau : ce vide n'affirme rien, et
    // surtout pas « aucun LAN ».
    render(<DeviceCard device={device()} siteLat={null} siteLon={null} capabilities={capabilities()} />)
    expect(screen.queryByText(/Réseaux? LAN/)).toBeNull()
  })
})

describe('licence SpeedFusion Cloud (R-1.5)', () => {
  // Un instant FIXE, passé en paramètre : l'échéance se lit par rapport à lui.
  const LE_24_SEPTEMBRE = Date.parse('2026-09-24T12:00:00Z')
  const carte = (over: Partial<DeviceCapabilities>) =>
    render(<DeviceCard device={device()} siteLat={null} siteLon={null} maintenant={LE_24_SEPTEMBRE}
      capabilities={capabilities({ sfc_licensed: true, sfc_suspended: false, ...over })} />)

  it('dit l’échéance et le quota comme un fait, sans badge à 99 jours', () => {
    carte({ sfc_expires_at: '2027-01-01T00:00:00Z',
            sfc_quota_remaining_mb: 3145728, sfc_quota_limit_mb: 3145728 })
    expect(screen.getByText('jusqu’au 01/01/2027 · quota : reste 3 To sur 3 To')).toBeInTheDocument()
    expect(screen.queryByText(/échéance dans/)).not.toBeInTheDocument()
    expect(screen.queryByText('SpeedFusion Cloud échu')).not.toBeInTheDocument()
  })

  it('prévient dans les 30 derniers jours, puis dit l’échéance passée', () => {
    const { unmount } = carte({ sfc_expires_at: '2026-10-10T00:00:00Z' })
    expect(screen.getByText('SpeedFusion Cloud : échéance dans 16 jours')).toBeInTheDocument()
    unmount()
    carte({ sfc_expires_at: '2023-01-19T00:00:00Z' })
    expect(screen.getByText('SpeedFusion Cloud échu')).toBeInTheDocument()
    expect(screen.getByText('échu le 19/01/2023')).toBeInTheDocument()
  })

  it('tait un quota sans son total', () => {
    carte({ sfc_expires_at: '2027-01-01T00:00:00Z', sfc_quota_remaining_mb: 1024, sfc_quota_limit_mb: null })
    expect(screen.getByText('jusqu’au 01/01/2027')).toBeInTheDocument()
  })

  it('ne dit rien sans licence', () => {
    carte({ sfc_licensed: false, sfc_expires_at: null })
    expect(screen.queryByText('SpeedFusion Cloud')).not.toBeInTheDocument()
  })
})

describe('clients du LAN (R-1.10)', () => {
  const carte = (lanClients: LanClients | null) =>
    render(<DeviceCard device={device()} capabilities={null} siteLat={null} siteLon={null}
      lanClients={lanClients} />)

  it('compte les appareils qui dépendent de la liaison, dont ce poste', () => {
    carte({ active: 3, wireless: 2, wired: 1, includes_this_host: true })
    expect(screen.getByText('3 actifs — 2 Wi-Fi, 1 filaire, dont ce poste')).toBeInTheDocument()
  })

  it('ne nomme que les colonnes non vides, et dit les raccordements inconnus', () => {
    const { unmount } = carte({ active: 1, wireless: 1, wired: 0, includes_this_host: false })
    expect(screen.getByText('1 actif — 1 Wi-Fi')).toBeInTheDocument()
    unmount()
    carte({ active: 4, wireless: 1, wired: 2, includes_this_host: null })
    expect(screen.getByText('4 actifs — 1 Wi-Fi, 2 filaires, 1 autre')).toBeInTheDocument()
  })

  it('dit un vrai zéro, et tait une absence de lecture', () => {
    const { unmount } = carte({ active: 0, wireless: 0, wired: 0, includes_this_host: false })
    expect(screen.getByText('aucun client actif')).toBeInTheDocument()
    unmount()
    carte(null)
    expect(screen.queryByText('Clients du LAN')).not.toBeInTheDocument()
  })
})

describe('horloge du routeur (R-3.9)', () => {
  it('ne dit rien d’une horloge à l’heure, et dit l’écart d’une horloge décalée', () => {
    const { unmount } = render(<DeviceCard device={device({ clock_offset_s: 2 })} capabilities={null}
      siteLat={null} siteLon={null} />)
    expect(screen.queryByText('Horloge du routeur')).not.toBeInTheDocument()
    unmount()
    render(<DeviceCard device={device({ clock_offset_s: -754 })} capabilities={null} siteLat={null} siteLon={null} />)
    expect(screen.getByText('12 min 34 s en retard sur ce poste')).toBeInTheDocument()
  })
})

describe('un numéro de série non publié', () => {
  it('se dit « — » au lieu de disparaître', () => {
    // Le serveur ne publie plus la série à un client non local : la ligne
    // disparaissait, et l'absence se lisait comme un châssis sans série.
    render(<DeviceCard device={device({ serial: null })} capabilities={null} siteLat={null} siteLon={null} />)
    const cle = screen.getByText('Numéro de série')
    expect(cle.nextElementSibling?.textContent).toBe('—')
  })
})

describe('un état stable n’est pas un avertissement (C-44)', () => {
  it('dit le support expiré sans badge d’alerte permanent', () => {
    const { container } = render(<DeviceCard device={{ ...device(), care_plan_active: false }}
      capabilities={null} siteLat={null} siteLon={null} />)
    const badge = [...container.querySelectorAll('.badge')].find((b) => b.textContent === 'support expiré')
    expect(badge).toBeDefined()
    expect(badge!.classList.contains('warn')).toBe(false)
  })
})
