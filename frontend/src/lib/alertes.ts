import type { Alert } from '../types'

/** Le titre de chaque genre d'alerte, en clair.
 *
 *  Le genre vient du serveur sous forme de mot-clé ; l'écran le traduit ici et
 *  nulle part ailleurs. Un genre inconnu — le jour où une septième règle
 *  naîtra — s'affiche tel quel plutôt que de disparaître en silence.
 *
 *  Un titre court, le fait seul : le détail (lien, valeur) vient du serveur,
 *  l'heure et la durée de l'alerte elle-même. Les paragraphes d'explication qui
 *  suivaient chaque titre — conséquences supposées, conseils, histoire des
 *  règles — ont été retirés à la demande de l'exploitant (« pas
 *  d'interprétation, sois factuel »). */
export const GENRES_ALERTE: Record<string, { titre: (a: Alert) => string }> = {
  'hors du bond': { titre: (a) => `Lien ${a.wan_id} hors du bond` },
  'liaison sur un seul lien': { titre: () => 'Un seul lien dans le bond' },
  // Le BOND, pas la liaison : le trafic peut sortir en direct par les
  // interfaces pendant que le tunnel ne porte plus rien (MAX HD2, concentrateur
  // injoignable) — c'est le bandeau qui dit l'état de la liaison.
  'liaison sans aucun lien': { titre: () => 'Aucun lien dans le bond' },
  'connexions admin refusées': { titre: () => 'Connexions admin refusées' },
  // L'écart se mesure contre ce POSTE : le titre ne désigne pas de fautif.
  'horloge du routeur décalée': { titre: () => 'Écart d’horloge routeur / poste' },
  'supervision interrompue': { titre: () => 'Supervision interrompue' },
}

/** Le titre d'une alerte, en clair : un seul endroit pour le panneau et pour la
 *  notification du système (A-2). Un genre inconnu se dit tel quel plutôt que
 *  de disparaître. */
export const titreAlerte = (a: Alert): string => GENRES_ALERTE[a.kind]?.titre(a) ?? a.kind
