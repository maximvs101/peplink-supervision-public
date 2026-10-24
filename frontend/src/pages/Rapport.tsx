import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts'

import { fetchEvents, fetchHistory, fetchRouter, fetchSessionSummary } from '../api/client'
import { EventLog } from '../components/EventLog'
import { SessionSheet } from '../components/SessionSheet'
import { withGaps } from '../lib/chart'
import { etiquetteSupport, fmtClock } from '../lib/format'
import type { EventLog as Log, HistorySeries, RouterSummary, SessionLink, SessionSummary } from '../types'

/**
 * Le rapport d'une session, à emporter (revue, R-3.7).
 *
 * Pour prouver une intervention à un tiers — un client, un opérateur —, il
 * fallait des captures d'écran. Cette page rassemble la fiche, le journal et
 * une courbe par lien sur toute la session, mise en forme pour l'impression :
 * le PDF sort de l'impression du navigateur, sans bibliothèque de plus.
 *
 * « À partager » par défaut : l'équipement par son modèle, sans son nom ni sa
 * série ; aucune position (A-12) ; aucun SSID. `?complet=1` rend le
 * reste, pour un usage interne. Les règles de lecture sont celles de l'écran :
 * « — » est une absence, un taux voyage avec son compte.
 */

type Courbe = { wan: SessionLink; metric: 'rsrp' | 'path_latency_ms'; serie: HistorySeries | null }

const LIBELLES = { rsrp: 'RSRP, dBm', path_latency_ms: 'Latence du lien, ms' } as const

export function Rapport() {
  const { sessionId } = useParams()
  const [recherche] = useSearchParams()
  const complet = recherche.get('complet') === '1'
  const id = Number(sessionId)

  const [fiche, setFiche] = useState<SessionSummary | null>(null)
  const [routeur, setRouteur] = useState<RouterSummary | null>(null)
  const [journal, setJournal] = useState<Log | null>(null)
  const [courbes, setCourbes] = useState<Courbe[]>([])
  const [erreur, setErreur] = useState<string | null>(null)
  // Un échec de lecture se dit comme tel (C-22) : avalé, il s'imprimait en
  // « Aucune mesure sur la session » ou en relecture sans fin, dans un PDF
  // remis à un tiers. Et l'impression attend la fin de toutes les lectures.
  const [journalIllisible, setJournalIllisible] = useState(false)
  const [routeurLu, setRouteurLu] = useState(false)
  const [journalLu, setJournalLu] = useState(false)
  const [courbesLues, setCourbesLues] = useState(false)

  useEffect(() => {
    let vivant = true
    fetchSessionSummary(id)
      .then(async (f) => {
        if (!vivant) return
        setFiche(f)
        const fenetre = { since: f.session.started_at, until: f.session.last_seen }
        // L'équipement ne sert qu'à le nommer : son échec retombe sur
        // « Équipement », qui ne prétend rien.
        fetchRouter(f.session.router_id)
          .then((r) => vivant && setRouteur(r))
          .catch(() => {})
          .finally(() => vivant && setRouteurLu(true))
        fetchEvents(f.session.router_id, fenetre)
          .then((j) => vivant && setJournal(j))
          .catch(() => vivant && setJournalIllisible(true))
          .finally(() => vivant && setJournalLu(true))
        // Une courbe par lien et par grandeur : la radio pour un modem, la
        // latence du lien pour tous.
        const voulues: Omit<Courbe, 'serie'>[] = f.links.flatMap((wan) => [
          ...(wan.kind === 'cellular' ? [{ wan, metric: 'rsrp' as const }] : []),
          { wan, metric: 'path_latency_ms' as const },
        ])
        const lues = await Promise.all(voulues.map((c) =>
          fetchHistory(f.session.router_id, c.wan.wan_id, c.metric, 30, fenetre)
            .then((serie) => ({ ...c, serie }))
            .catch(() => ({ ...c, serie: null })),
        ))
        if (vivant) { setCourbes(lues); setCourbesLues(true) }
      })
      .catch((err) => vivant && setErreur(err instanceof Error ? err.message : 'Session introuvable'))
    return () => { vivant = false }
  }, [id])

  // Le Wi-Fi porte son SSID dans les messages du routeur : à partager, le
  // journal les tait pour ces liens.
  const journalRendu = useMemo(() => {
    if (journal === null || complet) return journal
    return {
      ...journal,
      episodes: journal.episodes.map((e) => (e.link_kind === 'wifi' ? { ...e, detail: null } : e)),
    }
  }, [journal, complet])

  if (erreur) return <div className="rapport"><p className="notice is-error" role="alert">{erreur}</p></div>
  if (fiche === null) return <div className="rapport"><p className="disclaimer">Lecture de la session…</p></div>

  const s = fiche.session
  const appareil = routeur?.device ?? null
  const duree = Math.round((Date.parse(s.last_seen) - Date.parse(s.started_at)) / 60000)
  const suffixe = complet ? '?complet=true' : ''

  return (
    <div className="rapport">
      <nav className="rapport-actions no-print">
        <button type="button" onClick={() => window.print()}
          disabled={!(routeurLu && journalLu && courbesLues)}
          title={routeurLu && journalLu && courbesLues ? undefined : 'Lecture de la session en cours…'}>
          Imprimer / enregistrer en PDF
        </button>
        <a href={`/api/sessions/${id}/export.csv${suffixe}`} download>Relevés (CSV)</a>
        <a href={`/api/sessions/${id}/export-routeur.csv`} download>Heure du routeur (CSV)</a>
        <Link to={complet ? `/rapport/${id}` : `/rapport/${id}?complet=1`}>
          {complet ? 'Version à partager' : 'Version complète (usage interne)'}
        </Link>
      </nav>

      <header className="rapport-tete">
        <h1>Rapport de session</h1>
        <p>
          {/* Le libellé nomme souvent le lieu : la version à partager le tait (C-38). */}
          {complet && s.label && <><b>{s.label}</b> · </>}
          du {new Date(s.started_at).toLocaleString('fr-FR')} au{' '}
          {new Date(s.last_seen).toLocaleString('fr-FR')} · {duree} min
        </p>
        <p>
          {complet && (appareil?.name ?? routeur?.name) && <><b>{appareil?.name ?? routeur?.name}</b> · </>}
          {appareil?.model ?? 'Équipement'}
          {appareil?.firmware && <> · firmware {appareil.firmware}</>}
          {/* Série non publiée (client non local) : « — », pas une omission. */}
          {complet && appareil && <> · série {appareil.serial ?? '—'}</>}
        </p>
        {!complet && (
          <p className="disclaimer">
            Version à partager : équipement désigné par son modèle ; position, nom de session
            et noms Wi-Fi retirés.
          </p>
        )}
      </header>

      <section className="rapport-section">
        <h2>Synthèse</h2>
        {/* Dans son conteneur de fiche : sans lui, la table perdait ses styles —
            colonnes serrées, chiffres collés, « WAN 1 » coupé (A-10). */}
        <div className="session-sheet">
          <SessionSheet fiche={fiche} partager={!complet} />
        </div>
      </section>

      <section className="rapport-section">
        {journalIllisible
          ? <p className="disclaimer">Journal non relu : non imprimé.</p>
          : <EventLog log={journalRendu} windowLabel="Session" complet />}
      </section>

      <section className="rapport-section">
        {courbes.map((c) => (
          <CourbeDeSession key={`${c.wan.wan_id}/${c.metric}`} courbe={c} />
        ))}
      </section>

      <footer className="rapport-pied disclaimer">
        Édité le {new Date().toLocaleString('fr-FR')} par l’outil de supervision Peplink. « — » :
        grandeur non mesurée. En pointillé : historique du routeur dans les trous de supervision.
      </footer>
    </div>
  )
}

/** Une courbe sur toute la session, à taille fixe pour l'impression. Les
 *  points de l'historique du routeur sont tracés à part, en pointillé. L'encre
 *  suit les jetons du thème : claire à l'écran, foncée sur le papier. */
function CourbeDeSession({ courbe }: { courbe: Courbe }) {
  const { wan, metric, serie } = courbe
  const points = useMemo(() => withGaps((serie?.points ?? []).map((p) => {
    const routeur = p.source === 'routeur'
    return { t: Date.parse(p.ts), value: routeur ? null : p.value, comble: routeur ? p.value : null }
  })), [serie])
  const mesures = points.filter((p) => p.value !== null || (p.comble ?? null) !== null).length
  return (
    <figure className="rapport-courbe">
      <h3>Courbes — {etiquetteSupport(wan.kind)} {wan.wan_id} · {LIBELLES[metric]}</h3>
      {serie === null ? (
        <p className="disclaimer">Lecture impossible : la courbe n’a pas pu être relue.</p>
      ) : mesures === 0 ? (
        <p className="disclaimer">Aucune mesure sur la session.</p>
      ) : (
        <LineChart width={680} height={140} data={points} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--rule)" vertical={false} />
          <XAxis dataKey="t" type="number" domain={['dataMin', 'dataMax']} scale="time"
            tickFormatter={(t) => fmtClock(Number(t))} tick={{ fontSize: 10 }} />
          <YAxis width={40} tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
          <Line dataKey="value" stroke="var(--ink)" strokeWidth={1.5} dot={false} isAnimationActive={false} />
          <Line dataKey="comble" stroke="var(--ink)" strokeOpacity={0.5} strokeDasharray="4 3"
            strokeWidth={1.2} dot={false} isAnimationActive={false} />
        </LineChart>
      )}
    </figure>
  )
}
