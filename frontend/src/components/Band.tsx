import type { RadioSample, Timeline, TimelineSlice } from '../types'
import type { Quality } from '../lib/quality'
import { QUALITY_COLOR, linkQuality, rsrpQuality, sinrQuality } from '../lib/quality'

/** Sous ce taux de relevés portants, la tranche est dite « écartée du bond ».
 *  Pas un seuil de confort : au-dessous, le lien n'a transporté qu'une fraction
 *  de la tranche, et le dire « dans le bond » masquerait la coupure. */
const PORTE = 0.5

/** Le lien PORTAIT-il pendant cette tranche ? Jugé sur les relevés où le tunnel
 *  a été SONDÉ, et sur eux seuls : la sonde du tunnel est espacée sur un
 *  équipement sans pair, et 36 % des cycles ne la portent pas (mesuré). Diviser
 *  par tous les relevés faisait tomber sous la moitié un lien qui portait à
 *  chaque sonde — « hors du bond » pour une absence de regard. Sans une seule
 *  sonde, on ne sait pas, et l'on ne dessine rien. */
export function tranchePorte(tr: TimelineSlice): boolean | null {
  if (tr.tunnel_known <= 0) return null
  return tr.carrying / tr.tunnel_known >= PORTE
}

/** Le fond d'une tranche, et si elle dit une CHUTE. Une tranche dont tous les
 *  relevés tombés sont des désactivations (C-20) n'est pas une panne : le WAN a
 *  été éteint par l'exploitant, et son relevé d'adieu, `connected = 0`, ne se
 *  peint pas du rouge d'une défaillance. */
export function fondDeTranche(tr: TimelineSlice, q: Quality): { fond: string; tombe: boolean } {
  if (tr.up > 0) return { fond: QUALITY_COLOR[q], tombe: false }
  const tombes = tr.samples - tr.disabled
  return tombes > 0
    ? { fond: QUALITY_COLOR.faible, tombe: true }
    : { fond: QUALITY_COLOR.inconnu, tombe: false }
}

/**
 * La bande de temps d'UN lien, pour la colonne qui la porte.
 *
 * Elle vivait dans un panneau séparé, « Stationnement en cours », qui répétait
 * exactement les lignes de la table des liens — une par lien, dans le même
 * ordre, avec la même identité à gauche. Deux panneaux pour un seul objet : la
 * bande est désormais une COLONNE de la table, et l'écran y gagne la hauteur
 * d'un panneau entier.
 *
 * Deux informations s'y superposent sans se confondre. Le fond dit la QUALITÉ
 * radio ; le ruban du bas dit si le lien PORTAIT. Un lien peut être parfaitement
 * connecté côté WAN et écarté du bond, et rien dans l'état du WAN ne le dit.
 *
 * Les tranches absentes restent vides, et c'est une information : le superviseur
 * ne regardait pas. Les combler referait le mensonge que tout ce projet combat.
 */
export function LinkBand({
  sample, slices, total,
}: {
  sample: RadioSample
  slices: TimelineSlice[]
  total: number
}) {
  const isWifi = sample.wan.kind === 'wifi'
  const isWired = sample.wan.kind === 'ethernet'

  return (
    <span className="track">
      {slices.map((tr) => {
        const large = 100 / total
        const gauche = (tr.t * 100) / total
        // La tranche porte les PIRES niveaux de sa fenêtre, pas le relevé
        // courant : sur un lien cellulaire c'est `pire(tr)` qui note. Pour les
        // autres supports, la règle commune — et un filaire n'a rien à noter.
        const q: Quality = isWired || isWifi ? linkQuality(sample.wan) : pire(tr)
        const { fond, tombe } = fondDeTranche(tr, q)
        // Le tunnel n'était pas lisible : on ne dessine pas de ruban plutôt que
        // d'affirmer que le lien était écarté.
        const sondee = tranchePorte(tr)
        const connu = sondee !== null
        const porte = sondee === true
        return (
          <span key={tr.t}>
            <i
              style={{
                left: `${gauche}%`,
                width: `calc(${large}% + 1px)`,
                background: fond,
                opacity: tombe ? 0.5 : 0.28,
              }}
            />
            {connu && (
              <b
                style={{
                  left: `${gauche}%`,
                  width: `calc(${large}% + 1px)`,
                  background: porte ? fond : 'transparent',
                  borderTop: porte ? 'none' : `1px dashed ${QUALITY_COLOR.inconnu}`,
                }}
              />
            )}
          </span>
        )
      })}
      {/* `left: 100%` plaçait le repère JUSTE au-delà du bord, et `.track` est
          en `overflow: hidden` : il n'a jamais été visible. Le dernier pixel de
          la piste est l'instant courant — c'est là qu'il doit se poser. */}
      <span className="now" />
    </span>
  )
}

/** Un moment où la redondance a fléchi : moins de liens dans le bond qu'il n'y
 *  en a, pendant une durée, avec le pire compte atteint. */
export interface Fléchissement {
  /** Début, en ISO — daté par le SERVEUR, comme tout le reste. */
  at: string
  seconds: number
  /** Le moins de liens simultanés observé pendant cet épisode. */
  worst: number
}

/**
 * Ce que les liens ont fait ENSEMBLE, dit en toutes lettres.
 *
 * Trois encodages visuels ont échoué à se faire comprendre : une surface pleine
 * qui dépensait toute son encre à dire « rien à signaler » — 97,2 % de la
 * fenêtre du 11 septembre à cinq liens sur cinq —, puis un dessin en creux qui
 * ne laissait rien à voir quand tout allait bien, puis un trait gradué dont
 * l'axe n'était écrit nulle part.
 *
 * Le tort était la forme, pas le style. L'information est RARE et ÉPISODIQUE, et
 * elle tient en une phrase : à quelle heure, pendant combien de temps, combien
 * de liens. Sur 196 px pour huit heures, un incident réel faisait trois pixels
 * et le lecteur devait deviner une échelle qu'on ne lui donnait pas ; en toutes
 * lettres, il n'y a rien à décoder et le cas normal — le plus fréquent — se dit
 * mieux que tout : « les cinq liens, toute la fenêtre ».
 */
export function bondTogether(
  timeline: Timeline | null,
  /** Nombre de liens QUI PEUVENT être dans le bond — ceux qui portent un objet
   *  tunnel. Compter tous les liens faisait d'un WAN filaire hors du profil un
   *  fléchissement permanent : « plus aucun lien dans le bond » sur toute la
   *  session, pour un lien qui n'y a jamais eu sa place. */
  links: number,
): { fléchissements: Fléchissement[]; observé: number } | null {
  if (timeline === null || links === 0) return null

  // Deux comptes par tranche : combien de liens ont RÉPONDU, et combien étaient
  // dans le bond. Sans le premier, une tranche qu'un modem n'a pas renseignée se
  // lirait comme un lien tombé — on ferait d'un silence du superviseur une perte
  // de redondance.
  const vus = new Map<number, number>()
  const dedans = new Map<number, number>()
  for (const lane of timeline.lanes) {
    for (const s of lane.slices) {
      vus.set(s.t, (vus.get(s.t) ?? 0) + 1)
      if (tranchePorte(s) === true) {
        dedans.set(s.t, (dedans.get(s.t) ?? 0) + 1)
      }
    }
  }
  const lanes = timeline.lanes.length
  const tranches = [...vus.keys()]
    .filter((t) => (vus.get(t) ?? 0) >= lanes)
    .sort((a, b) => a - b)
  if (tranches.length === 0) return null

  const debut = new Date(timeline.started_at).getTime()
  const pas = timeline.slice_seconds
  const fléchissements: Fléchissement[] = []
  let courant: { premiere: number; derniere: number; worst: number } | null = null

  const fermer = () => {
    if (courant === null) return
    fléchissements.push({
      at: new Date(debut + courant.premiere * pas * 1000).toISOString(),
      // La durée COUVRE la dernière tranche : d'une tranche à elle-même il s'est
      // écoulé un pas, pas zéro seconde.
      seconds: (courant.derniere - courant.premiere + 1) * pas,
      worst: courant.worst,
    })
    courant = null
  }

  for (const t of tranches) {
    const n = dedans.get(t) ?? 0
    if (n >= links) { fermer(); continue }
    // Une tranche non contiguë ouvre un nouvel épisode : entre les deux, on ne
    // regardait pas, et souder par-dessus le trou inventerait une durée.
    if (courant !== null && t !== courant.derniere + 1) fermer()
    if (courant === null) courant = { premiere: t, derniere: t, worst: n }
    else { courant.derniere = t; courant.worst = Math.min(courant.worst, n) }
  }
  fermer()

  return { fléchissements, observé: tranches.length / timeline.slice_count }
}

/** Repères d'heure répartis sur la fenêtre. */
export function bandTicks(timeline: Timeline | null, n = 5): string[] {
  if (timeline === null) return []
  const debut = new Date(timeline.started_at).getTime()
  const span = new Date(timeline.ended_at).getTime() - debut
  return Array.from({ length: n }, (_, i) =>
    new Date(debut + (span * i) / (n - 1)).toLocaleTimeString('fr-FR', {
      hour: '2-digit',
      minute: '2-digit',
    }),
  )
}

/** Tranches qu'AUCUN lien n'a renseignées : le superviseur ne regardait pas.
 *  C'est un fait sur l'outil, pas sur la liaison. */
export function bandGaps(timeline: Timeline | null): number {
  if (timeline === null) return 0
  const vues = new Set<number>()
  for (const lane of timeline.lanes) for (const s of lane.slices) vues.add(s.t)
  return timeline.slice_count - vues.size
}

/** La note d'une tranche : la PIRE des grandeurs mesurées, jamais leur moyenne.
 *  Une tranche sans aucune mesure radio reste « inconnu » — pas « faible » : une
 *  absence de mesure n'est pas une mauvaise mesure. */
function pire(tr: TimelineSlice) {
  const notes = [
    tr.sinr_worst === null ? null : sinrQuality(tr.sinr_worst),
    tr.rsrp_worst === null ? null : rsrpQuality(tr.rsrp_worst),
  ].filter((n): n is Exclude<ReturnType<typeof sinrQuality>, never> => n !== null)
  if (notes.length === 0) return 'inconnu' as const
  const rang = { excellent: 0, bon: 1, moyen: 2, faible: 3, inconnu: 4 }
  return notes.reduce((a, b) => (rang[b] > rang[a] ? b : a))
}
