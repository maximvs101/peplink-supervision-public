/** Fraîcheur d'une PARTIE d'écran : l'état de l'équipement, la liste des
 *  sessions, la bande, le journal, la fiche du stationnement.
 *
 *  Chacune se relit à sa cadence et peut échouer seule. Les échecs de la bande
 *  et du journal étaient muets — `setTimeline(null)` effaçait la bande au
 *  premier hoquet, sans un mot — et une partie restée sur une donnée ancienne ne
 *  se distinguait pas d'une partie fraîche. La règle : garder la dernière donnée
 *  valide, et dire son âge QUAND il compte, pas en permanence. Un compteur qui
 *  tourne sous chaque panneau est du papier peint : on cesse de le lire, et on
 *  cesse de le lire aussi le jour où il dit quelque chose. */

export interface Fraicheur {
  /** Instant de la dernière réponse ABOUTIE, en horloge monotone
   *  (`performance.now`) : une mise à l'heure du poste ne doit ni rajeunir ni
   *  vieillir une donnée. `null` tant qu'aucune n'a abouti. */
  lueA: number | null
  /** Cause du dernier échec, levée par la réponse aboutie suivante. */
  echec: string | null
  /** Période de relecture de cette partie, en millisecondes. */
  periodeMs: number
}

/** Au-delà de combien de périodes sans réponse une donnée est dite ancienne.
 *  Deux : une relecture manquée peut n'être qu'une réponse lente, deux de suite
 *  ne le sont plus. */
export const PERIODES_AVANT_ANCIEN = 2

export const fraicheurNeuve = (periodeMs: number): Fraicheur =>
  ({ lueA: null, echec: null, periodeMs })

/** Faut-il le dire ? Oui sur un échec, oui sur une donnée plus vieille que deux
 *  périodes ; non sinon — et non tant que la toute première lecture est en
 *  route sans avoir échoué : il n'y a rien d'ancien à signaler. */
export const aSignaler = (f: Fraicheur, maintenant: number): boolean =>
  f.echec !== null
  || (f.lueA !== null && maintenant - f.lueA > PERIODES_AVANT_ANCIEN * f.periodeMs)

/** « 45 s », « 3 min », « 2 h 05 » : l'ordre de grandeur suffit, la seconde
 *  près n'a de sens que sur les deux premières minutes. */
export function duree(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 120) return `${s} s`
  const min = Math.floor(s / 60)
  if (min < 120) return `${min} min`
  const h = Math.floor(min / 60)
  return `${h} h ${String(min % 60).padStart(2, '0')}`
}

/** La phrase, ou `null` quand il n'y a rien à dire. */
export function phraseFraicheur(nom: string, f: Fraicheur, maintenant: number): string | null {
  if (!aSignaler(f, maintenant)) return null
  const age = f.lueA === null
    ? 'aucune réponse obtenue'
    : `dernière réponse il y a ${duree(maintenant - f.lueA)}`
  const cause = f.echec !== null ? ` — dernière relecture en échec : ${f.echec}` : ''
  const suite = f.lueA === null ? '' : '. Ce qui est affiché date de cette réponse'
  return `${nom} : ${age}${cause}${suite}.`
}
