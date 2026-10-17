import { describe, expect, it } from 'vitest'

import {
  fmtClock, fmtId, fmtInt, fmtNumber, fmtPosition, fmtThroughput, fmtTime, fmtUptime,
} from './format'

/**
 * Ce fichier n'avait AUCUN test, et c'est lui qui écrit sur l'écran le dernier
 * mot de chaque grandeur. Les cas ci-dessous ont tous été reproduits sur
 * l'ancienne version avant d'être corrigés : aucun n'est hypothétique.
 */

describe('une valeur non finie n’est pas une valeur', () => {
  // `NaN` et les infinis naissent d'une division dont le dénominateur est
  // absent — exactement la situation que ce projet refuse de publier comme un
  // zéro. Les laisser traverser le formatage revenait à la publier comme un mot.
  it.each([
    ['fmtNumber', () => fmtNumber(Number.NaN, 1, 'ms'), '—'],
    ['fmtNumber +∞', () => fmtNumber(Number.POSITIVE_INFINITY, 1, 'ms'), '—'],
    ['fmtNumber −∞', () => fmtNumber(Number.NEGATIVE_INFINITY, 1, 'ms'), '—'],
    ['fmtInt NaN', () => fmtInt(Number.NaN), '—'],
    ['fmtInt ∞', () => fmtInt(Number.POSITIVE_INFINITY), '—'],
    ['fmtThroughput NaN', () => fmtThroughput(Number.NaN), '—'],
    ['fmtThroughput ∞', () => fmtThroughput(Number.POSITIVE_INFINITY), '—'],
    ['fmtUptime NaN', () => fmtUptime(Number.NaN), '—'],
  ])('%s rend un tiret', (_nom, appel, attendu) => {
    expect(appel()).toBe(attendu)
  })

  it('rend toujours le tiret sur une absence franche', () => {
    expect(fmtNumber(null)).toBe('—')
    expect(fmtInt(undefined)).toBe('—')
    expect(fmtThroughput(null)).toBe('—')
    expect(fmtUptime(undefined)).toBe('—')
  })

  it('n’altère pas une vraie mesure, zéro compris', () => {
    // Le garde ne doit pas avaler le zéro : zéro paquet perdu est une mesure.
    expect(fmtNumber(0, 2, '%')).toBe('0,00 %')
    expect(fmtInt(0)).toBe('0')
    expect(fmtThroughput(0)).toBe('0 kbit/s')
    expect(fmtUptime(0)).toBe('0 s')
    expect(fmtNumber(-88, 0, 'dBm')).toBe('-88 dBm')
  })
})

describe('fmtThroughput', () => {
  it('bascule en Mbit/s à partir de mille', () => {
    expect(fmtThroughput(999)).toBe('999 kbit/s')
    expect(fmtThroughput(1000)).toBe('1,0 Mbit/s')
    expect(fmtThroughput(1169)).toBe('1,2 Mbit/s')
  })
})

describe('fmtUptime', () => {
  it('arrondit sous la minute au lieu de publier la valeur brute', () => {
    // Rendait « 42.7 s » : un point décimal anglais et une précision que rien
    // d'autre à l'écran n'affiche, au milieu de durées entières.
    expect(fmtUptime(42.7)).toBe('43 s')
    expect(fmtUptime(59.4)).toBe('59 s')
  })

  it('refuse une durée négative', () => {
    // `EventLog` et `Overview` la calculent par différence de deux dates. Une
    // différence négative ne dit pas « moins que rien » : elle dit que les deux
    // horodatages ne viennent pas de la même horloge. « -7200 s » publiait ce
    // désaccord comme une mesure — et la garde `seconds < 60` l'y envoyait
    // droit, si bien que deux heures d'écart s'affichaient en secondes.
    expect(fmtUptime(-5)).toBe('—')
    expect(fmtUptime(-7200)).toBe('—')
  })

  it('compose les unités', () => {
    expect(fmtUptime(60)).toBe('1 min')
    expect(fmtUptime(8280)).toBe('2 h 18 min')
    expect(fmtUptime(90_000)).toBe('1 j 1 h')
  })
})

describe('fmtTime / fmtClock', () => {
  it('ne LÈVE jamais sur une valeur non datable', () => {
    // `HistoryChart` recevait de `recharts` un instant en millisecondes et le
    // repassait par `new Date(t).toISOString()` pour obéir à l'ancienne
    // signature. Cette conversion lève `RangeError` — à l'intérieur d'un rendu,
    // donc une seule graduation non numérique faisait tomber le graphe entier.
    expect(() => fmtTime('')).not.toThrow()
    expect(fmtTime('')).toBe('—')
    expect(fmtTime('pas une date')).toBe('—')
    expect(fmtClock(Number.NaN)).toBe('—')
  })

  it('accepte l’ISO, les millisecondes et un Date', () => {
    const iso = '2026-09-13T14:05:09Z'
    const ms = Date.parse(iso)
    // Même instant, trois écritures : les trois doivent rendre la même chose.
    expect(fmtTime(ms)).toBe(fmtTime(iso))
    expect(fmtTime(new Date(ms))).toBe(fmtTime(iso))
    expect(fmtClock(ms)).toBe(fmtClock(iso))
    // Les secondes distinguent les deux formats.
    expect(fmtTime(iso).length).toBeGreaterThan(fmtClock(iso).length)
  })
})

describe('fmtPosition', () => {
  it('rend null — et non un tiret — quand la position manque', () => {
    // L'appelant efface la ligne entière plutôt que d'afficher un champ vide.
    expect(fmtPosition(null, 2.2)).toBeNull()
    expect(fmtPosition(48.8, undefined)).toBeNull()
  })

  it('rend null sur une coordonnée non finie', () => {
    // Rendait « NaN, NaN » : une ligne de position pour un point que personne
    // n'a mesuré, là où l'absence devait effacer la ligne.
    expect(fmtPosition(Number.NaN, Number.NaN)).toBeNull()
    expect(fmtPosition(48.85641, Number.POSITIVE_INFINITY)).toBeNull()
  })

  it('s’arrête à cinq décimales, soit environ un mètre', () => {
    expect(fmtPosition(48.856412345, 2.352341111)).toBe('48.85641, 2.35234')
  })
})

describe('les deux seuils qui se lisaient de travers', () => {
  it('bascule en Mbit/s sur la valeur ARRONDIE', () => {
    // La comparaison se faisait avant l'arrondi : 999,6 restait sous le seuil,
    // puis `toFixed(0)` rendait « 1000 kbit/s » — une unité de trop, sur une
    // échelle qui n'existe pas.
    expect(fmtThroughput(999.6)).toBe('1,0 Mbit/s')
    expect(fmtThroughput(999.4)).toBe('999 kbit/s')
    expect(fmtThroughput(1000)).toBe('1,0 Mbit/s')
  })

  it('refuse l’île nulle', () => {
    // 0°/0° désigne un point du golfe de Guinée où aucun véhicule de reportage
    // ne stationnera : c'est la signature d'un récepteur sans fixation. Rendu
    // tel quel, il plaçait le véhicule à 5 000 km des côtes.
    expect(fmtPosition(0, 0)).toBeNull()
    // Un vrai zéro sur UN seul axe reste une position : l'équateur et le
    // méridien de Greenwich existent.
    expect(fmtPosition(0, 2.35234)).toBe('0.00000, 2.35234')
    expect(fmtPosition(48.85641, 0)).toBe('48.85641, 0.00000')
  })
})

describe('fmtId', () => {
  it('écrit un identifiant tel quel, sans séparateur de milliers', () => {
    // « 130 392 066 » ne se recolle plus à ce qu'affiche le routeur : un
    // numéro de cellule est un NOM, pas une quantité. Vu sur le MAX HD2.
    // Le backend les publie en chaînes : un nombre ne peut plus arriver ici,
    // le type le refuse. Ce qui reste à vérifier est que la chaîne passe telle
    // quelle, zéro de tête compris.
    expect(fmtId('130392066')).toBe('130392066')
    expect(fmtId('509344')).toBe('509344')
    expect(fmtId('01')).toBe('01')
    expect(fmtId('208-01')).toBe('208-01')
  })
  it('rend un cadratin sur une absence', () => {
    expect(fmtId(null)).toBe('—')
    expect(fmtId(undefined)).toBe('—')
    expect(fmtId('')).toBe('—')
  })
})

describe('la virgule décimale (A-8)', () => {
  it('écrit les décimales comme les entiers : à la française', () => {
    // « 0.2 dB » et « 87.7 Mo » côtoyaient « 1 428 o » sur la même page.
    expect(fmtNumber(0.2, 1, 'dB')).toBe('0,2 dB')
    expect(fmtNumber(1549.9, 1)).toBe('1\u202f549,9')
    expect(fmtNumber(-88, 1)).toBe('-88,0')
  })

  it('laisse le point aux coordonnées, où la virgule serait ambiguë', () => {
    expect(fmtPosition(48.85641, 2.35234)).toBe('48.85641, 2.35234')
  })
})
