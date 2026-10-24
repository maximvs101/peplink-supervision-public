/** Export CSV de la courbe AFFICHÉE, fabriqué dans le navigateur.
 *
 *  Exactement la série tracée — même fenêtre, même grandeur, même unité, mêmes
 *  points retirés — et rien d'autre : ni série, ni position, ni SSID. Le
 *  serveur sait exporter une session entière (`/sessions/{id}/export.csv`),
 *  pas « ce que je regarde » : une fenêtre de trente minutes, une enveloppe,
 *  une grandeur convertie en Mbit/s. Refaire la requête côté serveur aurait
 *  donné une AUTRE série que celle de l'écran — c'est précisément ce que
 *  l'export doit éviter.
 *
 *  Même convention que les exports du projet (`backend/temoin/export.py`,
 *  `backend/app/api/routes.py`) : BOM UTF-8, séparateur « ; », virgule
 *  décimale, en-tête en lignes « # clé;valeur », horodatage local lisible et
 *  fuseau dit — pour s'ouvrir tel quel dans un tableur en français. */

const BOM = '﻿'
const FIN_LIGNE = '\r\n'

/** Une ligne de la courbe, telle qu'elle est tracée. */
export interface LigneCourbe {
  /** Instant du relevé, en millisecondes. */
  t: number
  /** Valeur relevée par le superviseur, déjà convertie dans l'unité affichée. */
  value: number | null
  /** Valeur comblée par l'historique du routeur (R-3.1), tracée à part. */
  comble: number | null
  /** Taux retiré de la courbe faute de paquets : la cellule reste vide, et la
   *  raison est dite — une absence de mesure n'est pas une mesure basse. */
  retire: boolean
}

export interface CourbeExportee {
  /** Le NOM de l'équipement, jamais son identifiant opaque. */
  equipement: string
  /** Le libellé court du lien (« Cellulaire 4 »), jamais son nom configuré :
   *  celui-ci contient couramment un numéro de ligne. */
  lien: string
  grandeur: { key: string; label: string; unit: string }
  /** La fenêtre en clair (« 30 min », « session du … ») et son jeton pour le
   *  nom de fichier (« 30min », « session-12 »). */
  fenetre: { libelle: string; jeton: string }
  /** La courbe est-elle une enveloppe (minimum et maximum par tranche) ? */
  enveloppe: boolean
  /** Mesures présentes dans la fenêtre avant allègement. */
  disponibles: number
  /** Seuil de paquets sous lequel un taux est retiré. */
  seuilPaquets: number
  lignes: LigneCourbe[]
}

const deux = (n: number) => String(n).padStart(2, '0')

/** « 2026-08-30 16:00:05 », à l'heure du poste — comme `temoin/export.py`. */
export const instantLocal = (t: number): string => {
  const d = new Date(t)
  return `${d.getFullYear()}-${deux(d.getMonth() + 1)}-${deux(d.getDate())} `
    + `${deux(d.getHours())}:${deux(d.getMinutes())}:${deux(d.getSeconds())}`
}

/** « UTC+02:00 » à l'instant donné : heure d'été ou d'hiver. */
export const fuseau = (t: number): string => {
  const minutes = -new Date(t).getTimezoneOffset()
  const signe = minutes < 0 ? '-' : '+'
  const abs = Math.abs(minutes)
  return `UTC${signe}${deux(Math.floor(abs / 60))}:${deux(abs % 60)}`
}

/** Une cellule numérique : VIDE pour une absence — jamais 0 —, virgule
 *  décimale, six décimales au plus sans zéros traînants (`_cellule`). */
export const nombre = (v: number | null): string => {
  if (v === null || !Number.isFinite(v)) return ''
  if (Number.isInteger(v)) return String(v)
  const s = v.toFixed(6).replace(/0+$/, '').replace(/\.$/, '').replace('.', ',')
  return s === '-0' ? '0' : s
}

/** Une cellule de texte : sans séparateur, saut de ligne ni guillemet, et sans
 *  amorce de formule (= + - @) que le tableur exécuterait (`_texte`). */
export const texte = (v: string): string => {
  let s = v.replace(/[;\r\n"]/g, ' ').split(/\s+/).filter(Boolean).join(' ')
  if (s && '=+-@'.includes(s[0])) s = `'${s}`
  return s
}

export function courbeCsv(c: CourbeExportee): string {
  const premier = c.lignes[0]?.t ?? Date.now()
  const traces = c.lignes.length
  const lignes = [
    `# équipement;${texte(c.equipement)}`,
    `# lien;${texte(c.lien)}`,
    `# grandeur;${texte(`${c.grandeur.label} (${c.grandeur.unit})`)}`,
    `# fenêtre;${texte(c.fenetre.libelle)}`,
    // Dit en tête, parce qu'une enveloppe lue comme une série complète fait
    // conclure à des interruptions qui n'ont pas eu lieu.
    `# série;${c.enveloppe
      ? `enveloppe : minimum et maximum par tranche, ${traces} points pour ${c.disponibles} mesures`
      : 'complète'}`,
    `# fuseau;${fuseau(premier)}`,
    `instant;${texte(`valeur (${c.grandeur.unit})`)};source;remarque`,
  ]
  for (const l of c.lignes) {
    const duRouteur = l.comble !== null
    const remarque = l.retire ? `retiré : moins de ${c.seuilPaquets} paquets comptés` : ''
    lignes.push([
      instantLocal(l.t),
      nombre(duRouteur ? l.comble : l.value),
      duRouteur ? 'routeur' : 'superviseur',
      remarque,
    ].join(';'))
  }
  return BOM + lignes.join(FIN_LIGNE) + FIN_LIGNE
}

/** Un morceau de nom de fichier : lettres, chiffres, tirets — accents ôtés,
 *  pour que le nom survive à tous les systèmes de fichiers et messageries. */
const morceau = (s: string): string =>
  s.normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^A-Za-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'x'

/** « courbe-MBX-Cellulaire-4-rsrp-30min.csv », suffixé « -enveloppe » quand
 *  la courbe en est une : le nom seul dit déjà ce qu'on ouvre. */
export const nomFichierCourbe = (c: CourbeExportee): string =>
  ['courbe', morceau(c.equipement), morceau(c.lien), morceau(c.grandeur.key), morceau(c.fenetre.jeton)]
    .join('-') + (c.enveloppe ? '-enveloppe' : '') + '.csv'

/** Remet le fichier au navigateur : un Blob, un lien de téléchargement, un
 *  clic. Rien ne part sur le réseau. */
export function telecharger(nom: string, contenu: string): void {
  const blob = new Blob([contenu], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = nom
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Après le clic : certains navigateurs lisent l'URL de façon asynchrone.
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}
