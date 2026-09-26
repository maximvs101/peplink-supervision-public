/**
 * Tri naturel des identifiants de WAN.
 *
 * Les identifiants réels sont numériques et un châssis peut en exposer plus de
 * neuf — le MAX HD4 MBX en déclare onze. Un tri lexicographique y placerait
 * « 10 » avant « 2 ». Les identifiants du simulateur (« cellular-1 ») restent
 * triés par texte, après les numériques.
 */
export const wanOrder = (id: string): [number, number, string] =>
  /^\d+$/.test(id) ? [0, Number(id), ''] : [1, 0, id]

export function compareWanIds(a: string, b: string): number {
  const [ka, na, sa] = wanOrder(a)
  const [kb, nb, sb] = wanOrder(b)
  return ka - kb || na - nb || sa.localeCompare(sb)
}
