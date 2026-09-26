import type { RadioSample } from '../types'

/**
 * Combien de trajets radio distincts une liaison agrégée emprunte-t-elle vraiment ?
 *
 * Compter les SIM ne répond pas à la question. Mesuré sur le matériel : dans 84 %
 * des cycles les quatre SIM sont sur la MÊME station de base, et dans 17,5 % sur
 * la même cellule exacte. Quatre modems sur un seul secteur se partagent le même
 * pool de ressources radio et le même raccordement : l'agrégation apporte alors
 * de la redondance d'équipement, pas de la capacité, et une panne de site les
 * emporte tous les quatre ensemble.
 *
 * Deux niveaux, parce qu'ils n'appellent pas la même réaction :
 *
 *  - **même station** (`enb_id`) — mêmes mât et raccordement, mais des secteurs
 *    ou des porteuses différentes : la capacité peut s'additionner.
 *  - **même cellule** (`cell_id` ET `earfcn`) — même secteur, même porteuse,
 *    mêmes ressources : elle ne s'additionne pas. C'est le cas actionnable, et
 *    il l'est justement parce que le véhicule est à l'arrêt — on peut le décaler,
 *    ou forcer une autre bande.
 *
 * Le calcul ne sort jamais du routeur : il compare entre eux des identifiants que
 * les modems rapportent. Il ne nomme ni ne localise aucune antenne.
 */
export interface Diversity {
  /** wan_id -> les autres liens qui partagent sa cellule exacte. */
  sameCell: Map<string, string[]>
  /** Les GROUPES eux-mêmes : identifiant de cellule -> liens qui l'occupent.
   *
   *  `grouper` les construit déjà et ils étaient jetés ici. Le plan du bond les
   *  recomposait donc depuis `sameCell`, en recollant des wan_id avec des « + »
   *  pour les redécouper trois lignes plus loin — une seconde implémentation du
   *  même regroupement, non testée, et qui aurait fusionné deux groupes si un
   *  wan_id avait jamais contenu un « + ». */
  cellGroups: Map<string, string[]>
  /** wan_id -> les autres liens qui partagent sa station de base. */
  sameSite: Map<string, string[]>
  /** Cellules distinctes parmi les liens cellulaires connectés. */
  cells: number
  /** Stations de base distinctes. */
  sites: number
  /** Liens cellulaires connectés pris en compte. */
  links: number
  /** Ceux dont la STATION a pu être déterminée. Le parseur refuse de décomposer
   *  un identifiant 5G SA — « le découpage serait une invention » — si bien que
   *  `sites` peut valoir 0 alors que des cellules sont connues. */
  attached: number
  /** Taille du plus grand groupe partageant une même cellule. L'écran la
   *  recalculait par `located - cells + 1`, formule juste seulement s'il existe
   *  EXACTEMENT un groupe partagé : sur deux paires, elle annonçait trois
   *  modems quand le vrai maximum est deux. */
  largestSharedCell: number
  /** Ceux d'entre eux qui ont RAPPORTÉ une cellule : eux seuls sont comparables.
   *
   *  `cells < links` mélangeait deux choses : des modems qui partagent une
   *  cellule, et des modems dont on ne sait pas laquelle ils occupent. Une SIM
   *  qui vient de se rattacher sans `cellTower.cellId` faisait afficher « Des
   *  modems partagent une cellule », affirmation fausse, et sans qu'aucune tuile
   *  ne porte le badge correspondant. Sur un modèle qui ne publie aucun
   *  `cellId` — 5G NR, firmware partiel —, l'écran annonçait « 2 liens
   *  cellulaires · 0 cellules · 0 sites » assorti du même avertissement : le
   *  papier peint permanent que ce bandeau a justement été écrit pour éviter. */
  located: number
  /** Liens connectés dont la cellule est inconnue. Une absence de mesure. */
  unknown: number
}

function grouper(
  samples: RadioSample[],
  cle: (s: RadioSample) => string | null,
): { groupes: Map<string, string[]>; parWan: Map<string, string[]> } {
  const groupes = new Map<string, string[]>()
  for (const sample of samples) {
    const k = cle(sample)
    if (k === null) continue
    groupes.set(k, [...(groupes.get(k) ?? []), sample.wan.wan_id])
  }
  const parWan = new Map<string, string[]>()
  for (const membres of groupes.values()) {
    for (const wanId of membres) {
      parWan.set(wanId, membres.filter((autre) => autre !== wanId))
    }
  }
  return { groupes, parWan }
}

export function pathDiversity(samples: RadioSample[]): Diversity {
  // Seuls les liens cellulaires CONNECTÉS comptent : un lien filaire n'emprunte
  // aucune cellule, et un modem déconnecté rapporte la dernière qu'il a vue.
  const cellulaires = samples.filter((s) => s.wan.kind === 'cellular' && s.wan.connected)

  // La porteuse fait partie de l'identité : deux secteurs peuvent porter le même
  // identifiant de cellule sur des canaux différents, et ce sont alors des
  // ressources distinctes.
  //
  // Et l'une comme l'autre appartiennent à un OPÉRATEUR : deux SIM de deux
  // réseaux aux numéros égaux par hasard étaient déclarées « même station ».
  // Une porteuse inconnue ne départage pas, mais ne réunit pas non plus : la
  // clé devient propre au lien (C-40).
  const reseau = (s: RadioSample) => s.wan.plmn ?? '?'
  const cellule = grouper(cellulaires, (s) =>
    s.wan.cell_id === null
      ? null
      : s.wan.earfcn === null
        ? `${reseau(s)}/${s.wan.cell_id}/?/${s.wan.wan_id}`
        : `${reseau(s)}/${s.wan.cell_id}/${s.wan.earfcn}`,
  )
  const site = grouper(cellulaires, (s) =>
    s.wan.enb_id === null ? null : `${reseau(s)}/${s.wan.enb_id}`)

  const situes = cellulaires.filter((s) => s.wan.cell_id !== null).length
  // Un lien peut porter un identifiant de cellule sans qu'on sache la DÉCOMPOSER
  // en station : le parseur refuse de le faire en 5G SA, où « le découpage
  // serait une invention ». `sites` valait alors 0 sur une mesure qui existe, et
  // l'écran écrivait « N cellules · 0 site » — un zéro à la place d'une absence,
  // là où `located` traite correctement le cas symétrique pour les cellules.
  const rattaches = cellulaires.filter((s) => s.wan.enb_id !== null).length

  // Le plus grand groupe partageant une cellule. L'appelant le recalculait par
  // `located - cells + 1`, juste seulement s'il existe EXACTEMENT un groupe
  // partagé : sur deux paires, la formule annonçait trois modems quand le vrai
  // maximum est deux.
  const plusGrandPartage = [...cellule.groupes.values()].reduce(
    (max, membres) => Math.max(max, membres.length), 0,
  )

  return {
    sameCell: cellule.parWan,
    cellGroups: cellule.groupes,
    sameSite: site.parWan,
    cells: cellule.groupes.size,
    sites: site.groupes.size,
    links: cellulaires.length,
    located: situes,
    unknown: cellulaires.length - situes,
    attached: rattaches,
    largestSharedCell: plusGrandPartage,
  }
}
