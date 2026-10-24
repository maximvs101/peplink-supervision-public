import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { reauthDevice } from '../api/client'

/** Au-delà, un refus toujours affiché est celui de la tentative : plus de trois
 *  relectures de l'état par l'écran, qui en fait une toutes les dix secondes
 *  (`REFRESH_MS` de `DeviceShell`). */
export const VERDICT_REPRISE_MS = 35_000

/** Où en est la reprise demandée par l'exploitant. */
type Tentative =
  | { etat: 'aucune' }
  | { etat: 'envoi' }
  /** Autorisée par le serveur : elle aura lieu au prochain cycle. */
  | { etat: 'attendue' }
  | { etat: 'refusee' }
  | { etat: 'erreur'; message: string }

/**
 * Sans ce bloc, un équipement dont les identifiants ont été refusés l'était
 * jusqu'au redémarrage du serveur : la supervision l'écarte entièrement — cinq
 * échecs de connexion suspendent l'accès au routeur pendant cinq minutes — et
 * la route de reprise n'avait aucun appelant.
 *
 * Et une reprise ratée ne doit pas se présenter comme une reprise en attente
 * (C-21) : le message « aura lieu au prochain cycle » restait affiché après
 * l'échec, le bouton restait actif, et chaque clic rejouait les MÊMES
 * identifiants — un échec de plus vers la suspension. Le bouton est désactivé
 * pendant la tentative ; son échec est dit, avec le moyen de corriger.
 */
export function RepriseIdentifiants({ routerId, status }: { routerId: string; status: string | null }) {
  const [tentative, setTentative] = useState<Tentative>({ etat: 'aucune' })
  const precedent = useRef(status)

  useEffect(() => {
    // Revenu au refus APRÈS être passé ailleurs : la tentative a eu lieu, et
    // elle a échoué.
    if (status === 'identifiants refusés' && precedent.current !== status && tentative.etat === 'attendue') {
      setTentative({ etat: 'refusee' })
    }
    precedent.current = status
  }, [status, tentative.etat])

  // L'écran ne relit l'état de l'équipement que toutes les dix secondes :
  // le passage par « en attente » peut durer un seul cycle et lui échapper.
  // Toujours refusé passé ce délai, la tentative a eu lieu et a échoué.
  useEffect(() => {
    if (tentative.etat !== 'attendue') return
    const t = window.setTimeout(() => {
      if (precedent.current === 'identifiants refusés') setTentative({ etat: 'refusee' })
    }, VERDICT_REPRISE_MS)
    return () => window.clearTimeout(t)
  }, [tentative.etat])

  if (status !== 'identifiants refusés') return null
  const enCours = tentative.etat === 'envoi' || tentative.etat === 'attendue'

  return (
    <div className="notice is-error" role="alert">
      <strong>Supervision arrêtée sur cet équipement.</strong> Aucune nouvelle
      tentative n’est faite d’elle-même : cinq échecs de connexion suspendent
      l’accès au routeur pendant cinq minutes, y compris depuis son interface web.
      {tentative.etat === 'refusee' ? (
        <>
          {' '}<b>La nouvelle tentative a été refusée</b> : les identifiants en mémoire
          sont toujours faux, et la rejouer consommerait un échec de plus. Corrigez-les
          en ré-appairant l’équipement depuis la <Link to="/decouverte">découverte</Link>.{' '}
        </>
      ) : (
        <> Corrigez les identifiants, puis relancez une tentative — une seule.{' '}</>
      )}
      <button
        type="button"
        className="is-quiet"
        disabled={enCours}
        onClick={() => {
          setTentative({ etat: 'envoi' })
          reauthDevice(routerId)
            .then(() => setTentative({ etat: 'attendue' }))
            .catch((err) => setTentative({
              etat: 'erreur', message: err instanceof Error ? err.message : 'Échec',
            }))
        }}
      >
        Réessayer la connexion
      </button>
      {tentative.etat === 'attendue' && <> Nouvelle tentative autorisée : elle aura lieu au prochain cycle.</>}
      {tentative.etat === 'erreur' && <> {tentative.message}</>}
    </div>
  )
}
