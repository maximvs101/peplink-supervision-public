import type { WanRadioStatus } from '../types'

/**
 * Un lien EN ATTENTE derrière un autre — ce que l'écran appelle « secours ».
 *
 * L'étiquette était dérivée de la seule priorité : tout WAN de priorité 2 ou
 * plus était « secours ». Sur un HD2 réel, la SIM 5 (priorité 2) portait TOUT
 * le trafic, le WAN 1 de priorité 1 n'ayant pas de câble — et l'écran la disait
 * « secours ». La priorité est une consigne ; l'attente est un fait.
 *
 * Deux témoins du fait, dans cet ordre :
 *   1. le routeur le dit — son message commence par « Standby », le mot du
 *      firmware que `regles_coupure.en_secours` reconnaît côté serveur (même
 *      tolérance : blancs autour, casse ASCII) ;
 *   2. à défaut, un lien de priorité plus haute (numéro plus petit) est
 *      effectivement connecté et non désactivé.
 * Sinon, le lien n'attend derrière rien de connecté : on n'affiche que sa
 * priorité.
 */

const PREFIXE_SECOURS = 'standby'
const ESPACES = /^[ \t\n\r\f\v]+|[ \t\n\r\f\v]+$/g

/** Le routeur dit-il ce lien en attente ? Miroir de `en_secours` (Python). */
export function routeurDitSecours(message: string | null | undefined): boolean {
  if (typeof message !== 'string') return false
  const tete = message.replace(ESPACES, '').slice(0, PREFIXE_SECOURS.length)
  // ASCII seulement, comme SQLite et le serveur.
  return /^[\x00-\x7f]*$/.test(tete) && tete.toLowerCase() === PREFIXE_SECOURS
}

/** Ce lien attend-il derrière un autre ? */
export function enAttente(wan: WanRadioStatus, tous: WanRadioStatus[]): boolean {
  if (routeurDitSecours(wan.status_message)) return true
  const p = wan.priority
  if (p === null) return false
  return tous.some((w) =>
    w.wan_id !== wan.wan_id && !w.disabled && w.connected
    && w.priority !== null && w.priority < p)
}

/** L'étiquette de priorité d'un lien : `null` en priorité 1 ou inconnue ;
 *  « secours · P2 » s'il attend ; « P2 » sinon. */
export function etiquettePriorite(wan: WanRadioStatus, tous: WanRadioStatus[]): string | null {
  if (wan.priority === null || wan.priority <= 1) return null
  return enAttente(wan, tous) ? `secours · P${wan.priority}` : `P${wan.priority}`
}
