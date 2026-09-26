import { useLayoutEffect, useState } from 'react'

/**
 * Mesure un élément du DOM (taille utile pour les graphiques).
 *
 * Retourne une **ref de rappel** : la mesure démarre au moment où l'élément entre
 * réellement dans le DOM. C'est indispensable ici, car le graphique n'est monté qu'une
 * fois les données chargées — une ref classique mesurerait un élément encore absent.
 *
 * La mesure se fait dans un `useLayoutEffect` (lecture synchrone qui force le calcul de
 * la mise en page), puis est reprise à chaque événement susceptible de la changer. On ne
 * peut se fier à aucun mécanisme isolé :
 *  - le `ResizeObserver` de certains navigateurs ne déclenche pas son rappel initial ;
 *  - dans un onglet en arrière-plan, la mise en page peut être différée (largeur lue à 0)
 *    et `requestAnimationFrame` ne s'exécute pas — d'où des re-tentatives par `setTimeout`
 *    et une re-mesure au retour de visibilité.
 */
export function useElementSize<T extends HTMLElement>() {
  const [node, setNode] = useState<T | null>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  useLayoutEffect(() => {
    if (!node) return

    let cancelled = false
    // UNE minuterie en vol, pas une liste qui croît. `retry` n'en programme
    // jamais qu'une à la fois, mais chaque identifiant était empilé sans
    // jamais être retiré : sur un panneau replié — onglet en arrière-plan,
    // fenêtre réduite — le tableau enflait d'une entrée toutes les quatre
    // secondes, soit près de neuf cents par heure, pour des minuteries déjà
    // éteintes.
    let timer: number | undefined

    const measure = (): number => {
      if (cancelled) return 0
      const { width, height } = node.getBoundingClientRect()
      setSize((previous) =>
        previous.width === width && previous.height === height
          ? previous
          : { width, height },
      )
      return width
    }

    // Largeur nulle = mise en page pas encore calculée. On retente tant qu'elle
    // le reste, avec un intervalle croissant, et on s'arrête à la première
    // mesure utile.
    //
    // Une liste fixe de retentatives ne suffit pas : si elles expirent toutes
    // pendant que l'élément est encore replié — onglet en arrière-plan, panneau
    // masqué, fenêtre réduite — plus rien ne relance la mesure, et le graphe
    // reste vide indéfiniment. Mesuré : dans un contexte où le `ResizeObserver`
    // n'émet aucun rappel, seul un événement `resize` réveillait l'affichage.
    let delay = 50
    const retry = () => {
      if (cancelled || measure() > 0) return
      delay = Math.min(delay * 2, 4000)
      timer = window.setTimeout(retry, delay)
    }
    retry()

    const observer = new ResizeObserver(() => measure())
    observer.observe(node)
    window.addEventListener('resize', measure)
    // Un onglet qui revient au premier plan retrouve sa mise en page : c'est le
    // moment où une largeur restée nulle devient enfin mesurable.
    document.addEventListener('visibilitychange', measure)
    window.addEventListener('pageshow', measure)

    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
      observer.disconnect()
      window.removeEventListener('resize', measure)
      document.removeEventListener('visibilitychange', measure)
      window.removeEventListener('pageshow', measure)
    }
  }, [node])

  return [setNode, size] as const
}
