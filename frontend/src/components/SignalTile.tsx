import type { Quality } from '../lib/quality'
import { QUALITY_COLOR, QUALITY_LABEL } from '../lib/quality'
import { fmtNumber } from '../lib/format'

interface Props {
  label: string
  value: number | null
  unit: string
  /** Absente quand la grandeur n'a pas de seuils de qualité — un débit, par
   *  exemple : 0 Mbit/s sur un lien au repos est normal, pas mauvais. */
  quality?: Quality
  digits?: number
}

/** Tuile d'une métrique radio, colorée selon sa qualité.
 *
 * Une métrique absente n'est pas une métrique mauvaise : tous les modems ne
 * remontent pas les mêmes champs. Le Dome Pro Duo ne sert pas `status.traffic`
 * et n'a donc aucun débit par WAN ; sa porteuse 5G n78 arrive sans RSSI, quand
 * ses ancres LTE en ont un. Sans valeur, la tuile reste neutre et le dit, au
 * lieu d'afficher un niveau de qualité qui ne repose sur rien.
 *
 * L'exemple cité ici était FAUX et prenait justement le contre-pied de la
 * mesure : il donnait le MAX HD4 MBX comme ne fournissant aucun débit par WAN,
 * alors que 9 941 de ses 12 130 relevés cellulaires en portent un, jusqu'à
 * 59 Mbit/s descendants. C'est la capacité `per_wan_throughput`, constatée à
 * l'exécution selon que l'équipement réponde ou non, qui décide d'afficher ces
 * tuiles — jamais un test sur la valeur, ni une croyance sur le modèle.
 */
export function SignalTile({ label, value, unit, quality, digits = 1 }: Props) {
  const missing = value === null
  // Sans seuils, aucune note : mieux vaut ne rien dire que d'affirmer
  // « Excellent » sous une valeur qui ne se juge pas.
  const graded = !missing && quality !== undefined

  return (
    <div
      className="tile"
      style={{ ['--q' as string]: graded ? QUALITY_COLOR[quality] : 'var(--muted)' }}
    >
      <div className="tile-label">{label}</div>
      <div className="tile-value">
        {missing ? '—' : fmtNumber(value, digits)}
        {!missing && <span className="unit">{unit}</span>}
      </div>
      <div className="tile-grade">
        {missing ? 'non fourni' : graded ? QUALITY_LABEL[quality] : '\u00a0'}
      </div>
    </div>
  )
}
