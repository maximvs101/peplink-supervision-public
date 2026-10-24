import { causeDuTunnel } from '../lib/bondProfile'
import { useCallback, useEffect, useMemo, useState, useRef } from 'react'
import { Outlet, useLocation, useOutletContext, useParams } from 'react-router-dom'

import {
  fetchEvents, fetchRouter, fetchSessionSummary, fetchSessions, fetchTimeline,
  httpStatus, openLiveSocket, type LiveClosure,
} from '../api/client'
import type {
  DeviceLinkStatus, EventLog, Health, RadioSample, RouterSummary, Session, SessionSummary,
  Timeline,
} from '../types'
import type { FrameContext } from '../App'
import { EVENEMENT_PREVENIR } from '../components/PrevenirToggle'
import { type ModePrevenir, Veilleur, modeMemorise } from '../lib/prevenir'
import { EcranBoundary } from '../components/EcranBoundary'
import { Masthead } from '../components/Masthead'
import { compareWanIds } from '../lib/wanOrder'
import { bondRole, roleRetenu, type BondRole } from '../lib/bondRole'
import { paquetsParSecondeRecents } from '../lib/redundancy'
import { linkVerdict, type LinkVerdict } from '../lib/verdict'
import { RESUME_MS, debutObserve, libelleFenetre } from '../lib/session'
import { type Fraicheur as EtatFraicheur, aSignaler, duree, fraicheurNeuve } from '../lib/fraicheur'
import { Fraicheur } from '../components/Fraicheur'

export interface DeviceContext {
  routerId: string
  router: RouterSummary | null
  samples: RadioSample[]
  live: boolean
  showThroughput: boolean
  /** L'équipement porte-t-il un tunnel SpeedFusion ? Les grandeurs du tunnel —
   *  latence, perte — n'existent que là, et ne doivent pas être proposées
   *  ailleurs : un graphe vide sur un axe gradué se lit comme une mesure nulle. */
  hasTunnel: boolean
  /** État de la liaison avec l'équipement, rafraîchi indépendamment du flux. */
  status: DeviceLinkStatus
  /** Âge du relevé le plus récent : mesuré par le serveur à la réception du
   *  résumé, puis vieilli localement. */
  ageSeconds: number | null
  /** Cause de l'échec telle que le serveur la constate, expurgée de tout secret. */
  error: string | null
  /** Période observée entre deux cycles du serveur, quand il l'a mesurée : le
   *  seuil de péremption s'en déduit au lieu d'être figé dans le navigateur. */
  periodSeconds: number | null
  /** Le stationnement en cours. L'équipement n'est alimenté que véhicule à
   *  l'arrêt : c'est un lieu et une intervention, et c'est la fenêtre naturelle
   *  de tout ce qu'on relit. */
  session: Session | null
  /** Toute la liste, pour le sélecteur de l'historique. `DeviceShell` la lit
   *  déjà toutes les trente secondes pour en garder la première : le détail
   *  d'un lien la relisait de son côté toutes les quinze, et les deux
   *  coïncidaient une fois sur deux — le seul doublon de requête qui était
   *  du code, une fois écartée la fausse piste du second onglet. */
  sessions: Session[]
  /** À appeler après un renommage : la liste se relit hors de sa cadence. */
  rafraichirSessions: () => Promise<void>
  /** Ce que chaque lien a fait pendant cette fenêtre, en tranches de temps
   *  égales. `null` tant que la relecture n'a pas abouti. */
  timeline: Timeline | null
  /** Ce que la fenêtre relue COUVRE, dit en clair. Calculé ici et non dans
   *  chaque écran : la vue d'ensemble et la fiche d'un lien doivent nommer la
   *  même base de temps, sous peine d'afficher deux comptes sous un même mot. */
  windowLabel: string
  /** À quoi sert le bond : annoncé par le pair ET constaté au trafic. */
  role: BondRole
  /** Ce qui s'est passé pendant cette fenêtre, daté. */
  events: EventLog | null
  /** La fiche du stationnement en cours : le VOLUME échangé depuis le premier
   *  relevé. L'écran ne donnait que le débit de l'instant — « 0,0 Mbit/s » à
   *  côté d'un rapport qui comptait 1,5 Go descendus (A-5). */
  resume: SessionSummary | null
  /** L'état de santé du serveur, pour ce qui relève du diagnostic et non de
   *  l'exploitation — la décomposition de la cadence, notamment. */
  health: Health | null
  /** Le verdict BRUT de la liaison, calculé UNE fois par relevé ici : le
   *  bandeau le lisse, le tableau de bord en tire les griefs du bond et de la
   *  radio. Chacun le recalculait — `Overview` sans mémo, à chaque trame. */
  verdict: LinkVerdict
  /** La fraîcheur de chaque partie relue séparément : quand elle a répondu
   *  pour la dernière fois, et si sa dernière relecture a échoué. Chaque
   *  écran pose la mention sous la partie qu'elle concerne. */
  fraicheur: Record<PartieRelue, EtatFraicheur>
}

/** Les parties de l'écran relues chacune de leur côté. */
export type PartieRelue = 'etat' | 'sessions' | 'bande' | 'journal' | 'fiche'

export const useDevice = () => useOutletContext<DeviceContext>()

/** Cadence de rafraîchissement de l'état de l'équipement. */
const REFRESH_MS = 10_000

/** Cadence de relecture de la bande de stationnement. Bien plus lente : elle
 *  couvre des heures, une tranche vaut plusieurs dizaines de secondes, et la
 *  rejouer à chaque cycle coûterait une agrégation SQL pour un pixel. */
const BAND_MS = 30_000

/** Chaque partie avec SA période : c'est elle qui dit quand une donnée cesse
 *  d'être fraîche. La fiche n'est relue que toutes les cinq minutes ; la juger
 *  à la cadence de la bande la déclarerait ancienne en permanence. */
const fraicheursNeuves = (): Record<PartieRelue, EtatFraicheur> => ({
  etat: fraicheurNeuve(REFRESH_MS),
  sessions: fraicheurNeuve(BAND_MS),
  bande: fraicheurNeuve(BAND_MS),
  journal: fraicheurNeuve(BAND_MS),
  fiche: fraicheurNeuve(RESUME_MS),
})

const messageDe = (err: unknown): string => (err instanceof Error ? err.message : 'Erreur inconnue')

/** Dernier résumé RÉELLEMENT obtenu du serveur, et l'instant de sa réception.
 *
 *  Le couple est indissociable : `age_seconds` est une mesure du serveur valable
 *  à cet instant-là et à aucun autre. Le conserver seul revenait à afficher
 *  éternellement l'âge qu'avait la mesure au dernier échange abouti. */
interface Snapshot {
  summary: RouterSummary
  /** Horloge MONOTONE (`performance.now`), pas `Date.now()` : une horloge système
   *  qui recule — mise à l'heure NTP, sortie de veille — rendrait l'écart négatif
   *  et rajeunirait la mesure, exactement le mode de panne déjà corrigé en
   *  déplaçant le calcul de l'âge côté serveur. */
  receivedAt: number
}

/**
 * Enveloppe d'un équipement : porte le flux temps réel pour TOUS ses écrans.
 *
 * La connexion WebSocket vit ici plutôt que dans chaque page, sinon naviguer du
 * premier au second niveau la fermerait et la rouvrirait — avec une seconde de
 * données vides à chaque aller-retour, exactement là où l'opérateur enchaîne les
 * allers-retours.
 */
export function DeviceShell() {
  const { routerId = '' } = useParams()
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [samples, setSamples] = useState<Record<string, RadioSample>>({})
  const [live, setLive] = useState(false)
  /** Il n'y a plus rien à montrer : chargement impossible, ou équipement retiré. */
  const [fatal, setFatal] = useState<string | null>(null)
  // L'identifiant ne désigne aucun équipement supervisé — 404 dès la PREMIÈRE
  // lecture. Ce n'est pas un retrait : rien n'a jamais été affiché.
  const [inconnu, setInconnu] = useState(false)
  /** L'écran tient encore sur un résumé antérieur, mais il ne se rafraîchit plus. */
  const [refreshError, setRefreshError] = useState<string | null>(null)
  /** Le serveur a refermé le flux pour une raison définitive. */
  const [liveClosure, setLiveClosure] = useState<string | null>(null)
  /** Avance à CHAQUE tentative de rafraîchissement, aboutie ou non : c'est lui
   *  qui fait vieillir l'âge, donc qui rend la péremption détectable. */
  const [clock, setClock] = useState(() => performance.now())
  /** Le stationnement en cours et sa bande. Séparés du reste : ils se relisent
   *  à une cadence bien plus lente, et un échec ici ne doit pas priver l'écran
   *  de son état courant. */
  const [session, setSession] = useState<Session | null>(null)
  const [sessions, setSessions] = useState<Session[]>([])
  const relireRef = useRef<(() => Promise<void>) | null>(null)
  // L'équipement a été retiré (404) : plus rien à relire. `stopped` n'existait
  // que dans l'effet du flux principal, et la bande, le journal et les
  // sessions continuaient d'être demandés toutes les trente secondes — trois
  // requêtes sur un équipement qui n'existe plus, tant que l'onglet restait
  // ouvert. Une référence, parce que deux effets doivent la lire.
  const retire = useRef(false)
  const [timeline, setTimeline] = useState<Timeline | null>(null)
  const [events, setEvents] = useState<EventLog | null>(null)
  const [resume, setResume] = useState<SessionSummary | null>(null)
  const [fraicheur, setFraicheur] = useState(fraicheursNeuves)
  /** Une réponse aboutie (`echec` nul) ou un échec, pour une partie. Un échec
   *  ne touche pas à `lueA` : la donnée affichée date toujours de là. */
  const noter = useCallback((partie: PartieRelue, echec: string | null) => {
    setFraicheur((prev) => ({
      ...prev,
      [partie]: echec === null
        ? { ...prev[partie], lueA: performance.now(), echec: null }
        : { ...prev[partie], echec },
    }))
  }, [])
  /** « Réessayer maintenant » sur l'état de l'équipement : posé par l'effet du
   *  flux principal, qui seul connaît le jeton de séquence et la minuterie. */
  const reessayerEtat = useRef<(() => void) | null>(null)
  /** La relance MANUELLE est en vol : le bouton le dit et ne la double pas. */
  // La relance manuelle : partie (« relance »), ou rattachée à une lecture
  // déjà en vol (« deja »).
  const [relanceEnVol, setRelanceEnVol] = useState<false | 'relance' | 'deja'>(false)
  /** Chaque « Réessayer » du flux temps réel le rouvre : la fermeture était
   *  définitive, et seul un geste de l'exploitant la lève — jamais une boucle,
   *  qui redemanderait toutes les deux secondes ce que le serveur refuse. */
  const [essaiFlux, setEssaiFlux] = useState(0)

  const onStatus = useCallback((connected: boolean) => setLive(connected), [])
  const onClosed = useCallback((closure: LiveClosure) => {
    setLiveClosure(closure.reason || `fermeture ${closure.code}`)
  }, [])

  useEffect(() => {
    let cancelled = false
    // Passe à vrai dès qu'on sait qu'il n'y a plus rien à demander : redemander
    // toutes les dix secondes un équipement retiré ne ferait que répéter le 404.
    let stopped = false
    // Jeton de séquence : `cancelled` ne couvre que le démontage, pas deux
    // requêtes qui se DOUBLENT. Une requête lente partie avant une rapide
    // arrivait après elle, et `store` estampillait l'instant de réception sur le
    // résumé arrivé EN DERNIER, quel que soit son ordre de départ : l'âge passait
    // de quatre-vingt-dix secondes à deux, et le bandeau repassait au vert sur
    // une mesure périmée. Prouvé par sonde.
    let rang = 0
    let dernierRendu = 0
    // Un autre équipement repart à neuf : `retire` et `inconnu` n'étaient
    // jamais remis à zéro, et après un 404 l'équipement suivant restait
    // « inconnu », sans plus jamais relire sa bande ni son journal. Cet effet
    // est déclaré AVANT celui de la bande : il remet la référence à faux
    // avant que l'autre ne la lise.
    retire.current = false
    setInconnu(false)
    setSnapshot(null)
    setSamples({})
    setFatal(null)
    setRefreshError(null)
    setClock(performance.now())
    setFraicheur(fraicheursNeuves())
    setRelanceEnVol(false)

    const store = (summary: RouterSummary) => {
      // Le même instant pour les deux : l'âge affiché part alors exactement de
      // celui que le serveur vient de mesurer, sans écart de rendu.
      const at = performance.now()
      setSnapshot({ summary, receivedAt: at })
      setClock(at)
      setRefreshError(null)
      noter('etat', null)
      // Un résumé reçu dément tout échec de chargement antérieur. Le premier
      // échec non-404 posait `fatal`, le rafraîchissement continuait, mais rien
      // ne le levait : l'écran restait sur « Impossible de charger
      // l'équipement » une fois le service revenu. Un retrait (404) arrête la
      // relecture, et aucune réponse plus ancienne ne passe le jeton de rang.
      if (!retire.current) setFatal(null)
      // Le résumé fait AUTORITÉ sur la liste des liens. `setSamples` n'était
      // appelé qu'au montage et le flux ne sait que fusionner : un WAN désactivé
      // depuis l'administration — que le serveur purge bien de son état et omet
      // de `/routers` — restait à l'écran pour toujours, tuile connectée, signal
      // plein, dans le bond, avec son dernier bon relevé. Le compte du bandeau
      // suivait : « 4 liens sur 4 connectés » quand il n'en restait trois.
      //
      // Le relevé le plus RÉCENT gagne, et non systématiquement celui du flux :
      // un flux interrompu laisserait sinon un état plus vieux que le résumé.
      setSamples((prev) => {
        const vivants: Record<string, RadioSample> = {}
        for (const frais of summary.wans) {
          const connu = prev[frais.wan.wan_id]
          vivants[frais.wan.wan_id] =
            connu && Date.parse(connu.ts) > Date.parse(frais.ts) ? connu : frais
        }
        return vivants
      })
    }

    // Un échec de rafraîchissement est une information à afficher, pas un
    // événement à avaler. Le `.catch(() => undefined)` d'avant laissait survivre
    // le résumé précédent en silence : trente échecs consécutifs — cinq minutes —
    // et le bandeau annonçait toujours « Liaison OK » en vert.
    const fail = (err: unknown, initial: boolean) => {
      if (httpStatus(err) === 404) {
        stopped = true
        retire.current = true
        if (initial) {
          // Rien n'a jamais été affiché : ce n'est pas un retrait, c'est un
          // identifiant qui ne désigne rien — un lien erroné, ou un équipement
          // retiré avant l'ouverture. Aucune cause n'est inventée.
          setInconnu(true)
          setFatal(
            // Sans recopier l'identifiant : une empreinte opaque n'aide pas.
            'Aucun équipement supervisé sous cet identifiant. ' +
              'Le lien est peut-être erroné, ou l’équipement a été retiré de la ' +
              'supervision. La liste des équipements dit ce qui est supervisé.',
          )
          return
        }
        // L'équipement n'est plus dans la flotte. Continuer à afficher son
        // dernier état connu, c'est présenter comme supervisé ce qui ne l'est
        // plus — reproduit en le retirant depuis un autre onglet.
        setFatal(
          'Cet équipement n’est plus supervisé : il a été retiré depuis un autre ' +
            'onglet ou une autre session. Les valeurs affichées jusqu’ici dataient ' +
            'd’avant son retrait.',
        )
        return
      }
      const message = messageDe(err)
      noter('etat', message)
      if (initial) setFatal(`Impossible de charger l’équipement : ${message}`)
      else setRefreshError(message)
    }

    // Une lecture de l'état, quelle qu'en soit l'origine — montage, période,
    // relance manuelle. Toutes portent le même jeton : n'importe laquelle peut
    // être doublée par une autre, et c'est même le cas le plus courant au
    // montage, sur un serveur qui vient de démarrer. Rend la promesse de SA
    // réponse, pour que la relance manuelle sache quand elle est retombée.
    // L'instant de départ de la plus récente, tant qu'elle n'a pas répondu.
    // Sa promesse aussi : un clic pendant qu'elle vole attend SA réponse.
    let enVolDepuis: number | null = null
    let enVol: Promise<void> | null = null
    const lire = (initial: boolean): Promise<void> => {
      const mien = ++rang
      const depart = performance.now()
      enVolDepuis = depart
      const retombee = () => { if (enVolDepuis === depart) { enVolDepuis = null; enVol = null } }
      const promesse = fetchRouter(routerId)
        .then((summary) => {
          retombee()
          // Une réponse dépassée est jetée : elle décrit un instant que l'écran
          // a déjà dépassé, et l'accepter ferait reculer le temps.
          if (cancelled || mien < dernierRendu) return
          dernierRendu = mien
          store(summary)
        })
        .catch((err) => {
          retombee()
          if (!cancelled && mien >= dernierRendu) fail(err, initial)
        })
      if (enVolDepuis === depart) enVol = promesse
      return promesse
    }

    lire(true)

    // Le flux WebSocket porte les relevés, pas l'état de l'équipement : sans ce
    // rafraîchissement, un routeur qui tombe laisserait l'écran sur son dernier
    // état connu — « connecté » — indéfiniment.
    const periodique = () => {
      if (cancelled || stopped) return
      // L'horloge avance AVANT la requête, et non à sa résolution : une requête
      // qui n'aboutit jamais — le serveur accepte la connexion puis ne répond
      // plus — ne rejette pas, et gelait l'âge avec elle.
      setClock(performance.now())
      void lire(false)
    }
    let refresh = window.setInterval(periodique, REFRESH_MS)

    // « Réessayer maintenant » : la lecture part aussitôt, et la minuterie
    // repart d'une période entière derrière elle. Sans cela l'échéance
    // périodique pouvait tomber une seconde après la relance — deux requêtes
    // dos à dos pour une même question. Une lecture déjà en vol depuis moins
    // d'une période n'est pas doublée : sa réponse est celle qu'on attend, et
    // le bouton le DIT (« Lecture déjà en cours… ») — un clic qui ne faisait
    // rien passait pour un bouton cassé. Au-delà, elle est réputée pendue (le
    // serveur a accepté la connexion et ne répond plus) et ne doit pas
    // confisquer le bouton. `getJson` l'abandonne de toute façon après son
    // délai : aucune lecture ne tient le bouton indéfiniment.
    reessayerEtat.current = () => {
      if (cancelled || stopped) return
      if (enVol !== null && enVolDepuis !== null && performance.now() - enVolDepuis < REFRESH_MS) {
        setRelanceEnVol('deja')
        enVol.finally(() => { if (!cancelled) setRelanceEnVol(false) })
        return
      }
      window.clearInterval(refresh)
      refresh = window.setInterval(periodique, REFRESH_MS)
      setClock(performance.now())
      setRelanceEnVol('relance')
      lire(false).finally(() => { if (!cancelled) setRelanceEnVol(false) })
    }

    return () => {
      cancelled = true
      reessayerEtat.current = null
      window.clearInterval(refresh)
    }
  }, [routerId, noter])

  // Le flux temps réel, dans son propre effet : « Réessayer maintenant » le
  // rouvre sans relancer la lecture de l'état ni remettre l'écran à zéro.
  useEffect(() => {
    setLiveClosure(null)
    return openLiveSocket(
      routerId,
      (sample) => setSamples((prev) => ({ ...prev, [sample.wan.wan_id]: sample })),
      onStatus,
      onClosed,
    )
  }, [routerId, onStatus, onClosed, essaiFlux])

  // La fenêtre naturelle de relecture est le stationnement en cours — pas une
  // fenêtre glissante. L'équipement n'est alimenté que véhicule à l'arrêt :
  // « depuis la mise sous tension » est la seule borne qui ait un sens pour
  // relire ce qui s'est passé à un endroit donné. Faute de session connue, on
  // retombe sur les trois dernières heures.
  useEffect(() => {
    let cancelled = false
    setSession(null)
    setTimeline(null)
    setEvents(null)
    setResume(null)

    // Jeton de séquence, comme sur le flux principal. `cancelled` protège du
    // démontage, pas du DOUBLAGE : cette relecture enchaîne trois requêtes et se
    // rejoue toutes les trente secondes. Une lente partie au tour N peut rendre
    // APRÈS la rapide du tour N+1, et l'écran repose alors une bande et un
    // journal périmés par-dessus les frais — sans que rien ne le signale, puisque
    // les deux sont des réponses valides.
    let rang = 0
    let dernierRendu = 0
    // La dernière liste LUE : un échec de `/sessions` ne l'efface pas. Il
    // posait `[]` — la session choisie disparaissait du sélecteur, la courbe
    // repassait en fenêtre glissante pendant que la fiche restait, et le pied
    // écrivait « session du Invalid Date ». Un hoquet n'est pas une liste vide.
    let connues: Session[] | null = null
    let resumeDe: number | null = null
    let resumeLu = 0
    // La fenêtre que décrivent la bande et le journal AFFICHÉS. Un échec garde
    // la dernière donnée valide — l'effacer faisait disparaître la bande au
    // premier hoquet, sans un mot —, mais seulement pour la MÊME fenêtre : la
    // bande de la session précédente, posée sous le libellé de la nouvelle,
    // mentirait sur ce qu'elle couvre.
    let bandeDe: string | null = null
    let journalDe: string | null = null

    const relire = async () => {
      if (retire.current) return
      const mien = (rang += 1)
      const depasse = () => cancelled || mien < dernierRendu
      let echecSessions: string | null = null
      try {
        connues = await fetchSessions(routerId)
      } catch (err) {
        // Une session illisible n'empêche pas de relire les trois dernières
        // heures : on perd la borne, pas la bande — et on garde la dernière
        // liste connue.
        echecSessions = messageDe(err)
      }
      if (depasse()) return
      noter('sessions', echecSessions)
      dernierRendu = mien
      const toutes = connues ?? []
      const courante = toutes[0] ?? null
      setSession(courante)
      setSessions(toutes)
      // Depuis le premier RELEVÉ, pas depuis l'allumage : entre les deux il n'y
      // a rien à dessiner, et l'axe donnait quarante pour cent de sa largeur à
      // ce vide sur le MAX HD2 (allumé à 08:35, supervisé à partir de 10:53).
      const fenetre = courante ? { since: debutObserve(courante) } : { minutes: 180 }
      const cleFenetre = JSON.stringify(fenetre)
      try {
        const bande = await fetchTimeline(routerId, fenetre)
        if (!depasse()) {
          setTimeline(bande)
          bandeDe = cleFenetre
          noter('bande', null)
        }
      } catch (err) {
        if (!depasse()) {
          if (bandeDe !== cleFenetre) setTimeline(null)
          noter('bande', messageDe(err))
        }
      }
      try {
        const journal = await fetchEvents(routerId, fenetre)
        if (!depasse()) {
          setEvents(journal)
          journalDe = cleFenetre
          noter('journal', null)
        }
      } catch (err) {
        if (!depasse()) {
          if (journalDe !== cleFenetre) setEvents(null)
          noter('journal', messageDe(err))
        }
      }
      // Toutes les cinq minutes, pas à chaque relecture : la fiche relit toute
      // la session (24 h au plus), pour deux chiffres qui bougent peu — à la
      // cadence du journal, c'était une seconde lecture lourde toutes les 30 s
      // (revue du 17/10). Une session qui change est relue aussitôt.
      const maintenant = Date.now()
      if (courante?.id !== resumeDe || maintenant - resumeLu >= RESUME_MS) {
        try {
          const fiche = courante ? await fetchSessionSummary(courante.id) : null
          if (!depasse()) {
            setResume(fiche)
            resumeDe = courante?.id ?? null
            resumeLu = maintenant
            noter('fiche', null)
          }
        } catch (err) {
          // La fiche de CETTE session reste, marquée ancienne ; celle d'une
          // autre session ne décrit pas ce stationnement et s'efface.
          if (!depasse()) {
            if (resumeDe !== (courante?.id ?? null)) setResume(null)
            noter('fiche', messageDe(err))
          }
        }
      }
    }

    relire()
    relireRef.current = relire
    // Onglet caché, personne ne lit la bande ni le journal : le journal seul
    // coûte plus d'une seconde de SQL, et un stationnement dure des heures
    // (C-31). La relecture attend, et reprend dès le retour au premier plan.
    const cache = () => document.visibilityState === 'hidden'
    const t = window.setInterval(() => { if (!cache()) relire() }, BAND_MS)
    const auRetour = () => { if (!cache()) relire() }
    document.addEventListener('visibilitychange', auRetour)
    return () => {
      cancelled = true
      relireRef.current = null
      window.clearInterval(t)
      document.removeEventListener('visibilitychange', auRetour)
    }
  }, [routerId, noter])

  // Reconstruit à chaque rendu, ce tableau était une nouvelle valeur à chaque
  // fois : le bandeau ne pouvait plus s'y fier comme dépendance et bricolait une
  // clé de substitution, et l'observateur de taille de la carte se recréait des
  // dizaines de fois par minute. Mémoïsé, une nouvelle liste signifie vraiment
  // « les relevés ont changé ».
  const ordered = useMemo(
    () => Object.values(samples).sort((a, b) => compareWanIds(a.wan.wan_id, b.wan.wan_id)),
    [samples],
  )

  // Le journal se relit dès qu'un lien CHANGE D'ÉTAT — tombe, revient, est
  // désactivé —, et pas seulement toutes les trente secondes. Sinon la table,
  // en direct, montrait un lien revenu pendant que le journal affichait sa
  // coupure « en cours » : deux vérités sur un même écran (revue, scénarios 1
  // et 2). Le premier état connu n'en déclenche pas : il ne change rien.
  const etats = ordered
    .map((s) => `${s.wan.wan_id}:${s.wan.connected ? 1 : 0}${s.wan.disabled ? 'd' : ''}`)
    .join('|')
  const etatsVus = useRef('')
  useEffect(() => {
    if (etatsVus.current !== '' && etats !== etatsVus.current) void relireRef.current?.()
    etatsVus.current = etats
  }, [etats])

  // Âge du dernier relevé, vieilli depuis sa réception. On additionne une mesure
  // du serveur et une DURÉE écoulée localement : ce n'est pas la comparaison de
  // deux horloges que le calcul d'origine faisait, seul l'écoulement est lu ici.
  // Sans cela l'âge restait figé sur la valeur du dernier échange abouti, et le
  // seuil de péremption s'appliquait à un nombre qui ne bougeait plus.
  const ageSeconds = useMemo(() => {
    if (snapshot === null || snapshot.summary.age_seconds === null) return null
    return snapshot.summary.age_seconds + (clock - snapshot.receivedAt) / 1000
  }, [snapshot, clock])

  const router = snapshot?.summary ?? null

  // Le veilleur d'alertes (A-2) : un par équipement ouvert, pour que l'état
  // d'ouverture serve de référence — ce qui y figure se voit déjà.
  const veilleur = useRef<Veilleur | null>(null)
  useEffect(() => {
    const v = new Veilleur(modeMemorise(), 'équipement')
    veilleur.current = v
    const changer = (e: Event) => v.changerMode((e as CustomEvent<ModePrevenir>).detail)
    window.addEventListener(EVENEMENT_PREVENIR, changer)
    return () => {
      window.removeEventListener(EVENEMENT_PREVENIR, changer)
      veilleur.current = null
    }
  }, [routerId])
  // Le nom affiché dans les notifications : jamais l'identifiant opaque.
  const nomEquipement = router?.name ?? 'équipement'
  useEffect(() => {
    if (router) veilleur.current?.renommer(nomEquipement)
    if (router) veilleur.current?.observer(router.alerts ?? [])
  }, [router, nomEquipement])

  // Le `Frame` publie l'état de santé du serveur ; on n'en retient que la
  // période de scrutation, dont le seuil de péremption se déduit.
  const { health, routers } = useOutletContext<FrameContext>()
  const { pathname } = useLocation()

  // À quoi sert le bond : deux témoins, et non un seul. Ce que le pair ANNONCE
  // — la route par défaut y figure-t-elle ? — et ce qui TRANSITE réellement.
  // Calculé ici, et non dans chaque écran : le bandeau et le plan du bond en
  // tiraient chacun leur version, et seul celui qui avait la bande pouvait voir
  // le second témoin.
  // Le débit de paquets dans le tunnel, et non leur nombre : un compte comparé à
  // une fenêtre de durée libre franchissait le seuil sur un simple keepalive.
  // Et sur les DERNIÈRES minutes, non sur toute la session : une moyenne de
  // six heures mettait vingt-huit minutes à voir le trafic arriver, et des
  // heures à le voir partir.
  // Mémoïsé : `bondRole` rend un objet neuf, et le verdict ci-dessous dépend
  // du rôle — sans cela il se recalculerait à chaque rendu.
  const mesure = useMemo(
    () => bondRole(router?.bond_routes, paquetsParSecondeRecents(timeline)),
    [router?.bond_routes, timeline],
  )
  // Le dernier rôle MESURÉ, par équipement. Le débit du tunnel s'éteint avec
  // lui : sans cette mémoire, dix minutes après la chute du concentrateur le
  // témoin ne voyait plus rien et le bandeau changeait d'avis (C-2).
  const dernierMesure = useRef<{ routerId: string; role: BondRole } | null>(null)
  if (mesure.kind === 'porte' || mesure.kind === 'routes') dernierMesure.current = { routerId, role: mesure }
  const role = roleRetenu(
    mesure, dernierMesure.current?.routerId === routerId ? dernierMesure.current.role : null,
  )

  const statut = inconnu ? 'inconnu' : (router?.status ?? 'en attente')
  const periode = router?.period_seconds ?? health?.cycle_period_seconds ?? null
  const tunnelCause = router
    ? causeDuTunnel(router.bond_profiles ?? [], router.capabilities, Date.now())
    : null
  const seuilLatence = router?.latency_threshold_ms ?? null
  const tunnelAttendu = router?.tunnel_attendu ?? null
  const jamais = router?.never_connected
  // Une seule fois par changement d'ENTRÉE — un relevé, un résumé, l'âge qui
  // avance —, et non à chaque rendu ni une fois par consommateur. `tunnelCause`
  // est une chaîne : recalculée à chaque rendu, elle ne change pas d'identité.
  const verdict = useMemo(
    () => linkVerdict({
      samples: ordered, status: statut, ageSeconds, periodSeconds: periode, role,
      latencyThresholdMs: seuilLatence, tunnelAttendu, tunnelCause,
      neverConnected: jamais ?? [],
    }),
    [ordered, statut, ageSeconds, periode, role, seuilLatence, tunnelAttendu, tunnelCause, jamais],
  )

  const context: DeviceContext = {
    routerId,
    router,
    samples: ordered,
    live,
    // Piloté par les capacités de l'équipement, jamais par un test sur la valeur :
    // un débit momentanément nul reste une mesure.
    showThroughput: router?.capabilities?.per_wan_throughput ?? true,
    // Piloté par la DONNÉE et non par la capacité matérielle. `capabilities
    // .pepvpn` dit « ce châssis sait faire du SpeedFusion », ce qui reste vrai
    // quand aucun profil n'est configuré : `metricsFor` proposait alors
    // « Latence » et « Perte », et `/history?metric=rtt_ms` rendait des points
    // DATÉS à valeur nulle — donc `data.length > 0`, donc pas de « Collecte en
    // cours » : une courbe vide sur un axe gradué 0-100. C'est le défaut même
    // que `metricsFor` a été écrit pour supprimer. La présence d'au moins un
    // objet tunnel est la seule preuve qu'un tunnel est CONFIGURÉ, et c'est déjà
    // la règle qu'applique `keyFigures`.
    hasTunnel: ordered.some((s) => s.wan.tunnel !== null),
    status: router?.status ?? 'en attente',
    ageSeconds,
    error: router?.error ?? null,
    // La période de CET équipement d'abord : chacun a sa propre boucle de
    // scrutation, et la vue de flotte retient le pire — un équipement mort
    // relâcherait le seuil de péremption de ses voisins sains de tout son budget.
    // La période observée, jamais la consigne : voir `staleAfterSeconds`.
    periodSeconds: periode,
    session,
    sessions,
    rafraichirSessions: async () => { await relireRef.current?.() },
    timeline,
    windowLabel: session ? libelleFenetre(session) : 'Trois dernières heures',
    role,
    events,
    resume,
    health,
    verdict,
    fraicheur,
  }

  // Le bouton des bandeaux de l'état : une relance à la fois, et dit comme telle.
  const boutonEtat = (
    <button
      type="button"
      className="is-quiet reessayer"
      disabled={relanceEnVol !== false}
      onClick={() => reessayerEtat.current?.()}
    >
      {relanceEnVol === 'deja'
        ? 'Lecture déjà en cours…'
        : relanceEnVol ? 'Lecture en cours…' : 'Réessayer maintenant'}
    </button>
  )

  // Le bandeau de tête reste en place même quand il n'y a plus rien à montrer :
  // il porte le verdict, et « cet équipement n'est plus supervisé » EST un
  // verdict. Le faire disparaître laisserait l'écran sans réponse.
  const mast = (
    <Masthead
      routerId={routerId}
      router={router}
      routers={routers}
      samples={ordered}
      live={live}
      status={statut}
      ageSeconds={ageSeconds}
      error={context.error}
      periodSeconds={context.periodSeconds}
      role={role}
      latencyThresholdMs={router?.latency_threshold_ms ?? null}
      tunnelAttendu={router?.tunnel_attendu ?? null}
      tunnelCause={tunnelCause}
      neverConnected={router?.never_connected ?? []}
      verdict={verdict}
    />
  )

  if (fatal) {
    return (
      <>
        {mast}
        <div className="page">
          <div className="notice is-error" role="alert">
            {fatal}
            {/* Un 404 est définitif — identifiant inconnu ou équipement retiré :
                redemander ne changerait rien, et le bouton le laisserait croire. */}
            {!retire.current && <> {boutonEtat}</>}
          </div>
        </div>
      </>
    )
  }

  // L'état de l'équipement ne répond plus SANS échec déclaré — une requête
  // pendue ne rejette pas : seule l'horloge peut le dire. Sur un échec, c'est
  // le bandeau « non rafraîchi » qui porte l'âge.
  const etatAncien = refreshError === null && aSignaler(fraicheur.etat, clock)
  const ageEtat = fraicheur.etat.lueA !== null
    ? ` Dernière réponse il y a ${duree(clock - fraicheur.etat.lueA)}.`
    : ''

  return (
    <>
      {mast}
      {(refreshError || liveClosure || etatAncien) && (
        <div className="page" style={{ paddingBottom: 0 }}>
          {refreshError && (
            <div className="notice is-warn" role="alert">
              <strong>État de l’équipement non rafraîchi.</strong> {refreshError}.{ageEtat} Ce
              qui est à l’écran date du dernier échange abouti et vieillit : le bandeau le
              déclarera périmé. {boutonEtat}
            </div>
          )}
          {etatAncien && <Fraicheur nom="État de l’équipement" etat={fraicheur.etat} />}
          {liveClosure && (
            <div className="notice is-error" role="alert">
              Flux temps réel refermé par le serveur : {liveClosure}. Aucune reconnexion
              automatique ne sera tentée.{' '}
              <button
                type="button"
                className="is-quiet reessayer"
                onClick={() => { setLiveClosure(null); setEssaiFlux((n) => n + 1) }}
              >
                Réessayer maintenant
              </button>
            </div>
          )}
        </div>
      )}
      {/* Le filet de l'ÉCRAN, sous le bandeau : une erreur de rendu du tableau
          de bord ou de la fiche d'un lien ne doit emporter ni le verdict ni la
          navigation. `key` sur la route : l'écran suivant repart à neuf. */}
      <EcranBoundary key={pathname} titre="Cet écran">
        <Outlet context={context} />
      </EcranBoundary>
    </>
  )
}
