import type { SessionSummary } from '../types'
import { decimal, etiquetteSupport, fmtId, fmtInt } from '../lib/format'
import { Notes } from './Note'

/**
 * La fiche d'une session : synthèse par lien, disponibilité du bond, diversité,
 * latence par cellule. Extraite du panneau d'historique pour que le RAPPORT
 * imprimable (R-3.7) la rende à l'identique — une fiche, pas deux copies qui
 * divergent.
 */

const volume = (mo: number | null) => (mo === null ? '—' : decimal(mo, 1))

/** La durée que couvrent `n` points de l'historique du routeur, à 10 s l'un. */
/** « sur N cycles » : le dénominateur propre à une part de diversité. Tu
 *  quand le serveur ne le publie pas (une version en arrière) : on ne
 *  l'invente pas. */
const surCycles = (n: number | undefined) =>
  n === undefined || n <= 0 ? '' : ` sur ${fmtInt(n)} cycles`

/** Le nombre de mesures sous un percentile, écrit — pas dans un `title`. */
function Compte({ n }: { n: number | undefined }) {
  if (n === undefined || n <= 0) return null
  return <small className="sub-note"> sur {fmtInt(n)}</small>
}

function dureeComblee(n: number): string {
  const secondes = n * 10
  return secondes < 60 ? `${secondes} s` : `${Math.floor(secondes / 60)} min`
}

export function SessionSheet({
  fiche, periodSeconds = null, partager = false,
}: {
  fiche: SessionSummary
  periodSeconds?: number | null
  /** « À partager » (rapport, R-3.7) : aucune position — arrondie à ~100 m,
   *  elle désignait encore le lieu d'intervention (A-12) —, et pas d'opérateur
   *  pour un lien Wi-Fi : l'outil y range le SSID. */
  partager?: boolean
}) {
  const aBond = fiche.links.some((l) => l.rtt_p50_ms !== null && l.rtt_p50_ms !== undefined)
  const aRadio = fiche.links.some((l) => l.kind === 'cellular')
  return (
    <>
      <div className="session-sheet-head">
        {/* Un volume non mesuré n'est pas un volume nul. Le Dome Pro Duo
            ne publie aucun débit par WAN : la fiche écrivait « 0.0 Mo
            montants · 0.0 Mo descendants », en gras, sur sept cent vingt
            relevés où rien n'avait jamais été mesuré. */}
        {fiche.mb_up === null && fiche.mb_down === null ? (
          <strong>Volumes non mesurés sur cet équipement</strong>
        ) : (
          <strong>
            {volume(fiche.mb_up)} Mo montants · {volume(fiche.mb_down)} Mo descendants
          </strong>
        )}
        {/* Ce badge compte des STATIONS de base, et il disait « site ». Dans
            le vocabulaire radio du projet les deux mots se valent — mais
            il est posé à côté d'une POSITION GPS, où « 2 sites » se lit
            comme deux lieux. Invisible sur le MBX, un opérateur et une
            station : « un seul site » collait à la position par
            accident. Le MAX HD2, deux opérateurs, affichait « 2 sites » à
            côté de « ± 1 m » — un équipement qui n'a pas bougé. */}
        {fiche.sites > 0 && (
          <span className="badge" title="Stations de base distinctes vues sur la session — un fait radio, pas un déplacement">
            {fiche.sites === 1 ? 'une seule station' : `${fiche.sites} stations`}
          </span>
        )}
        {fiche.lat !== null && !partager && (
          <span className="badge">
            {fiche.lat.toFixed(5)}, {fiche.lon?.toFixed(5)}
            {fiche.radius_m !== null && ` ± ${Math.round(fiche.radius_m)} m`}
          </span>
        )}
      </div>
      <div className="scroll">
        <table className="session-table">
          <thead>
            <tr>
              <th>Lien</th><th>Mo ↑</th><th>Mo ↓</th>
              {/* Deux latences, comme au tableau de bord : celle du BOND
                  (aller-retour vers le concentrateur) et celle du LIEN,
                  hors tunnel — la seule d'un équipement dont le tunnel ne
                  tient pas (R-3.3). Elles ne visent pas la même cible. */}
              {/* Sans tunnel mesuré, trois colonnes vides : elles serraient le
                  rapport au point de coller les volumes (A-7). */}
              {aBond && <><th>Bond médian</th><th>Bond p90</th><th>Bond max</th></>}
              <th>Lien médian</th><th>Lien p90</th><th>Lien max</th>
              {/* Calculé par `sessions.py`, déclaré dans les types, et
                  rendu nulle part : la colonne manquait, pas la mesure.
                  C'est elle qui distingue un lien faible d'un lien
                  seulement lent — et la médiane, non la moyenne, pour la
                  même raison que le RTT. */}
              {aRadio && <th>RSRP médian</th>}
              <th>Coupures</th>
              {aRadio && <th>Cellules</th>}
              {/* La colonne du TAUX de brassage n'avait pas d'en-tête : un
                  nombre sans nom en bout de ligne (revue, R-3.8). Même mot
                  qu'au tableau de bord. */}
              {aRadio && <th>Sauts de cellule / h</th>}
            </tr>
          </thead>
          <tbody>
            {fiche.links.map((l) => (
              <tr key={l.wan_id}>
                <td>
                  {etiquetteSupport(l.kind)} {l.wan_id}
                  {/* Un lien qui a changé d'opérateur le dit : le WAN 3 de la
                      revue, passé chez SFR, restait muet sur sa fiche. */}
                  {(l.operators ?? []).length > 1 && !(partager && l.kind === 'wifi') && (
                    <small className="sub-note"> {l.operators.join(' → ')}</small>
                  )}
                </td>
                <td className="num">{volume(l.mb_up)}</td>
                <td className="num">{volume(l.mb_down)}</td>
                {aBond && <>
                  <td className="num">
                    {l.rtt_p50_ms ?? '—'}<Compte n={l.rtt_samples} />
                  </td>
                  <td className="num">{l.rtt_p90_ms ?? '—'}</td>
                  <td className="num">{l.rtt_max_ms ?? '—'}</td>
                </>}
                {/* Le compte voyage avec les percentiles, pour qu'un p90
                    sur trois points ne se lise pas comme un p90 sur trois
                    heures. Sans cadence annoncée : un point / 10 s depuis
                    que la série du routeur est gardée (R-3.2), un par
                    minute avant. */}
                {/* En TEXTE, et non dans un `title` : un title ne s'imprime
                    pas, et le rapport promet que les latences voyagent avec
                    leur nombre de mesures (C-12). */}
                <td className="num">
                  {l.path_latency_p50_ms ?? '—'}<Compte n={l.path_latency_samples} />
                </td>
                <td className="num">{l.path_latency_p90_ms ?? '—'}</td>
                <td className="num">{l.path_latency_max_ms ?? '—'}</td>
                {aRadio && <td className="num">
                  {l.rsrp_median ?? '—'}
                  {/* La médiane couvre toute la session : nos relevés, et
                      l'heure du routeur dans les trous (R-3.1). Elle dit ce
                      qu'elle lui doit — un point toutes les 10 s. */}
                  {(l.rsrp_filled_samples ?? 0) > 0 && (
                    <small
                      className="sub-note"
                      title={`${l.rsrp_filled_samples} points de l’historique du routeur, pris dans les trous de supervision`}
                    >
                      {' '}dont {dureeComblee(l.rsrp_filled_samples)} comblées
                    </small>
                  )}
                </td>}
                <td className="num">
                  {l.outages}
                  {(l.maneuvers ?? 0) > 0 && (
                    <small className="sub-note">
                      {' '}+ {l.maneuvers} manœuvre{l.maneuvers > 1 ? 's' : ''}
                    </small>
                  )}
                </td>
                {/* Zéro cellule distincte n'est pas « zéro » : c'est une
                    absence de relevé d'identité — un lien filaire n'en a
                    pas, un modem muet n'en a jamais publié. Les coupures,
                    elles, se comptent vraiment à partir de zéro. */}
                {aRadio && <td className="num">{l.cells > 0 ? l.cells : '—'}</td>}
                {/* Le TAUX, pas le compte : c'est lui qui se compare d'un
                    lien à l'autre et d'une session à l'autre. Absent sur
                    un lien sans cellule — un filaire ne peut pas en
                    changer, et zéro s'y lirait comme une stabilité. */}
                {aRadio && <td className="num">
                  {l.cell_changes_per_hour ?? '—'}
                  {l.cell_changes_per_hour !== null && l.cell_changes_per_hour !== undefined && (
                    <small className="sub-note">
                      {' '}· {fmtInt(l.cell_changes)} saut{l.cell_changes > 1 ? 's' : ''}
                    </small>
                  )}
                </td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* Ce que la liaison a TENU, et ce que les modems partageaient.
          Les deux se calculent sur les relevés que cette fiche lit déjà :
          aucune requête au routeur, aucun champ nouveau. */}
      <Notes>
      {/* Quand le bond n'a porté AUCUN lien de toute la session, « 0 % de
          lien propre » ne juge pas la liaison : il constate l'absence du
          tunnel. Vu sur le MAX HD2, concentrateur injoignable — 200 Mo
          sortis en direct pendant que la fiche affichait « 0 % ». Le fait
          se dit alors en premier, et le taux ne s'affiche pas : il
          répondrait à une question que personne n'a posée. */}
      {/* `cycles_in_bond === 0` d'un côté, `> 0` de l'autre : un champ
          ABSENT — écran reconstruit servi par un serveur pas redémarré,
          ce qui est la forme réelle de ce déploiement — rendait les deux
          faux et effaçait tout le bloc sans un mot. Absent, on ne sait
          pas : le taux s'affiche comme avant. */}
      {fiche.availability && fiche.availability.observed_cycles > 0
        && fiche.availability.cycles_in_bond === 0 && (
        <p className="panel-note">
          <b>Le tunnel n’a porté aucun lien sur {fiche.partial ? 'la fenêtre relue' : 'cette session'}</b> — sur{' '}
          {fmtInt(fiche.availability.observed_cycles)} cycles observés, aucun lien
          n’a été dans le bond. La disponibilité « propre » se mesure sur le bond ;
          elle est sans objet ici. Ce que la liaison a porté se lit dans les volumes.
        </p>
      )}
      {fiche.availability && fiche.availability.observed_cycles > 0
        && !(fiche.availability.cycles_in_bond === 0) && (
        <p className="panel-note">
          <b>Au moins un lien propre {fiche.availability.at_least_one_pct} % du
          temps</b>{fiche.links.length >= 2 && (
            <>, au moins deux {fiche.availability.at_least_two_pct} %</>
          )}.
          {/* « Au moins deux » sur un châssis qui n'a qu'un lien est un
              zéro pour une question qu'on ne peut pas poser. */}
          {' '}« Propre » veut dire : connecté, dans le bond, et sous{' '}
          {fmtInt(fiche.availability.threshold_ms)} ms d’aller-retour — c’est un
          choix, pas une norme.
          {' '}Mesuré sur {fmtInt(fiche.availability.observed_cycles)} cycles
          {fiche.availability.total_cycles > fiche.availability.observed_cycles && (
            <>
              {' '}sur {fmtInt(fiche.availability.total_cycles)} :{' '}
              {fmtInt(fiche.availability.total_cycles - fiche.availability.observed_cycles)}
              {' '}n’ont porté aucune lecture de tunnel et sont EXCLUS du compte.
              Les y inclure prendrait l’absence de regard pour une panne — sur une
              halte de la base, cela faisait passer la disponibilité de 99,8 % à
              63,8 %, soit une heure et demie de panne qui n’a pas eu lieu.
            </>
          )}
          {fiche.availability.total_cycles === fiche.availability.observed_cycles && (
            <>, tous observés.</>
          )}
        </p>
      )}
      {fiche.diversity && fiche.diversity.cycles > 0 && (
        <p className="panel-note">
          <b>Trajets radio partagés</b> —{' '}
          {fiche.diversity.same_site_pct === null
            ? 'station indéterminable sur cette session'
            : `même station ${fiche.diversity.same_site_pct} % du temps${surCycles(fiche.diversity.site_cycles)}`},{' '}
          {fiche.diversity.same_cell_pct === null
            ? 'cellule inconnue'
            : `même cellule ${fiche.diversity.same_cell_pct} %${surCycles(fiche.diversity.cell_cycles)}`},{' '}
          {fiche.diversity.same_earfcn_pct === null
            ? 'fréquence inconnue'
            : `même fréquence ${fiche.diversity.same_earfcn_pct} %${surCycles(fiche.diversity.earfcn_cycles)}`}.
          {fiche.diversity.operators === 1 && (
            <> Un seul opérateur : une panne de son cœur de réseau emporterait
            tous les liens à la fois, et aucune agrégation n’y changerait rien.</>
          )}
          {' '}Chaque part a son propre dénominateur : un cycle où l’un des liens
          ne publie pas le champ n’est pas un cycle sans partage, c’est un cycle
          qu’on ne sait pas juger.
        </p>
      )}
      </Notes>
      {fiche.cells.length > 0 && (
        <div className="scroll">
          <table>
            <caption className="sr-only">
              Latence par cellule occupée pendant cette session.
            </caption>
            <thead>
              <tr>
                <th scope="col">Lien</th>
                <th scope="col">Cellule</th><th scope="col">Station</th>
                <th scope="col">Relevés</th>
                <th scope="col">RTT médian</th><th scope="col">RTT p90</th>
                <th scope="col">RTT p99</th>
              </tr>
            </thead>
            <tbody>
              {fiche.cells.map((c) => (
                <tr key={`${c.wan_id}/${c.plmn ?? ''}/${c.cell_id}`}>
                  <td>{fmtId(c.wan_id)}</td>
                  <td>{fmtId(c.cell_id)}</td>
                  <td>{fmtId(c.enb_id)}</td>
                  <td className="num">{fmtInt(c.samples)}</td>
                  <td className="num">{c.rtt_p50_ms ?? '—'}</td>
                  <td className="num">{c.rtt_p90_ms ?? '—'}</td>
                  <td className="num">{c.rtt_p99_ms ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Notes>
      {fiche.cells.length > 0 && (
        <p className="panel-note">
          La latence coupée par CELLULE, parce que la médiane d’un lien ne bouge
          presque pas quand sa queue se multiplie par quatre. Mesuré sur une
          cellule tenue huit heures : le signal varie de 1,4 dB, la médiane passe
          de 31 à 40 ms — et le p90 de 119 à 435 ms. C’est la queue qui fait geler
          une image. Seules les cellules ayant réuni au moins deux cents relevés
          figurent ici ; en dessous, un p90 désigne l’avant-dernière valeur d’une
          poignée de mesures. Et le routeur republie parfois la même mesure sur
          deux cycles : le nombre de relevés surestime la solidité du chiffre.
        </p>
      )}
      </Notes>
      {/* Une fiche muette sur la fenêtre qu'elle a lue se lit comme une
          fiche complète — or volumes, coupures et barycentre sont tous des
          totaux. Une session de trois semaines ne se relit pas en entier. */}
      {fiche.partial && fiche.window_start && (
        <div className="notice is-warn">
          Session trop longue pour être relue en entier : cette fiche ne porte que
          sur la fenêtre du {new Date(fiche.window_start).toLocaleString('fr-FR')} au{' '}
          {new Date(fiche.window_end ?? fiche.session.last_seen).toLocaleString('fr-FR')}.
          Volumes, coupures et cellules ne comptent que cette fenêtre.
        </div>
      )}
      <p className="disclaimer">
        Volumes intégrés depuis les débits relevés : le routeur ne publie qu’un cumul
        depuis son installation, qui ne borne aucune session. À {' '}
        {Math.round((new Date(fiche.session.last_seen).getTime() -
          new Date(fiche.session.started_at).getTime()) / 60000)} minutes de session et
        une mesure
        {periodSeconds === null
          ? ' à la cadence de scrutation'
          : ` toutes les ${decimal(periodSeconds, 1)} s`}
        , un pic plus court passe entre les mailles — c’est un ordre de grandeur.
      </p>
    </>
  )
}
