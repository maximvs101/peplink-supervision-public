/** Formatage des grandeurs affichées.
 *
 *  Une seule règle gouverne tout ce fichier, et c'est celle du projet : une
 *  grandeur absente n'est pas une grandeur mauvaise. Un tiret dit « on ne
 *  sait pas » ; « NaN », « Infinity », « Invalid Date » ou « -7200 s »
 *  affirment quelque chose de faux avec l'aplomb d'une mesure.
 *
 *  Les gardes se contentaient de `null`/`undefined`, et `fmtNumber` était seul
 *  à ajouter `Number.isNaN`. Tout le reste passait : mesuré à l'exécution,
 *  `fmtInt(NaN)` rendait « NaN », `fmtThroughput(Infinity)` « Infinity Mbit/s »,
 *  `fmtUptime(-7200)` « -7200 s » et `fmtPosition(NaN, NaN)` « NaN, NaN ».
 *  `fmtTime('')` faisait pire : `new Date('').toISOString()` LÈVE, et les deux
 *  appels de `HistoryChart` passaient par cette conversion. */

import type { Id } from '../types'

/** Le seul garde numérique du fichier : écarte `null`, `undefined`, `NaN` et
 *  les deux infinis d'un coup. `Number.isFinite` fait les quatre. */
export const fini = (value: number | null | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value)

/** Un nombre décimal à la française : virgule décimale, espace des milliers —
 *  comme les entiers de `fmtInt`. L'écran écrivait « 0.2 dB » et « 87.7 Mo » à
 *  côté de « 1 428 o » : deux conventions sur une même page (A-8). Les
 *  coordonnées GPS gardent le point : « 48,85661, 2,35222 » serait ambigu. */
export const decimal = (value: number, digits = 1): string =>
  value.toLocaleString('fr-FR', { minimumFractionDigits: digits, maximumFractionDigits: digits })

export const fmtNumber = (value: number | null | undefined, digits = 1, unit = ''): string =>
  fini(value) ? `${decimal(value, digits)}${unit ? ' ' + unit : ''}` : '—'

export const fmtInt = (value: number | null | undefined): string =>
  fini(value) ? value.toLocaleString('fr-FR') : '—'

/** Un IDENTIFIANT : jamais mis en forme comme une quantité.
 *
 *  `fmtInt` pose des séparateurs de milliers, ce qui est juste pour un
 *  volume ou un MTU et faux pour un numéro de cellule, de station ou de zone
 *  de suivi : « 130 392 066 » ne se recolle plus à ce qu'affiche le routeur, ne
 *  se copie pas, ne se cherche pas. La revue l'avait fait corriger dans la
 *  lecture du plan ; la fiche de lien portait le même défaut sur quatre
 *  champs, vu sur le MAX HD2. Un identifiant s'écrit tel quel.
 *
 *  Il n'accepte QUE des chaînes : le backend publie les identifiants ainsi
 *  (`Id` dans `types.ts`), donc un nombre ici est une grandeur qui s'est
 *  trompée de porte — et un identifiant passé à `fmtInt` est refusé de même.
 *  Le compilateur tient les deux sens ; ce fichier n'a plus à les deviner. */
export const fmtId = (value: Id | null | undefined): string => value || '—'

/** Débit : kbit/s -> Mbit/s au-delà de 1000.
 *
 *  La bascule était testée AVANT l'arrondi : 999,6 kbit/s restait sous le seuil,
 *  puis `toFixed(0)` rendait « 1000 kbit/s » — une unité de trop, sur une
 *  échelle qui n'existe pas. On arrondit donc d'abord, on compare ensuite. */
export function fmtThroughput(kbps: number | null | undefined): string {
  if (!fini(kbps)) return '—'
  return Math.round(kbps) >= 1000
    ? `${decimal(kbps / 1000, 1)} Mbit/s`
    : `${decimal(kbps, 0)} kbit/s`
}

/** Une durée en secondes -> "2 h 18 min".
 *
 *  Une durée NÉGATIVE rend un tiret, et c'est délibéré. Deux appelants la
 *  calculent par différence de dates — `EventLog` entre les deux bouts d'un
 *  épisode, `Overview` depuis la mise sous tension — et une durée négative n'y
 *  signifie jamais « il s'est écoulé moins que rien » : elle signifie que les
 *  deux horodatages ne viennent pas de la même horloge. Afficher « -5 s »
 *  publierait ce désaccord comme s'il s'agissait d'une mesure.
 *
 *  Le seuil de la minute rendait la valeur BRUTE : 42,7 secondes s'affichaient
 *  « 42.7 s », avec son point décimal anglais, au milieu d'un écran où toutes
 *  les autres durées sont entières. */
export function fmtUptime(seconds: number | null | undefined): string {
  if (!fini(seconds) || seconds < 0) return '—'
  if (seconds < 60) return `${Math.round(seconds)} s`
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days} j ${hours} h`
  if (hours > 0) return `${hours} h ${minutes} min`
  return `${minutes} min`
}

/** Accepte ce que les appelants ont sous la main — une date ISO, un nombre de
 *  millisecondes, un `Date` — plutôt que d'imposer l'ISO.
 *
 *  Les deux appels de `HistoryChart` recevaient de `recharts` un instant en
 *  millisecondes et le repassaient par `new Date(t).toISOString()` pour obéir à
 *  l'ancienne signature. Cette conversion LÈVE `RangeError` sur une valeur non
 *  datable, à l'intérieur d'un rendu : un axe dont une graduation n'est pas un
 *  nombre faisait tomber le graphe entier, pas seulement son étiquette. */
const horloge = (
  value: string | number | Date,
  options: Intl.DateTimeFormatOptions,
): string => {
  const date = value instanceof Date ? value : new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString('fr-FR', options)
}

export const fmtTime = (value: string | number | Date): string =>
  horloge(value, { hour: '2-digit', minute: '2-digit', second: '2-digit' })

export const fmtClock = (value: string | number | Date): string =>
  horloge(value, { hour: '2-digit', minute: '2-digit' })

/** Position GPS du routeur, en degrés décimaux : « 48.85641, 2.35234 ».
 *
 *  Cinq décimales, soit environ 1,1 m à nos latitudes — au-delà on afficherait
 *  du bruit, le GPS d'un routeur embarqué se situant à quelques mètres près.
 *  Rend `null` plutôt qu'un tiret quand la position manque : l'appelant efface
 *  alors la ligne entière au lieu de montrer un champ vide. C'est aussi ce
 *  qu'une coordonnée non finie doit rendre — « NaN, NaN » dessinait une ligne
 *  de position pour un point que personne n'a mesuré. */
export function fmtPosition(
  lat: number | null | undefined,
  lon: number | null | undefined,
): string | null {
  if (!fini(lat) || !fini(lon)) return null
  // L'ÎLE NULLE. Zéro degré de latitude ET de longitude désigne un point du
  // golfe de Guinée où aucun véhicule de reportage ne stationnera jamais : c'est
  // la signature d'un récepteur sans fixation, pas une position. La rendre
  // dessinait une ligne de position pour un lieu que personne n'a mesuré — et
  // sur une carte, elle placerait le véhicule à 5 000 km des côtes.
  if (lat === 0 && lon === 0) return null
  return `${lat.toFixed(5)}, ${lon.toFixed(5)}`
}


/** « SIM 4 », « WAN 1 », « Wi-Fi 3 » — le SUPPORT du lien, jamais une
 *  supposition sur ce qu'il transporte.
 *
 *  Cette correspondance existait en six exemplaires : trois fonctions
 *  identiques au caractère près dans `BondPlan`, `EventLog` et `HistoryChart`,
 *  et trois versions en ligne dans `LinkTable`, `WanCard` et `WanDetail`. Six
 *  copies d'une seule décision de produit : le jour où un support nouveau
 *  mérite un mot à lui, cinq fichiers changent et le sixième continue d'écrire
 *  l'ancien sur un autre écran. `EventLog` porte d'ailleurs le souvenir de
 *  cette panne-là dans son propre commentaire. */
export const etiquetteSupport = (kind: string): string =>
  kind === 'ethernet' ? 'WAN' : kind === 'wifi' ? 'Wi-Fi' : 'SIM'
