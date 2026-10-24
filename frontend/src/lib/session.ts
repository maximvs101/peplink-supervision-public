import { fmtClock } from './format'
import type { Session } from '../types'

/** Au-delà de cet écart entre l'allumage et le premier relevé, le libellé dit
 *  les deux instants. En deçà, c'est une période de scrutation : le superviseur
 *  regardait « depuis la mise sous tension », à un cycle près. */
const ECART_DIT_S = 120

/** Relecture de la fiche d'un stationnement EN COURS : la tuile du volume du
 *  tableau de bord (A-5) et la fiche de l'historique suivent le même rythme.
 *  La fiche relit toute la session (24 h au plus) pour des chiffres qui
 *  bougent peu : à la cadence du journal, c'était une lecture lourde toutes les
 *  trente secondes (revue du 17/10). */
export const RESUME_MS = 5 * 60_000

/** La borne gauche de ce qui se DESSINE pour une session : le premier relevé.
 *
 *  La session commence à l'allumage, déduit de l'uptime. Mais l'outil est
 *  souvent lancé bien après — deux heures dix-huit sur le MAX HD2 du 19
 *  septembre — et entre les deux il n'y a rien à tracer : l'axe donnait quarante
 *  pour cent de sa largeur à un vide, et les deux cents tranches de la bande se
 *  répartissaient sur une durée dont un tiers ne pouvait rien porter. Faute de
 *  relevé rattaché, l'allumage reste la seule borne connue. */
export const debutObserve = (session: Session): string =>
  session.first_seen ?? session.started_at

/** Ce que la fenêtre relue couvre, en clair. Quand la supervision a commencé
 *  nettement après l'allumage, le libellé dit les deux heures : le lecteur
 *  saurait sinon que l'axe part de 10:53 sans savoir que le véhicule était sous
 *  tension depuis 08:35 — et c'est précisément l'information que l'ancien axe
 *  portait, en pixels vides. */
export const libelleFenetre = (session: Session): string => {
  if (session.first_seen === null) return 'Depuis la mise sous tension'
  const ecartS = (Date.parse(session.first_seen) - Date.parse(session.started_at)) / 1000
  if (!(ecartS > ECART_DIT_S)) return 'Depuis la mise sous tension'
  return `Depuis ${fmtClock(session.first_seen)} — sous tension depuis ${fmtClock(session.started_at)}`
}
