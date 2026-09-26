/**
 * Le thème de l'écran (R-0.1) : il suit le réglage du système, sauf choix
 * contraire sur ce poste. Le choix se pose en `data-theme` sur la racine, que
 * la feuille de style lit ; « auto » le retire.
 *
 * Retenu dans le stockage du navigateur — une commodité de ce poste, pas une
 * donnée : un stockage refusé (navigation privée, stockage bloqué) ne casse
 * rien, le thème suit alors le système.
 */

export type ChoixTheme = 'auto' | 'light' | 'dark'

const CLE = 'theme'

export function themeMemorise(): ChoixTheme {
  try {
    const v = localStorage.getItem(CLE)
    return v === 'light' || v === 'dark' ? v : 'auto'
  } catch {
    return 'auto'
  }
}

export function appliquerTheme(choix: ChoixTheme): void {
  if (choix === 'auto') delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme = choix
  try {
    if (choix === 'auto') localStorage.removeItem(CLE)
    else localStorage.setItem(CLE, choix)
  } catch {
    // Stockage refusé : le choix vaut pour cette page seulement.
  }
}

/** Au démarrage, AVANT le premier rendu : sans cela, l'écran s'affichait un
 *  instant dans le thème du système avant de basculer. */
export function appliquerThemeMemorise(): void {
  const choix = themeMemorise()
  if (choix === 'auto') delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme = choix
}
