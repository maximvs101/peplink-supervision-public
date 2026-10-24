import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { HashRouter, Navigate, Outlet, Route, Routes, useParams } from 'react-router-dom'

import { fetchHealth, fetchRouters } from './api/client'
import type { Health, RouterSummary } from './types'
import { EcranBoundary } from './components/EcranBoundary'
import { DeviceShell } from './pages/DeviceShell'
import { Discovery } from './pages/Discovery'
// Chargée à la demande, comme la courbe d'un lien : le rapport importe
// `recharts`, et un import statique ici le remettait dans le paquet principal —
// 688 Ko chargés à l'ouverture du tableau de bord au lieu de ~290 Ko (revue
// d'optimisation du 17/10).
const Rapport = lazy(() => import('./pages/Rapport').then((m) => ({ default: m.Rapport })))
import { Overview } from './pages/Overview'
import { WanDetail } from './pages/WanDetail'

/** Premier équipement RÉEL, à défaut le premier tout court.
 *
 *  Sans fichier de flotte — c'est-à-dire exactement le cas « je ne connais ni
 *  l'adresse ni le mot de passe » — la flotte de démonstration est chargée, et
 *  l'ouverture tombait dessus : l'opérateur voyait d'abord des données simulées,
 *  vertes et plausibles, et devait aller chercher son équipement dans le
 *  sélecteur après l'avoir appairé. */
function premierReel(routers: RouterSummary[]): RouterSummary {
  // Parmi les réels, celui qui RÉPOND d'abord : un équipement du fichier de
  // flotte éteint passait avant celui qu'on venait d'appairer, et l'ouverture
  // tombait sur « Équipement injoignable » (vu sur un MBX réel, le 30/09).
  const reels = routers.filter((r) => r.mode !== 'mock')
  return reels.find((r) => r.status === 'connecté') ?? reels[0] ?? routers[0]
}

/** Combien de temps l'entrée attend une première réponse avant de se rabattre
 *  sur le premier équipement. */
const ATTENTE_ENTREE_MS = 30_000

/** Faut-il encore attendre avant de choisir ? Oui tant qu'aucun équipement réel
 *  ne répond et qu'un réel au moins n'a pas encore été scruté : au démarrage du
 *  superviseur, tous sont « en attente », et `premierReel` retenait le premier
 *  du fichier de flotte — absent de ce réseau, « injoignable » au cycle
 *  suivant — alors que le HD2 répondait. Le `replace` de la redirection l'y
 *  laissait. */
function choixPremature(routers: RouterSummary[]): boolean {
  const reels = routers.filter((r) => r.mode !== 'mock')
  return reels.every((r) => r.status !== 'connecté')
    && reels.some((r) => r.status === 'en attente')
}

/** Ce que le cadre publie à ses écrans.
 *
 *  Il n'y a plus de barre de marque au-dessus : le bandeau de tête appartient à
 *  l'équipement et vit dans `DeviceShell`, parce que ce qu'il porte — le verdict,
 *  l'âge du relevé — n'existe qu'une fois l'équipement choisi. Une barre qui
 *  répète le nom du produit à qui vient de l'ouvrir prenait la place du seul
 *  élément que l'exploitant doit voir. */
export interface FrameContext {
  health: Health | null
  routers: RouterSummary[]
}

/** Le cadre. Une panne du service se dit ICI, en bandeau, sans démonter ce qui
 *  est en dessous : elle remplaçait tout l'arbre par un message, et au cycle
 *  suivant qui réussissait, tout était remonté à neuf — nouvelle WebSocket,
 *  session choisie, métrique et fiche perdues, pour un hoquet Wi-Fi. Et le
 *  message disait « rechargez » pour un rétablissement automatique. */
function Panne({ panne, premiere }: { panne: string | null; premiere: boolean }) {
  if (panne === null) return null
  return (
    <div className="page is-narrow" style={{ paddingBottom: 0 }}>
      <div className="notice is-error" role="alert">
        <strong>Le service de supervision ne répond pas.</strong> {panne}. Nouvelle
        tentative toutes les quinze secondes
        {premiere
          ? ' ; rien n’a encore été lu — il tourne sur cette machine et sert aussi cette interface.'
          : ' ; ce qui est affiché date de la dernière réponse.'}
      </div>
    </div>
  )
}

function Frame({ routers, health, panne, santePanne }: {
  /** `null` tant que la flotte n'a jamais été lue. */
  routers: RouterSummary[] | null
  health: Health | null
  panne: string | null
  /** Échec de la dernière lecture de `/health`. */
  santePanne: string | null
}) {
  return (
    <>
      {/* Le VRAI état : sur `#/decouverte`, la flotte peut n'avoir jamais été
          lue. `premiere={false}` codé en dur y faisait dire « ce qui est
          affiché date de la dernière réponse » — sans aucune réponse. */}
      <Panne panne={panne} premiere={routers === null} />
      {/* `/health` porte la période de scrutation du serveur, d'où se déduit
          le seuil de péremption quand l'équipement ne publie pas la sienne.
          Son échec était avalé et la période gardait sa dernière valeur sans
          un mot. Tu quand le service entier ne répond pas : la panne le dit. */}
      {panne === null && santePanne !== null && (
        <div className="page is-narrow" style={{ paddingBottom: 0 }}>
          <div className="notice is-warn" role="status">
            <strong>État de santé du service illisible :</strong> {santePanne}.{' '}
            {health !== null
              ? 'La période de scrutation retenue date de la dernière lecture aboutie.'
              : 'Aucune période de scrutation du service n’est connue.'}
          </div>
        </div>
      )}
      <Outlet context={{ health, routers: routers ?? [] } satisfies FrameContext} />
    </>
  )
}

/** Là où mène l'adresse vide. Tant que la flotte n'est pas lue, on attend ;
 *  lue et VIDE, c'est l'écran de découverte — la seule chose à faire. Un
 *  `return` avant le routeur affichait « Chargement de la flotte… » à vie sur
 *  une flotte vide, et `#/decouverte` n'existait plus : l'exécutable sans
 *  routers.yaml — le cas normal — n'avait aucun moyen d'appairer, et retirer
 *  le dernier équipement faisait disparaître l'écran de découverte sous
 *  l'utilisateur au sondage suivant. */
function Entree({ routers, panne }: { routers: RouterSummary[] | null; panne: string | null }) {
  // L'attente d'une première réponse est bornée : un équipement qui resterait
  // « en attente » ne bloque pas l'entrée.
  const [delaiEcoule, setDelaiEcoule] = useState(false)
  useEffect(() => {
    const t = window.setTimeout(() => setDelaiEcoule(true), ATTENTE_ENTREE_MS)
    return () => window.clearTimeout(t)
  }, [])
  if (routers === null || (!delaiEcoule && routers.length > 0 && choixPremature(routers))) {
    // La flotte n'a jamais été lue. Si c'est parce que le service ne répond
    // pas, il faut le dire ICI : le bandeau du cadre ne couvre que les écrans
    // qu'il enveloppe, et `#/` restait sur « Chargement de la flotte… » à vie
    // sans un mot — l'ancien écran, lui, affichait l'erreur.
    return (
      <>
        <Panne panne={panne} premiere={routers === null} />
        {panne === null && (
          <div className="page is-narrow">
            <div className="notice" role="status">Chargement de la flotte…</div>
          </div>
        )}
      </>
    )
  }
  if (routers.length === 0) return <Navigate to="/decouverte" replace />
  return <Navigate to={`/e/${encodeURIComponent(premierReel(routers).id)}`} replace />
}

/** `DeviceShell`, remonté à chaque changement d'équipement.
 *
 *  Le `key` ne peut pas être posé sur l'élément de route lui-même : il faut
 *  lire le paramètre, et seul un composant monté SOUS la route y a accès. */
function DeviceShellParEquipement() {
  const { routerId } = useParams()
  return (
    <EcranBoundary key={routerId} titre="Équipement">
      <DeviceShell key={routerId} />
    </EcranBoundary>
  )
}

export default function App() {
  // `null` tant que la flotte n'a pas été lue : une liste vide est une réponse,
  // pas une attente.
  const [routers, setRouters] = useState<RouterSummary[] | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [santeErreur, setSanteErreur] = useState<string | null>(null)
  // Jetons de séquence, comme dans `DeviceShell` : deux lectures peuvent se
  // DOUBLER — une lente partie au tour N rendue après la rapide du tour N+1 —
  // et la réponse périmée écrasait la récente (un équipement appairé entre
  // les deux disparaissait du sélecteur pour quinze secondes). Une réponse
  // dépassée est jetée, succès comme échec.
  const rang = useRef({ flotte: 0, flotteRendue: 0, sante: 0, santeRendue: 0 })

  const load = useCallback(() => {
    const r = rang.current
    const mienne = ++r.flotte
    fetchRouters()
      // Le retour efface l'erreur. Elle n'était JAMAIS remise à nul : un
      // redémarrage du serveur, une sortie de veille, un hoquet Wi-Fi, et
      // l'écran restait bloqué sur « Le service ne répond pas » jusqu'au
      // rechargement — alors que le rafraîchissement de quinze secondes avait
      // repris et réussissait. Prouvé : après un échec puis quatre cycles tous
      // réussis, le message était toujours là.
      .then((liste) => {
        if (mienne < r.flotteRendue) return
        r.flotteRendue = mienne
        setRouters(liste)
        setError(null)
      })
      .catch((err) => {
        if (mienne < r.flotteRendue) return
        r.flotteRendue = mienne
        setError(err instanceof Error ? err.message : 'Backend injoignable')
      })
    const sienne = ++r.sante
    fetchHealth()
      .then((h) => {
        if (sienne < r.santeRendue) return
        r.santeRendue = sienne
        setHealth(h)
        setSanteErreur(null)
      })
      .catch((err) => {
        if (sienne < r.santeRendue) return
        r.santeRendue = sienne
        setSanteErreur(err instanceof Error ? err.message : 'Lecture impossible')
      })
  }, [])

  // La période observée n'existe qu'après deux cycles : un chargement unique la
  // trouverait toujours nulle, et le navigateur retomberait à vie sur son défaut
  // prudent. Ce rafraîchissement fait aussi apparaître un équipement appairé
  // depuis l'écran de découverte, qui restait invisible jusqu'au rechargement.
  useEffect(() => {
    load()
    const t = window.setInterval(load, 15_000)
    return () => window.clearInterval(t)
  }, [load])

  return (
    // HashRouter et non BrowserRouter : le backend sert l'interface compilée via
    // StaticFiles monté sur « / », qui répondrait 404 à un rafraîchissement sur
    // une URL profonde. Le routage par fragment donne les liens partageables et
    // le bouton Précédent sans rien demander au serveur.
    <HashRouter>
      <Routes>
        <Route element={<Frame routers={routers} health={health} panne={error} santePanne={santeErreur} />}>
          <Route path="/decouverte" element={<EcranBoundary titre="Découverte"><Discovery /></EcranBoundary>} />
          {/* `key` sur le paramètre : React Router ne remonte pas un élément
              quand seul le paramètre change, si bien qu'un rendu entier avait
              lieu sous la NOUVELLE identité avec les données de l'ANCIENNE —
              nom, verdict, âge, table des liens. La remise à zéro vit dans un
              effet, donc après le rendu. Prouvé : la trace des rendus donnait
              « B|A », puis « B|— », puis « B|B ». Le remontage supprime aussi
              la trame en vol qui s'appliquait au suivant, et le message de
              reprise d'un équipement affiché sur l'écran d'un autre. */}
          <Route path="/e/:routerId" element={<DeviceShellParEquipement />}>
            <Route index element={<Overview />} />
            <Route path="wan/:wanId" element={<WanDetail />} />
          </Route>
        </Route>
        {/* Le rapport d'une session, hors du cadre de l'application : une page
            à imprimer, sans barre ni navigation (R-3.7). */}
        <Route path="/rapport/:sessionId" element={
          <EcranBoundary titre="Rapport">
            <Suspense fallback={<div className="rapport"><p className="disclaimer">Chargement du rapport…</p></div>}>
              <Rapport />
            </Suspense>
          </EcranBoundary>
        } />
        <Route path="*" element={<Entree routers={routers} panne={error} />} />
      </Routes>
    </HashRouter>
  )
}
