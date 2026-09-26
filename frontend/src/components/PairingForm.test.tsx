import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ pairDevice: vi.fn() }))

import { pairDevice } from '../api/client'
import { PairingForm } from './PairingForm'

const saisir = (label: string, valeur: string) => {
  const champ = screen.getByText(label).parentElement?.querySelector('input') as HTMLInputElement
  fireEvent.change(champ, { target: { value: valeur } })
}

const bouton = () => screen.getByRole('button', { name: /Mettre en supervision/ })
const cliquer = (nom: RegExp) => fireEvent.click(screen.getByRole('button', { name: nom }))

beforeEach(() => {
  // Sans cela, `mock.calls[0]` désigne l'appel du test PRÉCÉDENT : l'assertion
  // sur ce qui a été envoyé porterait sur un autre mode d'appairage.
  vi.clearAllMocks()
  vi.mocked(pairDevice).mockResolvedValue({
    id: 'mbx', name: 'MBX', host: '192.0.2.1', serial: null, mode: 'router',
    source: 'paired', status: 'connecté', error: null, device: null,
  })
})

describe('appairage par compte admin', () => {
  it('envoie l’identifiant et le mot de passe', () => {
    render(<PairingForm host="192.0.2.1" onPaired={() => {}} onCancel={() => {}} />)
    expect(bouton()).toBeDisabled()

    saisir('Mot de passe', 'secret')
    expect(bouton()).toBeEnabled()
    fireEvent.click(bouton())

    expect(pairDevice).toHaveBeenCalledWith({
      host: '192.0.2.1', username: 'admin', password: 'secret', name: undefined,
      verify_ssl: false,
    })
  })
})

/**
 * Le mode client API était INATTEIGNABLE.
 *
 * C'est pourtant le seul qui n'expose pas le compte admin de l'équipement : il
 * se crée depuis l'administration web, en lecture seule, et se révoque sans
 * changer de mot de passe. Le backend le gère depuis le début — trois formes
 * d'envoi vérifiées sur matériel — mais le formulaire envoyait `username` et
 * `password`, c'est-à-dire « admin » et une chaîne vide, et son bouton restait
 * désactivé tant que le mot de passe admin était vide : ce qu'il restait par
 * construction dans ce mode.
 */
describe('appairage par client API', () => {
  it('envoie clientId et clientSecret, et jamais le compte admin', () => {
    render(<PairingForm host="192.0.2.1" onPaired={() => {}} onCancel={() => {}} />)
    cliquer(/Client API/)

    saisir('clientId', 'lecture-seule')
    saisir('clientSecret', 'jeton')
    fireEvent.click(bouton())

    expect(pairDevice).toHaveBeenCalledWith({
      host: '192.0.2.1', client_id: 'lecture-seule', client_secret: 'jeton', name: undefined,
      verify_ssl: false,
    })
    const envoye = vi.mocked(pairDevice).mock.calls[0][0] as unknown as Record<string, unknown>
    expect(envoye.username).toBeUndefined()
    expect(envoye.password).toBeUndefined()
  })

  it('n’autorise la tentative qu’une fois les DEUX champs remplis', () => {
    // Une seule tentative : cinq échecs suspendent l'accès à l'équipement
    // pendant cinq minutes, y compris depuis son interface web.
    render(<PairingForm host="192.0.2.1" onPaired={() => {}} onCancel={() => {}} />)
    cliquer(/Client API/)
    expect(bouton()).toBeDisabled()

    saisir('clientId', 'lecture-seule')
    expect(bouton()).toBeDisabled()

    saisir('clientSecret', 'jeton')
    expect(bouton()).toBeEnabled()
  })

  it('ne réclame pas le mot de passe admin pour s’activer', () => {
    // La condition d'activation portait sur `password`, jamais rempli dans ce
    // mode : le bouton était inerte quoi qu'on saisisse.
    render(<PairingForm host="192.0.2.1" onPaired={() => {}} onCancel={() => {}} />)
    cliquer(/Client API/)
    expect(screen.queryByText('Mot de passe')).not.toBeInTheDocument()

    saisir('clientId', 'a')
    saisir('clientSecret', 'b')
    expect(bouton()).toBeEnabled()
  })
})

describe('ce que le formulaire ne doit pas laisser faire', () => {
  it('n’envoie qu’une tentative, même sur deux soumissions rapides', async () => {
    // Le bouton est désactivé pendant l'envoi, mais la touche Entrée dans un
    // champ soumet le formulaire sans passer par lui. Or CINQ tentatives ratées
    // suspendent l'accès à l'équipement pendant cinq minutes — c'est la règle
    // dont tout ce module découle, et un double envoi en consommait deux d'un
    // coup.
    vi.mocked(pairDevice).mockReturnValue(new Promise(() => {}) as never)
    const { container } = render(
      <PairingForm host="192.0.2.1" onPaired={() => {}} onCancel={() => {}} />,
    )
    saisir('Mot de passe', 'secret')

    const form = container.querySelector('form')!
    fireEvent.submit(form)
    fireEvent.submit(form)

    expect(pairDevice).toHaveBeenCalledTimes(1)
  })

  it('purge le secret du mode qu’on abandonne', async () => {
    // Les deux secrets vivaient dans deux états jamais vidés : un mot de passe
    // administrateur saisi puis abandonné restait en mémoire du navigateur pour
    // toute la vie de la page. Il n'était plus envoyé, mais n'avait plus aucune
    // raison d'exister.
    const { container } = render(
      <PairingForm host="192.0.2.1" onPaired={() => {}} onCancel={() => {}} />,
    )
    saisir('Mot de passe', 'secret-admin')

    cliquer(/Client API/)
    cliquer(/Compte admin/)

    const champ = [...container.querySelectorAll('label')]
      .find((l) => l.textContent?.includes('Mot de passe'))!
      .querySelector('input')!
    expect(champ.value).toBe('')
  })
})

describe('le client API reçoit les mêmes garde-fous que le compte admin (C-43)', () => {
  it('dit qu’un hôte http:// recevra le secret en clair', () => {
    render(<PairingForm host="http://10.0.0.1" onPaired={() => {}} onCancel={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /Client API/ }))
    expect(screen.getByText(/en clair/)).toBeInTheDocument()
  })

  it('offre la vérification du certificat sur un hôte HTTPS', () => {
    render(<PairingForm host="192.0.2.1" onPaired={() => {}} onCancel={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /Client API/ }))
    expect(screen.getByRole('checkbox', { name: /Vérifier le certificat/ })).toBeInTheDocument()
  })
})
