import type { DeviceCapabilities, DeviceInfo, LanClients } from '../types'
import { fmtId, fmtInt, fmtNumber, fmtPosition } from '../lib/format'
import { dateCourte, echeanceLicence } from '../lib/bondProfile'

interface Props {
  device: DeviceInfo | null
  capabilities: DeviceCapabilities | null
  /** Position GPS relevée par l'équipement. C'est une mesure de l'engin, au même
   *  titre que sa charge CPU : sur un véhicule elle dit où il se trouve, et le
   *  badge « GPS » ci-dessous annonçait jusqu'ici une capacité dont on ne
   *  montrait nulle part le résultat. */
  siteLat?: number | null
  siteLon?: number | null
  /** Cadence de scrutation, telle que le serveur la MESURE.
   *
   *  La période vaut `cycle + consigne`, et seule la période était publiée : un
   *  écart ne pouvait alors s'expliquer qu'en devinant. Les deux figurent ici,
   *  dans la fiche technique — c'est un chiffre de diagnostic, pas une donnée
   *  d'exploitation, et il n'a rien à faire en tête d'écran. */
  periodSeconds?: number | null
  cycleSeconds?: number | null
  intervalSeconds?: number | null
  /** L'instant auquel l'échéance de la licence se lit — `Date.now()` hors tests. */
  maintenant?: number
  /** Clients actifs du LAN, en décomptes (R-1.10). `null` : pas lu, ligne absente. */
  lanClients?: LanClients | null
}

/** Un volume en Mo, dans l'unité qui se lit (base 1 024, comme le routeur). */
function volume(mo: number): string {
  const [v, u] = mo >= 1024 ** 2 ? [mo / 1024 ** 2, 'To'] : mo >= 1024 ? [mo / 1024, 'Go'] : [mo, 'Mo']
  return `${Number.isInteger(v) ? v : v.toFixed(1).replace('.', ',')} ${u}`
}

/** « jusqu'au 01/01/2027 · quota : reste 3 To sur 3 To » — le quota avec son
 *  total, ou rien de lui. `null` quand la licence ne publie ni l'un ni l'autre. */
function ficheLicence(c: DeviceCapabilities, maintenant: number): string | null {
  const date = c.sfc_expires_at === null
    ? null
    : Date.parse(c.sfc_expires_at) <= maintenant
      ? `échu le ${dateCourte(c.sfc_expires_at)}`
      : `jusqu’au ${dateCourte(c.sfc_expires_at)}`
  const quota = c.sfc_quota_remaining_mb !== null && c.sfc_quota_limit_mb !== null
    ? `quota : reste ${volume(c.sfc_quota_remaining_mb)} sur ${volume(c.sfc_quota_limit_mb)}`
    : null
  const parts = [date, quota].filter((p): p is string => p !== null)
  return parts.length ? parts.join(' · ') : null
}

/** « 3 actifs — 2 Wi-Fi, 1 filaire, dont ce poste ». Les colonnes vides se
 *  taisent ; un raccordement que le routeur ne nomme ni Wi-Fi ni filaire est
 *  dit « autre » plutôt que perdu. Zéro client est une MESURE, et se dit. */
function clientsDuLan(c: LanClients): string {
  if (c.active === 0) return 'aucun client actif'
  const autres = c.active - c.wireless - c.wired
  const parts = [
    c.wireless > 0 ? `${c.wireless} Wi-Fi` : null,
    c.wired > 0 ? `${c.wired} ${c.wired > 1 ? 'filaires' : 'filaire'}` : null,
    autres > 0 ? `${autres} ${autres > 1 ? 'autres' : 'autre'}` : null,
  ].filter((p): p is string => p !== null)
  return `${c.active} ${c.active > 1 ? 'actifs' : 'actif'} — ${parts.join(', ')}`
    + (c.includes_this_host === true ? ', dont ce poste' : '')
}

/** « 12 min 34 s en retard sur ce poste », au-delà d'une minute d'écart. */
function horlogeDecalee(ecart: number | null | undefined): string | null {
  if (ecart === null || ecart === undefined || Math.abs(ecart) <= 60) return null
  const total = Math.round(Math.abs(ecart))
  const duree = total >= 3600
    ? `${Math.floor(total / 3600)} h ${Math.floor((total % 3600) / 60)} min`
    : `${Math.floor(total / 60)} min ${total % 60} s`
  return `${duree} ${ecart > 0 ? 'en avance' : 'en retard'} sur ce poste`
}

function Field({ label, value }: { label: string; value: string | null }) {
  if (!value) return null
  return (
    <div>
      <div className="detail-key lbl">{label}</div>
      <div className="detail-val num">{value}</div>
    </div>
  )
}

/** Identité de l'équipement branché : modèle, firmware, santé, capacités.
 *
 * Rendue seulement si l'équipement a répondu. Les modèles ne publient pas les
 * mêmes champs — le Dome Pro Duo n'a ni consommation ni double alimentation —
 * donc chaque ligne disparaît proprement quand la donnée manque, plutôt que
 * d'afficher un tiret de plus.
 */
export function DeviceCard({
  device, capabilities, siteLat, siteLon, periodSeconds, cycleSeconds, intervalSeconds,
  maintenant = Date.now(), lanClients = null,
}: Props) {
  if (!device) return null

  // La licence SpeedFusion Cloud : un FAIT daté, et un badge seulement dans ses
  // 30 derniers jours ou une fois échue — jamais allumé des mois durant (R-1.5).
  const echeance = capabilities ? echeanceLicence(capabilities, maintenant) : null
  const licenceCloud = capabilities?.sfc_licensed === true ? ficheLicence(capabilities, maintenant) : null

  // `Field` efface la ligne sur une valeur nulle : une position absente — GPS
  // non fixé, ou modèle sans récepteur — ne laisse donc aucune trace.
  const position = fmtPosition(siteLat, siteLon)

  // Décompte par type, tel que l'exploitant le compte : ports basculés en LAN
  // exclus, emplacements USB comptés à part.
  const byKind = (kind: string) =>
    capabilities ? Object.values(capabilities.wan_types).filter((t) => t === kind).length : 0
  // Le TOTAL doit être la somme de ce qu'on détaille, sinon l'étiquette et la
  // valeur se contredisent à un centimètre d'écart. Le titre comptait toutes les
  // entrées de `wan_types` — modems USB et types inconnus compris — pendant que
  // la liste ne sommait que cellulaire, filaire et Wi-Fi : sur le châssis de
  // référence, « WAN (11) » surplombait « 4 cellulaires · 1 filaire ».
  const detailles = capabilities
    ? byKind('cellular') + byKind('ethernet') + byKind('wifi')
    : 0
  const inventory = capabilities
    ? [
        [byKind('cellular'), 'cellulaire'],
        [byKind('ethernet'), 'filaire'],
        [byKind('wifi'), 'Wi-Fi'],
      ]
        .filter(([n]) => (n as number) > 0)
        .map(([n, label]) => `${n} ${label}${(n as number) > 1 && label !== 'Wi-Fi' ? 's' : ''}`)
        .join(' · ')
    : null

  // Le repli quand le champ MANQUE, plutôt qu'une page blanche : un serveur
  // qui n'a pas été relancé depuis la veille ne publie pas encore
  // `lan_networks`, et `undefined.length` a effacé tout l'écran — vu le jour
  // même, sur le HD2. Même règle que pour `cycles_in_bond`.
  const reseauxLan = capabilities?.lan_networks ?? []
  // Même repli que `lan_networks` : un serveur d'une version antérieure ne
  // publie pas `power_sources`, et `undefined.filter` était une page noire.
  const alimentations = device.power_sources ?? []
  const unplugged = alimentations.filter((s) => !s.connected)
  const plugged = alimentations.filter((s) => s.connected)

  return (
    <section className="card">
      <header className="card-head">
        <span className="card-title">{device.model ?? 'Équipement inconnu'}</span>
        {device.firmware && <span className="badge tech">{device.firmware}</span>}
        {capabilities?.pepvpn && <span className="badge accent">SpeedFusion</span>}
        {capabilities?.gps && <span className="badge">GPS</span>}
        {/* Une fiche non CONCLUE le dit. `gps: false` y signifie « on ne sait
            pas encore » — un délai dépassé sur `info.location` pendant qu'un
            routeur démarre ne prouve pas qu'il n'a pas de GPS —, et l'absence de
            badge se lisait comme une capacité niée. Le serveur rejoue la fiche
            tant que ce drapeau est levé ; l'écran doit donc dire qu'il attend,
            au lieu d'afficher une négation qu'il n'a pas mesurée. */}
        {capabilities?.provisional && (
          <span className="badge" title="Toutes les sondes n’ont pas encore abouti : ce qui n’est pas affiché n’est pas pour autant absent.">
            fiche en cours
          </span>
        )}
        {/* D'OÙ vient cette fiche. `provisional` avait été branché, `source`
            non — alors que les deux disent la même famille de chose : jusqu'où
            va ce qu'on affirme. Une fiche « reconstituée » l'est faute de droits
            sur `info.capability` : son inventaire de WAN se limite alors aux WAN
            ACTIFS, et un port désactivé y manque sans que rien ne le distingue
            d'un port absent. L'exploitant qui compte ses ports doit le savoir. */}
        {capabilities?.source === 'probed' && (
          <span
            className="badge"
            title="`info.capability` n’est pas couvert par les droits de ce client API : l’inventaire a été reconstitué en sondant, et ne couvre que les WAN actifs."
          >
            fiche reconstituée
          </span>
        )}
        {/* Quatre faits que le châssis publiait et que rien ne rendait. Chacun
            n'apparaît que s'il a quelque chose à dire : un badge permanent
            serait le papier peint que cette interface refuse. */}
        {/* Un état STABLE — le contrat ne se renouvelle pas de lui-même — dit
            en badge neutre : en ambre, il s'allumait à chaque ouverture, le
            papier peint que cette règle refuse (C-44). */}
        {device.care_plan_active === false && (
          <span
            className="badge"
            title={`Contrat ${device.care_plan ?? 'de support'} expiré : plus de mise à jour ni d’assistance de l’éditeur.`}
          >
            support expiré
          </span>
        )}
        {echeance?.etat === 'proche' && (
          <span className="badge warn" title="La licence SpeedFusion Cloud arrive à échéance : le service s’arrêtera à cette date.">
            SpeedFusion Cloud : échéance dans {echeance.jours} {echeance.jours > 1 ? 'jours' : 'jour'}
          </span>
        )}
        {echeance?.etat === 'echue' && (
          <span className="badge warn" title="La licence SpeedFusion Cloud est échue : un profil SpeedFusion Cloud ne peut plus s’établir.">
            SpeedFusion Cloud échu
          </span>
        )}
        {capabilities?.sfc_licensed === true && capabilities.sfc_suspended === true && (
          <span
            className="badge warn"
            title="Une licence SpeedFusion Cloud existe mais elle est SUSPENDUE : le service est indisponible malgré la licence."
          >
            licence SpeedFusion suspendue
          </span>
        )}
        {device.remote_assistance === true && (
          <span
            className="badge warn"
            title="Un tunnel d’assistance est ouvert vers l’éditeur. C’est un fait de sécurité, pas une panne."
          >
            assistance distante ouverte
          </span>
        )}
        {device.config_activating && (
          <span
            className="badge"
            title="Une configuration s’applique en ce moment : un transitoire n’est pas forcément une panne."
          >
            configuration en cours
          </span>
        )}
        <span className="spacer" />
        {device.name && <span className="badge">{device.name}</span>}
      </header>

      <div className="card-body">
        <div className="details" style={{ marginTop: 0 }}>
          <Field label="Code produit" value={device.product_code} />
          {/* `null` : le serveur ne publie la série qu'au poste local. La ligne
              reste, avec « — » : disparue, l'absence se lirait comme un châssis
              sans série. */}
          <Field label="Numéro de série" value={device.serial ?? '—'} />
          <Field label="Révision matérielle" value={device.hardware_revision} />
          <Field label="MAC LAN" value={device.mac_lan} />
          <Field label="Position GPS" value={position} />
          {device.cpu_load_percent !== null && (
            <Field label="Charge CPU" value={`${device.cpu_load_percent} %`} />
          )}
          {/* La température du châssis : un véhicule au soleil en été, un
              boîtier en soute, et c'est le seul indicateur de risque thermique
              dont l'outil dispose. Absente du Dome Pro Duo, qui n'a pas de
              capteur — d'où la condition plutôt qu'un zéro. */}
          {device.temperature_c !== null && (
            <Field label="Température" value={`${fmtNumber(device.temperature_c, 1)} °C`} />
          )}
          {/* La CHARGE de l'alimentation, en part de son budget. La tension et
              le courant restent hors écran : leurs unités se déduisent par
              cohérence interne — 54 × 360 donne bien les 19 W publiés, donc des
              volts et des milliampères — mais sur une seule mesure d'un seul
              matériel. Une tension affichée à tort ferait diagnostiquer une
              panne d'alimentation qui n'existe pas. */}
          {device.power_load_percent !== null && (
            <Field
              label="Charge alimentation"
              value={`${fmtNumber(device.power_load_percent, 1)} %`}
            />
          )}
          <Field label="Version PepVPN" value={device.pepvpn_version} />
          {/* La cadence, DÉCOMPOSÉE. La période vaut `cycle + consigne` : les
              publier ensemble rend leur écart explicable, là où seule la période
              était mesurée et où une dérive ne se constatait qu'après coup, sans
              cause. Absente tant que deux cycles n'ont pas eu lieu — mieux vaut
              rien qu'un nombre qui ne veut encore rien dire. */}
          {periodSeconds != null && (
            <Field
              label="Cadence de scrutation"
              value={
                cycleSeconds != null && intervalSeconds != null
                  ? `${fmtNumber(periodSeconds, 1)} s · cycle ${fmtNumber(cycleSeconds, 1)}`
                    + ` + pause ${fmtNumber(intervalSeconds, 1)}`
                  : `${fmtNumber(periodSeconds, 1)} s`
              }
            />
          )}
          {device.power_watts !== null && (
            <Field label="Consommation" value={`${device.power_watts} W`} />
          )}
          {capabilities && inventory && (
            <Field
              label={`WAN (${detailles})`}
              value={inventory}
            />
          )}
          {/* L'écart d'horloge ne se dit qu'au-delà d'une minute (R-3.9) : sous
              ce seuil il ne change rien, et l'afficher toujours serait un
              chiffre de plus que personne ne lit. */}
          <Field label="Horloge du routeur" value={horlogeDecalee(device.clock_offset_s)} />
          <Field label="SpeedFusion Cloud" value={licenceCloud} />
          <Field label="Clients du LAN" value={lanClients === null ? null : clientsDuLan(lanClients)} />
          {capabilities && capabilities.sim_slots > 0 && (
            <Field label="Emplacements SIM" value={fmtInt(capabilities.sim_slots)} />
          )}
          {/* Les réseaux LAN, nommés par le routeur. « C'est quoi cette adresse
              en 10.10.1.1 ? je pensais que le routeur était sur 192.168.56.1 » :
              les deux, un LAN natif et un VLAN pour le Wi-Fi, et la réponse
              était dans une charge utile déjà téléchargée. Les adresses, elles,
              ne sont pas lues — `config.lan` reste interdit — donc on ne dit
              pas par quel réseau l'outil voit le routeur. */}
          {reseauxLan.length > 0 && (
            <Field
              label={reseauxLan.length > 1 ? 'Réseaux LAN' : 'Réseau LAN'}
              value={reseauxLan
                .map((r) => (r.vlan_id === '0' ? `${r.name} (natif)` : `${r.name} (VLAN ${fmtId(r.vlan_id)})`))
                .join(' · ')}
            />
          )}
        </div>

        {capabilities && (capabilities.lan_repurposed.length > 0 || capabilities.usb_modem_slots > 0) && (
          <p className="disclaimer">
            {capabilities.lan_repurposed.length > 0 && (
              <>
                Port{capabilities.lan_repurposed.length > 1 ? 's' : ''} WAN{' '}
                {capabilities.lan_repurposed.join(', ')} basculé
                {capabilities.lan_repurposed.length > 1 ? 's' : ''} en LAN, donc indisponible
                {capabilities.lan_repurposed.length > 1 ? 's' : ''} comme WAN.
              </>
            )}
            {capabilities.lan_repurposed.length > 0 && capabilities.usb_modem_slots > 0 && ' '}
            {capabilities.usb_modem_slots > 0 && (
              <>
                {capabilities.usb_modem_slots} emplacement
                {capabilities.usb_modem_slots > 1 ? 's' : ''} de modem USB, non supervisé
                {capabilities.usb_modem_slots > 1 ? 's' : ''} tant qu’aucun périphérique n’y
                est reconnu.
              </>
            )}
          </p>
        )}

        {plugged.length > 0 && (
          <p className="disclaimer">
            Alimentation : {plugged.map((s) => s.name).join(', ')} raccordée
            {plugged.length > 1 ? 's' : ''}
            {/* « Donc pas de redondance » n'est vrai que s'il reste MOINS DE
                DEUX sources raccordées. Avec deux raccordées et une troisième
                qui ne l'est pas, l'écran affirmait le contraire de la vérité —
                le défaut ne se voit pas sur ce matériel, qui n'en a qu'une. */}
            {unplugged.length > 0 && plugged.length < 2 && (
              <>
                {' '}— {unplugged.map((s) => s.name).join(', ')} non raccordée
                {unplugged.length > 1 ? 's' : ''}, donc pas de redondance d'alimentation.
              </>
            )}
          </p>
        )}
      </div>
    </section>
  )
}
