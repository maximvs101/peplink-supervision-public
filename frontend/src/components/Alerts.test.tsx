import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import type { Alert } from '../types'
import { Alerts } from './Alerts'

const T0 = '2026-09-13T14:00:00Z'

const alerte = (over: Partial<Alert> = {}): Alert => ({
  kind: 'hors du bond', wan_id: '4', since: T0, until: null,
  detail: 'WAN_DISABLED', sustained_s: 60,
  ...over,
})

describe('le panneau n’existe pas quand il n’a rien à dire', () => {
  it('ne rend rien du tout sur une liaison saine', () => {
    // Un cadre vide intitulé « Alertes » est un cadre qu'on cesse de regarder,
    // et il emporte avec lui les fois où il aurait compté. C'est le papier
    // peint que tout cet outil refuse.
    const { container } = render(<Alerts alerts={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('ce qu’une alerte dit', () => {
  it('nomme le lien, et donne le mot du ROUTEUR comme cause', () => {
    render(<Alerts alerts={[alerte()]} />)
    expect(screen.getByText(/Lien 4 hors du bond/)).toBeInTheDocument()
    // La cause vient du routeur, pas d'une déduction de l'outil.
    expect(screen.getByText('WAN_DISABLED')).toBeInTheDocument()
  })

  it('publie le SEUIL de durée qui l’a fait parler', () => {
    // Sans lui, « lien 4 hors du bond » ne dit pas si l'outil a attendu une
    // seconde ou une minute avant de le croire — et c'est cette attente qui
    // fait la différence entre un fait et un clignotement.
    render(<Alerts alerts={[alerte()]} />)
    expect(screen.getByText(/signalée après 1 min de la même condition/)).toBeInTheDocument()
  })

  it('distingue ce qui dure de ce qui est revenu', () => {
    render(
      <Alerts
        alerts={[
          alerte({ until: null }),
          alerte({ wan_id: '5', until: '2026-09-13T14:03:00Z' }),
        ]}
      />,
    )
    // L'en-tête compte les alertes EN COURS, et dit les autres à côté.
    expect(screen.getByText('1 alerte')).toBeInTheDocument()
    expect(screen.getByText(/1 récemment résolue/)).toBeInTheDocument()
    // Une alerte close porte ses deux bornes et sa durée.
    expect(screen.getByText(/· 3 min$/)).toBeInTheDocument()
  })

  it('titre autrement quand tout est déjà rentré dans l’ordre', () => {
    render(<Alerts alerts={[alerte({ until: '2026-09-13T14:02:00Z' })]} />)
    expect(screen.getByText('Alertes récentes')).toBeInTheDocument()
  })
})

describe('les trois genres', () => {
  it('dit ce que « un seul lien » coûte, et qu’elle se tait d’ordinaire', () => {
    render(
      <Alerts
        alerts={[alerte({ kind: 'liaison sur un seul lien', wan_id: null, detail: 'lien 4 seul, sur 5 liens', sustained_s: 15 })]}
      />,
    )
    expect(screen.getByText(/ne tient plus que sur un lien/)).toBeInTheDocument()
    expect(screen.getByText(/l’agrégation ne protège plus de rien/i)).toBeInTheDocument()
    expect(screen.getByText('lien 4 seul, sur 5 liens')).toBeInTheDocument()
  })

  it('dit ce que l’interruption de supervision a coûté', () => {
    render(
      <Alerts
        alerts={[alerte({
          kind: 'supervision interrompue', wan_id: null,
          detail: null,
          until: '2026-09-13T14:13:00Z', sustained_s: 300,
        })]}
      />,
    )
    expect(screen.getByText(/La supervision s’est interrompue/)).toBeInTheDocument()
    // Ce qui s'y est passé n'est ni mesuré ni récupérable : il faut le DIRE.
    expect(screen.getByText(/sans témoin/)).toBeInTheDocument()
  })

  it('affiche un genre inconnu tel quel plutôt que de le taire', () => {
    // Le jour où une quatrième règle naîtra côté serveur, elle ne doit pas
    // disparaître en silence faute de traduction.
    render(<Alerts alerts={[alerte({ kind: 'quelque chose de neuf', detail: null })]} />)
    expect(screen.getByText('quelque chose de neuf')).toBeInTheDocument()
  })
})

describe('les connexions admin refusées', () => {
  it('ont un titre et disent ce qu’elles risquent', () => {
    render(<Alerts alerts={[alerte({
      kind: 'connexions admin refusées', wan_id: null,
      detail: "3 refus en 5 min — cinq suspendent l'accès admin pendant 5 min",
    })]} />)
    expect(screen.getByText('Connexions admin refusées')).toBeInTheDocument()
    expect(document.body.textContent).toMatch(/suspendent l’accès admin/)
  })
})

describe('l’horloge du routeur décalée (R-3.9)', () => {
  it('a un titre et dit ce qu’elle coûte, et comment y remédier', () => {
    render(<Alerts alerts={[alerte({
      kind: 'horloge du routeur décalée', wan_id: null,
      detail: '12 min en retard sur ce poste — son historique à 10 s est écarté…',
    })]} />)
    expect(screen.getByText('Horloges du routeur et du poste en désaccord')).toBeInTheDocument()
    expect(document.body.textContent).toMatch(/NTP/)
  })
})

describe('la note des alertes compte juste (C-42)', () => {
  it('ne dit plus « trois règles » quand l’écran en traduit six', () => {
    const { container } = render(<Alerts alerts={[alerte({ kind: 'hors du bond', wan_id: '4' })]} />)
    expect(container.textContent).not.toMatch(/Trois règles/)
    expect(container.textContent).toMatch(/Six règles/)
  })
})

describe('masquer les alertes résolues', () => {
  const T1 = '2026-09-13T14:05:00Z'
  const resolue = (over: Partial<Alert> = {}) => alerte({ until: T1, ...over })

  beforeEach(() => { try { sessionStorage.clear() } catch { /* stockage refusé */ } })

  it('fait disparaître le panneau « Alertes récentes »', () => {
    const { container } = render(<Alerts alerts={[resolue()]} />)
    expect(screen.getByText('Alertes récentes')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Masquer/ }))
    expect(container).toBeEmptyDOMElement()
  })

  it('ne propose jamais de masquer une alerte EN COURS', () => {
    render(<Alerts alerts={[alerte()]} />)
    expect(screen.queryByRole('button', { name: /Masquer/ })).toBeNull()
  })

  it('ne retire que les résolues quand une alerte est en cours', () => {
    render(<Alerts alerts={[alerte(), resolue({ kind: 'supervision interrompue', wan_id: null })]} />)
    fireEvent.click(screen.getByRole('button', { name: /Masquer/ }))
    expect(screen.getByText(/Lien 4 hors du bond/)).toBeInTheDocument()
    expect(screen.queryByText(/La supervision s’est interrompue/)).toBeNull()
    expect(screen.queryByText(/récemment résolue/)).toBeNull()
  })

  it('revient pour une alerte nouvelle, et tient au remontage', () => {
    const premiere = resolue()
    const { unmount } = render(<Alerts alerts={[premiere]} />)
    fireEvent.click(screen.getByRole('button', { name: /Masquer/ }))
    unmount()
    // Retour depuis le détail d'un lien : l'écran est remonté, le choix tient.
    const second = render(<Alerts alerts={[premiere]} />)
    expect(second.container).toBeEmptyDOMElement()
    second.unmount()
    // Une autre alerte résolue n'a pas été vue : elle se montre.
    render(<Alerts alerts={[premiere, resolue({ since: '2026-09-13T15:00:00Z', until: '2026-09-13T15:02:00Z' })]} />)
    expect(screen.getByText('Alertes récentes')).toBeInTheDocument()
    expect(screen.getAllByText(/Lien 4 hors du bond/)).toHaveLength(1)
  })
})
