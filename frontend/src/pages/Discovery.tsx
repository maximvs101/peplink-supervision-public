import { ThemeToggle } from '../components/ThemeToggle'
import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import {
  fetchDevices, fetchDiscovery, fetchKnownDevices, reconnectKnownDevice, unpairDevice,
} from '../api/client'
import { PairingForm } from '../components/PairingForm'
import type { DeviceStatus, DiscoveredDevice, KnownDevice } from '../types'
import { QUALITY_COLOR } from '../lib/quality'
import { Notes, Absent } from '../components/Note'
import { hoteCanonique } from '../lib/routes'

/** Couleur associée à chaque état, empruntée à la qualité : c'est la seule
 *  famille chromatique de l'interface. */
const STATUS_COLOR: Record<string, string> = {
  connecté: QUALITY_COLOR.excellent,
  'en attente': QUALITY_COLOR.inconnu,
  injoignable: QUALITY_COLOR.moyen,
  'identifiants refusés': QUALITY_COLOR.faible,
  'erreur de configuration': QUALITY_COLOR.faible,
}

/** Chaque état porte son REMÈDE, et c'est tout l'intérêt de les distinguer :
 *  « injoignable » envoie vérifier un raccordement, « erreur de configuration »
 *  envoie corriger une déclaration. Les confondre fait déplacer un technicien
 *  pour rien. L'écran affichait l'état nu, sans jamais dire quoi en faire. */
const STATUS_REMEDY: Record<string, string> = {
  'en attente': 'Première lecture en cours.',
  injoignable: 'Vérifier le raccordement et l’adresse.',
  'identifiants refusés': 'Corriger les identifiants, puis relancer une tentative — une seule.',
  'erreur de configuration': 'Corriger la déclaration de l’équipement.',
}

/**
 * Les équipements : ce qui est supervisé, ce qui est sur le réseau, ce qu'on a déjà vu.
 *
 * L'ordre a été inversé. L'écran présentait d'abord ce que la recherche venait
 * de trouver et reléguait en dernier ce qui est RÉELLEMENT supervisé — c'est-à-dire
 * la seule chose qui produise des mesures. On ouvre donc sur la flotte en service,
 * et la découverte vient après, comme le moyen de l'agrandir.
 *
 * Les trois blocs étaient trois cartes de poids identique remplies de listes à
 * puces, où le nom, l'adresse, le firmware et la date se suivaient dans une seule
 * phrase. Ce sont maintenant des tables : mêmes colonnes, mêmes chiffres
 * tabulaires et même rail de couleur que la table des liens — un équipement se
 * compare à un autre comme un lien se compare à un lien.
 */
export function Discovery() {
  const [devices, setDevices] = useState<DiscoveredDevice[] | null>(null)
  const [supervised, setSupervised] = useState<DeviceStatus[]>([])
  const [connus, setConnus] = useState<KnownDevice[]>([])
  // La reconnexion par le trousseau en vol (série), et son dernier échec.
  const [reconnexion, setReconnexion] = useState<string | null>(null)
  const [reconnexionEchec, setReconnexionEchec] = useState<{ serie: string; message: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Le retrait a son propre état : son échec s'affichait à la place de la
  // liste des DÉTECTÉS, deux panneaux plus bas, et effaçait ce qui n'avait
  // rien à voir. Et il se confirme : un clic sans retour arrière, dont l'effet
  // immédiat est un 404 sur tous les autres onglets, mérite un second clic.
  const [retraitErreur, setRetraitErreur] = useState<string | null>(null)
  const [aRetirer, setARetirer] = useState<string | null>(null)
  // Un échec de lecture n'est PAS une liste vide. `fetchDevices` échouait en
  // silence, `supervised` restait à `[]`, et l'écran affirmait « Aucun
  // équipement en supervision » — une absence de réponse présentée comme un
  // fait. Pire, il proposait alors « Mettre en supervision » sur un équipement
  // que le serveur venait de marquer comme déjà supervisé.
  const [supervisedError, setSupervisedError] = useState<string | null>(null)
  const [connusError, setConnusError] = useState<string | null>(null)
  const [searching, setSearching] = useState(false)
  const [pairing, setPairing] = useState<string | null>(null)
  const navigate = useNavigate()

  const refresh = useCallback(() => {
    fetchDevices()
      .then((liste) => { setSupervised(liste); setSupervisedError(null) })
      .catch((err) =>
        setSupervisedError(err instanceof Error ? err.message : 'Lecture impossible'),
      )
  }, [])

  const search = useCallback(() => {
    setSearching(true)
    setError(null)
    fetchDiscovery()
      .then(setDevices)
      .catch((err) => setError(err instanceof Error ? err.message : 'Recherche impossible'))
      .finally(() => setSearching(false))
    refresh()
  }, [refresh])

  useEffect(() => {
    fetchKnownDevices()
      .then((liste) => { setConnus(liste); setConnusError(null) })
      .catch((err) => setConnusError(err instanceof Error ? err.message : 'Lecture impossible'))
  }, [])

  useEffect(search, [search])

  const supervisedHosts = new Set(supervised.map((d) => hoteCanonique(d.host)).filter(Boolean))
  // De l'hôte détecté à l'équipement supervisé qui l'occupe : « déjà supervisé »
  // était une étiquette morte, et l'exploitant cherchait le tableau de bord
  // derrière elle. Elle mène désormais à l'équipement.
  const supervisePar = new Map(
    supervised.flatMap((d) => (d.host ? [[hoteCanonique(d.host), d.id] as const] : [])),
  )
  // Le formulaire d'appairage se déplie dans la table des équipements DÉTECTÉS :
  // proposer « Reconnecter » sur un équipement que la recherche ne voit pas
  // ouvrirait un formulaire invisible, et le bouton ne ferait rien du tout.
  const detectes = new Set((devices ?? []).map((d) => hoteCanonique(d.host)))

  // Le formulaire, ouvert sur l'hôte tel que la DÉTECTION l'écrit, pas sur
  // celui que la mémoire a gardé : il s'ouvre sur `pairing === device.host`, et
  // une comparaison sur l'hôte brut pouvait n'ouvrir aucun formulaire
  // (`10.10.1.1` retenu, `http://10.10.1.1` détecté).
  const ouvrirFormulaire = (host: string) => setPairing(
    (devices ?? []).find((x) => hoteCanonique(x.host) === hoteCanonique(host))?.host ?? host,
  )

  // Avec des identifiants retenus : UNE tentative, sans formulaire. Un refus
  // ouvre le formulaire pour corriger — ce qui y sera saisi remplacera l'entrée.
  const reconnecter = (d: KnownDevice) => {
    if (reconnexion !== null) return
    setReconnexion(d.serial)
    setReconnexionEchec(null)
    reconnectKnownDevice(d.serial)
      .then((paire) => {
        refresh()
        navigate(`/e/${encodeURIComponent(paire.id)}`)
      })
      .catch((err: Error & { credentials?: boolean; status?: number }) => {
        setReconnexionEchec({ serie: d.serial, message: err.message })
        if ((err.credentials || err.status === 409) && d.host && detectes.has(hoteCanonique(d.host))) {
          ouvrirFormulaire(d.host)
        }
      })
      .finally(() => setReconnexion(null))
  }

  return (
    <>
      {/* Le bandeau de tête appartient à l'équipement observé, et il n'y en a
          pas ici : cet écran porte le sien, réduit à ce qu'il est et au chemin
          du retour. */}
      <header className="mast is-plain">
        <div className="mast-id">
          <b>Équipements</b>
          <span className="num">supervision · découverte · appairage</span>
        </div>
        <span className="spacer" />
        <div className="mast-tools">
          <ThemeToggle />
          <Link className="tag" to="/">‹ Retour à la supervision</Link>
        </div>
      </header>

      <div className="page is-narrow">
        {/* 1. CE QUI EST SUPERVISÉ — la seule chose qui produise des mesures. */}
        <section className="panel">
          <div className="panel-head">
            <h2>En supervision</h2>
            <span className="note">
              {supervisedError !== null
                ? 'liste illisible'
                : supervised.length === 0
                  ? 'aucun équipement'
                  : supervised.length === 1
                    ? 'un équipement'
                    : `${supervised.length} équipements`}
            </span>
          </div>

          {supervisedError !== null ? (
            <div className="panel-body">
              {/* Une absence de réponse n'est pas une liste vide : l'écran
                  affirmait « Aucun équipement en supervision » et proposait
                  d'appairer des équipements que le serveur venait de marquer
                  comme déjà supervisés. */}
              <div className="notice is-error">
                <strong>La liste des équipements supervisés n’a pas pu être lue.</strong>{' '}
                {supervisedError}. Ce qui suit ne dit donc pas ce qui est déjà en service :
                appairer un équipement déjà supervisé le remplacerait.
              </div>
            </div>
          ) : supervised.length === 0 ? (
            <div className="panel-body">
              <div className="notice">
                Aucun équipement en supervision. Ceux détectés ci-dessous peuvent être
                mis en service.
              </div>
            </div>
          ) : (
            <div className="ltable-scroll">
              {retraitErreur && (
                <div className="notice is-error" role="alert">{retraitErreur}</div>
              )}
              <table className="ltable">
                <thead>
                  <tr>
                    <th className="l">Équipement</th>
                    <th className="l">État</th>
                    <th className="l">Adresse</th>
                    <th className="l">Numéro de série</th>
                    <th className="l">Modèle</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {supervised.map((device) => (
                    <tr key={device.id}>
                      <td className="l">
                        <span className="who">
                          <span
                            className="rail"
                            style={{
                              ['--q' as string]:
                                STATUS_COLOR[device.status] ?? QUALITY_COLOR.inconnu,
                            }}
                          />
                          <span style={{ minWidth: 0 }}>
                            <b>
                              <Link to={`/e/${encodeURIComponent(device.id)}`}>{device.name}</Link>
                            </b>
                            <span className="sub">{device.id}</span>
                          </span>
                        </span>
                      </td>
                      <td className="l">
                        <span className="tag">{device.status}</span>
                        {device.mode === 'mock' && <> <span className="tag">simulateur</span></>}
                        {/* Le remède, pas seulement le constat. */}
                        {STATUS_REMEDY[device.status] && (
                          <div className="sub-note">{STATUS_REMEDY[device.status]}</div>
                        )}
                        {device.error && <div className="sub-note">{device.error}</div>}
                      </td>
                      <td className="l num">{device.host ?? <Absent />}</td>
                      <td className="l num">
                        {device.serial ?? <Absent />}
                      </td>
                      <td className="l">
                        {device.device?.model ?? <Absent />}
                      </td>
                      <td>
                        {/* Un équipement venu du fichier de flotte ne se retire
                            pas depuis l'écran : il reviendrait au redémarrage. */}
                        {device.source === 'paired' && aRetirer !== device.id && (
                          <button className="is-quiet" onClick={() => setARetirer(device.id)}>
                            Retirer
                          </button>
                        )}
                        {device.source === 'paired' && aRetirer === device.id && (
                          <>
                            <button
                              onClick={() => {
                                setARetirer(null)
                                setRetraitErreur(null)
                                unpairDevice(device.id)
                                  .then(refresh)
                                  // Un retrait qui échoue ne disait rien : ni
                                  // message, ni bouton rendu inactif. L'exploitant
                                  // croyait l'équipement retiré.
                                  .catch((err) =>
                                    setRetraitErreur(
                                      err instanceof Error
                                        ? `Retrait de ${device.name} impossible : ${err.message}`
                                        : `Retrait de ${device.name} impossible`,
                                    ),
                                  )
                              }}
                            >
                              Confirmer le retrait
                            </button>{' '}
                            <button className="is-quiet" onClick={() => setARetirer(null)}>
                              Annuler
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

<Notes>
          <p className="panel-note">
            Les identifiants ne sont jamais écrits en clair. Avec « Retenir sur ce
            poste », ils vont au trousseau chiffré du système, et l’équipement se
            reconnecte au prochain démarrage ; sinon, ils sont à ressaisir.
          </p>
</Notes>
        </section>

        {/* 2. CE QUI EST SUR LE RÉSEAU */}
        <section className="panel">
          <div className="panel-head">
            <h2>Détectés sur le réseau</h2>
            <span className="note">
              {devices === null ? 'sondage en cours…' : `${devices.length} répondant`}
            </span>
            <span className="spacer" />
            <button onClick={search} disabled={searching}>
              {searching ? 'Recherche…' : 'Relancer la recherche'}
            </button>
          </div>

          {error ? (
            <div className="panel-body">
              <div className="notice is-error">{error}</div>
            </div>
          ) : devices === null ? (
            <div className="panel-body">
              <div className="notice">Sondage du réseau…</div>
            </div>
          ) : devices.length === 0 ? (
            <div className="panel-body">
              <div className="notice">
                Aucun équipement Peplink trouvé. La recherche couvre la passerelle par
                défaut, les bornes usuelles du sous-réseau et les adresses d’usine — pas
                un balayage complet. Si l’équipement est ailleurs, son adresse peut être
                saisie dans <code>backend/routers.yaml</code>.
              </div>
            </div>
          ) : (
            <div className="ltable-scroll">
              <table className="ltable">
                <thead>
                  <tr>
                    <th className="l">Adresse</th>
                    <th className="l">API locale</th>
                    <th className="l">Firmware</th>
                    <th className="l">Ce qui l’a identifié</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {devices.map((device) => {
                    // Le serveur publie `already_configured` sur chaque
                    // équipement détecté — un champ émis, déclaré dans les types,
                    // et lu par personne : l'écran recalculait la même chose à sa
                    // façon. On retient les DEUX, parce qu'ils ne vieillissent pas
                    // ensemble : le drapeau date de la découverte et ne bouge plus,
                    // la liste supervisée est relue après chaque appairage. Le
                    // premier des deux qui dit « déjà connu » a raison.
                    const deja = device.already_configured || supervisedHosts.has(hoteCanonique(device.host))
                    const ouvert = pairing === device.host
                    return [
                      <tr key={device.host}>
                        <td className="l">
                          <span className="who">
                            <span
                              className="rail"
                              style={{
                                ['--q' as string]: device.api_reachable
                                  ? QUALITY_COLOR.excellent
                                  : QUALITY_COLOR.moyen,
                              }}
                            />
                            <span style={{ minWidth: 0 }}>
                              <b className="num">{device.host}</b>
                              <span className="sub">
                                {device.mac ?? device.certificate_name ?? 'Peplink'}
                              </span>
                            </span>
                          </span>
                        </td>
                        <td className="l">
                          {device.api_reachable ? (
                            <span className="tag is-strong">répond</span>
                          ) : (
                            <>
                              <span className="tag is-watch">muette</span>
                              <div className="sub-note">
                                Reconnu, mais non supervisable tant que l’API locale ne
                                répond pas.
                              </div>
                            </>
                          )}
                        </td>
                        <td className="l num">
                          {device.firmware ?? <Absent />}
                        </td>
                        <td className="l">
                          <span className="sub-note">{device.evidence.join(' · ')}</span>
                        </td>
                        <td>
                          {deja ? (
                            supervisePar.has(hoteCanonique(device.host)) ? (
                              <Link
                                className="tag"
                                to={`/e/${encodeURIComponent(supervisePar.get(hoteCanonique(device.host))!)}`}
                              >
                                déjà supervisé › ouvrir
                              </Link>
                            ) : (
                              <span className="tag">déjà supervisé</span>
                            )
                          ) : device.api_reachable ? (
                            <button
                              className={ouvert ? 'is-quiet' : 'is-primary'}
                              onClick={() => setPairing(ouvert ? null : device.host)}
                            >
                              {ouvert ? 'Fermer' : 'Mettre en supervision'}
                            </button>
                          ) : null}
                        </td>
                      </tr>,
                      ouvert ? (
                        <tr key={`${device.host}-form`} className="is-form">
                          <td className="l" colSpan={5}>
                            <PairingForm
                              host={device.host}
                              onCancel={() => setPairing(null)}
                              /* L'objet rendu par l'appairage était jeté, et la
                                 liste des routeurs n'était rafraîchie nulle
                                 part : le nouvel équipement n'apparaissait ni
                                 dans le sélecteur ni à l'ouverture. On va donc
                                 directement sur ses mesures, qui sont la raison
                                 de l'avoir appairé. */
                              onPaired={(paire) => {
                                setPairing(null)
                                refresh()
                                navigate(`/e/${encodeURIComponent(paire.id)}`)
                              }}
                            />
                          </td>
                        </tr>
                      ) : null,
                    ]
                  })}
                </tbody>
              </table>
            </div>
          )}

<Notes>
          <p className="panel-note">
            Aucun identifiant n’est envoyé pendant la recherche : elle se limite aux deux
            points d’accès lisibles sans session, au certificat et au cache ARP.
          </p>
</Notes>
        </section>

        {/* 3. CE QU'ON A DÉJÀ VU DEPUIS CE POSTE */}
        {/* Un panneau ABSENT se lit comme un panneau vide : quand la lecture
            échoue, le dire plutôt que d'escamoter la section entière. */}
        {connusError !== null && (
          <section className="panel">
            <div className="panel-head"><h2>Déjà vus depuis ce poste</h2></div>
            <div className="panel-body">
              <div className="notice is-error">
                Historique des équipements illisible : {connusError}.
              </div>
            </div>
          </section>
        )}
        {connus.length > 0 && (
          <section className="panel">
            <div className="panel-head">
              <h2>Déjà vus depuis ce poste</h2>
              <span className="note">{connus.length}</span>
            </div>
            {reconnexionEchec !== null && (
              <div className="panel-body">
                <div className="notice is-error" role="alert">
                  Reconnexion impossible : {reconnexionEchec.message}
                </div>
              </div>
            )}
            <div className="ltable-scroll">
              <table className="ltable">
                <thead>
                  <tr>
                    <th className="l">Équipement</th>
                    <th className="l">Adresse</th>
                    <th className="l">Firmware</th>
                    <th className="l">Vu le</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {connus.map((d) => (
                    <tr key={d.serial}>
                      <td className="l">
                        <span className="who">
                          <span className="rail" style={{ ['--q' as string]: 'var(--rule)' }} />
                          <span style={{ minWidth: 0 }}>
                            <b>{d.name ?? d.model ?? d.serial}</b>
                            <span className="sub">
                              {d.serial}{d.credentials_stored ? ' · identifiants retenus' : ''}
                            </span>
                          </span>
                        </span>
                      </td>
                      <td className="l num">{d.host ?? <Absent />}</td>
                      <td className="l num">
                        {d.firmware ?? <Absent />}
                      </td>
                      <td className="l num">
                        {new Date(d.last_seen).toLocaleString('fr-FR')}
                      </td>
                      <td>
                        {d.host && d.credentials_stored && !supervisedHosts.has(hoteCanonique(d.host)) ? (
                          <button
                            className="is-quiet"
                            disabled={reconnexion !== null}
                            onClick={() => reconnecter(d)}
                          >
                            {reconnexion === d.serial ? 'Connexion…' : 'Reconnecter'}
                          </button>
                        ) : d.host && detectes.has(hoteCanonique(d.host)) ? (
                          <button className="is-quiet" onClick={() => ouvrirFormulaire(d.host!)}>
                            Reconnecter
                          </button>
                        ) : (
                          <span className="sub-note">
                            {supervisedHosts.has(hoteCanonique(d.host))
                              ? 'déjà supervisé'
                              : 'absent du réseau'}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
<Notes>
            <p className="panel-note">
              Le mot de passe n’est jamais dans cette liste : il est au trousseau chiffré
              du système quand vous l’avez voulu (« identifiants retenus »), à ressaisir
              sinon. Le reste est là, pour que l’exploitant qui rallume au matin retrouve
              son équipement sans tout retaper.
            </p>
</Notes>
          </section>
        )}
      </div>
    </>
  )
}
