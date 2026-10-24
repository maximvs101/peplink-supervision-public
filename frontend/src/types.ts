/** Types miroir des modèles Pydantic du backend (app/models.py). */

/** Un identifiant : Cell ID, station, secteur, TAC, PCI, canal. Le backend le
 *  publie en CHAÎNE parce que c'est un nom, pas une quantité — « 25 735 436 »
 *  avec ses séparateurs de milliers ne désigne plus la cellule 25735436. Une
 *  chaîne, `fmtInt` la refuse à la compilation ; seul `fmtId` l'écrit. */
export type Id = string

export interface BandMeasurement {
  rat: string
  band: string
  label: string | null
  channel: Id | null
  pci: Id | null
  channel_width: string | null
  rsrp: number | null
  rsrq: number | null
  sinr: number | null
  rssi: number | null
}

export interface WifiLink {
  ssid: string | null
  bssid: string | null
  channel: Id | null
  band: string | null
  signal_dbm: number | null
  signal_level: number | null
}

/** État d'un lien vu de l'intérieur du tunnel SpeedFusion.
 *
 *  C'est le tunnel qui porte le flux. Un lien peut être « connecté » côté WAN et
 *  écarté du bond — son état n'est alors pas `ACTIVE` et il ne transporte rien.
 *  La latence, la perte et le désordre de paquets n'existent qu'ici. */
/** Un profil SpeedFusion : nom, état, pairs connectés. `cloud` : profil
 *  SpeedFusion Cloud (`SFC Client`), le seul que la licence Cloud conditionne. */
/** Combien d'appareils dépendent de la liaison — des nombres, jamais d'identité.
 *  `includes_this_host` : le poste qui supervise est-il parmi eux ? */
export interface LanClients {
  active: number
  wireless: number
  wired: number
  includes_this_host: boolean | null
  /** L'instant de la lecture (C-33). Optionnel : un serveur d'une version en
   *  arrière ne le publie pas. Au-delà de deux minutes sans lecture, le
   *  serveur ne publie plus de décompte du tout. */
  read_at?: string | null
}

export interface BondProfile {
  id: string
  name: string | null
  status: string | null
  peers_connected: number | null
  cloud: boolean
}

export interface TunnelLink {
  state: string | null
  /** À qui la cause s'adresse quand le lien est hors du bond : « pair » pour
   *  la sentinelle que le serveur pose quand aucun pair ne répond, « lien »
   *  pour un état publié par le routeur, `null` quand le lien porte ou que son
   *  état est inconnu. Classé par le serveur, là où la sentinelle naît. */
  cause_scope: 'pair' | 'lien' | null
  /** Ce lien porte-t-il le flux ? Le backend le SÉRIALISE (`computed_field`)
   *  plutôt que de laisser chaque écran redériver `state === 'ACTIVE'` : les
   *  trois endroits qui le faisaient n'en tiraient pas la même conclusion, et
   *  aucun ne traitait l'état inconnu de la même façon. Un `state` à null
   *  signifie INCONNU : ni dans le bond, ni compté comme écarté. */
  in_bond: boolean
  /** Mot d'état du pair sous `PEER_DOWN` (« START » : le routeur tente de le
   *  joindre), quand tous les pairs déclarés portent le même (R-1.5). */
  peer_state: string | null
  rtt_ms: number | null
  /** Perte et désordre, tous sens confondus : le backend y met le PIRE des deux
   *  sens. Une perte de 4 % à la montée moyennée avec 0 % à la descente donnait
   *  2 %, sous le seuil d'alerte, alors que la visioconférence est déjà hachée. */
  loss_percent: number | null
  /** Désordre de paquets. Lu, stocké, et DÉLIBÉRÉMENT jamais affiché.
   *
   *  Ce n'est pas un oubli, et c'est mesuré : sur 39 825 relevés de la base de
   *  supervision, DEUX portent une valeur non nulle — 0,44 % et 0,30 %. Une
   *  grandeur qui n'a jamais varié en trente-cinq jours ne peut rien décider, et
   *  lui donner une colonne en ferait un tiret permanent : le papier peint que
   *  cette interface refuse. Elle reste collectée parce qu'un autre déploiement
   *  — un tunnel sur satellite, un bond à quatre opérateurs — pourrait la faire
   *  vivre, et qu'on ne le saura qu'en la gardant. */
  out_of_order_percent: number | null
  /** Le détail par sens. Le sens qui décroche désigne le remède : une perte à la
   *  montée est un problème d'émission du site, à la descente un problème de
   *  couverture ou de charge de la cellule. */
  loss_percent_up: number | null
  loss_percent_down: number | null
  out_of_order_percent_up: number | null
  out_of_order_percent_down: number | null
  /** Paquets RÉELLEMENT COMPTÉS sur l'intervalle, par sens : le dénominateur des
   *  taux ci-dessus. Le backend le calculait déjà (`total = portés + perdus`)
   *  pour faire la division, puis le jetait — si bien qu'un « 0,00 % » établi
   *  sur trois paquets de keepalive s'affichait exactement comme un « 0,00 % »
   *  établi sur douze mille paquets utiles. Null quand l'intervalle n'a pas de
   *  référence : premier relevé, compteurs reculés, ou changement de pair. */
  packets_up: number | null
  packets_down: number | null
  /** Débit RÉELLEMENT porté par le bond sur ce lien, par sens, en kbit/s.
   *  `WanRadioStatus.tx_kbps` mesure autre chose : toute l'interface WAN,
   *  tunnel et hors tunnel confondus. */
  tx_kbps: number | null
  rx_kbps: number | null
  /** Redondance de SpeedFusion sur l'intervalle : paquets de correction émis,
   *  et paquets reconstitués grâce à elle. De la capacité dépensée à réparer
   *  plutôt qu'à transporter. */
  repair_sent: number | null
  repair_used: number | null
  /** Seuil de latence du bond, tel que le profil SpeedFusion le configure, et
   *  dépassement constaté.
   *
   *  Le firmware arrête le trafic sur un lien qui dépasse de cette valeur celui
   *  de latence la plus basse — et rien dans `state` ne le dit : sur les 113
   *  relevés de la base qui franchissent le seuil, il vaut `ACTIVE` cent fois
   *  sur cent, alors que les intervalles sans un seul paquet montant y sont
   *  quatre fois et demie plus fréquents et que le débit porté n'y dépasse
   *  jamais zéro. `in_bond` n'est pas corrigé pour autant : il porte ce que
   *  l'équipement DÉCLARE. */
  cutoff_ms: number | null
  beyond_cutoff: boolean
}

/** Le plafond de volume que le routeur applique, et ce qu'il fera au-delà.
 *
 *  Les trois champs de détail ne sont publiés QUE lorsque `enabled` est vrai :
 *  un plafond non configuré rend `null`, jamais `0`. */
export interface BandwidthAllowance {
  enabled: boolean
  monthly_allowance_mb: number | null
  /** Jour du mois où le compteur repart, 1 à 31. */
  reset_day: number | null
  /** Vu : "email", "restrict". `restrict` signifie que le routeur COUPE la SIM. */
  actions: string[]
}

export interface SimSlot {
  slot: string
  /** `null` : le firmware ne l'a pas dit. Ce n'est pas un emplacement vide. */
  card_detected: boolean | null
  apn: string | null
  /** L'emplacement que le modem emploie en ce moment. Publié par les deux
   *  matériels de référence, et longtemps non lu au nom d'un commentaire du
   *  parseur qui affirmait le contraire. `null` : non publié. */
  active: boolean | null
  allowance: BandwidthAllowance | null
}

export interface WanRadioStatus {
  kind: 'cellular' | 'wifi' | 'ethernet'
  wan_id: string
  wan_name: string
  connected: boolean
  operator: string | null
  /** L'opérateur tel qu'on le NOMME : par son code réseau quand il est connu
   *  (208-10 → SFR), sinon le libellé du modem, qui reste dans `operator`
   *  (R-2.6). C'est lui qu'on affiche. */
  operator_name: string | null
  /** Code réseau « 208-01 », tel que le routeur l'écrit — zéro de tête compris.
   *  Remplace les deux entiers `mcc`/`mnc` : le MNC est un identifiant, pas une
   *  quantité, et « 01 » n'est pas 1. */
  plmn: string | null
  rat: string | null
  band: string | null
  earfcn: Id | null
  pci: Id | null
  /** Libellé d'itinérance du routeur ("home"), pas son code : aucune valeur
   *  d'itinérance n'a été observée, et traduire « code ≠ 1 » serait une règle
   *  non calibrée. */
  roaming_status: string | null
  modem_vendor: string | null
  modem_model: string | null
  modem_firmware: string | null
  /** MTU de ce lien. Hétérogène sur le châssis de référence : 1428 sur les SIM,
   *  1440 sur le WAN 2, 1500 ailleurs — cause classique de fragmentation. */
  mtu: number | null
  /** `null` et non `false` sur un matériel qui ne le publie pas. */
  carrier_aggregation: boolean | null
  network_mode: string | null
  allowance: BandwidthAllowance | null
  /** Volumes de la PÉRIODE EN COURS, en mégaoctets. « Période » et non « mois » :
   *  la réponse du routeur ne porte aucun marqueur de période, et nommer une
   *  fenêtre non vérifiée serait affirmer plus qu'on ne sait. */
  period_rx_mb: number | null
  period_tx_mb: number | null
  /** Latence du CHEMIN, en ms — celle du lien lui-même, hors de tout tunnel.
   *
   *  À ne pas confondre avec `tunnel.rtt_ms`, l'aller-retour vers le
   *  concentrateur : celui-ci n'existe que pour un lien DANS le bond, sur un
   *  châssis qui porte un profil SpeedFusion. Un lien filaire, un lien écarté,
   *  un équipement sans SpeedFusion n'avaient aucune latence du tout.
   *
   *  Leur DIFFÉRENCE n'est pas un « coût du tunnel » : mesurée sur dix relevés
   *  appariés par lien, elle vaut +2 à +5 ms sur quatre liens et −4 ms sur le
   *  cinquième. Les deux sondes ne visent pas la même destination.
   *
   *  `null` sur les cycles où l'endpoint n'a pas été lu — il l'est une fois par
   *  minute, et une absence de mesure n'est pas une mesure. */
  path_latency_ms: number | null
  /** La DERNIÈRE latence du lien et l'heure du relevé qui l'a portée — pour
   *  le tableau de bord seul, jamais en base. Tenue jusqu'à 90 s tant que le
   *  lien reste connecté, puis `null` : sans elle, la colonne affichait « — »
   *  cinq cycles sur six. Datée, donc pas un chiffre recopié. */
  path_latency_recent_ms: number | null
  path_latency_at: string | null
  /** Température du MODEM, en °C, et l'heure de sa lecture — une fois par
   *  minute, tenue 90 s au plus, sans historique ni seuil (R-1.11). Distincte
   *  de `DeviceInfo.temperature_c`, celle du châssis. */
  modem_temperature_c: number | null
  modem_temperature_at: string | null
  ip: string | null
  priority: number | null
  tunnel: TunnelLink | null
  cell_id: Id | null
  tac: Id | null
  /** Station de base (20 bits de poids fort de l'ECI). Deux WAN partageant cette
   *  valeur campent sur le même site physique : aucune diversité de trajet. */
  enb_id: Id | null
  /** Cellule sur cette station (8 bits de poids faible) : secteur x porteuse. */
  sector_id: Id | null
  rsrp: number | null
  rsrq: number | null
  sinr: number | null
  rssi: number | null
  signal_bars: number | null
  bands: BandMeasurement[]
  /** Emplacements SIM de ce modem : présence d'une carte, et APN. Vide sur un
   *  lien qui n'en a pas. L'emplacement ACTIF n'est pas publié par le routeur —
   *  on ne le déduit donc pas. Ni IMSI ni ICCID : ils n'apprennent rien à la
   *  supervision et rendraient toute capture d'écran impartageable. */
  sim_slots: SimSlot[]
  wifi: WifiLink | null
  tx_kbps: number | null
  rx_kbps: number | null
  uptime_s: number | null
  status_message: string | null
  /** Désactivé par l'exploitant pendant la session : pas une panne. */
  disabled: boolean
}

export interface RadioSample {
  router_id: string
  router_name: string
  ts: string
  /** Position GPS DU ROUTEUR, relevée par l'équipement lui-même. Sans rapport
   *  avec l'identification d'antenne retirée : c'est une mesure de l'engin, qui
   *  bouge avec lui, et le seul repère géographique qui subsiste. */
  site_lat: number | null
  site_lon: number | null
  wan: WanRadioStatus
  cell_changes: number
  disconnects: number
}

export interface PowerSource {
  name: string
  connected: boolean
}

/** Identité et santé de l'équipement. Tout est optionnel : les modèles ne
 *  remontent pas les mêmes champs, et l'endpoint n'existe pas partout. */
export interface DeviceInfo {
  model: string | null
  product_code: string | null
  serial: string | null
  firmware: string | null
  hardware_revision: string | null
  name: string | null
  /** Ancienneté du CHÂSSIS, telle que le routeur la publie. L'outil l'approchait
   *  par le maximum des `uptime` de liens, et c'est de là que venaient les
   *  sessions scindées : ce maximum retombe dès que le lien qui le portait
   *  quitte le relevé. */
  uptime_s: number | null
  cpu_load_percent: number | null
  power_watts: number | null
  power_sources: PowerSource[]
  mac_lan: string | null
  /** Température du châssis en °C. `null` sur un modèle qui n'a pas de capteur —
   *  le Dome Pro Duo n'en publie aucun — et non zéro. */
  temperature_c: number | null
  /** Charge de l'alimentation en % de son budget. La tension et le courant
   *  restent hors écran : leurs unités se déduisent par cohérence interne mais
   *  sur une seule mesure d'un seul matériel, et une tension affichée à tort
   *  ferait diagnostiquer une panne d'alimentation inexistante. */
  power_load_percent: number | null
  care_plan: string | null
  care_plan_active: boolean | null
  /** Version du protocole de tunnel, distincte du firmware : c'est elle qui
   *  gouverne la compatibilité avec le concentrateur. */
  pepvpn_version: string | null
  timezone: string | null
  /** Horloge du routeur moins celle du poste, en secondes (R-3.9). Au-delà de
   *  60 s, son historique à 10 s est écarté et son journal n'est plus lu. */
  clock_offset_s: number | null
  /** Une configuration s'applique : explique un transitoire qu'on prendrait
   *  sinon pour une panne. Null : le routeur ne l'a pas dit. */
  config_activating: boolean | null
  /** Tunnel d'assistance ouvert vers l'éditeur. Fait de sécurité. */
  remote_assistance: boolean | null
}

/** Ce que l'équipement déclare savoir faire. Pilote l'affichage : une donnée
 *  qu'un modèle ne produit pas ne doit pas apparaître comme une donnée mauvaise.
 *
 *  Ce miroir ne reprend que les drapeaux dont un écran se sert. Le backend en
 *  publie d'autres — point d'accès Wi-Fi, VLAN, pare-feu — qu'aucun composant
 *  n'a jamais lus : déclarés ici, ils se seraient fait passer pour des réglages
 *  supervisés alors que rien ne les montre ni ne les surveille. Les champs
 *  supplémentaires du JSON sont simplement ignorés à la lecture. */
export interface LanNetwork {
  /** Un nom, pas une quantité : « 0 » est le réseau natif. */
  vlan_id: Id
  name: string
}

export interface DeviceCapabilities {
  gps: boolean
  pepvpn: boolean
  wan_types: Record<string, string>
  lan_repurposed: string[]
  /** Les réseaux LAN que `status.port` nomme, VLAN natif compris. Nom et
   *  numéro de VLAN seulement — les adresses vivent dans `config.lan`, que
   *  l'outil s'interdit. Vide quand l'endpoint n'a pas été lu (fiche sondée). */
  lan_networks: LanNetwork[]
  usb_modem_slots: number
  sim_slots: number
  per_wan_throughput: boolean
  /** La fiche a-t-elle pu être CONCLUE ? Fausse dès qu'une sonde n'a pas su
   *  trancher — un délai dépassé sur `info.location` pendant qu'un routeur
   *  démarre ne dit pas qu'il n'a pas de GPS.
   *
   *  Le champ était émis par le serveur et absent d'ici, donc lu par personne :
   *  tant que la fiche restait provisoire, `gps: false` voulait dire « on ne
   *  sait pas » et l'écran l'affichait comme « pas de GPS ». Une absence de
   *  mesure présentée comme une mesure négative — ce que tout ce projet
   *  combat. */
  provisional: boolean
  /** Licence SpeedFusion Cloud. Les deux booléens ne se lisent qu'ENSEMBLE :
   *  sur le matériel de référence ils valent tous deux vrai — une licence
   *  existe, et le service est suspendu. */
  sfc_licensed: boolean | null
  sfc_suspended: boolean | null
  /** Échéance de la licence SpeedFusion Cloud, et quota restant sur total en Mo. */
  sfc_expires_at: string | null
  sfc_quota_remaining_mb: number | null
  sfc_quota_limit_mb: number | null
  sfc_throughput_limit_mbps: number | null
  /** Profils SpeedFusion ACTIFS au plus. C'est cette limite qui contraint
   *  l'exploitation, pas le nombre de profils configurables. */
  pepvpn_max_active_profiles: number | null
  /** D'où vient la fiche : `declared` (le routeur l'a publiée) ou `probed`. */
  source: string
}

/** État de la liaison avec l'équipement, tel que le poller le constate.
 *
 *  Les cinq valeurs sont EXHAUSTIVES et l'union est fermée : le libellé porte le
 *  remède, et confondre « injoignable » avec « erreur de configuration » fait
 *  déplacer un technicien vers un raccordement quand c'est une déclaration
 *  d'équipement qu'il faut corriger. Une union laissée ouverte par un `| string`
 *  ne vérifierait plus l'orthographe de ces libellés, dont dépend l'aiguillage
 *  du verdict. */
export type DeviceLinkStatus =
  | 'en attente'
  | 'connecté'
  | 'identifiants refusés'
  | 'injoignable'
  | 'erreur de configuration'

/** Un fait qui demande une décision.
 *
 *  Ni le bandeau ni le journal. Le bandeau dit l'état de l'instant, lissé ; le
 *  journal dit ce qui s'est passé, listé. Une alerte a une DURÉE — c'est elle
 *  qui distingue un fait d'un clignotement — et elle survit un moment à sa
 *  propre disparition, pour que quelqu'un qui s'est éloigné deux minutes
 *  l'apprenne.
 *
 *  Trois règles seulement, et le choix se défend par la mesure : les règles
 *  candidates écartées sont listées côté serveur avec le nombre de fois
 *  qu'elles se seraient déclenchées sur 14 h 30 de relevés réels. « Des modems
 *  partagent une cellule » y aurait parlé 99 % du temps. */
export interface Alert {
  /** "hors du bond" · "liaison sur un seul lien" · "liaison sans aucun lien"
   *  · "supervision interrompue" — les deux genres du milieu sont une même
   *  famille, qui se relaie sans rompre la durée. */
  kind: string
  wan_id: string | null
  /** Depuis quand la condition est vraie — pas depuis quand on l'annonce. */
  since: string
  /** Quand elle a cessé. `null` tant qu'elle dure. */
  until: string | null
  /** La cause, telle que le ROUTEUR la donne quand il la donne, ou la grandeur
   *  mesurée. */
  detail: string | null
  /** Le seuil de durée qui a fait publier cette alerte, en secondes. Publié
   *  pour que l'exploitant sache sur quoi elle se fonde. */
  sustained_s: number
}

export interface RouterSummary {
  id: string
  name: string
  /** « mock » ou « router », PAR ÉQUIPEMENT. `/api/health` publie un mode
   *  global, celui du fichier de configuration : un routeur réel appairé à
   *  chaud s'affichait sous un badge « Simulateur ». */
  mode: string
  /** Position GPS du routeur, comme sur `RadioSample` : affichée en clair dans
   *  la carte d'identité de l'équipement, seul lecteur depuis le retrait de la
   *  carte géographique. */
  site_lat: number | null
  site_lon: number | null
  wans: RadioSample[]
  device: DeviceInfo | null
  capabilities: DeviceCapabilities | null
  /** État de la liaison avec l'équipement lui-même, constaté par le poller. */
  status: DeviceLinkStatus
  error: string | null
  /** Âge du relevé le plus récent, mesuré par le SERVEUR : le calculer dans le
   *  navigateur reviendrait à comparer deux horloges. */
  age_seconds: number | null
  /** Période observée entre deux relevés de CET équipement — chacun a sa propre
   *  boucle de scrutation. C'est d'elle que se déduit le seuil de péremption :
   *  la mesure de flotte de `/api/health` retient le pire, et un équipement mort
   *  relâchait ainsi le seuil de tous ses voisins sains. Null avant deux cycles. */
  period_seconds: number | null
  /** Ce que COÛTE une scrutation de cet équipement, hors pause. Mêler un cycle
   *  de flotte à une période d'équipement faisait une soustraction qui ne
   *  tombait juste que sur une flotte d'un seul. */
  cycle_seconds: number | null
  /** Sous-réseaux acheminés PAR le tunnel, tels que le pair les déclare. Sans
   *  eux, un débit nul dans le bond se lit comme une panne — alors qu'un tunnel
   *  dont aucun profil n'est sélectionné dans « Send All Traffic To » ne
   *  transporte légitimement que ce qui lui est explicitement routé. */
  bond_routes: string[]
  /** Les profils SpeedFusion, relus une fois par minute (R-1.5). */
  bond_profiles: BondProfile[]
  /** Clients actifs du LAN, en décomptes seulement (R-1.10). `null` : pas lu. */
  lan_clients: LanClients | null
  /** Depuis quand le bond TIENT, en secondes, selon le routeur. Distingue un
   *  tunnel stable d'un tunnel qui vient de se rétablir. */
  tunnel_uptime_s: number | null
  /** Le bond a-t-il déjà porté un lien dans CETTE session ? C'est la règle
   *  qui sépare, côté serveur, un tunnel tombé d'un tunnel qui n'a jamais tenu
   *  — une panne d'un état. `null` tant que la question n'a pas été posée. */
  tunnel_attendu: boolean | null
  /** Les WAN qui n'ont pas été connectés une seule fois depuis la mise sous
   *  tension — un Ethernet sans câble. Un état, pas une panne. */
  never_connected: string[]
  /** Ce qui demande une décision, et qui n'est pas passé tout seul. */
  alerts: Alert[]
  /** Garde-fou absolu de latence, publié par le serveur. Il vivait en double de
   *  part et d'autre de la couture, pour une seule règle éditoriale. */
  latency_threshold_ms: number | null
}

/** Une tranche de temps, pour UN lien, résumée par son PIRE cas.
 *
 *  Jamais une moyenne : une bande qui lisse l'incident qu'on cherche ne sert à
 *  rien. Le relevé le plus instructif de la base est un pic isolé à 1 929 ms au
 *  milieu de liens à 30 ms — une moyenne sur cinq minutes l'effacerait. */
export interface TimelineSlice {
  t: number
  samples: number
  /** Relevés où le lien était debout, et où il PORTAIT vraiment. Les deux ne se
   *  confondent pas : un lien peut être connecté côté WAN et écarté du bond. */
  up: number
  carrying: number
  /** Relevés où l'état du tunnel était connu. Sans lui, `carrying = 0` ne
   *  distinguerait pas « écarté du bond » de « on ne sait pas ». */
  tunnel_known: number
  sinr_worst: number | null
  rsrp_worst: number | null
  /** Redondance dépensée et paquets transportés, CUMULÉS sur la tranche.
   *
   *  Pas de quoi tracer une courbe — sur le matériel de référence la réparation
   *  vaut zéro sur les 15 500 relevés où elle a été enregistrée — mais de quoi
   *  établir si la redondance du profil SpeedFusion est active. Voir
   *  `lib/redundancy.ts`. */
  /** Réparation émise et paquets transportés, cumulés sur la tranche.
   *  `null` quand le firmware n'a renseigné AUCUN relevé de la tranche : une
   *  colonne absente n'est pas un intervalle sans réparation. */
  repair: number | null
  packets: number | null
  /** Relevés d'adieu d'un WAN désactivé (C-20) : `connected = 0`, comptés dans
   *  `samples` et pas dans `up`. Un choix de l'exploitant, pas une panne. */
  disabled: number
}

export interface TimelineLane {
  wan_id: string
  slices: TimelineSlice[]
}

/** L'état de tous les liens sur la durée d'une SESSION D'ALIMENTATION.
 *
 *  Le mot « session » a UN seul sens dans cette interface, et c'est celui-là :
 *  une mise sous tension du châssis, bornée par son premier et son dernier
 *  relevé. Il en portait deux à l'écran — la carte d'un lien affichait « Durée
 *  de session » pour l'ancienneté de CE lien, c'est-à-dire son `uptime`, qui
 *  redémarre à chaque reconnexion sans que le châssis s'éteigne. Deux durées
 *  différentes, un seul mot, à deux clics d'écart. L'ancienneté d'un lien se
 *  dit maintenant « Connecté depuis ».
 *
 *  Le « stationnement » reste ce que l'exploitant NOMME : le lieu et
 *  l'occasion — « le parking de la mairie ». Il coïncide avec la session sans
 *  s'y confondre, et c'est pourquoi les deux mots subsistent.
 *
 *
 *  Les tranches ABSENTES sont une information : le superviseur ne regardait pas.
 *  Les combler à zéro referait le mensonge que tout ce projet combat. */
export interface Timeline {
  router_id: string
  started_at: string
  ended_at: string
  /** La fenêtre demandée a été ROGNÉE à vingt-quatre heures par le serveur :
   *  `started_at` est le début relu, pas celui demandé. Jamais tu. */
  capped: boolean
  /** Avant cet instant, les relevés sont regroupés à la minute (rétention) :
   *  la bande y a la résolution de la minute. Absent ou `null` : pleine
   *  résolution. */
  grouped_before?: string | null
  slice_count: number
  slice_seconds: number
  lanes: TimelineLane[]
}

/** Un fait daté de la fenêtre, sur un lien. Tout ceci était détecté depuis
 *  longtemps et n'était jamais LISTÉ : l'écran donnait des compteurs, pas une
 *  chronologie. */
export interface Episode {
  /** Secondes depuis le dernier changement de cellule de ce lien, à l'ouverture
   *  de l'épisode. `null` quand il n'y en a pas eu d'assez récent — et c'est le
   *  cas le plus fréquent.
   *
   *  Une EXPLICATION, jamais une prédiction : après un changement de cellule, un
   *  pic de plus de 150 ms survient 43,8 % du temps contre 21,7 % sans, soit un
   *  risque doublé — mais le pic dure une quinzaine de secondes, donc
   *  l'information arrive en même temps que l'effet. Et elle ne couvre que
   *  15,8 % des pics : les autres restent sans explication plutôt que d'en
   *  recevoir une fausse. */
  after_handover_s: number | null
  /** coupure · retour · micro-coupure · latence — et les faits du journal du
   *  routeur : bascule de SIM, priorités, configuration, démarrage, connexion
   *  admin refusée. */
  kind: string
  /** `null` pour un fait du routeur entier, tiré de son journal. */
  wan_id: string | null
  /** Support du lien : le journal le nomme comme le reste de l'interface. */
  link_kind: string
  at: string
  /** Fin de l'épisode, pour ceux qui durent. Les transitions sont ponctuelles. */
  until: string | null
  samples: number
  peak_ms: number | null
  /** Ce que le routeur disait du lien : « No Cable Detected », « Disabled »,
   *  « absent du relevé ». C'est la CAUSE, et l'historique ne la gardait pas. */
  detail: string | null
}

/** Ce qui est trop fréquent pour être listé, et se compte donc. */
export interface LinkChurn {
  wan_id: string
  /** Support du lien : sans lui, un WAN filaire s'affichait « SIM 1 ». */
  link_kind: string
  /** Ce lien a-t-il jamais publié une cellule ? Un filaire n'en a pas, et son
   *  « 0 changement » n'est pas une stabilité : c'est une absence de grandeur. */
  has_cells: boolean
  cell_changes: number
  /** `null` : station non identifiable sur la fenêtre — inconnu, pas zéro. */
  station_changes: number | null
  /** Coupures et rétablissements de la FENÊTRE. L'écran les cumulait depuis le
   *  démarrage du processus superviseur — une base de temps qui n'apparaissait
   *  nulle part et qui se remettait à zéro à chaque relance — dans un panneau
   *  intitulé « depuis la mise sous tension ». */
  outages: number
}

export interface EventLog {
  router_id: string
  started_at: string
  ended_at: string
  /** Fenêtre rognée à vingt-quatre heures : voir `Timeline.capped`. */
  capped: boolean
  /** Avant cet instant, les relevés sont regroupés à la minute : le journal
   *  n'y a plus que les épisodes figés des sessions closes. `null` quand il
   *  est entier — y compris pour une session close relue sur sa fenêtre. */
  grouped_before?: string | null
  episodes: Episode[]
  churn: LinkChurn[]
  /** Épisodes retenus par le plafond. Jamais tu : un journal tronqué en silence
   *  se lit comme un journal complet. */
  omitted: number
  latency_threshold_ms: number
}

export interface HistoryPoint {
  ts: string
  value: number | null
  /** Paquets réellement comptés sous ce point, quand la grandeur est un TAUX.
   *
   *  Un taux sans son dénominateur est indéfendable, et la courbe de perte était
   *  le dernier endroit qui le perdait : la tuile et la table refusent de publier
   *  un taux calculé sur moins de deux cents paquets, le graphe traçait un pic à
   *  33,33 % obtenu sur UN paquet manquant. `null` sur les grandeurs qui ne sont
   *  pas des taux — un niveau radio n'a pas de dénominateur. */
  denominator?: number | null
  /** Technologie du relevé, pour une grandeur radio seulement : un SINR 5G et
   *  un SINR LTE ne se comparent pas. */
  rat?: string | null
  /** « routeur » : un point de l'heure que le ROUTEUR garde, placé dans un trou
   *  de supervision (R-3.1) — dessiné à part. Absent : un relevé de cet outil.
   *  « minute » : le minimum ou le maximum d'une minute regroupée (rétention). */
  source?: string | null
}

export interface HistorySeries {
  router_id: string
  wan_id: string
  metric: string
  points: HistoryPoint[]
  /** Points réellement présents dans la fenêtre, avant allègement. Quand il
   *  dépasse la longueur de `points`, la courbe est une enveloppe. */
  available: number
  /** Avant cet instant, la courbe est faite de minutes regroupées. */
  grouped_before?: string | null
}

export interface Health {
  status: string
  mode: string
  routers: number
  /** Consigne : la pause configurée entre deux cycles. */
  poll_interval_seconds: number
  /** La PLUS LONGUE période observée de la flotte, en secondes. Vue d'ensemble :
   *  un consommateur qui parle d'un seul équipement lit `period_seconds` sur son
   *  résumé. Jamais la consigne — le serveur scrute PUIS dort, donc la période
   *  vaut `cycle + consigne`. Null tant que deux cycles n'ont pas eu lieu. */
  cycle_period_seconds: number | null
  /** Ce que COÛTE une scrutation, hors pause. La période vaut
   *  `cycle + consigne` : publier les deux rend leur écart explicable. */
  cycle_seconds: number | null
  /** Sessions closes dont la rétention n'a pas pu figer la fiche : elles
   *  restent brutes, et le regroupement s'arrête avant elles. Absent d'un
   *  serveur antérieur. */
  retention_sessions_en_echec?: number[]
}

/** Équipement repéré sur le réseau, avant tout appairage. Ces informations sont
 *  les seules obtenables sans identifiants : le modèle et le numéro de série
 *  exigent une authentification. */
export interface DiscoveredDevice {
  host: string
  mac: string | null
  firmware: string | null
  certificate_name: string | null
  api_reachable: boolean
  evidence: string[]
  already_configured: boolean
}

/** État d'un équipement supervisé. Les cinq statuts se distinguent pour dire à
 *  l'utilisateur s'il doit corriger un mot de passe ou vérifier un raccordement. */
export interface DeviceStatus {
  id: string
  name: string
  host: string | null
  serial: string | null
  mode: string
  source: string
  status: DeviceLinkStatus
  error: string | null
  device: DeviceInfo | null
}

export interface PairRequest {
  host: string
  name?: string
  username?: string
  password?: string
  verify_ssl?: boolean
  /** Client API en lecture seule — le SEUL mode qui n'expose pas le compte
   *  admin de l'équipement. Il se crée depuis l'administration web et se
   *  révoque sans changer de mot de passe. */
  client_id?: string
  client_secret?: string
  /** Confier les identifiants au trousseau du système ; faux efface ce qu'il
   *  gardait. */
  remember?: boolean
}

/** Une mise sous tension de l'équipement, d'un bout à l'autre.
 *
 *  Le Peplink n'est alimenté que véhicule à l'arrêt : chaque session est un lieu
 *  et une intervention. Les nommer est ce qui rend deux stationnements
 *  comparables — « le parking de la mairie » se retrouve, un horodatage non. */
export interface Session {
  id: number
  router_id: string
  /** La mise sous tension, déduite de l'uptime : le début du STATIONNEMENT,
   *  pas celui de la supervision. */
  started_at: string
  /** Premier relevé rattaché — l'instant où le superviseur a commencé à
   *  regarder, souvent bien après l'allumage : le routeur démarre avec le
   *  véhicule, l'outil quand on l'ouvre. Borne gauche de tout ce qui se
   *  dessine ; `null` tant qu'aucun relevé n'est rattaché. */
  first_seen: string | null
  /** Dernier relevé rattaché : l'équipement s'éteint sans prévenir, il n'y a pas
   *  d'instant de fin observable. */
  last_seen: string
  samples: number
  label: string | null
}

/** Un équipement déjà appairé depuis cette machine.
 *
 *  Sans mot de passe : il est au trousseau du système quand l'exploitant l'a
 *  voulu (`credentials_stored`), jamais ici. Ce qui est gardé permet de reconnaître
 *  l'équipement et de le proposer à nouveau. */
export interface KnownDevice {
  serial: string
  device_id: string
  name: string | null
  host: string | null
  model: string | null
  firmware: string | null
  first_seen: string
  last_seen: string
  /** Le trousseau du système garde ses identifiants : « Reconnecter » se fait
   *  sans formulaire. Un booléen — le contenu ne quitte jamais le serveur. */
  credentials_stored: boolean
}

/** Ce qu'un lien a fait pendant une session. */
export interface SessionLink {
  wan_id: string
  kind: string
  operator: string | null
  /** La succession des opérateurs du lien, dans l'ordre, un nom par réseau. */
  operators: string[]
  /** Manœuvres de l'exploitant (bascule de SIM, configuration, désactivation),
   *  lues dans le journal du routeur — à part des coupures. `null` : journal
   *  non lu, on ne sait pas — jamais zéro. */
  maneuvers: number | null
  samples: number
  /** Volumes INTÉGRÉS depuis les débits relevés : le routeur ne publie qu'un
   *  cumul depuis son installation, qui ne borne aucune session. À 5,3 s de
   *  période, un pic plus court passe entre les mailles — c'est un ordre de
   *  grandeur, et il se lit comme tel.
   *
   *  Null quand l'équipement ne publie pas de débit par WAN — le Dome Pro Duo ne
   *  le fait pas. Zéro voudrait dire « rien n'a circulé », et la fiche l'écrivait
   *  en gras sur sept cent vingt relevés où rien n'avait été mesuré. */
  mb_up: number | null
  mb_down: number | null
  /** Le nombre de mesures du BOND sous ses percentiles (C-12). Optionnel : un
   *  serveur d'une version en arrière ne le publie pas. */
  rtt_samples?: number
  rtt_p50_ms: number | null
  rtt_p90_ms: number | null
  rtt_max_ms: number | null
  /** Latence du LIEN, hors tunnel, et le nombre de mesures (une par minute)
   *  qui la fondent (R-3.3). */
  path_latency_samples: number
  path_latency_p50_ms: number | null
  path_latency_p90_ms: number | null
  path_latency_max_ms: number | null
  rsrp_median: number | null
  /** Points de l'heure du ROUTEUR comptés dans la médiane de RSRP, pris dans
   *  les trous de supervision seulement — un toutes les 10 s (R-3.1). */
  rsrp_filled_samples: number
  outages: number
  cells: number
  /** Changements de cellule observés, et leur taux horaire.
   *
   *  Le compte brut ne se compare pas d'une session à l'autre ; le taux, si —
   *  et c'est ainsi qu'on voit un modem changer de cellule six fois plus souvent
   *  que son voisin au même endroit et au même instant.
   *
   *  `null` sous un quart d'heure observé, et `null` sur un lien qui n'a jamais
   *  publié de cellule : un filaire ne peut pas en changer, et « 0,0 par heure »
   *  s'y lirait comme une stabilité exemplaire. */
  cell_changes: number
  cell_changes_per_hour: number | null
}

/** La fiche d'une session : ce qui la rend comparable à une autre. */
/** Ce qu'une CELLULE a coûté en latence pendant la session.
 *
 *  La médiane d'un lien ne bouge presque pas quand sa queue se multiplie par
 *  quatre : c'est la queue qui fait geler une image, et elle ne se voit qu'à
 *  cellule fixe. Seules les cellules ayant réuni assez de relevés figurent ici —
 *  un p90 sur vingt mesures n'est pas un p90. */
export interface CellLatency {
  cell_id: Id
  /** Le lien qui a mesuré, et son réseau : un percentile est propre à un modem,
   *  et un ECI n'est unique que dans son PLMN. */
  wan_id: Id
  plmn: string | null
  enb_id: Id | null
  samples: number
  rtt_p50_ms: number | null
  rtt_p90_ms: number | null
  rtt_p99_ms: number | null
}

/** Part du temps où la liaison avait au moins un lien PROPRE.
 *
 *  `observed_cycles` et `total_cycles` voyagent avec le taux, et c'est le point :
 *  les cycles où la sonde de tunnel n'a pas tourné ne sont pas des pannes, ce
 *  sont des cycles non observés. Les compter ferait passer une halte de 99,8 %
 *  à 63,8 % — une heure et demie de panne fabriquée par un défaut de regard. */
export interface BondAvailability {
  threshold_ms: number
  total_cycles: number
  observed_cycles: number
  at_least_one_pct: number | null
  at_least_two_pct: number | null
  /** Cycles observés où le bond a porté au moins un lien. Zéro sur toute la
   *  session : tunnel jamais monté, et « 0 % propre » ne juge pas la liaison. */
  cycles_in_bond: number
}

/** Ce que les modems partageaient, en part de temps — PAR SESSION.
 *
 *  Un chiffre global mélangerait des régimes opposés : mesuré, les quatre
 *  modems sont sur la même station 0,2 % du temps à une halte et 99,7 % à une
 *  autre. Chaque part a son propre dénominateur : un cycle où l'un des liens ne
 *  publie pas le champ n'est pas un cycle sans partage, c'est un cycle qu'on ne
 *  sait pas juger, et il rend `null` plutôt que zéro. */
export interface PathDiversity {
  cycles: number
  same_site_pct: number | null
  same_cell_pct: number | null
  same_earfcn_pct: number | null
  /** Le dénominateur de CHAQUE part (C-12). Optionnels : un serveur d'une
   *  version en arrière ne les publie pas, et l'écran se tait alors. */
  site_cycles?: number
  cell_cycles?: number
  earfcn_cycles?: number
  operators: number
}

export interface SessionSummary {
  session: Session
  links: SessionLink[]
  cells: CellLatency[]
  availability: BondAvailability | null
  diversity: PathDiversity | null
  mb_up: number | null
  mb_down: number | null
  lat: number | null
  lon: number | null
  radius_m: number | null
  /** Stations de base distinctes. `null` : aucune n'est identifiable — on ne
   *  sait pas, ce n'est pas « zéro station ». */
  sites: number | null
  /** La fiche porte-t-elle sur TOUTE la session ? Une session de trois semaines
   *  — un châssis allumé depuis trois semaines et supervisé pour la première
   *  fois — ne se relit pas en entier sans faire tomber le superviseur : on en
   *  lit les dernières vingt-quatre heures. Volumes, coupures et barycentre sont
   *  tous des totaux ; taire la fenêtre ferait lire un résumé partiel comme un
   *  résumé complet. */
  partial: boolean
  window_start: string | null
  window_end: string | null
}
