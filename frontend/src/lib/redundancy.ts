import type { Timeline } from '../types'

/**
 * La redondance du bond dépense-t-elle quelque chose, et que dit son silence ?
 *
 * SpeedFusion peut protéger le flux de deux façons — correction d'erreur en
 * amont, et duplication des paquets sur plusieurs liens. Les deux coûtent de la
 * capacité MONTANTE : entre 13 % et 27 % pour la correction d'erreur selon son
 * réglage, jusqu'au double ou au triple du trafic pour la duplication. Le
 * routeur compte ce qu'il émet en plus et ce qu'il a pu reconstituer à
 * l'arrivée ; l'outil enregistrait ces deux compteurs depuis le début et n'en
 * faisait rien.
 *
 * Il n'y a pourtant PAS de courbe à en tirer. Tant que le profil est éteint, la
 * réparation vaut zéro sur des dizaines de milliers de relevés, et tracer une
 * grandeur qui ne varie jamais la ferait passer pour une mesure : c'est le
 * défaut que ce projet a retiré cinq fois.
 *
 * Ce que son silence établit, en revanche, vaut d'être dit. Si la correction
 * d'erreur était active, elle émettrait en permanence ; si la duplication
 * l'était, une perte aurait été reconstituée. Leur absence conjointe, SOUS
 * RÉSERVE que les compteurs aient été renseignés, ne laisse qu'une lecture : la
 * redondance du profil est éteinte, et chaque paquet perdu sur un lien est perdu
 * pour le flux.
 *
 * Les deux états ont été observés sur ce matériel : éteint jusqu'au 13 septembre
 * — zéro paquet réparé sur 26 629 montants — puis allumé le jour où le trafic
 * SpeedFusion a été activé, 923 paquets de réparation pour 26 037 transportés,
 * soit 3,4 % de la capacité montante.
 *
 * C'est un fait de configuration, pas une alarme, et il n'est énoncé que lorsque
 * assez de trafic l'établit. En dessous, on ne sait pas — et on le dit aussi.
 */
export type Redundancy =
  | { state: 'off'; packets: number }
  | { state: 'on'; share: number; repair: number; packets: number }
  | { state: 'unknown'; packets: number }

/** Paquets montants en dessous desquels le silence des compteurs ne prouve rien.
 *
 *  Un tunnel au repos n'échange qu'un keepalive de quelques paquets : n'en voir
 *  aucun réparé ne dit alors rien du profil. Mille paquets suffisent largement —
 *  la correction d'erreur, quand elle est active, en ajoute au moins 13 %. */
export const PAQUETS_CONCLUANTS = 1000

export function bondRedundancy(timeline: Timeline | null): Redundancy {
  if (timeline === null) return { state: 'unknown', packets: 0 }

  let repair = 0
  let packets = 0
  // Le compteur de réparation a-t-il été RENSEIGNÉ, ne serait-ce qu'une fois ?
  // Le serveur sommait avec un repli à zéro, si bien qu'une colonne que le
  // firmware n'a jamais remplie rendait « zéro paquet réparé » — et c'est de
  // cette somme que sortait la conclusion « la redondance du profil est
  // éteinte ». Mesuré : 720 relevés du 11 septembre portent un `packets_up`
  // mesuré et un `repair_sent` absent.
  let mesure = false
  for (const lane of timeline.lanes) {
    for (const slice of lane.slices) {
      if (slice.repair !== null) { repair += slice.repair; mesure = true }
      if (slice.packets !== null) packets += slice.packets
    }
  }

  if (repair > 0) {
    // La part de capacité montante dépensée à réparer plutôt qu'à transporter —
    // publiée sur un trafic concluant seulement : 3 paquets réparés pour 5
    // transportés titraient « 37,5 % de la capacité part en redondance » sur
    // un keepalive (C-39). En deçà, on ne sait pas.
    if (repair + packets < PAQUETS_CONCLUANTS) return { state: 'unknown', packets }
    return { state: 'on', share: repair / (repair + packets), repair, packets }
  }
  // Un silence ne vaut que si quelqu'un a parlé : sans un seul relevé de
  // réparation, l'absence de réparation n'est pas un fait de configuration.
  if (mesure && packets >= PAQUETS_CONCLUANTS) return { state: 'off', packets }
  return { state: 'unknown', packets }
}

/** Fenêtre, en secondes, sur laquelle le débit de paquets du tunnel est
 *  mesuré pour dire si le bond PORTE.
 *
 *  Le témoin était une moyenne sur toute la fenêtre relue — Σ paquets / durée
 *  totale : six heures au repos puis du trafic, et il fallait vingt-huit
 *  minutes pour franchir dix paquets par seconde, pendant lesquelles le bandeau
 *  niait que le bond porte ; six heures en charge puis l'arrêt, et « porte »
 *  persistait des heures. Dix minutes : assez pour lisser une rafale, assez
 *  court pour que le témoin dise l'instant. */
export const FENETRE_TEMOIN_S = 600

/** Paquets par seconde dans le tunnel sur les dernières minutes de la fenêtre,
 *  tous liens confondus.
 *
 *  Le dénominateur est le temps OBSERVÉ — les tranches où le compteur a été
 *  renseigné — et non la durée de la fenêtre : une tranche absente est un
 *  superviseur qui ne regardait pas, pas un tunnel silencieux. Sans une seule
 *  tranche renseignée, `null` : on n'a rien vu, ce qui n'est pas avoir vu zéro.
 *  Le zéro qu'on rendait ici faisait passer le bond à `routes` — « rien ne
 *  transite » — dès que le tunnel tombait, puisque son compteur s'éteint avec
 *  lui ; et le bandeau repassait au vert, tunnel à terre (C-2).
 *
 *  La dernière tranche compte pour sa durée ÉCOULÉE, pas pour une tranche
 *  entière : sur une fenêtre courte, où la largeur est au plancher de la
 *  cadence, elle s'arrête à la borne de fin, et ses paquets dilués sur un pas
 *  complet abaissaient le débit vers le seuil (C-39). */
export function paquetsParSecondeRecents(timeline: Timeline | null, fenetreS = FENETRE_TEMOIN_S): number | null {
  if (timeline === null || timeline.slice_seconds <= 0 || timeline.slice_count <= 0) return null
  const tranches = Math.max(1, Math.ceil(fenetreS / timeline.slice_seconds))
  const depuis = timeline.slice_count - tranches
  let packets = 0
  const observees = new Set<number>()
  for (const lane of timeline.lanes) {
    for (const slice of lane.slices) {
      if (slice.t < depuis || slice.packets === null) continue
      packets += slice.packets
      observees.add(slice.t)
    }
  }
  if (observees.size === 0) return null
  const pas = timeline.slice_seconds
  const derniere = timeline.slice_count - 1
  const etendue = (new Date(timeline.ended_at).getTime() - new Date(timeline.started_at).getTime()) / 1000
  const reste = etendue - derniere * pas
  // Une borne incohérente — fenêtre plus courte que les tranches qu'elle
  // porte — ne raccourcit rien : la tranche compte alors entière.
  const dureeDerniere = reste > 0 && reste < pas ? reste : pas
  let duree = 0
  for (const t of observees) duree += t === derniere ? dureeDerniere : pas
  return packets / duree
}
