import { useEffect, useState } from 'react'

import { type Fraicheur as Etat, phraseFraicheur } from '../lib/fraicheur'

/** Pas de l'horloge locale. Elle ne fait que décider QUAND la mention apparaît
 *  — une donnée qui franchit deux périodes sans réponse — et faire avancer le
 *  nombre une fois qu'elle est là ; cinq secondes suffisent aux deux. */
const PAS_MS = 5_000

/**
 * « Bande : dernière réponse il y a 75 s. » — rien tant que la partie est
 * fraîche.
 *
 * Discrète (le ton d'une note de pied) mais lisible : c'est une réserve sur ce
 * qui est affiché juste au-dessus, pas une alarme. `role="status"` : un lecteur
 * d'écran l'annonce sans interrompre.
 */
export function Fraicheur({ nom, etat }: { nom: string; etat: Etat }) {
  // L'horloge locale ne sert qu'à provoquer un rendu ; l'instant est relu à
  // chaque rendu, si bien qu'une réponse aboutie efface la mention aussitôt,
  // sans attendre le pas suivant.
  const [, battre] = useState(0)
  useEffect(() => {
    const t = window.setInterval(() => battre((n) => n + 1), PAS_MS)
    return () => window.clearInterval(t)
  }, [])
  const phrase = phraseFraicheur(nom, etat, performance.now())
  if (phrase === null) return null
  return <p className="disclaimer fraicheur" role="status">{phrase}</p>
}
