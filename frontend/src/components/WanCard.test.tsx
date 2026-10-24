import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { RadioSample, TunnelLink, WanRadioStatus } from '../types'
import { WanCard } from './WanCard'
import { causeScope, monterTableau } from '../test/factories'

function sample(wan: Partial<WanRadioStatus>): RadioSample {
  return {
    router_id: 'r', router_name: 'R', ts: '2026-08-30T14:00:00Z',
    site_lat: null, site_lon: null, cell_changes: 0, disconnects: 0,
    wan: {
      kind: 'cellular', wan_id: '4', wan_name: 'SIM Orange', connected: true,
      operator: 'Orange', operator_name: 'Orange', plmn: '208-01', rat: 'LTE-A', band: 'B3',
      earfcn: '1300', pci: null, cell_id: '25735433', tac: '52641',
      enb_id: '100529', sector_id: '9',
      rsrp: -88, rsrq: -11, sinr: 15, rssi: -54, signal_bars: 4,
      bands: [], sim_slots: [], wifi: null, path_latency_ms: null, path_latency_recent_ms: null, path_latency_at: null, modem_temperature_c: null, modem_temperature_at: null, ip: null, priority: 1, tunnel: null,
      tx_kbps: 0, rx_kbps: 0, uptime_s: 3600, status_message: 'Connected', disabled: false,
      roaming_status: null, modem_vendor: null, modem_model: null, modem_firmware: null,
      mtu: null, carrier_aggregation: null, network_mode: null, allowance: null,
      period_rx_mb: null, period_tx_mb: null,
      ...wan,
    } satisfies WanRadioStatus,
  }
}

// Le décompte par défaut dépasse le seuil de publication d'un taux : sans lui,
// chaque cas de ce fichier se jouerait sur le tunnel au repos, où aucun taux
// n'est publié, et les assertions sur la perte ne vérifieraient plus rien.
const tunnel = (over: Partial<TunnelLink> = {}): TunnelLink => ({
  state: 'ACTIVE', in_bond: true, rtt_ms: 25,
  loss_percent: 0, out_of_order_percent: 0,
  loss_percent_up: 0, loss_percent_down: 0,
  out_of_order_percent_up: 0, out_of_order_percent_down: 0,
  packets_up: 12000, packets_down: 12000,
    tx_kbps: null, rx_kbps: null,
    repair_sent: null, repair_used: null,
  cutoff_ms: null, beyond_cutoff: false, peer_state: null,
  ...over,
  // Dérivé comme le serveur le fait : la portée suit l'état.
  cause_scope: causeScope(over.state ?? 'ACTIVE', over.in_bond ?? true),
})

/** Valeur affichée en face d'un libellé de la grille de détails. */
const detailValue = (container: HTMLElement, label: string) =>
  [...container.querySelectorAll('.details > div')]
    .find((cell) => cell.querySelector('.detail-key')?.textContent === label)
    ?.querySelector('.detail-val')?.textContent

const WIRED: Partial<WanRadioStatus> = {
  kind: 'ethernet', wan_id: '1', wan_name: 'WAN 1', rat: 'Ethernet', band: null,
  ip: '10.231.114.2', rsrp: null, rsrq: null, sinr: null, rssi: null,
  cell_id: null, tac: null, earfcn: null, enb_id: null, sector_id: null,
  rx_kbps: 194309, tx_kbps: 12923,
}

// Les libellés sont mis en majuscules par CSS : le DOM garde la casse d'origine.
// Interroger « CELL ID » ne correspondrait à rien, et rendrait vides de sens les
// assertions négatives.
describe('carte d’un lien filaire', () => {
  it('n’affiche aucune grandeur radio', () => {
    // Sur une fibre, SINR et RSRP ne sont pas absents : ils n'ont pas de sens.
    // Les afficher marqués « non fourni » laisserait croire à des mesures perdues.
    render(<WanCard sample={sample(WIRED)} />)
    expect(screen.queryByText('SINR')).not.toBeInTheDocument()
    expect(screen.queryByText('RSRP')).not.toBeInTheDocument()
    expect(screen.queryByText('RSRQ')).not.toBeInTheDocument()
    expect(screen.queryByText('Cell ID')).not.toBeInTheDocument()
    // « TAC » et non plus « PCI » : le PCI a quitté la grille cellulaire aussi,
    // et une assertion négative sur un libellé qui n'existe plus nulle part ne
    // distingue plus le filaire du cellulaire — elle passerait toute seule.
    expect(screen.queryByText('TAC')).not.toBeInTheDocument()
  })

  it('affiche ce qu’un lien filaire porte vraiment', () => {
    render(<WanCard sample={sample(WIRED)} />)
    expect(screen.getByText('10.231.114.2')).toBeInTheDocument()
    expect(screen.getByText('194,3')).toBeInTheDocument()   // Mbit/s descendants
    expect(screen.getByText('12,9')).toBeInTheDocument()    // Mbit/s montants
    expect(screen.getByText('Connected')).toBeInTheDocument()
  })

  it('ne note pas les débits', () => {
    render(<WanCard sample={sample(WIRED)} />)
    for (const note of ['Excellent', 'Bon', 'Moyen', 'Faible']) {
      expect(screen.queryByText(note)).not.toBeInTheDocument()
    }
  })

  it('dit qu’un lien coupé est coupé', () => {
    render(<WanCard sample={sample({ ...WIRED, connected: false })} />)
    expect(screen.getByText(/Lien filaire coupé/)).toBeInTheDocument()
  })
})

describe('carte d’un lien cellulaire', () => {
  it('note les grandeurs qui ont des seuils, pas le débit', () => {
    const { container } = render(<WanCard sample={sample({})} />)
    const grade = (label: string) =>
      [...container.querySelectorAll('.tile')]
        .find((t) => t.querySelector('.tile-label')?.textContent === label)
        ?.querySelector('.tile-grade')?.textContent

    expect(grade('RSRP')).toBe('Bon')
    expect(grade('SINR')).toBe('Bon')
    // Le débit est affiché, mais sans note : 0 Mbit/s n'est pas « excellent ».
    expect(screen.getByText('Débit descendant')).toBeInTheDocument()
    expect(grade('Débit descendant')?.trim()).toBe('')
  })

  it('expose la station de base et le secteur', () => {
    // Deux SIM partageant l'eNodeB n'ont aucune diversité de trajet : la
    // décomposition de l'ECI est ce qui le révèle.
    render(<WanCard sample={sample({})} />)
    expect(screen.getByText('Station (eNodeB)')).toBeInTheDocument()
    // Un identifiant s'écrit tel quel : « 100 529 » ne se recolle pas à ce
    // qu'affiche le routeur. Ce test affirmait la forme avec séparateur.
    expect(screen.getByText('100529')).toBeInTheDocument()
    expect(screen.getByText('Secteur')).toBeInTheDocument()
  })

  it('masque le débit quand l’équipement n’en fournit pas', () => {
    render(<WanCard sample={sample({})} showThroughput={false} />)
    expect(screen.queryByText('Débit descendant')).not.toBeInTheDocument()
    expect(screen.getByText('SINR')).toBeInTheDocument()
  })
})

describe('lien dans le bond', () => {
  it('lit `in_bond` plutôt que le libellé du firmware', () => {
    // Le serveur tranche ; deviner par le préfixe de l'état revenait à parier
    // sur des valeurs qu'on n'a pas toutes observées.
    const { container } = render(<WanCard sample={sample({ tunnel: tunnel() })} />)
    expect(detailValue(container, 'Participation')).toBe('porte le flux')
  })

  it('traduit les états qui portent un remède', () => {
    const { container } = render(
      <WanCard sample={sample({ tunnel: tunnel({ state: 'PEER_DOWN', in_bond: false }) })} />,
    )
    // « Pair injoignable » était une conclusion que le routeur ne tire pas (R-1.5).
    expect(detailValue(container, 'Participation')).toBe('pair : aucun pair connecté')
  })

  it('dit le mot du routeur sous un pair tombé', () => {
    const { container } = render(<WanCard sample={sample({
      tunnel: tunnel({ state: 'PEER_DOWN', in_bond: false, peer_state: 'START' }),
    })} />)
    expect(detailValue(container, 'Participation')).toBe('pair : tentative en cours (START)')
  })

  it('n’affirme rien sur un état inconnu', () => {
    // Un `state` à null veut dire inconnu : ni porteur, ni écarté du bond.
    const { container } = render(
      <WanCard sample={sample({ tunnel: tunnel({ state: null, in_bond: false }) })} />,
    )
    expect(detailValue(container, 'Participation')).toBe('—')
  })
})

/**
 * La grille de détails affirmait cinq choses que le matériel ne dit pas : un
 * niveau de barres figé à 5 sur 12 129 relevés, un PCI que le backend annule
 * lui-même, un RSSI qui suit le RSRP à une constante près, un canal que la bande
 * en badge donne déjà, et un PLMN qui n'a jamais valu autre chose que 208-01.
 * Une ligne qui ne varie pas ne se lit pas comme une constante : elle se lit
 * comme une mesure, et rassure d'autant plus qu'elle ne bouge jamais.
 */
describe('grille cellulaire vidée de ce qui ne varie pas', () => {
  it('ne montre plus les lignes qui n’ont jamais eu qu’une valeur', () => {
    render(<WanCard sample={sample({})} />)
    for (const gone of ['PCI', 'RSSI', 'Barres', 'PLMN', 'EARFCN']) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument()
    }
  })

  it('garde les identifiants de cellule, qui bougent vraiment', () => {
    // Six Cell ID et trois TAC distincts sur la même période de relevés : c'est
    // là-dessus que se lit un changement de site, donc ils restent.
    render(<WanCard sample={sample({})} />)
    expect(screen.getByText('Cell ID')).toBeInTheDocument()
    expect(screen.getByText('52641')).toBeInTheDocument()
  })

  it('garde le RSSI sur le Wi-Fi, où il est la mesure principale', () => {
    // Le Wi-Fi n'a pas de RSRP derrière lui : sans RSSI il ne resterait aucune
    // grandeur à noter, et wifiQuality() n'aurait plus rien à juger.
    render(
      <WanCard
        sample={sample({
          kind: 'wifi', rat: 'Wi-Fi', rssi: -58, rsrp: null, rsrq: null, sinr: null,
          wifi: { ssid: 'Régie', bssid: null, channel: '36', band: '5 GHz',
            signal_dbm: -58, signal_level: 4 },
        })}
      />,
    )
    expect(screen.getByText('Force du signal')).toBeInTheDocument()
    expect(screen.getByText('-58')).toBeInTheDocument()
  })
})

/**
 * La priorité vaut 1 sur tous les WAN exploitables du MAX HD4 MBX. Elle n'est
 * pourtant pas décorative partout : sur le Dome Pro Duo, les deux SIM sont à 2
 * derrière le WAN filaire. La supprimer aurait aveuglé ce modèle ; la laisser
 * affichait « 1 » à l'infini sur l'autre.
 */
describe('priorité sortante', () => {
  it('reste muette sur un lien de priorité 1', () => {
    render(<WanCard sample={sample({ priority: 1 })} />)
    expect(screen.queryByText('Priorité sortante')).not.toBeInTheDocument()
  })

  it('annonce un lien de secours quand il attend derrière un autre', () => {
    const { container } = render(<WanCard sample={sample({ priority: 2 })} enAttente />)
    expect(detailValue(container, 'Priorité sortante')).toBe('2 — secours')
  })

  it('ne dit pas « secours » d’un lien de priorité 2 qui n’attend derrière rien', () => {
    // HD2 réel : la SIM 5 (priorité 2) portait tout le trafic, le WAN 1 de
    // priorité 1 sans câble — et la carte la disait « secours ».
    const { container } = render(<WanCard sample={sample({ priority: 2 })} enAttente={false} />)
    expect(detailValue(container, 'Priorité sortante')).toBe('2')
  })

  it('ne met pas « secours » dans la table quand le lien prioritaire est sans câble', () => {
    const wan1 = sample({ wan_id: '1', kind: 'ethernet', priority: 1, connected: false, status_message: 'No Cable Detected' })
    const sim5 = sample({ wan_id: '5', priority: 2 })
    monterTableau([wan1, sim5])
    expect(screen.queryByText(/secours/)).toBeNull()
    expect(screen.getByText('P2')).toBeInTheDocument()
  })

  it('met « secours » dans la table quand le lien prioritaire est connecté', () => {
    const wan1 = sample({ wan_id: '1', priority: 1 })
    const sim5 = sample({ wan_id: '5', priority: 2 })
    monterTableau([wan1, sim5])
    expect(screen.getByText('secours · P2')).toBeInTheDocument()
  })
})

/**
 * Un taux de perte sans son dénominateur ne se juge pas. Sur ce matériel, la
 * seule perte non nulle jamais enregistrée vaut 33,33 % — un paquet sur trois,
 * parce que le dénominateur était un keepalive. Le lien était sain.
 */
describe('perte dans le tunnel, avec son dénominateur', () => {
  const withTunnel = (over: Partial<TunnelLink>) =>
    render(<WanCard sample={sample({ tunnel: tunnel(over) })} />)

  it('publie le taux quand l’intervalle porte assez de paquets', () => {
    const { container } = withTunnel({ loss_percent_up: 1.25, packets_up: 900 })
    expect(detailValue(container, 'Perte montante')).toBe('1,25 % · 900 paquets')
  })

  it('refuse le taux sous le seuil, et dit sur quoi il aurait porté', () => {
    const { container } = withTunnel({ loss_percent_up: 33.33, packets_up: 3 })
    expect(detailValue(container, 'Perte montante')).toBe('— · 3 paquets')
    expect(screen.queryByText(/33\.33/)).not.toBeInTheDocument()
  })

  it('n’écrit jamais « 0,00 % » sur une poignée de paquets', () => {
    // C'est le cas symétrique et le plus trompeur des deux : un tunnel au repos
    // affichait une perte nulle « mesurée », indiscernable d'une vraie.
    const { container } = withTunnel({ loss_percent_up: 0, packets_up: 12 })
    expect(detailValue(container, 'Perte montante')).toBe('— · 12 paquets')
  })

  it('juge chaque sens sur son propre décompte', () => {
    // Les deux sens sont indépendants. L'un peut être mesurable
    // quand le descendant ne l'est pas : un seuil commun aurait fait taire
    // celui des deux qui avait de quoi conclure.
    const { container } = withTunnel({
      loss_percent_up: 2, packets_up: 940,
      loss_percent_down: 0, packets_down: 5,
    })
    expect(detailValue(container, 'Perte montante')).toBe('2,00 % · 940 paquets')
    expect(detailValue(container, 'Perte descendante')).toBe('— · 5 paquets')
  })

  it('se tait entièrement sans relevé de référence', () => {
    // Premier cycle du tunnel, compteurs reculés ou pair changé : on ne sait
    // même pas combien de paquets ont été comptés.
    const { container } = withTunnel({ loss_percent_up: null, packets_up: null })
    expect(detailValue(container, 'Perte montante')).toBe('—')
  })

  it('n’explique le seuil que lorsqu’il mord', () => {
    withTunnel({})
    expect(screen.queryByText(/Moins de 200 paquets/)).not.toBeInTheDocument()
  })

  it('explique le seuil quand un sens tombe en dessous', () => {
    withTunnel({ packets_down: 7 })
    expect(screen.getByText(/Moins de 200 paquets/)).toBeInTheDocument()
  })

  it('ne montre plus le désordre de paquets', () => {
    // 7 218 relevés de ce matériel, pas un seul au-dessus de zéro : la colonne
    // existait, la mesure non.
    withTunnel({})
    expect(screen.queryByText('Désordre de paquets')).not.toBeInTheDocument()
  })
})

describe('emplacements SIM', () => {
  it('ne déclare pas vide un emplacement dont le firmware ne dit rien', () => {
    // `bool(bloc.get("simCardDetected"))` côté serveur faisait d'une clé
    // absente un emplacement « vide », affirmé ici. `null` n'est ni vide ni
    // occupé : il ne compte pas, et le décompte le dit.
    const { container } = render(<WanCard sample={sample({ sim_slots: [
      { slot: '1', card_detected: true, apn: 'orange', active: true, allowance: null },
      { slot: '2', card_detected: null, apn: null, active: null, allowance: null },
    ] })} />)
    expect(container.textContent).toMatch(/1 carte sur 2 \(1 non renseigné\)/)
    expect(detailValue(container, 'Emplacement 2')).toMatch(/non renseigné/)
    expect(detailValue(container, 'Emplacement 2')).not.toMatch(/vide/)
    expect(detailValue(container, 'Emplacement 1 · en service')).toMatch(/APN orange/)
  })
})

describe('latence du lien (R-1.3)', () => {
  it('montre la dernière mesure avec son âge, pas un tiret entre deux lectures', () => {
    // Relevé à 14:00:00, latence lue à 13:59:20 : « il y a 40 s ».
    const { container } = render(<WanCard sample={sample({
      ...WIRED, path_latency_ms: null, path_latency_recent_ms: 4,
      path_latency_at: '2026-08-30T13:59:20Z',
    })} />)
    expect(detailValue(container, 'Latence du lien')).toBe('4 ms · il y a 40 s')
  })

  it('ne dit pas d’âge quand la mesure est de ce relevé', () => {
    const { container } = render(<WanCard sample={sample({
      ...WIRED, path_latency_ms: 4, path_latency_recent_ms: 4,
      path_latency_at: '2026-08-30T14:00:00Z',
    })} />)
    expect(detailValue(container, 'Latence du lien')).toBe('4 ms')
  })
})

describe('température du modem (R-1.11)', () => {
  it('montre la dernière lecture avec son âge, sur un lien cellulaire', () => {
    const { container } = render(<WanCard sample={sample({
      modem_temperature_c: 37, modem_temperature_at: '2026-08-30T13:59:40Z',
    })} />)
    expect(detailValue(container, 'Température du modem')).toBe('37 °C · il y a 20 s')
  })

  it('n’ouvre pas la ligne sans lecture — un modem qui ne la publie pas', () => {
    const { container } = render(<WanCard sample={sample({})} />)
    expect(detailValue(container, 'Température du modem')).toBeUndefined()
  })
})

describe('nom de l’opérateur (R-2.6)', () => {
  it('nomme le réseau par son code, et garde le libellé du modem en infobulle', () => {
    render(<WanCard sample={sample({ operator: 'Mobile S Mobile', operator_name: 'SFR', plmn: '208-10' })} />)
    const badge = screen.getByText('SFR')
    expect(badge.getAttribute('title')).toBe('PLMN 208-10 · libellé du modem : Mobile S Mobile')
    expect(screen.queryByText('Mobile S Mobile')).not.toBeInTheDocument()
  })

  it('ne répète pas le libellé quand il est déjà le nom', () => {
    render(<WanCard sample={sample({ operator: 'Orange', operator_name: 'Orange', plmn: '208-01' })} />)
    expect(screen.getByText('Orange').getAttribute('title')).toBe('PLMN 208-01')
  })
})

describe('un lien désactivé par l’exploitant (C-20)', () => {
  it('le dit, sans pastille rouge ni « Modem déconnecté »', () => {
    const { container } = render(<WanCard sample={sample({ connected: false, disabled: true })} />)
    expect(container.textContent).toContain('Lien désactivé par l’exploitant')
    expect(container.textContent).not.toContain('Modem déconnecté')
    const pastille = container.querySelector('.dot') as HTMLElement
    expect(pastille.style.background).toBe('var(--q-inconnu)')
  })
})

describe('la fenêtre des comptes, dite une fois (A-9)', () => {
  it('ne se répète pas dans chaque libellé', () => {
    // Vu le 17/10 : « Coupures · depuis 12:51 — sous tension depuis 09:52 »
    // et « Changements de cellule · … » serraient la grille au point de
    // chevaucher le libellé voisin.
    const fenetre = 'Depuis 12:51 — sous tension depuis 09:52'
    const { container } = render(
      <WanCard sample={sample(WIRED)} windowLabel={fenetre}
               churn={{ wan_id: '1', link_kind: 'ethernet', stations: 0, cellules: 0, avec_cellule: 0, coupures: 0 } as never} />,
    )
    const texte = container.textContent ?? ''
    expect(texte.split('depuis 12:51').length - 1).toBe(1)
    expect(screen.getByText('Coupures')).toBeInTheDocument()
  })
})
