import { Link } from 'react-router-dom'

import type { LinkChurn, RadioSample, Timeline, TimelineSlice } from '../types'
import {
  LOSS_ATTENTION_PERCENT, LOSS_MIN_PACKETS, LOSS_PERCENT_THRESHOLD, seuilUtile,
  reportableLoss,
} from '../lib/verdict'
import { LinkBand, bandGaps, bandTicks, bondTogether } from './Band'
import { Note, Notes, Absent, Pourquoi } from './Note'
import type { Quality } from '../lib/quality'
import { QUALITY_COLOR, QUALITY_THRESHOLDS, couleurDuLien, rsrpQuality, rsrqQuality, sinrQuality, wifiQuality } from '../lib/quality'
import { fmtNumber, fmtUptime, etiquetteSupport } from '../lib/format'
import { lienWan } from '../lib/routes'
import { ecarteDuBond } from '../lib/verdict'

/** L'heure d'un fléchissement, À LA MINUTE.
 *
 *  Le début est aligné sur une tranche de la bande relue — deux minutes et
 *  demie sur une fenêtre de huit heures. Afficher les secondes annoncerait une
 *  précision que la mesure n'a pas ; la minute est déjà plus fine que la
 *  tranche. Le journal, lui, date à la seconde parce qu'il tient des relevés,
 *  pas des tranches. */
const heure = (iso: string) =>
  new Date(iso).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })

interface Props {
  routerId: string
  samples: RadioSample[]
  showThroughput: boolean
  hasTunnel: boolean
  /** wan_id -> autres liens sur la MÊME cellule exacte. */
  sameCell: Map<string, string[]>
  /** Ce que chaque lien a fait sur la fenêtre relue. La bande était un panneau
   *  séparé qui répétait exactement ces lignes — même ordre, même identité — et
   *  elle est devenue une colonne. */
  timeline: Timeline | null
  /** Ce que la fenêtre couvre, dit en en-tête de colonne. */
  windowLabel: string
  /** Ce que chaque lien a compté SUR LA FENÊTRE : sauts de cellule et coupures.
   *  Les deux se comptaient jusqu'ici depuis le démarrage du processus. */
  churn: LinkChurn[]
  /** Garde-fou absolu de latence, tel que le SERVEUR le publie. Le recopier
   *  ferait vivre un même seuil en deux exemplaires de part et d'autre de la
   *  couture. Nul tant que la fiche n'est pas arrivée : on se replie alors sur
   *  la valeur documentée. */
  latencyThresholdMs: number | null
  /** Liens en signal faible, que le verdict a REFUSÉS. Ils restent dits — ici,
   *  auprès des colonnes radio, le seul endroit où ils décrivent quelque chose. */
  radioGrievances: string[]
  /** Le bond a-t-il déjà porté dans cette session (`RouterSummary.tunnel_attendu`) ?
   *  À `false`, il n'y a pas de redondance qui ait pu se RÉDUIRE : un bond
   *  jamais établi est un état, pas une succession de fléchissements. */
  tunnelAttendu?: boolean | null
}

/** Plages utiles des réglettes. Ce ne sont pas les extrêmes physiques mais les
 *  bornes entre lesquelles la valeur se lit : en dehors, la réglette sature, et
 *  c'est le nombre qui parle.
 *
 *  La latence n'y figure plus : elle se situe parmi les AUTRES liens du même
 *  relevé, pas dans une plage fixe. */
const PLAGES: Record<string, [number, number]> = {
  sinr: [0, 30],
  rsrp: [-120, -70],
  rsrq: [-20, -5],
}

/**
 * Tous les liens, une ligne chacun, une colonne par grandeur.
 *
 * La question réelle du technicien est « laquelle décroche ? ». Avec des tuiles,
 * comparer quatre SINR oblige à balayer quatre blocs en zigzag ; alignés en
 * colonne et composés en chiffres tabulaires, ils se comparent d'un coup d'œil.
 *
 * Cette table remplace `LinkTile` et `WanRow`, qui étaient DEUX implémentations
 * complètes du même objet — même calcul de qualité, mêmes marqueurs, deux jeux
 * de classes disjoints et des tailles divergentes pour les mêmes rôles.
 *
 * Les colonnes qui n'ont de sens pour aucun lien présent disparaissent. Une
 * colonne entièrement composée de tirets se lit comme une mesure perdue, et une
 * donnée qu'un matériel ne produit pas ne doit jamais passer pour une donnée
 * mauvaise.
 */
export function LinkTable({
  routerId, samples, showThroughput, hasTunnel, sameCell, timeline, windowLabel, churn,
  latencyThresholdMs, radioGrievances, tunnelAttendu = null,
}: Props) {
  // Le seuil publié fait foi ; le repli n'est là que le temps que la fiche
  // arrive.
  const seuilMs = seuilUtile(latencyThresholdMs)
  const griefRadio = radioGrievances.length > 0 ? radioGrievances.join(', ') : null
  const parChurn = new Map(churn.map((c) => [c.wan_id, c]))
  const cellulaire = samples.some((s) => s.wan.kind === 'cellular')
  const wifi = samples.some((s) => s.wan.kind === 'wifi')
  const cellules = samples.some((s) => s.wan.cell_id !== null)
  // Les latences des liens qui PORTENT, au même instant. C'est la référence
  // contre laquelle chacun se juge — celle du bandeau, et la seule publiée.
  const latences = samples
    .filter((s) => s.wan.tunnel?.in_bond && s.wan.tunnel.rtt_ms !== null)
    .map((s) => s.wan.tunnel!.rtt_ms!)
  // La latence propre de chaque lien, hors tunnel : la seule qu'aient un lien
  // filaire, un lien écarté et un équipement dont le tunnel ne tient pas
  // (R-1.3). Sa colonne s'ouvre dès qu'un lien en porte une.
  const latencesLien = samples
    .filter((s) => s.wan.connected && s.wan.path_latency_recent_ms !== null)
    .map((s) => s.wan.path_latency_recent_ms!)
  const aLatenceLien = samples.some((s) => s.wan.path_latency_recent_ms !== null)
  const parLien = new Map(timeline?.lanes?.map((l) => [l.wan_id, l.slices]) ?? [])
  const tranches = timeline?.slice_count ?? 0
  const creux = bandGaps(timeline)
  const reperes = bandTicks(timeline)
  // Ce que les liens ont fait ENSEMBLE, pour le dire en toutes lettres plutôt
  // qu'en pixels : trois encodages visuels ont échoué à se faire comprendre.
  // Seuls les liens du bond comptent : un filaire hors du profil n'a jamais
  // eu sa place dedans, et le compter en faisait un fléchissement permanent.
  const dansLeBond = samples.filter((s) => s.wan.tunnel !== null).length
  // Un bond jamais établi n'a pas de redondance à perdre. Mesuré sur un Dome
  // Pro Duo dont le pair était injoignable depuis l'allumage : « Redondance
  // réduite 3 fois. Au plus bas : 0 sur 3 » — trois « épisodes » découpés dans
  // un même état permanent, que le bandeau disait à juste titre « jamais
  // établi ». Le même fait, deux lectures contraires, sur un seul écran.
  const ensemble = tunnelAttendu === false ? null : bondTogether(timeline, dansLeBond)
  // Nombre de colonnes de mesure à droite de la bande, pour aligner l'axe.
  const droite =
    (cellulaire ? 3 : 0) + (wifi ? 1 : 0) + (showThroughput ? 2 : 0)
    + (hasTunnel ? 2 : 0) + (cellules ? 1 : 0) + 1

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Les liens</h2>
        <span className="note">
          {samples.length === 1 ? 'un lien supervisé' : `${samples.length} liens supervisés`}
          {timeline && ` · ${windowLabel.toLowerCase()}`}
          {/* Le serveur a rogné la fenêtre : la bande ne couvre que les
              dernières vingt-quatre heures, et le dire est obligatoire — une
              fenêtre rognée en silence se lit comme entière. */}
          {timeline?.capped && ' · dernières 24 h seulement'}
        </span>
      </div>
      {/* La zone défile horizontalement quand les colonnes débordent. Sans
          `tabIndex`, elle n'était atteignable QU'À LA SOURIS : au clavier, les
          colonnes de droite — perte, coupures — étaient inaccessibles. Un
          conteneur défilant doit pouvoir recevoir le focus, et dire ce qu'il
          est. */}
      <div className="ltable-scroll" tabIndex={0} role="region" aria-label="Tableau des liens">
        <table className="ltable">
          {/* Le tableau n'avait pas de titre accessible : un lecteur d'écran
              annonçait « tableau, 13 colonnes » sans dire de quoi. La légende est
              masquée visuellement — le panneau porte déjà son titre — mais elle
              existe dans l'arbre d'accessibilité. */}
          <caption className="sr-only">
            État de chaque lien de la liaison agrégée : rattachement, niveaux
            radio, débit, latence et coupures sur {windowLabel.toLowerCase()}.
          </caption>
          <thead>
            <tr>
              <th scope="col" className="l">Lien</th>
              <th scope="col" className="l">Rattachement</th>
              {timeline && <th scope="col" className="l is-band">{windowLabel}</th>}
              {cellulaire && <th scope="col">SINR dB</th>}
              {cellulaire && <th scope="col">RSRP dBm</th>}
              {cellulaire && <th scope="col">RSRQ dB</th>}
              {wifi && <th scope="col">RSSI dBm</th>}
              {showThroughput && <th scope="col">↑ Mbit/s</th>}
              {showThroughput && <th scope="col">↓ Mbit/s</th>}
              {hasTunnel && <th scope="col">Bond ms</th>}
              {hasTunnel && <th scope="col">Perte</th>}
              {aLatenceLien && <th scope="col">Lien ms</th>}
              {/* L'identifiant de cellule a quitté le tableau de bord : un
                  nombre à huit chiffres qui change toutes les une à huit minutes
                  ne se lit pas, et sa seule utilité — savoir si deux modems
                  partagent une cellule — est déjà portée par le marqueur. Ce qui
                  se lit, c'est combien de fois il a changé. */}
              {cellules && <th scope="col">Sauts de cellule</th>}
              <th scope="col">Coupures</th>
            </tr>
          </thead>
          <tbody>
            {samples.map((s) => (
              <Row
                key={s.wan.wan_id}
                routerId={routerId}
                sample={s}
                showThroughput={showThroughput}
                hasTunnel={hasTunnel}
                cellulaire={cellulaire}
                wifi={wifi}
                cellules={cellules}
                shares={sameCell.get(s.wan.wan_id) ?? []}
                slices={timeline ? (parLien.get(s.wan.wan_id) ?? []) : null}
                tranches={tranches}
                latences={latences}
                latencesLien={aLatenceLien ? latencesLien : null}
                seuilMs={seuilMs}
                churn={parChurn.get(s.wan.wan_id) ?? null}
              />
            ))}
          </tbody>
          {/* L'axe du temps sous la seule colonne qu'il concerne. */}
          {timeline && reperes.length > 0 && (
            <tfoot>
              <tr>
                <td colSpan={2} />
                <td className="l is-band">
                  <span className="ticks">
                    {/* Clé par RANG, et non par libellé. Les repères sont des HH:MM : sur
                        une fenêtre courte — « depuis la mise sous tension », quelques
                        minutes après l'allumage — deux d'entre eux tombent sur la même
                        minute, et React recevait deux enfants de même clé. Le rang, lui,
                        est l'identité réelle : le i-ème repère est toujours le i-ème. */}
                    {reperes.map((t, i) => <span key={i}>{t}</span>)}
                  </span>
                </td>
                <td colSpan={droite} />
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      {/* Ce que les liens ont fait ENSEMBLE, en toutes lettres.
          L'information est rare et épisodique — 97 à 100 % de « rien » — et
          c'est exactement le profil où une phrase bat un graphique : il n'y a ni
          échelle à deviner, ni pixels à compter, et le cas normal se dit mieux
          que tout. « Dans le bond » et non « qui portent » : sur ce déploiement,
          le tunnel n'est pas le chemin du trafic. */}
      <Notes>
{ensemble !== null && (
        <Note pourquoi={
          (ensemble.observé >= 0.99
            ? "Jugé sur toute la fenêtre relue. "
            : `Jugé sur les ${Math.round(ensemble.observé * 100)} % de la fenêtre que TOUS `
              + "les liens ont renseignés — le reste n'est pas jugé. ")
          + (ensemble.fléchissements.length === 0
              ? "Aucun moment où un lien a quitté le bond sans qu'un autre le remplace."
              : ensemble.fléchissements
                  .map((f) => `${heure(f.at)} pendant ${fmtUptime(Math.round(f.seconds))} : `
                    + (f.worst === 0 ? "plus aucun lien dans le bond"
                                     : `${f.worst} lien(s) sur ${dansLeBond}`))
                  .join(' · '))
        }>
          {ensemble.fléchissements.length === 0 ? (
            <b>Redondance intacte.</b>
          ) : (
            <>
              <b>
                Redondance réduite {ensemble.fléchissements.length === 1
                  ? 'une fois'
                  : `${ensemble.fléchissements.length} fois`}.
              </b>{' '}
              Au plus bas :{' '}
              <span className="num">
                {Math.min(...ensemble.fléchissements.map((f) => f.worst))}
              </span>{' '}
              sur {dansLeBond}.
            </>
          )}
        </Note>
      )}
{tunnelAttendu === false && hasTunnel && timeline !== null && (
        <Note pourquoi={
          "Aucun lien n'a été dans le bond depuis la mise sous tension : il n'y a pas "
          + "de redondance qui ait pu se réduire. L'état de chaque lien reste dit sur sa ligne."
        }>
          <b>Bond jamais établi</b> dans cette session.
        </Note>
      )}
      {creux > 0 && (
        <Note pourquoi={
          "Ce ne sont pas des liens au repos : la supervision ne tournait pas. Les "
          + "combler referait le mensonge que tout cet outil combat — une absence de "
          + "mesure n'est pas une mesure."
        }>
          {Math.round((100 * creux) / tranches)} % de la fenêtre sans relevé.
        </Note>
      )}
      {cellulaire && (
        <Note pourquoi={
          "Ce sont des choix éditoriaux, pas une norme : entre constructeurs, le RSRP "
          + "dit « excellent » va de −80 à −90 dBm. La note d'un lien retient la PIRE des "
          + "trois — un RSRP excellent avec un SINR moyen est le signe d'une interférence."
        }>
          Seuils (excellent / bon / moyen) — {QUALITY_THRESHOLDS.map((s, i) => (
            <span key={s.key}>
              {i > 0 ? ' · ' : ''}
              <b>{s.key}</b> {fmtNumber(s.bornes[0], 0)} / {fmtNumber(s.bornes[1], 0)} /{' '}
              {fmtNumber(s.bornes[2], 0)} {s.unit}
            </span>
          ))}.
        </Note>
      )}
      {cellulaire && (
        <Note pourquoi={
          "Mesuré sur 35 jours : la corrélation de rang entre SINR, RSRP, RSRQ ou RSSI "
          + "et la latence ou la perte du tunnel ne dépasse |ρ| = 0,19 sur aucun des "
          + "quatre modems, chacun sur près de 8 800 relevés. Avant 1 181 pics de latence "
          + "isolés, le niveau radio du cycle précédent est à 0,00 dB de la médiane de la "
          + "même cellule — indiscernable des 22 700 cycles calmes témoins. La corrélation "
          + "entre RSRP et débit (ρ ≈ −0,6) s'évapore à −0,03 à cellule et fréquence "
          + "constantes : elle ne mesurait que le déplacement du véhicule. Vaut pour la "
          + "plage observée, −75 à −105 dBm."
        }>
          <b>Ces niveaux ne prédisent pas le tunnel</b> — le verdict ne s'y fonde donc pas.
          {griefRadio !== null && <> {griefRadio}.</>}
        </Note>
      )}
</Notes>
    </section>
  )
}

function Row({
  routerId, sample, showThroughput, hasTunnel, cellulaire, wifi, cellules, shares,
  slices, tranches, latences, latencesLien, churn, seuilMs,
}: {
  routerId: string
  sample: RadioSample
  showThroughput: boolean
  hasTunnel: boolean
  cellulaire: boolean
  wifi: boolean
  cellules: boolean
  shares: string[]
  slices: TimelineSlice[] | null
  tranches: number
  latences: number[]
  /** Latences propres des liens connectés, ou `null` quand la colonne est fermée. */
  latencesLien: number[] | null
  /** Garde-fou absolu de latence, tel que le serveur le publie. */
  seuilMs: number
  churn: LinkChurn | null
}) {
  const { wan } = sample
  const isWifi = wan.kind === 'wifi'
  const isWired = wan.kind === 'ethernet'
  const colour = couleurDuLien(wan)
  const label = etiquetteSupport(wan.kind)
  // Un état de tunnel nul veut dire INCONNU : on n'accuse pas le lien d'être
  // hors du bond sur une absence de mesure.
  const ecarte =
    wan.connected && ecarteDuBond(wan)

  const cible = lienWan(routerId, wan.wan_id)
  const sousTitre = isWifi
    ? (wan.wifi?.ssid ?? '—')
    : isWired
      ? (wan.ip ?? '—')
      : (wan.operator_name ?? '—')

  return (
    <tr className={wan.connected || wan.disabled ? undefined : 'is-down'}>
      {/* `<th scope="row">` et non `<td>` : c'est la cellule qui NOMME la ligne.
          Un lecteur d'écran annonce alors « SIM 4, SINR, 14,2 dB » en parcourant
          la rangée ; avec un `<td>`, il ne dit que « SINR, 14,2 dB » et
          l'auditeur ignore de quel lien on parle — dans un tableau de cinq
          lignes dont c'est tout l'objet de les distinguer. */}
      <th scope="row" className="l">
        <Link to={cible}>
          <span className="who">
            <span className="rail" style={{ ['--q' as string]: colour }} />
            <span style={{ minWidth: 0 }}>
              <b>{label} {wan.wan_id}</b>
              {/* `.who .sub` tronque par CSS : le texte complet doit rester
                  atteignable au survol, sinon la troncature perd de l'information
                  sans le dire. */}
              <span className="sub" title={sousTitre}>{sousTitre}</span>
            </span>
          </span>
        </Link>
      </th>

      <td className="l">
        {wan.disabled ? (
          // Une décision de l'exploitant, dite sans alarme.
          <span className="tag">désactivé</span>
        ) : !wan.connected ? (
          <span className="tag is-off">{wan.status_message ?? 'hors service'}</span>
        ) : (
          <>
            {wan.rat && <span className="tag">{wan.rat}</span>}{' '}
            {wan.band && <span className="tag is-mono">{wan.band}</span>}{' '}
            {isWired && !wan.rat && <span className="tag">Établi</span>}
            {/* Un WAN de priorité supérieure à 1 ne porte rien tant qu'un lien
                de priorité 1 tient : le dire évite de chercher pourquoi il est
                à zéro. */}
            {wan.priority !== null && wan.priority > 1 && (
              <> <span className="tag">secours · P{wan.priority}</span></>
            )}
            {ecarte && <> <span className="tag is-off">hors du bond</span></>}
            {/* Le routeur arrête le trafic sur un lien qui dépasse de plus de
                `cutoff_ms` la latence du plus rapide — et son état reste
                `ACTIVE`. Vérifié sur la base : sur les 113 relevés qui
                franchissent le seuil, l'état vaut ACTIVE cent fois sur cent, et
                les intervalles sans un seul paquet montant y sont quatre fois et
                demie plus fréquents. L'écran annonçait donc « porte le flux ». */}
            {wan.tunnel?.beyond_cutoff && (
              <> <Pourquoi
                className="tag is-off"
                texte={`Plus de ${wan.tunnel.cutoff_ms} ms au-dessus du lien le plus rapide : le routeur cesse d’employer ce lien.`}
              >
                au-delà du seuil
              </Pourquoi></>
            )}
            {/* Un FAIT, pas une alarme : sur ce matériel, le partage de cellule
                est fréquent, et un marqueur permanent en ambre finirait par ne
                plus être lu — ni lui, ni les autres. */}
            {shares.length > 0 && (
              <> <span className="tag" title="Même secteur et même porteuse : les ressources radio ne s’additionnent pas.">
                cellule partagée
              </span></>
            )}
          </>
        )}
      </td>

      {slices !== null && (
        <td className="l is-band">
          <LinkBand sample={sample} slices={slices} total={tranches} />
        </td>
      )}

      {cellulaire && <Cell value={isWifi || isWired ? null : wan.sinr} digits={1}
        quality={sinrQuality} range={PLAGES.sinr} />}
      {cellulaire && <Cell value={isWifi || isWired ? null : wan.rsrp} digits={0}
        quality={rsrpQuality} range={PLAGES.rsrp} />}
      {cellulaire && <Cell value={isWifi || isWired ? null : wan.rsrq} digits={0}
        quality={rsrqQuality} range={PLAGES.rsrq} />}
      {wifi && <Cell value={isWifi ? wan.rssi : null} digits={0}
        quality={wifiQuality} range={[-90, -40]} />}

      {showThroughput && <Cell value={mbit(wan.tx_kbps)} digits={1} />}
      {showThroughput && <Cell value={mbit(wan.rx_kbps)} digits={1} />}

      {/* La réglette situe ce lien parmi les AUTRES, et non dans une plage
          absolue : la table sert à comparer, et « qui est le plus lent » est la
          question qu'on lui pose. */}
      {hasTunnel && (
        <Cell
          value={wan.tunnel?.rtt_ms ?? null}
          digits={0}
          quality={(v) => rttQuality(v, wan.tunnel?.beyond_cutoff ?? false, seuilMs)}
          range={latences.length > 1 ? [0, Math.max(...latences)] : undefined}
        />
      )}
      {hasTunnel && (
        <td>
          <Loss sample={sample} />
        </td>
      )}
      {/* Le seuil du bond ne la concerne pas : seul le garde-fou absolu juge. */}
      {latencesLien !== null && (
        <Cell
          value={wan.path_latency_recent_ms}
          digits={0}
          quality={(v) => rttQuality(v, false, seuilMs)}
          range={latencesLien.length > 1 ? [0, Math.max(...latencesLien)] : undefined}
        />
      )}

      {cellules && (
        <td className="num">
          {/* Un lien SANS cellule — filaire, Wi-Fi — n'a pas de saut à compter :
              son « 0 » se lisait comme une stabilité, la règle que la fiche de
              session applique déjà (`has_cells`). Un cadratin, pas un zéro. */}
          {churn === null || !churn.has_cells ? (
            <Absent />
          ) : churn.cell_changes === 0 ? (
            <span className="absent">0</span>
          ) : (
            churn.cell_changes
          )}
        </td>
      )}

      {/* Les coupures DE LA FENÊTRE. Elles se comptaient depuis le démarrage du
          processus superviseur — une base de temps qui n'apparaissait nulle part
          et qui se remettait à zéro à chaque relance — dans un panneau intitulé
          « depuis la mise sous tension ». */}
      <td className="num">
        {churn === null ? (
          <Absent />
        ) : churn.outages === 0 ? (
          <span className="absent">0</span>
        ) : (
          churn.outages
        )}
      </td>
    </tr>
  )
}

/** Une cellule de mesure : le nombre, et sa position dans la plage utile.
 *
 *  Sans valeur, un cadratin — jamais un zéro. C'est la règle la plus chère de ce
 *  projet : un zéro affiché à la place d'une absence de mesure y a déjà menti
 *  cinq fois. */
function Cell({
  value, digits, quality, range,
}: {
  value: number | null
  digits: number
  quality?: (v: number | null) => Quality
  range?: [number, number]
}) {
  if (value === null) {
    return <td><Absent /></td>
  }
  const note = quality?.(value)
  const colour = note ? QUALITY_COLOR[note] : undefined
  const part = range
    ? Math.max(0, Math.min(1, (value - range[0]) / (range[1] - range[0])))
    : null
  return (
    <td>
      <span className="meas" style={colour ? { ['--q' as string]: colour } : undefined}>
        <span className="n">{fmtNumber(value, digits)}</span>
        {part !== null && (
          <span className="g"><i style={{ width: `${part * 100}%` }} /></span>
        )}
      </span>
    </td>
  )
}


/**
 * La perte, jugée exactement comme le bandeau la juge.
 *
 * Cette cellule portait sa propre règle : `loss_percent` — le PIRE des deux
 * sens — accolé à `max(packets_up, packets_down)`, donc au dénominateur de
 * l'autre sens dès que le sens dégradé était le moins chargé. La docstring de
 * `reportableLoss` interdit explicitement de lire `loss_percent` pour cette
 * raison ; la table le lisait quand même.
 *
 * Mesuré : quarante-neuf relevés de la base où la table publie un taux non nul,
 * dont douze sans aucun sens au-dessus de deux cents paquets, dont trois peints
 * en rouge. Le 13 septembre à 14 h 00 sur le WAN 6, la table affichait
 * « 4,71 % » en rouge — établi sur 191 paquets descendants — pendant que le
 * bandeau, jugeant chaque sens sur SON dénominateur, annonçait « Liaison OK ».
 *
 * Une seule règle désormais, la sienne, et les deux ne peuvent plus diverger.
 */
function Loss({ sample }: { sample: RadioSample }) {
  const t = sample.wan.tunnel
  if (!t) return <Absent />
  const retenu = reportableLoss(t)
  if (retenu === null) {
    // Le compte des paquets reste utile — un tunnel au repos se reconnaît à
    // cela — mais il n'est montré que s'il a été MESURÉ : un compteur absent
    // n'est pas un intervalle sans paquet.
    const comptes = [t.packets_up, t.packets_down].filter((p): p is number => p !== null)
    return (
      <Pourquoi className="absent" texte={
        comptes.length === 0
          ? 'Aucun compteur de paquets sur cet intervalle : le taux ne peut pas être établi.'
          : `Moins de ${LOSS_MIN_PACKETS} paquets comptés dans chaque sens : le taux n’est pas publiable.`
      }>
        — {comptes.length > 0 && <small>{Math.max(...comptes)} pq.</small>}
      </Pourquoi>
    )
  }
  const colour = retenu.percent >= LOSS_PERCENT_THRESHOLD ? QUALITY_COLOR.faible
    : retenu.percent >= LOSS_ATTENTION_PERCENT ? QUALITY_COLOR.moyen : undefined
  return (
    <Pourquoi
      className="meas"
      style={colour ? { ['--q' as string]: colour } : undefined}
      texte={`Perte ${retenu.direction} sur ${retenu.packets} paquets comptés`}
    >
      <span className="n">{fmtNumber(retenu.percent, 2)} %</span>
    </Pourquoi>
  )
}

const mbit = (kbps: number | null) => (kbps === null ? null : kbps / 1000)

/**
 * La latence se juge sur la règle du ROUTEUR, et sur elle seule.
 *
 * Cette fonction a porté deux règles successives, et aucune n'était celle du
 * firmware. D'abord des seuils absolus — plus de 120 ms, rouge —, qui
 * contredisaient le bandeau une fois sur trois : mesuré sur 5 679 cycles,
 * 2 513 relevés allumaient la jauge en rouge et 859 d'entre eux n'étaient repris
 * par aucun verdict. Puis l'écart à la médiane des autres liens, pour s'accorder
 * avec le bandeau — mais le journal, lui, était passé entre-temps à la règle que
 * le routeur applique VRAIMENT, et la contradiction s'est déplacée sans
 * disparaître : mesuré sur 33 679 relevés, les deux divergeaient sur 7,7 %, et
 * toujours dans le même sens.
 *
 * Le firmware publie son `latencyDiffCutoff` et le serveur en tire
 * `beyond_cutoff` : le fait est lisible, il n'y a plus rien à approximer. Le
 * badge « au-delà du seuil » de la colonne voisine s'appuie déjà dessus — la
 * couleur du chiffre le rejoint, et les deux ne peuvent plus se contredire.
 *
 * La réglette, elle, ne juge pas : elle SITUE le lien parmi les autres. Comparer
 * et accuser sont deux gestes distincts, et c'est de les avoir confondus que
 * venaient les trois règles.
 */
function rttQuality(v: number | null, ecarte: boolean, absoluMs: number): Quality {
  if (v === null) return 'inconnu'
  return ecarte || v >= absoluMs ? 'faible' : 'inconnu'
}
