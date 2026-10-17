import type { Alert } from '../types'

/** Le titre d'une alerte, en clair : un seul endroit pour le panneau et pour la
 *  notification du système (A-2). Un genre inconnu — le jour où une règle
 *  naîtra — se dit tel quel plutôt que de disparaître. */
const TITRES: Record<string, (a: Alert) => string> = {
  'hors du bond': (a) => `Lien ${a.wan_id} hors du bond`,
  'liaison sur un seul lien': () => 'La liaison ne tient plus que sur un lien',
  'liaison sans aucun lien': () => 'Plus aucun lien dans le bond',
  'connexions admin refusées': () => 'Connexions admin refusées',
  'horloge du routeur décalée': () => 'Horloges du routeur et du poste en désaccord',
  'supervision interrompue': () => 'La supervision s’est interrompue',
}

export const titreAlerte = (a: Alert): string => TITRES[a.kind]?.(a) ?? a.kind
