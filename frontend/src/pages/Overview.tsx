import { useMemo } from 'react'

import { Alerts } from '../components/Alerts'
import { Note, Notes } from '../components/Note'
import { RepriseIdentifiants } from '../components/RepriseIdentifiants'
import { SansMesure } from '../components/SansMesure'
import { BondPlan } from '../components/BondPlan'
import { DeviceCard } from '../components/DeviceCard'
import { EventLog } from '../components/EventLog'
import { LinkTable } from '../components/LinkTable'
import { pathDiversity } from '../lib/diversity'
import { bondRedundancy } from '../lib/redundancy'
import { linkVerdict } from '../lib/verdict'
import { keyFigures } from '../lib/dashboard'
import { compareWanIds } from '../lib/wanOrder'
import { fmtUptime } from '../lib/format'
import { useDevice } from './DeviceShell'

/**
 * Le tableau de bord : trois questions, dans l'ordre où elles se posent.
 *
 *  1. **Qu'est-ce qui porte ?** — le plan du bond. La convergence de N chemins
 *     physiques vers une liaison est le sujet de cet outil ; une liste de tuiles
 *     n'en exprimait rien et laissait croire à des objets indépendants posés
 *     côte à côte. Sur le matériel de référence, une fibre transporte la
 *     totalité du flux pendant que quatre SIM attendent à zéro : cinq tuiles de
 *     même taille racontaient exactement le contraire.
 *
 *  2. **Que s'est-il passé pendant que j'étais là ?** — la bande de
 *     stationnement. L'équipement n'est alimenté que véhicule à l'arrêt, donc
 *     chaque mise sous tension est une intervention. Ce concept, central dans le
 *     modèle de données, vivait dans un menu déroulant coincé dans la barre
 *     d'outils d'un graphique.
 *
 *  3. **Lequel décroche ?** — la table. Aligner quatre SINR en colonne les rend
 *     comparables d'un coup d'œil ; les répartir dans quatre tuiles oblige à
 *     balayer l'écran en zigzag pour répondre à la question la plus fréquente.
 *
 * L'identité du matériel vient en dernier, et c'est délibéré : le numéro de
 * série ne se lit pas pendant une intervention.
 */
export function Overview() {
  const {
    routerId, router, samples, showThroughput, hasTunnel, status, ageSeconds,
    timeline, events, periodSeconds, health, role, windowLabel, sessions,
  } = useDevice()

  // Mémorisées sur `samples`. Ces trois dérivations rendent des objets NEUFS à
  // chaque rendu — dont deux `Map` —, et elles descendent en props : sans cela,
  // aucune mémorisation en aval ne peut faire mouche, puisque ses dépendances
  // changent d'identité à chaque fois. Le tableau se rafraîchit à la cadence de
  // la sonde, et chaque relevé de WAN arrive dans son propre message : un cycle
  // produit donc autant de rendus qu'il y a de liens.
  const diversite = useMemo(() => pathDiversity(samples), [samples])
  // Établie sur toute la fenêtre relue, pas sur l'instant : un tunnel au repos
  // n'échange qu'un keepalive, et n'y voir aucun paquet réparé ne prouverait rien.
  const redondance = bondRedundancy(timeline)
  // Le rôle du bond est établi une fois pour toutes dans `DeviceShell` : lui
  // seul dispose des deux témoins — ce que le pair annonce, et ce qui transite.
  // Les griefs du tunnel que le bandeau a refusés, pour les dire auprès du bond.
  // Recalculés ici plutôt que remontés du bandeau : la fonction est pure, son
  // coût tient dans quelques filtres sur onze relevés, et la faire redescendre
  // à travers deux composants pour économiser cela aurait couplé la tête de
  // page au panneau du bond.
  // Avec l'ÂGE du relevé : sans lui, les griefs restaient affichés auprès du
  // bond sur un relevé que le bandeau, lui, déclarait périmé.
  const { bondGrievances: griefsDuTunnel, radioGrievances: griefsRadio } = linkVerdict({
    samples, status, ageSeconds, periodSeconds,
    role,
    latencyThresholdMs: router?.latency_threshold_ms ?? null,
    tunnelAttendu: router?.tunnel_attendu ?? null,
    neverConnected: router?.never_connected ?? [],
  })
  const chiffres = useMemo(() => keyFigures(samples), [samples])
  // Ordre STABLE, et rien d'autre. Trier les liens par ce qu'ils portent
  // paraissait servir la lecture ; en réalité un lien qui décroche remonte ou
  // descend dans la liste, et toute la colonne se décale sous l'œil. La
  // certitude spatiale est ce qui permet le coup d'œil : quand on ignore OÙ un
  // événement va apparaître, on doit balayer l'écran au lieu de le consulter.
  // Une panne se signale SUR PLACE — c'est aussi la règle des planches
  // d'instruments, où le drapeau remplace la valeur à son emplacement exact.
  const ordonnes = useMemo(
    () => [...samples].sort((a, b) => compareWanIds(a.wan.wan_id, b.wan.wan_id)),
    [samples],
  )

  // Ce que la fenêtre relue COUVRE, dit en clair. « Stationnement en cours » ne
  // désignait rien pour qui n'a pas le modèle de données en tête, et le mot
  // était faux dès que le véhicule roulait : c'est une durée depuis la mise sous
  // tension, et c'est tout ce qu'on en sait.
  const fenetre = windowLabel

  // Depuis combien de temps le châssis est sous tension. La valeur vient du
  // ROUTEUR, et de lui seul.
  // AUCUN repli sur une soustraction d'horloges. Le commentaire ci-dessus
  // affirmait que le repli était « lui-même ancré sur la même mesure côté
  // serveur » ; il ne l'était pas : `Date.now()` est l'horloge du navigateur et
  // `started_at` celle du serveur, c'est-à-dire exactement le défaut que ce
  // projet a corrigé pour l'âge des relevés. Et la dérive rendait parfois le
  // résultat négatif, que la condition d'affichage masquait en silence.
  //
  // Un équipement qui ne publie pas son ancienneté ne la publie pas : la ligne
  // disparaît plutôt que d'afficher une valeur fabriquée ici.
  const sousTension = router?.device?.uptime_s ?? null

  return (
    <div className="page">
      <RepriseIdentifiants routerId={routerId} status={status} />

      {samples.length === 0 ? (
        <SansMesure status={status} sessions={sessions} />
      ) : (
        <>
          {/* L'ordre suit les questions : ce qui porte, puis lequel décroche,
              puis ce qui s'est passé. La bande de temps a rejoint la table des
              liens — elle en répétait exactement les lignes, une par lien, dans
              le même ordre et avec la même identité à gauche. */}
          {/* Avant le plan du bond : c'est ce qui demande une décision, et le reste
              de l'écran décrit un état. Le panneau n'existe pas quand il n'y a rien
              à dire — un cadre vide intitulé « Alertes » est un cadre qu'on cesse de
              regarder, et il emporte avec lui les fois où il aurait compté. */}
          <Alerts alerts={router?.alerts ?? []} />

          <BondPlan
            samples={ordonnes}
            figures={chiffres}
            redundancy={redondance}
            cells={diversite.cells}
            sites={diversite.sites}
            located={diversite.located}
            attached={diversite.attached}
            role={role}
            tunnelAttendu={router?.tunnel_attendu ?? null}
            grievances={griefsDuTunnel}
            routerId={routerId}
            cellGroups={diversite.cellGroups}
            tunnelUptimeS={router?.tunnel_uptime_s ?? null}
            throughputLimitMbps={router?.capabilities?.sfc_throughput_limit_mbps ?? null}
          />

          <LinkTable
            routerId={routerId}
            samples={ordonnes}
            showThroughput={showThroughput}
            hasTunnel={hasTunnel}
            sameCell={diversite.sameCell}
            timeline={timeline}
            windowLabel={fenetre}
            churn={events?.churn ?? []}
            latencyThresholdMs={router?.latency_threshold_ms ?? null}
            radioGrievances={griefsRadio}
            tunnelAttendu={router?.tunnel_attendu ?? null}
          />

          {/* Le journal et la fiche technique se partagent la dernière rangée :
              l'un est long et étroit, l'autre courte et large, et les empiler
              coûtait deux cents pixels pour rien. */}
          <div className="grid-side">
            <EventLog log={events} windowLabel={fenetre} />
            <DeviceCard
              device={router?.device ?? null}
              capabilities={router?.capabilities ?? null}
              siteLat={router?.site_lat ?? null}
              siteLon={router?.site_lon ?? null}
              periodSeconds={periodSeconds}
              cycleSeconds={router?.cycle_seconds ?? null}
              intervalSeconds={health?.poll_interval_seconds ?? null}
              lanClients={router?.lan_clients ?? null}
            />
          </div>

          {/* Le partage de cellule est un FAIT, et il est actionnable justement
              parce que le véhicule est à l'arrêt. Il n'est énoncé que lorsqu'il
              a lieu : sur ce matériel, 100 % des minutes où deux SIM sont
              connectées ont un partage de STATION, et un avertissement allumé
              en permanence est du papier peint — au bout de deux jours on cesse
              de le lire, et on cesse aussi de lire les autres. */}
          <Notes>
{diversite.located > 1 && diversite.cells < diversite.located && (
            <Note
              ton="alerte"
              pourquoi={
                "Même secteur, même porteuse, mêmes ressources radio : la capacité ne "
                + "s'additionne pas et le rapport signal/bruit s'en ressent, même quand la "
                + "puissance reçue est excellente. Déplacer le véhicule ou forcer une autre "
                + "bande est la seule action qui change ce fait."
                + (diversite.unknown > 0
                    ? ` ${diversite.unknown} lien(s) ne rapportent pas leur cellule et restent hors de ce compte.`
                    : "")
              }
            >
              <strong>{diversite.largestSharedCell} modems sur une même cellule.</strong>
            </Note>
          )}

          {sousTension !== null && sousTension > 0 && (
            <Note pourquoi={
              "L'équipement n'est alimenté que véhicule à l'arrêt : cette durée est celle "
              + "du stationnement, et c'est la fenêtre de tout ce qui est relu ci-dessus."
            }>
              Sous tension depuis {fmtUptime(sousTension)}.
            </Note>
          )}
</Notes>
        </>
      )}

      {/* Sans le moindre relevé, la fiche technique est tout ce qu'on peut
          montrer : elle reste. */}
      {samples.length === 0 && (
        <DeviceCard
          device={router?.device ?? null}
          capabilities={router?.capabilities ?? null}
          siteLat={router?.site_lat ?? null}
          siteLon={router?.site_lon ?? null}
          periodSeconds={periodSeconds}
          cycleSeconds={router?.cycle_seconds ?? null}
          intervalSeconds={health?.poll_interval_seconds ?? null}
          lanClients={router?.lan_clients ?? null}
        />
      )}
    </div>
  )
}
