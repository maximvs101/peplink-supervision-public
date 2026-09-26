/**
 * Seuils de qualité radio LTE/5G.
 *
 * Valeurs usuelles de l'ingénierie radio :
 *  - RSRP (dBm) : puissance du signal de référence reçu
 *  - RSRQ (dB)  : qualité du signal de référence
 *  - SINR (dB)  : rapport signal / (interférence + bruit) — le plus parlant pour le débit
 */

export type Quality = 'excellent' | 'bon' | 'moyen' | 'faible' | 'inconnu'

/**
 * LA famille chromatique de l'interface, et la seule.
 *
 * Cinq notes, cinq teintes, aucune autre couleur ailleurs : ni pour l'accent, ni
 * pour les séries de graphe, ni pour les états d'équipement — ceux-ci
 * empruntent ici. La version précédente comptait vingt-sept valeurs distinctes
 * dont dix-neuf reprises telles quelles d'un framework CSS, avec deux rouges
 * pour un même fait à un clic d'écart et le même violet pour le RSRP et pour le
 * débit montant.
 *
 * Les valeurs vivent dans la feuille de style (`--q-*`), une par thème : le
 * vert calibré pour le fond sombre ferait 2:1 sur fond clair (R-0.1). Leurs
 * contrastes — ≥ 4,5:1 sur chaque fond, dans les deux thèmes — sont tenus par
 * `lib/theme.test.ts`. `inconnu` avait été à 3,58:1 — l'état « donnée
 * absente » était le texte le moins lisible de l'écran, ce qui contredit le
 * principe qu'une donnée manquante ne doit pas passer pour une donnée mauvaise.
 */
export const QUALITY_COLOR: Record<Quality, string> = {
  excellent: 'var(--q-excellent)',
  bon: 'var(--q-bon)',
  moyen: 'var(--q-moyen)',
  faible: 'var(--q-faible)',
  inconnu: 'var(--q-inconnu)',
}

export const QUALITY_LABEL: Record<Quality, string> = {
  excellent: 'Excellent',
  bon: 'Bon',
  moyen: 'Moyen',
  faible: 'Faible',
  inconnu: '—',
}

const grade = (
  value: number | null | undefined,
  excellent: number,
  bon: number,
  moyen: number,
): Quality => {
  // `Number.isFinite` et non `!Number.isNaN` : un ±Infinity — né d'une division
  // dont le dénominateur manquait — passait la garde et ressortait « excellent »
  // pour +∞, « faible » pour −∞. Une valeur non finie n'est pas une mesure.
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'inconnu'
  if (value >= excellent) return 'excellent'
  if (value >= bon) return 'bon'
  if (value >= moyen) return 'moyen'
  return 'faible'
}

/**
 * Les seuils, et ils sont un CHOIX, pas une norme.
 *
 * Il n'existe aucune convention partagée : entre quatre constructeurs majeurs,
 * le RSRP dit « excellent » va de −80 à −90 dBm et le SINR de 10 à 20 dB — et
 * deux pages du même éditeur se contredisent de 5 dB. Les afficher sans le dire
 * reviendrait à présenter une décision éditoriale comme une vérité mesurée.
 * `QUALITY_THRESHOLDS` existe pour que l'interface les publie.
 */
/** Ce que l'interface affiche à l'exploitant pour qu'il sache sur quoi elle se
 *  fonde — et la SEULE déclaration de ces nombres.
 *
 *  Ils vivaient en deux exemplaires : une fois dans les appels à `grade`, une
 *  fois dans cette table, sans rien qui les relie. La docstring jurait pourtant
 *  qu'ils n'étaient « recopiés nulle part ailleurs ». Changer un seuil de
 *  jugement laissait l'écran publier l'ancien, et c'est très exactement le
 *  défaut « un même seuil en deux exemplaires » que ce projet a payé trois fois
 *  ailleurs. Les fonctions de notation les lisent désormais ici. */
export const QUALITY_THRESHOLDS: {
  key: string
  unit: string
  bornes: [number, number, number]
}[] = [
  { key: 'SINR', unit: 'dB', bornes: [20, 13, 5] },
  { key: 'RSRP', unit: 'dBm', bornes: [-85, -95, -105] },
  { key: 'RSRQ', unit: 'dB', bornes: [-10, -13, -16] },
  // Le Wi-Fi amont a ses propres seuils, distincts du cellulaire. Il n'était
  // publié nulle part alors qu'il est appliqué à la colonne RSSI.
  { key: 'Wi-Fi', unit: 'dBm', bornes: [-55, -65, -75] },
]

const bornes = (key: string): [number, number, number] =>
  QUALITY_THRESHOLDS.find((s) => s.key === key)!.bornes

export const rsrpQuality = (v: number | null) => grade(v, ...bornes('RSRP'))
export const rsrqQuality = (v: number | null) => grade(v, ...bornes('RSRQ'))
export const sinrQuality = (v: number | null) => grade(v, ...bornes('SINR'))

/** Force d'un lien Wi-Fi amont (dBm) — seuils usuels du Wi-Fi, distincts du cellulaire. */
export const wifiQuality = (v: number | null) => grade(v, ...bornes('Wi-Fi'))

// `metricQuality` vivait ici : une table qui associait un nom de grandeur à sa
// fonction de notation. AUCUN appelant — la table des liens appelle les
// fonctions directement — et elle portait une quatrième règle latente, le RSSI
// jugé aux seuils du Wi-Fi y compris sur un lien cellulaire, dont le RSSI vaut
// ici −68 dBm, soit « moyen » à ce barème.

/**
 * LA note d'un lien, quel que soit son support — et le seul endroit où ce
 * choix se fait.
 *
 * La règle vivait en QUATRE exemplaires (`WanCard`, `LinkTable`, `Band`,
 * `BondPlan`), et ils ne disaient pas la même chose : `Band` notait un lien
 * Wi-Fi « inconnu » là où les trois autres appelaient `wifiQuality`.
 *
 * Un lien filaire y valait « excellent ». C'est une mesure inventée : une fibre
 * n'a ni RSRP, ni SINR, ni rien à noter — elle est connectée ou elle ne l'est
 * pas, et `verdict.ts` l'écrit en toutes lettres avant d'exclure les liens
 * filaires de son compte des liens faibles. Afficher « excellent » revenait à
 * décerner la meilleure note à une absence de mesure, quand tout le reste du
 * projet refuse de décerner la pire. L'état de connexion est porté ailleurs,
 * par sa propre couleur : celle-ci ne dit que la radio.
 */
export function linkQuality(
  wan: { kind: string; rsrp: number | null; rsrq: number | null; sinr: number | null;
         rssi: number | null },
): Quality {
  if (wan.kind === 'ethernet') return 'inconnu'
  if (wan.kind === 'wifi') return wifiQuality(wan.rssi)
  return overallQuality(wan.rsrp, wan.rsrq, wan.sinr)
}

/** Qualité globale d'un WAN : on retient le maillon le plus faible. */
export function overallQuality(
  rsrp: number | null,
  rsrq: number | null,
  sinr: number | null,
): Quality {
  const order: Quality[] = ['faible', 'moyen', 'bon', 'excellent']
  const grades = [rsrpQuality(rsrp), rsrqQuality(rsrq), sinrQuality(sinr)].filter(
    (g) => g !== 'inconnu',
  )
  if (grades.length === 0) return 'inconnu'
  return grades.reduce((worst, g) => (order.indexOf(g) < order.indexOf(worst) ? g : worst))
}

/** La teinte d'un lien, UNE règle pour le plan, la table et la carte.
 *
 *  Un lien désactivé par l'exploitant n'est pas tombé : c'est une décision.
 *  La couleur ne dépendait que de `connected`, et un WAN désactivé se traçait
 *  en rouge partout — on cherchait une panne qui n'existait pas (C-20). Il
 *  prend la teinte de l'inconnu : on ne le juge pas. */
export function couleurDuLien(
  wan: Parameters<typeof linkQuality>[0] & { connected: boolean; disabled?: boolean },
): string {
  if (wan.disabled) return QUALITY_COLOR.inconnu
  return wan.connected ? QUALITY_COLOR[linkQuality(wan)] : QUALITY_COLOR.faible
}
