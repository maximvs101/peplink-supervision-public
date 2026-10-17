import { Link, useParams } from 'react-router-dom'

import { lazy, Suspense } from 'react'

/** Chargé à la demande. `recharts` pèse à lui seul les deux tiers du code
 *  compilé, et il ne sert que sur cet écran de second niveau : le faire attendre
 *  au tableau de bord, qui est celui qu'on ouvre en arrivant et qu'on garde
 *  ouvert, retardait la première mesure pour rien. Le module part avec
 *  l'interface — il est empaqueté dans `dist`, pas servi par un tiers — donc la
 *  liaison peut être coupée : c'est un fichier de plus sur le même serveur. */
const HistoryChart = lazy(() =>
  import('../components/HistoryChart').then((m) => ({ default: m.HistoryChart })),
)
import { ChartBoundary } from '../components/ChartBoundary'
import { WanCard } from '../components/WanCard'
import { useDevice } from './DeviceShell'
import { SansMesure } from '../components/SansMesure'
import { etiquetteSupport } from '../lib/format'

/**
 * Second niveau : tout le détail d'un lien.
 *
 * Porteuses agrégées, identité de cellule, tunnel et historique — les
 * données qui demandent à être lues, par opposition au premier niveau qui se
 * balaie.
 */
export function WanDetail() {
  const { wanId = '' } = useParams()
  const {
    routerId, samples, showThroughput, hasTunnel, periodSeconds, events, windowLabel,
    sessions, rafraichirSessions, status,
  } = useDevice()
  const sample = samples.find((s) => s.wan.wan_id === wanId)

  if (!sample) {
    return (
      <div className="page">
        <Link className="link-back" to={`/e/${encodeURIComponent(routerId)}`}>
          ‹ Retour aux liens
        </Link>
        {samples.length === 0 ? (
          <SansMesure status={status} sessions={sessions} />
        ) : (
          <div className="notice">Aucun lien « {wanId} » sur cet équipement.</div>
        )}
      </div>
    )
  }

  // L'identifiant, pas le nom configuré : celui-ci contient couramment un numéro
  // de ligne et un ICCID complet, qui débordent du titre sans rien identifier.
  // Le nom complet reste sur la carte du lien, en second plan.
  const titreCourt = `${
    etiquetteSupport(sample.wan.kind)
  } ${sample.wan.wan_id}`

  return (
    <div className="page">
      <Link className="link-back" to={`/e/${encodeURIComponent(routerId)}`}>
        ‹ Retour aux liens
      </Link>
      <WanCard
        sample={sample}
        showThroughput={showThroughput}
        churn={events?.churn?.find((c) => c.wan_id === sample.wan.wan_id) ?? null}
        windowLabel={windowLabel}
      />
      {/* Le repli dit ce qu'il attend, et non « chargement » : sur un poste de
          terrain la seule chose qui puisse retarder ce module est le disque. */}
      <ChartBoundary>
      <Suspense
        fallback={
          <section className="panel">
            <div className="panel-head"><h2>Historique</h2></div>
            <div className="panel-body">
              <div className="notice">Ouverture du tracé…</div>
            </div>
          </section>
        }
      >
        <HistoryChart
          sessions={sessions}
          onRafraichirSessions={rafraichirSessions}
          periodSeconds={periodSeconds}
          routerId={routerId}
          wanId={sample.wan.wan_id}
          wanName={titreCourt}
          kind={sample.wan.kind}
          hasTunnel={hasTunnel}
          episodes={events?.episodes ?? []}
        />
      </Suspense>
      </ChartBoundary>
    </div>
  )
}
