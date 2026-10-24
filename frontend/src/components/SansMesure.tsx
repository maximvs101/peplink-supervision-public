import { Link } from 'react-router-dom'
import type { DeviceLinkStatus, Session } from '../types'

/**
 * Ce que dit un écran d'équipement quand aucun relevé n'est là.
 *
 * Le libellé suit l'ÉTAT, pas seulement l'absence de relevé : « première
 * mesure en cours… » sous un bandeau « Équipement injoignable » promettait une
 * mesure qui ne viendrait pas (vu sur un MBX réel, d'abord sur la vue
 * d'ensemble, puis le 30/09 sur le détail d'un lien, qui avait gardé l'ancien
 * libellé).
 *
 * Et les stationnements déjà enregistrés restent atteignables : leur seul
 * accès était l'historique d'un lien, qu'on n'ouvre pas sans relevé en cours —
 * un équipement éteint gardait ses rapports en base sans chemin vers eux.
 */
export function SansMesure({ status, sessions }: { status: DeviceLinkStatus; sessions: Session[] }) {
  return (
    <>
      <div className="notice">
        {status === 'connecté' || status === 'en attente'
          ? 'Connexion et première mesure en cours…'
          : status === 'identifiants refusés'
            // Un refus n'est pas un silence : « ne répond pas » envoyait
            // chercher une panne de réseau (C-42).
            ? 'Aucune mesure — identifiants refusés.'
            : 'Aucune mesure — équipement injoignable.'}
      </div>
      {sessions.length > 0 && (
        <div className="notice">
          Stationnements enregistrés :{' '}
          {sessions.map((s, i) => (
            <span key={s.id}>
              {i > 0 ? ' · ' : ''}
              <Link to={`/rapport/${s.id}`} target="_blank" rel="noreferrer">
                {s.label ?? new Date(s.started_at).toLocaleString('fr-FR')}
              </Link>
            </span>
          ))}
        </div>
      )}
    </>
  )
}
