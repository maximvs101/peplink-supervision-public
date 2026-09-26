import { useState } from 'react'

import type { EventLog as Log, Episode } from '../types'
import { QUALITY_COLOR } from '../lib/quality'
import { fmtInt, fmtNumber, fmtUptime, etiquetteSupport } from '../lib/format'
import { Note, Notes, Absent } from './Note'

interface Props {
  log: Log | null
  windowLabel: string
  /** Tout rendre, sans bouton : le rapport imprimé (R-3.7) ne sait pas presser
   *  « Voir les autres », et un PDF qui en tait vingt-six ment par omission. */
  complet?: boolean
}

/** Ce que chaque genre d'épisode vaut à l'écran : son libellé, et le rail qui
 *  le classe. Une union fermée — un genre inconnu n'est pas colorié au hasard. */
const GENRES: Record<string, { label: string; colour: string }> = {
  coupure: { label: 'Coupure', colour: QUALITY_COLOR.faible },
  retour: { label: 'Retour', colour: QUALITY_COLOR.excellent },
  'micro-coupure': { label: 'Micro-coupure', colour: QUALITY_COLOR.moyen },
  // Deux genres, parce que ce sont deux faits différents. « Écarté » est une
  // action DU ROUTEUR, constatée : le firmware a cessé d'employer ce lien parce
  // qu'il dépassait de plus de son seuil le plus rapide du bond. « Latence
  // excessive » est une appréciation de cet outil, quand son garde-fou absolu se
  // déclenche sans que le routeur ait rien fait — mesuré, les dix-huit relevés
  // de la base au-delà de mille millisecondes n'ont JAMAIS été écartés.
  // Les confondre faisait affirmer à l'écran une action qui n'avait pas eu lieu.
  latence: { label: 'Écarté par la latence', colour: QUALITY_COLOR.moyen },
  'latence haute': { label: 'Latence excessive', colour: QUALITY_COLOR.moyen },
  'position perdue': { label: 'Position perdue', colour: QUALITY_COLOR.moyen },
  'position retrouvée': { label: 'Position retrouvée', colour: QUALITY_COLOR.excellent },
  // Le lien est debout : ce n'est pas une coupure, et le rail ne doit pas le
  // dire. Mais il ne transporte plus rien, ce qui n'est pas rien non plus.
  'hors du bond': { label: 'Hors du bond', colour: QUALITY_COLOR.moyen },
  'de retour dans le bond': { label: 'De retour dans le bond', colour: QUALITY_COLOR.excellent },
  // Les mots du routeur, quand il change d'avis sans que la connexion bouge.
  // Gris : ce n'est ni une panne ni un rétablissement, c'est un état annoncé —
  // « Detecting MTU... » ne mérite pas un rail rouge.
  signalement: { label: 'Signalement', colour: QUALITY_COLOR.inconnu },
  // Un lien revenu sur un AUTRE réseau — bascule de SIM, itinérance (R-2.3).
  // Gris : un fait qui change tout pour le choix d'un emplacement, pas une panne.
  "changement d'opérateur": { label: 'Changement d’opérateur', colour: QUALITY_COLOR.inconnu },
  // Les mots du journal du ROUTEUR : des manœuvres, pas des pannes — gris. Seule
  // la connexion admin refusée alerte : cinq suspendent l'accès cinq minutes.
  'bascule de SIM': { label: 'Bascule de SIM', colour: QUALITY_COLOR.inconnu },
  // « Priority changed » : le routeur l'écrit quand l'EXPLOITANT change une
  // priorité, mais aussi de lui-même quand un WAN tombe ou revient (vu le
  // 25 septembre, à 0 s et à 36 s d'un reset de module). « Priorités
  // modifiées » se lisait comme un geste ; ce libellé est vrai dans les deux cas.
  // La clé reste celle de la base.
  'priorités modifiées': { label: 'Ordre des WAN réévalué', colour: QUALITY_COLOR.inconnu },
  'configuration modifiée': { label: 'Configuration modifiée', colour: QUALITY_COLOR.inconnu },
  'routeur démarré': { label: 'Routeur démarré', colour: QUALITY_COLOR.inconnu },
  'connexion admin refusée': { label: 'Connexion admin refusée', colour: QUALITY_COLOR.moyen },
}

/**
 * Le journal : ce qui s'est passé, daté.
 *
 * Coupures, rétablissements que seul un `uptime` qui recule trahit, épisodes de
 * latence — tout était détecté et COMPTÉ depuis le début, et rien n'était jamais
 * listé. « Trois coupures » ne dit ni quand, ni sur quel lien, ni pourquoi ;
 * c'est un compteur, pas une chronologie, et on ne relit pas une intervention
 * avec des compteurs.
 *
 * Deux registres, parce que les faits de ce déploiement n'ont pas le même ordre
 * de grandeur. Mesuré sur 32 641 relevés : cinq coupures, mais neuf cent
 * quatre-vingt-cinq changements de station. Ce qui se liste se liste ; ce qui est
 * trop fréquent se compte, par lien — et le compte est parlant, puisque sur
 * vingt-quatre heures un modem a changé de station cent soixante-trois fois
 * quand son voisin, dans le même véhicule et au même endroit, l'a fait quatre
 * fois.
 */
/** Épisodes montrés d'emblée.
 *
 *  Le journal en portait cinquante-sept d'un coup et occupait à lui seul plus de
 *  hauteur que le reste du tableau de bord réuni : on scrollait pour atteindre
 *  la table des liens, c'est-à-dire l'écran. Les plus récents suffisent à savoir
 *  s'il s'est passé quelque chose ; le reste est à un clic, et jamais caché en
 *  silence. */
const PREMIERS = 6

/** Quel épisode CLÔT quel autre.
 *
 *  Une coupure est une transition : le backend la date et laisse `until` nul
 *  jusqu'à ce que le lien revienne, parce qu'il ignore quand cela arrivera. La
 *  colonne « Durée » affichait donc un tiret — qui, dans cette table, veut dire
 *  « un seul relevé, pas de durée mesurée ». Un câble WAN débranché à 23 h 21 se
 *  lisait ainsi comme un incident instantané, alors qu'il durait toujours.
 *
 *  Ce qui manquait n'était pas une donnée : le retour, quand il a lieu, est dans
 *  le journal. Son ABSENCE derrière une coupure est l'information. */
const FERMETURE: Record<string, string> = {
  coupure: 'retour',
  'position perdue': 'position retrouvée',
  'hors du bond': 'de retour dans le bond',
}

/** Quand une transition s'est terminée, et si elle dure encore.
 *
 *  Cherché dans le journal lui-même plutôt qu'auprès de l'état courant : deux
 *  sources pour un même fait finissent toujours par se contredire à l'écran. La
 *  troncature ne fausse rien, puisqu'elle garde les épisodes les PLUS RÉCENTS —
 *  un retour survenu après la coupure y est donc forcément.
 *
 *  Le retour PORTE la fin : le backend date la coupure et laisse `until` nul
 *  pour toujours, parce qu'au moment où il l'écrit il ignore quand le lien
 *  reviendra — et il ne repasse pas sur ses lignes. Rebranchement mesuré le 11
 *  septembre à 23 h 33 : l'épisode de retour est arrivé, et la coupure de
 *  23 h 21 serait retombée sur le tiret des épisodes instantanés.
 *
 *  Le plus PROCHE des retours postérieurs, et non le dernier : un lien qui
 *  tombe et revient trois fois produit trois paires, et prendre le dernier
 *  retour donnerait à la première coupure la durée des trois. */
function fin(
  episode: Episode,
  tous: Episode[],
  finFenetre: string,
): { at: string; enCours: boolean } | null {
  const cloture = FERMETURE[episode.kind]
  if (cloture === undefined) return null
  let proche: string | null = null
  for (const e of tous) {
    if (e.kind !== cloture || e.wan_id !== episode.wan_id || e.at <= episode.at) continue
    if (proche === null || e.at < proche) proche = e.at
  }
  return proche === null ? { at: finFenetre, enCours: true } : { at: proche, enCours: false }
}


export function EventLog({ log, windowLabel, complet = false }: Props) {
  const [ouvert, setTout] = useState(false)
  const tout = complet || ouvert
  if (log === null) {
    return (
      <section className="panel">
        <div className="panel-head"><h2>Journal</h2></div>
        <div className="panel-body">
          <div className="notice">Relecture de la fenêtre en cours…</div>
        </div>
      </section>
    )
  }

  // `?? []` : un journal d'une version antérieure du serveur peut ne pas
  // porter ces listes, et chacune emportait l'écran entier.
  const episodes = log.episodes ?? []
  const churn = log.churn ?? []
  const vide = episodes.length === 0 && churn.length === 0
  // Le message du routeur n'existe que sur les transitions — coupure, retour.
  // La colonne était donc entièrement composée de tirets dès qu'aucune n'était
  // survenue, et une colonne de tirets se lit comme une mesure perdue.
  // La colonne porte DEUX sortes de cause, et elle s'ouvre pour l'une comme
  // pour l'autre : ce que le routeur a déclaré — un fait —, et le changement de
  // cellule qui précède un pic — une corrélation mesurée. Elle ne testait que la
  // première, si bien qu'une session sans message de statut cachait aussi les
  // explications de pics.
  // Seuls les liens qui ONT des cellules figurent au décompte du brassage. Un
  // lien filaire y affichait « 0 / 0 », au milieu de modems dont le zéro serait
  // une vraie stabilité — le zéro pour une absence, une fois de plus.
  const brassage = churn.filter((c) => c.has_cells)
  const avecCause = episodes.some((e) => e.detail || e.after_handover_s !== null)

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Journal</h2>
        <span className="note">
          {windowLabel.toLowerCase()}
          {log.capped && ' · dernières 24 h seulement'}
        </span>
        <span className="spacer" />
        {episodes.length > 0 && (
          <span className="tag">
            {episodes.length === 1 ? 'un épisode' : `${episodes.length} épisodes`}
          </span>
        )}
        {/* Rien n'est masqué en silence : le bouton dit combien restent. */}
        {episodes.length > PREMIERS && !complet && (
          <button className="is-quiet" onClick={() => setTout(!tout)}>
            {tout
              ? `Ne montrer que les ${PREMIERS} derniers`
              : `Voir les ${episodes.length - PREMIERS} autres`}
          </button>
        )}
      </div>

      {vide ? (
        <div className="panel-body">
          <div className="notice">
            Aucune coupure, aucun lien écarté par la latence et aucune perte de position
            sur cette fenêtre.
          </div>
        </div>
      ) : (
        <>
          {episodes.length > 0 && (
            <div className="ltable-scroll">
              <table className="ltable">
                <thead>
                  <tr>
                    <th className="l">Quand</th>
                    <th className="l">Lien</th>
                    <th className="l">Épisode</th>
                    <th>Durée</th>
                    <th>Pic</th>
                    {avecCause && <th scope="col" className="l">Cause</th>}
                  </tr>
                </thead>
                <tbody>
                  {(tout ? episodes : episodes.slice(0, PREMIERS)).map((e) => (
                    <Ligne
                      key={`${e.kind}-${e.wan_id}-${e.at}`}
                      episode={e}
                      avecCause={avecCause}
                      // Mesurée entre deux horodatages du SERVEUR — le début
                      // de l'épisode et sa clôture, ou la fin de la fenêtre
                      // quand il dure encore —, jamais contre l'horloge du
                      // navigateur.
                      fin={fin(e, episodes, log.ended_at)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Jamais silencieux : un journal tronqué sans le dire se lit comme un
              journal complet. */}
          <Notes>
{log.omitted > 0 && (
            <p className="panel-note">
              {fmtInt(log.omitted)} épisode{log.omitted > 1 ? 's' : ''} de plus
              n’{log.omitted > 1 ? 'ont' : 'a'} pas été listé{log.omitted > 1 ? 's' : ''} : seuls les{' '}
              {fmtInt(episodes.length)} plus récents le sont.
            </p>
          )}

          {brassage.length > 0 && (
            <Note pourquoi={
              "Trop fréquent pour faire des lignes, donc compté. Un modem qui saute bien "
              + "plus que ses voisins, au même endroit et au même instant, ne voit pas le "
              + "même réseau qu'eux : c'est son antenne, pas la couverture."
            }>
              <b>Brassage</b> —{' '}
              {brassage.map((c, i) => (
                <span key={c.wan_id}>
                  {i > 0 ? ' · ' : ''}
                  {etiquetteSupport(c.link_kind)} {c.wan_id}{' '}
                  <span className="num">{fmtInt(c.station_changes)}</span>
                  {' / '}
                  <span className="num">{fmtInt(c.cell_changes)}</span>
                </span>
              ))}
              {' '}(stations / cellules)
            </Note>
          )}

          <Note pourquoi={
            "« Écarté par la latence » est une ACTION du routeur, constatée. « Latence "
            + "excessive » est le garde-fou de cet outil, à " + fmtInt(log.latency_threshold_ms)
            + " ms, qui se déclenche sans que le routeur ait rien fait — un bond dont TOUS "
            + "les liens sont mauvais se ressemble parfaitement, et l'écart n'y voit rien. "
            + "Les confondre ferait affirmer une exclusion qui n'a pas eu lieu. Les mots du "
            + "routeur, eux, sont repris tels quels : c'est ce qui distingue un câble "
            + "rebranché depuis quinze secondes d'une coupure qui dure."
          }>
            Épisodes continus, pas un par relevé. Deux règles de latence, distinguées.
          </Note>
</Notes>
        </>
      )}
    </section>
  )
}

function Ligne({
  episode, avecCause, fin: bout,
}: {
  episode: Episode
  avecCause: boolean
  /** Clôture de la transition, quand le genre en admet une. */
  fin: { at: string; enCours: boolean } | null
}) {
  const genre = GENRES[episode.kind] ?? { label: episode.kind, colour: QUALITY_COLOR.inconnu }
  const debut = new Date(episode.at)
  const duree = episode.until
    ? Math.round((new Date(episode.until).getTime() - debut.getTime()) / 1000)
    : null

  return (
    <tr>
      <td className="l num">
        {debut.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
      </td>
      <td className="l">
        <span className="who">
          <span className="rail" style={{ ['--q' as string]: genre.colour }} />
          {/* Un épisode sans lien décrit l'ÉQUIPEMENT : la position n'appartient
              à aucun modem. */}
          <b>
            {episode.wan_id === '' || episode.wan_id === null
              ? 'Équipement'
              : `${etiquetteSupport(episode.link_kind)} ${episode.wan_id}`}
          </b>
        </span>
      </td>
      <td className="l">{genre.label}</td>
      <td className="num">
        {/* Trois cas, et non deux. Un épisode CLOS porte sa durée. Un épisode
            qui dure encore porte le temps écoulé et le dit — sans quoi une
            coupure en cours se lit comme un incident passé. Un épisode d'un seul
            relevé n'a pas de durée MESURÉE : il a duré au plus un cycle, et
            écrire « 0 s » affirmerait qu'il a été instantané. */}
        {bout !== null ? (
          <>
            {fmtUptime(Math.round((new Date(bout.at).getTime() - debut.getTime()) / 1000))}
            {bout.enCours && <> <span className="tag">en cours</span></>}
          </>
        ) : duree === null ? (
          <Absent />
        ) : (
          `${duree} s`
        )}
      </td>
      <td className="num">
        {episode.peak_ms === null ? (
          <Absent />
        ) : (
          /* Le pic voyage avec le NOMBRE DE RELEVÉS sur lequel il a été
             constaté — la même règle que le taux de perte et son dénominateur.
             Un pic à 1 900 ms vu une fois et le même vu sur quarante relevés
             sont deux faits différents, et `samples` était compté, publié, et
             lu par personne. */
          <span title={`constaté sur ${episode.samples} relevé${episode.samples > 1 ? 's' : ''}`}>
            {fmtNumber(episode.peak_ms, 0)} ms
            {episode.samples > 1 && (
              <span className="sub-note"> · {episode.samples} relevés</span>
            )}
          </span>
        )}
      </td>
      {avecCause && (
        <td className="l">
          {/* Ce que le ROUTEUR a dit, et — à défaut — ce qui explique le pic.
              Le changement de cellule est une EXPLICATION, jamais une
              prédiction : le pic dure une quinzaine de secondes, donc
              l'information arrive en même temps que l'effet. Elle ne couvre
              qu'un pic sur six ; les autres restent sans explication plutôt
              que d'en recevoir une fausse. */}
          {episode.detail ? (
            <span className="sub-note">{episode.detail}</span>
          ) : episode.after_handover_s !== null ? (
            <span className="sub-note">
              {episode.after_handover_s < 1
                ? 'au moment d’un changement de cellule'
                : `${Math.round(episode.after_handover_s)} s après un changement de cellule`}
            </span>
          ) : (
            <Absent />
          )}
        </td>
      )}
    </tr>
  )
}
