/** Les chemins de l'application, construits au même endroit que leur forme.
 *
 *  `/e/:routerId/wan/:wanId` était assemblé à la main à trois endroits, dont
 *  deux dans le même composant : le gestionnaire de clic et celui du clavier.
 *  Deux copies d'une même URL dans une même fonction peuvent diverger sans que
 *  l'œil le voie, et un renommage de route devait en retrouver trois.
 */

/** La fiche d'un lien. Les deux identifiants viennent du routeur et peuvent
 *  contenir n'importe quoi : ils sont échappés ici, pas chez l'appelant. */
export const lienWan = (routerId: string, wanId: string): string =>
  `/e/${encodeURIComponent(routerId)}/wan/${encodeURIComponent(wanId)}`

/** L'IDENTITÉ d'un hôte, pour comparer deux graphies — miroir de
 *  `hote_canonique` côté serveur.
 *
 *  Depuis que la découverte rend `http://10.x` pour un équipement sans TLS,
 *  `10.x`, `http://10.x` et `http://10.x/` désignent le même châssis, et trois
 *  comparaisons de chaînes le manquaient : l'écran proposait d'appairer un
 *  équipement déjà suivi, ou le disait « absent du réseau » alors qu'il figurait
 *  dans la table des détectés une section plus haut. Le schéma n'entre pas dans
 *  l'identité : c'est une manière de joindre l'équipement, pas l'équipement. */
export const hoteCanonique = (host: string | null | undefined): string | null => {
  if (!host) return null
  // Le schéma AVANT le slash final : dans l'autre ordre, `http://` seul
  // devenait `http:` — une identité pour un hôte qui n'en est pas un.
  const nu = host.trim().replace(/^https?:\/\//i, '').replace(/\/+$/, '').toLowerCase()
  return nu || null
}

/** L'hôte parle-t-il en clair ? C'est ce que le formulaire d'appairage doit
 *  dire AVANT qu'on y tape un mot de passe. */
export const sansTls = (host: string | null | undefined): boolean =>
  /^http:\/\//i.test((host ?? '').trim())
