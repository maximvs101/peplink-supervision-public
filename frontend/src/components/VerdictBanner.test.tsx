import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { RadioSample, WanRadioStatus } from '../types'
import { VERDICT_COLOR } from '../lib/verdict'
import { VerdictBanner } from './VerdictBanner'

const T0 = Date.parse('2026-08-30T14:00:00Z')

function sample(wan: Partial<WanRadioStatus> = {}, cycle = 0): RadioSample {
  return {
    router_id: 'r', router_name: 'R', ts: new Date(T0 + cycle * 5_300).toISOString(),
    site_lat: null, site_lon: null, cell_changes: 0, disconnects: 0,
    wan: {
      kind: 'cellular', wan_id: '4', wan_name: 'SIM', connected: true,
      operator: 'Orange', operator_name: 'Orange', plmn: '208-01', rat: 'LTE-A', band: 'B3',
      earfcn: null, pci: null, cell_id: null, tac: null, enb_id: null, sector_id: null,
      rsrp: -88, rsrq: -11, sinr: 15, rssi: null, signal_bars: 4,
      bands: [], sim_slots: [], wifi: null, path_latency_ms: null, path_latency_recent_ms: null, path_latency_at: null, modem_temperature_c: null, modem_temperature_at: null, ip: null, priority: 1, tunnel: null,
      tx_kbps: null, rx_kbps: null, uptime_s: 1, status_message: null, disabled: false,
      roaming_status: null, modem_vendor: null, modem_model: null, modem_firmware: null,
      mtu: null, carrier_aggregation: null, network_mode: null, allowance: null,
      period_rx_mb: null, period_tx_mb: null,
      ...wan,
    } satisfies WanRadioStatus,
  }
}

/** Un CYCLE de scrutation : chaque appel rend une nouvelle liste, dont tous les
 *  relevés portent l'horodatage de ce cycle-là.
 *
 *  L'horodatage n'est pas décoratif : le bandeau compte les cycles et non les
 *  trames, parce que le serveur publie un relevé PAR WAN et par cycle. Sur onze
 *  liens, l'historique se remplissait onze fois plus vite que prévu et un unique
 *  cycle de retour suffisait à rassurer le bandeau. */
const links = (total: number, down: number, cycle = 0): RadioSample[] =>
  Array.from({ length: total }, (_, i) =>
    sample({ wan_id: String(i), connected: i >= down }, cycle),
  )

const colour = (container: HTMLElement) =>
  (container.querySelector('.mast-verdict') as HTMLElement).style.getPropertyValue('--v')

const detail = (container: HTMLElement) =>
  container.querySelector('.mast-verdict .detail')?.textContent ?? ''

afterEach(() => {
  vi.useRealTimers()
})

describe('bandeau de verdict', () => {
  it('accorde toujours le texte et la couleur', () => {
    // Défaut corrigé : la couleur venait du verdict lissé et le texte du verdict
    // instantané, ce qui affichait « Liaison OK » en rouge pendant le lissage.
    const { container } = render(
      <VerdictBanner samples={[sample()]} live status="connecté" ageSeconds={2} />,
    )
    expect(screen.getByText('Liaison OK')).toBeInTheDocument()
    expect(colour(container)).toBe(VERDICT_COLOR.ok)
  })

  it('affiche rouge et texte cohérents quand l’équipement est injoignable', () => {
    const { container } = render(
      <VerdictBanner samples={[sample()]} live status="injoignable" ageSeconds={90} />,
    )
    expect(screen.getByText('Équipement injoignable')).toBeInTheDocument()
    expect(colour(container)).toBe(VERDICT_COLOR.rompu)
  })

  it('marque la mesure comme périmée au-delà du seuil', () => {
    render(<VerdictBanner samples={[sample()]} live status="connecté" ageSeconds={300} />)
    expect(screen.getByText('300 s')).toBeInTheDocument()
    expect(screen.getByText('relevé périmé')).toBeInTheDocument()
    expect(screen.getByText('Mesures périmées')).toBeInTheDocument()
  })

  it('signale un flux interrompu', () => {
    render(<VerdictBanner samples={[sample()]} live={false} status="connecté" ageSeconds={2} />)
    expect(screen.getByText('flux interrompu')).toBeInTheDocument()
  })
})

describe('fidélité au détail des relevés', () => {
  it('suit le décompte des liens tombés, à verdict inchangé', () => {
    // Un lien déconnecté reste dans la liste avec `connected: false` : verdict,
    // âge et nombre de relevés sont identiques dans les deux cas. C'est
    // exactement ce que la clé de dépendance bricolée ratait — l'écran
    // continuait d'annoncer « 1 déconnecté » avec trois liens tombés.
    const { container, rerender } = render(
      <VerdictBanner samples={links(4, 1, 0)} live status="connecté" ageSeconds={2} />,
    )
    expect(detail(container)).toMatch(/1 déconnecté/)

    rerender(<VerdictBanner samples={links(4, 3, 1)} live status="connecté" ageSeconds={2} />)
    expect(detail(container)).toMatch(/3 déconnectés/)
  })
})

describe('hystérésis', () => {
  it('exige trois constats avant d’annoncer une dégradation', () => {
    // L'historique n'étant alimenté qu'aux changements de la clé bricolée, vingt
    // relevés sains suivis d'un dégradé faisaient basculer le bandeau sur-le-champ.
    const cycle = (down: number, n: number) => (
      <VerdictBanner samples={links(2, down, n)} live status="connecté" ageSeconds={2} />
    )
    const { container, rerender } = render(cycle(0, 0))
    for (let i = 1; i < 7; i += 1) rerender(cycle(0, i))
    expect(screen.getByText('Liaison OK')).toBeInTheDocument()

    rerender(cycle(1, 7))
    expect(screen.getByText('Liaison OK')).toBeInTheDocument()
    expect(colour(container)).toBe(VERDICT_COLOR.ok)

    rerender(cycle(1, 8))
    expect(screen.getByText('Liaison OK')).toBeInTheDocument()

    rerender(cycle(1, 9))
    expect(screen.getByText('Liaison dégradée')).toBeInTheDocument()
    expect(colour(container)).toBe(VERDICT_COLOR.degrade)
  })

  it('affiche une rupture sans attendre', () => {
    // Différer une mauvaise nouvelle serait exactement le défaut qu'on corrige.
    const { container, rerender } = render(
      <VerdictBanner samples={links(2, 0, 0)} live status="connecté" ageSeconds={2} />,
    )
    for (let i = 1; i < 7; i += 1) {
      rerender(<VerdictBanner samples={links(2, 0, i)} live status="connecté" ageSeconds={2} />)
    }
    rerender(<VerdictBanner samples={links(2, 2, 7)} live status="connecté" ageSeconds={2} />)
    expect(colour(container)).toBe(VERDICT_COLOR.rompu)
  })

  it('compte des cycles et non des trames, quel que soit le nombre de liens', () => {
    // Le serveur publie un relevé PAR WAN : sur onze liens, chaque cycle
    // provoquait onze rendus, `slice(-12)` ne couvrait plus qu'un seul cycle et
    // un unique cycle de retour faisait passer le bandeau de « dégradée » à
    // « Liaison OK ». La même constante ne voulait pas dire la même chose selon
    // le matériel : cinq cycles sur un Dome mono-lien, moins d'un sur un MBX.
    //
    // Un cycle qui arrive lien par lien : les onze relevés d'un cycle
    // n'avancent l'horodatage du DERNIER qu'à la toute fin.
    const trames = (down: number, n: number, arrives: number): RadioSample[] =>
      Array.from({ length: 11 }, (_, i) =>
        sample(
          { wan_id: String(i), connected: i >= down },
          // Les liens déjà rafraîchis portent le cycle n, les autres le n−1.
          i < arrives ? n : n - 1,
        ),
      )

    const degrade = (n: number, arrives: number) => (
      <VerdictBanner samples={trames(1, n, arrives)} live status="connecté" ageSeconds={2} />
    )
    const sain = (n: number, arrives: number) => (
      <VerdictBanner samples={trames(0, n, arrives)} live status="connecté" ageSeconds={2} />
    )

    const { rerender } = render(degrade(1, 11))
    for (let n = 2; n <= 8; n += 1) rerender(degrade(n, 11))
    expect(screen.getByText('Liaison dégradée')).toBeInTheDocument()

    // UN cycle de retour, trame par trame : rien ne doit basculer.
    for (let arrives = 1; arrives <= 11; arrives += 1) rerender(sain(9, arrives))
    expect(screen.getByText('Liaison dégradée')).toBeInTheDocument()

    // Il en faut cinq. Les trois suivants ne suffisent toujours pas.
    for (let n = 10; n <= 12; n += 1) rerender(sain(n, 11))
    expect(screen.getByText('Liaison dégradée')).toBeInTheDocument()

    rerender(sain(13, 11))
    expect(screen.getByText('Liaison OK')).toBeInTheDocument()
  })
})

/**
 * Ces deux cas éprouvaient une remise à zéro écrite en branche de rendu, qui
 * était MORTE : `Masthead` monte ce bandeau avec `key={routerId}`, donc React
 * le démonte et le remonte au changement d'équipement. Les tests rerendaient le
 * même composant avec un autre identifiant — ce que la production ne fait
 * jamais — et validaient donc un mécanisme qui ne s'exécute pas. Ils passent
 * désormais par la CLÉ, comme `Masthead`, et éprouvent ce qui protège
 * réellement.
 */
describe('changement d’équipement', () => {
  it('ne fait pas hériter le rouge du précédent', () => {
    // Constaté sur un vrai changement de route : après douze cycles où A restait
    // injoignable, B répondait sain et le bandeau restait rouge le temps des
    // cinq constats de désescalade, soit ~50 s sur un équipement sain.
    // Chaque itération rend une nouvelle liste : douze RELEVÉS, pas douze
    // rendus du même. C'est ce qui remplit l'historique de A.
    const down = (n: number) => (
      <VerdictBanner
        key="A"
        samples={links(2, 2, n)}
        live
        status="injoignable"
        ageSeconds={90}
      />
    )
    const { container, rerender } = render(down(0))
    for (let i = 1; i <= 12; i += 1) rerender(down(i))
    expect(colour(container)).toBe(VERDICT_COLOR.rompu)

    rerender(
      <VerdictBanner key="B" samples={links(2, 0, 0)} live status="connecté" ageSeconds={2} />,
    )
    expect(screen.getByText('Liaison OK')).toBeInTheDocument()
    expect(colour(container)).toBe(VERDICT_COLOR.ok)
  })

  it('remet le chronomètre à zéro', () => {
    vi.useFakeTimers()
    const { container, rerender } = render(
      <VerdictBanner key="A" samples={[sample()]} live status="injoignable" ageSeconds={90} />,
    )
    act(() => { vi.advanceTimersByTime(180_000) })
    expect(screen.getByText('3 min')).toBeInTheDocument()
    // « Observé depuis » : le chronomètre part du montage de la page, pas de
    // l'entrée dans l'état — « 1 min dans cet état » pour une panne de 2 h (C-42).
    expect(screen.getByText('observé depuis')).toBeInTheDocument()

    rerender(
      <VerdictBanner key="B" samples={[sample()]} live status="injoignable" ageSeconds={90} />,
    )
    act(() => { vi.advanceTimersByTime(5_000) })
    // Le cachet ne réapparaît qu'après 30 s passées sur CE nouvel équipement.
    expect(container.textContent).not.toMatch(/observé depuis/)
  })
})

describe('durée retenue', () => {
  it('emploie le même barème que les durées de session', () => {
    // « depuis 180 min » pour trois heures, « 2880 min » pour deux jours : le
    // formatage écrit à la main divergeait de tout le reste de l'interface.
    vi.useFakeTimers()
    render(<VerdictBanner samples={[sample()]} live status="injoignable" ageSeconds={90} />)
    act(() => { vi.advanceTimersByTime(3 * 3600 * 1000) })
    expect(screen.getByText('3 h 0 min')).toBeInTheDocument()
  })
})

describe('cause de la panne', () => {
  it('dit pourquoi l’équipement ne répond pas', () => {
    // Le serveur connaît la cause ; la taire envoie chercher un câble débranché
    // là où c'est un mot de passe qui a changé.
    render(
      <VerdictBanner
        samples={[sample()]}
        live
        status="injoignable"
        ageSeconds={90}
        error="connexion refusée vers 192.168.50.1:443"
      />,
    )
    expect(screen.getByText(/connexion refusée vers 192\.168\.50\.1:443/)).toBeInTheDocument()
  })

  it('ne ressort pas la cause sur une liaison qui va bien', () => {
    render(
      <VerdictBanner
        samples={[sample()]}
        live
        status="connecté"
        ageSeconds={2}
        error="connexion refusée vers 192.168.50.1:443"
      />,
    )
    expect(screen.queryByText(/connexion refusée/)).not.toBeInTheDocument()
  })
})

describe('routeur figé', () => {
  it('escalade quand les mesures périment, sans nouveau relevé', () => {
    // Un équipement qui se fige ne produit plus de cycle : l'effet du bandeau
    // dépend de `[cycle, status, raw.stale]`, et seul `stale` peut encore
    // bouger. Il ne fournit alors qu'UN constat, là où l'hystérésis d'aggravation
    // en demande trois — le titre pouvait rester « Liaison OK » sur un écran qui
    // ne décrit plus rien.
    const { container, rerender } = render(
      <VerdictBanner key="A" samples={links(4, 0, 0)} live status="connecté" ageSeconds={2}
        periodSeconds={5.3} />,
    )
    expect(screen.getByText('Liaison OK')).toBeInTheDocument()

    // Le routeur se fige : mêmes relevés, âge qui grandit. Un seul rendu suffit.
    rerender(
      <VerdictBanner key="A" samples={links(4, 0, 0)} live status="connecté" ageSeconds={90}
        periodSeconds={5.3} />,
    )
    expect(screen.getByText('Mesures périmées')).toBeInTheDocument()
    expect(colour(container)).toBe(VERDICT_COLOR.rompu)
  })
})

describe('un fait qui cesse', () => {
  it('rend la main dès que les relevés reviennent, sans figer le texte', () => {
    // Le bandeau gardait « Mesures périmées — dernier relevé il y a 46 s »
    // pendant les cinq constats de désescalade, alors que les relevés
    // arrivaient de nouveau toutes les trois secondes. Une mesure périmée
    // est un fait, pas une tendance : quand il cesse, il n'y a rien à retenir.
    const { container, rerender } = render(
      <VerdictBanner key="A" samples={links(4, 0, 0)} live status="connecté" ageSeconds={2}
        periodSeconds={5.3} />,
    )
    // Assez de cycles pour que l'hystérésis soit armée.
    for (let cycle = 1; cycle <= 6; cycle += 1) {
      rerender(
        <VerdictBanner key="A" samples={links(4, 0, cycle)} live status="connecté" ageSeconds={2}
          periodSeconds={5.3} />,
      )
    }
    rerender(
      <VerdictBanner key="A" samples={links(4, 0, 6)} live status="connecté" ageSeconds={46}
        periodSeconds={5.3} />,
    )
    expect(screen.getByText('Mesures périmées')).toBeInTheDocument()
    expect(detail(container)).toMatch(/46 s/)

    // Un relevé frais : le fait a cessé.
    rerender(
      <VerdictBanner key="A" samples={links(4, 0, 7)} live status="connecté" ageSeconds={1}
        periodSeconds={5.3} />,
    )
    expect(screen.getByText('Liaison OK')).toBeInTheDocument()
    expect(detail(container)).not.toMatch(/46 s/)
  })

  it('en fait autant quand l’équipement répond de nouveau', () => {
    const { rerender } = render(
      <VerdictBanner key="A" samples={links(4, 0, 0)} live status="injoignable" ageSeconds={2}
        periodSeconds={5.3} />,
    )
    for (let cycle = 1; cycle <= 6; cycle += 1) {
      rerender(
        <VerdictBanner key="A" samples={links(4, 0, cycle)} live status="injoignable" ageSeconds={2}
          periodSeconds={5.3} />,
      )
    }
    expect(screen.getByText(/injoignable/i)).toBeInTheDocument()
    rerender(
      <VerdictBanner key="A" samples={links(4, 0, 7)} live status="connecté" ageSeconds={1}
        periodSeconds={5.3} />,
    )
    expect(screen.getByText('Liaison OK')).toBeInTheDocument()
  })
})

describe('le verdict pour un lecteur d’écran', () => {
  it('annonce le CHANGEMENT de verdict, pas l’âge qui avance chaque seconde', () => {
    // Le titre ne change qu'aux transitions retenues par l'hystérésis : c'est
    // lui, et lui seul, qui doit être annoncé. L'âge du relevé et le détail
    // bougent à chaque cycle : en direct, ils noieraient l'annonce.
    const { container } = render(
      <VerdictBanner samples={[sample()]} live status="connecté" ageSeconds={2} />,
    )
    const titre = screen.getByRole('heading', { level: 1 })
    expect(titre).toHaveAttribute('aria-live', 'polite')
    expect(titre).toHaveAttribute('aria-atomic', 'true')
    const vivants = [...container.querySelectorAll('[aria-live], [role="status"], [role="alert"]')]
    expect(vivants).toEqual([titre])
  })
})
