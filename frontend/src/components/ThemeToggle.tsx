import { useState } from 'react'

import { type ChoixTheme, appliquerTheme, themeMemorise } from '../lib/themeChoice'

const CHOIX: { valeur: ChoixTheme; libelle: string; aide: string }[] = [
  { valeur: 'auto', libelle: 'Auto', aide: 'Suivre le réglage clair ou sombre du système' },
  { valeur: 'light', libelle: 'Clair', aide: 'Thème clair, pour le plein jour — retenu sur ce poste' },
  { valeur: 'dark', libelle: 'Sombre', aide: 'Thème sombre — retenu sur ce poste' },
]

/** Auto · Clair · Sombre (R-0.1). En plein soleil, on veut forcer le clair sans
 *  aller dans les réglages du système. */
export function ThemeToggle() {
  const [choix, setChoix] = useState<ChoixTheme>(themeMemorise)
  return (
    <div className="segmented" role="group" aria-label="Thème de l’écran">
      {CHOIX.map((c) => (
        <button
          key={c.valeur}
          type="button"
          className={choix === c.valeur ? 'is-active' : ''}
          aria-pressed={choix === c.valeur}
          title={c.aide}
          onClick={() => { appliquerTheme(c.valeur); setChoix(c.valeur) }}
        >
          {c.libelle}
        </button>
      ))}
    </div>
  )
}
