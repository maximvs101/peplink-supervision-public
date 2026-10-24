/** Verdict de liaison : la réponse à « est-ce que ça marche ? » en un coup d'œil. */

import { motDuPair } from './bondProfile'
import type { RadioSample, TunnelLink, WanRadioStatus } from '../types'
import { porteLeFlux, type BondRole } from './bondRole'
import { QUALITY_COLOR, overallQuality } from './quality'

export type Verdict = 'ok' | 'degrade' | 'rompu'

// Le verdict emprunte les teintes de la qualité : une liaison dégradée et un
// lien « moyen » sont la même nouvelle à deux échelles, et leur donner deux
// ambres différents était l'une des vingt-sept couleurs de trop.
export const VERDICT_COLOR: Record<Verdict, string> = {
  ok: QUALITY_COLOR.excellent,
  degrade: QUALITY_COLOR.moyen,
  rompu: QUALITY_COLOR.faible,
}

export interface LinkVerdict {
  verdict: Verdict
  headline: string
  detail: string | null
  /** Âge de la mesure la plus récente, en secondes, **mesuré par le serveur**. */
  ageSeconds: number | null
  /** La mesure est-elle trop vieille pour qu'on s'y fie ? */
  stale: boolean
  /** Griefs qui ne concernent QUE le tunnel, écartés du verdict quand celui-ci
   *  n'est pas le chemin du trafic.
   *
   *  Ils restent vrais et restent dits — ailleurs, à côté du bond. Ce qui était
   *  faux, c'est d'annoncer « Liaison dégradée » parce que la latence vers le
   *  pair s'est allongée, alors que le trafic ne passe pas par là : sur ce
   *  déploiement, aucun profil n'est sélectionné dans « Send All Traffic To » et
   *  le tunnel n'achemine que deux sous-réseaux dédiés. */
  bondGrievances: string[]
  /** Liens dont le NIVEAU RADIO est faible, écartés du verdict.
   *
   *  Ils y entraient : `weak.length > 0` suffisait à annoncer « Liaison
   *  dégradée ». La mesure ne le soutient pas. Sur 4 × 8 800 relevés de la base,
   *  la corrélation de rang entre SINR/RSRP/RSRQ/RSSI et la latence ou la perte
   *  du tunnel ne dépasse **|ρ| = 0,19** sur aucun des quatre liens ; et sur
   *  1 181 pics isolés — plus de 150 ms après cinq cycles calmes — le niveau
   *  radio du cycle PRÉCÉDENT est à **0,00 dB** de la médiane de la même
   *  cellule, c'est-à-dire indiscernable des 22 700 cycles calmes témoins.
   *
   *  Un signal faible reste un FAIT, et il reste dit — la table le colore, la
   *  carte le chiffre, et le bandeau le porte ici. Ce qui était faux, c'est
   *  d'en conclure que la liaison est dégradée : elle se juge sur ce que le
   *  tunnel porte, pas sur ce que l'antenne reçoit.
   *
   *  Portée de cette abstention : la plage OBSERVÉE, −75 à −105 dBm de RSRP
   *  (médiane −88). Un lien réellement en limite de couverture, sous −110,
   *  n'est pas représenté dans ces mesures et se comporterait sûrement
   *  autrement. C'est pourquoi le fait est affiché plutôt qu'effacé. */
  radioGrievances: string[]
}

/** Constats consécutifs exigés pour DESCENDRE d'un cran de gravité.
 *
 *  Elle sert deux fois : ici, pour le seuil de péremption — cinq cycles sans
 *  relevé —, et comme sortie d'hystérésis du bandeau. Les deux valeurs étaient
 *  deux littéraux reliés par un commentaire, qui énonçait un invariant que rien
 *  ne faisait respecter. */
export const SORTIE_HYSTERESIS = 5

/** Au-delà, un relevé ne dit plus rien de l'instant présent.
 *
 *  Ce n'est qu'un REPLI, pour l'appelant qui ignore la période de scrutation.
 *  Celle-ci vit dans la configuration du serveur — `backend/.env` porte
 *  `POLL_INTERVAL_SECONDS=3`, la stabilité ayant été confirmée
 *  une fois la stabilité confirmée, et le défaut de `config.py` est déjà 3 s.
 *  Un seuil figé dans le navigateur contredit forcément l'une des deux valeurs. */
export const STALE_AFTER_SECONDS = 45

/** Nombre de scrutations manquées au-delà duquel on cesse de croire le relevé.
 *  Cinq, comme le seuil de sortie de `smoothVerdict` : en dessous, une seule
 *  requête HTTP lente suffirait à peindre le bandeau en rouge. */
export const STALE_POLL_CYCLES = SORTIE_HYSTERESIS

/** Plancher du seuil déduit : à 1 s de scrutation, cinq cycles donneraient 5 s,
 *  que le moindre aller-retour vers un équipement distant dépasse. */
const STALE_FLOOR_SECONDS = 15

/** Au-delà, un relevé est périmé quelle que soit la cadence annoncée.
 *
 *  Sans plafond, le garde-fou se relâchait exactement quand il devait se
 *  resserrer : `period_seconds` est une moyenne mobile du délai RÉELLEMENT
 *  observé, donc elle monte tant qu'un équipement met du temps à répondre.
 *  Mesuré : `staleAfterSeconds(1e9)` rendait cinq milliards de secondes, et un
 *  relevé vieux de onze jours passait pour frais. Deux minutes : une supervision
 *  dont le dernier relevé a deux minutes n'en est plus une. */
const STALE_CEILING_SECONDS = 120

/**
 * Seuil de péremption, déduit de la période de scrutation quand on la connaît.
 *
 * `withGaps` procède déjà ainsi pour les graphes : il DÉDUIT son seuil de
 * l'intervalle médian observé au lieu de le figer, précisément parce que la
 * période de scrutation est configurable et varie d'un équipement à l'autre. Le
 * verdict n'avait aucune raison de faire autrement.
 */
export function staleAfterSeconds(periodSeconds?: number | null): number {
  if (
    periodSeconds === null ||
    periodSeconds === undefined ||
    !Number.isFinite(periodSeconds) ||
    periodSeconds <= 0
  ) {
    return STALE_AFTER_SECONDS
  }
  return Math.min(
    STALE_CEILING_SECONDS,
    Math.max(periodSeconds * STALE_POLL_CYCLES, STALE_FLOOR_SECONDS),
  )
}

export interface VerdictInput {
  samples: RadioSample[]
  /** État de la liaison avec l'équipement : « connecté », « injoignable »… */
  status?: string
  /** Âge du relevé le plus récent, en secondes, tel que le serveur le mesure. */
  ageSeconds?: number | null
  /** Période RÉELLEMENT observée entre deux cycles du serveur, quand l'appelant
   *  la connaît. Surtout pas la consigne de scrutation : le serveur scrute PUIS
   *  dort, donc la période vaut `cycle + consigne`. Mesuré, la confusion rendait
   *  le seuil plus court que l'âge légitime le plus élevé — bandeau rouge sur
   *  liaison saine, dès un seul équipement. */
  periodSeconds?: number | null
  /** Le tunnel est-il le chemin du trafic ?
   *
   *  Vrai par défaut — c'est le cas d'un bond ordinaire, et le repli sûr quand
   *  l'appelant ne sait pas. Faux quand le pair n'achemine que des routes
   *  dédiées : la route par défaut est alors absente de ce qu'il DÉCLARE, et le
   *  trafic général sort par les interfaces.
   *
   *  Ce que cela change : les grandeurs du tunnel — latence, perte, exclusion du
   *  bond — cessent d'alimenter le verdict de la LIAISON. Elles restent vraies,
   *  mais elles décrivent autre chose, et le bandeau annonçait « Liaison
   *  dégradée — 1 en latence excessive (353 ms contre 19 ms) » pour un trafic
   *  qui ne passait pas par là. Elles ressortent par `bondGrievances`, à dire
   *  auprès du bond. */
  role?: BondRole
  /** Garde-fou absolu de latence, tel que le SERVEUR le publie
   *  (`RouterSummary.latency_threshold_ms`). Le recopier ici ferait vivre un
   *  même seuil en deux exemplaires de part et d'autre de la couture, ce dont ce
   *  projet a déjà fait les frais. Le repli n'est là que pour l'appelant qui n'a
   *  pas encore reçu la fiche. */
  latencyThresholdMs?: number | null
  /** Le bond a-t-il déjà porté un lien dans cette session, tel que le SERVEUR
   *  le publie (`RouterSummary.tunnel_attendu`) ? C'est la règle des alertes :
   *  un tunnel qui n'a jamais tenu n'est pas tombé, c'est un état. L'écran ne
   *  la recevait pas et titrait « Tunnel SpeedFusion tombé — 2 h dans cet
   *  état » en ambre sur un HD2 où SpeedFusion n'est pas censé fonctionner.
   *  `false` retire les grandeurs du tunnel du verdict de la liaison, comme un
   *  rôle `routes` ; `null` ne dit rien et laisse la règle par défaut. */
  tunnelAttendu?: boolean | null
  /** Ce qui tient le tunnel à terre, lu sur les profils SpeedFusion et la
   *  licence (`causeDuTunnel`, R-1.5). Il l'emporte sur l'état des liens :
   *  `PEER_DOWN` est une sentinelle de ce projet, pas un mot du routeur. */
  tunnelCause?: string | null
  /** Les WAN jamais connectés depuis la mise sous tension, tels que le SERVEUR
   *  les nomme (`RouterSummary.never_connected`). Un WAN ETHERNET parmi eux ne
   *  dégrade pas le verdict : un câble jamais branché est un état. Mesuré sur
   *  un Dome Pro Duo, WAN 1 « No Cable Detected » depuis l'allumage : le
   *  bandeau disait « Liaison dégradée » en permanence, et un bandeau toujours
   *  ambre est un bandeau qu'on cesse de lire. Un lien qui a été connecté une
   *  fois et TOMBE, lui, redevient une nouvelle. */
  neverConnected?: string[]
}

/** Ce que chaque état de la liaison avec l'équipement veut dire à l'exploitant.
 *
 *  Les cinq valeurs du contrat sont traitées, parce que le libellé porte le
 *  remède : « injoignable » envoie vérifier un raccordement, « erreur de
 *  configuration » envoie corriger une déclaration d'équipement — les confondre
 *  fait déplacer un technicien pour rien. Et « en attente », qui est l'état
 *  d'avant la première scrutation, n'est pas une panne : l'afficher en rouge au
 *  démarrage apprend à l'exploitant à ignorer le rouge.
 *
 *  Mesuré : un équipement en `erreur de configuration` s'annonçait
 *  « Équipement injoignable — plus aucune mesure ne remonte ». */
const STATUS_VERDICT: Record<
  string,
  Omit<LinkVerdict, 'ageSeconds' | 'stale' | 'bondGrievances' | 'radioGrievances'>
> = {
  'en attente': {
    verdict: 'degrade',
    headline: 'En attente du premier relevé',
    detail: 'la scrutation n’a pas encore abouti — rien n’indique une panne',
  },
  'identifiants refusés': {
    verdict: 'rompu',
    headline: 'Équipement inaccessible',
    detail: 'identifiants refusés — les mesures affichées datent d’avant',
  },
  injoignable: {
    verdict: 'rompu',
    headline: 'Équipement injoignable',
    detail: 'plus aucune mesure ne remonte — les valeurs affichées datent d’avant',
  },
  'erreur de configuration': {
    verdict: 'rompu',
    headline: 'Configuration à corriger',
    detail:
      'l’équipement est mal déclaré — corriger la configuration, pas le raccordement',
  },
  // État de l'ÉCRAN, pas du serveur : `#/e/inconnu` répond 404 dès la
  // première lecture. Le bandeau disait « En attente du premier relevé » et
  // l'écran « retiré depuis un autre onglet » — une attente et une cause
  // inventées pour un identifiant qui ne désigne rien.
  inconnu: {
    verdict: 'rompu',
    headline: 'Équipement inconnu',
    detail: 'aucun équipement supervisé sous cet identifiant — vérifier le lien',
  },
}

/* --- Le tunnel dans le verdict -------------------------------------------- *
 *
 * Jusqu'ici le verdict ne lisait que `connected`, `in_bond` et la qualité radio.
 * Ni la latence ni la perte n'y entraient — alors que `chart.ts` et `models.py`
 * affirment tous les deux, en toutes lettres, que ce sont CES grandeurs-là qui
 * font geler une image en bonding, et qu'aucune ne figure ailleurs dans l'API.
 *
 * Le relevé du 30 août à 13:51:42 tranche la question. Quatre SIM, toutes
 * ACTIVE dans le bond, bandeau vert :
 *
 *     WAN 4 ·    34 ms · SINR 12,0 · RSRP -92
 *     WAN 5 ·    25 ms · SINR 12,2 · RSRP -91
 *     WAN 6 · 1 929 ms · SINR 13,8 · RSRP -91     <-- empoisonne le bond
 *     WAN 7 ·    33 ms · SINR 11,9 · RSRP -92
 *
 * Le lien qui ruinait la remontée avait le MEILLEUR SINR des quatre. Sur ce
 * matériel, le niveau radio ne prédit pas l'état du tunnel : c'est mesuré, pas
 * supposé, et c'est la raison pour laquelle la qualité radio ne peut pas tenir
 * lieu de surveillance du tunnel.
 *
 * Le désordre de paquets reste dehors, faute de matière : sur 11 937 relevés,
 * `out_of_order_percent` est renseigné 6 984 fois pour UNE seule valeur
 * distincte, 0,0. Une grandeur qui n'a jamais varié ne peut rien décider.
 */

/* Seuils de latence — DES POINTS DE DÉPART, à revoir après une remontée
 * supervisée.
 *
 * La seule mesure dont on dispose est le relevé ci-dessus. Elle établit qu'un
 * lien peut empoisonner le bond sans que rien du niveau radio ne l'annonce. Elle
 * n'établit PAS à partir de quel écart l'image gèle : personne ne regardait le
 * relevé de 13:51:42. Ces trois nombres sont donc calibrés pour attraper ce
 * cas-là sans crier sur une liaison saine, et ils demandent à être repris quand
 * on aura, une fois, un journal de remontée en regard de ce que l'exploitant a
 * réellement vu à l'antenne.
 *
 * La règle porte sur l'ÉCART à la médiane des AUTRES liens du même relevé, et
 * non sur un seuil absolu : c'est ce que fait le concentrateur avec son
 * `latencyDiffCutoff` de 500 ms, et c'est la seule forme qui survive à un
 * changement de trajet — 30 ms en 4G urbaine et 600 ms par satellite sont deux
 * normalités, et un seuil fixe se tromperait sur l'une des deux.
 */

/** Au-delà, on signale sans comparer à personne.
 *
 *  Un écart à la médiane est aveugle à deux situations : le bond dont TOUS les
 *  liens sont mauvais — quatre liens à deux secondes se ressemblent
 *  parfaitement — et le bond réduit à un seul lien, qui n'a pas d'autre médiane
 *  que la sienne. Il faut donc un garde-fou absolu, et c'est le plus fragile des
 *  trois nombres : c'est celui à revoir en premier.
 *
 *  Une seconde d'aller-retour, et non les 500 ms du `latencyDiffCutoff` : ce
 *  cutoff-là est DIFFÉRENTIEL, et le transposer en seuil absolu dénoncerait tout
 *  un bond dont chaque lien siège légitimement à 550 ms — ce qui est exactement
 *  le bandeau rouge sur liaison saine qu'on a déjà corrigé une fois. À une
 *  seconde d'aller-retour, en revanche, plus aucun tampon de bonding ne tient
 *  un usage temps réel, quel que soit le trajet.
 *
 *  Ce n'est qu'un REPLI. Le serveur publie la même valeur dans `RouterSummary`
 *  et dans `EventLog` : elle vivait en double de part et d'autre de la couture,
 *  pour une seule et même règle éditoriale, et deux déclarations d'un même seuil
 *  finissent toujours par diverger. L'appelant qui a la fiche passe la valeur
 *  publiée ; celle-ci ne sert qu'à ceux qui ne l'ont pas encore reçue. */
export const LATENCY_ABSOLUTE_MS = 1000

/** Le seuil publié, ou le repli quand ce n'en est pas un.
 *
 *  Le coalescing nul laisse passer ZÉRO — et le modèle du journal déclare
 *  `latency_threshold_ms: float = 0` comme défaut. Un seuil de zéro rendrait
 *  tout lien coupable : mesuré, un lien à 30 ms devenait « en latence
 *  excessive ». */
export const seuilUtile = (publie: number | null | undefined): number =>
  publie !== null && publie !== undefined && publie > 0 ? publie : LATENCY_ABSOLUTE_MS

/** Perte retenue dans le verdict, en pourcentage.
 *
 *  Absolu ici, alors que la latence se juge en écart, et ce n'est pas une
 *  inconséquence : la perte a un zéro naturel et c'est sa valeur normale, tandis
 *  que la latence a un plancher irréductible qui dépend du trajet. Une médiane
 *  de perte à 3 % ne rendrait pas un lien à 3 % acceptable.
 *
 *  Point de départ lui aussi : 2 % de perte soutenue sur le sens montant se voit
 *  sur une image, mais le chiffre vient de l'usage du métier, pas d'une mesure
 *  faite ici. */
export const LOSS_PERCENT_THRESHOLD = 2

/** En deçà du grief, mais assez pour teinter un chiffre.
 *
 *  La table peignait en ambre à partir d'un demi pour cent, avec un littéral nu
 *  et aucun nom. Le seuil est éditorial comme son voisin : il se déclare ici,
 *  auprès de lui, et se publie à l'écran avec les autres. */
export const LOSS_ATTENTION_PERCENT = 0.5

/** Dénominateur minimal — la règle des 200 paquets.
 *
 *  Un taux calculé sur trois paquets n'est pas une mesure. Mesuré : sur
 *  l'ensemble des relevés, les valeurs de perte non nulles se comptent par
 *  dizaines et la plus fréquente reste 33,33 % — un paquet sur trois,
 *  c'est-à-dire un keepalive au dénominateur.
 *  Laisser entrer cette valeur-là dans le verdict peindrait le bandeau en ambre
 *  sur un tunnel au repos. */
export const LOSS_MIN_PACKETS = 200

// `median` vivait ici, avec une docstring qui défendait la comparaison à la
// médiane des autres liens. Cette règle a été remplacée par celle du routeur —
// `beyond_cutoff` — et la fonction n'avait plus aucun appelant en production.
// `dashboard.ts` en porte une copie ligne pour ligne, et c'est celle-là qui sert.

/** Un lien que sa latence fait écarter, avec de quoi le dire à l'écran. */
export interface LatencyOutlier {
  /** Rang dans le tableau reçu, pour retrouver le lien. */
  index: number
  rtt: number
  /** Le plus RAPIDE du bond au même instant : c'est à lui que le routeur
   *  compare, et c'est donc à lui qu'il faut opposer le chiffre. `null` quand il
   *  n'y a personne à qui se comparer — le lien n'a alors été retenu que sur le
   *  garde-fou absolu, et l'écran ne doit pas prétendre l'avoir comparé. */
  reference: number | null
}

/**
 * Liens que leur latence fait écarter du bond, au même instant.
 *
 * **La règle est celle du ROUTEUR, et elle est la seule.** Le firmware arrête le
 * trafic sur un lien qui dépasse de plus de son `latencyDiffCutoff` celui de
 * latence la plus basse ; il publie ce seuil, et le serveur en tire
 * `beyond_cutoff` pour chaque lien. C'est un FAIT, pas une appréciation.
 *
 * Ce module approximait ce fait avant de pouvoir le lire : il comparait chaque
 * lien à la médiane des autres, avec un plancher et un rapport calibrés à la
 * main. L'approximation a survécu à son motif. Mesuré sur 33 679 relevés du
 * matériel, les deux règles divergeaient sur 2 585 — 7,7 % —, et toujours dans
 * le même sens : l'approximation dénonçait des liens que le routeur employait
 * sans broncher. Constaté à l'écran pendant l'audit, la note du bond annonçant
 * « 2 en latence excessive » pendant que les cinq liens portaient
 * `beyond_cutoff: false` et que la table n'affichait aucun badge.
 *
 * Reste le garde-fou absolu, pour les deux situations que l'écart du routeur ne
 * voit pas : le bond dont TOUS les liens sont mauvais — ils se ressemblent
 * parfaitement —, et le lien seul, qui n'a personne à dépasser.
 */
export function latencyOutliers(
  links: { rtt: number; beyondCutoff: boolean }[],
  absoluteMs: number = LATENCY_ABSOLUTE_MS,
): LatencyOutlier[] {
  // Le plus rapide du peloton : la référence du routeur, et donc la nôtre.
  const bas = links.length > 1 ? Math.min(...links.map((l) => l.rtt)) : null
  return links.flatMap(({ rtt, beyondCutoff }, index) =>
    beyondCutoff || rtt >= absoluteMs
      ? [{ index, rtt, reference: bas !== null && bas !== rtt ? bas : null }]
      : [],
  )
}

/** Perte d'un lien dans le sens retenu, avec le dénominateur qui l'autorise. */
export interface DirectionLoss {
  percent: number
  /** Paquets réellement comptés sur l'intervalle dans ce sens. */
  packets: number
  /** Le sens désigne le remède : une perte montante est un problème d'émission
   *  du véhicule, une perte descendante un problème de couverture ou de charge
   *  de la cellule. */
  direction: 'montante' | 'descendante'
}

/**
 * Perte exploitable d'un lien : le pire des deux sens DONT le dénominateur est
 * significatif, `null` si aucun ne l'est.
 *
 * Le champ `loss_percent`, tous sens confondus, n'est volontairement pas lu : le
 * backend y met le pire des deux sens, et les deux sens n'ont pas le même
 * dénominateur. On ne saurait donc pas sur combien de paquets ce taux-là a été
 * établi, ce qui interdit de lui appliquer la règle des 200 paquets. Le détail
 * par sens, lui, vient avec son compte.
 *
 * À égalité, le sens montant l'emporte. Non parce qu'il compterait davantage —
 * l'équipement fournit de la connectivité, et c'est l'usage qui décide lequel des
 * deux importe — mais parce qu'il est structurellement la ressource rare : les
 * porteuses LTE et 5G sont asymétriques, le montant y dispose de bien moins de
 * capacité que le descendant. À perte égale, c'est donc lui qui sature le premier,
 * et le nommer oriente mieux le dépannage. Le départage reste arbitraire ; il est
 * seulement adossé à une propriété du lien, pas à une hypothèse sur l'usage.
 */
export function reportableLoss(tunnel: TunnelLink): DirectionLoss | null {
  const measured: DirectionLoss[] = []
  const consider = (
    percent: number | null,
    packets: number | null,
    direction: DirectionLoss['direction'],
  ) => {
    if (percent === null || packets === null || packets < LOSS_MIN_PACKETS) return
    measured.push({ percent, packets, direction })
  }
  consider(tunnel.loss_percent_up, tunnel.packets_up, 'montante')
  consider(tunnel.loss_percent_down, tunnel.packets_down, 'descendante')
  if (measured.length === 0) return null
  return measured.reduce((worst, l) => (l.percent > worst.percent ? l : worst))
}

/** Nombre en français : « 1 929 », « 4,8 ». Le bandeau est une phrase lue par un
 *  exploitant francophone, pas une colonne de tableau. */
const fmt = (value: number, digits = 0) =>
  value.toLocaleString('fr-FR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })

/** Accorde un mot sur le nombre auquel il se rapporte.
 *
 *  Le décompte accordait le nom sur le total et l'adjectif sur les connectés :
 *  « 1 liens sur 4 connecté ». C'est le cas d'une liaison déjà dégradée,
 *  c'est-à-dire précisément celui qu'on lit le plus souvent. */
export const accord = (n: number, mot: string, pluriel?: string) =>
  n > 1 ? (pluriel ?? `${mot}s`) : mot

/** Le grief de latence, en une incise.
 *
 *  La valeur figure dans le bandeau, et la médiane avec elle quand elle existe :
 *  « 1 929 ms » seul ne dit pas si c'est beaucoup ici, alors que « contre 33 ms »
 *  contient toute la démonstration. Le lien n'est pas nommé — le détail est à un
 *  clic, et c'est la règle que suivent déjà les autres griefs. */
function describeLatency(outliers: LatencyOutlier[]): string {
  const worst = outliers.reduce((a, b) => (b.rtt > a.rtt ? b : a))
  if (outliers.length > 1) return `jusqu’à ${fmt(worst.rtt)} ms`
  return worst.reference === null
    ? `${fmt(worst.rtt)} ms`
    : `${fmt(worst.rtt)} ms contre ${fmt(worst.reference)} ms`
}

/** Un lien ÉCARTÉ du bond : tunnel connu, état connu, et hors du bond.
 *
 *  `state === null` est un état INCONNU, ni dedans ni dehors — le tunnel n'a
 *  pas été sondé à ce cycle — et n'écarte personne. Cette définition vivait en
 *  trois copies, dans le plan, la table et le bandeau ; le module se souvient
 *  de ce qu'une copie divergente a coûté (« 1 hors du bond » ici, « — » là,
 *  pour le même lien). Une seule, pour que les trois écrans tombent d'accord
 *  sur un rail en pointillé, une ligne grisée et un grief. */
export const ecarteDuBond = (wan: WanRadioStatus): boolean =>
  wan.tunnel !== null && wan.tunnel.state !== null && !wan.tunnel.in_bond

/**
 * Ce que le ROUTEUR dit des liens tombés, en une incise.
 *
 * Le message d'état existait, était stocké, et n'apparaissait que dans le
 * journal — donc après coup, et après avoir déroulé la page. Or « 1 déconnecté »
 * ne dit pas où chercher, tandis que « WAN failed DNS test » ou
 * « No Cable Detected » désignent deux interventions entièrement différentes :
 * l'une se règle dans la configuration, l'autre au bout du câble. Relevé sur le
 * matériel le 13 septembre, WAN 1 tombé seize minutes sur un échec DNS.
 *
 * Nommée seulement quand TOUS les liens tombés portent le MÊME message. Deux
 * liens peuvent tomber pour deux raisons — un câble ici, un refus
 * d'enregistrement là — et coller au total la cause de l'un ferait chercher la
 * panne du mauvais côté. C'est la règle que `describeLoss` applique déjà au sens
 * d'une perte.
 *
 * Les mots sont ceux du routeur, repris tels quels : il nomme des conditions que
 * nous ne saurions pas énumérer, et les traduire reviendrait à les ranger dans
 * des catégories inventées ici.
 */
function describeCause(down: RadioSample[]): string | null {
  if (down.length === 0) return null
  const premier = down[0].wan.status_message
  if (!premier) return null
  return down.every((s) => s.wan.status_message === premier) ? premier : null
}

/** La cause pour laquelle des liens sont hors du bond — même règle : nommée
 *  seulement si TOUS la partagent. La première version prenait celle du premier
 *  lien : avec WAN_DOWN sur l'un et COLD sur l'autre, le bandeau attribuait
 *  WAN_DOWN aux deux et envoyait chercher une panne de câble sur un lien qui
 *  refroidissait — pendant que le serveur, lui, exigeait l'unanimité et
 *  émettait deux alertes distinctes. */
function describeTunnelCause(out: RadioSample[]): string | null {
  if (out.length === 0) return null
  const premier = out[0].wan.tunnel?.state ?? null
  if (!premier) return null
  if (!out.every((s) => s.wan.tunnel?.state === premier)) return null
  // Sous la sentinelle, le mot du routeur — s'il est le même partout.
  if (out[0].wan.tunnel?.cause_scope === 'pair') {
    const mot = out[0].wan.tunnel?.peer_state ?? null
    return out.every((s) => (s.wan.tunnel?.peer_state ?? null) === mot)
      ? `pair : ${motDuPair(mot)}`
      : null
  }
  return premier
}

/** Le grief de perte, en une incise.
 *
 *  Le sens n'est nommé que lorsqu'un seul lien est en cause : deux liens
 *  peuvent décrocher dans des sens opposés, et coller au total le nom du pire
 *  des deux ferait chercher la panne du mauvais côté. */
function describeLoss(losses: DirectionLoss[]): string {
  const worst = losses.reduce((a, b) => (b.percent > a.percent ? b : a))
  return losses.length > 1
    ? `perte de paquets (jusqu’à ${fmt(worst.percent, 1)} %)`
    : `perte ${worst.direction} (${fmt(worst.percent, 1)} %)`
}

/**
 * Établit le verdict à partir des derniers relevés.
 *
 * Trois états seulement, parce qu'un opérateur qui vient de brancher son
 * portable a besoin d'une décision, pas d'une nuance : la liaison tient, elle
 * est diminuée, ou elle est tombée. Le détail est à un clic.
 *
 * Les liens Wi-Fi amont comptent dans la liaison agrégée mais ne portent pas de
 * qualité radio cellulaire : ils entrent dans le décompte des liens connectés
 * sans peser sur l'appréciation de la qualité.
 *
 * Quatre familles de griefs peuvent dégrader le verdict : un lien tombé, un lien
 * écarté du bond, un lien qui empoisonne le bond par sa latence ou sa perte, un
 * signal faible. Aucune ne conclut seule à la rupture — un lien lent transporte
 * encore, mal, et annoncer une rupture qui n'a pas eu lieu se paie aussi cher
 * que de taire celle qui a eu lieu.
 */
export function linkVerdict({
  samples,
  status,
  ageSeconds = null,
  periodSeconds = null,
  role = { kind: 'inconnu' } as BondRole,
  latencyThresholdMs = null,
  tunnelAttendu = null,
  tunnelCause = null,
  neverConnected = [],
}: VerdictInput): LinkVerdict {
  const stale = ageSeconds !== null && ageSeconds > staleAfterSeconds(periodSeconds)

  // L'état de l'équipement passe AVANT l'absence de relevés : au démarrage, il
  // n'y a ni mesure ni panne, et « Aucune mesure » en rouge est un contresens.
  // Quand l'équipement ne répond plus, les relevés en mémoire sont ceux d'avant
  // la panne ; les présenter tels quels afficherait « Liaison OK » en vert sur
  // une liaison morte — le mode de panne le plus coûteux possible.
  if (status && status !== 'connecté') {
    // Une valeur hors contrat est traitée comme une injoignabilité : rester
    // muet sur un état qu'on ne sait pas nommer serait pire qu'alerter à tort.
    const base = STATUS_VERDICT[status] ?? STATUS_VERDICT.injoignable
    // Sans relevé en mémoire, il n'y a pas de « valeurs affichées » auxquelles
    // renvoyer : la moitié de phrase qui les annonce est retirée (vu le 30/09
    // sur un MBX éteint depuis le lancement).
    const detail = samples.length === 0 && base.detail
      ? base.detail.replace(/ — les (mesures|valeurs) affichées datent d’avant$/, '')
      : base.detail
    return { ...base, detail, ageSeconds, stale, bondGrievances: [], radioGrievances: [] }
  }

  if (samples.length === 0) {
    return {
      verdict: 'rompu', headline: 'Aucune mesure', detail: null,
      ageSeconds, stale, bondGrievances: [], radioGrievances: [],
    }
  }

  // Même connecté, un relevé trop vieux ne dit rien de l'instant présent.
  if (stale) {
    return {
      verdict: 'rompu',
      headline: 'Mesures périmées',
      detail: `dernier relevé il y a ${Math.round(ageSeconds!)} s`,
      ageSeconds, stale, bondGrievances: [], radioGrievances: [],
    }
  }

  // Tous les liens comptent, y compris filaires : c'est souvent le lien fibre qui
  // porte le débit, et une supervision qui l'ignore annonce « tout va bien »
  // alors que le lien principal est tombé.
  const links = samples
  const up = links.filter((s) => s.wan.connected)

  // Un lien connecté mais écarté du bond ne transporte rien. Le compter comme
  // porteur reviendrait à annoncer une redondance qui n'existe pas.
  //
  // Le test est celui du contrat, et lui seul : `state !== null && in_bond`.
  // Le `!state?.startsWith('ACTIVE')` d'avant comptait un état INCONNU comme
  // « hors du bond » — `undefined?.` puis `!undefined` vaut `true` —, si bien
  // qu'un lien de `tunnel: { state: null }` déclenchait « 1 hors du bond »
  // pendant que `WanCard` affichait « — » pour le même champ. Un état null ne
  // dit rien : ni porteur, ni écarté.
  const outOfBond = up.filter((s) => ecarteDuBond(s.wan))

  if (up.length === 0) {
    return {
      verdict: 'rompu',
      headline: 'Liaison rompue',
      detail:
        (links.length === 1
          ? "le seul lien n'est pas connecté"
          : `aucun des ${links.length} liens n'est connecté`)
        + (describeCause(links) === null ? '' : ` (${describeCause(links)})`),
      ageSeconds, stale, bondGrievances: [], radioGrievances: [],
    }
  }

  // Tous les liens connectés sont écartés du bond : plus rien ne transite, ce
  // qui est une rupture et non une dégradation. Mesuré : quatre SIM connectées
  // et quatre tunnels en `WAN_DOWN` donnaient « 4 liens sur 4 connectés — 4 hors
  // du bond » en ambre, alors que le module pose lui-même qu'un lien écarté du
  // bond ne transporte rien. Depuis que le backend attache un état `PEER_DOWN` à
  // tous les liens quand le bond entier tombe, c'est le cas nominal de la
  // rupture de tunnel — celui qu'il faut le plus sûrement voir en rouge.
  //
  // Encore faut-il que le bond soit le chemin du trafic : quand il n'achemine
  // que des routes dédiées, tous ses liens peuvent tomber sans que rien ne
  // s'arrête du trafic de l'exploitant. Annoncer « plus rien ne transite »
  // serait alors le contraire de la vérité.
  // Ces trois faits se calculent AVANT la branche « tunnel tombé » : elle
  // sortait sans eux, et sur un châssis où le tunnel n'est jamais attendu —
  // le MAX HD2 — elle taisait à jamais un signal faible et un lien tombé,
  // que le chemin ordinaire aurait dits. « Écartés du verdict, jamais tus. »
  const weak = up.filter(
    (s) =>
      s.wan.kind === 'cellular' &&
      overallQuality(s.wan.rsrp, s.wan.rsrq, s.wan.sinr) === 'faible',
  )
  // Un Ethernet jamais raccordé depuis l'allumage n'est pas « tombé » : il
  // reste compté au dénominateur — dit dans le décompte —, pas en grief.
  const jamaisRaccordes = links.filter(
    (s) => !s.wan.connected && s.wan.kind === 'ethernet' && neverConnected.includes(s.wan.wan_id),
  )
  // Un lien DÉSACTIVÉ par l'exploitant n'est pas tombé : c'est une décision.
  // Il restait « absent du relevé » et comptait comme une panne.
  const desactives = links.filter((s) => s.wan.disabled)
  const tombes = links.filter(
    (s) => !s.wan.connected && !jamaisRaccordes.includes(s) && !s.wan.disabled,
  )
  const down = tombes.length
  const cause = describeCause(tombes)
  const radioGrievances = weak.length > 0
    ? [`${weak.length} ${accord(weak.length, 'lien')} en signal faible`]
    : []

  // Le bond est-il le chemin du trafic ? Seul `routes` dit que non — le pair
  // n'annonce que des sous-réseaux dédiés et rien d'autre n'y transite. Le
  // verdict recevait cette réponse en booléen, et le booléen s'appelait « le
  // bond porte le trafic » pour déclencher une branche qui dit qu'il ne le
  // porte plus : `tout`, `porte` et `inconnu` y tombaient sous une même phrase
  // alors qu'ils n'ont ni la même gravité ni la même lecture.
  //
  // Et un bond qui n'a JAMAIS porté dans cette session ne porte pas le flux
  // non plus : le serveur l'a tranché (`tunnel_attendu`), et n'ouvre aucune
  // alerte dessus. L'écran appliquait sa propre règle et titrait « tombé ».
  const porte = porteLeFlux(role, tunnelAttendu)

  if (porte && outOfBond.length === up.length) {
    // Des liens DEBOUT, tous hors du bond : c'est un tunnel tombé, pas une
    // liaison rompue. « Plus rien ne transite » était une affirmation que
    // seul l'état du tunnel fondait — or un concentrateur SpeedFusion peut
    // être injoignable sans que la connectivité en souffre. Sur le premier
    // équipement rencontré hors du réseau de développement, un MAX HD2, les
    // trois WAN étaient debout et l'écran titrait « rompue » en rouge ; c'est
    // l'exploitant qui a posé la question juste.
    //
    // Une première correction s'appuyait sur le débit MESURÉ aux interfaces
    // pour départager. C'était le bruit d'un instant : sur un réseau calme, le
    // débit vaut 0,0 la plupart des relevés, et le bandeau clignotait entre
    // ambre et rouge au gré des paquets. L'état du tunnel, lui, est stable.
    //
    // Et le bandeau n'affirme plus que « le trafic sort en direct » : il ne
    // le mesure pas, et un routeur configuré pour couper le trafic quand le
    // tunnel tombe dirait la même phrase. Il dit ce qu'il sait — le tunnel
    // ne porte plus — et ce qui en découle : l'agrégation ne protège plus.
    const causeTunnel = tunnelCause ?? describeTunnelCause(outOfBond)
    const etat = `${up.length} ${accord(up.length, 'lien')} sur ${links.length}`
      + ` ${accord(up.length, 'connecté')}, ${up.length > 1 ? 'tous' : 'mais'} hors du bond`
      + (causeTunnel ? ` (${causeTunnel})` : '')
      + (down > 0 ? ` — ${down} ${accord(down, 'déconnecté')}${cause ? ` (${cause})` : ''}` : '')
    // Ce que le tunnel PORTAIT, dit selon ce qu'on en sait — et rien de plus.
    // Un cinquième rôle ne tombe dans aucune phrase par défaut : le `never`
    // l'arrête à la compilation.
    let consequence: string
    switch (role.kind) {
      case 'tout':
        consequence = 'tout le trafic passait par le tunnel — il n’a plus de chemin déclaré'
        break
      case 'porte':
        consequence = `les routes desservies (${role.routes.join(', ')}) ne sont plus jointes`
        break
      case 'inconnu':
        consequence = role.routes
          ? `le pair annonce ${role.routes.join(', ')} ; ce que le tunnel portait n’a pas été mesuré — l’agrégation ne protège plus`
          : 'le pair ne déclarait rien ; ce que le tunnel portait est inconnu — l’agrégation ne protège plus'
        break
      // `routes` n'arrive pas ici : TypeScript suit le prédicat `porteLeFlux` et l'a déjà
      // retiré du type — c'est le `never` du défaut qui le garantit.
      default: {
        const jamais: never = role
        throw new Error(`rôle de bond inconnu : ${JSON.stringify(jamais)}`)
      }
    }
    return {
      verdict: 'degrade',
      headline: 'Tunnel SpeedFusion tombé',
      detail: `${etat} — ${consequence}`,
      ageSeconds, stale, bondGrievances: [], radioGrievances,
    }
  }

  // Les grandeurs du tunnel ne disent quelque chose que des liens qui portent
  // effectivement le flux : un lien écarté du bond a déjà son propre décompte,
  // et sa latence ne concerne plus personne.
  const carrying = up.filter((s) => s.wan.tunnel?.in_bond)

  // Les latences absentes sont retirées AVANT la médiane, et non remplacées par
  // une valeur de convenance : un lien dont on ignore le temps de transit ne
  // doit ni être jugé, ni servir à juger les autres.
  const timed = carrying.filter((s) => s.wan.tunnel!.rtt_ms !== null)
  const slow = latencyOutliers(
    timed.map((s) => ({
      rtt: s.wan.tunnel!.rtt_ms!,
      beyondCutoff: s.wan.tunnel!.beyond_cutoff,
    })),
    seuilUtile(latencyThresholdMs),
  )

  // Chaque lien est jugé sur SON dénominateur, pas sur celui du relevé : deux
  // liens du même bond ne portent pas le même volume.
  const lossy = carrying
    .map((s) => reportableLoss(s.wan.tunnel!))
    .filter((l): l is DirectionLoss => l !== null && l.percent >= LOSS_PERCENT_THRESHOLD)

  const counted =
    `${up.length} ${accord(up.length, 'lien')} sur ${links.length} ${accord(up.length, 'connecté')}`
    + (jamaisRaccordes.length > 0
      ? ` (${jamaisRaccordes.length} ${accord(jamaisRaccordes.length, 'lien')} filaire`
        + `${jamaisRaccordes.length > 1 ? 's' : ''} jamais raccordé${jamaisRaccordes.length > 1 ? 's' : ''} depuis l’allumage)`
      : '')
    + (desactives.length > 0
      ? ` (${desactives.length} désactivé${desactives.length > 1 ? 's' : ''})`
      : '')

  // Les griefs du tunnel passent AVANT le signal faible : le 30 août, le lien à
  // 1 929 ms avait le meilleur SINR des quatre. Lire « 1 en signal faible » en
  // premier enverrait chercher la panne du mauvais côté.
  const tunnel = [
    outOfBond.length > 0 ? `${outOfBond.length} hors du bond` : null,
    slow.length > 0 ? `${slow.length} en latence excessive (${describeLatency(slow)})` : null,
    lossy.length > 0 ? `${lossy.length} en ${describeLoss(lossy)}` : null,
  ].filter((c): c is string => c !== null)

  // Ce qui juge la LIAISON quand le trafic ne passe pas par le tunnel : le lien
  // est-il debout, et son signal tient-il ?
  //
  // C'est court, et c'est volontaire. Le débit d'interface ne s'y ajoute pas :
  // un lien à zéro kbit/s est un lien AU REPOS, et en faire un grief allumerait
  // le bandeau sur les quatre SIM d'un véhicule à l'arrêt. On ne remplace pas
  // une mauvaise règle par une règle qui crie.
  const liaison = [
    down > 0
      ? `${down} ${accord(down, 'déconnecté')}${cause === null ? '' : ` (${cause})`}`
      : null,
    ...(porte ? tunnel : []),
    // `weak` ne figure PLUS ici : un signal faible ne dégrade pas la liaison.
    // Voir `radioGrievances` pour la mesure qui l'établit.
  ].filter((c): c is string => c !== null)

  // Écartés du verdict, jamais tus : l'appelant les affiche auprès du bond, le
  // seul endroit où ils décrivent quelque chose.
  const bondGrievances = porte ? [] : tunnel
  // `radioGrievances` est calculé plus haut, avant la branche du tunnel tombé.

  if (liaison.length > 0) {
    return {
      verdict: 'degrade',
      headline: 'Liaison dégradée',
      detail: `${counted} — ${liaison.join(', ')}`,
      ageSeconds, stale, bondGrievances, radioGrievances,
    }
  }

  return {
    verdict: 'ok', headline: 'Liaison OK',
    // Un bond jamais établi est dit comme un ÉTAT, à côté du décompte — ni
    // tu, ni promu en panne. C'est ce que l'exploitant a demandé du HD2.
    detail: tunnelAttendu === false && outOfBond.length > 0 && outOfBond.length === up.length
      ? `${counted} — tunnel SpeedFusion jamais établi dans cette session`
        + (tunnelCause ? ` (${tunnelCause})` : '')
      : counted,
    ageSeconds, stale, bondGrievances, radioGrievances,
  }
}


/**
 * Lisse les changements de verdict.
 *
 * Un bandeau qui bascule à chaque cycle de scrutation est un bandeau qu'on cesse
 * de regarder. On n'entre dans un état plus grave qu'après l'avoir constaté
 * `enter` fois de suite, et on n'en sort qu'après `leave` constats — le seuil de
 * sortie est explicite, non dérivé du seuil d'entrée.
 *
 * `recent` contient les verdicts BRUTS, du plus ancien au plus récent, le
 * dernier étant celui du cycle courant. `displayed` est ce qui est à l'écran :
 * comparer au dernier verdict brut plutôt qu'à l'affichage laisserait passer
 * une oscillation sur deux.
 *
 * Une rupture, elle, s'affiche immédiatement : différer une mauvaise nouvelle
 * serait exactement le défaut qu'on cherche à corriger.
 */
export const VERDICT_SEVERITY: Record<Verdict, number> = { ok: 0, degrade: 1, rompu: 2 }

export function smoothVerdict(
  recent: Verdict[],
  displayed: Verdict,
  { enter = 3, leave = SORTIE_HYSTERESIS } = {},
): Verdict {
  if (recent.length === 0) return displayed

  // Établissement : tant qu'on n'a pas assez de relevés pour juger d'une
  // tendance, on suit ce qu'on mesure. Amortir dès le premier rendu figeait
  // « Aucune mesure » pendant toute la fenêtre de lissage, alors que les
  // données étaient déjà à l'écran.
  if (recent.length < leave) return recent[recent.length - 1]

  const current = recent[recent.length - 1]
  if (current === 'rompu') return 'rompu'
  if (current === displayed) return displayed

  // Il faut moins de constats pour aggraver l'affichage que pour l'apaiser :
  // signaler tôt, se rassurer tard.
  if (VERDICT_SEVERITY[current] > VERDICT_SEVERITY[displayed]) {
    const window = recent.slice(-enter)
    return window.length >= enter && window.every((v) => v === current) ? current : displayed
  }
  // Désescalade : `leave` constats de suite tous MOINS GRAVES que l'affichage,
  // et l'on descend — jusqu'au pire d'entre eux, pas plus bas. Exiger `leave`
  // constats IDENTIQUES retenait un « rompu » indéfiniment dès qu'un grief
  // clignotait un cycle sur deux : ok, dégradé, ok, dégradé… n'apaise jamais
  // une règle qui attend cinq fois la même chose, et le bandeau restait rouge
  // sur une liaison qui n'était plus que dégradée.
  const window = recent.slice(-leave)
  if (window.length < leave || !window.every((v) => VERDICT_SEVERITY[v] < VERDICT_SEVERITY[displayed])) {
    return displayed
  }
  return window.reduce((pire, v) => (VERDICT_SEVERITY[v] > VERDICT_SEVERITY[pire] ? v : pire))
}
