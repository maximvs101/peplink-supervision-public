import { PrevenirToggle } from './PrevenirToggle'
import { ThemeToggle } from './ThemeToggle'
import { Link, useNavigate } from 'react-router-dom'

import type { DeviceLinkStatus, RadioSample, RouterSummary } from '../types'
import { VerdictBanner } from './VerdictBanner'
import type { BondRole } from '../lib/bondRole'

interface Props {
  routerId: string
  router: RouterSummary | null
  routers: RouterSummary[]
  samples: RadioSample[]
  live: boolean
  /** `inconnu` est un état de l'ÉCRAN : l'identifiant ne désigne rien. */
  status: DeviceLinkStatus | 'inconnu'
  ageSeconds: number | null
  error: string | null
  periodSeconds: number | null
  /** Le tunnel est-il le chemin du trafic ? Établi une fois par `DeviceShell`,
   *  qui dispose des deux témoins : ce que le pair annonce, et ce qui transite. */
  role: BondRole
  latencyThresholdMs: number | null
  tunnelAttendu: boolean | null
  /** Ce qui tient le tunnel à terre, lu sur les profils (R-1.5). */
  tunnelCause?: string | null
  neverConnected: string[]
}

/**
 * Le bandeau de tête : qui l'on regarde, et si ça marche.
 *
 * Un seul bandeau, et non deux. La version précédente empilait une barre de
 * marque — « Supervision radio 4G/5G · Équipements Peplink · qualité de
 * liaison » — au-dessus d'une carte de verdict posée dans la page. Le nom du
 * produit n'apprend rien à qui l'a ouvert ; le MATÉRIEL supervisé, si. La barre
 * porte donc l'identité de l'équipement, le verdict en grand, l'âge du relevé,
 * et les deux seules commandes de navigation.
 */
export function Masthead({
  routerId, router, routers, samples, live, status, ageSeconds, error, periodSeconds,
  role, latencyThresholdMs, tunnelAttendu, tunnelCause = null, neverConnected,
}: Props) {
  const navigate = useNavigate()
  const info = router?.device ?? null

  return (
    <header className="mast">
      <div className="mast-id">
        {/* `.mast-id b` tronque par CSS. Sans `title`, un nom long — et les
            noms de WAN de ce parc portent un numéro de ligne et un ICCID
            complet — était coupé sans aucun moyen de le lire en entier. */}
        <b title={info?.model ?? router?.name ?? routerId}>
          {info?.model ?? router?.name ?? routerId}
        </b>
        <span className="num">
          {[info?.serial, info?.firmware].filter(Boolean).join(' · ')
            // « En cours de lecture » seulement si une lecture aura lieu :
            // injoignable ou refusé, l'identité n'est pas lue (C-42).
            || (status === 'connecté' || status === 'en attente'
              ? 'identité en cours de lecture'
              : 'identité non lue')}
        </span>
      </div>

      {/* Rendu en fragment : le verdict et les cachets sont deux cellules de la
          grille, pas un bloc imbriqué. */}
      <VerdictBanner
        key={routerId}
        samples={samples}
        live={live}
        status={status}
        ageSeconds={ageSeconds}
        error={error}
        periodSeconds={periodSeconds}
        role={role}
        latencyThresholdMs={latencyThresholdMs}
        tunnelAttendu={tunnelAttendu}
        tunnelCause={tunnelCause}
        neverConnected={neverConnected}
      />

      <div className="mast-tools">
        <PrevenirToggle />
        <ThemeToggle />
        {/* Le mode n'est PAS global : il est propre à chaque équipement. Un
            routeur réel appairé à chaud s'affichait sous un badge
            « Simulateur » parce que la valeur venait du fichier de config. */}
        {router?.mode === 'mock' && <span className="tag">Simulateur</span>}
        {routers.length > 1 && (
          <select
            value={routerId}
            onChange={(event) => navigate(`/e/${encodeURIComponent(event.target.value)}`)}
            aria-label="Équipement supervisé"
          >
            {/* Un identifiant absent de la liste : sans cette option, le
                navigateur affichait le PREMIER équipement, et le sélecteur
                mentait sur ce que l'écran regardait. */}
            {routers.every((r) => r.id !== routerId) && (
              <option value={routerId} disabled>{routerId} — inconnu</option>
            )}
            {routers.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
        )}
        <Link className="tag" to="/decouverte">Équipements</Link>
      </div>
    </header>
  )
}
