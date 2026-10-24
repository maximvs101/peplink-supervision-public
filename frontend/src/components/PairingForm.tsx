import { useState } from 'react'

import { pairDevice } from '../api/client'
import type { DeviceStatus } from '../types'
import { sansTls } from '../lib/routes'

interface Props {
  host: string
  onPaired: (device: DeviceStatus) => void
  onCancel: () => void
}

/**
 * Saisie des identifiants d'un équipement, pour une tentative et une seule.
 *
 * Le formulaire ne réessaie jamais tout seul : cinq tentatives ratées
 * suspendent l'accès à l'équipement pendant cinq minutes, y compris avec les
 * bons identifiants. C'est aussi pourquoi l'échec distingue « identifiants
 * refusés » de « injoignable » — le premier consomme une tentative, le second non.
 */
export function PairingForm({ host, onPaired, onCancel }: Props) {
  // Deux modes d'authentification. Le client API est le SEUL qui n'expose pas le
  // compte admin de l'équipement : il se crée depuis l'administration web, en
  // lecture seule, et se révoque sans changer de mot de passe. Le backend le
  // gérait depuis le début — validé sur matériel, trois formes d'envoi testées —
  // mais le formulaire ne le proposait pas : il était inatteignable.
  const [mode, setMode] = useState<'admin' | 'client'>('admin')
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [verifierCert, setVerifierCert] = useState(false)
  // Cochée par défaut : le trousseau du système est le coffre chiffré qu'on
  // attendait pour ne plus tout ressaisir à chaque lancement (octobre 2026).
  const [retenir, setRetenir] = useState(true)
  const [error, setError] = useState<{ message: string; credentials: boolean } | null>(null)

  // Le mode CLIENT était inatteignable : le formulaire affichait ses deux
  // champs, mais l'envoi reprenait `username`/`password` — donc « admin » et une
  // chaîne vide — et le bouton était désactivé tant que le mot de passe admin
  // était vide, ce qu'il restait par construction dans ce mode. Le seul mode qui
  // n'expose pas le compte admin de l'équipement ne pouvait pas être employé.
  const complet = mode === 'client' ? Boolean(clientId && clientSecret) : Boolean(password)

  // Changer de mode PURGE le secret de l'autre. Ils vivaient dans deux états
  // distincts, jamais vidés : un mot de passe administrateur saisi puis
  // abandonné au profit du mode client restait en mémoire du navigateur pour
  // toute la vie de la page. Il n'était plus envoyé — mais il n'avait plus
  // aucune raison d'exister, et ce projet ne garde aucun identifiant plus
  // longtemps qu'il n'en a besoin.
  const changerDeMode = (suivant: 'admin' | 'client') => {
    if (suivant === mode) return
    setMode(suivant)
    setPassword('')
    setClientSecret('')
    setError(null)
  }

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    // Une seule tentative en vol. Le bouton est bien désactivé pendant l'envoi,
    // mais la touche Entrée dans un champ soumet le formulaire sans passer par
    // lui : deux appuis rapides lançaient DEUX appairages. Or cinq tentatives
    // ratées suspendent l'accès à l'équipement pendant cinq minutes — c'est la
    // règle dont tout ce module découle, et un double envoi en consommait deux.
    if (busy) return
    setBusy(true)
    setError(null)
    pairDevice(
      mode === 'client'
        ? { host, client_id: clientId, client_secret: clientSecret,
            name: name || undefined, verify_ssl: verifierCert, remember: retenir }
        : { host, username, password, name: name || undefined, verify_ssl: verifierCert,
            remember: retenir },
    )
      .then(onPaired)
      .catch((err: Error & { credentials?: boolean }) =>
        setError({ message: err.message, credentials: Boolean(err.credentials) }),
      )
      .finally(() => setBusy(false))
  }

  return (
    <form className="pairing" onSubmit={submit}>
      <div className="segmented">
        <button type="button" className={mode === 'admin' ? 'is-active' : ''}
                onClick={() => changerDeMode('admin')} disabled={busy}>
          Compte admin
        </button>
        <button type="button" className={mode === 'client' ? 'is-active' : ''}
                onClick={() => changerDeMode('client')} disabled={busy}>
          Client API (lecture seule)
        </button>
      </div>

      {mode === 'client' ? (
        <div className="pairing-fields">
          <label>
            <span className="field-label">clientId</span>
            <input value={clientId} onChange={(e) => setClientId(e.target.value)}
                   autoComplete="off" disabled={busy} autoFocus required />
          </label>
          <label>
            <span className="field-label">clientSecret</span>
            <input type="password" value={clientSecret}
                   onChange={(e) => setClientSecret(e.target.value)}
                   autoComplete="off" disabled={busy} required />
          </label>
          <label>
            <span className="field-label">Nom (facultatif)</span>
            <input value={name} onChange={(e) => setName(e.target.value)}
                   placeholder="celui du routeur" disabled={busy} />
          </label>
        </div>
      ) : (
      <div className="pairing-fields">
        <label>
          <span className="field-label">Identifiant</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="off"
            disabled={busy}
          />
        </label>
        <label>
          <span className="field-label">Mot de passe</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="off"
            disabled={busy}
            autoFocus
          />
        </label>
        <label>
          <span className="field-label">Nom (facultatif)</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="celui du routeur"
            disabled={busy}
          />
        </label>
      </div>
      )}

      {/* Hors des deux branches : le mode client API n'avait ni l'avertissement
          « en clair » ni la case TLS, et son secret partait sur http:// sans que
          rien ne le signale (C-43). */}
      {/* Un hôte en `http://` reçoit le mot de passe EN CLAIR. Le formulaire
          ne le disait pas : la seule mention « sans TLS » vivait dans la
          colonne des indices de la table de découverte, pas là où le secret
          se tape — et la case « vérifier le certificat » s'affichait quand
          même, sans rien à vérifier. Dans un projet dont la règle centrale
          est la protection des identifiants, c'est ici que ça se dit. */}
      {sansTls(host) ? (
        <p className="notice is-warn">
          Cet équipement ne parle qu’en HTTP : {mode === 'client' ? 'le secret du client' : 'le mot de passe'} partira <b>en clair</b> sur
          ce réseau, à l’appairage puis à chaque relevé. Préférez activer HTTPS sur
          l’équipement si vous le pouvez.
        </p>
      ) : (
        /* `verify_ssl` était déclaré des deux côtés — dans le type de la requête
           et dans le modèle du serveur — et n'était JAMAIS envoyé : le réglage
           existait sans commande. Décoché par défaut, parce qu'un Peplink sort
           d'usine avec un certificat auto-signé et que l'exiger empêcherait tout
           appairage ; à cocher sur un déploiement qui a installé le sien. */
        <label className="pairing-check">
          <input
            type="checkbox"
            checked={verifierCert}
            onChange={(e) => setVerifierCert(e.target.checked)}
            disabled={busy}
          />
          <span>Vérifier le certificat TLS de l’équipement</span>
        </label>
      )}

      <label className="pairing-check">
        <input
          type="checkbox"
          checked={retenir}
          onChange={(e) => setRetenir(e.target.checked)}
          disabled={busy}
        />
        <span>
          Retenir sur ce poste, dans le trousseau chiffré du système. Décoché, ce qui
          était retenu est effacé.
        </span>
      </label>

      {error && (
        <div className={`notice ${error.credentials ? 'is-error' : 'is-warn'}`} role="alert">
          {error.message}
        </div>
      )}

      <div className="pairing-actions">
        {/* L'action principale se distingue de l'annulation. Les deux étaient
            rendues à l'identique : rien ne disait laquelle engage une tentative
            de connexion — dont il n'y en a qu'une. */}
        <button type="submit" className="is-primary" disabled={busy || !complet}>
          {busy ? 'Connexion…' : 'Mettre en supervision'}
        </button>
        <button type="button" className="is-quiet" onClick={onCancel} disabled={busy}>
          Annuler
        </button>
        <span className="disclaimer">
          Une seule tentative. Cinq échecs suspendent l’accès à l’équipement pendant
          cinq minutes.
        </span>
      </div>
    </form>
  )
}
