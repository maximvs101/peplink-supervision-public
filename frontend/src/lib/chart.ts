/** Mise à l'échelle des graphes d'historique.
 *
 * Extrait de `HistoryChart` pour être vérifiable : ce sont des règles de lecture,
 * pas du rendu, et elles se sont révélées être la source des défauts les plus
 * trompeurs — une échelle mal choisie fait mentir une courbe juste.
 */

export interface Metric {
  key: string
  label: string
  unit: string
  color: string
  /**
   * Fenêtre minimale de l'axe vertical.
   *
   * Sans elle, l'échelle suit l'amplitude des données : un RSRP qui varie de
   * 2 dB en trente minutes — c'est-à-dire un signal parfaitement stable —
   * remplit toute la hauteur et se lit comme une instabilité. C'est un MINIMUM :
   * une mesure qui en sort élargit l'axe. Elle ne cesse de l'élargir que si elle
   * s'en écarte au point d'écraser la bande utile, auquel cas `framedDomain` la
   * laisse hors du cadre et la SIGNALE — jamais elle n'est tue.
   *
   * Les bornes reprennent les seuils de qualité, de sorte que la position de la
   * courbe dans le cadre porte elle-même du sens.
   */
  window?: [number, number]
  /** Diviseur appliqué avant affichage (kbit/s -> Mbit/s). */
  divide?: number
  /** L'axe part de zéro : un débit a un zéro naturel, pas un niveau radio. */
  fromZero?: boolean
  /** La grandeur est-elle un TAUX ?
   *
   *  Un taux ne se publie jamais sans son dénominateur — la règle des deux cents
   *  paquets. Le graphe traitait un dénominateur absent comme « publiable sans
   *  condition », ce qui est l'inverse : ne pas savoir sur combien de paquets un
   *  taux a été établi interdit de le tracer. Le drapeau dit quelles grandeurs
   *  sont concernées, plutôt que de le deviner à l'unité. */
  rate?: boolean
}

/** Grandeurs du tunnel : ce sont elles qui décident si une image gèle, pas le
 *  niveau radio. Elles ne dépendent pas du support du lien — mais elles
 *  n'existent que si l'ÉQUIPEMENT porte un tunnel, d'où `metricsFor`.
 *
 *  Exportées parce que le test en recopiait la liste à la main : ajouter une
 *  grandeur de tunnel lui aurait fait vérifier autre chose sans jamais échouer. */
/** La couleur d'une courbe ne signifie RIEN : une seule grandeur est tracée à
 *  la fois, et la teinte est réservée à la qualité radio. La courbe emprunte
 *  donc la lumière du bond, comme tout ce qui est structurel dans cette
 *  interface. La version précédente peignait sept séries avec sept nuances de
 *  framework, dont le même violet pour le RSRP et pour le débit montant — et un
 *  débit montant qui changeait de couleur selon le support du lien. */
// Le jeton du bond : clair sur fond sombre, foncé sur fond clair (R-0.1).
const TRACE = 'var(--bond)'

export const TUNNEL_METRICS: Metric[] = [
  // « Latence du bond », et non « Latence » tout court : depuis que la latence
  // du CHEMIN existe, les deux voisinent dans la même barre de boutons. Un même
  // mot pour deux grandeurs différentes à un centimètre l'une de l'autre est le
  // défaut que « Durée de session » a déjà coûté à cette interface.
  { key: 'rtt_ms', label: 'Latence du bond', unit: 'ms', color: TRACE, window: [0, 100] },
  { key: 'loss_percent', label: 'Perte', unit: '%', color: TRACE, fromZero: true, rate: true },
  // Ce que le BOND porte réellement sur ce lien, par opposition au débit
  // d'interface au-dessus : les deux diffèrent dès qu'un trafic sort hors
  // tunnel. Les deux colonnes existent en base, figurent dans la liste blanche
  // de l'historique, et sont même affichées en tuile sur la carte du lien —
  // mais aucun bouton n'en traçait la courbe. Le serveur savait déjà répondre.
  {
    key: 'tunnel_rx_kbps', label: 'Bond ↓', unit: 'Mbit/s', color: TRACE,
    divide: 1000, fromZero: true, window: [0, 1],
  },
  {
    key: 'tunnel_tx_kbps', label: 'Bond ↑', unit: 'Mbit/s', color: TRACE,
    divide: 1000, fromZero: true, window: [0, 1],
  },
]

/** La latence du CHEMIN — à ne pas confondre avec celle du tunnel.
 *
 *  Elle vaut pour TOUS les supports, filaire compris : c'est la seule que
 *  possèdent un lien hors du bond et un châssis sans SpeedFusion. Relevée une
 *  fois par minute, sa courbe est donc clairsemée — un point par minute, et des
 *  trous entre, ce qui est exact. */
const LATENCE_CHEMIN: Metric = {
  key: 'path_latency_ms', label: 'Latence du lien', unit: 'ms', color: TRACE, window: [0, 100],
}

export const CELLULAR_METRICS: Metric[] = [
  { key: 'sinr', label: 'SINR', unit: 'dB', color: TRACE, window: [0, 25] },
  { key: 'rsrp', label: 'RSRP', unit: 'dBm', color: TRACE, window: [-115, -75] },
  { key: 'rsrq', label: 'RSRQ', unit: 'dB', color: TRACE, window: [-20, -5] },
  // Les deux sens, au même rang. L'équipement fournit de la connectivité : selon
  // l'usage qui en est fait, c'est l'un ou l'autre qui compte, et l'outil n'a pas
  // à en choisir un. Le montant n'était pas traçable du tout — il figurait
  // pourtant dans la liste blanche de l'historique et dans la base, avec des
  // milliers de valeurs par lien cellulaire.
  {
    key: 'rx_kbps', label: 'Débit ↓', unit: 'Mbit/s', color: TRACE,
    divide: 1000, fromZero: true, window: [0, 1],
  },
  {
    key: 'tx_kbps', label: 'Débit ↑', unit: 'Mbit/s', color: TRACE,
    divide: 1000, fromZero: true, window: [0, 1],
  },
  LATENCE_CHEMIN,
]

/** Un lien Wi-Fi amont ne remonte qu'une force de signal. */
export const WIFI_METRICS: Metric[] = [
  { key: 'rssi', label: 'Force', unit: 'dBm', color: TRACE, window: [-90, -40] },
  LATENCE_CHEMIN,
]

/** La fenêtre des débits vaut un mégabit par seconde, et c'est un MINIMUM comme
 *  partout ailleurs.
 *
 *  Sans elle, le cadre robuste se resserrait sur un lien AU REPOS : le 95e
 *  centile d'une série oisive vaut quelques kilobits, la bande « utile » devenait
 *  du bruit amplifié cent fois, et l'axe graduait « 0.0 » cinq fois de suite en
 *  signalant quinze mesures hors cadre pour une pointe à 0,1 Mbit/s. La fenêtre
 *  dit ce que la latence disait déjà avec son `[0, 100]` : voici la plage qui
 *  mérite d'être protégée ; en deçà, il n'y a rien à protéger. */

/** Un lien filaire n'a aucune grandeur radio : il ne reste que le trafic. */
export const WIRED_METRICS: Metric[] = [
  {
    key: 'rx_kbps', label: 'Débit ↓', unit: 'Mbit/s', color: TRACE,
    divide: 1000, fromZero: true, window: [0, 1],
  },
  {
    key: 'tx_kbps', label: 'Débit ↑', unit: 'Mbit/s', color: TRACE,
    divide: 1000, fromZero: true, window: [0, 1],
  },
  // C'est ce lien-là qui y gagne le plus : jusqu'ici un filaire n'avait AUCUNE
  // latence, ni de chemin — non lue — ni de tunnel, qu'il ne porte pas toujours.
  LATENCE_CHEMIN,
]

/** Grandeurs PROPRES à chaque type de lien, sans le tunnel : celui-ci relève de
 *  l'équipement, pas du support. */
export const METRICS_BY_KIND: Record<string, Metric[]> = {
  cellular: CELLULAR_METRICS,
  wifi: WIFI_METRICS,
  ethernet: WIRED_METRICS,
}

/**
 * Grandeurs traçables pour un lien, selon son support ET l'équipement.
 *
 * Les grandeurs du tunnel étaient ajoutées aux trois types sans condition. Sur
 * un équipement sans SpeedFusion, la page de détail offrait donc deux boutons
 * « Latence » et « Perte » de plus, qui traçaient un graphe VIDE sur un axe
 * gradué 0-100 : l'historique renvoie des points datés à valeur nulle, le
 * garde-fou « Collecte en cours » n'est jamais atteint et `verticalDomain`
 * rend la fenêtre par défaut. Une donnée que l'équipement ne produit pas ne
 * doit pas apparaître comme une donnée mauvaise — c'est déjà ce que fait
 * `showThroughput`, piloté par les capacités détectées.
 */
export function metricsFor(kind: string, { hasTunnel = false } = {}): Metric[] {
  const own = METRICS_BY_KIND[kind] ?? CELLULAR_METRICS
  return hasTunnel ? [...own, ...TUNNEL_METRICS] : own
}

export interface Point {
  t: number
  value: number | null
  /** Une mesure de l'heure que le routeur garde, dans un trou de supervision
   *  (R-3.1) : tracée à part, jamais dans `value`. */
  comble?: number | null
}

/* --- Bornage robuste de l'axe vertical ------------------------------------ *
 *
 * L'axe partait de la fenêtre puis s'élargissait à TOUTES les données. Une seule
 * mesure aberrante suffit alors à écraser la bande utile : sur la latence du
 * 30 août, un point à 1 929 ms au milieu d'une série tenant entre 25 et 35 ms
 * étirait l'axe à [0, 1 929] et réduisait la bande utile à 1,7 % de la hauteur —
 * exactement à l'instant où l'exploitant vient la regarder. La courbe était
 * juste et le graphe illisible.
 *
 * On borne donc l'axe sur le CORPS des mesures, et on SIGNALE ce qui reste
 * dehors. Signaler, pas masquer : une mesure qui sort du cadre est le plus
 * souvent la plus intéressante de la série, et un graphe qui l'escamote sans
 * rien dire serait une sixième façon de mentir à son exploitant.
 */

/** Centiles qui délimitent le corps des mesures. Le 95e laisse dehors une mesure
 *  sur vingt : sur une fenêtre de trente minutes scrutée toutes les 5,3 s, cela
 *  représente plusieurs centaines de points, donc une quinzaine de mesures. */
export const ROBUST_LOW_QUANTILE = 0.05
export const ROBUST_HIGH_QUANTILE = 0.95

/** À partir de quel dépassement on renonce à faire entrer un extrême dans le
 *  cadre, exprimé en hauteurs de bande utile.
 *
 *  Un extrême situé à une hauteur de bande au-dessus du cadre réduit déjà la
 *  bande à la moitié du graphe. En deçà, on préfère tout montrer : un cadre
 *  rétréci pour épargner 20 % de hauteur ne vaut pas la mention qu'il coûte. */
export const FRAME_TRIGGER = 1

/** Un cadre vertical, avec ce qu'il laisse dehors. */
export interface Framing {
  domain: [number, number]
  /** Mesures au-dessus / en dessous du cadre. Elles sont à SIGNALER — jamais à
   *  passer sous silence. */
  above: number
  below: number
  /** Extrêmes réellement mesurés, pour la mention. */
  highest: number | null
  lowest: number | null
}

/** Quantile par rang le plus proche, sans interpolation : la borne rendue est
 *  une valeur qu'on a vraiment relevée, et non une valeur intermédiaire qu'on
 *  aurait inventée. */
function quantile(sorted: number[], q: number): number {
  const rank = Math.ceil(q * sorted.length) - 1
  return sorted[Math.min(sorted.length - 1, Math.max(0, rank))]
}

/**
 * Cadre vertical d'une série, et les mesures qu'il laisse dehors.
 *
 * La bande à protéger est le corps des mesures, élargi à la fenêtre déclarée par
 * la métrique — laquelle reste un MINIMUM, comme avant. On ne rétrécit le cadre
 * que si cette bande a une hauteur (sinon il n'y a rien à protéger : une série
 * de perte entièrement nulle avec un unique pic à 33,33 % se lit parfaitement
 * sur un axe [0, 33], et la rogner n'apprendrait rien) et que l'extrême lui
 * coûterait plus de la moitié du graphe.
 *
 * Sur une poignée de points, le 95e centile EST le point extrême et rien n'est
 * borné : quatre mesures ne permettent pas de désigner l'aberrante. C'est
 * voulu — la fenêtre de trente minutes en porte plusieurs centaines.
 */
export function framedDomain(metric: Metric, values: number[]): Framing {
  if (values.length === 0) {
    return { domain: metric.window ?? [0, 1], above: 0, below: 0, highest: null, lowest: null }
  }
  const sorted = [...values].sort((a, b) => a - b)
  const lowest = sorted[0]
  const highest = sorted[sorted.length - 1]

  // Une grandeur sans fenêtre déclarée n'en a PAS — elle ne se replie pas sur
  // les extrêmes de la série.
  //
  // Le repli valait `[lowest, highest]`, et il rendait le bornage inerte par
  // construction : `bandHigh` prenait alors le maximum de la série, donc
  // exactement `naiveHigh`, donc un dépassement nul, donc jamais de cadre. Les
  // trois grandeurs concernées sont précisément les plus sujettes aux pics —
  // perte et les deux débits. Mesuré sur ce matériel : deux cents relevés à
  // 1 Mbit/s et une pointe à 155 Mbit/s donnaient un axe [0, 155] où la
  // bande utile occupait 0,65 % de la hauteur, c'est-à-dire le défaut même que
  // ce module a été écrit pour corriger. Aucun test ne le voyait : le bloc de
  // bornage n'éprouvait que la latence, seule grandeur à porter une fenêtre.
  const windowLow = metric.window?.[0] ?? null
  const windowHigh = metric.window?.[1] ?? null

  // Bornes d'avant : la fenêtre, élargie à toutes les données. Un débit se lit
  // par rapport à zéro — démarrer l'axe à la valeur minimale ferait passer une
  // liaison stable pour une liaison en dents de scie — et le plafond minimal
  // évite un axe dégénéré quand tout vaut zéro.
  const naiveLow = metric.fromZero ? 0 : Math.min(lowest, windowLow ?? lowest)
  const naiveHigh = metric.fromZero
    ? Math.max(highest, 1)
    : Math.max(highest, windowHigh ?? highest)

  const bandLow = metric.fromZero
    ? 0
    : Math.min(quantile(sorted, ROBUST_LOW_QUANTILE), windowLow ?? Infinity)
  const bandHigh = Math.max(quantile(sorted, ROBUST_HIGH_QUANTILE), windowHigh ?? -Infinity)
  const band = bandHigh - bandLow

  // Le bas est borné aux mêmes conditions que le haut : un axe ne sait pas de
  // quel côté tombe l'aberration, et un effondrement isolé écrase la bande
  // utile exactement comme un pic.
  const clipHigh = band > 0 && naiveHigh - bandHigh > band * FRAME_TRIGGER
  const clipLow = band > 0 && !metric.fromZero && bandLow - naiveLow > band * FRAME_TRIGGER
  const domain: [number, number] = [clipLow ? bandLow : naiveLow, clipHigh ? bandHigh : naiveHigh]

  return {
    domain,
    above: values.filter((v) => v > domain[1]).length,
    below: values.filter((v) => v < domain[0]).length,
    highest,
    lowest,
  }
}

/** Bornes de l'axe vertical : la fenêtre de la métrique, élargie au corps des
 *  mesures. Les appelants qui n'ont que faire des points hors cadre — il y en
 *  a — gardent une signature inchangée. */
export function verticalDomain(metric: Metric, values: number[]): [number, number] {
  return framedDomain(metric, values).domain
}

/**
 * Mention des mesures laissées hors du cadre, `null` s'il n'y en a aucune.
 *
 * C'est la contrepartie du bornage, et elle n'est pas facultative : sans elle,
 * le pic du 30 août disparaîtrait purement et simplement du graphe. La valeur
 * extrême est citée, pour que la mention porte le fait et pas seulement son
 * existence.
 */
export function outOfFrameNote(framing: Framing, metric: Metric): string | null {
  const digits = axisDigits(framing.domain)
  const show = (v: number) =>
    `${v.toLocaleString('fr-FR', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })} ${metric.unit}`.trim()
  const mesures = (n: number) => `${n} ${n > 1 ? 'mesures' : 'mesure'}`

  const parts = [
    framing.above > 0
      ? `${mesures(framing.above)} au-dessus du cadre, jusqu’à ${show(framing.highest!)}`
      : null,
    framing.below > 0
      ? `${mesures(framing.below)} sous le cadre, jusqu’à ${show(framing.lowest!)}`
      : null,
  ].filter(Boolean)
  return parts.length > 0 ? parts.join(' · ') : null
}

/**
 * Coupe la série là où la collecte s'est interrompue.
 *
 * L'axe des temps étant linéaire, une interruption occupe déjà la largeur qui
 * lui revient — mais elle serait quand même franchie d'un trait droit, donnant
 * à voir une continuité qui n'a pas existé. Le seuil se déduit de l'intervalle
 * médian : la période de scrutation est configurable et varie d'un équipement
 * à l'autre.
 */
export function withGaps(points: Point[], factor = 3): Point[] {
  // La série DOIT être croissante dans le temps : tout le raisonnement repose
  // sur des écarts positifs, et un écart négatif ferait une médiane négative,
  // donc un `median <= 0`, donc plus aucun trou dessiné — une coupure effacée en
  // silence. Le stockage ordonne aujourd'hui ses deux requêtes par `ts`, et
  // l'enveloppe allégée ne passe plus par ici : le cas n'est plus déclenchable.
  // La garde reste, parce qu'elle coûte une ligne et que le jour où une source
  // rendra des points dans le désordre, la fonction doit le corriger plutôt que
  // de cesser silencieusement de faire son travail.
  points = [...points].sort((a, b) => a.t - b.t)
  const intervals = points.slice(1).map((p, i) => p.t - points[i].t).sort((a, b) => a - b)
  const median = intervals.length ? intervals[Math.floor(intervals.length / 2)] : 0
  if (median <= 0) return points

  const out: Point[] = []
  points.forEach((point, index) => {
    if (index > 0 && point.t - points[index - 1].t > median * factor) {
      out.push({ t: points[index - 1].t + 1, value: null })
    }
    out.push(point)
  })
  return out
}

/**
 * Décimales des graduations, choisies sur l'amplitude de l'axe.
 *
 * Un choix fait valeur par valeur ferait voisiner « -115 » et « -95,0 » sur la
 * même échelle.
 */
export const axisDigits = (domain: [number, number]): number => {
  const amplitude = domain[1] - domain[0]
  if (!Number.isFinite(amplitude) || amplitude <= 0) return 1
  if (amplitude >= 10) return 0
  // Le nombre de décimales SUIT l'amplitude, au lieu d'être plafonné à deux.
  // Un axe de 0 à 0,1 Mbit/s graduait « 0.00 » cinq fois de suite — le même
  // défaut qu'il corrigeait un palier plus haut, simplement repoussé d'un
  // facteur dix. Cinq décimales au plus : au-delà, ce n'est plus un débit
  // qu'on lit, et `toFixed` refuse au-delà de cent.
  return Math.min(5, Math.max(1, Math.ceil(-Math.log10(amplitude)) + 1))
}

/** Famille de technologie d'un libellé de RAT : « 5G NSA » → 5G, « LTE-A » → LTE. */
const familleRat = (rat: string | null | undefined): string | null => {
  if (!rat) return null
  if (/5G|NR/i.test(rat)) return '5G'
  if (/LTE|4G/i.test(rat)) return 'LTE'
  if (/3G|UMTS|HSPA|WCDMA/i.test(rat)) return '3G'
  return null
}

/** Les instants où la TECHNOLOGIE d'une série radio change de famille.
 *
 *  Un SINR 5G et un SINR LTE ne se mesurent pas sur la même porteuse et ne se
 *  comparent pas : mesuré sur un Dome forcé en LTE, la courbe passait de 28 à
 *  13 dB sans rien qui le dise, et un changement de technologie se lisait comme
 *  une chute de signal. Un point sans technologie (un trou, une renégociation)
 *  ne rompt pas la continuité : on compare à la dernière famille connue. */
export function changementsDeTechno(
  points: { ts: string; rat?: string | null }[],
): { t: number; de: string; vers: string }[] {
  const sortie: { t: number; de: string; vers: string }[] = []
  let connue: string | null = null
  for (const p of points) {
    const f = familleRat(p.rat)
    if (f === null) continue
    if (connue !== null && f !== connue) sortie.push({ t: new Date(p.ts).getTime(), de: connue, vers: f })
    connue = f
  }
  return sortie
}
