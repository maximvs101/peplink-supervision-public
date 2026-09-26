/** Accès au backend : REST pour les listes/historiques, WebSocket pour le live. */

import type {
  DeviceStatus,
  DiscoveredDevice,
  Health,
  HistorySeries,
  RadioSample,
  PairRequest,
  RouterSummary,
  KnownDevice,
  Session,
  SessionSummary,
  Timeline,
  EventLog,
} from '../types'

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) {
    const error = new Error(`${response.status} ${response.statusText} sur ${url}`)
    ;(error as Error & { status?: number }).status = response.status
    throw error
  }
  return (await response.json()) as T
}

/**
 * Code HTTP d'une erreur levée par `getJson`, quand elle en porte un.
 *
 * Toutes les erreurs de rafraîchissement ne se valent pas : un 404 sur un
 * équipement veut dire « retiré de la supervision, il ne reviendra pas », là où
 * une coupure réseau veut dire « redemande dans dix secondes ». Mesuré : un
 * `DELETE` depuis un autre onglet laissait l'écran figé sur l'équipement retiré,
 * parce que le 404 était avalé avec tout le reste. Renvoie `null` pour une
 * erreur qui n'est pas venue du serveur (réseau coupé, requête interrompue).
 */
export const httpStatus = (error: unknown): number | null =>
  error instanceof Error ? ((error as Error & { status?: number }).status ?? null) : null

export const fetchHealth = () => getJson<Health>('/api/health')

export const fetchRouters = () => getJson<RouterSummary[]>('/api/routers')

/** Recherche d'équipements sur le réseau. N'envoie aucun identifiant. */
export const fetchDiscovery = () => getJson<DiscoveredDevice[]>('/api/discover')

export const fetchDevices = () => getJson<DeviceStatus[]>('/api/devices')

/**
 * Met un équipement en supervision. **Une seule tentative de connexion.**
 *
 * Un refus renvoie 401 (identifiants) ou 502 (injoignable) : la distinction dit
 * à l'utilisateur quoi corriger. Ne jamais réessayer automatiquement — cinq
 * échecs suspendent l'accès à l'équipement pendant cinq minutes.
 */
export async function pairDevice(body: PairRequest): Promise<DeviceStatus> {
  const response = await fetch('/api/devices/pair', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => null)
    const error = new Error(detail?.detail ?? `Échec de l'appairage (${response.status})`)
    ;(error as Error & { credentials?: boolean }).credentials = response.status === 401
    throw error
  }
  return (await response.json()) as DeviceStatus
}

/**
 * Autorise UNE nouvelle tentative de connexion sur un équipement verrouillé.
 *
 * C'est l'action humaine explicite sans laquelle rien ne retente : le serveur
 * écarte entièrement un équipement dont les identifiants ont été refusés, parce
 * que cinq tentatives ratées suspendent l'accès admin cinq minutes — et il en
 * consommait une par cycle, soit un verrouillage en vingt-six secondes qui
 * atteignait aussi l'exploitant essayant d'ouvrir l'interface web du routeur.
 *
 * La route existait depuis le début et n'avait AUCUN appelant : un équipement
 * verrouillé l'était donc jusqu'au redémarrage du serveur, sans aucun bouton.
 *
 * Relancer sans avoir corrigé le mot de passe reverrouille l'équipement au cycle
 * suivant, en une seule tentative : c'est voulu.
 */
export async function reauthDevice(deviceId: string): Promise<void> {
  const response = await fetch(`/api/devices/${encodeURIComponent(deviceId)}/reauth`, {
    method: 'POST',
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => null)
    throw new Error(detail?.detail ?? `Nouvelle tentative impossible (${response.status})`)
  }
}

export async function unpairDevice(deviceId: string): Promise<void> {
  const response = await fetch(`/api/devices/${encodeURIComponent(deviceId)}`, {
    method: 'DELETE',
  })
  if (!response.ok) throw new Error(`Retrait impossible (${response.status})`)
}

export const fetchRouter = (routerId: string) =>
  getJson<RouterSummary>(`/api/routers/${encodeURIComponent(routerId)}`)

/** Équipements déjà appairés depuis cette machine. Le mot de passe n'y est pas :
 *  tant qu'aucun coffre chiffré n'existe, le garder en clair serait pire que de
 *  le redemander. */
export const fetchKnownDevices = () =>
  getJson<KnownDevice[]>('/api/devices/known')

/** Ce que chaque lien a fait pendant la fenêtre, en tranches de temps égales.
 *
 *  L'agrégation est faite par SQLite et le nombre de tranches est fixe : la
 *  réponse pèse le même poids pour une demi-heure que pour une session de
 *  vingt-quatre heures. */
export const fetchTimeline = (
  routerId: string,
  window: { minutes?: number; since?: string; until?: string },
) => {
  const p = new URLSearchParams()
  if (window.since) p.set('since', window.since)
  if (window.until) p.set('until', window.until)
  if (window.minutes !== undefined) p.set('minutes', String(window.minutes))
  return getJson<Timeline>(
    `/api/routers/${encodeURIComponent(routerId)}/timeline?${p.toString()}`,
  )
}

/** Le journal d'une fenêtre : ce qui s'y est passé, daté. */
export const fetchEvents = (
  routerId: string,
  window: { minutes?: number; since?: string; until?: string },
) => {
  const p = new URLSearchParams()
  if (window.since) p.set('since', window.since)
  if (window.until) p.set('until', window.until)
  if (window.minutes !== undefined) p.set('minutes', String(window.minutes))
  return getJson<EventLog>(`/api/routers/${encodeURIComponent(routerId)}/events?${p.toString()}`)
}

export const fetchSessionSummary = (sessionId: number) =>
  getJson<SessionSummary>(`/api/sessions/${sessionId}/summary`)

export const fetchSessions = (routerId: string) =>
  getJson<Session[]>(`/api/routers/${encodeURIComponent(routerId)}/sessions`)

export const fetchHistory = (
  routerId: string,
  wanId: string,
  metric: string,
  minutes = 30,
  /** Fenêtre ABSOLUE, prioritaire sur `minutes`. C'est la seule façon de relire
   *  une session passée : une fenêtre glissante ne sait regarder que la fin. */
  window?: { since: string; until: string },
  /** Pour un TAUX : la règle de publication de l'écran — le nombre de paquets
   *  en deçà duquel un sens n'est pas publié. Le serveur l'applique AVANT
   *  d'alléger la série : sinon l'enveloppe élisait le 33,33 % sur 3 paquets,
   *  que l'écran jetait ensuite, et le 4,7 % réel sur 300 avait disparu en
   *  amont sans mention. La règle reste ici ; le serveur ne fait que l'exécuter. */
  minPackets?: number,
) =>
  getJson<HistorySeries>(
    `/api/routers/${encodeURIComponent(routerId)}/history` +
      `?wan=${encodeURIComponent(wanId)}&metric=${encodeURIComponent(metric)}` +
      (window
        ? `&since=${encodeURIComponent(window.since)}&until=${encodeURIComponent(window.until)}`
        : `&minutes=${minutes}`) +
      (minPackets === undefined ? '' : `&min_packets=${minPackets}`),
  )

/** Fermeture DÉFINITIVE du flux, décidée par le serveur. */
export interface LiveClosure {
  /** Code applicatif (4000-4999). 4404 : l'équipement n'est plus supervisé. */
  code: number
  /** Raison en clair, telle que le serveur la formule. Peut être vide. */
  reason: string
}

/**
 * Ouvre le flux temps réel d'un routeur. Retourne une fonction de fermeture.
 *
 * La reconnexion est automatique tant que la coupure ressemble à un aléa. Elle
 * cesse dès que le serveur ferme avec un code applicatif : il a alors une raison
 * définitive, et `onClosed` la remonte à l'interface.
 */
/**
 * Nomme une session d'alimentation, ou retire son nom.
 *
 * La route existait depuis le début et n'avait AUCUN appelant : nommer une
 * session — présenté comme ce qui rend deux stationnements comparables, « le
 * parking de la mairie » se retrouvant là où un horodatage ne se retrouve pas —
 * était inatteignable depuis l'interface.
 *
 * Restreinte à la machine locale côté serveur, comme tout ce qui écrit.
 */
export async function nameSession(sessionId: number, label: string | null): Promise<void> {
  const response = await fetch(`/api/sessions/${sessionId}/label`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    // Une chaîne vide n'est pas un nom : elle RETIRE le nom, et le contrat du
    // serveur l'exprime par `null`.
    body: JSON.stringify({ label: label && label.trim() ? label.trim() : null }),
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => null)
    throw new Error(detail?.detail ?? `Nom refusé (${response.status})`)
  }
}

export function openLiveSocket(
  routerId: string,
  onSample: (sample: RadioSample) => void,
  onStatus?: (connected: boolean) => void,
  onClosed?: (closure: LiveClosure) => void,
): () => void {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const url = `${scheme}://${window.location.host}/ws/routers/${encodeURIComponent(routerId)}`

  let socket: WebSocket | null = null
  let retryTimer: number | undefined
  let closed = false
  /** Rangs de reprise, en millisecondes. Le délai était FIXE à deux secondes :
   *  serveur arrêté une heure, le navigateur rouvrait mille huit cents fois. Le
   *  recul plafonne à trente secondes — au-delà, une supervision qui met une
   *  demi-minute à se rebrancher n'est plus une supervision. */
  const RECULS = [2000, 2000, 5000, 10000, 30000]
  let tentative = 0

  const connect = () => {
    if (closed) return
    socket = new WebSocket(url)

    socket.onopen = () => {
      // La reprise a réussi : le prochain incident repart du délai le plus court.
      tentative = 0
      onStatus?.(true)
    }
    socket.onmessage = (event) => {
      // Le `try` n'enveloppe QUE la lecture. Il couvrait aussi l'appel, si bien
      // qu'une trame parfaitement JSON mais qui n'est pas un relevé — un
      // `{"stat":"fail"}` — traversait l'analyse, faisait échouer le
      // consommateur, et l'exception du consommateur était avalée elle aussi :
      // un flux entièrement corrompu laissait l'écran sur « première mesure en
      // cours » sans une trace nulle part. Prouvé par sonde.
      let trame: RadioSample
      try {
        trame = JSON.parse(event.data) as RadioSample
      } catch {
        return // Trame illisible : on ignore plutôt que de casser le flux.
      }
      onSample(trame)
    }
    socket.onclose = (event) => {
      // Rien à signaler après une fermeture DEMANDÉE : au changement
      // d'équipement, la notification de la socket du précédent atterrissait sur
      // l'état « en direct » du suivant. Prouvé par sonde.
      if (closed) return
      onStatus?.(false)

      // Les codes 4000-4999 sont réservés à l'applicatif : le serveur ne les
      // émet que lorsqu'il a une raison DÉFINITIVE de refuser — 4404
      // « Équipement retiré » quand la sentinelle du poller réveille les
      // abonnés d'un équipement qu'on vient de désappairer. Y répondre par une
      // reconnexion, c'est redemander toutes les deux secondes ce que le
      // serveur vient de refuser. Mesuré, backend démarré : le client recevait
      // bien `code=4404 reason='Équipement retiré'`, revenait 2 s plus tard,
      // tombait sur un nouveau refus, et recommençait tant que l'onglet restait
      // ouvert — la sentinelle posée côté serveur était entièrement inerte.
      //
      // Tout le reste (1006 sur coupure TCP, 1001 sur rechargement du serveur)
      // est un aléa dont on revient : on réessaie.
      if (event.code >= 4000 && event.code <= 4999) {
        closed = true
        onClosed?.({ code: event.code, reason: event.reason })
        return
      }
      const recul = RECULS[Math.min(tentative, RECULS.length - 1)]
      tentative += 1
      retryTimer = window.setTimeout(connect, recul)
    }
    // La socket de CET événement, et non la variable partagée : `connect` la
    // réassigne, et une erreur tardive fermerait la connexion suivante.
    socket.onerror = (event) => (event.target as WebSocket).close()
  }

  connect()

  return () => {
    closed = true
    if (retryTimer) window.clearTimeout(retryTimer)
    socket?.close()
  }
}
