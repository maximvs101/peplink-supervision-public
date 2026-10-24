import type { BondProfile, DeviceCapabilities } from '../types'

/**
 * R-1.5 : ce qui tient le tunnel à terre, dit avec les mots du routeur.
 *
 * L'écran écrivait « pair injoignable » — une conclusion que le routeur ne
 * tire pas — et le bandeau recopiait `PEER_DOWN`, une sentinelle de ce projet.
 * Le routeur, lui, publie `START` : il tente de joindre le pair. Seuls les mots
 * OBSERVÉS sont traduits ; les autres sont rendus tels quels, la liste des
 * valeurs possibles n'ayant pas été vue en entier.
 */

type Licence = Pick<DeviceCapabilities, 'sfc_licensed' | 'sfc_suspended' | 'sfc_expires_at'>

const JOUR_MS = 86_400_000
/** Échéance prévenue dans ses 30 derniers jours, pas avant : un avertissement
 *  allumé pendant des mois ne se lit plus. */
const PREAVIS_JOURS = 30

export function motDuPair(etat: string | null): string {
  if (etat === null) return 'aucun pair connecté'
  if (etat === 'START') return 'tentative en cours (START)'
  if (etat === 'CONNECTED') return 'connecté'
  return etat
}

/** Une date du routeur (UTC), au format court français. */
export const dateCourte = (iso: string): string =>
  new Date(iso).toLocaleDateString('fr-FR', { timeZone: 'UTC' })

/** Licence échue ou suspendue : la seule qui puisse nommer une cause. */
function licenceHorsService(licence: Licence | null, maintenant: number): string | null {
  if (licence === null || licence.sfc_licensed !== true) return null
  if (licence.sfc_expires_at !== null && Date.parse(licence.sfc_expires_at) <= maintenant) {
    return `échue le ${dateCourte(licence.sfc_expires_at)}`
  }
  if (licence.sfc_suspended === true) return 'suspendue'
  return null
}

/** La cause, profil par profil, de ceux qui ne tiennent pas. La licence Cloud
 *  n'est accusée que d'un profil Cloud : le FusionHub d'un exploitant n'en
 *  dépend pas. `null` quand aucun profil ne dit rien.
 *
 *  `null` aussi dès qu'un profil TIENT : le pair est alors joignable, et la
 *  panne se lit sur les liens. Le bandeau accusait sinon un profil secondaire
 *  en START, étranger à la panne, et écrasait la cause unanime des liens —
 *  « (WAN_DOWN) » (C-16). */
export function causeDuTunnel(
  profils: BondProfile[], licence: Licence | null, maintenant: number,
): string | null {
  if (profils.some((p) => p.status === 'CONNECTED')) return null
  const causes = profils
    .filter((p) => p.status !== 'CONNECTED')
    .map((p) => {
      const nom = `profil ${p.name ?? p.id}`
      const horsService = p.cloud ? licenceHorsService(licence, maintenant) : null
      if (horsService) return `${nom} : licence SpeedFusion Cloud ${horsService}`
      const pairs = p.peers_connected === null ? '' : `, ${p.peers_connected} pair connecté`
      if (p.status === 'START') return `${nom} : tentative en cours (START)${pairs}`
      return p.status === null ? null : `${nom} : état ${p.status}`
    })
    .filter((c): c is string => c !== null)
  return causes.length ? causes.join(' ; ') : null
}

export type Echeance = { etat: 'proche' | 'echue'; jours: number }

/** L'échéance à dire, ou rien : échue, ou dans les 30 derniers jours. */
export function echeanceLicence(licence: Licence | null, maintenant: number): Echeance | null {
  if (licence === null || licence.sfc_licensed !== true || licence.sfc_expires_at === null) return null
  const reste = Date.parse(licence.sfc_expires_at) - maintenant
  if (reste <= 0) return { etat: 'echue', jours: 0 }
  const jours = Math.ceil(reste / JOUR_MS)
  return jours <= PREAVIS_JOURS ? { etat: 'proche', jours } : null
}
