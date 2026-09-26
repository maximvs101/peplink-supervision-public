import type { BandMeasurement } from '../types'
import { QUALITY_COLOR, rsrpQuality, sinrQuality } from '../lib/quality'
import { fmtId, fmtNumber } from '../lib/format'

/**
 * Détail des porteuses agrégées (carrier aggregation).
 *
 * Le modem agrège plusieurs porteuses simultanément : en 5G NSA, une porteuse 5G qui
 * porte les données et une ou plusieurs ancres LTE. Chacune a ses propres niveaux —
 * une ancre LTE peut être excellente alors que la 5G est faible, ce qui explique un
 * débit décevant malgré un affichage « 5G ». D'où ce détail, utile au réglage.
 *
 * Il faut au moins DEUX porteuses pour qu'il y ait quelque chose à comparer. Sur une
 * porteuse unique, la ligne ne fait que répéter les tuiles du dessus — mêmes RSRP,
 * RSRQ et SINR, puisque c'est d'elle que le backend les tire comme porteuse
 * principale — sous un titre « Porteuses agrégées » qui annonce une agrégation
 * inexistante. On ne rend alors rien du tout.
 *
 * Les colonnes « Largeur » et « PCI » n'apparaissent que si au moins une porteuse les
 * renseigne. Le Telit LM960 du MAX HD4 MBX ne publie ni l'une ni l'autre — il renvoie
 * `pci: 0` sur toutes ses porteuses, absence que le backend traduit en null, et
 * aucune largeur de canal — et deux colonnes entièrement remplies de tirets se
 * lisent comme des mesures perdues. Le Dome Pro Duo, lui, les sert vraiment : quatre
 * PCI distincts et des largeurs de 15 à 90 MHz sur le même relevé. Effacer ces
 * colonnes partout aurait aveuglé le seul modèle qui les mesure.
 */
export function BandTable(
  { bands, primaire, canal = null }:
  { bands: BandMeasurement[]; primaire: string | null; canal?: string | null },
) {
  if (bands.length < 2) return null

  // La principale se reconnaît à sa bande ET à son canal. Par la seule bande,
  // deux porteuses B3 agrégées étaient marquées « principale » toutes les
  // deux (C-44). Sans canal connu, on ne départage que si la bande est unique.
  const memeBande = bands.filter((b) => b.band === primaire).length
  const estPrincipale = (b: BandMeasurement) =>
    primaire !== null && b.band === primaire
    && (canal !== null ? String(b.channel) === String(canal) : memeBande === 1)

  const hasWidth = bands.some((band) => band.channel_width !== null)
  const hasPci = bands.some((band) => band.pci !== null)

  return (
    <div className="bands">
      <div className="section-label">
        Porteuses agrégées ({bands.length})
        <span className="ca-badge">CA active</span>
      </div>

      <div className="band-scroll">
        <table className="band-table">
          <thead>
            <tr>
              <th>Bande</th>
              {hasWidth && <th>Largeur</th>}
              <th>ARFCN</th>
              {hasPci && <th>PCI</th>}
              <th>RSRP</th>
              <th>RSRQ</th>
              <th>SINR</th>
            </tr>
          </thead>
          <tbody>
            {bands.map((band, index) => (
              // Le rang départage : deux porteuses agrégées peuvent être rapportées
              // avec les mêmes RAT, bande et canal quand le modem ne les distingue
              // pas, et une clé dupliquée fait taire silencieusement une ligne.
              <tr key={`${band.rat}-${band.band}-${band.channel ?? '?'}#${index}`}>
                <td>
                  <span className={`rat-tag ${band.rat.startsWith('5G') ? 'nr' : 'lte'}`}>
                    {band.rat}
                  </span>
                  <span className="band-code">{band.band}</span>
                  {/* La porteuse PRINCIPALE est celle que le backend a désignée,
                      et son code voyage dans `wan.band`. Le badge se posait sur
                      l'index 0, c'est-à-dire sur la première LISTÉE — et
                      `_primary_band` retient la porteuse de la famille courante,
                      qui n'est pas toujours la première : en 5G NSA elle saute
                      l'ancre LTE. Le badge nommait donc une porteuse que la tuile
                      du dessus ne décrivait pas. Aucune correspondance, aucun
                      badge : on ne devine pas. */}
                  {estPrincipale(band) && (
                    <span className="primary-tag">principale</span>
                  )}
                </td>
                {hasWidth && <td>{band.channel_width ?? '—'}</td>}
                <td>{fmtId(band.channel)}</td>
                {hasPci && <td>{fmtId(band.pci)}</td>}
                <td style={{ color: QUALITY_COLOR[rsrpQuality(band.rsrp)] }}>
                  {fmtNumber(band.rsrp, 0)}
                </td>
                <td>{fmtNumber(band.rsrq, 0)}</td>
                <td style={{ color: QUALITY_COLOR[sinrQuality(band.sinr)] }}>
                  {fmtNumber(band.sinr, 0)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
