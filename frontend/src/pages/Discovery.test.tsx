import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  fetchDevices: vi.fn(),
  fetchDiscovery: vi.fn(),
  fetchKnownDevices: vi.fn(),
  forgetDevice: vi.fn(),
  pairDevice: vi.fn(),
  reconnectKnownDevice: vi.fn(),
  unpairDevice: vi.fn(),
  httpStatus: () => null,
}))

import {
  fetchDevices, fetchDiscovery, fetchKnownDevices, reconnectKnownDevice, unpairDevice,
} from '../api/client'
import type { DeviceStatus, DiscoveredDevice } from '../types'
import { Discovery } from './Discovery'

const detecte = (over: Partial<DiscoveredDevice> = {}): DiscoveredDevice => ({
  host: 'http://10.10.1.1', mac: 'a8:c0:ea:00:00:01', firmware: '8.5.4 build 5808',
  certificate_name: null, api_reachable: true, evidence: ['préfixe MAC Peplink'],
  already_configured: true, ...over,
})

const supervise = (over: Partial<DeviceStatus> = {}): DeviceStatus => ({
  id: '0000-0000-0063', name: 'EQUIPEMENT-DEMO', host: 'http://10.10.1.1', serial: '0000-0000-0063',
  mode: 'router', source: 'paired', status: 'connecté', error: null, device: null, ...over,
})

const Outil = () => <Outlet context={{ health: null, routers: [] }} />

const monter = () =>
  render(
    <MemoryRouter initialEntries={['/decouverte']}>
      <Routes>
        <Route element={<Outil />}>
          <Route path="/decouverte" element={<Discovery />} />
          <Route path="/e/:routerId" element={<div>tableau de bord</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )

beforeEach(() => {
  vi.mocked(fetchKnownDevices).mockResolvedValue([])
  vi.mocked(reconnectKnownDevice).mockReset()
})

describe('un équipement détecté déjà en supervision', () => {
  it('mène à son tableau de bord au lieu d’une étiquette morte', async () => {
    // « Le routeur est indiqué déjà en supervision mais je n'ai pas accès au
    // tableau de bord » : l'étiquette ne menait nulle part, et le lien vivait
    // trois panneaux plus haut, sur le nom de l'équipement.
    vi.mocked(fetchDevices).mockResolvedValue([supervise()])
    vi.mocked(fetchDiscovery).mockResolvedValue([detecte()])
    monter()
    const lien = await screen.findByRole('link', { name: /déjà supervisé/ })
    expect(lien).toHaveAttribute('href', '/e/0000-0000-0063')
    lien.click()
    await waitFor(() => expect(screen.getByText('tableau de bord')).toBeInTheDocument())
  })

  it('reste une étiquette quand le serveur ne sait pas QUI le supervise', async () => {
    // `already_configured` vient de la découverte ; la liste supervisée peut
    // être illisible à cet instant. On ne fabrique pas un lien vers un
    // identifiant qu'on n'a pas.
    vi.mocked(fetchDevices).mockRejectedValue(new Error('hors sujet'))
    vi.mocked(fetchDiscovery).mockResolvedValue([detecte()])
    monter()
    expect(await screen.findByText('déjà supervisé')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /déjà supervisé/ })).toBeNull()
  })
})

describe('retirer un équipement', () => {
  it('demande confirmation, et un échec ne touche pas aux détectés', async () => {
    // Un clic sans retour arrière, dont l'effet immédiat est un 404 sur tous
    // les autres onglets. Et l'erreur d'un retrait manqué s'affichait À LA
    // PLACE de la liste des détectés, deux panneaux plus bas.
    vi.mocked(fetchDevices).mockResolvedValue([supervise()])
    vi.mocked(fetchDiscovery).mockResolvedValue([detecte({ host: 'http://10.10.1.2', already_configured: false })])
    vi.mocked(unpairDevice).mockRejectedValue(new Error('verrou'))
    monter()
    const retirer = await screen.findByRole('button', { name: 'Retirer' })
    retirer.click()
    expect(unpairDevice).not.toHaveBeenCalled()
    const confirmer = await screen.findByRole('button', { name: 'Confirmer le retrait' })
    confirmer.click()
    await waitFor(() => expect(unpairDevice).toHaveBeenCalledWith('0000-0000-0063'))
    await screen.findByRole('alert')
    expect(screen.getByRole('alert').textContent).toMatch(/Retrait de EQUIPEMENT-DEMO impossible : verrou/)
    // Les détectés sont toujours là.
    expect(screen.getByText(/10\.10\.1\.2/)).toBeInTheDocument()
  })
})

describe('reconnecter un équipement déjà vu', () => {
  it('ouvre le formulaire même si la mémoire écrit l’hôte autrement', async () => {
    // « Reconnecter » était montré sur `hoteCanonique(host)` mais ouvert sur
    // `setPairing(host)` brut : `10.10.1.1` retenu, `http://10.10.1.1`
    // détecté, et aucun formulaire ne s'ouvrait.
    vi.mocked(fetchDevices).mockResolvedValue([])
    vi.mocked(fetchDiscovery).mockResolvedValue([detecte({ host: 'http://10.10.1.1', already_configured: false })])
    vi.mocked(fetchKnownDevices).mockResolvedValue([{
      serial: '0000-0000-0063', device_id: '0000-0000-0063', name: 'EQUIPEMENT-DEMO', host: '10.10.1.1',
      model: 'MAX HD2', firmware: '8.5.4', first_seen: '2026-09-19T10:00:00Z', last_seen: '2026-09-19T12:00:00Z',
      credentials_stored: false,
    }])
    monter()
    const reconnecter = await screen.findByRole('button', { name: 'Reconnecter' })
    reconnecter.click()
    await screen.findByRole('button', { name: 'Fermer' })
  })
})

describe('reconnecter avec les identifiants retenus', () => {
  const connu = {
    serial: '0000-0000-0063', device_id: '0000-0000-0063', name: 'EQUIPEMENT-DEMO', host: '10.10.1.1',
    model: 'MAX HD2', firmware: '8.5.4', first_seen: '2026-09-19T10:00:00Z',
    last_seen: '2026-09-19T12:00:00Z', credentials_stored: true,
  }

  it('reconnecte sans formulaire, et mène au tableau de bord', async () => {
    vi.mocked(fetchDevices).mockResolvedValue([])
    vi.mocked(fetchDiscovery).mockResolvedValue([])
    vi.mocked(fetchKnownDevices).mockResolvedValue([connu])
    vi.mocked(reconnectKnownDevice).mockResolvedValue(supervise())
    monter()
    // Même absent de la recherche : le trousseau suffit pour tenter.
    ;(await screen.findByRole('button', { name: 'Reconnecter' })).click()
    await waitFor(() => expect(screen.getByText('tableau de bord')).toBeInTheDocument())
    expect(reconnectKnownDevice).toHaveBeenCalledTimes(1)
    expect(reconnectKnownDevice).toHaveBeenCalledWith('0000-0000-0063')
  })

  it('dit un refus, et propose alors de ressaisir', async () => {
    vi.mocked(fetchDevices).mockResolvedValue([])
    vi.mocked(fetchDiscovery).mockResolvedValue([detecte({ already_configured: false })])
    vi.mocked(fetchKnownDevices).mockResolvedValue([connu])
    vi.mocked(reconnectKnownDevice).mockRejectedValue(
      Object.assign(new Error('Identifiants refusés.'), { credentials: true, status: 401 }),
    )
    monter()
    ;(await screen.findByRole('button', { name: 'Reconnecter' })).click()
    expect(await screen.findByText(/Identifiants refusés/)).toBeInTheDocument()
    // Le formulaire s'ouvre pour corriger : il remplacera l'entrée retenue.
    await screen.findByRole('button', { name: 'Fermer' })
    expect(reconnectKnownDevice).toHaveBeenCalledTimes(1)
  })
})

describe('un équipement déjà vu et déjà supervisé (A-11)', () => {
  it('ne propose pas de le reconnecter', async () => {
    // Vu le 17/10 : le HD2 reconnecté seul au lancement restait « Reconnecter »
    // dans « Déjà vus » — la condition testait « détecté » avant « supervisé ».
    vi.mocked(fetchDevices).mockResolvedValue([supervise()])
    vi.mocked(fetchDiscovery).mockResolvedValue([detecte()])
    vi.mocked(fetchKnownDevices).mockResolvedValue([{
      serial: '0000-0000-0063', device_id: '0000-0000-0063', name: 'HD2-ESSAI', host: 'http://10.10.1.1',
      model: 'MAX HD2', firmware: '8.5.4', first_seen: '2026-09-19T10:00:00Z',
      last_seen: '2026-10-17T12:00:00Z', credentials_stored: true,
    }])
    monter()
    await screen.findByText('déjà supervisé', { selector: '.sub-note' })
    expect(screen.queryByRole('button', { name: 'Reconnecter' })).toBeNull()
  })
})

describe('avant la première lecture de la flotte', () => {
  it('n’affirme ni « aucun équipement » ni « mettre en supervision »', async () => {
    // `supervised` démarrait à `[]` : le temps de la première réponse, l'écran
    // affirmait « Aucun équipement en supervision » et, si la recherche
    // répondait avant la liste, proposait d'appairer un équipement que le
    // serveur supervisait déjà.
    let rendre: ((l: DeviceStatus[]) => void) | undefined
    vi.mocked(fetchDevices).mockImplementation(() => new Promise((r) => { rendre = r }))
    vi.mocked(fetchDiscovery).mockResolvedValue([detecte({ already_configured: false })])
    monter()
    expect(await screen.findByText('vérification…')).toBeInTheDocument()
    expect(screen.queryByText(/Aucun équipement en supervision/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Mettre en supervision' })).toBeNull()
    expect(screen.getByRole('status').textContent).toMatch(/Lecture des équipements supervisés/)

    // La réponse arrive : l'équipement détecté est déjà supervisé.
    rendre!([supervise()])
    expect(await screen.findByRole('link', { name: /déjà supervisé/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Mettre en supervision' })).toBeNull()
  })
})
