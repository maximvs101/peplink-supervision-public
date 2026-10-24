import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DELAI_LECTURE_MS, fetchRouter, openLiveSocket, pairDevice } from './client'

/**
 * C-46 (F14 à F16) : `openLiveSocket` et `pairDevice` étaient moqués dans
 * tous les tests — leur propre logique (reprise, codes applicatifs, refus
 * d'identifiants) n'était éprouvée nulle part, et les mutants survivaient.
 */

class FausseSocket {
  static ouvertes: FausseSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: ((e: { code: number; reason: string }) => void) | null = null
  onerror: ((e: { target: FausseSocket }) => void) | null = null
  fermee = false
  constructor(public url: string) { FausseSocket.ouvertes.push(this) }
  close() { this.fermee = true }
}

beforeEach(() => {
  FausseSocket.ouvertes = []
  vi.stubGlobal('WebSocket', FausseSocket)
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('le flux temps réel', () => {
  it('reprend après un aléa, avec un recul croissant', () => {
    const statuts: boolean[] = []
    openLiveSocket('dome', () => {}, (c) => statuts.push(c))
    expect(FausseSocket.ouvertes).toHaveLength(1)
    expect(FausseSocket.ouvertes[0].url).toMatch(/\/ws\/routers\/dome$/)

    FausseSocket.ouvertes[0].onclose!({ code: 1006, reason: '' })
    expect(statuts).toEqual([false])
    vi.advanceTimersByTime(1999)
    expect(FausseSocket.ouvertes).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(FausseSocket.ouvertes).toHaveLength(2)

    // Deuxième, troisième échec : 2 s, puis 5 s.
    FausseSocket.ouvertes[1].onclose!({ code: 1006, reason: '' })
    vi.advanceTimersByTime(2000)
    FausseSocket.ouvertes[2].onclose!({ code: 1006, reason: '' })
    vi.advanceTimersByTime(4999)
    expect(FausseSocket.ouvertes).toHaveLength(3)
    vi.advanceTimersByTime(1)
    expect(FausseSocket.ouvertes).toHaveLength(4)
  })

  it('s’arrête sur un code applicatif et le remonte', () => {
    const fermetures: { code: number; reason: string }[] = []
    openLiveSocket('dome', () => {}, undefined, (f) => fermetures.push(f))
    FausseSocket.ouvertes[0].onclose!({ code: 4404, reason: 'Équipement retiré' })
    vi.advanceTimersByTime(60_000)
    expect(FausseSocket.ouvertes).toHaveLength(1)
    expect(fermetures).toEqual([{ code: 4404, reason: 'Équipement retiré' }])
  })

  it('ignore une trame illisible sans casser le flux', () => {
    const recus: unknown[] = []
    openLiveSocket('dome', (s) => recus.push(s))
    FausseSocket.ouvertes[0].onmessage!({ data: '{pas du json' })
    FausseSocket.ouvertes[0].onmessage!({ data: '{"router_id":"dome"}' })
    expect(recus).toEqual([{ router_id: 'dome' }])
  })

  it('ne reprend plus une fois fermé par l’appelant', () => {
    const statuts: boolean[] = []
    const fermer = openLiveSocket('dome', () => {}, (c) => statuts.push(c))
    fermer()
    FausseSocket.ouvertes[0].onclose?.({ code: 1006, reason: '' })
    vi.advanceTimersByTime(60_000)
    expect(FausseSocket.ouvertes).toHaveLength(1)
    expect(statuts).toEqual([])
  })
})

describe('l’appairage', () => {
  const repondre = (status: number, corps: unknown) =>
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(corps), { status })))

  it('marque un 401 comme un refus d’identifiants, avec le mot du serveur', async () => {
    repondre(401, { detail: 'login refusé' })
    const erreur = await pairDevice({ host: '192.0.2.1', username: 'a', password: 'b' }).catch((e) => e)
    expect(erreur.message).toBe('login refusé')
    expect(erreur.credentials).toBe(true)
  })

  it('ne confond pas une panne du routeur avec un refus', async () => {
    repondre(502, { detail: 'injoignable' })
    const erreur = await pairDevice({ host: '192.0.2.1', username: 'a', password: 'b' }).catch((e) => e)
    expect(erreur.credentials).toBe(false)
  })

  it('rend l’équipement appairé', async () => {
    repondre(200, { id: 'dome', status: 'en attente' })
    await expect(pairDevice({ host: '192.0.2.1', username: 'a', password: 'b' }))
      .resolves.toMatchObject({ id: 'dome' })
  })
})

describe('une lecture sans réponse', () => {
  it('abandonne au bout du délai, et le dit', async () => {
    // Le serveur accepte la connexion et ne répond plus : `fetch` ne rejette
    // jamais, et la relance manuelle restait « Lecture en cours… » pour
    // toujours, bouton désactivé.
    vi.stubGlobal('fetch', vi.fn((_url: string, init?: RequestInit) => new Promise((_ok, ko) => {
      init?.signal?.addEventListener('abort', () => ko(new DOMException('abandon', 'AbortError')))
    })))
    const issue = fetchRouter('mbx').then(() => 'répondu', (e: Error) => e.message)
    await vi.advanceTimersByTimeAsync(DELAI_LECTURE_MS - 1)
    let fini = false
    void issue.then(() => { fini = true })
    await vi.advanceTimersByTimeAsync(0)
    expect(fini).toBe(false)
    await vi.advanceTimersByTimeAsync(1)
    expect(await issue).toMatch(/pas de réponse du serveur en 20 s/)
  })

  it('laisse passer une réponse arrivée à temps', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ id: 'mbx' }), { status: 200 })))
    await expect(fetchRouter('mbx')).resolves.toEqual({ id: 'mbx' })
  })
})
