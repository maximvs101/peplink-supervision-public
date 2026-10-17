import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { Episode, EventLog as Log } from '../types'
import { EventLog } from './EventLog'
import { QUALITY_COLOR } from '../lib/quality'

const episode = (over: Partial<Episode> = {}): Episode => ({
  kind: 'latence', wan_id: '4', link_kind: 'cellular',
  at: '2026-09-11T20:46:11Z', until: null, samples: 1,
  peak_ms: 1025, detail: null, after_handover_s: null,
  ...over,
})

const journal = (episodes: Episode[], over: Partial<Log> = {}): Log => ({
  router_id: 'mbx',
  started_at: '2026-09-11T11:34:22Z',
  ended_at: '2026-09-11T20:50:00Z',
  capped: false,
  episodes,
  churn: [],
  omitted: 0,
  latency_threshold_ms: 1000,
  ...over,
})

const colonnes = () => [...document.querySelectorAll('thead th')].map((t) => t.textContent)

/** La colonne portait le seul message du routeur. Elle porte désormais DEUX
 *  sortes de cause — ce que le routeur a déclaré, qui est un fait, et le
 *  changement de cellule qui précède un pic, qui est une corrélation mesurée —
 *  d'où son titre plus court. Elle ne s'ouvrait que pour la première, si bien
 *  qu'une session sans message de statut cachait aussi les explications. */
describe('la colonne des causes', () => {
  it('n’ouvre pas la colonne quand aucun épisode ne la renseigne', () => {
    // Le message du routeur n'existe que sur les transitions — coupure, retour.
    // Mesuré sur la base : 0 des 76 épisodes en portaient un, et la colonne
    // était donc entièrement composée de tirets. Une colonne de tirets se lit
    // comme une mesure perdue.
    render(<EventLog log={journal([episode(), episode({ at: '2026-09-11T20:22:06Z' })])}
                     windowLabel="Depuis la mise sous tension" />)
    expect(colonnes()).not.toContain('Cause')
    expect(screen.getAllByText('Écarté par la latence')).toHaveLength(2)
  })

  it('l’ouvre dès qu’un épisode porte la cause', () => {
    // Contre-épreuve : sans elle, l'assertion ci-dessus passerait même si la
    // colonne n'existait plus du tout.
    render(
      <EventLog
        log={journal([
          episode(),
          episode({ kind: 'coupure', peak_ms: null, detail: 'No Cable Detected' }),
        ])}
        windowLabel="Depuis la mise sous tension"
      />,
    )
    expect(colonnes()).toContain('Cause')
    expect(screen.getByText('No Cable Detected')).toBeInTheDocument()
  })
})

describe('un épisode qui ne décrit aucun lien', () => {
  it('nomme l’équipement, et non un modem', () => {
    // La position est celle de l'équipement : l'attribuer à un modem serait
    // faux, et lui donner un identifiant vide afficherait « SIM  ».
    render(
      <EventLog
        log={journal([episode({ kind: 'position perdue', wan_id: '', link_kind: '', peak_ms: null })])}
        windowLabel="Depuis la mise sous tension"
      />,
    )
    expect(screen.getByText('Équipement')).toBeInTheDocument()
    expect(screen.getByText('Position perdue')).toBeInTheDocument()
  })
})

describe('ce qui reste caché ne l’est jamais en silence', () => {
  it('dit combien d’épisodes ne sont pas montrés', () => {
    const beaucoup = Array.from({ length: 20 }, (_, i) =>
      episode({ at: `2026-09-11T20:${String(10 + i).padStart(2, '0')}:00Z` }),
    )
    render(<EventLog log={journal(beaucoup)} windowLabel="Depuis la mise sous tension" />)
    expect(screen.getByRole('button', { name: /Voir les 14 autres/ })).toBeInTheDocument()
    expect(document.querySelectorAll('tbody tr')).toHaveLength(6)
  })
})

describe('une fenêtre sans incident', () => {
  it('le dit sans inventer de seuil', () => {
    render(<EventLog log={journal([])} windowLabel="Depuis la mise sous tension" />)
    expect(screen.getByText(/Aucune coupure/)).toBeInTheDocument()
    expect(document.querySelector('table')).toBeNull()
  })
})

describe('un épisode qui dure encore', () => {
  it('affiche le temps écoulé, et non le tiret des épisodes instantanés', () => {
    // Câble WAN retiré à 23 h 21 sur le matériel de référence : le backend date
    // la coupure et laisse `until` nul jusqu'au retour, qu'il ignore. La colonne
    // affichait donc « — », qui dans cette table veut dire « un seul relevé » :
    // un lien débranché depuis trois minutes se lisait comme un incident passé.
    render(
      <EventLog
        log={journal(
          [episode({ kind: 'coupure', at: '2026-09-11T20:21:25Z', peak_ms: null,
                     wan_id: '1', link_kind: 'ethernet', detail: 'No Cable Detected' })],
          { ended_at: '2026-09-11T20:50:00Z' },
        )}
        windowLabel="Depuis la mise sous tension"
      />,
    )
    expect(screen.getByText('en cours')).toBeInTheDocument()
    expect(screen.getByText(/28 min/)).toBeInTheDocument()
  })

  it('fige la durée sur le retour, qui la porte', () => {
    // Rebranchement mesuré le 11 septembre à 23 h 33 : le backend date la
    // coupure et laisse `until` nul POUR TOUJOURS — au moment où il l'écrit, il
    // ignore quand le lien reviendra, et il ne repasse pas sur ses lignes. Sans
    // lire le retour, la coupure de douze minutes serait retombée sur le tiret
    // des épisodes instantanés dès qu'elle s'est terminée.
    render(
      <EventLog
        log={journal([
          episode({ kind: 'retour', at: '2026-09-11T20:33:30Z', peak_ms: null,
                    wan_id: '1', detail: 'Connected' }),
          episode({ kind: 'coupure', at: '2026-09-11T20:21:25Z', peak_ms: null,
                    wan_id: '1', detail: 'No Cable Detected' }),
        ])}
        windowLabel="Depuis la mise sous tension"
      />,
    )
    expect(screen.queryByText('en cours')).not.toBeInTheDocument()
    expect(screen.getByText(/12 min/)).toBeInTheDocument()
  })

  it('apparie chaque coupure à SON retour, et non au dernier', () => {
    // Un lien qui tombe et revient deux fois produit deux paires. Prendre le
    // dernier retour donnerait à la première coupure la durée des deux, soit
    // ici 40 minutes au lieu de 3.
    render(
      <EventLog
        log={journal([
          episode({ kind: 'retour', at: '2026-09-11T20:45:00Z', peak_ms: null, wan_id: '1' }),
          episode({ kind: 'coupure', at: '2026-09-11T20:40:00Z', peak_ms: null, wan_id: '1' }),
          episode({ kind: 'retour', at: '2026-09-11T20:08:00Z', peak_ms: null, wan_id: '1' }),
          episode({ kind: 'coupure', at: '2026-09-11T20:05:00Z', peak_ms: null, wan_id: '1' }),
        ])}
        windowLabel="Depuis la mise sous tension"
      />,
    )
    const durees = [...document.querySelectorAll<HTMLTableRowElement>('tbody tr')].map(
      (r) => r.cells[3].textContent,
    )
    expect(durees).toEqual(['—', '5 min', '—', '3 min'])
  })

  it('ne fige pas la coupure d’un AUTRE lien', () => {
    // Le retour de la SIM 4 ne clôt pas la coupure du WAN 1 : un appariement
    // par genre seul refermerait n'importe quel incident dès qu'un voisin
    // revient.
    render(
      <EventLog
        log={journal(
          [
            episode({ kind: 'retour', at: '2026-09-11T20:30:00Z', peak_ms: null, wan_id: '4' }),
            episode({ kind: 'coupure', at: '2026-09-11T20:21:25Z', peak_ms: null, wan_id: '1' }),
          ],
          { ended_at: '2026-09-11T20:50:00Z' },
        )}
        windowLabel="Depuis la mise sous tension"
      />,
    )
    expect(screen.getByText('en cours')).toBeInTheDocument()
  })

  it('ne prétend pas qu’un relevé de latence isolé dure encore', () => {
    // Un épisode de latence n'a pas d'épisode de clôture : son `until` nul veut
    // dire « un seul relevé », pas « toujours en cours ». Les confondre ferait
    // durer indéfiniment chaque pic de 500 ms.
    render(<EventLog log={journal([episode()])} windowLabel="Depuis la mise sous tension" />)
    expect(screen.queryByText('en cours')).not.toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})

describe('les mots du routeur, et le bond quitté sans coupure', () => {
  it('nomme un signalement sans en faire une panne', () => {
    // « Detecting MTU... » n'est ni une coupure ni un rétablissement : c'est un
    // état annoncé, et lui donner un rail rouge apprendrait à ignorer le rouge.
    render(
      <EventLog
        log={journal([
          episode({ kind: 'signalement', peak_ms: null, wan_id: '1', link_kind: 'ethernet',
                    detail: 'No Cable Detected → Connecting...' }),
        ])}
        windowLabel="Depuis la mise sous tension"
      />,
    )
    expect(screen.getByText('Signalement')).toBeInTheDocument()
    expect(screen.getByText('No Cable Detected → Connecting...')).toBeInTheDocument()
  })

  it('chronomètre une sortie du bond jusqu’à son retour', () => {
    // Un lien peut quitter le bond en restant connecté — le tunnel tombe, ou
    // quelqu'un désactive le WAN. La paire se referme comme celle des coupures.
    render(
      <EventLog
        log={journal([
          episode({ kind: 'de retour dans le bond', at: '2026-09-11T20:33:35Z',
                    peak_ms: null, wan_id: '1' }),
          episode({ kind: 'hors du bond', at: '2026-09-11T20:21:31Z', peak_ms: null,
                    wan_id: '1', detail: 'WAN_DISABLED' }),
        ])}
        windowLabel="Depuis la mise sous tension"
      />,
    )
    expect(screen.getByText('Hors du bond')).toBeInTheDocument()
    expect(screen.getByText('De retour dans le bond')).toBeInTheDocument()
    expect(screen.getByText(/12 min/)).toBeInTheDocument()
    expect(screen.queryByText('en cours')).not.toBeInTheDocument()
    // La décision de configuration est nommée : sans ce mot, on cherche un
    // incident là où quelqu'un a désactivé un WAN.
    expect(screen.getByText('WAN_DISABLED')).toBeInTheDocument()
  })
})

describe('le changement de cellule comme EXPLICATION', () => {
  it('dit pourquoi un pic est arrivé, quand il y a une raison', () => {
    // Mesuré : après un changement de cellule, un pic de plus de 150 ms survient
    // dans les trois relevés suivants 43,8 % du temps contre 21,7 % sans — un
    // risque doublé. Mais le pic dure une quinzaine de secondes : l'information
    // arrive EN MÊME TEMPS que l'effet. C'est une explication, pas une alerte.
    render(<EventLog log={journal([episode({ after_handover_s: 5.1, peak_ms: 2342 })])} windowLabel="depuis la mise sous tension" />)
    expect(screen.getByText('5 s après un changement de cellule')).toBeInTheDocument()
  })

  it('dit « au moment » plutôt que « 0 s après »', () => {
    render(<EventLog log={journal([episode({ after_handover_s: 0 })])} windowLabel="depuis la mise sous tension" />)
    expect(screen.getByText('au moment d’un changement de cellule')).toBeInTheDocument()
  })

  it('laisse un pic SANS explication quand il n’y en a pas', () => {
    // Le marquage ne couvre qu'un pic sur six. Les cinq autres doivent rester
    // sans explication plutôt que d'en recevoir une fausse.
    const { container } = render(<EventLog log={journal([episode()])} windowLabel="depuis la mise sous tension" />)
    expect(container.textContent).not.toMatch(/changement de cellule/)
  })

  it('laisse la parole au ROUTEUR quand il a dit quelque chose', () => {
    // Ce que le routeur déclare prime : c'est un fait, pas une corrélation.
    render(
      <EventLog
        log={journal([episode({ detail: 'No Cable Detected', after_handover_s: 3 })])}
        windowLabel="depuis la mise sous tension"
      />,
    )
    expect(screen.getByText('No Cable Detected')).toBeInTheDocument()
    expect(screen.queryByText(/changement de cellule/)).toBeNull()
  })
})

describe('une fenêtre rognée par le serveur', () => {
  it('est dite comme telle', () => {
    // Une fenêtre rognée en silence se lit comme une fenêtre entière.
    render(<EventLog log={journal([], { capped: true })} windowLabel="Depuis la mise sous tension" />)
    expect(screen.getByText(/dernières 24 h seulement/)).toBeInTheDocument()
  })
})

describe('les faits du journal du routeur', () => {
  it('sont nommés, rattachés à l’équipement quand ils ne visent aucun lien', () => {
    render(<EventLog log={journal([
      episode({ kind: 'bascule de SIM', wan_id: '3', detail: 'vers la SIM B', peak_ms: null }),
      episode({ kind: 'priorités modifiées', wan_id: null, peak_ms: null }),
      episode({ kind: 'connexion admin refusée', wan_id: null, peak_ms: null }),
      episode({ kind: 'coupure', wan_id: '3', detail: 'Disabled', peak_ms: null }),
    ])} windowLabel="Depuis la mise sous tension" />)
    for (const mot of ['Bascule de SIM', 'Ordre des WAN réévalué', 'Connexion admin refusée', 'vers la SIM B', 'Disabled']) {
      expect(screen.getByText(mot)).toBeInTheDocument()
    }
    expect(screen.getAllByText('Équipement')).toHaveLength(2)
    expect(document.body.textContent).not.toMatch(/null/)
  })
})

describe('le changement d’opérateur (R-2.3)', () => {
  it('est une ligne du lien, neutre, avec l’avant et l’après', () => {
    render(<EventLog log={journal([
      episode({ kind: "changement d'opérateur", wan_id: '3', peak_ms: null,
                detail: 'Orange F (208-01) → Mobile S Mobile (208-10)' }),
    ])} windowLabel="Depuis la mise sous tension" />)
    expect(screen.getByText('Changement d’opérateur')).toBeInTheDocument()
    expect(screen.getByText('Orange F (208-01) → Mobile S Mobile (208-10)')).toBeInTheDocument()
    // Un genre inconnu s'afficherait sous sa clé brute : ce n'est pas le cas.
    expect(screen.queryByText("changement d'opérateur")).not.toBeInTheDocument()
  })
})

describe('le journal d’un rapport imprimé (R-3.7)', () => {
  it('rend tous ses épisodes, sans bouton qu’un PDF ne sait pas presser', () => {
    const beaucoup = Array.from({ length: 12 }, (_, i) =>
      episode({ kind: 'coupure', wan_id: '4', at: `2026-09-11T20:${String(10 + i).padStart(2, '0')}:00Z`, peak_ms: null }))
    render(<EventLog log={journal(beaucoup)} windowLabel="Session" complet />)
    expect(document.querySelectorAll('tbody tr')).toHaveLength(12)
    expect(screen.queryByRole('button', { name: /Voir les/ })).not.toBeInTheDocument()
  })
})

describe('un lien désactivé par l’exploitant (C-20)', () => {
  it('se dit comme une décision, en gris, et sa durée se ferme au retour', () => {
    // Vu en service le 17/10 : SIM 4 désactivée à 13:51:23, revenue à 13:52:34.
    // Le journal écrivait « Coupure — 1 min » sur un rail rouge.
    const { container } = render(
      <EventLog
        log={journal([
          episode({ kind: 'retour', at: '2026-10-17T11:52:34Z', peak_ms: null,
                    detail: 'Connected to Bouygues' }),
          episode({ kind: 'désactivation', at: '2026-10-17T11:51:23Z', peak_ms: null,
                    detail: 'désactivé' }),
        ])}
        windowLabel="Depuis la mise sous tension"
      />,
    )
    expect(screen.getByText('Désactivé par l’exploitant')).toBeInTheDocument()
    expect(screen.queryByText('Coupure')).not.toBeInTheDocument()
    expect(screen.getByText(/1 min/)).toBeInTheDocument()
    const rails = [...container.querySelectorAll('tbody .rail')]
      .map((r) => (r as HTMLElement).style.getPropertyValue('--q'))
    expect(rails[1]).toBe(QUALITY_COLOR.inconnu)
  })
})
