import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import type { RadioSample } from '../types'
import type { KeyFigures } from '../lib/dashboard'
import { QUALITY_COLOR, couleurDuLien } from '../lib/quality'
import { accord, ecarteDuBond } from '../lib/verdict'
import type { Redundancy } from '../lib/redundancy'
import { porteLeFlux, type BondRole } from '../lib/bondRole'
import { decimal, etiquetteSupport, fini, fmtId, fmtInt, fmtNumber, fmtUptime } from '../lib/format'
import { lienWan } from '../lib/routes'
import { Note, Notes, Absent } from './Note'

interface Props {
  /** Volume échangé depuis le premier relevé du stationnement (A-5), et
   *  l'heure de ce relevé. Absent tant que la fiche n'est pas lue. */
  volume?: { up: number | null; down: number | null; depuis: string } | null
  /** Liens jamais raccordés depuis l'allumage : rail neutre, pas rouge (A-6). */
  neverConnected?: string[]
  samples: RadioSample[]
  figures: KeyFigures
  /** Ce que la redondance du bond dépense — et ce que son silence établit. */
  redundancy: Redundancy
  /** Cellules et stations distinctes : le quatrième total dit si l'agrégation
   *  emprunte vraiment des chemins différents, ou seulement plusieurs modems. */
  cells: number
  sites: number
  located: number
  /** Liens dont la STATION a pu être déterminée. */
  attached: number
  /** À quoi sert le bond : chemin du trafic, ou tunnel vers quelques routes. */
  role: BondRole
  /** Le serveur a-t-il vu le bond porter dans cette session ? `false` : jamais,
   *  et le plan ne le présente pas comme le chemin du trafic (C-17). */
  tunnelAttendu?: boolean | null
  /** Les griefs que le verdict de la liaison a REFUSÉS parce qu'ils ne décrivent
   *  que le tunnel. Vides quand le tunnel est le chemin du trafic : ils sont
   *  alors dans le bandeau, à leur place. */
  grievances: string[]
  /** Depuis quand le bond TIENT, en secondes, selon le routeur. « Depuis quand
   *  le tunnel est-il monté ? » n'avait aucune réponse dans l'outil, alors que
   *  la valeur arrive dans une charge utile déjà lue à chaque sonde. */
  tunnelUptimeS: number | null
  /** Plafond de débit imposé par la licence SpeedFusion, en Mbit/s. */
  throughputLimitMbps: number | null
  /** Identifiant de l'équipement, pour que chaque rail mène à sa fiche. */
  routerId: string
  /** Les cellules occupées et, pour chacune, les liens qui s'y trouvent. Ce
   *  n'est pas une décoration : quatre modems sur une même cellule n'offrent
   *  AUCUNE diversité de trajet, et c'est le risque structurel de ce véhicule —
   *  mesuré à 99,7 % du temps sur une halte. Le plan le dessine ; la table le
   *  disait en badge. */
  cellGroups: Map<string, string[]>
}

/** Géométrie du tracé. Le viewBox est fixe et le SVG s'étire en largeur : la
 *  hauteur suit le nombre de liens, pas l'inverse. */
/** Le tracé s'étire en largeur à rapport constant : sur un écran large, sa
 *  hauteur suivait donc la largeur et le plan mangeait deux cent soixante
 *  pixels pour cinq rails. Un viewBox plus large à hauteur égale rend le même
 *  dessin plus plat, sans rien déformer. */
// Le viewBox s'est resserré en même temps que le texte du nœud est sorti du
// dessin : à 1400 unités pour 178 de haut, cinq rails s'étiraient sur un ruban
// de quatre-vingts pixels où la convergence ne se lisait plus. Neuf cents rend
// au tracé sa pente.
const VUE = 900
const LEFT = 76
// Les étiquettes finissent ici, ancrées à droite : elles poussent vers la
// gauche au lieu de courir vers le dessin, donc leur longueur ne peut plus
// entrer en collision ni avec la ligature ni avec l'origine des rails.
const LABEL_FIN = LEFT - 16
// Le nœud recule : le libellé « SpeedFusion » et ses deux lignes de détail
// occupaient tout le quart droit du tracé, à onze pixels de viewBox — illisibles
// et encombrants. Ils vivent désormais dans la LECTURE, à côté du dessin, où ils
// ont la place de se lire et où ils changent au survol d'un rail.
const JOIN = 0.82
const OUT = 0.99
const ROW = 30
const PAD = 14


/** Épaisseur d'un rail, en pixels de viewBox.
 *
 *  Elle dit le débit PORTÉ, et c'est tout l'intérêt : sur le matériel de
 *  référence une fibre transporte la totalité du flux pendant que quatre SIM
 *  attendent à zéro, et cinq tuiles de même taille racontaient exactement le
 *  contraire. Le minimum de 1,5 px n'est pas une valeur plancher déguisée en
 *  mesure : un lien qui ne porte rien reste TRACÉ, parce qu'il existe, mais le
 *  chiffre à côté dit zéro.
 */
function thickness(kbps: number | null, top: number): number {
  if (kbps === null || kbps <= 0 || top <= 0) return 1.5
  return 1.5 + Math.sqrt(kbps / top) * 10.5
}

/**
 * La convergence, dessinée.
 *
 * Le sujet de cet outil est UNE liaison faite de N chemins physiques réunis dans
 * un bond. Une liste de tuiles n'exprime aucune convergence — c'est la mise en
 * page d'une liste de serveurs, et elle laisse croire que les liens sont des
 * objets indépendants qui se trouvent côte à côte.
 *
 * Trois faits se lisent d'un coup d'œil, sans lire un chiffre : combien de
 * chemins existent, lequel porte réellement le flux, et lequel est écarté du
 * bond — celui-là rejoint le nœud en pointillé. C'est la distinction que le
 * modèle de données porte depuis le début et que l'écran réduisait à un badge.
 */
export function BondPlan({
  samples, figures, redundancy, cells, sites, located, attached, role, grievances,
  tunnelUptimeS, throughputLimitMbps, routerId, cellGroups, tunnelAttendu = null,
  neverConnected = [], volume = null,
}: Props) {
  // Le bond porte-t-il le trafic ? Deux témoins, établis par `DeviceShell` :
  // ce que le pair ANNONCE, et ce qui TRANSITE. Le premier seul ne suffisait
  // pas — le 13 septembre, l'activation du trafic SpeedFusion a fait passer
  // vingt-six mille paquets dans le tunnel sans changer d'un mot les routes
  // annoncées, et l'écran a continué d'affirmer que le bond n'était pas le
  // chemin du trafic.
  // Et la MÊME règle que le bandeau : un bond jamais établi dans la session ne
  // porte pas le flux (C-17).
  const porte = porteLeFlux(role, tunnelAttendu)
  const hauteur = PAD * 2 + Math.max(1, samples.length) * ROW
  const centre = hauteur / 2
  // Un débit NON FINI n'est pas une mesure : `Math.max(0, NaN)` vaut NaN, et
  // ce NaN devenait le `top` de tous les rails — mesuré : « stroke-width="NaN" »
  // sur le lien sain à 1 000 kbit/s comme sur le malformé, et sur le brin de
  // sortie. Le navigateur ignore l'attribut et trace tout à un pixel ; la
  // règle « épaisseur = débit » s'effondrait sans un mot, et le balayage de
  // texte ne pouvait pas le voir — c'est un attribut.
  const debits = samples.map((s) => {
    const v = s.wan.tunnel?.tx_kbps ?? s.wan.tx_kbps
    return fini(v) ? v : null
  })
  // Un état de tunnel NUL veut dire inconnu : on n'accuse pas le lien d'être
  // hors du bond sur une absence de mesure. Calculé ici plutôt que dans la
  // boucle de tracé, parce que le brin de sortie en a besoin AUSSI — il
  // sommait jusqu'ici les liens que le dessin montre comme n'alimentant rien.
  const rejoints = samples.map((s) => s.wan.connected && !ecarteDuBond(s.wan))
  const top = Math.max(0, ...debits.filter((v): v is number => v !== null))

  const [survole, setSurvole] = useState<string | null>(null)
  const lu = survole === null
    ? null
    : samples.find((s) => s.wan.wan_id === survole) ?? null
  // L'identifiant EFFECTIVEMENT survolé. `survole` seul survit à la
  // disparition de son rail : un modem qui tombe pendant que le pointeur est
  // dessus démonte le `<g>`, `mouseleave` ne part jamais, et tout le plan
  // restait atténué à 28 % jusqu'au survol suivant.
  const actif = lu?.wan.wan_id ?? null

  // La somme portée par les seuls liens qui REJOIGNENT, et `null` quand aucun
  // d'eux n'a publié de débit — `reduce((n, v) => n + (v ?? 0), 0)` rendait 0
  // pour une absence de mesure, que le plancher de 4 px rendait ensuite
  // indiscernable d'un bond mesuré à l'arrêt. C'est la règle de `somme()` dans
  // `lib/dashboard.ts`, appliquée ici.
  const portes = debits.filter((v, i): v is number => v !== null && rejoints[i])
  const totalPorte = portes.length ? portes.reduce((a, b) => a + b, 0) : null

  const trace = (y: number) =>
    `M ${LEFT} ${y} C ${VUE * 0.5} ${y}, ${VUE * 0.66} ${centre}, ${VUE * JOIN} ${centre}`

  // La cible de survol s'arrête AVANT la convergence. Elle suivait la courbe
  // entière : or tous les rails finissent au même point, donc au-delà de la
  // moitié du dessin les cinq pistes larges de 26 unités se superposaient et le
  // navigateur attribuait le survol au dernier rail rendu. Mesuré sur la page :
  // en visant le rail du haut, on recevait SIM 5, puis SIM 6, puis SIM 7 à
  // mesure qu'on avançait vers le nœud. Sur le segment plat, les rails restent
  // séparés de ROW et chacun reste à soi.
  const piste = (y: number) => `M ${LEFT} ${y} L ${VUE * 0.46} ${y}`

  // Les groupes de rails qui partagent une cellule, réduits à leurs bornes
  // verticales. Ils VIENNENT de `pathDiversity` : le plan les reconstruisait
  // depuis la vue par voisin, en recollant des wan_id avec des « + » pour les
  // redécouper aussitôt — une seconde implémentation du même regroupement, non
  // testée, et qui aurait fusionné deux groupes si un wan_id contenait un « + ».
  const groupes = useMemo(() => {
  const rang = new Map(samples.map((s, i) => [s.wan.wan_id, i]))
  // La colonne d'une accolade compte les groupes DESSINÉS, pas les entrées de
  // `cellGroups` : celles-ci comptent aussi chaque cellule occupée par un seul
  // lien, qui ne dessine rien, et l'accolade partait vers la gauche d'autant —
  // mesurée à x = 60, l'ancre des étiquettes, avec deux cellules à un lien
  // devant elle.
  let colonne = 0
  return [...cellGroups.entries()].flatMap(([cle, membres]) => {
    const rangs = membres.map((w) => rang.get(w))
      .filter((r): r is number => r !== undefined)
      .sort((a, b) => a - b)
    // Un groupe d'un seul lien n'en est pas un : il occupe une cellule à lui.
    if (rangs.length < 2) return []
    const x = colonne++
    // Découpage en suites CONTIGUËS. Une seule accolade des bornes du groupe
    // affirmait que tout ce qu'elle enjambe partage la cellule : avec des rails
    // 4 et 7 sur une même cellule, elle englobait 5 et 6, qui n'y sont pas.
    const suites: number[][] = []
    for (const r of rangs) {
      const derniere = suites[suites.length - 1]
      if (derniere && r === derniere[derniere.length - 1] + 1) derniere.push(r)
      else suites.push([r])
    }
    // Chaque suite reçoit son accolade — y compris une suite d'UN rail, qui
    // devient un simple repère dans la même colonne. Les écarter effaçait le
    // cas le plus courant à deux opérateurs : SIM 4 et SIM 6 sur une cellule,
    // SIM 5 d'un autre opérateur entre elles — vérifié par rendu, zéro
    // ligature, là où l'ancienne accolade des bornes dessinait trop mais
    // dessinait. Deux repères dans une même colonne disent « ces deux-là »
    // sans enrôler celui du milieu.
    return suites.map((suite) => ({
      cle: `${cle}#${suite[0]}`,
      // Chaque groupe a sa colonne : deux groupes entrelacés se dessinaient
      // au même x et ne faisaient plus qu'une accolade à l'œil.
      x,
      haut: PAD + ROW * suite[0] + ROW / 2,
      bas: PAD + ROW * suite[suite.length - 1] + ROW / 2,
    }))
  })
  }, [samples, cellGroups])

  return (
    <section className="panel plan">
      <div className="panel-head">
        <h2>Le bond</h2>
        <span className="note">
          {samples.length === 1 ? 'un seul chemin physique' : `${samples.length} chemins physiques`}
          {!figures.hasTunnel
            ? ' · aucun tunnel configuré'
            : porte
              ? ' · réunis en une liaison · épaisseur = débit porté'
              : ' · le tunnel n’achemine que ce qui lui est routé'}
        </span>
      </div>

      <div className="plan-canvas">
        <svg viewBox={`0 0 ${VUE} ${hauteur}`}>
          {/* Le dessin portait `role="img"` : ses descendants devenaient alors
              une image unique, alors qu'ils sont depuis peu des liens au
              clavier. Un `<title>` nomme le dessin sans effacer ce qu'il
              contient. */}
          <title>{`${samples.length} liens convergeant vers le bond`}</title>
          <defs>
            <linearGradient id="sortie" x1="0" x2="1">
              <stop offset="0" style={{ stopColor: 'var(--bond)' }} stopOpacity="0.12" />
              <stop offset="1" style={{ stopColor: 'var(--bond)' }} stopOpacity="0.5" />
            </linearGradient>
          </defs>

          {/* Les liens qui PARTAGENT une cellule, liés par une ligature.
              Quatre modems sur une même cellule n'offrent aucune diversité de
              trajet : c'est le risque structurel de ce véhicule, mesuré à 99,7 %
              du temps sur une halte, et le plan le taisait — seule la table le
              portait, en badge, une ligne à la fois. Une ligature dit « ces
              rails-là sont un seul chemin » sans prétendre qu'ils fusionnent. */}
          {groupes.map((g) => {
            // Le tracé de l'accolade, une seule fois : la cible transparente
            // et le trait visible suivent exactement le même chemin.
            const x = LEFT - 4 - g.x * 6
            const accolade = `M ${x} ${g.haut} L ${x - 5} ${g.haut} L ${x - 5} ${g.bas} L ${x} ${g.bas}`
            return (
            <g key={g.cle} className="plan-tie">
              {/* Une accolade dans la gouttière, pas un trait posé sur les
                  rails : elle dit « ces chemins-là n'en font qu'un » sans
                  prétendre qu'ils fusionnent, et sans rien recouvrir. */}
              {/* `<title>` en PREMIER : c'est ce que la spécification SVG
                  demande, et l'infobulle d'un groupe s'y attache. */}
              <title>
                Ces liens partagent une cellule : ils n’offrent aucune diversité de trajet.
              </title>
              {/* Une piste transparente, comme pour les rails : l'accolade est
                  un trait d'une unité et demie, soit moins d'un pixel rendu —
                  son infobulle était inatteignable à la souris. */}
              <path
                d={accolade}
                className="plan-cible"
                fill="none" stroke="transparent" strokeWidth="10"
              />
              <path d={accolade} fill="none" strokeWidth="1.5" />
            </g>
            )
          })}

          {samples.map((s, i) => {
            const y = PAD + ROW * i + ROW / 2
            const dessin = trace(y)
            const colour = neverConnected.includes(s.wan.wan_id) && !s.wan.connected
              ? QUALITY_COLOR.inconnu
              : couleurDuLien(s.wan)
            // Un état de tunnel NUL veut dire inconnu : on n'accuse pas le lien
            // d'être hors du bond sur une absence de mesure.
            const rejoint = rejoints[i]
            const vise = actif === null || actif === s.wan.wan_id
            return (
              <Link
                key={s.wan.wan_id}
                to={lienWan(routerId, s.wan.wan_id)}
                className="plan-rail"
                // `tabIndex={0}` explicite : Safari, avec son réglage par défaut,
                // saute les liens au Tab et ne s'arrête que sur les éléments
                // qui portent un tabindex. Passer de `<g tabIndex>` à `<Link>`
                // avait retiré le rail du parcours clavier sur ce navigateur.
                tabIndex={0}
                onMouseEnter={() => setSurvole(s.wan.wan_id)}
                onMouseLeave={() => setSurvole(null)}
                onFocus={() => setSurvole(s.wan.wan_id)}
                onBlur={() => setSurvole(null)}
                aria-label={`${etiquetteSupport(s.wan.kind)} ${s.wan.wan_id} — voir le détail`}
                style={{ opacity: vise ? 1 : 0.28 }}
              >
                {/* Une piste de survol large et invisible : viser un trait de
                    deux pixels à la souris est un exercice, pas une commande. */}
                <path
                  className="plan-cible"
                  d={piste(y)}
                  fill="none" stroke="transparent" strokeWidth={ROW - 4}
                />
                <path
                  d={dessin}
                  fill="none"
                  stroke={colour}
                  strokeWidth={rejoint ? thickness(debits[i], top) : 1.5}
                  strokeDasharray={rejoint ? undefined : '3 4'}
                  opacity={s.wan.connected ? 0.95 : 0.4}
                  className={actif === s.wan.wan_id ? 'plan-trace is-actif' : 'plan-trace'}
                />
                <text x={LABEL_FIN} y={y + 4} textAnchor="end" className="plan-label">
                  {etiquetteSupport(s.wan.kind)} {s.wan.wan_id}
                </text>
              </Link>
            )
          })}

          {/* Le brin de sortie porte la somme des liens qui REJOIGNENT, sur la
              même échelle que les rails — c'est-à-dire relative au lien le plus
              chargé, comme eux. Il ne dit donc pas un débit absolu, et la
              version précédente le prétendait : en normalisant la somme par le
              maximum du même ensemble, son épaisseur ne mesurait plus que la
              RÉPARTITION. Un lien seul rendait 12 px qu'il porte 100 kbit/s ou
              500 Mbit/s ; deux liens à 100 kbit/s rendaient 16 px, soit un brin
              plus épais pour un bond mille fois moins chargé.
              Sans mesure, pas de brin : une absence n'est pas un zéro. */}
          {totalPorte !== null && (
            <path d={`M ${VUE * JOIN} ${centre} L ${VUE * OUT} ${centre}`}
                  fill="none" stroke="url(#sortie)"
                  strokeWidth={Math.max(4, thickness(totalPorte, top))} />
          )}
          <circle className="plan-node" cx={VUE * JOIN} cy={centre} r="6" />
        </svg>
      </div>

      {/* LA LECTURE. Au repos elle décrit le bond ; au survol d'un rail, ce
          lien-là. C'est ce qui transforme un schéma en instrument : le plan
          disait la forme, il dit maintenant aussi les chiffres, sans les
          entasser sur le dessin ni les répéter de la table. */}
      <div className="plan-lecture">
        {lu === null ? (
          <>
            <b className="plan-lecture-titre">
              {figures.hasTunnel ? 'SpeedFusion' : 'Sortie directe'}
            </b>
            <span className="plan-lecture-sous">
              {!figures.hasTunnel
                ? 'aucune agrégation'
                : figures.carrying === null
                  ? 'état du tunnel inconnu'
                  : porte
                    ? `${figures.carrying} ${accord(figures.carrying, 'lien')} ${accord(figures.carrying, 'porte', 'portent')}`
                    : `${figures.carrying} ${accord(figures.carrying, 'lien')} · routes dédiées`}
            </span>
            {figures.hasTunnel && tunnelUptimeS !== null && (
              <Ligne cle="Monté depuis" val={fmtUptime(tunnelUptimeS)} />
            )}
            {figures.hasTunnel && (
              <Ligne
                cle="Latence médiane"
                val={figures.rttMedianMs === null ? null : fmtNumber(figures.rttMedianMs, 0, 'ms')}
              />
            )}
            <Ligne cle="Cellules" val={located > 1 ? `${cells} sur ${located} liens situés` : null} />
            <span className="plan-lecture-aide lbl">
              <span className="au-survol">Survolez un rail.</span>
              <span className="au-toucher">Touchez un rail pour sa fiche.</span>
            </span>
          </>
        ) : (
          <>
            <b className="plan-lecture-titre">
              {etiquetteSupport(lu.wan.kind)} {lu.wan.wan_id}
            </b>
            <span className="plan-lecture-sous">
              {[lu.wan.operator_name, lu.wan.rat, lu.wan.band].filter(Boolean).join(' · ') || '—'}
            </span>
            <Ligne
              cle="Débit ↑ / ↓ Mbit/s"
              val={
                lu.wan.tx_kbps === null && lu.wan.rx_kbps === null
                  ? null
                  : `${fmtNumber(lu.wan.tx_kbps === null ? null : lu.wan.tx_kbps / 1000, 1)}`
                    + ` / ${fmtNumber(lu.wan.rx_kbps === null ? null : lu.wan.rx_kbps / 1000, 1)}`
              }
            />
            <Ligne
              cle="Latence bond"
              val={lu.wan.tunnel?.rtt_ms == null ? null : fmtNumber(lu.wan.tunnel.rtt_ms, 0, 'ms')}
            />
            <Ligne
              cle="Latence lien"
              val={lu.wan.path_latency_recent_ms === null ? null : fmtNumber(lu.wan.path_latency_recent_ms, 0, 'ms')}
            />
            {lu.wan.kind === 'cellular' && (
              <Ligne cle="Cellule" val={lu.wan.cell_id === null ? null : fmtId(lu.wan.cell_id)} />
            )}
            <Ligne cle="État" val={lu.wan.status_message} />
            <span className="plan-lecture-aide lbl">Cliquez pour le détail.</span>
          </>
        )}
      </div>

      <div className="plan-totals">
        {/* Le débit d'INTERFACE en tête : c'est le trafic réel, celui qui sort
            de l'équipement. Le débit du bond y figurait à sa place et valait
            zéro en permanence — mesuré sur 35 751 relevés, le tunnel de ce
            déploiement a transporté au plus 2,6 kbit/s, soit un keepalive, quand
            les interfaces montaient à 75 Mbit/s en émission et 262 en réception.
            Le chiffre de tête du tableau de bord était donc un zéro qui ne
            bougeait jamais, et le trafic réel n'apparaissait nulle part dans les
            totaux. */}
        <div className="tot is-lead">
          <span className="v"><Pair up={figures.txKbps} down={figures.rxKbps} /></span>
          <span className="lbl">Mbit/s ↑ / ↓ · interface</span>
        </div>

        {/* Le débit dit l'instant ; le volume dit le stationnement. Seul, le
            premier affichait « 0,0 » pendant qu'un rapport comptait 1,5 Go
            descendus sur la même session (A-5). Un volume non mesuré — le
            Dome ne publie aucun débit par WAN — n'est pas un volume nul. */}
        {volume && (volume.up !== null || volume.down !== null) && (
          <div className="tot">
            <span className="v">
              {volume.up === null ? <Absent /> : decimal(volume.up, volume.up < 100 ? 1 : 0)}
              <small> / </small>
              {volume.down === null ? <Absent /> : decimal(volume.down, volume.down < 100 ? 1 : 0)}
            </span>
            <span className="lbl">Mo ↑ / ↓ · depuis {volume.depuis}</span>
          </div>
        )}

        {/* Le bond à côté, et non à la place : leur ÉCART est l'information.
            Un tunnel monté qui ne transporte rien se lit d'un coup d'œil quand
            les deux chiffres se font face. */}
        {figures.hasTunnel && (
          <div className="tot">
            <span className="v">
              <Pair up={figures.bondTxKbps} down={figures.bondRxKbps} />
            </span>
            <span className="lbl">Mbit/s ↑ / ↓ · dans le bond</span>
          </div>
        )}

        <div className="tot">
          <span className="v">
            {figures.carrying ?? <Absent />} <small>/ {figures.total}</small>
          </span>
          <span className="lbl">
            {/* « Portent » suppose que le tunnel EST le chemin du trafic. Quand
                il ne l'est pas, les liens y sont bien — mais ils ne portent
                rien, et le dire était faux. */}
            {!figures.hasTunnel
              ? 'liens connectés'
              : porte
                ? 'liens qui portent'
                : 'liens dans le bond'}
            {/* Ce que le routeur a cessé d'employer est retiré du compte ET dit :
                l'écran annonçait « 5 liens portent » pendant que la table
                marquait l'un d'eux « au-delà du seuil ». */}
            {figures.excludedByLatency > 0 &&
              ` · ${figures.excludedByLatency} écarté${figures.excludedByLatency > 1 ? 's' : ''} par la latence`}
          </span>
        </div>

        {(figures.hasTunnel || figures.rttSource === 'lien') && (
          <div className="tot">
            <span className="v">
              {/* `Math.round` ne garde que de `null` : sur un nombre malformé —
                  une chaîne vide convertie, une division par zéro chez le
                  collecteur — il rend « NaN », que l'écran affichait alors avec
                  l'aplomb d'une mesure. `fmtNumber` écarte les deux infinis et
                  NaN d'un coup. Trouvé par la suite de confrontation, sur un
                  profil d'équipement qui publie des nombres malformés. */}
              {fmtNumber(figures.rttMedianMs, 0)}
              {/* Le pire à côté de la médiane : quatre SIM peuvent partager la
                  même médiane à 32 ms et culminer de 56 à 1 929 ms. */}
              {fini(figures.rttWorstMs) && fini(figures.rttMedianMs) &&
                figures.rttWorstMs > figures.rttMedianMs * 1.5 && (
                  <small> · {fmtNumber(figures.rttWorstMs, 0)} max</small>
                )}
            </span>
            <span className="lbl">
              {figures.rttSource === 'lien' ? 'latence médiane des liens, ms' : 'latence médiane, ms'}
            </span>
          </div>
        )}

        {/* Un FAIT, pas une alarme. Sur ce matériel, 100 % des minutes où deux
            SIM sont connectées ont un partage de station : un avertissement
            allumé en permanence est du papier peint, et on finit par ne plus
            lire les autres. */}
        {located > 1 && (
          <div className="tot">
            <span className="v">
              {/* Chaque grandeur reste soudée à son unité — la ligne peut
                  revenir entre les deux groupes, jamais au milieu de l'un
                  d'eux. `white-space: nowrap` posé sur tout le `.v` gardait
                  bien « 2 cellules · 1 site » d'un seul tenant, mais dans une
                  grille `auto-fit` dont les pistes ne rétrécissent pas sous
                  leur contenu, il transformait le retour à la ligne en
                  DÉBORDEMENT par-dessus la tuile voisine. */}
              <span className="soude">{cells} <small>cellule{cells > 1 ? 's' : ''}</small></span>
              {/* Une station qu'on n'a pas su déterminer n'est pas « zéro
                  station » : le parseur refuse de décomposer un identifiant
                  5G SA, « le découpage serait une invention ». L'écran écrivait
                  « N cellules · 0 site », un zéro à la place d'une absence — là
                  où `located` traite correctement le cas symétrique. */}
              {attached > 0 ? (
                <> · <span className="soude">{sites} <small>site{sites > 1 ? 's' : ''}</small></span></>
              ) : (
                <> · <small className="absent">station indéterminable</small></>
              )}
            </span>
            <span className="lbl">trajets radio distincts</span>
          </div>
        )}
      </div>

      {/* La bande d'ensemble vivait ici, et n'y servait à rien : elle existe pour
          dire si les creux des liens sont DÉCALÉS — les uns couvrent les autres —
          ou ALIGNÉS, et c'est alors une vraie coupure. Cette lecture demande la
          même échelle de temps que les bandes par lien, à la verticale. Elle est
          donc passée en pied de la table des liens, dans leur propre colonne. */}

      {/* Ce que le bond ACHEMINE. Sans cette liste, un débit nul s'y lisait
          comme une panne : le pair déclare lui-même les sous-réseaux qu'il
          route, et l'absence de la route par défaut prouve que le trafic général
          sort par les interfaces — ce n'est pas une déduction tirée d'un zéro. */}
      <Notes>
{(role.kind === 'routes' || role.kind === 'porte') && (
        <Note pourquoi={
          role.kind === 'routes'
            ? "Le pair annonce " + role.routes.join(', ') + " et la route par défaut n'y "
              + "figure pas ; moins de mille paquets ont traversé le tunnel sur la fenêtre "
              + "relue — un keepalive. Les débits, latences et pertes du bond ne décrivent "
              + "donc que ces routes-là, pas votre trafic."
            : "Le pair annonce " + role.routes.join(', ') + " sans la route par défaut, "
              + "mais des paquets traversent bel et bien le tunnel : ce qu'un pair annonce "
              + "dit ce qu'il sait joindre, pas ce que le routeur décide de lui envoyer — "
              + "la politique de sortie est locale."
        }>
          {role.kind === 'routes'
            ? <><b>Rien ne transite par le bond.</b> Les grandeurs ci-dessous ne décrivent pas votre trafic.</>
            : <><b>Le bond porte le trafic.</b></>}
        </Note>
      )}

      {/* L'état du tunnel, dit LÀ où il décrit quelque chose.
          Le bandeau annonçait « Liaison dégradée — 1 en latence excessive
          (353 ms contre 19 ms) » : la mesure est juste, mais elle porte sur le
          trajet vers le pair, que le trafic de l'exploitant n'emprunte pas.
          Elle n'a pas disparu pour autant — une latence qui s'envole vers le
          concentrateur reste le symptôme d'un lien en peine. Elle a changé de
          sujet : ce n'est plus un verdict sur la liaison, c'est un état du
          tunnel, et il se lit à côté des routes qu'il dessert. */}
      {/* Le plafond de la licence, lu à l'appairage et jamais regardé. Il ne
          s'affiche que s'il existe ET qu'un tunnel est monté : hors de ce cas,
          il ne décrit rien. */}
      {figures.hasTunnel && throughputLimitMbps !== null && (
        <Note pourquoi="Plafond imposé par la licence de l'équipement, quelle que soit la capacité des liens agrégés.">
          Tunnel plafonné à <b>{fmtNumber(throughputLimitMbps, 0)} Mbit/s</b>.
        </Note>
      )}
      {grievances.length > 0 && (
        <Note pourquoi={
          "Ces mesures portent sur le trajet vers le pair — elles ne décrivent ni votre "
          + "trafic, qui sort par les interfaces, ni l'état des liens eux-mêmes, qui est "
          + "dans la table. Elles ne font donc pas le verdict de la liaison ; elles disent "
          + "ce que subissent les routes desservies par le bond."
        }>
          <b>Dans le tunnel :</b> {grievances.join(', ')}.
        </Note>
      )}

      {/* La redondance du bond : un FAIT de configuration, énoncé seulement
          quand assez de trafic l'établit, et jamais comme une alarme. */}
      {figures.hasTunnel && redundancy.state !== 'unknown' && (
        redundancy.state === 'off' ? (
          <Note pourquoi={
            fmtInt(redundancy.packets) + " paquets montants ont traversé le tunnel sans "
            + "qu'un seul ne soit émis en correction ni reconstitué à l'arrivée. "
            + (porte
                ? "Chaque paquet perdu sur un lien est donc perdu pour le flux. "
                : "Sans conséquence pour votre trafic, qui n'emprunte pas le tunnel — mais "
                  + "ce qui y est routé n'a aucune protection. ")
            + "Les activer se règle depuis l'administration du routeur, et se paie en "
            + "capacité montante : 13 à 27 % pour la correction d'erreur, le double ou le "
            + "triple du trafic pour le lissage."
          }>
            <b>Aucune redondance sur ce bond.</b>
          </Note>
        ) : (
          <Note pourquoi={
            fmtInt(redundancy.repair) + " paquets émis en correction ou en duplication pour "
            + fmtInt(redundancy.packets) + " transportés. C'est le prix payé pour que la "
            + "perte d'un lien ne se voie pas dans le flux — et il monte AVANT que la perte "
            + "n'apparaisse."
          }>
            <b>{fmtNumber(redundancy.share * 100, 1)} % de la capacité montante</b> part en
            redondance.
          </Note>
        )
      )}
</Notes>
    </section>
  )
}

/* `Routes` vivait ici : elle mettait en ligne les sous-réseaux annoncés par le
   pair. Ils sont passés en infobulle avec le reste de la justification — leur
   liste n'apprend rien à qui balaie l'écran, et tout à qui doute du verdict. */

/** Un couple montant / descendant. Une grandeur non mesurée s'écrit « — ». */
function Pair({ up, down }: { up: number | null; down: number | null }) {
  const mbit = (k: number | null) => (k === null ? '—' : fmtNumber(k / 1000, 1))
  return (
    <>
      {mbit(up)} <small>/ {mbit(down)}</small>
    </>
  )
}


/** Une ligne de la lecture. Une valeur absente rend un tiret et NON une ligne
 *  effacée : savoir qu'une grandeur existe et n'a pas été mesurée vaut mieux
 *  que de la voir disparaître, ce qui se lit comme « ce lien n'en a pas ». */
function Ligne({ cle, val }: { cle: string; val: string | null }) {
  return (
    <span className="plan-lecture-ligne">
      <span className="plan-lecture-cle">{cle}</span>
      <span className="plan-lecture-val num">{val ?? <Absent />}</span>
    </span>
  )
}
