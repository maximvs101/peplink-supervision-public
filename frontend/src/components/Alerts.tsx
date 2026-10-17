import { useState } from 'react'
import type { Alert } from '../types'
import { fmtClock, fmtUptime } from '../lib/format'
import { Notes } from './Note'

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

/** Ce que chaque genre veut dire, en clair, et ce qu'on va en faire.
 *
 *  Le genre vient du serveur sous forme de mot-clé ; l'écran le traduit ici et
 *  nulle part ailleurs. Un genre inconnu — le jour où une quatrième règle
 *  naîtra — s'affiche tel quel plutôt que de disparaître en silence. */
const LIBELLE: Record<string, { titre: (a: Alert) => string; suite: string }> = {
  'hors du bond': {
    titre: (a) => `Lien ${a.wan_id} hors du bond`,
    suite:
      'Ce lien est debout et ne porte plus rien. La cause entre parenthèses est ' +
      'le mot du routeur, pas une déduction.',
  },
  'liaison sur un seul lien': {
    titre: () => 'La liaison ne tient plus que sur un lien',
    suite:
      'L’agrégation ne protège plus de rien : la prochaine perte est une coupure. ' +
      'Cette règle ne s’est jamais déclenchée sur les relevés de référence — elle ' +
      'reste muette jusqu’au jour où elle compte.',
  },
  'liaison sans aucun lien': {
    // Le BOND, pas la liaison. Sur le MAX HD2 dont le concentrateur était
    // injoignable, ce titre disait « plus aucun lien ne porte la liaison »
    // sous un bandeau qui disait « portent le trafic en direct » — les deux
    // étaient vrais et se contredisaient, parce que le premier employait le
    // mot de l'autre. Le tunnel ne porte plus rien ; la liaison, peut-être si.
    titre: () => 'Plus aucun lien dans le bond',
    suite:
      'Tous les liens regardés sont hors du bond : le tunnel ne transporte plus rien, ' +
      'et l’agrégation ne protège plus de rien. Si le trafic sort en direct par les ' +
      'interfaces, la liaison tient — c’est le bandeau qui le dit, pas cette alerte.',
  },
  'connexions admin refusées': {
    titre: () => 'Connexions admin refusées',
    suite:
      'Le journal du routeur compte des refus de connexion à son interface. Cinq en cinq ' +
      'minutes suspendent l’accès admin pendant cinq minutes — la supervision comprise. ' +
      'Ils peuvent venir d’un autre poste : vérifier qui tente de se connecter.',
  },
  'horloge du routeur décalée': {
    // L'écart se mesure contre ce POSTE : le titre ne désigne pas le fautif,
    // le détail le fait quand tous les équipements s'accordent (C-32).
    titre: () => 'Horloges du routeur et du poste en désaccord',
    suite:
      'L’heure du routeur s’écarte de plus d’une minute de celle de ce poste : l’historique '
      + 'qu’il garde ne peut plus combler les trous, et les faits de son journal ne seraient '
      + 'plus rattachés aux coupures. Si tous les équipements montrent le même écart, c’est '
      + 'l’horloge du poste qui dérive ; sinon, un routeur démarré sans accès Internet n’a pas '
      + 'pu régler la sienne : vérifier son serveur de temps (NTP) dans son interface.',
  },
  'supervision interrompue': {
    titre: () => 'La supervision s’est interrompue',
    suite:
      'Pendant ce temps, le routeur a continué de fonctionner sans témoin : ce qui ' +
      's’y est passé n’est ni mesuré ni récupérable. La bande de la fenêtre montre ' +
      'le creux ; ceci le dit.',
  },
}

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
            et {closes.length} récemment {closes.length > 1 ? 'résolues' : 'résolue'}
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
<Notes>
      <p className="panel-note">
        Six règles, et six seulement. Les trois de la liaison — un lien hors du bond,
        des porteurs réduits à un ou à aucun — et l’interruption de la supervision ont
        été éprouvées sur quatorze heures et demie de relevés réels, où la plupart des
        candidates sont mortes : « des modems partagent une cellule » se serait
        déclenchée 99 % du temps, et « plus d’une seconde d’aller-retour » dix fois sans
        conséquence. Les connexions admin refusées et l’horloge décalée sont venues
        ensuite, de la revue d’exploitation. Une alerte qui parle tout le temps n’est
        pas une alerte, et elle emporte les autres avec elle.
      </p>
</Notes>
    </section>
  )
}

function Ligne({ alerte }: { alerte: Alert }) {
  const mots = LIBELLE[alerte.kind]
  const debut = new Date(alerte.since).getTime()
  const fin = alerte.until === null ? Date.now() : new Date(alerte.until).getTime()
  const duree = Math.max(0, Math.round((fin - debut) / 1000))
  const close = alerte.until !== null

  return (
    <li className={close ? 'alerte is-close' : 'alerte'}>
      <b>
        {mots ? mots.titre(alerte) : alerte.kind}
        {alerte.detail !== null && <span className="tag is-mono"> {alerte.detail}</span>}
      </b>
      <span className="alerte-quand">
        {close
          ? `de ${fmtClock(alerte.since)} à ${fmtClock(alerte.until!)} · ${fmtUptime(duree)}`
          : `depuis ${fmtClock(alerte.since)} · ${fmtUptime(duree)}`}
      </span>
      {mots && <span className="alerte-suite">{mots.suite}</span>}
      {/* Le seuil de durée voyage avec l'alerte. Sans lui, « lien 4 hors du
          bond » ne dit pas si l'outil a attendu une seconde ou une minute avant
          de le croire — et c'est cette attente qui fait la différence entre un
          fait et un clignotement. */}
      <span className="alerte-seuil">
        signalée après {fmtUptime(alerte.sustained_s)} de la même condition
      </span>
    </li>
  )
}
