import { describe, expect, it } from 'vitest'

import {
  QUALITY_COLOR, QUALITY_THRESHOLDS, couleurDuLien, linkQuality, overallQuality,
  rsrpQuality, rsrqQuality, sinrQuality, wifiQuality,
} from './quality'

describe('seuils radio', () => {
  it.each([
    [-80, 'excellent'], [-85, 'excellent'], [-90, 'bon'],
    [-100, 'moyen'], [-110, 'faible'], [null, 'inconnu'],
  ])('classe un RSRP de %s en %s', (value, expected) => {
    expect(rsrpQuality(value as number | null)).toBe(expected)
  })

  it('utilise des seuils Wi-Fi distincts du cellulaire', () => {
    // -60 dBm est un bon signal Wi-Fi, ce serait un excellent RSRP.
    expect(wifiQuality(-60)).toBe('bon')
    expect(rsrpQuality(-60)).toBe('excellent')
  })
})

describe('qualité globale', () => {
  it('retient le maillon le plus faible', () => {
    expect(overallQuality(-80, -9, 2)).toBe('faible')
  })

  it('ignore les grandeurs non mesurées', () => {
    expect(overallQuality(-80, null, null)).toBe('excellent')
  })

  it('reste inconnue quand rien n’est mesuré', () => {
    expect(overallQuality(null, null, null)).toBe('inconnu')
  })
})

// Le contraste des couleurs de qualité se juge dans `theme.test.ts`, sur les
// jetons de la feuille de style et pour les DEUX thèmes (R-0.1). Il se jugeait
// ici sur un fond (#131c2e) que l'interface n'emploie plus.

describe('les seuils publiés SONT ceux qui jugent', () => {
  it('n’existent qu’en un seul exemplaire', () => {
    // Ils vivaient en double — une fois dans les appels à `grade`, une fois dans
    // la table publiée — sans rien qui les relie, et la docstring jurait qu'ils
    // n'étaient « recopiés nulle part ailleurs ». Changer un seuil de jugement
    // laissait l'écran publier l'ancien.
    for (const { key, bornes } of QUALITY_THRESHOLDS) {
      const note = { SINR: sinrQuality, RSRP: rsrpQuality, RSRQ: rsrqQuality,
                     'Wi-Fi': wifiQuality }[key]!
      const [excellent, bon, moyen] = bornes
      expect(note(excellent)).toBe('excellent')
      expect(note(bon)).toBe('bon')
      expect(note(moyen)).toBe('moyen')
      // Juste en dessous de la plus basse : la note tombe.
      expect(note(moyen - 0.1)).toBe('faible')
    }
  })

  it('publie aussi le seuil du Wi-Fi, qui était appliqué sans être dit', () => {
    expect(QUALITY_THRESHOLDS.map((s) => s.key)).toContain('Wi-Fi')
  })
})

describe('la note d’un lien, en un seul exemplaire', () => {
  const wan = (over: Partial<Parameters<typeof linkQuality>[0]> = {}) =>
    ({ kind: 'cellular', rsrp: null, rsrq: null, sinr: null, rssi: null, ...over })

  it('ne décerne aucune note à un lien filaire', () => {
    // La règle vivait en QUATRE exemplaires — `WanCard`, `LinkTable`, `Band`,
    // `BondPlan` — et toutes notaient un filaire « excellent ». C'est une mesure
    // inventée : une fibre n'a ni RSRP, ni SINR, ni rien à noter. `verdict.ts`
    // l'écrit en toutes lettres avant d'exclure les filaires de son compte des
    // liens faibles. Décerner la meilleure note à une absence de mesure est le
    // pendant exact du zéro pour une absence que ce projet refuse ailleurs.
    expect(linkQuality(wan({ kind: 'ethernet' }))).toBe('inconnu')
    // Et l'absence de note ne dépend d'aucun niveau résiduel.
    expect(linkQuality(wan({ kind: 'ethernet', rsrp: -60, sinr: 25 }))).toBe('inconnu')
  })

  it('note un lien Wi-Fi à SES seuils, partout', () => {
    // `Band` rendait « inconnu » pour un lien Wi-Fi là où les trois autres
    // appelaient `wifiQuality` : le même lien portait deux notes selon l'écran.
    expect(linkQuality(wan({ kind: 'wifi', rssi: -50 }))).toBe('excellent')
    expect(linkQuality(wan({ kind: 'wifi', rssi: -80 }))).toBe('faible')
    expect(linkQuality(wan({ kind: 'wifi', rssi: null }))).toBe('inconnu')
  })

  it('retient le maillon le plus faible sur un modem', () => {
    expect(linkQuality(wan({ rsrp: -80, rsrq: -9, sinr: 22 }))).toBe('excellent')
    // RSRP excellent, SINR moyen : c'est le signe d'une interférence.
    expect(linkQuality(wan({ rsrp: -80, rsrq: -9, sinr: 8 }))).toBe('moyen')
  })
})

describe('une valeur non finie n’est pas une note', () => {
  it('ne décerne « excellent » à aucun infini', () => {
    // `grade` ne testait que `Number.isNaN` : un ±Infinity — né d'une division
    // dont le dénominateur manquait — traversait la garde et ressortait noté.
    expect(sinrQuality(Number.POSITIVE_INFINITY)).toBe('inconnu')
    expect(rsrpQuality(Number.NEGATIVE_INFINITY)).toBe('inconnu')
    expect(rsrqQuality(Number.NaN)).toBe('inconnu')
    expect(overallQuality(Number.POSITIVE_INFINITY, -9, 22)).toBe('excellent')
  })
})

describe('un lien désactivé n’est pas une panne (C-20)', () => {
  it('prend la teinte de l’inconnu, pas le rouge', () => {
    // La couleur ne dépendait que de `connected` : un WAN désactivé par
    // l'exploitant se traçait en rouge sur le plan, la table et la carte, et
    // l'on cherchait une panne qui n'existait pas.
    const base = { kind: 'cellular', rsrp: null, sinr: null, rsrq: null, rssi: null, signal_bars: null }
    expect(couleurDuLien({ ...base, connected: false, disabled: true } as never)).toBe(QUALITY_COLOR.inconnu)
    expect(couleurDuLien({ ...base, connected: false, disabled: false } as never)).toBe(QUALITY_COLOR.faible)
  })
})
