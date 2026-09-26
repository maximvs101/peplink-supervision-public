import type { RadioSample } from '../types'

/**
 * Les quatre chiffres qu'on lit en arrivant, et la densité d'affichage à adopter.
 *
 * Tout est dérivé des relevés déjà présents à l'écran : aucun appel, aucun champ
 * nouveau. La règle qui gouverne l'ensemble est qu'une grandeur non mesurée vaut
 * `null` et s'affiche « — », jamais zéro : sur cet outil, un zéro affiché à la
 * place d'une absence de mesure a déjà menti cinq fois.
 */

export interface KeyFigures {
  /** Somme des débits d'interface des liens connectés, en kbit/s. */
  txKbps: number | null
  rxKbps: number | null
  /** Somme de ce qui passe RÉELLEMENT par le bond, quand un tunnel existe.
   *  Distinct du précédent : l'interface compte aussi le trafic hors tunnel. */
  bondTxKbps: number | null
  bondRxKbps: number | null
  /** Liens qui portent : dans le bond s'il y en a un, connectés sinon — MOINS
   *  ceux que le routeur a cessé d'employer faute de latence. L'écran annonçait
   *  « 5 liens portent » pendant que la table marquait l'un d'eux « au-delà du
   *  seuil », c'est-à-dire exactement l'inverse.
   *
   *  `null` quand un tunnel existe mais qu'aucun lien connecté n'en a d'état
   *  connu : un état inconnu n'est ni porteur ni écarté, et « 0 / 2 liens qui
   *  portent » s'affichait à côté de « Liaison OK » (C-18). */
  carrying: number | null
  /** Ceux-là, écartés par le seuil de latence du bond. `in_bond` n'est pas
   *  corrigé pour autant : il porte ce que l'équipement DÉCLARE. */
  excludedByLatency: number
  total: number
  /** Latence médiane des liens du bond. La médiane et non la moyenne : un lien
   *  à 1 929 ms au milieu de trois à 30 ms déplacerait la moyenne de 470 ms. */
  rttMedianMs: number | null
  /** La PIRE latence de l'instant, qui dit ce que la médiane cache.
   *  Ce n'est pas un p90 : c'est le maximum des liens du relevé courant, et
   *  l'écran l'affiche bien comme « max ». */
  rttWorstMs: number | null
  /** Ce que la médiane résume : le TUNNEL dès qu'un lien du bond en mesure
   *  une, sinon la latence propre des liens connectés (R-1.3). Jamais un
   *  mélange : les deux sondes ne visent pas la même destination. */
  rttSource: 'bond' | 'lien' | null
  /** L'équipement porte-t-il un tunnel ? Décide de l'affichage des colonnes. */
  hasTunnel: boolean
}

function somme(valeurs: (number | null)[]): number | null {
  const mesures = valeurs.filter((v): v is number => v !== null)
  return mesures.length ? mesures.reduce((a, b) => a + b, 0) : null
}

function mediane(valeurs: number[]): number | null {
  if (!valeurs.length) return null
  const t = [...valeurs].sort((a, b) => a - b)
  const m = Math.floor(t.length / 2)
  return t.length % 2 ? t[m] : (t[m - 1] + t[m]) / 2
}

export function keyFigures(samples: RadioSample[]): KeyFigures {
  const up = samples.filter((s) => s.wan.connected)
  // Un lien sans objet tunnel n'est pas « hors du bond » : l'équipement n'a
  // peut-être aucun profil SpeedFusion. C'est la présence d'au moins un objet
  // qui prouve qu'un tunnel est configuré.
  const hasTunnel = samples.some((s) => s.wan.tunnel !== null)
  const dansLeBond = up.filter((s) => s.wan.tunnel?.in_bond)
  // Le routeur arrête le trafic sur un lien qui dépasse de plus de son seuil la
  // latence du plus rapide, sans changer son état : le compter comme porteur
  // faisait dire à l'écran le contraire de ce que la table marquait à côté.
  const ecartes = dansLeBond.filter((s) => s.wan.tunnel?.beyond_cutoff)
  const porteurs = hasTunnel
    ? dansLeBond.filter((s) => !s.wan.tunnel?.beyond_cutoff)
    : up

  const duTunnel = dansLeBond
    .map((s) => s.wan.tunnel?.rtt_ms)
    .filter((v): v is number => v !== null && v !== undefined)
  const desLiens = up
    .map((s) => s.wan.path_latency_recent_ms)
    .filter((v): v is number => v !== null && v !== undefined)
  const rttSource = duTunnel.length ? 'bond' : desLiens.length ? 'lien' : null
  const rtts = rttSource === 'bond' ? duTunnel : desLiens

  return {
    txKbps: somme(up.map((s) => s.wan.tx_kbps)),
    rxKbps: somme(up.map((s) => s.wan.rx_kbps)),
    bondTxKbps: hasTunnel ? somme(dansLeBond.map((s) => s.wan.tunnel?.tx_kbps ?? null)) : null,
    bondRxKbps: hasTunnel ? somme(dansLeBond.map((s) => s.wan.tunnel?.rx_kbps ?? null)) : null,
    carrying: hasTunnel && !up.some((s) => s.wan.tunnel?.state != null) && up.length > 0
      ? null
      : porteurs.length,
    excludedByLatency: ecartes.length,
    total: samples.length,
    rttMedianMs: mediane(rtts),
    rttWorstMs: rtts.length ? Math.max(...rtts) : null,
    rttSource,
    hasTunnel,
  }
}

/* `orderForDashboard` vivait ici : elle plaçait en tête ce qui portait le flux,
   puis ce qui était connecté sans porter, puis ce qui était tombé. Retirée avec
   la refonte, et c'est un renoncement assumé. Un classement par état se
   REORDONNE tout seul : un lien qui décroche change de rang, et toute la colonne
   se décale sous l'œil de qui la consultait. La certitude spatiale est ce qui
   permet le coup d'œil — quand on ignore où un événement va apparaître, il faut
   balayer l'écran au lieu de le lire. Les liens sont donc rangés par identifiant,
   toujours au même endroit, et c'est le rail de qualité qui signale l'état SUR
   PLACE. */
