import type { Episode } from '../types'

/** Écart toléré entre un geste de l'exploitant et la trace qu'en laisse le
 *  routeur (« Ordre des WAN réévalué ») : vu le 17/10, 12 s avant la
 *  désactivation et 28 s avant le retour. */
const AUTOUR_S = 30

const instant = (e: Episode) => new Date(e.at).getTime()

/** Fenêtre d'un geste dont le retour n'est pas dans la liste. */
const SANS_RETOUR_S = 15 * 60

/** Un geste de l'exploitant — désactiver un lien, puis le voir revenir — en
 *  UNE ligne (A-3). Vu le 17/10 : six lignes pour une SIM désactivée une
 *  minute ; la ligne « désactivation » absorbe, sur le même lien et jusqu'à son
 *  retour, les signalements intermédiaires et le retour lui-même, et les
 *  réévaluations de l'ordre des WAN qui l'entourent. Rien d'autre : une
 *  coupure d'un autre lien, au même moment, reste à sa place.
 *
 *  Rend la liste à afficher et, pour chaque geste, ce qu'il a absorbé. */
export function regrouperGestes(episodes: Episode[]): {
  lignes: Episode[]
  absorbes: Map<Episode, Episode[]>
} {
  const absorbes = new Map<Episode, Episode[]>()
  const pris = new Set<Episode>()
  for (const d of episodes) {
    if (d.kind !== 'désactivation' || pris.has(d)) continue
    const t0 = instant(d)
    const retour = episodes
      .filter((e) => e.kind === 'retour' && e.wan_id === d.wan_id && instant(e) > t0 && !pris.has(e))
      .sort((a, b) => instant(a) - instant(b))[0]
    // Sans retour connu — journal tronqué, ou lien encore désactivé —, la
    // fenêtre s'arrête à un quart d'heure : sinon tout signalement ultérieur du
    // lien se fondait dans le geste (revue du 17/10).
    const t1 = retour ? instant(retour) : t0 + SANS_RETOUR_S * 1000
    const fin = retour ? t1 : t0
    const groupe = episodes.filter((e) => e !== d && !pris.has(e) && (
      e === retour
      || (e.kind === 'signalement' && e.wan_id === d.wan_id && instant(e) > t0 && instant(e) < t1)
      || (e.kind === 'priorités modifiées' && !e.wan_id
          && instant(e) >= t0 - AUTOUR_S * 1000 && instant(e) <= fin + AUTOUR_S * 1000)
    ))
    if (groupe.length === 0) continue
    for (const e of groupe) pris.add(e)
    absorbes.set(d, groupe)
  }
  return { lignes: episodes.filter((e) => !pris.has(e)), absorbes }
}
