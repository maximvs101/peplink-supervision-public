import { useEffect, useMemo, useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, Tooltip, XAxis, YAxis } from 'recharts'

import { fetchEvents, fetchHistory, fetchSessionSummary, nameSession } from '../api/client'
import type { Episode, HistoryPoint, Session, SessionSummary } from '../types'
import { decimal, fmtClock, fmtInt, fmtTime } from '../lib/format'
import {
  axisDigits, changementsDeTechno, framedDomain, metricsFor, outOfFrameNote, reperesDuJournal,
  trousDeSupervision, trousLisibles, withGaps,
} from '../lib/chart'
import { useElementSize } from '../lib/useElementSize'
import { LOSS_MIN_PACKETS } from '../lib/verdict'
import { debutObserve } from '../lib/session'
import { SessionSheet } from './SessionSheet'

/** Fenêtres d'historique proposées. Le maximum suit le plafond de la route.
 *  Une session de stationnement dure couramment trois heures et plus — c'est la
 *  durée pendant laquelle l'équipement est alimenté —, et l'écran ne savait
 *  regarder que trente minutes. */
const FENETRES = [
  { minutes: 30, label: '30 min' },
  { minutes: 180, label: '3 h' },
  { minutes: 720, label: '12 h' },
  { minutes: 1440, label: '24 h' },
]

const REFRESH_MS = 15000


/** Un volume non mesuré s'écrit « — », jamais « 0.0 » : sur cet outil, un zéro
 *  affiché à la place d'une absence de mesure a déjà menti cinq fois. */


interface Props {
  /** Les épisodes du journal de la fenêtre relue, à poser sur la courbe (A-4). */
  episodes?: Episode[]
  routerId: string
  wanId: string
  wanName: string
  kind: 'cellular' | 'wifi' | 'ethernet'
  /** L'équipement porte-t-il un tunnel SpeedFusion ? Piloté par les capacités
   *  détectées, comme `showThroughput`, et jamais par un test sur la valeur. */
  hasTunnel?: boolean
  /** Période RÉELLEMENT observée entre deux relevés, telle que le serveur la
   *  mesure. La fiche de session la citait en dur — « une mesure toutes les cinq
   *  secondes » — pour justifier la précision d'un volume intégré. Or elle
   *  dépend de la consigne et du temps de cycle, et vaut 5,2 s ici, 6,4 s quand
   *  le routeur répond plus lentement : un chiffre recopié dans une phrase qui
   *  sert d'argument devient faux sans prévenir. */
  periodSeconds?: number | null
  /** La liste des sessions, lue par `DeviceShell` — plus par ce composant. */
  sessions?: Session[]
  /** Après un renommage, demande à `DeviceShell` de relire la liste. */
  onRafraichirSessions?: () => Promise<void>
}

/** Historique glissant d'une métrique, rafraîchi périodiquement. */

export function HistoryChart({
  routerId, wanId, wanName, kind, hasTunnel = false, periodSeconds = null,
  sessions: sessionsProp, onRafraichirSessions, episodes = [],
}: Props) {
  // Les grandeurs du tunnel étaient offertes sur tous les liens de tous les
  // équipements. Sur un boîtier sans SpeedFusion, cela faisait deux boutons de
  // plus — « Latence », « Perte » — qui traçaient un graphe VIDE sur un axe
  // gradué de 0 à 100 : l'historique renvoie des points datés à valeur nulle, le
  // garde-fou « Collecte en cours » n'est donc jamais atteint. Mémoïsé parce que
  // la liste sert de dépendance à l'effet de repli ci-dessous.
  const metrics = useMemo(() => metricsFor(kind, { hasTunnel }), [kind, hasTunnel])
  const [metric, setMetric] = useState<string>(metrics[0].key)

  // Passer d'un lien cellulaire à un lien filaire depuis le même écran laisserait
  // une métrique sélectionnée qui n'existe pas pour ce type de lien.
  useEffect(() => {
    if (!metrics.some((m) => m.key === metric)) setMetric(metrics[0].key)
  }, [metrics, metric])
  // Fenêtres proposées. Les sessions réelles durent trois heures et plus : ne
  // savoir regarder que la dernière demi-heure interdisait de voir ce qui s'est
  // passé avant l'arrivée de celui qui prend la relève.
  const [minutes, setMinutes] = useState(30)
  // La série LUE, avec la clé de sa requête : grandeur, fenêtre, session. Au
  // changement de grandeur, l'ancienne restait affichée sous le nouveau libellé
  // pendant le chargement, jugée par les règles de la nouvelle — le pied
  // annonçait des points « retirés » qui n'existaient pas (C-41).
  const [lu, setLu] = useState<{ cle: string; points: HistoryPoint[]; available: number }>(
    { cle: '', points: [], available: 0 },
  )
  // Les sessions d'alimentation servent de fenêtres : l'équipement n'est allumé
  // que véhicule à l'arrêt, donc « cette session-là » est la seule borne qui ait
  // un sens pour relire ce qui s'est passé à un endroit donné.
  // La liste vient de `DeviceShell`, qui la lit déjà toutes les trente
  // secondes ; une sonde propre à quinze secondes la doublait ici — et les
  // deux coïncidaient une fois sur deux, le seul doublon de requête qui était
  // du code une fois écartée la fausse piste du second onglet. Le `last_seen`
  // de la session en cours suit donc la cadence de `DeviceShell`.
  const sessions = sessionsProp ?? []
  const [sessionId, setSessionId] = useState<number | null>(null)
  // La fiche de la session choisie. C'est elle qui rend deux stationnements
  // comparables : sans elle, une session n'est qu'un intervalle de temps.
  const [fiche, setFiche] = useState<SessionSummary | null>(null)
  // Le nom en cours d'édition. Réamorcé sur celui de la fiche à chaque
  // changement de session, sans quoi l'écran proposerait de renommer une session
  // avec le nom d'une autre.
  const [nom, setNom] = useState('')
  const [nommage, setNommage] = useState(false)
  const [nomErreur, setNomErreur] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [chartRef, chartSize] = useElementSize<HTMLDivElement>()

  useEffect(() => {
    if (sessionId === null) { setFiche(null); return }
    let cancelled = false
    fetchSessionSummary(sessionId)
      .then((f) => {
        if (cancelled) return
        setFiche(f)
        setNom(f.session.label ?? '')
        setNomErreur(null)
      })
      .catch(() => { if (!cancelled) setFiche(null) })
    return () => { cancelled = true }
  }, [sessionId])

  // Les BORNES de la session choisie — deux chaînes stables, là où l'objet
  // session change d'identité à chaque relève de la liste.
  const sessionChoisie = sessions.find((s) => s.id === sessionId)
  const bornes = sessionChoisie
    ? { since: debutObserve(sessionChoisie), lastSeen: sessionChoisie.last_seen }
    : null
  // Une session PASSÉE — pas la plus récente — ne produit plus de relevé : sa
  // courbe ne peut pas changer. Elle était rejouée toutes les quinze secondes
  // (4 147 mesures rechargées pour rien), et une session passée sans point
  // affichait « Collecte en cours », une attente qui ne finirait jamais.
  const passee = sessionChoisie !== undefined && sessionChoisie.id !== sessions[0]?.id

  // Les repères d'une session PASSÉE viennent de SON journal : ceux de la
  // fenêtre courante tombaient hors de l'axe, et la courbe d'hier ne montrait
  // ni désactivation ni coupure (revue du 17/10). Lu une fois — une session
  // passée ne change plus.
  const [episodesPasses, setEpisodesPasses] = useState<Episode[] | null>(null)
  useEffect(() => {
    if (!passee || bornes === null) {
      setEpisodesPasses(null)
      return
    }
    let annule = false
    Promise.resolve()
      .then(() => fetchEvents(routerId, { since: bornes.since, until: bornes.lastSeen }))
      .then((j) => { if (!annule) setEpisodesPasses(j?.episodes ?? []) })
      .catch(() => { if (!annule) setEpisodesPasses([]) })
    return () => { annule = true }
  }, [routerId, passee, bornes?.since, bornes?.lastSeen])
  const journal = passee ? (episodesPasses ?? []) : episodes

  useEffect(() => {
    let cancelled = false
    // La série est rejouée toutes les quinze secondes et une fenêtre de session
    // peut porter des milliers de points : c'est la requête la plus lente de
    // l'écran, donc la plus susceptible d'être doublée. Sans jeton, une réponse
    // ancienne repose une courbe périmée et son `available` avec — l'enveloppe
    // se lisait alors comme une série complète.
    let rang = 0
    let dernierRendu = 0
    const cleDeLaRequete = [routerId, wanId, metric, minutes, sessionId].join('|')

    const load = async () => {
      const mien = (rang += 1)
      try {
        const series = await fetchHistory(
          routerId, wanId, metric, minutes,
          // La fin est décalée d'une minute : `last_seen` est le dernier relevé
          // reçu, et une session en cours continue d'en produire.
          bornes
            ? {
                since: bornes.since,
                until: new Date(new Date(bornes.lastSeen).getTime() + 60_000).toISOString(),
              }
            : undefined,
          // Un taux voyage avec la règle qui dit s'il est publiable : le
          // serveur l'applique avant l'enveloppe, et élit le pire des sens
          // PUBLIABLES — la même règle que la table, enfin.
          metrics.find((m) => m.key === metric)?.rate ? LOSS_MIN_PACKETS : undefined,
        )
        if (!cancelled && mien >= dernierRendu) {
          dernierRendu = mien
          setLu({ cle: cleDeLaRequete, points: series.points, available: series.available })
          setError(null)
        }
      } catch (err) {
        if (!cancelled && mien >= dernierRendu) {
          dernierRendu = mien
          setError(err instanceof Error ? err.message : 'Erreur inconnue')
        }
      }
    }

    load()
    const timer = passee ? null : window.setInterval(load, REFRESH_MS)
    return () => {
      cancelled = true
      if (timer !== null) window.clearInterval(timer)
    }
    // `sessions` n'est PAS une dépendance : la liste est un tableau neuf à
    // chaque relève de quinze secondes, et l'effet se relançait à chacune —
    // mesuré aux timers factices, /history partait deux fois par période, dos
    // à dos, pour une courbe identique. Ce qui compte est la session choisie
    // et ses bornes.
  }, [routerId, wanId, metric, minutes, sessionId, bornes?.since, bornes?.lastSeen, metrics, passee])

  const cle = [routerId, wanId, metric, minutes, sessionId].join('|')
  const data = useMemo(() => (lu.cle === cle ? lu.points : []), [lu, cle])
  // Points réellement présents dans la fenêtre. Au-delà de ce que la courbe
  // porte, elle est une ENVELOPPE — minimum et maximum par tranche — et le dire
  // est nécessaire : une courbe allégée se lit comme une courbe complète.
  const available = lu.cle === cle ? lu.available : 0

  const current = metrics.find((m) => m.key === metric) ?? metrics[0]

  // Conversion d'unité avant affichage : un axe gradué en dizaines de milliers
  // de kbit/s ne se lit pas.
  const divide = current.divide ?? 1

  // Un taux calculé sur trop peu de paquets n'est pas une mesure basse : c'est
  // une absence de mesure. Il devient donc un trou dans la courbe, comme tout
  // ce qui n'a pas été mesuré — et le nombre de points retirés est dit.
  // Un taux dont on ignore le dénominateur n'est PAS publiable : le graphe
  // traitait `denominator === null` comme « publiable sans condition », donc
  // exactement à l'envers. Huit mille relevés de la base portent un taux sans
  // aucun compteur.
  const publiable = (p: HistoryPoint) =>
    !current.rate || (p.denominator !== null && p.denominator !== undefined
      && p.denominator >= LOSS_MIN_PACKETS)

  const retires = data.filter((p) => p.value !== null && !publiable(p)).length

  // La courbe est-elle une ENVELOPPE ? Le serveur allège les fenêtres denses en
  // rendant deux points par tranche — le plus bas et le plus haut — et l'espacement
  // des points cesse alors de refléter la cadence de scrutation.
  const enveloppe = available > data.length

  // Les points du ROUTEUR (R-3.1) vont dans leur propre série : ils se tracent
  // à part, et la courbe de cet outil s'interrompt là où elle n'a rien relevé.
  const duRouteur = (p: HistoryPoint) => p.source === 'routeur'
  const bruts = data.map((p) => {
    const v = p.value === null || !publiable(p) ? null : p.value / divide
    return { t: new Date(p.ts).getTime(), value: duRouteur(p) ? null : v, comble: duRouteur(p) ? v : null }
  })
  const combles = bruts.filter((p) => p.comble !== null).length
  // On ne coupe la série QUE sur une courbe complète. `withGaps` déduit son
  // seuil de l'intervalle médian ; sur une enveloppe, cet intervalle n'est plus
  // la cadence mais la largeur des tranches, et deux points d'une même tranche
  // peuvent être très éloignés dans le temps sans qu'aucune collecte ait manqué.
  // Mesuré sur la session 1 : trois coupures FANTÔMES tracées — vérifié dans la
  // base, vingt relevés en cent dix secondes, intervalle maximal 9,2 s — et
  // trois interruptions réelles de dix-sept secondes NON tracées, pendant que le
  // pied annonçait « aucun extrême perdu ».
  const points = enveloppe ? bruts : withGaps(bruts)
  // Ce qui a RÉELLEMENT été mesuré, par opposition aux relevés traversés. La
  // distinction n'existait pas tant que toutes les grandeurs étaient relevées à
  // chaque cycle ; elle décide de l'affichage des marqueurs depuis qu'une
  // grandeur est lue une fois par minute.
  const mesures = points.reduce((n, p) => (p.value === null && (p.comble ?? null) === null ? n : n + 1), 0)
  const values = points.flatMap((p) => [p.value, p.comble ?? null]).filter((v): v is number => v !== null)
  const framing = framedDomain(current, values)
  const domain = framing.domain
  const digits = axisDigits(domain)
  const horsCadre = outOfFrameNote(framing, current)
  // Les changements de technologie : sans repère, un passage de 5G en LTE se
  // lisait comme une chute de signal (revue, scénario 5).
  const technos = changementsDeTechno(data)
  // Ce qui explique un creux (A-4) : les trous de supervision — sur une courbe
  // complète seulement, l'enveloppe le dit déjà — et les épisodes du journal de
  // CE lien, dans la fenêtre tracée.
  const tracees = points.filter((p) => p.value !== null || (p.comble ?? null) !== null).map((p) => p.t)
  const etendue: [number, number] = tracees.length
    ? [Math.min(...tracees), Math.max(...tracees)] : [0, 0]
  const reperes = reperesDuJournal(journal, wanId, etendue)
  const desactivations = reperes.filter((r) => r.genre === 'désactivation')
  const trous = enveloppe ? [] : trousLisibles(trousDeSupervision(points), desactivations)
  const transitions = reperes.filter((r) => r.genre !== 'désactivation')

  return (
    <section className="card">
      <header className="card-head">
        <span className="card-title">Historique — {wanName}</span>
        <span className="spacer" />
        <div className="segmented">
          {metrics.map((m) => (
            <button
              key={m.key}
              type="button"
              className={m.key === metric ? 'is-active' : ''}
              aria-pressed={m.key === metric}
              onClick={() => setMetric(m.key)}
            >
              {m.label}
            </button>
          ))}
        </div>
        {/* La fenêtre, à côté des grandeurs : le choix « quoi regarder » et le
            choix « sur quelle durée » se font au même moment. */}
        <div className="segmented">
          {FENETRES.map((f) => (
            <button
              key={f.minutes}
              type="button"
              className={sessionId === null && f.minutes === minutes ? 'is-active' : ''}
              aria-pressed={sessionId === null && f.minutes === minutes}
              onClick={() => { setSessionId(null); setMinutes(f.minutes) }}
            >
              {f.label}
            </button>
          ))}
          {sessions.length > 0 && (
            <select
              className={sessionId !== null ? 'is-active' : ''}
              value={sessionId ?? ''}
              onChange={(e) => setSessionId(e.target.value === '' ? null : Number(e.target.value))}
              aria-label="Session d’alimentation"
            >
              <option value="">Session…</option>
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label ?? new Date(s.started_at).toLocaleString('fr-FR')}
                </option>
              ))}
            </select>
          )}
        </div>
      </header>

      <div className="card-body">
        {error ? (
          <div className="notice error">Historique indisponible : {error}</div>
        ) : data.length === 0 ? (
          <div className="notice">
            {passee
              ? 'Aucune mesure de cette grandeur dans cette session.'
              : 'Collecte en cours — les mesures apparaîtront d’ici quelques secondes.'}
          </div>
        ) : (
          <div className="chart-shell" ref={chartRef}>
            {chartSize.width > 0 && (
              <LineChart
                width={chartSize.width}
                height={chartSize.height || 240}
                data={points}
                margin={{ top: 6, right: 10, bottom: 0, left: -18 }}
              >
                <CartesianGrid stroke="var(--rule)" strokeDasharray="2 4" />
                {/* Axe temporel linéaire, et non catégoriel : sinon une
                    interruption de sept minutes occuperait la même largeur
                    qu'un intervalle de dix secondes. */}
                <XAxis
                  dataKey="t"
                  type="number"
                  scale="time"
                  domain={['dataMin', 'dataMax']}
                  tickFormatter={(t: number) => fmtClock(t)}
                  stroke="var(--ink-3)"
                  fontSize={11}
                  fontFamily="IBM Plex Mono"
                  minTickGap={40}
                />
                {/* `allowDataOverflow` doit être VRAI, sans quoi recharts
                    réélargit le domaine aux données et le bornage ne sert à
                    rien : `ChartUtils.js` fait
                    `Math.min(specifiedDomain[0], dataDomain[0])` quand il est
                    faux. Sur la série de latence du 30 août — 25 à 35 ms plus un
                    pic à 1 929 ms —, le cadre calculé [0, ~35] était rendu à
                    [0, 1 929] et la bande utile restait écrasée à 1,7 % de la
                    hauteur, c'est-à-dire le défaut que ce cadre corrige. Ce qui
                    sort du cadre n'est pas tu : `horsCadre` le dit. */}
                <YAxis
                  stroke="var(--ink-3)"
                  fontSize={11}
                  fontFamily="IBM Plex Mono"
                  width={54}
                  domain={domain}
                  allowDataOverflow
                  tickFormatter={(v: number) => decimal(v, digits)}
                />
                <Tooltip
                  contentStyle={{
                    background: 'var(--panel-2)',
                    border: '1px solid var(--rule)',
                    borderRadius: 2,
                    fontSize: 13,
                    fontFamily: 'Barlow Semi Condensed, system-ui, sans-serif',
                  }}
                  labelFormatter={(t) => fmtTime(Number(t))}
                  formatter={(value: number) => [
                    `${decimal(value, divide > 1 ? 2 : 1)} ${current.unit}`,
                    current.label,
                  ]}
                />
                {/* Pas de `connectNulls` : une coupure laisse un trou dans la
                    série, et le tracer d'un trait continu la ferait disparaître
                    — exactement l'événement qu'on cherche à voir. Les points
                    sont marqués quand la série est courte, sinon une mesure
                    isolée entre deux trous serait invisible.

                    Le seuil porte sur les points MESURÉS, pas sur la longueur
                    de la série. Une grandeur espacée — `path_latency_ms` l'était,
                    une fois par minute, avant que la série du routeur ne soit
                    gardée (R-3.2) — donne sept valeurs parmi trois cents relevés : le compte brut dépassait quarante, les
                    marqueurs s'éteignaient, et comme chaque mesure est isolée
                    entre deux nuls, aucun trait ne les reliait non plus. La
                    courbe existait et ne se voyait pas. */}
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke={current.color}
                  strokeWidth={2}
                  dot={mesures <= 40 ? { r: 2 } : false}
                  isAnimationActive={false}
                />
                {combles > 0 && (
                  <Line
                    type="monotone"
                    dataKey="comble"
                    stroke={current.color}
                    strokeOpacity={0.5}
                    strokeWidth={1.5}
                    strokeDasharray="4 3"
                    dot={false}
                    isAnimationActive={false}
                  />
                )}
                {trous.map((t) => (
                  <ReferenceArea
                    key={`trou-${t.de}`}
                    x1={t.de}
                    x2={t.a}
                    fill="var(--ink-3)"
                    fillOpacity={0.08}
                    stroke="none"
                  />
                ))}
                {desactivations.map((d) => (
                  <ReferenceArea
                    key={`des-${d.de}`}
                    x1={d.de}
                    x2={d.a ?? etendue[1]}
                    fill="var(--q-inconnu)"
                    fillOpacity={0.15}
                    stroke="none"
                    label={{ value: 'désactivé', position: 'insideTop', fill: 'var(--ink-3)', fontSize: 10 }}
                  />
                ))}
                {transitions.map((r) => (
                  <ReferenceLine
                    key={`${r.genre}-${r.de}`}
                    x={r.de}
                    stroke={r.genre === 'retour' ? 'var(--q-excellent)' : r.genre === 'coupure' ? 'var(--q-faible)' : 'var(--q-moyen)'}
                    strokeDasharray="2 3"
                  />
                ))}
                {technos.map((c) => (
                  <ReferenceLine
                    key={`${c.t}-${c.vers}`}
                    x={c.t}
                    stroke="var(--ink-3)"
                    strokeDasharray="3 3"
                    label={{ value: `→ ${c.vers}`, position: 'insideTopLeft', fill: 'var(--ink-3)', fontSize: 10 }}
                  />
                ))}
              </LineChart>
            )}
          </div>
        )}
        {/* Signaler, jamais masquer : sans cette mention, le pic du 30 août
            disparaîtrait purement et simplement du graphe. */}
        {horsCadre && <p className="disclaimer">{horsCadre}</p>}
        {combles > 0 && (
          <p className="disclaimer">
            En pointillé, {fmtInt(combles)} point{combles > 1 ? 's' : ''} comblé{combles > 1 ? 's' : ''} par l’historique du
            routeur (un point toutes les 10 s), là où cet outil n’a rien relevé — des mesures
            passées, sans état de connexion.
          </p>
        )}
        {(trous.length > 0 || reperes.length > 0) && (
          <p className="disclaimer">
            Sur la courbe :{' '}
            {[
              trous.length > 0
                ? `${trous.length} trou${trous.length > 1 ? 's' : ''} de supervision (`
                  + trous.slice(0, 6).map((t) => fmtClock(t.de)).join(', ')
                  + (trous.length > 6 ? ', …' : '')
                  + ') — zones grisées, le superviseur ne relevait rien'
                : null,
              ...desactivations.map((d) => `désactivé par l’exploitant de ${fmtClock(d.de)}`
                + (d.a ? ` à ${fmtClock(d.a)}` : ', toujours')),
              ...(() => {
                const c = transitions.filter((r) => r.genre !== 'retour').length
                return c > 0 ? [`${c} coupure${c > 1 ? 's' : ''} marquée${c > 1 ? 's' : ''} en pointillé`] : []
              })(),
            ].filter(Boolean).join(' · ')}.
          </p>
        )}
        {technos.length > 0 && (
          <p className="disclaimer">
            Technologie changée {technos.length === 1 ? 'une fois' : `${technos.length} fois`} —{' '}
            {technos.map((c) => `${c.de} → ${c.vers} à ${fmtClock(c.t)}`).join(', ')} : les valeurs
            de part et d’autre d’un repère ne se mesurent pas sur la même porteuse et ne se
            comparent pas.
          </p>
        )}
        {retires > 0 && (
          <p className="disclaimer">
            {retires === 1
              ? 'Un point a été retiré de la courbe'
              : `${retires} points ont été retirés de la courbe`}{' '}
            : moins de {LOSS_MIN_PACKETS} paquets comptés sur l’intervalle. Un unique paquet
            manquant sur un keepalive affiche 33 % de perte sur un lien parfaitement sain —
            ce n’est pas une mesure basse, c’est une absence de mesure.
          </p>
        )}
        {fiche && (
          <div className="session-sheet">
            {/* Le NOM de la session, modifiable ici. « Le parking de la mairie »
                se retrouve d'une semaine sur l'autre ; un horodatage, non —
                c'est ce qui rend deux stationnements comparables, et c'était
                jusqu'ici inatteignable depuis l'interface. */}
            <form
              className="session-name"
              onSubmit={(e) => {
                e.preventDefault()
                if (nommage) return
                setNommage(true)
                setNomErreur(null)
                nameSession(fiche.session.id, nom)
                  .then(() => onRafraichirSessions?.())
                  .catch((err) =>
                    setNomErreur(err instanceof Error ? err.message : 'Nom refusé'),
                  )
                  .finally(() => setNommage(false))
              }}
            >
              <input
                value={nom}
                maxLength={120}
                onChange={(e) => setNom(e.target.value)}
                placeholder="Nommer ce stationnement…"
                aria-label="Nom de la session"
              />
              <button type="submit" className="is-quiet" disabled={nommage}>
                {nommage ? 'Enregistrement…' : 'Enregistrer'}
              </button>
              {nomErreur !== null && <span className="absent">{nomErreur}</span>}
              {/* Emporter la session : fiche, journal et courbes, à imprimer en
                  PDF, et les relevés en CSV (R-3.7). Dans un autre onglet : la
                  page de supervision reste où elle est. */}
              <a href={`#/rapport/${fiche.session.id}`} target="_blank" rel="noreferrer">
                Rapport
              </a>
            </form>
            <SessionSheet fiche={fiche} periodSeconds={periodSeconds} />
          </div>
        )}

        <p className="disclaimer">
          {sessionId !== null
            // Une session choisie que la liste ne porte plus — relue entre
            // deux lectures — n'a pas de date à inventer : `new Date('')`
            // écrivait « Invalid Date », un mot que l'écran s'interdit.
            ? (sessionChoisie
                ? `session du ${new Date(sessionChoisie.started_at).toLocaleString('fr-FR')}`
                : 'session choisie')
            : (FENETRES.find((f) => f.minutes === minutes)?.label ?? `${minutes} min`)}{' '}
          ·{' '}
          {enveloppe
            ? `${available} mesures, tracées en enveloppe — ${data.length} points, `
              + 'minimum et maximum par tranche, aucun extrême perdu ; '
              + 'les interruptions ne sont pas marquées, l’espacement des points '
              + 'ne reflétant plus la cadence'
            : `${points.filter((p) => p.value !== null).length} `
              + `mesure${points.filter((p) => p.value !== null).length > 1 ? 's' : ''} tracée`
              + `${points.filter((p) => p.value !== null).length > 1 ? 's' : ''}`}{' '}
          · {passee ? 'session passée, courbe figée' : 'rafraîchi toutes les 15 s'}
        </p>
      </div>
    </section>
  )
}
