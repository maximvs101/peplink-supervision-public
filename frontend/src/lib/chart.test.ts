import { describe, expect, it } from 'vitest'

import {
  changementsDeTechno,
  CELLULAR_METRICS,
  TUNNEL_METRICS,
  WIRED_METRICS,
  axisDigits,
  framedDomain,
  metricsFor,
  outOfFrameNote,
  verticalDomain,
  withGaps,
  type Metric,
} from './chart'

const rsrp = CELLULAR_METRICS.find((m) => m.key === 'rsrp')!
/** Tirées du module et non recopiées : la liste écrite à la main ici vérifiait
 *  sa propre copie, si bien qu'une grandeur de tunnel ajoutée là-bas laissait le
 *  test passer en contrôlant autre chose. */
const TUNNEL_KEYS = new Set(TUNNEL_METRICS.map((m) => m.key))
const debit = WIRED_METRICS.find((m) => m.key === 'rx_kbps')!

describe('échelle verticale', () => {
  it('ne zoome pas sur une mesure stable', () => {
    // Cas réel mesuré : 2 dB d'amplitude sur trente minutes, c'est-à-dire un
    // signal parfaitement stable. Une échelle ajustée aux données le ferait
    // remplir toute la hauteur du graphe et se lire comme une instabilité.
    expect(verticalDomain(rsrp, [-89, -88, -87])).toEqual([-115, -75])
  })

  it('élargit l’axe plutôt que de tronquer une mesure hors fenêtre', () => {
    expect(verticalDomain(rsrp, [-125, -70])).toEqual([-125, -70])
    expect(verticalDomain(rsrp, [-120, -88])).toEqual([-120, -75])
  })

  it('ancre un débit à zéro', () => {
    // Démarrer à la valeur minimale ferait passer une liaison stable autour de
    // 190 Mbit/s pour une liaison en dents de scie.
    expect(verticalDomain(debit, [188, 190, 194])).toEqual([0, 194])
  })

  it('évite un axe dégénéré quand tout vaut zéro', () => {
    expect(verticalDomain(debit, [0, 0, 0])).toEqual([0, 1])
  })

  it('retombe sur la fenêtre quand il n’y a aucune mesure', () => {
    expect(verticalDomain(rsrp, [])).toEqual([-115, -75])
    expect(verticalDomain({ key: 'x', label: 'x', unit: '', color: '' }, [])).toEqual([0, 1])
  })

  it('sans fenêtre déclarée, suit les données', () => {
    const brut: Metric = { key: 'x', label: 'x', unit: '', color: '' }
    expect(verticalDomain(brut, [3, 9])).toEqual([3, 9])
  })
})

describe('graduations', () => {
  it('n’affiche pas de décimale sur un axe large', () => {
    // Un choix fait valeur par valeur ferait voisiner « -115 » et « -95,0 ».
    expect(axisDigits([-115, -75])).toBe(0)
  })

  it('en garde une sur un axe étroit', () => {
    expect(axisDigits([0, 1])).toBe(1)
    expect(axisDigits([0, 9.5])).toBe(1)
  })
})

describe('interruptions de collecte', () => {
  const every10s = (count: number, from = 0) =>
    Array.from({ length: count }, (_, i) => ({ t: from + i * 10_000, value: i }))

  it('laisse une série régulière intacte', () => {
    const points = every10s(5)
    expect(withGaps(points)).toHaveLength(5)
    expect(withGaps(points).every((p) => p.value !== null)).toBe(true)
  })

  it('coupe la série sur une interruption', () => {
    // Sept minutes sans mesure : relier les deux bords donnerait à voir une
    // continuité qui n'a pas existé.
    const points = [...every10s(4), ...every10s(4, 460_000)]
    const withHole = withGaps(points)

    const nulls = withHole.filter((p) => p.value === null)
    expect(nulls).toHaveLength(1)
    // La rupture est posée juste après le dernier point mesuré.
    expect(nulls[0].t).toBe(30_001)
  })

  it('déduit le seuil de l’intervalle médian, pas d’une constante', () => {
    // Scrutation à 3 s : un trou de 30 s doit être vu, alors qu'il passerait
    // inaperçu avec un seuil taillé pour une scrutation à 10 s.
    const dense = Array.from({ length: 6 }, (_, i) => ({ t: i * 3000, value: 1 }))
    const points = [...dense, { t: 45_000, value: 2 }]
    expect(withGaps(points).filter((p) => p.value === null)).toHaveLength(1)
  })

  it('ne coupe rien quand il y a trop peu de points pour juger', () => {
    expect(withGaps([{ t: 0, value: 1 }])).toEqual([{ t: 0, value: 1 }])
    expect(withGaps([])).toEqual([])
  })

  it('ne modifie pas la série reçue', () => {
    const points = every10s(3)
    withGaps(points)
    expect(points).toHaveLength(3)
  })
})

describe('métriques par type de lien', () => {
  it('n’offre aucune grandeur radio sur un lien filaire', () => {
    // Mais il a désormais une LATENCE, et c'est la seule qu'il ait jamais eue :
    // le tunnel ne lui en donne que s'il en porte un, et `status.wan.latency`
    // en mesure une pour tous les supports.
    expect(metricsFor('ethernet').map((m) => m.key))
      .toEqual(['rx_kbps', 'tx_kbps', 'path_latency_ms'])
  })

  it('n’offre qu’une force de signal sur un lien Wi-Fi', () => {
    expect(metricsFor('wifi').map((m) => m.key)).toEqual(['rssi', 'path_latency_ms'])
  })

  it('ne propose les grandeurs du tunnel que si l’équipement en a un', () => {
    // Latence et perte décident si une image gèle quel que soit le support du
    // lien — mais le tunnel est une propriété de l'ÉQUIPEMENT. Mesuré : sur un
    // équipement sans SpeedFusion, la page de détail offrait deux boutons de
    // plus, qui traçaient un graphe vide sur un axe gradué 0-100.
    for (const kind of ['cellular', 'wifi', 'ethernet']) {
      const sans = metricsFor(kind).map((m) => m.key)
      expect(sans.filter((k) => TUNNEL_KEYS.has(k))).toEqual([])

      const avec = metricsFor(kind, { hasTunnel: true }).map((m) => m.key)
      for (const key of TUNNEL_KEYS) expect(avec).toContain(key)
    }
  })

  it('retombe sur les grandeurs cellulaires pour un type inconnu', () => {
    expect(metricsFor('inconnu')).toEqual(CELLULAR_METRICS)
  })

  it('ancre le taux de perte à zéro et borne la latence', () => {
    expect(TUNNEL_METRICS.find((m) => m.key === 'loss_percent')?.fromZero).toBe(true)
    expect(TUNNEL_METRICS.find((m) => m.key === 'rtt_ms')?.window).toEqual([0, 100])
  })

  it('affiche les débits en Mbit/s', () => {
    // L'axe montrait 194309 en kbit/s bruts. La latence du lien, elle, est en
    // millisecondes et ne se divise pas : le test ne porte que sur les débits.
    for (const metric of WIRED_METRICS.filter((m) => m.unit === 'Mbit/s')) {
      expect(metric.divide).toBe(1000)
    }
    expect(WIRED_METRICS.filter((m) => m.unit === 'Mbit/s')).toHaveLength(2)
  })

  it('donne une fenêtre à toute grandeur radio', () => {
    const radio = CELLULAR_METRICS.filter((m) => !m.fromZero)
    expect(radio).not.toHaveLength(0)
    for (const metric of radio) expect(metric.window).toBeDefined()
  })
})

describe('bornage robuste de l’axe', () => {
  const latence = TUNNEL_METRICS.find((m) => m.key === 'rtt_ms')!
  const perte = TUNNEL_METRICS.find((m) => m.key === 'loss_percent')!

  /** La latence du 30 août : une demi-heure entre 25 et 35 ms, et un point à
   *  1 929 ms. C'est le relevé qui a fait basculer le verdict, et c'est le même
   *  qui rendait son graphe illisible. */
  const trenteMinutes = (pic = 1929) => [
    ...Array.from({ length: 340 }, (_, i) => 25 + (i % 11)),
    pic,
  ]

  it('ne laisse pas un pic isolé écraser la bande utile', () => {
    // Avant : l'axe allait de 0 à 1 929 et la bande 25-35 ms occupait 1,7 % de
    // la hauteur — au moment précis où on vient la regarder.
    const { domain } = framedDomain(latence, trenteMinutes())
    expect(domain).toEqual([0, 100])
  })

  it('signale ce qu’il laisse dehors, au lieu de l’escamoter', () => {
    // Sans cette mention, le bornage serait une sixième façon de mentir : le
    // point le plus intéressant de la série disparaîtrait sans un mot.
    const framing = framedDomain(latence, trenteMinutes())
    expect(framing.above).toBe(1)
    expect(framing.highest).toBe(1929)

    const note = outOfFrameNote(framing, latence)!
    expect(note).toContain('1 mesure au-dessus du cadre')
    expect(note).toMatch(/1\s?929 ms/)
  })

  it('ne dit rien quand tout tient dans le cadre', () => {
    const framing = framedDomain(latence, trenteMinutes(40))
    expect(framing.domain).toEqual([0, 100])
    expect(framing.above).toBe(0)
    expect(outOfFrameNote(framing, latence)).toBeNull()
  })

  it('accorde la mention sur le nombre de mesures', () => {
    const framing = framedDomain(latence, [...trenteMinutes(), 1800, 1700])
    expect(framing.above).toBe(3)
    expect(outOfFrameNote(framing, latence)).toContain('3 mesures au-dessus')
  })

  it('ne borne pas pour épargner un cinquième de hauteur', () => {
    // 180 ms au-dessus d'une fenêtre de 100 ms : l'extrême coûte moins de la
    // moitié du graphe, et c'est une valeur qu'on veut voir en situation. Un
    // cadre rétréci ne vaut pas la mention qu'il coûte.
    const framing = framedDomain(latence, trenteMinutes(180))
    expect(framing.domain).toEqual([0, 180])
    expect(framing.above).toBe(0)
  })

  it('ne désigne pas d’aberration dans une poignée de mesures', () => {
    // Sur quatre points, le 95e centile EST le point extrême : quatre mesures ne
    // permettent pas de dire laquelle est aberrante. La fenêtre de trente
    // minutes en porte plusieurs centaines.
    expect(framedDomain(latence, [25, 33, 34, 1929]).domain).toEqual([0, 1929])
  })

  it('laisse un taux de perte montrer son pic', () => {
    // Série entièrement nulle avec un unique pic à 33,33 % : il n'y a aucune
    // bande utile à protéger, et un trait plat à zéro se lit parfaitement sur un
    // axe qui monte à 33. Le rogner n'apprendrait rien et cacherait l'événement.
    const framing = framedDomain(perte, [...Array(340).fill(0), 33.33])
    expect(framing.domain).toEqual([0, 33.33])
    expect(framing.above).toBe(0)
  })

  it('borne un DÉBIT sur une base ordinaire', () => {
    // Le bloc de bornage n'éprouvait que la latence — seule grandeur à porter
    // une fenêtre — et le repli `[lowest, highest]` rendait le cadre inerte par
    // construction sur toutes les autres : `bandHigh` prenait le maximum de la
    // série, donc exactement la borne naïve, donc un dépassement nul.
    //
    // EN MBIT/S, comme la chaîne réelle les fournit : `HistoryChart` divise par
    // mille AVANT d'appeler cette fonction, et la fenêtre déclarée des débits
    // est elle aussi en Mbit/s. La première version de ce test nourrissait la
    // fonction en kbit/s face à une fenêtre en Mbit/s — une combinaison que le
    // pipeline ne produit jamais. Il passait sans éprouver le cas qu'il
    // prétendait éprouver.
    //
    // Mesuré sur ce matériel : deux cents relevés autour d'1 Mbit/s et une
    // pointe à 155 Mbit/s — la bande utile occupait 0,65 % de la hauteur.
    const debit = CELLULAR_METRICS.find((m) => m.key === 'rx_kbps')!
    const framing = framedDomain(debit, [...Array(200).fill(1), 155])
    expect(framing.domain).toEqual([0, 1])
    expect(framing.above).toBe(1)
    expect(outOfFrameNote(framing, debit)).toContain('1 mesure au-dessus du cadre')
  })

  it('ne resserre pas le cadre sur un lien AU REPOS', () => {
    // Correction de la correction, constatée à l'écran : le cadre robuste se
    // resserrait sur le 95e centile d'une série oisive — quelques kilobits — et
    // l'axe graduait « 0.0 » cinq fois de suite, en signalant quinze mesures
    // hors cadre pour une pointe à 0,1 Mbit/s. La fenêtre d'un mégabit dit
    // qu'en deçà il n'y a rien à protéger.
    const debit = CELLULAR_METRICS.find((m) => m.key === 'rx_kbps')!
    // Un kbit/s de fond et une pointe à 100 kbit/s, exprimés en Mbit/s.
    const repos = [...Array(200).fill(0.001), 0.1]
    const framing = framedDomain(debit, repos)
    expect(framing.domain).toEqual([0, 1])
    expect(framing.above).toBe(0)
    expect(axisDigits(framing.domain)).toBe(1)
  })

  it('laisse un débit ordinaire tranquille', () => {
    // Contre-épreuve : sans elle, un cadre qui rognerait TOUJOURS passerait le
    // test ci-dessus. Une série qui monte franchement doit se voir en entier.
    const debit = CELLULAR_METRICS.find((m) => m.key === 'rx_kbps')!
    // Une montée régulière jusqu'à 10 Mbit/s, en Mbit/s.
    const montante = Array.from({ length: 200 }, (_, i) => i * 0.05)
    const framing = framedDomain(debit, montante)
    expect(framing.domain).toEqual([0, 9.950000000000001])
    expect(framing.above).toBe(0)
  })

  it('borne aussi par le bas', () => {
    // Un axe ne sait pas de quel côté tombe l'aberration : un effondrement isolé
    // écrase la bande utile exactement comme un pic.
    const framing = framedDomain(rsrp, [...Array(340).fill(-88), -160])
    expect(framing.domain).toEqual([-115, -75])
    expect(framing.below).toBe(1)
    expect(outOfFrameNote(framing, rsrp)).toContain('1 mesure sous le cadre, jusqu’à -160 dBm')
  })

  it('garde la fenêtre de la métrique comme minimum', () => {
    // Le bornage ne doit pas rendre le zoom que la fenêtre interdit : 2 dB
    // d'amplitude restent 2 dB dans un cadre de 40 dB.
    expect(framedDomain(rsrp, Array.from({ length: 340 }, (_, i) => -89 + (i % 3))).domain)
      .toEqual([-115, -75])
  })

  it('rend le même cadre que `verticalDomain`', () => {
    // Les deux ne doivent pas pouvoir diverger : `verticalDomain` n'est que la
    // porte étroite ouverte aux appelants qui n'ont que faire du hors-cadre.
    const values = trenteMinutes()
    expect(verticalDomain(latence, values)).toEqual(framedDomain(latence, values).domain)
  })
})

describe('les décimales de l’axe suivent son amplitude', () => {
  it('ne grave plus cinq fois la même graduation', () => {
    // Le nombre de décimales était plafonné à deux : un axe de 0 à 0,1 Mbit/s
    // graduait « 0.00 » cinq fois de suite — le même défaut que le palier
    // au-dessus corrigeait, simplement repoussé d'un facteur dix.
    expect(axisDigits([0, 50])).toBe(0)
    expect(axisDigits([0, 5])).toBe(1)
    expect(axisDigits([0, 0.5])).toBe(2)
    expect(axisDigits([0, 0.05])).toBe(3)
    expect(axisDigits([0, 0.005])).toBe(4)
  })

  it('s’arrête à cinq décimales et ne lève sur aucune amplitude', () => {
    // `toFixed` refuse au-delà de cent décimales, et au-delà de cinq ce n'est
    // plus un débit qu'on lit.
    expect(axisDigits([0, 1e-12])).toBe(5)
    expect(axisDigits([0, 0])).toBe(1)
    expect(axisDigits([5, 5])).toBe(1)
    expect(() => (0).toFixed(axisDigits([0, Number.POSITIVE_INFINITY]))).not.toThrow()
  })
})

describe('une série dans le désordre', () => {
  it('est remise en ordre plutôt que de perdre ses trous', () => {
    // Tout le raisonnement de `withGaps` repose sur des écarts POSITIFS : un
    // point hors d'ordre produit une médiane négative, donc `median <= 0`, donc
    // plus aucun trou dessiné — une coupure effacée en silence. Le stockage
    // ordonne aujourd'hui par `ts` et le cas n'est plus déclenchable ; la garde
    // reste parce qu'elle coûte une ligne.
    const desordre = [
      { t: 0, value: 1 },
      { t: 100, value: 4 },  // hors d'ordre, et séparé par un vrai trou
      { t: 10, value: 2 },
      { t: 20, value: 3 },
    ]
    const sortie = withGaps(desordre)
    const temps = sortie.map((p) => p.t)
    expect([...temps].sort((a, b) => a - b)).toEqual(temps)
    // Médiane des écarts = 10 s ; le saut de 20 à 100 la dépasse d'un facteur 8.
    expect(sortie.filter((p) => p.value === null)).toHaveLength(1)
  })

  it('ne modifie pas le tableau qu’on lui donne', () => {
    const source = [{ t: 10, value: 1 }, { t: 0, value: 2 }]
    withGaps(source)
    expect(source.map((p) => p.t)).toEqual([10, 0])
  })
})

describe('changements de technologie', () => {
  it('repère chaque changement de famille, et seulement ceux-là', () => {
    // Scénario 5 de la revue : 5G n78 à 28 dB, LTE B3 à 13 dB, dans une seule
    // courbe sans rien qui le dise — un changement de technologie lu comme une
    // chute de signal.
    const p = (s: number, rat: string | null) => ({ ts: new Date(Date.UTC(2026, 8, 24, 16, 12, s)).toISOString(), value: 1, rat })
    const vus = changementsDeTechno([
      p(0, '5G NSA'), p(5, '5G NSA'), p(10, null), p(15, 'LTE-A'), p(20, 'LTE'), p(25, '5G NSA'),
    ])
    expect(vus.map((c) => `${c.de}→${c.vers}`)).toEqual(['5G→LTE', 'LTE→5G'])
    expect(vus[0].t).toBe(Date.UTC(2026, 8, 24, 16, 12, 15))
    // Sans technologie (débit, tunnel) : aucun repère.
    expect(changementsDeTechno([p(0, null), p(5, null)])).toEqual([])
  })
})
