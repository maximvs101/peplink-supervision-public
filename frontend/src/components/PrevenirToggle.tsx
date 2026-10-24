import { useEffect, useState } from 'react'

import { type ModePrevenir, modeMemorise, preparerSon, retenirMode } from '../lib/prevenir'

/** L'événement par lequel le choix rejoint le veilleur de l'écran ouvert. */
export const EVENEMENT_PREVENIR = 'peplink-prevenir'

const CHOIX: { valeur: ModePrevenir; libelle: string }[] = [
  { valeur: 'muet', libelle: 'Alertes : muet' },
  { valeur: 'notification', libelle: 'Notifier' },
  { valeur: 'son', libelle: 'Notifier + son' },
]

/** Muet · Notifier · + son (A-2). Un stationnement dure des heures : une alerte
 *  qui s'ouvre onglet caché ne se voit pas sans cela. */
export function PrevenirToggle() {
  const [mode, setMode] = useState<ModePrevenir>(modeMemorise)
  const [refus, setRefus] = useState(false)

  // Le son retenu se réarme au premier geste sur la page : un navigateur
  // n'accorde le son qu'après une interaction, et le contexte audio ne naissait
  // qu'au clic dans cette liste — après un rechargement, « + son » restait
  // affiché et ne sonnait plus (revue du 17/10).
  useEffect(() => {
    if (mode !== 'son') return
    const armer = () => {
      preparerSon()
      window.removeEventListener('pointerdown', armer, true)
      window.removeEventListener('keydown', armer, true)
    }
    window.addEventListener('pointerdown', armer, true)
    window.addEventListener('keydown', armer, true)
    return () => {
      window.removeEventListener('pointerdown', armer, true)
      window.removeEventListener('keydown', armer, true)
    }
  }, [mode])

  const choisir = async (valeur: ModePrevenir) => {
    if (valeur !== 'muet' && typeof Notification !== 'undefined' && Notification.permission === 'default') {
      try {
        await Notification.requestPermission()
      } catch {
        // Un navigateur ancien rend void : la permission se relit ci-dessous.
      }
    }
    const refusee = valeur !== 'muet'
      && (typeof Notification === 'undefined' || Notification.permission !== 'granted')
    setRefus(refusee)
    if (valeur === 'son') preparerSon()
    retenirMode(valeur)
    setMode(valeur)
    window.dispatchEvent(new CustomEvent(EVENEMENT_PREVENIR, { detail: valeur }))
  }

  // Une liste et non trois boutons : à côté du thème, trois boutons de plus
  // repoussaient le verdict sur une seconde rangée à 1 024 px.
  return (
    <>
      <select
        value={mode}
        aria-label="Prévenir d’une alerte"
        title={refus
          ? 'Notifications refusées par le navigateur.'
          : 'Notification du système à l’ouverture d’une alerte, onglet caché — retenu sur ce poste'}
        onChange={(e) => { void choisir(e.target.value as ModePrevenir) }}
      >
        {CHOIX.map((c) => <option key={c.valeur} value={c.valeur}>{c.libelle}</option>)}
      </select>
      {refus && <span className="tag is-off">notifications refusées</span>}
    </>
  )
}
