import { describe, expect, it } from 'vitest'

import { bondRole, roleRetenu } from './bondRole'

const DEDIEES = ['169.254.131.0/24', '212.83.185.61/32']

/**
 * La première règle de ce module était FAUSSE, et le matériel l'a démontré.
 *
 * On lisait les sous-réseaux annoncés par le pair, et de l'absence de la route
 * par défaut on concluait qu'aucun profil n'était sélectionné dans « Send All
 * Traffic To ». Le 13 septembre, l'exploitant a activé le trafic SpeedFusion :
 * vingt-six mille paquets ont traversé le tunnel, le débit des interfaces a
 * fondu d'autant — et les routes annoncées n'ont pas bougé d'un mot.
 */
describe('à quoi sert le bond', () => {
  it('n’affirme rien quand le pair ne déclare aucune route', () => {
    expect(bondRole(undefined, 0)).toEqual({ kind: 'inconnu' })
    expect(bondRole([], 50_000)).toEqual({ kind: 'inconnu' })
  })

  it('reconnaît la route par défaut : le tunnel achemine tout', () => {
    expect(bondRole(['0.0.0.0/0', ...DEDIEES], 0)).toEqual({ kind: 'tout' })
  })

  it('ne prend pas un keepalive pour du trafic, quelle que soit la fenêtre', () => {
    // Le témoin a d'abord été un NOMBRE de paquets comparé à une fenêtre de
    // durée libre. Mesuré sur le matériel, le tunnel au repos du 11 septembre
    // porte 1,33 paquet par seconde : il franchissait mille paquets en douze
    // minutes, et l'écran annonçait « le bond porte du trafic » sur la journée
    // même dont les données prouvaient le contraire.
    expect(bondRole(DEDIEES, 1.33)).toEqual({ kind: 'routes', routes: DEDIEES })
  })

  it('cesse de nier dès qu’un débit le dément', () => {
    // Le tunnel en charge du 13 septembre : 122,7 paquets par seconde, soit
    // quatre-vingt-douze fois le repos.
    expect(bondRole(DEDIEES, 122.7)).toEqual({ kind: 'porte', routes: DEDIEES })
  })

  it('place le seuil loin des deux régimes mesurés', () => {
    expect(bondRole(DEDIEES, 9.9).kind).toBe('routes')
    expect(bondRole(DEDIEES, 10).kind).toBe('porte')
  })

  it('sans témoin de trafic, ne nie pas que le tunnel porte (C-2)', () => {
    // Ce test affirmait l'inverse : faute de débit mesuré, le rôle retombait
    // sur `routes` — « rien ne transite ». Or le débit s'éteint avec le tunnel
    // lui-même : dix minutes après la chute du concentrateur, la dernière
    // tranche portante sortait de la fenêtre et le bandeau repassait au vert
    // « Liaison OK », tunnel toujours à terre. Une absence de mesure ne fonde
    // pas une négation.
    expect(bondRole(DEDIEES)).toEqual({ kind: 'inconnu', routes: DEDIEES })
    expect(bondRole(DEDIEES, null)).toEqual({ kind: 'inconnu', routes: DEDIEES })
    // La route par défaut, elle, n'a pas besoin de témoin de trafic.
    expect(bondRole(['0.0.0.0/0'], null)).toEqual({ kind: 'tout' })
  })
})

describe('le rôle établi tunnel debout survit à sa chute', () => {
  it('garde le dernier rôle mesuré quand le débit s’éteint', () => {
    const avant = bondRole(DEDIEES, 1.33)
    expect(roleRetenu(bondRole(DEDIEES, null), avant)).toEqual({ kind: 'routes', routes: DEDIEES })
    const charge = bondRole(DEDIEES, 122.7)
    expect(roleRetenu(bondRole(DEDIEES, null), charge)).toEqual({ kind: 'porte', routes: DEDIEES })
  })

  it('ne garde pas un rôle établi pour d’autres routes', () => {
    const avant = bondRole(['10.9.0.0/16'], 1.33)
    expect(roleRetenu(bondRole(DEDIEES, null), avant)).toEqual({ kind: 'inconnu', routes: DEDIEES })
  })

  it('laisse une mesure fraîche l’emporter sur la mémoire', () => {
    const avant = bondRole(DEDIEES, 122.7)
    expect(roleRetenu(bondRole(DEDIEES, 1.33), avant).kind).toBe('routes')
  })

  it('sans mémoire, reste inconnu', () => {
    expect(roleRetenu(bondRole(DEDIEES, null), null)).toEqual({ kind: 'inconnu', routes: DEDIEES })
  })
})
