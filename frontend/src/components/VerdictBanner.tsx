import { useEffect, useMemo, useRef, useState } from 'react'

import type { RadioSample } from '../types'
import { fmtUptime } from '../lib/format'
import { VERDICT_COLOR, linkVerdict, smoothVerdict, type LinkVerdict, type Verdict } from '../lib/verdict'
import type { BondRole } from '../lib/bondRole'

interface Props {
  samples: RadioSample[]
  live: boolean
  /** État de la liaison avec l'équipement, tel que le serveur le constate. */
  status?: string
  /** Âge du relevé le plus récent, mesuré par le serveur. */
  ageSeconds?: number | null
  /** Cause de l'échec, telle que le serveur la constate (expurgée de tout
   *  secret côté serveur). Affichée seulement quand la liaison ne va pas. */
  error?: string | null
  /** Période de scrutation du serveur. Le seuil de péremption s'en déduit :
   *  une constante figée ici serait fausse dès que la période change. */
  periodSeconds?: number | null
  /** Le tunnel est-il le chemin du trafic ? Quand il ne l'est pas, ses griefs
   *  ne font plus le verdict de la liaison — ils sont dits auprès du bond. */
  role?: BondRole
  /** Garde-fou absolu de latence, publié par le serveur. */
  latencyThresholdMs?: number | null
  /** Le bond a-t-il déjà porté dans cette session ? Publié par le serveur. */
  tunnelAttendu?: boolean | null
  /** Ce qui tient le tunnel à terre, lu sur les profils (R-1.5). */
  tunnelCause?: string | null
  /** Les WAN jamais connectés depuis l'allumage, publiés par le serveur. */
  neverConnected?: string[]
  /** Le verdict brut, déjà calculé par l'enveloppe de l'équipement. Fourni,
   *  il n'est pas recalculé ici : le tableau de bord en a besoin aussi, et le
   *  même calcul se faisait deux fois par trame. Absent (bandeau monté seul),
   *  le bandeau le calcule à partir des props. */
  verdict?: LinkVerdict
}

/**
 * Réponse à « est-ce que ça marche ? », en tête d'écran et en une phrase.
 *
 * Trois garde-fous, chacun corrigeant une façon d'afficher du vert à tort :
 * l'état de l'équipement (les relevés en mémoire survivent à sa panne), l'âge
 * du relevé mesuré par le serveur (comparer deux horloges laissait afficher
 * « à l'instant » sur des données mortes), et une hystérésis (un bandeau qui
 * clignote toutes les trois secondes est un bandeau qu'on cesse de regarder).
 *
 * Le verdict RETENU porte à la fois la couleur et le texte. Les dissocier —
 * couleur lissée, texte instantané — produisait un bandeau « Liaison OK » en
 * rouge pendant toute la durée du lissage.
 */
export function VerdictBanner({
  samples, live, status, ageSeconds, error, periodSeconds, role,
  latencyThresholdMs, tunnelAttendu = null, tunnelCause = null, neverConnected, verdict,
}: Props) {
  // Une CLÉ, et non le tableau : il est neuf à chaque relecture du résumé.
  const jamais = (neverConnected ?? []).join(',')
  // Le verdict dépend de ses vraies entrées, et l'effet plus bas dépend du
  // verdict. Une clé bricolée (verdict | âge | nombre de relevés) tenait lieu de
  // dépendance et ratait tout changement de DÉTAIL : un lien déconnecté reste
  // dans la liste avec `connected: false`, donc quatre liens dont un tombé puis
  // trois tombés produisaient la même clé, et l'écran continuait d'annoncer
  // « 1 déconnecté » pendant que la fonction de verdict en comptait trois.
  // La stabilité de `samples` d'un rendu à l'autre est la responsabilité de
  // l'appelant, qui le mémoïse : ici, une nouvelle liste EST un nouveau relevé.
  const raw = useMemo(
    () => verdict ?? linkVerdict({
      samples, status, ageSeconds, periodSeconds, role, latencyThresholdMs, tunnelAttendu, tunnelCause,
      neverConnected: jamais ? jamais.split(',') : [],
    }),
    [verdict, samples, status, ageSeconds, periodSeconds, role, latencyThresholdMs, tunnelAttendu, tunnelCause, jamais],
  )

  // Repère de CYCLE, et non de trame. `_publish` émet un relevé par WAN et par
  // cycle : sur onze liens, `raw` changeait onze fois par cycle, l'historique se
  // remplissait onze fois plus vite que prévu, et `slice(-12)` ne couvrait plus
  // qu'un seul cycle — un unique cycle de retour faisait passer le bandeau de
  // « dégradée » à « Liaison OK » là où `leave = 5` exige cinq constats. La même
  // constante ne voulait donc pas dire la même chose selon le matériel : cinq
  // cycles sur un Dome mono-lien, moins d'un sur un MAX HD4 MBX.
  //
  // Le plus ANCIEN horodatage de la liste n'avance que lorsque le DERNIER lien a
  // été rafraîchi, c'est-à-dire exactement une fois par cycle, quel que soit le
  // nombre de liens. Il vaut aussi pour un équipement mono-lien.
  const cycle = useMemo(
    () => (samples.length === 0 ? 0 : Math.min(...samples.map((s) => Date.parse(s.ts)))),
    [samples],
  )

  const history = useRef<Verdict[]>([])
  const since = useRef<number>(Date.now())
  const [shown, setShown] = useState<LinkVerdict>(raw)
  // Miroir de ce qui est à l'écran, lisible depuis l'effet sans y figurer en
  // dépendance : l'effet ne doit se rejouer qu'à l'arrivée d'un relevé, sinon
  // il empilerait des doublons dans l'historique et l'hystérésis compterait faux.
  const shownVerdict = useRef<Verdict>(raw.verdict)
  // Le verdict affiché tient-il à un FAIT (mesure périmée, équipement qui ne
  // répond pas) plutôt qu'à une tendance ? Voir l'effet ci-dessous.
  const shownFactuel = useRef<boolean>(raw.stale || (status !== undefined && status !== 'connecté'))
  // Le verdict courant, lisible depuis l'effet sans y figurer en dépendance :
  // l'effet ne doit se rejouer qu'au CYCLE, pas à chaque relevé.
  const current = useRef(raw)
  current.current = raw
  const [, tick] = useState(0)

  // Changer d'équipement, c'est changer de sujet : l'historique, le chronomètre
  // et l'état affiché appartenaient au précédent. Constaté sur un vrai
  // changement de route — après douze cycles où A restait injoignable, B
  // répondait sain et le bandeau restait rouge le temps des cinq constats de
  // désescalade (~50 s), avec un badge « depuis 3 min » hérité de A.
  // Cette remise à zéro a existé ici, en branche de rendu. Elle était MORTE :
  // `Masthead` monte ce composant avec `key={routerId}`, donc React le démonte
  // et le remonte entièrement au changement d'équipement, et la branche n'était
  // jamais atteinte. Deux mécanismes pour la même garantie, dont un seul agit —
  // et celui qu'on lit n'est pas celui qui travaille. La clé est conservée : elle
  // purge TOUT l'état local, y compris ce qu'on oublierait d'énumérer ici.

  // Un constat par CYCLE, plus les transitions qui ne passent pas par les
  // relevés : un équipement qui cesse de répondre n'en produit plus, et le
  // bandeau doit pourtant escalader. `status` couvre sa panne, `stale` le
  // franchissement du seuil de péremption.
  useEffect(() => {
    const raw = current.current
    // Un verdict fondé sur un FAIT — la mesure est périmée, l'équipement ne
    // répond pas — n'est pas une tendance à lisser : quand le fait cesse, il
    // n'a plus rien à retenir. Sans cela, l'hystérésis gardait « Mesures
    // périmées — dernier relevé il y a 46 s » pendant cinq cycles, texte
    // figé, alors que les relevés arrivaient de nouveau toutes les trois
    // secondes. L'historique repart de ce constat-ci : la suite se lisse
    // comme d'habitude.
    const factuel = raw.stale || (status !== undefined && status !== 'connecté')
    if (shownFactuel.current && !factuel) {
      history.current = [raw.verdict]
      shownVerdict.current = raw.verdict
      shownFactuel.current = false
      since.current = Date.now()
      setShown(raw)
      return
    }
    // Les rendus intermédiaires jugeaient un tableau à moitié à jour — un lien
    // du cycle N et dix du cycle N−1 —, donc des états qui n'ont jamais existé.
    history.current = [...history.current, raw.verdict].slice(-12)
    const next = smoothVerdict(history.current, shownVerdict.current)
    // L'hystérésis retient l'affichage : on ne touche à rien, pas même au
    // détail, sous peine d'afficher le décompte d'un verdict qu'on n'annonce pas.
    if (next !== raw.verdict) return
    if (next !== shownVerdict.current) {
      shownVerdict.current = next
      since.current = Date.now()
    }
    shownFactuel.current = factuel
    setShown(raw)
  }, [cycle, status, raw.stale])

  // Rafraîchit la durée affichée sans dépendre de l'arrivée d'un relevé.
  useEffect(() => {
    const timer = window.setInterval(() => tick((n) => n + 1), 5000)
    return () => window.clearInterval(timer)
  }, [])

  const heldFor = Math.round((Date.now() - since.current) / 1000)
  // La cause n'a de sens que sur une liaison qui ne va pas : sur un équipement
  // connecté, `error` est au mieux le vestige de la panne précédente.
  const cause = status && status !== 'connecté' ? error : null

  // Deux cellules du bandeau de tête, et non une carte posée dans la page : le
  // verdict est la réponse à la question du produit, il occupe la place qui lui
  // revient. Il était composé en 18 px, quatrième plus grand élément de l'écran
  // derrière un chiffre de débit et un chevron décoratif.
  return (
    <>
      <div className="mast-verdict" style={{ ['--v' as string]: VERDICT_COLOR[shown.verdict] }}>
        <span className="mast-dot" />
        {/* Annoncé à un lecteur d'écran — le TITRE seul. Il ne change qu'aux
            transitions retenues par l'hystérésis ; le détail et l'âge du
            relevé bougent à chaque cycle, et les rendre « vivants » noierait
            l'annonce qui compte sous un décompte. `aria-live` et non
            `role="status"` : le titre reste un titre. */}
        <h1 aria-live="polite" aria-atomic="true">{shown.headline}</h1>
        {/* `title` parce que `.detail` coupe à l'ellipse : une cause longue —
            « Connection attempt failed: authentication rejected… » — se voit
            tronquée, ce qui est honnête, mais devenait irrécupérable sans aller
            au journal. Le survol rend la phrase entière. */}
        {shown.detail && (
          <span className="detail" title={shown.detail}>{shown.detail}</span>
        )}
        {/* Le serveur sait POURQUOI il n'aboutit pas — nom qui ne résout pas,
            connexion refusée, certificat rejeté. Le taire envoie chercher un
            câble débranché là où c'est un mot de passe qui a changé. */}
        {cause && <span className="detail">· {cause}</span>}
      </div>

      <div className="mast-stamps">
        {heldFor >= 30 && shown.verdict !== 'ok' && (
          // Même barème que les durées de session : « depuis 180 min » pour
          // trois heures se lisait mal, « 2880 min » pour deux jours ne se
          // lisait plus.
          <span className="stamp">
            <span className="v">{fmtUptime(heldFor)}</span>
            {/* Mesuré depuis l'ouverture de la page, pas depuis l'entrée dans
                l'état : « dans cet état » disait 1 min d'une panne de 2 h (C-42). */}
            <span className="lbl">observé depuis</span>
          </span>
        )}
        <span className={`stamp${raw.stale ? ' is-stale' : ''}`}>
          <span className="v">
            {raw.ageSeconds === null ? '—' : `${Math.round(raw.ageSeconds)} s`}
          </span>
          <span className="lbl">
            {/* L'âge vient de la mémoire du serveur, pas de la base : « jamais
                mesuré » se lisait sur un MBX dont la base gardait deux
                stationnements (30/09). */}
            {raw.ageSeconds === null
              ? 'aucun relevé depuis le lancement'
              : raw.stale
                ? 'relevé périmé'
                : 'dernier relevé'}
          </span>
        </span>
        {!live && (
          <span className="stamp is-stale">
            <span className="v">⚠</span>
            <span className="lbl">flux interrompu</span>
          </span>
        )}
      </div>
    </>
  )
}
