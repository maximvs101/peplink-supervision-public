import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { porteLeFlux } from '../lib/bondRole'
import { keyFigures } from '../lib/dashboard'
import { pathDiversity } from '../lib/diversity'
import { releve, tunnelRealiste, wanRealiste } from '../test/factories'
import { BondPlan } from './BondPlan'

/**
 * C-17 : le bandeau et le plan appliquaient deux règles différentes pour dire
 * si le bond porte le flux. Le verdict tenait compte de `tunnel_attendu` — un
 * bond qui n'a JAMAIS tenu dans la session ne porte pas le flux —, le plan
 * non : sur le MAX HD2, le bandeau disait « jamais établi » pendant que le plan
 * titrait « 0 / 3 liens qui portent » et affirmait que chaque paquet perdu
 * l'était pour le flux.
 */

describe('une seule règle pour « le bond porte le flux »', () => {
  it('tient compte du rôle ET de ce que le serveur a tranché', () => {
    expect(porteLeFlux({ kind: 'inconnu' }, null)).toBe(true)
    expect(porteLeFlux({ kind: 'tout' }, true)).toBe(true)
    expect(porteLeFlux({ kind: 'routes', routes: ['10.0.0.0/8'] }, null)).toBe(false)
    expect(porteLeFlux({ kind: 'inconnu' }, false)).toBe(false)
    expect(porteLeFlux({ kind: 'tout' }, false)).toBe(false)
  })

  it('le plan d’un bond jamais établi ne compte pas des liens « qui portent »', () => {
    const samples = ['1', '4', '5'].map((id) => releve(wanRealiste({
      wan_id: id, tunnel: tunnelRealiste({ state: 'PEER_DOWN', in_bond: false }),
    })))
    const diversite = pathDiversity(samples)
    const { container } = render(
      <MemoryRouter>
        <BondPlan
          samples={samples} figures={keyFigures(samples)}
          redundancy={{ state: 'unknown', packets: 0 }}
          cells={diversite.cells} sites={diversite.sites}
          located={diversite.located} attached={diversite.attached}
          role={{ kind: 'inconnu' }} grievances={[]} tunnelAttendu={false}
          tunnelUptimeS={null} throughputLimitMbps={null} routerId="hd2"
          cellGroups={diversite.cellGroups}
        />
      </MemoryRouter>,
    )
    const texte = container.textContent ?? ''
    expect(texte).not.toContain('qui portent')
    expect(texte).not.toContain('réunis en une liaison')
  })
})
