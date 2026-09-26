import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { BandMeasurement } from '../types'
import { BandTable } from './BandTable'

const band = (over: Partial<BandMeasurement> = {}): BandMeasurement => ({
  rat: 'LTE', band: 'B3', label: 'LTE Band 3 (1800 MHz)', channel: '1300',
  pci: null, channel_width: null, rsrp: -88, rsrq: -11, sinr: 12, rssi: -58,
  ...over,
})

/**
 * Le tableau n'existe que pour COMPARER des porteuses entre elles : une ancre
 * LTE excellente sous une 5G faible explique un débit décevant malgré un badge
 * « 5G ». Sur une porteuse unique il n'y a rien à comparer, et la ligne répète
 * mot pour mot les tuiles du dessus — le backend tire justement RSRP, RSRQ et
 * SINR de cette porteuse-là comme porteuse principale.
 */
describe('tableau des porteuses agrégées', () => {
  it('ne rend rien sur une porteuse unique', () => {
    const { container } = render(<BandTable bands={[band()]} primaire={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('ne rend rien quand le modem ne publie aucune porteuse', () => {
    const { container } = render(<BandTable bands={[]} primaire={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('rend le détail dès qu’il y a de quoi comparer', () => {
    render(
      <BandTable
        bands={[band(), band({ rat: '5G NSA', band: 'n78', channel: '632628' })]}
        primaire="B3"
      />,
    )
    expect(screen.getByText(/Porteuses agrégées \(2\)/)).toBeInTheDocument()
    expect(screen.getByText('CA active')).toBeInTheDocument()
    expect(screen.getByText('principale')).toBeInTheDocument()
  })
})

/**
 * Deux colonnes que le Telit LM960 du MAX HD4 MBX ne renseigne jamais : il
 * renvoie `pci: 0` sur toutes ses porteuses — absence que le backend traduit en
 * null — et aucune largeur de canal. Une colonne entièrement remplie de tirets
 * se lit comme une mesure perdue. Le Dome Pro Duo, lui, sert les deux : quatre
 * PCI distincts et des largeurs de 15 à 90 MHz sur le même relevé. La colonne
 * suit donc la donnée, au lieu d'être effacée ou imposée partout.
 */
describe('colonnes qui suivent ce que le modem publie', () => {
  it('efface PCI et Largeur quand aucune porteuse ne les renseigne', () => {
    render(<BandTable bands={[band(), band({ band: 'B7', channel: '3000' })]} primaire={null} />)
    expect(screen.queryByText('PCI')).not.toBeInTheDocument()
    expect(screen.queryByText('Largeur')).not.toBeInTheDocument()
    // Le canal, lui, est publié par les deux modèles et reste toujours là.
    expect(screen.getByText('ARFCN')).toBeInTheDocument()
  })

  it('garde PCI et Largeur dès qu’une seule porteuse les renseigne', () => {
    render(
      <BandTable
        bands={[
          band({ rat: '5G NSA', band: 'n78', channel: '650400', pci: '649', channel_width: '90 MHz' }),
          band({ pci: '268', channel_width: '20 MHz' }),
        ]}
        primaire={null}
      />,
    )
    expect(screen.getByText('PCI')).toBeInTheDocument()
    expect(screen.getByText('649')).toBeInTheDocument()
    expect(screen.getByText('90 MHz')).toBeInTheDocument()
  })

  it('marque d’un tiret la seule porteuse qui manque à l’appel', () => {
    // Colonne conservée parce qu'une porteuse la renseigne : celle qui ne la
    // renseigne pas est un trou réel, et doit se voir comme tel.
    render(<BandTable bands={[band({ pci: '649' }), band({ pci: null })]} primaire={null} />)
    expect(screen.getByText('PCI')).toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})

describe('la porteuse principale', () => {
  it('suit le choix du backend, pas le rang dans la liste', () => {
    // Le badge se posait sur l'index 0, c'est-à-dire la première LISTÉE. Or
    // `_primary_band` retient la porteuse de la famille courante, et en 5G NSA
    // elle saute l'ancre LTE : le badge nommait alors une porteuse que la tuile
    // du dessus ne décrivait pas — deux affirmations contradictoires à trois
    // centimètres l'une de l'autre.
    const bands = [
      { rat: 'LTE', band: 'B3', label: null, channel: '1300', pci: null,
        channel_width: null, rsrp: -83, rsrq: -9, sinr: 18, rssi: null },
      { rat: '5G', band: 'n78', label: null, channel: '640000', pci: null,
        channel_width: null, rsrp: -95, rsrq: -12, sinr: 9, rssi: null },
    ]
    const { rerender } = render(<BandTable bands={bands} primaire="n78" />)
    const principale = screen.getByText('principale').closest('td')
    expect(principale?.textContent).toContain('n78')

    // Aucune correspondance : aucun badge. On ne devine pas.
    rerender(<BandTable bands={bands} primaire={null} />)
    expect(screen.queryByText('principale')).toBeNull()
    rerender(<BandTable bands={bands} primaire="B99" />)
    expect(screen.queryByText('principale')).toBeNull()
  })
})

describe('la porteuse principale, en agrégation intra-bande (C-44)', () => {
  it('ne pose le badge que sur la porteuse de ce canal', () => {
    // Deux porteuses B3 agrégées : reconnue par son seul code de bande, la
    // principale était marquée deux fois.
    const { container } = render(<BandTable bands={[
      band({ band: 'B3', channel: '1300' }), band({ band: 'B3', channel: '1850' }),
    ]} primaire="B3" canal="1850" />)
    const lignes = [...container.querySelectorAll('tbody tr')]
    expect(lignes.map((l) => l.textContent?.includes('principale'))).toEqual([false, true])
  })

  it('ne devine pas quand le canal manque et que la bande est partagée', () => {
    const { container } = render(<BandTable bands={[
      band({ band: 'B3', channel: '1300' }), band({ band: 'B3', channel: '1850' }),
    ]} primaire="B3" canal={null} />)
    expect(container.textContent).not.toContain('principale')
  })
})
