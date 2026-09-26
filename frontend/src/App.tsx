import { useCallback, useEffect, useState } from 'react'
import { HashRouter, Navigate, Outlet, Route, Routes, useParams } from 'react-router-dom'

import { fetchHealth, fetchRouters } from './api/client'
import type { Health, RouterSummary } from './types'
import { EcranBoundary } from './components/EcranBoundary'
import { DeviceShell } from './pages/DeviceShell'
import { Discovery } from './pages/Discovery'
import { Rapport } from './pages/Rapport'
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
  return routers.find((r) => r.mode !== 'mock') ?? routers[0]
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

function Frame({ routers, health, panne }: { routers: RouterSummary[]; health: Health | null; panne: string | null }) {
  return (
    <>
      <Panne panne={panne} premiere={false} />
      <Outlet context={{ health, routers } satisfies FrameContext} />
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
  if (routers === null) {
    // La flotte n'a jamais été lue. Si c'est parce que le service ne répond
    // pas, il faut le dire ICI : le bandeau du cadre ne couvre que les écrans
    // qu'il enveloppe, et `#/` restait sur « Chargement de la flotte… » à vie
    // sans un mot — l'ancien écran, lui, affichait l'erreur.
    return (
      <>
        <Panne panne={panne} premiere />
        {panne === null && (
          <div className="page is-narrow">
            <div className="notice">Chargement de la flotte…</div>
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

  const load = useCallback(() => {
    fetchRouters()
      // Le retour efface l'erreur. Elle n'était JAMAIS remise à nul : un
      // redémarrage du serveur, une sortie de veille, un hoquet Wi-Fi, et
      // l'écran restait bloqué sur « Le service ne répond pas » jusqu'au
      // rechargement — alors que le rafraîchissement de quinze secondes avait
      // repris et réussissait. Prouvé : après un échec puis quatre cycles tous
      // réussis, le message était toujours là.
      .then((liste) => { setRouters(liste); setError(null) })
      .catch((err) => setError(err instanceof Error ? err.message : 'Backend injoignable'))
    fetchHealth().then(setHealth).catch(() => undefined)
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
        <Route element={<Frame routers={routers ?? []} health={health} panne={error} />}>
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
        <Route path="/rapport/:sessionId" element={<EcranBoundary titre="Rapport"><Rapport /></EcranBoundary>} />
        <Route path="*" element={<Entree routers={routers} panne={error} />} />
      </Routes>
    </HashRouter>
  )
}
