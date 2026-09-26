/**
 * Confrontation : l'écran face à des équipements qu'il n'a jamais vus.
 *
 * La galerie voisine (`scenarios.test.tsx`) couvre les configurations que le
 * projet A RENCONTRÉES. Celle-ci couvre celles qu'il va rencontrer : un autre
 * réseau, un autre châssis, un autre firmware, peut-être sans le moindre modem.
 *
 * Le contrôle central n'est pas esthétique. C'est un balayage du TEXTE RENDU à
 * la recherche de ce qu'une interface ne doit jamais écrire : « NaN », « null »,
 * « undefined », « Infinity », « Invalid Date ». Chacun de ces mots est une
 * grandeur absente qui s'est échappée d'un garde et s'affiche avec l'aplomb
 * d'une mesure. Ils sont indétectables à la relecture du code et évidents ici.
 */

import { describe, expect, it } from 'vitest'

import type { RadioSample } from '../types'
import { monterTableau, sampleNu, tunnelNu } from '../test/factories'

// Les fabriques vivent dans `src/test/factories.tsx`, partagées avec la
// galerie. Ici tout est NUL par défaut : on vérifie qu'une absence n'est
// jamais rendue par un zéro.
const ech = sampleNu
const tun = tunnelNu

/** Les familles d'équipement que la prochaine connexion peut présenter. */
const PROFILS: Record<string, RadioSample[]> = {
  // Une Balance de bureau : aucun modem. Tout l'écran radio doit s'effacer
  // plutôt que d'afficher des colonnes de tirets.
  'balance filaire seule': [
    ech({ wan_id: '1', kind: 'ethernet', wan_name: 'WAN 1', rat: 'Ethernet',
          tx_kbps: 12000, rx_kbps: 45000, ip: '192.168.1.2' }),
    ech({ wan_id: '2', kind: 'ethernet', wan_name: 'WAN 2', rat: 'Ethernet',
          connected: false, status_message: 'Disconnected' }),
  ],
  // Un BR1 : un seul lien. Il n'y a pas de bond, et rien ne doit prétendre
  // le contraire.
  'BR1, une seule SIM': [
    ech({ wan_id: '1', operator: 'Free', operator_name: 'Free', rat: 'LTE', band: 'B28',
          cell_id: '12345678', enb_id: '48225', rsrp: -101, sinr: 4.2 }),
  ],
  // Un WAN Wi-Fi, que ni le MBX ni le Dome n'ont.
  'Wi-Fi en second lien': [
    ech({ wan_id: '1', kind: 'ethernet', wan_name: 'WAN', rat: 'Ethernet', tx_kbps: 900 }),
    ech({ wan_id: '2', kind: 'wifi', wan_name: 'Wi-Fi WAN', rat: 'Wi-Fi',
          rssi: -62, ip: '192.168.50.7' }),
  ],
  // 5G SA : la cellule est connue, la station ne l'est pas. C'est le cas où
  // le bandeau de totaux écrit « station indéterminable ».
  '5G SA, station indéterminable': [
    ech({ wan_id: '1', operator: 'Orange', operator_name: 'Orange', rat: '5G SA', band: 'n78',
          cell_id: '1234567890123', enb_id: null }),
    ech({ wan_id: '2', operator: 'Orange', operator_name: 'Orange', rat: '5G SA', band: 'n78',
          cell_id: '9876543210987', enb_id: null }),
  ],
  // Le routeur répond, et absolument rien n'est mesuré.
  'tout absent': [ech({ wan_id: '1' }), ech({ wan_id: '2' })],
  // Aucun WAN du tout : châssis en configuration, ou tous ports en LAN.
  'aucun lien': [],
  // Un tunnel monté dont aucune grandeur n'est publiée.
  'tunnel muet': [
    ech({ wan_id: '1', tunnel: tun() }),
    ech({ wan_id: '2', tunnel: tun() }),
  ],
  // Un firmware qui publie des nombres MALFORMÉS. Ce n'est pas une hypothèse
  // d'école : le collecteur convertit ce que le routeur envoie, et une chaîne
  // vide, une division par zéro ou un champ « -- » produisent NaN ou Infinity
  // sans lever. C'est le seul profil qui rende le balayage des mots interdits
  // capable d'attraper quelque chose — sans lui, il passe sur du vide.
  'nombres malformés': [
    ech({ wan_id: '1', operator: 'Inconnu', operator_name: 'Inconnu', rat: 'LTE', band: 'B1',
          rsrp: NaN, sinr: Number.POSITIVE_INFINITY, rsrq: NaN,
          tx_kbps: NaN, rx_kbps: Number.POSITIVE_INFINITY,
          path_latency_ms: NaN, path_latency_recent_ms: NaN, modem_temperature_c: NaN, uptime_s: NaN,
          tunnel: tun({ rtt_ms: NaN, loss_percent: Number.POSITIVE_INFINITY }) }),
    ech({ wan_id: '2', operator: 'Inconnu', operator_name: 'Inconnu', rat: 'LTE', band: 'B1',
          rsrp: -95, sinr: 10, tx_kbps: 1000, rx_kbps: 2000 }),
  ],
  // Treize liens mixtes : le haut de gamme.
  'treize liens mixtes': [
    ...Array.from({ length: 3 }, (_, i) =>
      ech({ wan_id: String(i + 1), kind: 'ethernet', wan_name: `WAN ${i + 1}`, rat: 'Ethernet' })),
    ...Array.from({ length: 9 }, (_, i) =>
      ech({ wan_id: String(i + 4), operator: 'Orange', operator_name: 'Orange', rat: 'LTE', band: 'B7',
            cell_id: String(1000 + i), enb_id: String(10 + i), rsrp: -90 })),
    ech({ wan_id: '13', kind: 'wifi', wan_name: 'Wi-Fi', rat: 'Wi-Fi' }),
  ],
}

const monter = monterTableau

/* Les mots qu'une interface ne doit jamais écrire. Chacun est une grandeur
   absente qui s'est échappée d'un garde : `Math.round(null)` rend 0, mais
   `Math.round(undefined)` rend « NaN », et `toFixed` sur un infini rend
   « Infinity ». Ils s'affichent avec l'aplomb d'une mesure. */
const INTERDITS = ['NaN', 'undefined', 'Infinity', 'Invalid Date', '[object Object]']
/* `null` se cherche en MOT ENTIER : en sous-chaîne, il tombait sur « nulle »,
   « nullement », « annulé » — et ce fichier même parle de « hauteur nulle ». */
const NULL_ENTIER = /\bnull\b/

describe('confrontation : un équipement jamais vu', () => {
  for (const [nom, samples] of Object.entries(PROFILS)) {
    it(`« ${nom} » se rend sans rien casser`, () => {
      const { container, unmount } = monter(samples)
      expect(container.querySelector('.plan')).not.toBeNull()
      // Chaque lien a son rail ET sa ligne : rien n'est perdu en route.
      expect(container.querySelectorAll('.plan-rail')).toHaveLength(samples.length)
      expect(container.querySelectorAll('.ltable tbody tr')).toHaveLength(samples.length)
      unmount()
    })

    it(`« ${nom} » n’écrit aucune valeur technique à l’écran`, () => {
      const { container, unmount } = monter(samples)
      const texte = container.textContent ?? ''
      // Sans ce garde, le balayage passerait sur un écran vide — c'est-à-dire
      // exactement dans le cas où il devrait le plus alerter.
      expect(texte.length).toBeGreaterThan(80)
      for (const mot of INTERDITS) {
        expect(texte, `« ${nom} » affiche « ${mot} »`).not.toContain(mot)
      }
      expect(texte, `« ${nom} » affiche « null »`).not.toMatch(NULL_ENTIER)
      // Et les ATTRIBUTS, que le texte ne voit pas : un NaN dans une largeur
      // de trait ne s'affiche nulle part, il efface le dessin. Mesuré : une
      // seule valeur malformée rendait « stroke-width="NaN" » sur TOUS les
      // rails, y compris le sain, et ce balayage restait vert.
      for (const el of container.querySelectorAll('[stroke-width], [d], [opacity], [x], [y], [cx], [cy], [r]')) {
        for (const attr of ['stroke-width', 'd', 'opacity', 'x', 'y', 'cx', 'cy', 'r']) {
          const v = el.getAttribute(attr)
          if (v !== null) expect(v, `« ${nom} » : ${el.tagName} ${attr}="${v}"`).not.toMatch(/NaN|Infinity|undefined/)
        }
      }
      unmount()
    })
  }

  it('n’annonce pas un bond là où il n’y a qu’un lien', () => {
    // Un BR1 n'agrège rien. Le mot « bond » garde un sens précis dans cet
    // outil ; l'employer pour un lien unique le vide de ce sens.
    const { container, unmount } = monter(PROFILS['BR1, une seule SIM'])
    const tete = container.querySelector('.plan .panel-head')?.textContent ?? ''
    expect(tete).toContain('un seul chemin physique')
    unmount()
  })

  it('efface les colonnes radio sur un châssis sans modem', () => {
    // Sur une Balance, une colonne SINR pleine de tirets se lit comme une
    // panne de mesure. Elle ne doit pas exister.
    const { container, unmount } = monter(PROFILS['balance filaire seule'])
    const entetes = [...container.querySelectorAll('.ltable thead th')]
      .map((e) => e.textContent?.trim().toUpperCase())
    expect(entetes).not.toContain('SINR DB')
    expect(entetes).not.toContain('RSRP DBM')
    unmount()
  })

  it('ne dessine pas de brin de sortie quand aucun débit n’est mesuré', () => {
    const { container, unmount } = monter(PROFILS['tout absent'])
    const sortie = [...container.querySelectorAll('.plan-canvas path')]
      .find((p) => p.getAttribute('stroke')?.startsWith('url('))
    expect(sortie).toBeUndefined()
    unmount()
  })

  it('ne prétend pas connaître la station sur un identifiant 5G SA', () => {
    // Deux cellules distinctes, aucune station connue : l'écran doit dire
    // « indéterminable » et non « 0 site », qui serait un zéro pour une absence.
    const { container, unmount } = monter(PROFILS['5G SA, station indéterminable'])
    const texte = container.textContent ?? ''
    expect(texte).toContain('indéterminable')
    expect(texte).not.toMatch(/0\s*sites?/)
    unmount()
  })
})
