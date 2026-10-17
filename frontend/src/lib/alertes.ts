import type { Alert } from '../types'

/** Ce que chaque genre veut dire, en clair, et ce qu'on va en faire.
 *
 *  Le genre vient du serveur sous forme de mot-clé ; l'écran le traduit ici et
 *  nulle part ailleurs. Un genre inconnu — le jour où une quatrième règle
 *  naîtra — s'affiche tel quel plutôt que de disparaître en silence. */
export const GENRES_ALERTE: Record<string, { titre: (a: Alert) => string; suite: string }> = {
  'hors du bond': {
    titre: (a) => `Lien ${a.wan_id} hors du bond`,
    suite:
      'Ce lien est debout et ne porte plus rien. La cause entre parenthèses est ' +
      'le mot du routeur, pas une déduction.',
  },
  'liaison sur un seul lien': {
    titre: () => 'La liaison ne tient plus que sur un lien',
    suite:
      'L’agrégation ne protège plus de rien : la prochaine perte est une coupure. ' +
      'Cette règle ne s’est jamais déclenchée sur les relevés de référence — elle ' +
      'reste muette jusqu’au jour où elle compte.',
  },
  'liaison sans aucun lien': {
    titre: () => 'Plus aucun lien dans le bond',
    // Le BOND, pas la liaison. Sur le MAX HD2 dont le concentrateur était
    // injoignable, ce titre disait « plus aucun lien ne porte la liaison »
    // sous un bandeau qui disait « portent le trafic en direct » — les deux
    // étaient vrais et se contredisaient, parce que le premier employait le
    // mot de l'autre. Le tunnel ne porte plus rien ; la liaison, peut-être si.
    suite:
      'Tous les liens regardés sont hors du bond : le tunnel ne transporte plus rien, ' +
      'et l’agrégation ne protège plus de rien. Si le trafic sort en direct par les ' +
      'interfaces, la liaison tient — c’est le bandeau qui le dit, pas cette alerte.',
  },
  'connexions admin refusées': {
    titre: () => 'Connexions admin refusées',
    suite:
      'Le journal du routeur compte des refus de connexion à son interface. Cinq en cinq ' +
      'minutes suspendent l’accès admin pendant cinq minutes — la supervision comprise. ' +
      'Ils peuvent venir d’un autre poste : vérifier qui tente de se connecter.',
  },
  'horloge du routeur décalée': {
    titre: () => 'Horloges du routeur et du poste en désaccord',
    // L'écart se mesure contre ce POSTE : le titre ne désigne pas le fautif,
    // le détail le fait quand tous les équipements s'accordent (C-32).
    suite:
      'L’heure du routeur s’écarte de plus d’une minute de celle de ce poste : l’historique '
      + 'qu’il garde ne peut plus combler les trous, et les faits de son journal ne seraient '
      + 'plus rattachés aux coupures. Si tous les équipements montrent le même écart, c’est '
      + 'l’horloge du poste qui dérive ; sinon, un routeur démarré sans accès Internet n’a pas '
      + 'pu régler la sienne : vérifier son serveur de temps (NTP) dans son interface.',
  },
  'supervision interrompue': {
    titre: () => 'La supervision s’est interrompue',
    suite:
      'Pendant ce temps, le routeur a continué de fonctionner sans témoin : ce qui ' +
      's’y est passé n’est ni mesuré ni récupérable. La bande de la fenêtre montre ' +
      'le creux ; ceci le dit.',
  },
}

/** Le titre d'une alerte, en clair : un seul endroit pour le panneau et pour la
 *  notification du système (A-2), avec son explication — deux dictionnaires
 *  séparés devaient être tenus ensemble à la main (revue du 17/10). Un genre
 *  inconnu se dit tel quel plutôt que de disparaître. */
export const titreAlerte = (a: Alert): string => GENRES_ALERTE[a.kind]?.titre(a) ?? a.kind
