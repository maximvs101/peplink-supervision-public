/** Les réglages de l'historique d'un lien, portés par l'ADRESSE.
 *
 *  `#/e/<id>/wan/<lien>?grandeur=rsrp&fenetre=180&session=12` : la grandeur, la
 *  fenêtre glissante (en minutes) et la session d'alimentation choisies. Dans
 *  l'adresse plutôt que dans l'état du composant, pour qu'un lien collé dans un
 *  message rouvre la même courbe, et que « Précédent » rende le réglage d'avant
 *  au lieu de quitter la fiche. La requête suit le chemin de la fiche du lien,
 *  comme le veut le routage par fragment (`HashRouter`) : le chemin dit QUOI,
 *  la requête dit COMMENT on le regarde.
 *
 *  L'adresse est une ENTRÉE non fiable — un lien ancien, tronqué, retapé à la
 *  main : chaque valeur est lue telle quelle ici, puis validée par l'écran
 *  contre ce qu'il sait proposer, et une valeur inconnue retombe sur le défaut
 *  sans erreur. */

export interface ReglageBrut {
  grandeur: string | null
  /** Minutes de la fenêtre glissante. */
  fenetre: number | null
  session: number | null
}

/** Un entier strictement positif, ou rien : « 12abc », « -3 », « 1e3 » ne sont
 *  pas des identifiants. */
const entier = (v: string | null): number | null =>
  v !== null && /^[1-9]\d{0,9}$/.test(v) ? Number(v) : null

export const lireReglage = (params: URLSearchParams): ReglageBrut => ({
  grandeur: params.get('grandeur') || null,
  fenetre: entier(params.get('fenetre')),
  session: entier(params.get('session')),
})

/** Les réglages EFFECTIFS, au format de l'adresse. Une valeur égale au défaut
 *  n'y figure pas : l'adresse d'une fiche jamais réglée reste nue, et deux
 *  écritures d'un même réglage donnent la même adresse. Les autres paramètres
 *  éventuels sont conservés. */
export function ecrireReglage(
  params: URLSearchParams,
  r: { grandeur: string; fenetre: number; session: number | null },
  defauts: { grandeur: string; fenetre: number },
): URLSearchParams {
  const sortie = new URLSearchParams(params)
  const poser = (cle: string, valeur: string | null) => {
    if (valeur === null) sortie.delete(cle)
    else sortie.set(cle, valeur)
  }
  poser('grandeur', r.grandeur === defauts.grandeur ? null : r.grandeur)
  poser('fenetre', r.fenetre === defauts.fenetre ? null : String(r.fenetre))
  poser('session', r.session === null ? null : String(r.session))
  return sortie
}
