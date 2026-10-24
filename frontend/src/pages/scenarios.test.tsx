import { act, render, screen, within, fireEvent } from '@testing-library/react'
import { Notes } from '../components/Note'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import type { RadioSample, Timeline } from '../types'
import { QUALITY_COLOR } from '../lib/quality'
import { PlanSeul, monterTableau, sampleRealiste, tunnelRealiste } from '../test/factories'
import { BondPlan } from '../components/BondPlan'
import { LinkTable } from '../components/LinkTable'
import { keyFigures } from '../lib/dashboard'


/**
 * Galerie de configurations : chacune est un matériel ou un état que l'outil
 * peut réellement rencontrer.
 *
 * Le projet supervise des équipements qui vont d'un modem unique à treize liens,
 * avec ou sans tunnel, avec ou sans débit par WAN, en cellulaire, en filaire ou
 * en Wi-Fi amont. Une interface taillée pour le MAX HD4 MBX qui a servi au
 * développement se serait effondrée sur le premier Dome Pro Duo — ou aurait
 * affiché cinq tirets là où le matériel n'a rien à dire.
 *
 * Ces tests ne vérifient pas une esthétique. Ils vérifient qu'aucune
 * configuration ne casse le rendu, et surtout que ce qui n'existe pas sur un
 * matériel n'y est pas ANNONCÉ vide : une ligne « — » se lit comme une panne.
 */

// Les fabriques vivent dans `src/test/factories.tsx`, partagées avec la
// confrontation. Ici les valeurs RÉALISTES du matériel de référence.
const sample = sampleRealiste
const tunnel = tunnelRealiste

/** Les configurations réellement rencontrables, nommées par leur matériel. */
const CONFIGURATIONS: Record<string, RadioSample[]> = {
  'modem unique, sans tunnel': [sample({ wan_id: '1' })],

  'fibre seule': [
    sample({ wan_id: '1', kind: 'ethernet', operator: null, operator_name: null, rat: null, band: null,
             rsrp: null, rsrq: null, sinr: null, cell_id: null, enb_id: null,
             ip: '192.168.1.2' }),
  ],

  'Dome Pro Duo — 2 SIM avec tunnel': [
    sample({ wan_id: '1', rat: '5G NSA', tunnel: tunnel() }),
    sample({ wan_id: '2', rat: '5G NSA', cell_id: '25735433', tunnel: tunnel({ rtt_ms: 41 }) }),
  ],

  'MAX HD4 MBX — 4 SIM et une fibre': [
    sample({ wan_id: '1', kind: 'ethernet', operator: null, operator_name: null, rat: null, band: null,
             rsrp: null, rsrq: null, sinr: null, cell_id: null, enb_id: null,
             ip: '10.0.0.2', tunnel: tunnel({ rtt_ms: 6 }) }),
    ...['4', '5', '6', '7'].map((id) =>
      sample({ wan_id: id, cell_id: String(25735436 + Number(id)), tunnel: tunnel() }),
    ),
  ],

  'huit liens mixtes': [
    ...['1', '2'].map((id) =>
      sample({ wan_id: id, kind: 'ethernet', operator: null, operator_name: null, rat: null, band: null,
               rsrp: null, rsrq: null, sinr: null, cell_id: null, enb_id: null }),
    ),
    ...['4', '5', '6', '7'].map((id) => sample({ wan_id: id })),
    ...['8', '9'].map((id) =>
      sample({ wan_id: id, kind: 'wifi', operator: null, operator_name: null, rat: null, band: null,
               rsrp: null, rsrq: null, sinr: null, cell_id: null, enb_id: null,
               rssi: -58, wifi: { ssid: 'Starlink', bssid: null, channel: '36', band: '5 GHz',
                        signal_dbm: -58, signal_level: null } }),
    ),
  ],

  'treize liens': Array.from({ length: 13 }, (_, i) => sample({ wan_id: String(i + 1) })),

  'tous les liens tombés': ['4', '5'].map((id) =>
    sample({ wan_id: id, connected: false, rsrp: null, rsrq: null, sinr: null,
             tx_kbps: null, rx_kbps: null, status_message: 'No SIM detected' }),
  ),

  'bond entièrement tombé': ['4', '5'].map((id) =>
    sample({ wan_id: id, tunnel: tunnel({ state: 'PEER_DOWN', in_bond: false, rtt_ms: null,
                                          tx_kbps: null, rx_kbps: null }) }),
  ),

  'un lien écarté du bond': [
    sample({ wan_id: '4', tunnel: tunnel() }),
    sample({ wan_id: '5', tunnel: tunnel({ state: 'WAN_DOWN', in_bond: false, rtt_ms: null }) }),
  ],

  // Le routeur a lui-même écarté ce lien : `beyond_cutoff` est un FAIT publié
  // par le firmware, pas une appréciation de l'interface.
  'un lien écarté par la latence': [
    sample({ wan_id: '4', tunnel: tunnel({ rtt_ms: 22 }) }),
    sample({ wan_id: '5', tunnel: tunnel({ rtt_ms: 620, cutoff_ms: 500, beyond_cutoff: true }) }),
    sample({ wan_id: '6', tunnel: tunnel({ rtt_ms: 28 }) }),
  ],

  // Le même écart, mais que le routeur N'A PAS retenu. C'est le cas qui
  // séparait les deux règles : l'approximation criait ici.
  'un lien lent que le routeur emploie quand même': [
    sample({ wan_id: '4', tunnel: tunnel({ rtt_ms: 19 }) }),
    sample({ wan_id: '5', tunnel: tunnel({ rtt_ms: 353, cutoff_ms: 500 }) }),
    sample({ wan_id: '6', tunnel: tunnel({ rtt_ms: 18 }) }),
  ],

  'état de tunnel inconnu': [
    sample({ wan_id: '4', tunnel: tunnel({ state: null, in_bond: false }) }),
  ],

  'modem muet — aucune mesure radio': [
    sample({ wan_id: '4', operator: null, operator_name: null, rat: null, band: null, rsrp: null, rsrq: null,
             sinr: null, cell_id: null, enb_id: null, tx_kbps: null, rx_kbps: null }),
  ],
}

/** Monte le tableau de bord tel que l'écran l'assemble : le plan du bond, puis
 *  la table des liens. Ce sont les deux objets qui portent toute la lecture. */
const monter = monterTableau

const lignes = (container: HTMLElement) => container.querySelectorAll('.ltable tbody tr')
/** Les rails des liens. Le tracé de sortie est peint par un dégradé
 *  (`stroke="url(#…)"`) : il n'est pas un lien et ne compte pas. */
/* Un rail est un GROUPE, pas un tracé : chacun porte désormais deux chemins —
   le trait visible, et une piste de survol large et transparente, parce que
   viser un trait de deux pixels à la souris est un exercice, pas une commande. */
const rails = (container: HTMLElement) => container.querySelectorAll('.plan-rail')
/* Le TRAIT VISIBLE de chaque rail. Compter les groupes ne prouve rien : en
   supprimant le tracé peint et en ne laissant que la piste de survol
   transparente, toute la galerie restait verte sur un dessin vide. */
const traces = (container: HTMLElement) =>
  container.querySelectorAll('.plan-rail path[stroke]:not([stroke="transparent"])')

describe('galerie de configurations', () => {
  for (const [nom, samples] of Object.entries(CONFIGURATIONS)) {
    it(`rend « ${nom} » sans rien casser`, () => {
      const { container, unmount } = monter(samples)
      // Chaque lien a sa ligne, aucune n'est perdue ni dupliquée.
      expect(lignes(container)).toHaveLength(samples.length)
      // Et chaque lien a son rail dans le plan : un lien qui existe est tracé,
      // même quand il ne porte rien.
      expect(rails(container)).toHaveLength(samples.length)
      // Et chaque rail est PEINT : un groupe sans tracé est un lien effacé.
      expect(traces(container)).toHaveLength(samples.length)
      unmount()
    })
  }

  it('tient la même mise en page d’un lien à treize', () => {
    // La présentation suit le matériel plutôt que l'inverse : une table ne
    // change pas de nature entre un Dome Pro Duo et un MAX HD4 MBX, là où la
    // version précédente basculait de tuiles en lignes à un seuil arbitraire.
    for (const n of [1, 2, 6, 9, 10, 13]) {
      const samples = Array.from({ length: n }, (_, i) => sample({ wan_id: String(i + 1) }))
      const { container, unmount } = monter(samples)
      expect(lignes(container)).toHaveLength(n)
      unmount()
    }
  })
})

describe('ce qui n’existe pas n’est pas annoncé vide', () => {
  it('n’ouvre aucune colonne de bond sur un équipement sans tunnel', () => {
    const { unmount } = monter(CONFIGURATIONS['modem unique, sans tunnel'])
    expect(screen.queryByText('Bond ms')).not.toBeInTheDocument()
    expect(screen.queryByText('Perte')).not.toBeInTheDocument()
    expect(screen.queryByText(/dans le bond/)).not.toBeInTheDocument()
    unmount()

    // Contre-épreuve : ces libellés existent bel et bien quand un tunnel est là.
    // Sans elle, les assertions ci-dessus passeraient même si le composant ne
    // les rendait JAMAIS.
    monter(CONFIGURATIONS['Dome Pro Duo — 2 SIM avec tunnel'])
    expect(screen.getByText('Bond ms')).toBeInTheDocument()
    expect(screen.getByText('Perte')).toBeInTheDocument()
    expect(screen.getByText(/dans le bond/)).toBeInTheDocument()
  })

  it('nomme l’opérateur par son code réseau, dans la table et le plan (R-2.6)', () => {
    const { container } = monter([
      sample({ wan_id: '3', operator: 'Mobile S Mobile', operator_name: 'SFR', plmn: '208-10' }),
    ])
    expect(container.querySelector('.ltable tbody tr')?.textContent).toContain('SFR')
    expect(container.textContent).not.toContain('Mobile S Mobile')
    // La fiche du plan, ouverte au survol d'un rail, dit le même nom.
    const plan = render(<MemoryRouter><PlanSeul samples={[
      sample({ wan_id: '3', operator: 'Mobile S Mobile', operator_name: 'SFR', plmn: '208-10' }),
    ]} /></MemoryRouter>)
    fireEvent.mouseOver(plan.container.querySelector('.plan-rail')!)
    expect(plan.container.querySelector('.plan-lecture-sous')?.textContent).toMatch(/^SFR/)
  })

  it('ouvre la colonne « Lien ms » dès qu’un lien a une latence propre (R-1.3)', () => {
    // Sans tunnel, la seule latence mesurée était invisible sur le tableau de
    // bord : colonne et médiane ne lisaient que le tunnel.
    const { unmount } = monter(CONFIGURATIONS['modem unique, sans tunnel'])
    expect(screen.queryByText('Lien ms')).not.toBeInTheDocument()
    unmount()

    const { container } = monter([
      sample({ wan_id: '3', tunnel: null, path_latency_recent_ms: 31 }),
      sample({ wan_id: '5', tunnel: null, path_latency_recent_ms: 35 }),
    ])
    expect(screen.getByText('Lien ms')).toBeInTheDocument()
    expect(screen.queryByText('Bond ms')).not.toBeInTheDocument()
    const cellules = [...container.querySelectorAll('.ltable tbody tr')]
      .map((tr) => tr.textContent ?? '')
    expect(cellules[0]).toContain('31')
    expect(cellules[1]).toContain('35')
    // La médiane dit laquelle elle résume.
    expect(screen.getByText('latence médiane des liens, ms')).toBeInTheDocument()
  })

  it('n’ouvre pas de colonne SINR quand aucun lien n’est cellulaire', () => {
    // Ces grandeurs ne sont pas absentes sur une fibre : elles n'ont pas de
    // sens. Une colonne entière de tirets se lit comme une mesure perdue.
    const { unmount } = monter(CONFIGURATIONS['fibre seule'])
    expect(screen.queryByText('SINR dB')).not.toBeInTheDocument()
    expect(screen.queryByText('RSRP dBm')).not.toBeInTheDocument()
    unmount()

    monter([sample({ wan_id: '4' })])
    expect(screen.getByText('SINR dB')).toBeInTheDocument()
    expect(screen.getByText('RSRP dBm')).toBeInTheDocument()
  })

  it('ouvre une colonne RSSI pour un Wi-Fi amont, et pas un RSRP', () => {
    const wifi = CONFIGURATIONS['huit liens mixtes'].filter((s) => s.wan.kind === 'wifi')
    const { container } = monter(wifi)
    expect(screen.getByText('RSSI dBm')).toBeInTheDocument()
    expect(screen.queryByText('RSRP dBm')).not.toBeInTheDocument()
    expect(within(container).getAllByText('Starlink')).toHaveLength(2)
  })

  it('laisse la fibre sans mesure radio plutôt que de lui en inventer', () => {
    // La colonne existe — d'autres liens sont cellulaires — mais la cellule de
    // la fibre porte un cadratin, jamais un zéro.
    const { container } = monter(CONFIGURATIONS['MAX HD4 MBX — 4 SIM et une fibre'])
    const fibre = lignes(container)[0]
    expect(fibre.textContent).toContain('WAN 1')
    expect(within(fibre as HTMLElement).getAllByText('—').length).toBeGreaterThanOrEqual(3)
  })

  it('masque le débit quand l’équipement n’en publie pas', () => {
    const { unmount } = monter(CONFIGURATIONS['Dome Pro Duo — 2 SIM avec tunnel'], false)
    expect(screen.queryByText('↑ Mbit/s')).not.toBeInTheDocument()
    unmount()

    monter(CONFIGURATIONS['Dome Pro Duo — 2 SIM avec tunnel'], true)
    expect(screen.getByText('↑ Mbit/s')).toBeInTheDocument()
  })

  it('ne déclare pas « hors du bond » un état de tunnel inconnu', () => {
    // Un `state` nul veut dire INCONNU. L'accuser d'être écarté du bond serait
    // exactement le mensonge inverse de celui qu'on a corrigé.
    const { unmount } = monter(CONFIGURATIONS['état de tunnel inconnu'])
    expect(screen.queryByText('hors du bond')).not.toBeInTheDocument()
    unmount()

    monter(CONFIGURATIONS['un lien écarté du bond'])
    expect(screen.getByText('hors du bond')).toBeInTheDocument()
  })

  it('la latence ne se juge qu’à la règle du routeur, partout à la fois', () => {
    // Trois règles ont coexisté sur cet écran : un seuil absolu pour la couleur
    // du chiffre, l'écart à la médiane des autres pour le bandeau, et l'écart au
    // plus rapide — celle du firmware — pour le badge et le journal. Mesuré sur
    // 33 679 relevés, les deux dernières divergeaient sur 7,7 %, toujours dans
    // le même sens. Constaté à l'écran : la note du bond annonçait « 2 en
    // latence excessive » pendant que les cinq liens portaient
    // `beyond_cutoff: false` et que la table n'affichait aucun badge.
    const { container } = monter(CONFIGURATIONS['un lien écarté par la latence'])
    // Le badge le dit…
    expect(screen.getByText('au-delà du seuil')).toBeInTheDocument()
    // …et le chiffre le dit aussi, de la même source.
    const rouges = [...container.querySelectorAll('tbody td')].filter((td) =>
      td.querySelector(`[style*="${QUALITY_COLOR.faible}"]`),
    )
    expect(rouges.length).toBeGreaterThan(0)
  })

  it('se tait sur un lien lent que le routeur emploie quand même', () => {
    // Contre-épreuve, et c'est elle qui aurait échoué avant : 353 ms contre
    // 18 ms, l'approximation dénonçait. Le firmware, lui, s'en accommode — et
    // c'est lui qui décide de ce qui transite.
    const { container } = monter(CONFIGURATIONS['un lien lent que le routeur emploie quand même'])
    expect(screen.queryByText('au-delà du seuil')).not.toBeInTheDocument()
    const rouges = [...container.querySelectorAll('tbody td')].filter((td) =>
      td.querySelector(`[style*="${QUALITY_COLOR.faible}"]`),
    )
    expect(rouges).toHaveLength(0)
  })

  it('trace en pointillé le lien qui ne rejoint pas le bond', () => {
    // Le plan le DESSINE au lieu de le dire : c'est la distinction que le modèle
    // porte depuis le début et que l'écran réduisait à un marqueur.
    const { container } = monter(CONFIGURATIONS['un lien écarté du bond'])
    // Le pointillé est sur le trait VISIBLE du rail, pas sur sa piste de survol.
    const pointilles = [...rails(container)].filter(
      (g) => g.querySelector('path[stroke-dasharray]') !== null,
    )
    expect(pointilles).toHaveLength(1)
  })

  it('dit pourquoi un lien est tombé quand le routeur le dit', () => {
    monter(CONFIGURATIONS['tous les liens tombés'])
    expect(screen.getAllByText('No SIM detected')).toHaveLength(2)
  })

  it('n’annonce aucun partage de cellule quand la cellule est inconnue', () => {
    // « 2 liens cellulaires · 0 cellules » assorti de « des modems partagent une
    // cellule » est une affirmation fausse : la cellule est simplement absente.
    monter(CONFIGURATIONS['modem muet — aucune mesure radio'])
    expect(screen.queryByText('cellule partagée')).not.toBeInTheDocument()
    expect(screen.queryByText(/trajets radio distincts/)).not.toBeInTheDocument()
  })
})

describe('la bande a rejoint la table', () => {
  it('donne à chaque lien sa colonne de temps', () => {
    // La bande vivait dans un panneau séparé qui répétait exactement ces lignes
    // — une par lien, dans le même ordre, avec la même identité à gauche. Deux
    // panneaux pour un seul objet, et la hauteur de l'un perdue à scroller.
    // Deux liens DU BOND : la phrase sur la redondance ne concerne qu'eux.
    const samples = [sample({ wan_id: '4', tunnel: tunnel() }), sample({ wan_id: '5', tunnel: tunnel() })]
    const timeline: Timeline = {
      router_id: 'r', started_at: '2026-09-11T11:00:00Z', ended_at: '2026-09-11T19:00:00Z', capped: false,
      slice_count: 200, slice_seconds: 144,
      lanes: samples.map((s) => ({
        wan_id: s.wan.wan_id,
        slices: [{
          t: 0, samples: 10, up: 10, carrying: 10, tunnel_known: 10, sinr_worst: 12, rsrp_worst: -88, repair: 0, packets: 40, disabled: 0,
        }],
      })),
    }
    const { container } = render(
      <MemoryRouter>
        <LinkTable
          routerId="r" samples={samples} showThroughput hasTunnel
          sameCell={new Map()} timeline={timeline} windowLabel="Depuis la mise sous tension"
          churn={[]}
          latencyThresholdMs={1000} radioGrievances={[]}
        />
      </MemoryRouter>,
    )
    expect(container.querySelectorAll('tbody td.is-band')).toHaveLength(2)
    // L'axe du temps vit sous la seule colonne qu'il concerne.
    expect(container.querySelector('tfoot .ticks')).not.toBeNull()
    // Ce que les liens ont fait ENSEMBLE se dit en toutes lettres : trois
    // encodages visuels y ont échoué, et l'information est assez rare pour
    // qu'une phrase la porte mieux qu'un dessin.
    expect(container.querySelector('tfoot tr.is-ensemble')).toBeNull()
    expect(container.textContent).toContain('Redondance intacte')
    // La phrase est passée en INFOBULLE : l'écran dit le fait, le survol dit
    // sur quoi il porte. C'est cette part observée qui manquait — sur 0,8 % de
    // fenêtre, l'écran écrivait « Redondance intacte sur toute la fenêtre
    // relue », la phrase la plus rassurante sur la mesure la plus mince.
    // Et cette justification est ATTEIGNABLE — au doigt comme au clavier : un
    // bouton, dont le texte se déplie et se lit sans survol.
    const pourquoi = [...container.querySelectorAll('button.pourquoi')]
      .find((b) => /Jugé sur/.test(b.getAttribute('title') ?? ''))
    expect(pourquoi).toBeDefined()
    expect(pourquoi!.getAttribute('aria-expanded')).toBe('false')
    expect(container.textContent).not.toMatch(/Jugé sur/)
    act(() => { (pourquoi as HTMLButtonElement).click() })
    expect(pourquoi!.getAttribute('aria-expanded')).toBe('true')
    expect(container.textContent).toMatch(/Jugé sur/)
  })

  it('ne compte pas un filaire hors du profil comme un fléchissement du bond', () => {
    // `bondTogether` comptait TOUS les liens : un WAN filaire sans objet tunnel
    // ne portait jamais, et la table disait « Redondance réduite — plus aucun
    // lien dans le bond » sur toute la session, deux SIM bien dans le bond.
    const samples = [
      sample({ wan_id: '1', kind: 'ethernet', operator: null, operator_name: null, rat: null, band: null,
               rsrp: null, rsrq: null, sinr: null, cell_id: null, enb_id: null, tunnel: null }),
      sample({ wan_id: '4', tunnel: tunnel() }),
      sample({ wan_id: '5', tunnel: tunnel() }),
    ]
    const tr = (t: number, carrying: number, tunnel_known: number) => ({
      t, samples: 10, up: 10, carrying, tunnel_known, sinr_worst: 12, rsrp_worst: -88, repair: 0, packets: 40, disabled: 0,
    })
    const timeline: Timeline = {
      router_id: 'r', started_at: '2026-09-11T11:00:00Z', ended_at: '2026-09-11T19:00:00Z', capped: false,
      slice_count: 200, slice_seconds: 144,
      lanes: [
        { wan_id: '1', slices: [tr(0, 0, 0), tr(1, 0, 0)] },
        { wan_id: '4', slices: [tr(0, 10, 10), tr(1, 10, 10)] },
        { wan_id: '5', slices: [tr(0, 10, 10), tr(1, 10, 10)] },
      ],
    }
    const { container } = render(
      <MemoryRouter>
        <LinkTable
          routerId="r" samples={samples} showThroughput hasTunnel
          sameCell={new Map()} timeline={timeline} windowLabel="Depuis la mise sous tension"
          churn={[]}
          latencyThresholdMs={1000} radioGrievances={[]}
        />
      </MemoryRouter>,
    )
    expect(container.textContent).toContain('Redondance intacte')
    expect(container.textContent).not.toContain('Redondance réduite')
  })

  it('ne dit pas « redondance réduite » d’un bond jamais établi', () => {
    // Dome Pro Duo, pair injoignable depuis l'allumage : « Redondance réduite
    // 3 fois. Au plus bas : 0 sur 3 » sous un bandeau qui disait, à juste
    // titre, « jamais établi ». Trois épisodes découpés dans un même état.
    const samples = [sample({ wan_id: '2', tunnel: tunnel({ state: 'PEER_DOWN', in_bond: false }) }),
                     sample({ wan_id: '3', tunnel: tunnel({ state: 'PEER_DOWN', in_bond: false }) })]
    const tr = (t: number) => ({
      t, samples: 10, up: 10, carrying: 0, tunnel_known: 10, sinr_worst: 12, rsrp_worst: -88, repair: null, packets: null, disabled: 0,
    })
    const timeline: Timeline = {
      router_id: 'r', started_at: '2026-09-24T14:30:00Z', ended_at: '2026-09-24T14:40:00Z', capped: false,
      slice_count: 20, slice_seconds: 30,
      lanes: samples.map((s) => ({ wan_id: s.wan.wan_id, slices: [tr(0), tr(1), tr(2)] })),
    }
    const table = (tunnelAttendu: boolean | null) => render(
      <MemoryRouter>
        <LinkTable
          routerId="r" samples={samples} showThroughput hasTunnel
          sameCell={new Map()} timeline={timeline} windowLabel="Depuis 16:30"
          churn={[]} latencyThresholdMs={1000} radioGrievances={[]} tunnelAttendu={tunnelAttendu}
        />
      </MemoryRouter>,
    )
    const jamais = table(false)
    expect(jamais.container.textContent).not.toMatch(/Redondance réduite/)
    expect(jamais.container.textContent).toMatch(/Bond jamais établi/)
    jamais.unmount()
    // Un bond qui a porté, lui, peut se réduire — et le dit.
    const tombe = table(true)
    expect(tombe.container.textContent).toMatch(/Redondance réduite/)
    expect(tombe.container.textContent).toMatch(/sur 2/)
    tombe.unmount()
  })

  it('n’ouvre pas de colonne de temps sans fenêtre relue', () => {
    // Contre-épreuve : une colonne vide se lirait comme une mesure perdue.
    const { container } = monter([sample({ wan_id: '4' })])
    expect(container.querySelectorAll('tbody td.is-band')).toHaveLength(0)
    expect(container.querySelector('tfoot')).toBeNull()
  })
})

describe('le seuil de latence du bond', () => {
  it('nomme le lien que le routeur a cessé d’employer', () => {
    // `state: ACTIVE` ne veut pas dire « ce lien porte le flux » : le firmware
    // arrête le trafic au-delà du seuil sans changer l'état, et l'écran
    // l'affirmait quand même.
    monter([
      sample({ wan_id: '4', tunnel: tunnel({ rtt_ms: 30 }) }),
      sample({ wan_id: '6', tunnel: tunnel({ rtt_ms: 2249, cutoff_ms: 500, beyond_cutoff: true }) }),
    ])
    expect(screen.getByText('au-delà du seuil')).toBeInTheDocument()
  })

  it('ne le dit pas quand le lien reste sous le seuil', () => {
    // Contre-épreuve : sans elle, l'assertion ci-dessus passerait même si le
    // marqueur était rendu sur tous les liens.
    monter([
      sample({ wan_id: '4', tunnel: tunnel({ rtt_ms: 30, cutoff_ms: 500 }) }),
      sample({ wan_id: '6', tunnel: tunnel({ rtt_ms: 200, cutoff_ms: 500 }) }),
    ])
    expect(screen.queryByText('au-delà du seuil')).not.toBeInTheDocument()
  })

  it('n’affirme rien sans seuil publié', () => {
    // Tous les profils n'en configurent pas : en supposer un ferait accuser des
    // liens sur une règle que le routeur n'applique pas.
    monter([sample({ wan_id: '4', tunnel: tunnel({ rtt_ms: 5000 }) })])
    expect(screen.queryByText('au-delà du seuil')).not.toBeInTheDocument()
  })
})

describe('à quoi sert le bond', () => {
  it('ne prétend pas que les liens portent quand le tunnel n’est pas le chemin', () => {
    // Mesuré sur 35 751 relevés : le tunnel n'a jamais transporté plus de
    // 2,6 kbit/s — un keepalive — quand les interfaces montaient à 262 Mbit/s.
    // Le pair le dit lui-même : il n'achemine que deux sous-réseaux, et la route
    // par défaut n'y figure pas. « Liens qui portent » était faux.
    const samples = [sample({ wan_id: '4', tunnel: tunnel() })]
    render(
      <MemoryRouter>
        <BondPlan
          samples={samples} figures={keyFigures(samples)}
          redundancy={{ state: 'unknown', packets: 0 }}
          cells={1} sites={1} located={1} attached={1}
          role={{ kind: 'routes', routes: ['169.254.131.0/24', '203.0.113.61/32'] }}
          grievances={[]} tunnelUptimeS={null} throughputLimitMbps={null} routerId="mbx" cellGroups={new Map()}
        />
      </MemoryRouter>,
    )
    expect(screen.getByText('liens dans le bond')).toBeInTheDocument()
    expect(screen.queryByText('liens qui portent')).not.toBeInTheDocument()
    // Ce qui est AFFIRMÉ a changé : plus « aucun profil n'est sélectionné dans
    // Send All Traffic To » — une configuration déduite d'une table de routage,
    // que le matériel a démentie — mais le fait mesuré, « rien ne transite ».
    expect(screen.getByText(/Rien ne transite par le bond/)).toBeInTheDocument()
    // Les sous-réseaux annoncés sont passés en INFOBULLE : leur liste n'apprend
    // rien à qui balaie l'écran, et tout à qui doute du verdict. La justification
    // reste donc vérifiable, à un survol.
    expect(screen.getByTitle(/169\.254\.131\.0\/24/)).toBeInTheDocument()
  })

  it('dit l’état du tunnel auprès du bond, et non dans le verdict', () => {
    // Le grief existe toujours — une latence qui s'envole vers le pair reste le
    // symptôme d'un lien en peine. Il a changé de SUJET : ce n'est plus un
    // verdict sur la liaison, c'est un état du tunnel, et il se lit à côté des
    // routes qu'il dessert.
    const samples = [sample({ wan_id: '4', tunnel: tunnel() })]
    render(
      <MemoryRouter>
        <BondPlan
          samples={samples} figures={keyFigures(samples)}
          redundancy={{ state: 'unknown', packets: 0 }}
          cells={1} sites={1} located={1} attached={1}
          role={{ kind: 'routes', routes: ['203.0.113.61/32'] }}
          grievances={['1 en latence excessive (353 ms contre 19 ms)']} tunnelUptimeS={null} throughputLimitMbps={null} routerId="mbx" cellGroups={new Map()}
        />
      </MemoryRouter>,
    )
    expect(screen.getByText(/Dans le tunnel/)).toBeInTheDocument()
    expect(screen.getByText(/353 ms contre 19 ms/)).toBeInTheDocument()
    // Et ce sur quoi porte la mesure reste disponible au survol.
    expect(screen.getByTitle(/trajet vers le pair/)).toBeInTheDocument()
  })

  it('cesse de nier le trafic dès que des paquets le démentent', () => {
    // Le 13 septembre, l'activation du trafic SpeedFusion a fait passer
    // vingt-six mille paquets dans le tunnel SANS changer d'un mot les routes
    // annoncées par le pair. L'écran a continué d'affirmer que le bond n'était
    // pas le chemin du trafic — et d'en tirer que le verdict n'avait pas à
    // juger sur les grandeurs du tunnel.
    const samples = [sample({ wan_id: '4', tunnel: tunnel() })]
    render(
      <MemoryRouter>
        <BondPlan
          samples={samples} figures={keyFigures(samples)}
          redundancy={{ state: 'unknown', packets: 0 }}
          cells={1} sites={1} located={1} attached={1}
          role={{ kind: 'porte', routes: ['169.254.131.0/24'] }}
          grievances={[]} tunnelUptimeS={null} throughputLimitMbps={null} routerId="mbx" cellGroups={new Map()}
        />
      </MemoryRouter>,
    )
    expect(screen.getByText(/Le bond porte le trafic/)).toBeInTheDocument()
    expect(screen.queryByText(/Rien ne transite/)).not.toBeInTheDocument()
    // Ce qui le fonde — les routes annoncées et les paquets mesurés — survit
    // au survol.
    expect(screen.getByTitle(/Paquets mesurés dans le tunnel/)).toBeInTheDocument()
    // Et le décompte le suit : ces liens PORTENT.
    expect(screen.getByText('liens qui portent')).toBeInTheDocument()
  })

  it('le dit bien quand la route par défaut passe par le tunnel', () => {
    // Contre-épreuve : avec « Send All Traffic To » renseigné, le pair achemine
    // 0.0.0.0/0 et le tunnel EST le chemin du trafic.
    const samples = [sample({ wan_id: '4', tunnel: tunnel() })]
    render(
      <MemoryRouter>
        <BondPlan
          samples={samples} figures={keyFigures(samples)}
          redundancy={{ state: 'unknown', packets: 0 }}
          cells={1} sites={1} located={1} attached={1}
          role={{ kind: 'tout' }}
          grievances={[]} tunnelUptimeS={null} throughputLimitMbps={null} routerId="mbx" cellGroups={new Map()}
        />
      </MemoryRouter>,
    )
    expect(screen.getByText('liens qui portent')).toBeInTheDocument()
    expect(screen.queryByText(/n’est pas le chemin du trafic/)).not.toBeInTheDocument()
  })

  it('n’affirme rien quand le pair ne déclare aucune route', () => {
    // Un débit nul ne prouve rien à lui seul : c'est l'ABSENCE de la route par
    // défaut dans ce que le pair déclare qui prouve que le trafic passe à côté.
    const samples = [sample({ wan_id: '4', tunnel: tunnel() })]
    render(
      <MemoryRouter>
        <BondPlan
          samples={samples} figures={keyFigures(samples)}
          redundancy={{ state: 'unknown', packets: 0 }}
          cells={1} sites={1} located={1} attached={1}
          role={{ kind: 'inconnu' }}
          grievances={[]} tunnelUptimeS={null} throughputLimitMbps={null} routerId="mbx" cellGroups={new Map()}
        />
      </MemoryRouter>,
    )
    expect(screen.queryByText(/n’est pas le chemin du trafic/)).not.toBeInTheDocument()
  })
})

describe('cohérence de l’écran avec lui-même', () => {
  it('retire du décompte le lien que le routeur a cessé d’employer', () => {
    // L'écran annonçait « 5 liens portent » pendant que la table marquait l'un
    // d'eux « au-delà du seuil », c'est-à-dire exactement l'inverse. Deux
    // affirmations incompatibles côte à côte.
    const f = keyFigures([
      sample({ wan_id: '4', tunnel: tunnel({ rtt_ms: 30 }) }),
      sample({ wan_id: '5', tunnel: tunnel({ rtt_ms: 30 }) }),
      sample({ wan_id: '6', tunnel: tunnel({ rtt_ms: 2249, cutoff_ms: 500, beyond_cutoff: true }) }),
    ])
    expect(f.carrying).toBe(2)
    expect(f.excludedByLatency).toBe(1)
    expect(f.total).toBe(3)
  })

  it('ne retire rien quand aucun lien ne dépasse', () => {
    const f = keyFigures([
      sample({ wan_id: '4', tunnel: tunnel({ rtt_ms: 30, cutoff_ms: 500 }) }),
      sample({ wan_id: '5', tunnel: tunnel({ rtt_ms: 200, cutoff_ms: 500 }) }),
    ])
    expect(f.carrying).toBe(2)
    expect(f.excludedByLatency).toBe(0)
  })

  it('juge la latence en ÉCART, comme le bandeau, et non sur un seuil absolu', () => {
    // Mesuré sur 5 679 cycles : 2 513 relevés allumaient la jauge en rouge et
    // 859 — 34 % — n'étaient repris par aucun verdict. La table montrait du
    // rouge pendant que le bandeau annonçait « Liaison OK ». Un lien à 130 ms
    // au milieu de voisins à 25 est le cas type : rouge autrefois, silencieux
    // maintenant, et le bandeau se tait aussi.
    const rouge = (c: HTMLElement) =>
      [...c.querySelectorAll('tbody .meas')].filter(
        (m) => (m as HTMLElement).style.getPropertyValue('--q') === QUALITY_COLOR.faible,
      ).length

    const { container, unmount } = monter([
      sample({ wan_id: '4', tunnel: tunnel({ rtt_ms: 25 }) }),
      sample({ wan_id: '5', tunnel: tunnel({ rtt_ms: 25 }) }),
      sample({ wan_id: '6', tunnel: tunnel({ rtt_ms: 130 }) }),
    ])
    expect(rouge(container)).toBe(0)
    unmount()

    // Contre-épreuve : un lien VRAIMENT détaché du peloton reste dénoncé.
    const decroche = monter([
      sample({ wan_id: '4', tunnel: tunnel({ rtt_ms: 25 }) }),
      sample({ wan_id: '5', tunnel: tunnel({ rtt_ms: 25 }) }),
      sample({ wan_id: '6', tunnel: tunnel({ rtt_ms: 1929 }) }),
    ])
    expect(rouge(decroche.container)).toBe(1)
  })

  it('compte les coupures sur la FENÊTRE, pas depuis le démarrage du serveur', () => {
    // Le compteur cumulait depuis le démarrage du processus superviseur — une
    // base de temps qui n'apparaissait nulle part et qui se remettait à zéro à
    // chaque relance —, dans un panneau intitulé « depuis la mise sous tension ».
    const s = sample({ wan_id: '4' })
    // `disconnects` du relevé vaut zéro ; la fenêtre, elle, en a compté trois.
    const { container } = render(
      <MemoryRouter>
        <LinkTable
          routerId="r" samples={[s]} showThroughput hasTunnel={false}
          sameCell={new Map()} timeline={null} windowLabel="Depuis la mise sous tension"
          churn={[{ wan_id: '4', link_kind: 'cellular', has_cells: true, cell_changes: 7, station_changes: 5, outages: 3 }]}
          latencyThresholdMs={1000} radioGrievances={[]}
        />
      </MemoryRouter>,
    )
    const cellules = [...container.querySelectorAll('tbody td')].map((t) => t.textContent)
    expect(cellules).toContain('3')
    expect(cellules).toContain('7')
  })

  it('rend un cadratin, jamais un zéro, quand le brassage d’un lien n’est pas connu', () => {
    // `churn === null ? <Absent/> : …` mis à `0` laissait 395 tests verts :
    // le seul test qui montait la table passait `churn={[]}` sans rien
    // affirmer. Un lien sans compte n'est pas un lien stable.
    const sim = sample({ wan_id: '4' })
    const { container } = render(
      <MemoryRouter>
        <LinkTable
          routerId="r" samples={[sim]} showThroughput hasTunnel={false}
          sameCell={new Map()} timeline={null} windowLabel="Depuis la mise sous tension"
          churn={[]}
          latencyThresholdMs={1000} radioGrievances={[]}
        />
      </MemoryRouter>,
    )
    const entetes = [...container.querySelectorAll('thead th')].map((t) => t.textContent)
    const ligne = container.querySelector('tbody tr')!
    for (const colonne of ['Sauts de cellule', 'Coupures']) {
      const i = entetes.indexOf(colonne)
      expect(i, colonne).toBeGreaterThan(0)
      const cellule = ligne.querySelectorAll('td')[i - 1]
      expect(cellule.textContent, colonne).toBe('—')
    }
  })

  it('marque un lien désactivé sans l’afficher comme une panne', () => {
    const off = sample({ wan_id: '3', connected: false, disabled: true, status_message: 'désactivé',
                         rsrp: null, rsrq: null, sinr: null })
    const { container } = render(
      <MemoryRouter>
        <LinkTable
          routerId="r" samples={[sample({ wan_id: '2' }), off]} showThroughput hasTunnel={false}
          sameCell={new Map()} timeline={null} windowLabel="Depuis la mise sous tension"
          churn={[]} latencyThresholdMs={1000} radioGrievances={[]}
        />
      </MemoryRouter>,
    )
    const lignes = [...container.querySelectorAll('tbody tr')]
    expect(lignes[1].className).not.toMatch(/is-down/)
    expect(lignes[1].textContent).toMatch(/désactivé/)
    expect(lignes[1].querySelector('.tag.is-off')).toBeNull()
  })

  it('ne compte pas de sauts de cellule à un lien qui n’en a pas', () => {
    // WAN 1 (Ethernet) montrait un zéro gris sous « Sauts de cellule », lu
    // comme une stabilité — la règle que la fiche de session applique dans
    // l'autre sens (`has_cells`).
    const filaire = sample({ wan_id: '1', kind: 'ethernet', operator: null, operator_name: null, rat: null, band: null,
                             rsrp: null, rsrq: null, sinr: null, cell_id: null, enb_id: null })
    const sim = sample({ wan_id: '4' })
    const { container } = render(
      <MemoryRouter>
        <LinkTable
          routerId="r" samples={[filaire, sim]} showThroughput hasTunnel={false}
          sameCell={new Map()} timeline={null} windowLabel="Depuis la mise sous tension"
          churn={[
            { wan_id: '1', link_kind: 'ethernet', has_cells: false, cell_changes: 0, station_changes: 0, outages: 0 },
            { wan_id: '4', link_kind: 'cellular', has_cells: true, cell_changes: 0, station_changes: 0, outages: 0 },
          ]}
          latencyThresholdMs={1000} radioGrievances={[]}
        />
      </MemoryRouter>,
    )
    const entetes = [...container.querySelectorAll('thead th')].map((t) => t.textContent)
    const colonne = entetes.indexOf('Sauts de cellule')
    expect(colonne).toBeGreaterThan(0)
    const lignes = [...container.querySelectorAll('tbody tr')]
    const cellule = (ligne: Element) => ligne.querySelectorAll('td')[colonne - 1]?.textContent
    expect(cellule(lignes[0])).not.toBe('0')     // le filaire : un cadratin
    expect(cellule(lignes[1])).toBe('0')         // la SIM : une vraie stabilité
  })
})

describe('chiffres du tableau de bord', () => {
  it('somme les débits des liens connectés, et ignore les autres', () => {
    const f = keyFigures([
      sample({ wan_id: '4', tx_kbps: 1000, rx_kbps: 2000 }),
      sample({ wan_id: '5', tx_kbps: 500, rx_kbps: 1000 }),
      sample({ wan_id: '6', connected: false, tx_kbps: 9999, rx_kbps: 9999 }),
    ])
    expect(f.txKbps).toBe(1500)
    expect(f.rxKbps).toBe(2000 + 1000)
    expect(f.carrying).toBe(2)
    expect(f.total).toBe(3)
  })

  it('rend null — et jamais zéro — quand rien n’est mesuré', () => {
    // C'est la règle que ce projet a violée cinq fois : un zéro à la place d'une
    // absence de mesure rassure exactement quand il ne faut pas.
    const f = keyFigures([sample({ tx_kbps: null, rx_kbps: null })])
    expect(f.txKbps).toBeNull()
    expect(f.rxKbps).toBeNull()
    expect(f.rttMedianMs).toBeNull()
  })

  it('compte les liens DU BOND quand il y en a un', () => {
    const f = keyFigures(CONFIGURATIONS['un lien écarté du bond'])
    expect(f.hasTunnel).toBe(true)
    expect(f.carrying).toBe(1)
    expect(f.total).toBe(2)
  })

  it('prend la médiane des latences, pas la moyenne', () => {
    // Un lien à 1 929 ms au milieu de trois à 30 déplacerait la moyenne de 470 ms
    // et déclarerait saine une liaison qui ne l'est pas.
    const f = keyFigures([
      sample({ wan_id: '4', tunnel: tunnel({ rtt_ms: 30 }) }),
      sample({ wan_id: '5', tunnel: tunnel({ rtt_ms: 30 }) }),
      sample({ wan_id: '6', tunnel: tunnel({ rtt_ms: 32 }) }),
      sample({ wan_id: '7', tunnel: tunnel({ rtt_ms: 1929 }) }),
    ])
    expect(f.rttMedianMs).toBe(31)
    expect(f.rttWorstMs).toBe(1929)
  })

  it('ne réordonne PAS les liens quand l’un d’eux décroche', () => {
    // Un classement par état se réordonne tout seul : le lien qui décroche
    // change de rang et toute la colonne se décale sous l'œil. La certitude
    // spatiale est ce qui permet le coup d'œil — une panne se signale sur place.
    const avant = monter([
      sample({ wan_id: '4' }),
      sample({ wan_id: '5' }),
      sample({ wan_id: '7' }),
    ])
    const noms = (c: HTMLElement) =>
      [...lignes(c)].map((tr) => tr.querySelector('b')?.textContent)
    expect(noms(avant.container)).toEqual(['SIM 4', 'SIM 5', 'SIM 7'])
    avant.unmount()

    const apres = monter([
      sample({ wan_id: '4', connected: false }),
      sample({ wan_id: '5' }),
      sample({ wan_id: '7' }),
    ])
    expect(noms(apres.container)).toEqual(['SIM 4', 'SIM 5', 'SIM 7'])
  })

  it('ordonne les identifiants comme un humain les lit', () => {
    // « SIM 10 » vient après « SIM 9 », ce qu'un tri de chaînes ne fait pas.
    const { container } = monter(
      ['10', '2', '9', '1'].map((id) => sample({ wan_id: id })),
    )
    const noms = [...lignes(container)].map((tr) => tr.querySelector('b')?.textContent)
    expect(noms).toEqual(['SIM 1', 'SIM 2', 'SIM 9', 'SIM 10'])
  })
})

describe('la ligne mène au second niveau', () => {
  it('ouvre le détail du bon lien', () => {
    const { container } = monter([sample({ wan_id: '7' })])
    const lien = container.querySelector('.ltable a')
    // `MemoryRouter` ne préfixe pas de fragment ; `HashRouter` le fait en vrai.
    expect(lien?.getAttribute('href')).toBe('/e/r/wan/7')
  })
})

describe('le brin de sortie du plan', () => {
  // Le tracé de sortie est le seul peint par un dégradé : c'est ainsi qu'on le
  // distingue des rails, qui portent une couleur pleine.
  const sortie = (c: HTMLElement) =>
    [...c.querySelectorAll('.plan-canvas path')]
      .find((p) => p.getAttribute('stroke')?.startsWith('url('))

  const largeur = (c: HTMLElement) => Number(sortie(c)!.getAttribute('stroke-width'))

  it('s’épaissit quand PLUS DE LIENS portent', () => {
    // Ce test affirmait que le brin s'épaissit quand les liens portent
    // davantage. Il ne le prouvait pas : il changeait le NOMBRE de liens en
    // même temps que leur débit, et c'est le nombre seul qui bougeait le
    // résultat. L'échelle du dessin est relative au lien le plus chargé, donc
    // un lien seul rend la même épaisseur qu'il porte 100 kbit/s ou 500 Mbit/s.
    // Le test dit maintenant ce que le dessin encode vraiment.
    const un = monter([sample({ wan_id: '1', tx_kbps: 20000, rx_kbps: 0 })])
    const seul = largeur(un.container)
    un.unmount()

    const deux = monter([
      sample({ wan_id: '1', tx_kbps: 20000, rx_kbps: 0 }),
      sample({ wan_id: '2', tx_kbps: 20000, rx_kbps: 0 }),
    ])
    const paire = largeur(deux.container)
    deux.unmount()

    expect(paire).toBeGreaterThan(seul)
  })

  it('ne compte pas le débit d’un lien écarté du bond', () => {
    // Le brin sommait TOUS les liens, y compris ceux que le même dessin trace
    // en pointillé parce qu'ils sont hors du bond : un bond entièrement tombé
    // se dessinait alors au plus épais.
    const dedans = monter([sample({ wan_id: '1', tx_kbps: 20000, rx_kbps: 0 })])
    const seul = largeur(dedans.container)
    dedans.unmount()

    // ÉCARTÉ, pas déconnecté : connecté, tunnel connu, hors du bond. Une
    // version précédente passait `connected: false`, exclu par une autre clause
    // que celle du bond — le cas nommé par le test n'était pas exercé.
    const avecEcarte = monter([
      sample({ wan_id: '1', tx_kbps: 20000, rx_kbps: 0 }),
      { ...sample({ wan_id: '2', tx_kbps: 20000, rx_kbps: 0 }),
        wan: { ...sample({ wan_id: '2', tx_kbps: 20000, rx_kbps: 0 }).wan, tunnel: tunnel({ state: 'WAN_DOWN', in_bond: false }) } },
    ])
    expect(largeur(avecEcarte.container)).toBe(seul)
    avecEcarte.unmount()
  })

  it('n’est pas dessiné quand aucun débit n’a été mesuré', () => {
    // `reduce((n, v) => n + (v ?? 0), 0)` rendait 0 pour une absence, que le
    // plancher de 4 px rendait ensuite indiscernable d'un bond mesuré à
    // l'arrêt. Une absence de mesure n'est pas une mesure.
    const { container, unmount } = monter([
      sample({ wan_id: '1', tx_kbps: null, rx_kbps: null }),
    ])
    expect(sortie(container)).toBeUndefined()
    unmount()
  })

  it('reste dessiné, au plancher, quand le débit mesuré est nul', () => {
    // Zéro mesuré n'est pas une absence : le bond existe et le dessin le dit.
    const { container, unmount } = monter([
      sample({ wan_id: '1', tx_kbps: 0, rx_kbps: 0 }),
    ])
    expect(largeur(container)).toBe(4)
    unmount()
  })
})

describe('le pied de notes', () => {
  it('ne laisse aucune note isolée dans le bond ni dans la table', () => {
    // Onze notes empilées coûtaient 385 px sur une page de 1 515 — le quart de
    // l'écran pour six cents caractères. Elles s'écoulent désormais en lignes,
    // ce qui ne tient QUE si elles sont sœurs dans un même conteneur : une note
    // laissée en frère direct du panneau retrouve sa bande pleine largeur, et
    // rétablit le défaut sans que rien ne le signale.
    //
    // Ce test ne voit QUE les deux panneaux que `monter` rend. Il s'intitulait
    // « toutes les notes » et se lisait donc comme une garantie d'ensemble,
    // alors que trois composants — Alerts, HistoryChart, Discovery — gardaient
    // leurs notes empilées sans que rien ne vire au rouge. La garantie
    // d'ensemble est le test de fichiers ci-dessous.
    const { container, unmount } = monter(CONFIGURATIONS['MAX HD4 MBX — 4 SIM et une fibre'])
    const notes = [...container.querySelectorAll('.panel-note')]
    // Le garde-fou sert à ce que le test ne passe pas sur un écran vide.
    expect(notes.length).toBeGreaterThan(0)
    expect(notes.filter((n) => !n.parentElement?.classList.contains('panel-notes'))).toEqual([])
    unmount()
  })

  it('ne pose pas de filet quand il n’y a rien à dire', () => {
    // Le conteneur porte un filet horizontal. Rendu vide, il ajouterait un
    // trait sous un panneau qui n'a aucune note — une séparation qui ne sépare
    // rien de rien.
    const { container, unmount } = render(<Notes>{false}{null}{undefined}</Notes>)
    expect(container.querySelector('.panel-notes')).toBeNull()
    unmount()
  })
})

describe('la ligature des cellules partagées', () => {
  const ligatures = (c: HTMLElement) => c.querySelectorAll('.plan-tie')

  const surLaMemeCellule = (ids: string[], cell: number) =>
    ids.map((id) => sample({ wan_id: id, cell_id: String(cell), enb_id: String(Math.floor(cell / 256)) }))

  it('n’enjambe pas les rails qui ne partagent rien', () => {
    // Une accolade réduite aux BORNES du groupe affirmait que tout ce qu'elle
    // enjambe partage la cellule. Avec les rails 1 et 4 sur une cellule et les
    // rails 2 et 3 sur une autre, elle englobait les quatre — l'inverse du fait
    // qu'elle existe pour énoncer.
    const { container, unmount } = monter([
      ...surLaMemeCellule(['1'], 111),
      ...surLaMemeCellule(['2', '3'], 222),
      ...surLaMemeCellule(['4'], 111),
    ])
    // Les rails 1 et 4 ne sont pas voisins : aucune accolade ne doit les
    // relier par-dessus 2 et 3. Seule la suite contiguë 2–3 en reçoit une.
    const bornes = [...ligatures(container)].map((g) => {
      const d = g.querySelector('path')!.getAttribute('d')!
      const ys = [...d.matchAll(/ (\d+(?:\.\d+)?)(?: L|$)/g)].map((m) => Number(m[1]))
      return { haut: Math.min(...ys), bas: Math.max(...ys) }
    })
    // Un `every` sur une liste vide est vrai : sans ce garde, le test passerait
    // en ne dessinant aucune accolade — le défaut même qu'il surveille.
    expect(bornes.length).toBeGreaterThan(0)
    // Aucune accolade ne couvre toute la hauteur des quatre rails.
    const hauteurTotale = 4 * 30
    expect(bornes.every((b) => b.bas - b.haut < hauteurTotale - 30)).toBe(true)
    unmount()
  })

  it('donne sa propre colonne à chaque groupe', () => {
    // Deux groupes entrelacés se dessinaient au même x : deux accolades
    // superposées ne font plus qu'une à l'œil, et elle ment sur les deux.
    const { container, unmount } = monter([
      ...surLaMemeCellule(['1', '2'], 111),
      ...surLaMemeCellule(['3', '4'], 222),
    ])
    const colonnes = new Set(
      [...ligatures(container)].map((g) => g.querySelector('path')!.getAttribute('d')!.split(' ')[1]),
    )
    // Deux groupes distincts, deux abscisses distinctes.
    expect(ligatures(container).length).toBeGreaterThan(1)
    expect(colonnes.size).toBe(ligatures(container).length)
    unmount()
  })
})


describe('le survol d’un rail', () => {
  it('n’atténue plus tout le plan quand le lien survolé disparaît', () => {
    // `survole` gardait l'identifiant d'un rail DÉMONTÉ : un `<g>` qui n'existe
    // plus n'émet jamais `mouseleave`, donc `vise` devenait faux pour tous les
    // rails survivants et le dessin entier restait à 28 % jusqu'au survol
    // suivant. Déclencheur réel : un modem tombe pendant que le pointeur est
    // dessus — précisément le moment où on le regarde.
    const avant = [sample({ wan_id: '1' }), sample({ wan_id: '2' })]
    const { container, rerender, unmount } = render(
      <MemoryRouter><PlanSeul samples={avant} /></MemoryRouter>,
    )
    const groupes = () => [...container.querySelectorAll('.plan-rail')]
    // L'atténuation passe par le style : un `<Link>` est typé pour une ancre et
    // n'accepte pas l'attribut `opacity` de SVG.
    const attenuation = (e: Element) => (e as HTMLElement).style.opacity
    // On survole le second rail : le premier s'atténue.
    fireEvent.mouseOver(groupes()[1])
    expect(attenuation(groupes()[0])).toBe('0.28')

    // Le lien survolé quitte le relevé.
    rerender(<MemoryRouter><PlanSeul samples={[sample({ wan_id: '1' })]} /></MemoryRouter>)
    expect(groupes()).toHaveLength(1)
    expect(attenuation(groupes()[0])).toBe('1')
    unmount()
  })
})


describe('la ligature entre rails non adjacents', () => {
  it('marque deux liens sur une même cellule même quand un autre les sépare', () => {
    // SIM 4 et SIM 6 sur une cellule, SIM 5 d'un autre opérateur entre elles :
    // la disposition typique à deux opérateurs. Le découpage en suites
    // contiguës écartait les suites d'un seul rail, et n'y dessinait plus rien
    // — vérifié par rendu, zéro ligature là où le risque existe. Chaque suite
    // reçoit désormais son repère, dans la même colonne.
    const meme = (id: string, cell: number) =>
      sample({ wan_id: id, cell_id: String(cell), enb_id: String(Math.floor(cell / 256)) })
    const { container, unmount } = monter([meme('4', 111), meme('5', 222), meme('6', 111)])
    const ties = [...container.querySelectorAll('.plan-tie')]
    expect(ties.length).toBe(2)
    // Les deux repères sont dans la MÊME colonne : même abscisse.
    const xs = new Set(ties.map((t) => t.querySelector('path')!.getAttribute('d')!.split(' ')[1]))
    expect(xs.size).toBe(1)
    unmount()
  })

  it('compte la colonne sur les groupes dessinés, pas sur les cellules', () => {
    // Deux cellules à un seul lien avant une paire : l'index sur `cellGroups`
    // poussait la seule accolade dessinée deux colonnes vers la gauche, jusque
    // sur l'ancre des étiquettes.
    const meme = (id: string, cell: number) =>
      sample({ wan_id: id, cell_id: String(cell), enb_id: String(Math.floor(cell / 256)) })
    const { container, unmount } = monter([meme('4', 111), meme('5', 222), meme('6', 333), meme('7', 333)])
    const d = container.querySelector('.plan-tie path')!.getAttribute('d')!
    const x = Number(d.split(' ')[1])
    // Première colonne : LEFT - 4 = 72, pas 72 - 2 × 6.
    expect(x).toBe(72)
    unmount()
  })
})

describe('un filaire jamais raccordé dans la table des liens (A-6)', () => {
  it('a un rail, une étiquette et une bande neutres', () => {
    const filaire = sample({ wan_id: '2', kind: 'ethernet', connected: false, operator: null,
      operator_name: null, rat: null, band: null, rsrp: null, rsrq: null, sinr: null,
      cell_id: null, enb_id: null, status_message: 'No Cable Detected' })
    const timeline: Timeline = {
      router_id: 'r', started_at: '2026-10-17T07:00:00Z', ended_at: '2026-10-17T08:00:00Z',
      capped: false, slice_count: 10, slice_seconds: 360,
      lanes: [{ wan_id: '2', slices: [{
        t: 0, samples: 10, up: 0, carrying: 0, tunnel_known: 0, sinr_worst: null, rsrp_worst: null,
        repair: null, packets: null, disabled: 0,
      }] }],
    }
    const monter = (jamais: string[]) => render(
      <MemoryRouter>
        <LinkTable
          routerId="r" samples={[filaire]} showThroughput hasTunnel={false}
          sameCell={new Map()} timeline={timeline} windowLabel="Depuis la mise sous tension"
          churn={[]} latencyThresholdMs={1000} radioGrievances={[]} neverConnected={jamais}
        />
      </MemoryRouter>,
    )
    const neutre = monter(['2'])
    const rail = neutre.container.querySelector('tbody .rail') as HTMLElement
    expect(rail.style.getPropertyValue('--q')).toBe(QUALITY_COLOR.inconnu)
    expect(neutre.container.querySelector('tbody .tag.is-off')).toBeNull()
    expect(neutre.container.textContent).toContain('No Cable Detected')
    expect(neutre.container.innerHTML).not.toContain(QUALITY_COLOR.faible)
    neutre.unmount()
    // Un filaire qui TOMBE, lui, reste une panne.
    const tombe = monter([])
    expect((tombe.container.querySelector('tbody .rail') as HTMLElement).style.getPropertyValue('--q'))
      .toBe(QUALITY_COLOR.faible)
  })
})
