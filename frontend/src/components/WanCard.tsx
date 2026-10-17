import { motDuPair } from '../lib/bondProfile'
import type { LinkChurn, RadioSample, SimSlot, TunnelLink } from '../types'
import type { Quality } from '../lib/quality'
import { QUALITY_COLOR, linkQuality, rsrpQuality, rsrqQuality, sinrQuality, wifiQuality } from '../lib/quality'
import {
  fmtInt,
  fmtNumber,
  fmtThroughput,
  fmtTime,
  fmtUptime,
} from '../lib/format'
import { SignalTile } from './SignalTile'
import { BandTable } from './BandTable'
import { LOSS_MIN_PACKETS } from '../lib/verdict'
import { etiquetteSupport, fmtId } from '../lib/format'
import { Absent } from './Note'

/** Ce que l'état du lien dans le tunnel signifie pour l'exploitant.
 *
 *  Les libellés du routeur sont des codes ; ce qui compte est de savoir si le
 *  lien porte le flux, et c'est le serveur qui le tranche avec `in_bond`. Un
 *  état nul signifie INCONNU : on ne l'affiche ni comme porteur ni comme écarté.
 *  Les autres codes sont affichés tels quels plutôt que traduits à tort — la
 *  liste des valeurs possibles n'a pas été observée en entier. */
function tunnelLabel(tunnel: TunnelLink): string {
  if (tunnel.state === null) return '—'
  if (tunnel.in_bond) return 'porte le flux'
  if (tunnel.state === 'WAN_DOWN') return 'lien indisponible'
  if (tunnel.state === 'WAN_DISABLED') return 'lien désactivé'
  // Le mot du routeur sous la sentinelle, et non « pair injoignable » — une
  // conclusion que le routeur ne tire pas (R-1.5).
  if (tunnel.cause_scope === 'pair') return `pair : ${motDuPair(tunnel.peer_state)}`
  return tunnel.state
}


/** Taux de perte accompagné du décompte sur lequel il a été calculé.
 *
 *  Un taux sans son dénominateur est indéfendable : « 0,00 % » sur trois paquets
 *  et « 0,00 % » sur douze mille se lisent pareil et ne valent pas la même chose.
 *  Le décompte est donc toujours affiché, et le taux refusé quand l'intervalle
 *  est trop court pour qu'il signifie quoi que ce soit. Un décompte nul — pas de
 *  relevé de référence — ne permet même pas de dire combien on a compté : on se
 *  tait entièrement. */
function fmtLoss(percent: number | null, packets: number | null): string {
  // Zéro paquet compté ne permet même pas de dire combien on a compté : on se
  // tait entièrement, comme la docstring l'annonce. Le code ne testait que
  // `null` et rendait « — · 0 paquet », c'est-à-dire un décompte nul présenté
  // comme une mesure.
  if (packets === null || packets === 0) return '—'
  const counted = `${fmtInt(packets)} paquet${packets > 1 ? 's' : ''}`
  if (packets < LOSS_MIN_PACKETS || percent === null) return `— · ${counted}`
  return `${fmtNumber(percent, 2, '%')} · ${counted}`
}

/** Un sens a-t-il été mesuré, mais sur trop peu de paquets pour conclure ? */
const tooFewPackets = (packets: number | null): boolean =>
  packets !== null && packets < LOSS_MIN_PACKETS

function Detail(
  { label, value, title }: { label: string; value: string | null; title?: string },
) {
  return (
    // `title` n'est pas décoratif : certaines lignes portent un tiret qui veut
    // dire « pas mesuré à cet instant » plutôt que « pas mesurable », et les
    // deux se distinguent mal sans une phrase.
    <div title={title}>
      <div className="detail-key lbl">{label}</div>
      {/* Un compte inconnu n'est pas un compte nul : tant que la relecture de la
          fenêtre n'a pas abouti, la ligne le dit au lieu d'écrire « 0 ». */}
      <div className="detail-val num">{value ?? <Absent />}</div>
    </div>
  )
}

/** Priorité sortante, affichée SEULEMENT quand elle écarte le lien du service.
 *
 *  Elle vaut 1 sur tous les WAN exploitables du MAX HD4 MBX (relevé du matériel :
 *  les quatre SIM connectées à 1, les seuls 2 étant des logements USB vides, donc
 *  jamais affichés) : la ligne y répétait « 1 » indéfiniment. Elle ne devient une
 *  information que sur un équipement qui hiérarchise vraiment ses liens — sur le
 *  Dome Pro Duo le WAN filaire est à 1 et les deux SIM à 2, et l'exploitant doit
 *  alors savoir que ce lien ne portera rien tant que le principal tient. */
function OutboundPriority({ priority }: { priority: number | null }) {
  if (priority === null || priority <= 1) return null
  return <Detail label="Priorité sortante" value={`${priority} — secours`} />
}

interface WanCardProps {
  sample: RadioSample
  /** L'équipement publie-t-il un débit par WAN ? Piloté par les capacités
   *  détectées, jamais par un test sur la valeur : une mesure momentanément
   *  nulle n'est pas une mesure absente. */
  showThroughput?: boolean
  /** Coupures et sauts de cellule de CE lien, comptés sur la fenêtre relue —
   *  la même que la table des liens. `null` quand la relecture n'a pas encore
   *  abouti : un compte inconnu n'est pas un compte nul. */
  churn?: LinkChurn | null
  /** Ce que la fenêtre couvre, pour que le compte porte sa base de temps. */
  windowLabel?: string
}

/** Carte d'un WAN radio : cellulaire (qualité, porteuses, cellule) ou lien Wi-Fi amont. */
/**
 * Les emplacements SIM de ce modem.
 *
 * Le châssis en déclare huit — deux par modem sur ce matériel — et l'interface
 * n'en montrait aucun : elle annonçait « 8 emplacements SIM » sans jamais dire
 * s'ils étaient occupés. Vérifié en direct : les huit le sont, donc chaque modem
 * porte une carte de secours, ce que rien n'apprenait.
 *
 * Ce que le routeur ne publie PAS, et qu'on n'invente donc pas : quel
 * emplacement est actif. Un APN renseigné sur un seul des deux suggère lequel
 * sert — c'est une indication, pas une preuve, et la note le dit ainsi.
 */
/** Un volume en mégaoctets, passé au gigaoctet quand il le mérite. */
const volumeMo = (mb: number | null): string =>
  mb === null
    ? '—'
    : mb >= 1024
      ? `${fmtNumber(mb / 1024, 1)} Go`
      : `${fmtNumber(mb, 0)} Mo`

function SimSlots({ slots }: { slots: SimSlot[] | undefined }) {
  if (!slots || slots.length === 0) return null
  const cartes = slots.filter((s) => s.card_detected === true).length
  // Un emplacement dont le firmware ne dit rien n'est ni vide ni occupé :
  // il ne compte pas, et le décompte le dit.
  const inconnus = slots.filter((s) => s.card_detected === null).length
  const active = slots.find((s) => s.active)
  return (
    <>
      <span className="section-label">
        Emplacements SIM — {cartes} carte{cartes > 1 ? 's' : ''} sur {slots.length}
        {inconnus > 0 ? ` (${inconnus} non renseigné${inconnus > 1 ? 's' : ''})` : ''}
      </span>
      <div className="details">
        {slots.map((s) => (
          <Detail
            key={s.slot}
            /* L'emplacement ACTIF, enfin dit. Le routeur le publie —
               `cellular.sim.<n>.active`, sur les deux matériels de référence et
               sur les deux emplacements — et le parseur s'en abstenait au nom
               d'un commentaire qui affirmait le contraire. Vérifié sur le
               châssis : le modem du WAN 5 travaille sur sa carte 2. */
            label={`Emplacement ${s.slot}${s.active ? ' · en service' : ''}`}
            value={
              s.card_detected === null ? '— (non renseigné)'
                : !s.card_detected ? 'vide'
                : s.apn ? `carte présente · APN ${s.apn}` : 'carte présente'
            }
          />
        ))}
      </div>
      {slots.map((s) =>
        s.allowance?.enabled && s.allowance.monthly_allowance_mb !== null ? (
          <p className="disclaimer" key={`q-${s.slot}`}>
            <b>Emplacement {s.slot} : plafond de {fmtInt(s.allowance.monthly_allowance_mb)}
            {' '}Mo</b>, compteur remis à zéro le {s.allowance.reset_day} du mois.
            {s.allowance.actions.includes('restrict') && (
              <> Au dépassement, le routeur <b>coupe cette carte</b> — ce n’est pas un
              avertissement, c’est une panne programmée.</>
            )}
            {s.allowance.actions.includes('email') && !s.allowance.actions.includes('restrict') && (
              <> Au dépassement, le routeur envoie un courriel.</>
            )}
          </p>
        ) : null,
      )}
      {cartes > 1 && (
        <p className="disclaimer">
          Une seconde carte est présente : ce modem a de quoi basculer d’opérateur sans
          intervention.
          {active === undefined && (
            <> Ce modèle ne publie pas lequel des emplacements est actif — seul l’APN
            renseigné le suggère, et ce n’est pas une preuve.</>
          )}
        </p>
      )}
    </>
  )
}

export function WanCard({ sample, showThroughput = true, churn, windowLabel }: WanCardProps) {
  // Les coupures et les sauts de cellule se comptent SUR LA FENÊTRE, comme dans
  // la table des liens. La fiche lisait `sample.disconnects` et
  // `sample.cell_changes`, cumulés depuis le démarrage du serveur — une base de
  // temps qui n'apparaît nulle part et qui se remet à zéro à chaque relance.
  // Mesuré en direct : la table annonçait 64 sauts sur SIM 6 quand la fiche, à
  // un clic, en annonçait 14. C'est le défaut que le modèle `LinkChurn` déclare
  // avoir corrigé — il l'avait été dans la table, pas ici.
  const coupures = churn === null || churn === undefined ? null : churn.outages
  const sauts = churn === null || churn === undefined ? null : churn.cell_changes
  const fenetre = (windowLabel ?? 'sur la fenêtre').toLowerCase()
  const { wan } = sample
  const plafond =
    wan.allowance?.enabled && wan.allowance.monthly_allowance_mb !== null ? wan.allowance : null
  // La latence du lien est lue une fois par minute, la carte se rafraîchit
  // toutes les cinq secondes. Le serveur tient la dernière mesure jusqu'à 90 s
  // AVEC son heure : l'écran dit son âge, pour qu'une mesure de cinquante
  // secondes ne passe pas pour une mesure de maintenant (R-1.3).
  const avecSonAge = (texte: string, lue: string | null): string => {
    const age = lue === null ? null : Math.round((Date.parse(sample.ts) - Date.parse(lue)) / 1000)
    return texte + (age !== null && age > 0 ? ` · il y a ${age} s` : '')
  }
  const latenceChemin =
    wan.path_latency_recent_ms === null
      ? null
      : avecSonAge(`${fmtNumber(wan.path_latency_recent_ms, 0)} ms`, wan.path_latency_at)
  // La température du MODEM, même cadence et même règle d'âge (R-1.11). Sans
  // seuil : le routeur n'en publie aucun, et un seuil inventé allumerait un
  // avertissement faux ou permanent.
  const temperatureModem =
    wan.modem_temperature_c === null
      ? null
      : avecSonAge(`${fmtNumber(wan.modem_temperature_c, 0)} °C`, wan.modem_temperature_at)
  const noteLatence =
    'Latence du lien lui-même, hors de tout tunnel — la seule qu’aient un lien ' +
    'filaire, un lien écarté du bond et un équipement sans SpeedFusion. Relevée ' +
    'une fois par minute : la dernière mesure est montrée avec son âge, et ' +
    'disparaît au-delà de 90 s.'
  const isWifi = wan.kind === 'wifi'
  const isWired = wan.kind === 'ethernet'
  const global: Quality = linkQuality(wan)

  return (
    <section className="card">
      <header className="card-head">
        <span
          className="dot"
          // Le MÊME rouge que partout ailleurs : un lien tombé recevait `#f87171`
          // sur le tableau de bord et `#ef4444` ici — deux rouges pour un seul
          // fait, à un clic d'écart.
          style={{ background: wan.disabled ? QUALITY_COLOR.inconnu : QUALITY_COLOR[wan.connected ? global : 'faible'] }}
        />
        {/* L'identifiant d'abord, le nom configuré ensuite et en second plan :
            sur les équipements de terrain ce nom contient couramment un numéro de
            ligne et un ICCID complet — il identifie mal, il s'affiche mal, et il
            n'a rien à faire en titre. La ligne de lien appliquait déjà cette
            règle ; cette carte-ci ne la suivait pas. */}
        <span className="card-title">
          {etiquetteSupport(wan.kind)} {wan.wan_id}
        </span>
        {wan.wan_name && wan.wan_name !== `${wan.wan_id}` && (
          <span className="card-subtitle" title={wan.wan_name}>
            {wan.wan_name}
          </span>
        )}
        {wan.operator_name && (
          /* Le code réseau en infobulle, pas en badge : « Orange » suffit à
             l'écran, et « 208-01 » n'est utile que pour lever un doute — deux
             opérateurs peuvent porter le même nom commercial dans deux pays.
             Le NOM vient du code (R-2.6) ; le libellé du modem, quand il en
             diffère (« Mobile S Mobile » pour SFR), reste lisible au survol. */
          <span
            className="badge accent"
            title={[
              wan.plmn ? `PLMN ${wan.plmn}` : null,
              wan.operator && wan.operator !== wan.operator_name
                ? `libellé du modem : ${wan.operator}` : null,
            ].filter(Boolean).join(' · ') || undefined}
          >
            {wan.operator_name}
          </span>
        )}
        {wan.rat && <span className={`badge ${isWifi ? 'wifi' : 'tech'}`}>{wan.rat}</span>}
        {wan.band && <span className="badge band">{wan.band}</span>}
        <span className="spacer" />
        <span className="badge">{fmtTime(sample.ts)}</span>
      </header>
      {/* La fenêtre des comptes, dite UNE fois : collée à chaque libellé
          (« Coupures · depuis 12:51 — sous tension depuis 09:52 »), elle serrait
          la grille au point de chevaucher le libellé voisin (A-9). */}
      {windowLabel && <p className="card-window">Comptes {fenetre}.</p>}

      <div className="card-body">
        {!wan.connected && wan.disabled ? (
          /* Une décision, pas une panne : ni rouge, ni « déconnecté » (C-20). */
          <div className="panel-note">
            Lien désactivé par l’exploitant — aucune mesure tant qu’il ne l’est pas.
          </div>
        ) : !wan.connected ? (
          <div className="offline-banner">
            {isWired ? 'Lien filaire coupé' : isWifi ? 'Lien Wi-Fi déconnecté' : 'Modem déconnecté'}
            {' '}— aucune mesure disponible.
          </div>
        ) : isWired ? (
          /* Un lien filaire n'a aucune grandeur radio. Afficher des tuiles SINR
             ou RSRP marquées « non fourni » laisserait croire à des mesures
             manquantes, alors qu'elles n'ont simplement pas de sens ici. */
          <>
            {showThroughput && (
              <div className="tiles">
                <SignalTile
                  label="Débit descendant"
                  value={wan.rx_kbps === null ? null : wan.rx_kbps / 1000}
                  unit="Mbit/s"
                  digits={1}
                />
                <SignalTile
                  label="Débit montant"
                  value={wan.tx_kbps === null ? null : wan.tx_kbps / 1000}
                  unit="Mbit/s"
                  digits={1}
                />
              </div>
            )}

            <span className="section-label">Le lien</span>
            <div className="details">
              <Detail label="Adresse" value={wan.ip ?? '—'} />
              <OutboundPriority priority={wan.priority} />
              <Detail label="Coupures" value={coupures === null ? null : fmtInt(coupures)} />
              <Detail label="Connecté depuis" value={fmtUptime(wan.uptime_s)} />
              <Detail label="État" value={wan.status_message ?? '—'} />
              {/* La MTU vaut pour tous les supports : une MTU hétérogène entre
                  liens agrégés est une cause classique de fragmentation. */}
              {wan.mtu !== null && <Detail label="MTU" value={`${fmtInt(wan.mtu)} o`} />}
              {/* Sur un filaire, c'est la SEULE latence qui existe : il ne porte
                  pas toujours de tunnel, et jusqu'ici la carte n'en montrait
                  aucune. */}
              <Detail label="Latence du lien" value={latenceChemin} title={noteLatence} />
            </div>

            <p className="disclaimer">
              Lien filaire agrégé à la liaison. Il ne porte aucune grandeur radio — ni
              niveau, ni bande, ni cellule.
            </p>
          </>
        ) : isWifi ? (
          <>
            <div className="tiles">
              <SignalTile
                label="Force du signal"
                value={wan.rssi}
                unit="dBm"
                quality={wifiQuality(wan.rssi)}
                digits={0}
              />
              {/* Le niveau en barres a été retiré de la grille cellulaire, où il
                  ne bougeait jamais — mais rien ne le condamne ICI : sur le
                  Wi-Fi il sort d'un autre champ du routeur (`wifi.signal.level`,
                  et non `cellular.signalLevel`), et les deux WAN Wi-Fi du
                  MAX HD4 MBX sont désactivés, donc pas un seul relevé Wi-Fi de
                  ce matériel n'existe pour en juger. Supprimer faute de mesure
                  serait la même faute qu'afficher faute de mesure. */}
              <SignalTile
                label="Niveau"
                value={wan.signal_bars}
                unit="sur 5"
                digits={0}
              />
            </div>

            <span className="section-label">Le point d’accès</span>
            <div className="details">
              <Detail label="SSID" value={wan.wifi?.ssid ?? '—'} />
              <Detail label="BSSID" value={wan.wifi?.bssid ?? '—'} />
              <Detail label="Canal" value={fmtId(wan.wifi?.channel)} />
              <Detail label="Bande" value={wan.wifi?.band ?? '—'} />
              <Detail label="Coupures" value={coupures === null ? null : fmtInt(coupures)} />
              <Detail label="Connecté depuis" value={fmtUptime(wan.uptime_s)} />
            </div>

            <p className="disclaimer">
              Lien Wi-Fi amont agrégé aux modems cellulaires. Le routeur ne fournit qu'une
              force de signal : ni RSRP, ni RSRQ, ni SINR.
            </p>
          </>
        ) : (
          <>
            <div className="tiles">
              <SignalTile label="SINR" value={wan.sinr} unit="dB" quality={sinrQuality(wan.sinr)} />
              <SignalTile label="RSRP" value={wan.rsrp} unit="dBm" quality={rsrpQuality(wan.rsrp)} />
              <SignalTile label="RSRQ" value={wan.rsrq} unit="dB" quality={rsrqQuality(wan.rsrq)} />
              {showThroughput && (
                <SignalTile
                  label="Débit descendant"
                  value={wan.rx_kbps === null ? null : wan.rx_kbps / 1000}
                  unit="Mbit/s"
                  digits={1}
                />
              )}
            </div>

            {/* Cette grille ne porte plus que ce qui a été observé en train de
                varier sur le matériel. Cinq lignes en ont été retirées, chacune
                sur une mesure et non sur une impression :

                 - « Barres » : signal_bars valait 5 sur les 12 129 relevés
                   cellulaires du MAX HD4 MBX, sans une seule exception. Une
                   valeur qui ne bouge jamais ne renseigne sur rien ; verte en
                   permanence, elle rassurait même quand le lien décrochait.
                 - « PCI » : le Telit LM960 renvoie 0 sur toutes ses porteuses,
                   et le backend annule lui-même l'identifiant pour cette raison
                   (2 191 relevés, aucun exploitable). Sur les modems qui le
                   publient vraiment, il vaut de toute façon celui de la SEULE
                   porteuse principale, alors que le tableau des porteuses le
                   donne pour chacune — c'est là qu'il distingue deux cellules.
                 - « RSSI » : sur le cellulaire il suit le RSRP à une constante
                   près — écart mesuré entre 26 et 41 dB sur 12 129 relevés, la
                   puissance de bande contre la puissance par élément de
                   ressource. Il reste la mesure principale du Wi-Fi, où il n'a
                   pas de RSRP derrière lui, et wifiQuality() s'en sert.
                 - « EARFCN » : sur ce matériel chaque bande n'a été vue que sur
                   un seul canal (B1 → 524, B3 → 1300, B7 → 3000), et la bande
                   figure déjà en badge d'en-tête. Le canal reste utile porteuse
                   par porteuse : il survit en colonne du tableau ci-dessous.
                 - « PLMN » : 208-01 sur la totalité des 12 130 relevés
                   cellulaires, et le nom de l'opérateur est déjà dans l'en-tête.

                Restent ici les identifiants de cellule, qui bougent réellement
                (six Cell ID et trois TAC distincts sur la même période) et sur
                lesquels se lit un changement de site. */}
            {/* Deux grilles, deux sujets. Elles n'en formaient qu'une, où
                l'identité de la cellule — qui décrit le LIEU — voisinait la durée
                de session et le nombre de coupures, qui décrivent le LIEN. */}
            <span className="section-label">La cellule occupée</span>
            <div className="details">
              <Detail label="Cell ID" value={fmtId(wan.cell_id)} />
              <Detail label="Station (eNodeB)" value={fmtId(wan.enb_id)} />
              <Detail label="Secteur" value={fmtId(wan.sector_id)} />
              <Detail label="TAC" value={fmtId(wan.tac)} />
              <Detail label="Changements de cellule" value={sauts === null ? null : fmtInt(sauts)} />
            </div>

            <span className="section-label">Le lien</span>
            <div className="details">
              {showThroughput && (
                <Detail label="Débit montant" value={fmtThroughput(wan.tx_kbps)} />
              )}
              <OutboundPriority priority={wan.priority} />
              <Detail label="Coupures" value={coupures === null ? null : fmtInt(coupures)} />
              <Detail label="Connecté depuis" value={fmtUptime(wan.uptime_s)} />
              <Detail label="État" value={wan.status_message ?? '—'} />
              {/* Une MTU hétérogène entre liens agrégés est une cause classique
                  de fragmentation : elle n'a d'intérêt que comparée aux autres,
                  d'où sa place sur chaque carte plutôt que dans la fiche du
                  châssis. */}
              {wan.mtu !== null && <Detail label="MTU" value={`${fmtInt(wan.mtu)} o`} />}
              {wan.roaming_status !== null && (
                <Detail
                  label="Réseau"
                  value={wan.roaming_status === 'home' ? 'domestique' : wan.roaming_status}
                />
              )}
              {/* La latence du CHEMIN, distincte de celle du tunnel affichée
                  plus bas. Relevée une fois par minute : la dernière mesure est
                  montrée avec son âge, et disparaît au-delà de 90 s. */}
              <Detail label="Latence du lien" value={latenceChemin} title={noteLatence} />
              {temperatureModem !== null && (
                <Detail
                  label="Température du modem"
                  value={temperatureModem}
                  title="Température du module cellulaire, lue une fois par minute. Celle du châssis, quand le modèle la publie, est sur la fiche de l’équipement."
                />
              )}
            </div>

            {/* Le VOLUME de la période, et son plafond quand il y en a un. Les
                deux moitiés arrivaient dans des charges utiles déjà téléchargées
                à chaque cycle — `status.traffic` et `status.wan.connection` — et
                le parseur ne lisait ni l'une ni l'autre. Pour un véhicule de
                reportage, c'est le premier risque d'exploitation après la
                couverture. Zéro requête de plus. */}
            {(wan.period_rx_mb !== null || wan.period_tx_mb !== null) && (
              <>
                <span className="section-label">Volume de la période</span>
                <div className="details">
                  <Detail label="Montant" value={volumeMo(wan.period_tx_mb)} />
                  <Detail label="Descendant" value={volumeMo(wan.period_rx_mb)} />
                  {plafond !== null && (
                    <Detail
                      label="Plafond du WAN"
                      value={`${fmtInt(plafond.monthly_allowance_mb)} Mo`}
                    />
                  )}
                </div>
                <p className="disclaimer">
                  Le routeur ne date pas cette période : sa réponse ne porte ni début
                  ni numéro de mois. Qu’elle suive le mois calendaire ou le jour de
                  remise à zéro du plafond reste à vérifier sur un relevé à cheval sur
                  un premier du mois — d’ici là, l’écran ne la nomme pas.
                </p>
              </>
            )}

            {/* Le modem lui-même. Il explique les ABSENCES : ce projet sait que
                le Telit LM960 ne publie ni PCI ni largeur de canal, et l'écran ne
                le disait nulle part — deux colonnes de tirets se lisaient comme
                des mesures perdues plutôt que comme une limite du matériel. */}
            {(wan.modem_model !== null || wan.network_mode !== null) && (
              <div className="details">
                <Detail
                  label="Modem"
                  value={[wan.modem_vendor, wan.modem_model].filter(Boolean).join(' ') || '—'}
                />
                <Detail label="Version modem" value={wan.modem_firmware ?? '—'} />
                {wan.network_mode !== null && (
                  <Detail label="Sélection réseau" value={wan.network_mode} />
                )}
                {wan.carrier_aggregation !== null && (
                  <Detail
                    label="Agrégation"
                    value={wan.carrier_aggregation ? 'active' : 'inactive'}
                  />
                )}
              </div>
            )}

            <SimSlots slots={wan.sim_slots ?? []} />

            <BandTable bands={wan.bands ?? []} primaire={wan.band} canal={wan.earfcn} />
          </>
        )}

        {wan.tunnel && (
          <>
            {/* La perte est donnée PAR SENS, chacune avec le décompte dont elle
                sort. Le taux agrégé était le pire des deux sens : le montrer à
                côté d'un seul décompte aurait accolé un taux au dénominateur de
                l'autre sens. Le montant est lu en premier parce qu'il est la
                ressource rare — les porteuses cellulaires sont asymétriques —,
                et non parce qu'un usage particulier le rendrait prioritaire.

                Le désordre de paquets a disparu : 7 218 relevés sur ce matériel,
                pas un seul au-dessus de zéro. La colonne existait, la mesure
                non. */}
            <span className="section-label">Dans le bond</span>
            <div className="details">
              <Detail label="Participation" value={tunnelLabel(wan.tunnel)} />
              <Detail label="Latence vers le pair" value={fmtNumber(wan.tunnel.rtt_ms, 0, 'ms')} />
              <Detail
                label="Perte montante"
                value={fmtLoss(wan.tunnel.loss_percent_up, wan.tunnel.packets_up)}
              />
              <Detail
                label="Perte descendante"
                value={fmtLoss(wan.tunnel.loss_percent_down, wan.tunnel.packets_down)}
              />
              {/* Le débit porté PAR LE BOND, à ne pas confondre avec celui des
                  tuiles plus haut : celles-ci viennent de `status.traffic` et
                  comptent toute l'interface WAN, trafic hors tunnel compris. Ces
                  deux-ci ne comptent que l'agrégation. La donnée arrivait à chaque
                  cycle dans une charge utile déjà téléchargée, et était jetée. */}
              <Detail
                label="Dans le bond ↑"
                value={fmtNumber(
                  wan.tunnel.tx_kbps === null ? null : wan.tunnel.tx_kbps / 1000, 2, 'Mbit/s',
                )}
              />
              <Detail
                label="Dans le bond ↓"
                value={fmtNumber(
                  wan.tunnel.rx_kbps === null ? null : wan.tunnel.rx_kbps / 1000, 2, 'Mbit/s',
                )}
              />
              {/* Redondance de SpeedFusion : paquets de correction émis, et
                  paquets reconstitués grâce à elle. C'est de la capacité montante
                  dépensée à réparer plutôt qu'à transporter — invisible jusqu'ici,
                  et c'est ce qui se dégrade AVANT que la perte n'apparaisse. */}
              {(wan.tunnel.repair_sent ?? 0) > 0 && (
                <Detail label="Paquets de réparation" value={String(wan.tunnel.repair_sent)} />
              )}
              {(wan.tunnel.repair_used ?? 0) > 0 && (
                <Detail label="Paquets reconstitués" value={String(wan.tunnel.repair_used)} />
              )}
            </div>

            {(tooFewPackets(wan.tunnel.packets_up) ||
              tooFewPackets(wan.tunnel.packets_down)) && (
              <p className="disclaimer">
                Moins de {LOSS_MIN_PACKETS} paquets comptés sur l'intervalle : le taux n'est
                pas publié. Sur un tunnel au repos, le seul trafic est un keepalive de
                quelques paquets, et un unique paquet manquant y affiche 33 % de perte sur
                un lien parfaitement sain.
              </p>
            )}
          </>
        )}
      </div>
    </section>
  )
}
