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
    expect(screen.getByText(/seuil 1 min/)).toBeInTheDocument()
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
    expect(screen.getByText(/\+ 1 résolue/)).toBeInTheDocument()
    // Une alerte close porte ses deux bornes et sa durée.
    expect(screen.getByText(/· 3 min$/)).toBeInTheDocument()
  })

  it('titre autrement quand tout est déjà rentré dans l’ordre', () => {
    render(<Alerts alerts={[alerte({ until: '2026-09-13T14:02:00Z' })]} />)
    expect(screen.getByText('Alertes récentes')).toBeInTheDocument()
  })
})

describe('les trois genres', () => {
  it('dit « un seul lien » par un titre et le fait, sans commentaire', () => {
    const { container } = render(
      <Alerts
        alerts={[alerte({ kind: 'liaison sur un seul lien', wan_id: null, detail: 'lien 4 · 1 sur 5', sustained_s: 15 })]}
      />,
    )
    expect(screen.getByText(/Un seul lien dans le bond/)).toBeInTheDocument()
    expect(screen.getByText('lien 4 · 1 sur 5')).toBeInTheDocument()
    // Consigne de l'exploitant : pas d'interprétation (« la prochaine perte
    // est une coupure », « ne protège plus de rien »).
    expect(container.textContent).not.toMatch(/protège|prochaine perte/)
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
    expect(screen.getByText(/Supervision interrompue/)).toBeInTheDocument()
    // Le fait : de quand à quand, combien — sans narration.
    expect(screen.getByText(/→/)).toBeInTheDocument()
    expect(screen.queryByText(/sans témoin/)).toBeNull()
  })

  it('affiche un genre inconnu tel quel plutôt que de le taire', () => {
    // Le jour où une quatrième règle naîtra côté serveur, elle ne doit pas
    // disparaître en silence faute de traduction.
    render(<Alerts alerts={[alerte({ kind: 'quelque chose de neuf', detail: null })]} />)
    expect(screen.getByText('quelque chose de neuf')).toBeInTheDocument()
  })
})

describe('les connexions admin refusées', () => {
  it('ont un titre et le compte du serveur', () => {
    render(<Alerts alerts={[alerte({
      kind: 'connexions admin refusées', wan_id: null, detail: '3 en 5 min',
    })]} />)
    expect(screen.getByText('Connexions admin refusées')).toBeInTheDocument()
    expect(screen.getByText('3 en 5 min')).toBeInTheDocument()
  })
})

describe('l’horloge du routeur décalée (R-3.9)', () => {
  it('a un titre et l’écart mesuré, sans conseil', () => {
    render(<Alerts alerts={[alerte({
      kind: 'horloge du routeur décalée', wan_id: null,
      detail: 'routeur 12 min en retard sur ce poste',
    })]} />)
    expect(screen.getByText('Écart d’horloge routeur / poste')).toBeInTheDocument()
    expect(screen.getByText('routeur 12 min en retard sur ce poste')).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/NTP|vérifier/i)
  })
})

describe('le panneau des alertes', () => {
  it('ne porte plus de note sur ses propres règles', () => {
    const { container } = render(<Alerts alerts={[alerte({ kind: 'hors du bond', wan_id: '4' })]} />)
    expect(container.textContent).not.toMatch(/règles/)
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
    expect(screen.queryByText(/Supervision interrompue/)).toBeNull()
    expect(screen.queryByText(/résolue/)).toBeNull()
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
