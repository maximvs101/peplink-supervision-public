import { useState } from 'react'
import type { Alert } from '../types'
import { fmtClock, fmtUptime } from '../lib/format'
import { titreAlerte } from '../lib/alertes'

/**
 * Ce qui demande une décision, et qui n'est pas passé tout seul.
 *
 * Ni le bandeau ni le journal, et c'est la raison d'être de ce panneau. Le
 * bandeau dit l'état de l'instant, lissé : il retombe au vert dès que la
 * condition cesse. Le journal dit tout ce qui s'est passé, listé par ordre
 * d'importance : il faut le lire. Une alerte se place entre les deux — elle a
 * une DURÉE, et elle survit un moment à sa propre disparition, pour que
 * quelqu'un qui s'est éloigné deux minutes l'apprenne en revenant.
 *
 * Le panneau n'existe PAS quand il n'y a rien à dire. Un cadre vide intitulé
 * « Alertes » est un cadre qu'on cesse de regarder, et il emporte avec lui les
 * fois où il aurait compté — c'est le papier peint que tout cet outil refuse.
 */


/** Une alerte, telle qu'on la reconnaît d'un relevé à l'autre. */
const cle = (a: Alert) => `${a.kind}|${a.wan_id ?? ''}|${a.since}`

/** Les alertes résolues que l'exploitant a masquées, pour la session du
 *  navigateur : un retour depuis le détail d'un lien remonte ce panneau, et
 *  elles ne doivent pas revenir pour autant. Une commodité de ce poste, pas une
 *  donnée — un stockage refusé ne casse rien, le choix vaut pour la page. */
const STOCKAGE = 'alertes-masquees'

function masqueesMemorisees(): Set<string> {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(STOCKAGE) ?? '[]') as string[])
  } catch {
    return new Set()
  }
}

export function Alerts({ alerts }: { alerts: Alert[] }) {
  const [masquees, setMasquees] = useState(masqueesMemorisees)

  // Seules les RÉSOLUES se masquent : une alerte en cours demande une décision,
  // et la cacher ferait de ce panneau le papier peint qu'il refuse d'être. Une
  // alerte nouvelle, résolue ou non, n'a pas été vue : elle se montre.
  const visibles = alerts.filter((a) => a.until === null || !masquees.has(cle(a)))
  if (visibles.length === 0) return null

  const encours = visibles.filter((a) => a.until === null)
  const closes = visibles.filter((a) => a.until !== null)

  const masquer = () => {
    const suite = new Set(masquees)
    for (const a of closes) suite.add(cle(a))
    setMasquees(suite)
    try {
      sessionStorage.setItem(STOCKAGE, JSON.stringify([...suite]))
    } catch {
      // Stockage refusé : masquées pour cette page seulement.
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>
          {encours.length > 0
            ? `${encours.length} alerte${encours.length > 1 ? 's' : ''}`
            : 'Alertes récentes'}
        </h2>
        {closes.length > 0 && encours.length > 0 && (
          <span className="note">
            + {closes.length} {closes.length > 1 ? 'résolues' : 'résolue'}
          </span>
        )}
        {closes.length > 0 && (
          <>
            <span className="spacer" />
            <button type="button" className="is-quiet" onClick={masquer}>
              {encours.length > 0 ? 'Masquer les résolues' : 'Masquer'}
            </button>
          </>
        )}
      </div>
      <div className="panel-body">
        <ul className="alertes">
          {visibles.map((a) => (
            <Ligne key={cle(a)} alerte={a} />
          ))}
        </ul>
      </div>
    </section>
  )
}

function Ligne({ alerte }: { alerte: Alert }) {
  const debut = new Date(alerte.since).getTime()
  const fin = alerte.until === null ? Date.now() : new Date(alerte.until).getTime()
  const duree = Math.max(0, Math.round((fin - debut) / 1000))
  const close = alerte.until !== null

  return (
    <li className={close ? 'alerte is-close' : 'alerte'}>
      <b>
        {titreAlerte(alerte)}
        {alerte.detail !== null && <span className="tag is-mono"> {alerte.detail}</span>}
      </b>
      <span className="alerte-quand">
        {close
          ? `${fmtClock(alerte.since)} → ${fmtClock(alerte.until!)} · ${fmtUptime(duree)}`
          : `depuis ${fmtClock(alerte.since)} · ${fmtUptime(duree)}`}
      </span>
      {/* Le seuil de durée voyage avec l'alerte. Sans lui, « lien 4 hors du
          bond » ne dit pas si l'outil a attendu une seconde ou une minute avant
          de le croire — et c'est cette attente qui fait la différence entre un
          fait et un clignotement. */}
      <span className="alerte-seuil">
        seuil {fmtUptime(alerte.sustained_s)}
      </span>
    </li>
  )
}
