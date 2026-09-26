/**
 * À quoi sert le bond de cet équipement ?
 *
 * L'outil supposait que le tunnel SpeedFusion EST le chemin du trafic : le plan
 * annonçait « N chemins physiques réunis en une liaison », le décompte disait
 * « liens qui portent », et la note de redondance concluait que chaque paquet
 * perdu sur un lien était perdu pour le flux. Sur ce déploiement, les trois
 * étaient faux : le tunnel n'a jamais transporté plus de 2,6 kbit/s — un
 * keepalive — quand les interfaces montaient à 262 Mbit/s.
 *
 * LA PREMIÈRE RÈGLE ÉTAIT FAUSSE, ET LE MATÉRIEL L'A DÉMONTRÉ. On lisait les
 * sous-réseaux que le pair annonce, et de l'absence de la route par défaut on
 * concluait qu'« aucun profil n'est sélectionné dans Send All Traffic To ». Le
 * 13 septembre, l'exploitant a activé le trafic SpeedFusion : vingt-six mille
 * paquets ont traversé le tunnel, le débit des interfaces a fondu d'autant — et
 * les routes annoncées par le pair n'ont PAS bougé. La politique de sortie est
 * locale au routeur ; ce que le pair annonce dit ce qu'il sait joindre, pas ce
 * que l'on décide de lui envoyer. L'écran a donc affirmé pendant plusieurs
 * minutes que le bond n'était pas le chemin du trafic, alors qu'il l'était.
 *
 * D'où deux témoins au lieu d'un, et une affirmation qui exige de n'être
 * contredite par aucun. Ce que le pair ANNONCE, et ce qui TRANSITE réellement.
 * Nier que le tunnel porte le trafic est une affirmation forte — elle décide de
 * ce que le verdict a le droit de juger — et elle ne se tient plus dès qu'un
 * débit la dément.
 *
 * UN DÉBIT, ET NON UN COMPTE. Le second témoin a d'abord été un nombre de
 * paquets comparé à une fenêtre de durée libre : un keepalive franchissait mille
 * paquets en douze minutes, et l'affirmation s'effondrait sur toute fenêtre un
 * peu longue. Mesuré sur le matériel, le tunnel au repos du 11 septembre porte
 * 1,33 paquet par seconde et celui en charge du 13 en porte 122,7 — un facteur
 * quatre-vingt-douze, indépendant de la durée observée. C'est cela qu'il fallait
 * regarder.
 */
export type BondRole =
  /** Le pair annonce la route par défaut : le tunnel achemine tout. */
  | { kind: 'tout' }
  /** Il n'annonce que ces sous-réseaux, MAIS du trafic y transite. On ne sait
   *  pas ce qui y est routé ; on sait que ce n'est pas rien. */
  | { kind: 'porte'; routes: string[] }
  /** Il n'annonce que ces sous-réseaux, et rien d'autre n'y transite. */
  | { kind: 'routes'; routes: string[] }
  /** On ne sait pas, et on ne l'invente pas : le pair ne déclare rien, ou il
   *  annonce ces sous-réseaux sans qu'aucun débit du tunnel n'ait été mesuré
   *  récemment — un tunnel tombé n'a plus de compteur. */
  | { kind: 'inconnu'; routes?: string[] }

/** La route par défaut. Sa présence dans les routes du pair prouve que le
 *  tunnel sait joindre n'importe quoi — son absence ne prouve rien. */
const DEFAUT = '0.0.0.0/0'

/** Débit de paquets, en paquets par seconde, au-delà duquel le tunnel porte
 *  autre chose qu'un keepalive.
 *
 *  Mesuré sur le matériel : 1,33 paquet par seconde au repos, 122,7 en charge.
 *  Dix laisse sept fois de marge de chaque côté — et, contrairement à un compte,
 *  la valeur ne dépend pas de la durée de la fenêtre observée. */
export const PAQUETS_PAR_SECONDE_PORTANTS = 10

/** `paquetsParSeconde` à `null` : aucune tranche mesurée dans la fenêtre du
 *  témoin. Nier que le tunnel porte est une affirmation forte ; une absence de
 *  mesure ne la fonde pas (C-2). */
export function bondRole(routes: string[] | undefined, paquetsParSeconde: number | null = null): BondRole {
  if (!routes || routes.length === 0) return { kind: 'inconnu' }
  if (routes.includes(DEFAUT)) return { kind: 'tout' }
  if (paquetsParSeconde === null) return { kind: 'inconnu', routes }
  return paquetsParSeconde >= PAQUETS_PAR_SECONDE_PORTANTS
    ? { kind: 'porte', routes }
    : { kind: 'routes', routes }
}

const memesRoutes = (a: string[], b: string[]) =>
  a.length === b.length && a.every((r, i) => r === b[i])

/** Le rôle à afficher, compte tenu du dernier rôle MESURÉ.
 *
 *  Quand le tunnel tombe, son débit s'éteint avec lui : le témoin ne dit plus
 *  rien. Ce qu'il disait tunnel debout reste la meilleure réponse — un tunnel
 *  qui portait le trafic le portait encore à l'instant de sa chute, et un
 *  tunnel dédié à quelques sous-réseaux ne le devient pas. La mémoire ne vaut
 *  que pour les MÊMES routes annoncées, et une mesure fraîche l'emporte. */
export function roleRetenu(courant: BondRole, precedent: BondRole | null): BondRole {
  if (courant.kind !== 'inconnu' || !courant.routes || precedent === null) return courant
  if ((precedent.kind === 'porte' || precedent.kind === 'routes')
      && memesRoutes(precedent.routes, courant.routes)) return precedent
  return courant
}

/** Le bond porte-t-il le flux ? UNE règle, pour le bandeau et pour le plan.
 *
 *  Seul `routes` dit que non — le pair n'annonce que des sous-réseaux dédiés
 *  et rien d'autre n'y transite. Et un bond qui n'a JAMAIS porté dans cette
 *  session ne porte pas le flux non plus : le serveur l'a tranché
 *  (`tunnel_attendu`). Le verdict appliquait les deux, le plan seulement la
 *  première : sur le MAX HD2, le bandeau disait « jamais établi » et le plan
 *  « 0 / 3 liens qui portent » (C-17).
 *
 *  Prédicat de type : là où il est vrai, `routes` est retiré du rôle, et le
 *  `switch` exhaustif du verdict le sait. */
export function porteLeFlux(
  role: BondRole, tunnelAttendu: boolean | null,
): role is Exclude<BondRole, { kind: 'routes' }> {
  return role.kind !== 'routes' && tunnelAttendu !== false
}
