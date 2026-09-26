import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'

import { Notes } from './Note'

/** Toutes les sources de l'interface, lues telles quelles.
 *
 *  `import.meta.glob` est la lecture de fichiers de Vite : elle passe par le
 *  même resolver que le reste du build, là où `node:fs` demanderait des types
 *  Node que ce paquet frontal n'a pas. */
const SOURCES = import.meta.glob(['../**/*.tsx', '!../**/*.test.tsx'], {
  query: '?raw', import: 'default', eager: true,
}) as Record<string, string>

// Le glob exclut déjà les tests : on ne charge pas ce qu'on va filtrer.
const APPLICATIFS = Object.entries(SOURCES)

describe('le pied de notes, à l’échelle du dépôt', () => {
  it('lit bien les sources', () => {
    // Sans ce garde, le test suivant passerait sur une liste vide.
    expect(APPLICATIFS.length).toBeGreaterThan(10)
    expect(APPLICATIFS.some(([, t]) => t.includes('panel-note'))).toBe(true)
    expect(APPLICATIFS.some(([f]) => f.includes('.test.'))).toBe(false)
  })

  it('enveloppe CHAQUE note, composant ou balise, dans un conteneur ouvert', () => {
    // Le conteneur `Notes` est OPT-IN : tout son effet tient dans le sélecteur
    // parent `.panel-notes > .panel-note`. Rien n'oblige un point d'appel à
    // envelopper, et l'invariant était faux le jour même où il a été posé —
    // Alerts, HistoryChart et Discovery gardaient huit notes empilées pendant
    // que le test de la galerie, qui ne monte que deux panneaux, restait vert.
    //
    // Ce test regarde donc les SOURCES : aucun test ne peut monter les sept
    // composants à la fois, mais tous tiennent dans un glob.
    //
    // Une première version ne comparait que la PRÉSENCE d'une note à la
    // PRÉSENCE d'un conteneur dans le fichier, puis la première note à la
    // première ouverture — sans voir le composant `<Note>` ni la variante
    // `is-warn`. Une note nue posée après un `</Notes>` passait. Ici, pour
    // chaque occurrence, le nombre d'ouvertures moins fermetures AVANT elle
    // doit être strictement positif — ce qui contient l'ancien test.
    const NOTE = /<p className="panel-note[" ]|<Note[\s>]/g
    const fautifs: string[] = []
    for (const [f, t] of APPLICATIFS) {
      if (f.endsWith('/Note.tsx')) continue   // le fichier qui les définit
      for (const m of t.matchAll(NOTE)) {
        const avant = t.slice(0, m.index)
        const ouverts = (avant.match(/<Notes[\s>]/g) ?? []).length - (avant.match(/<\/Notes>/g) ?? []).length
        if (ouverts <= 0) { fautifs.push(`${f}@${m.index}`); break }
      }
    }
    expect(fautifs).toEqual([])
  })

  it('s’efface quand aucune de ses notes ne s’affiche', () => {
    const { container, unmount } = render(<Notes>{false}{null}{undefined}</Notes>)
    expect(container.querySelector('.panel-notes')).toBeNull()
    unmount()
  })
})
