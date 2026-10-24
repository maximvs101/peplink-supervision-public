import { describe, expect, it } from 'vitest'

import { QUALITY_COLOR } from './quality'

/**
 * R-0.1 : deux thèmes, et une règle de contraste tenue pour les DEUX.
 *
 * La palette sombre notait ses contrastes en commentaire ; la claire les
 * recalcule sur fond blanc — le vert de qualité sombre y ferait 2:1. Ce test
 * lit les jetons dans la feuille de style elle-même : une couleur changée sans
 * refaire le calcul le fait échouer.
 */

// Lue par le resolver de Vite, comme le garde du pied de notes lit ses sources :
// un import `?raw` d'une feuille de style rend une chaîne vide sous Vitest.
//
// Fins de ligne ramenées à LF : le runner Windows de l'action extrait le dépôt
// en CRLF, et « @media print {\n  :root » y était introuvable — l'exécutable
// Windows ne se construisait plus, les deux Mac oui.
const css = (Object.values(
  import.meta.glob('../index.css', { query: '?raw', import: 'default', eager: true }),
)[0] as string).replace(/\r\n/g, '\n')

function bloc(selecteur: string): Record<string, string> {
  const debut = css.indexOf(selecteur)
  if (debut < 0) throw new Error(`bloc introuvable : ${selecteur}`)
  const ouvre = css.indexOf('{', debut)
  const ferme = css.indexOf('}', ouvre)
  const jetons: Record<string, string> = {}
  for (const m of css.slice(ouvre + 1, ferme).matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})/g)) {
    jetons[m[1]] = m[2]
  }
  return jetons
}

const luminance = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const contraste = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

const SOMBRE = bloc(':root {')
const CLAIR = { ...SOMBRE, ...bloc(':root[data-theme="light"]') }
const FONDS = ['ground', 'sunk', 'panel', 'panel-2']
const TEXTES = ['ink', 'ink-2', 'ink-3', 'q-excellent', 'q-bon', 'q-moyen', 'q-faible', 'q-inconnu']

describe.each([['sombre', SOMBRE], ['clair', CLAIR]])('le thème %s', (_nom, jetons) => {
  it.each(TEXTES)('écrit %s lisiblement sur chaque fond (≥ 4,5:1)', (texte) => {
    for (const fond of FONDS) {
      expect(contraste(jetons[texte], jetons[fond]), `${texte} sur ${fond}`).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('garde le bond visible sur un panneau (≥ 3:1), et le bouton principal lisible au survol', () => {
    expect(contraste(jetons.bond, jetons.panel)).toBeGreaterThanOrEqual(3)
    expect(contraste(jetons.ground, jetons['ink-hover'])).toBeGreaterThanOrEqual(4.5)
  })
})

describe('les deux chemins vers le clair', () => {
  it('portent les mêmes jetons : le choix manuel et le système ne divergent pas', () => {
    const systeme = bloc(':root:not([data-theme="dark"])')
    expect(systeme).toEqual(bloc(':root[data-theme="light"]'))
  })

  it('et l’impression reprend la palette claire', () => {
    const impression = bloc('@media print {\n  :root')
    expect(impression).toEqual(bloc(':root[data-theme="light"]'))
  })
})

describe('la palette de qualité du code', () => {
  it('ne porte plus de couleur : elle nomme les jetons des deux thèmes', () => {
    // Elle vivait en hexadécimal dans le code, calibrée pour le seul fond
    // sombre (R-0.1) ; sa lisibilité se juge désormais ci-dessus, par thème.
    for (const [qualite, valeur] of Object.entries(QUALITY_COLOR)) {
      expect(valeur).toBe(`var(--q-${qualite})`)
      expect(SOMBRE[`q-${qualite}`]).toMatch(/^#/)
      expect(CLAIR[`q-${qualite}`]).toMatch(/^#/)
    }
  })
})

/** Les avis n'ont qu'une convention de modificateurs : `is-warn` / `is-error`.
 *  `notice warn` et `notice error` coexistaient, deux règles CSS pour la même
 *  chose — et un avis écrit avec l'un des deux noms perdait sa couleur le
 *  jour où l'on aurait retiré l'autre. */
describe('modificateurs des avis', () => {
  const sources = import.meta.glob(['../**/*.tsx', '!../**/*.test.tsx'], {
    query: '?raw', import: 'default', eager: true,
  }) as Record<string, string>

  it('la feuille ne garde que les noms en is-', () => {
    expect(css).not.toMatch(/\.notice\.(warn|error)\b/)
  })

  it('aucun écran n’emploie les anciens noms', () => {
    const fautifs = Object.entries(sources)
      .filter(([, texte]) => /notice (warn|error)\b|'(warn|error)' : '(warn|error)'/.test(texte))
      .map(([chemin]) => chemin)
    expect(fautifs).toEqual([])
  })

  it('un avis d’erreur est annoncé (role="alert")', () => {
    // Aucun avis n'avait de rôle : « Impossible de charger l'équipement »,
    // « Flux temps réel refermé » apparaissaient sans qu'un lecteur d'écran
    // en dise rien. Un avis d'erreur est un événement, il s'annonce.
    const muets = Object.entries(sources).flatMap(([chemin, texte]) =>
      texte.split('\n')
        .filter((ligne) => /className=.*notice[^"`]*is-error/.test(ligne) && !/role=/.test(ligne))
        .map((ligne) => `${chemin} : ${ligne.trim()}`))
    expect(muets).toEqual([])
  })
})
