import { Children, useId, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'

/**
 * Un fait, et sa justification ATTEIGNABLE.
 *
 * Elle ne vivait que dans `title=` : quinze attributs, six notes sur le
 * tableau de bord — invisibles au toucher (le téléphone du LAN n'a pas de
 * survol), inatteignables au clavier, rarement lus par un lecteur d'écran.
 * Le survol reste ; s'y ajoutent un bouton — donc le clavier et le doigt —
 * et un texte dépliable que le lecteur d'écran annonce (`aria-expanded`,
 * `aria-controls`). Rien n'est perdu, et rien ne s'impose : replié, c'est le
 * même souligné pointillé qu'avant.
 */
export function Pourquoi(
  { children, texte, className, style }:
  { children: ReactNode; texte: string; className?: string; style?: CSSProperties },
) {
  const [ouvert, setOuvert] = useState(false)
  const id = useId()
  return (
    <>
      <button
        type="button"
        className={className ? `pourquoi ${className}` : 'pourquoi'}
        style={style}
        title={texte}
        aria-expanded={ouvert}
        aria-controls={id}
        onClick={() => setOuvert((o) => !o)}
      >
        {children}
      </button>
      {ouvert && (
        <span id={id} className="pourquoi-texte" role="note">{texte}</span>
      )}
    </>
  )
}

/**
 * Une note d'écran : un FAIT court, et sa justification à un survol.
 *
 * Ce tableau de bord se consulte d'un coup d'œil, et il avait fini par porter
 * quatre mille cinq cents caractères de prose — l'équivalent de huit cents mots
 * répartis en quinze blocs, sur un seul écran. Chaque phrase se défendait :
 * elles disaient pourquoi un chiffre est ce qu'il est, sur quelle mesure une
 * règle se fonde, ce qu'un zéro ne veut pas dire. Mais une explication qu'on
 * doit traverser pour atteindre la mesure suivante coûte plus qu'elle
 * n'apporte : on cesse de lire, et les phrases qui comptaient disparaissent
 * avec les autres.
 *
 * D'où ce découpage, et il est le même partout :
 *
 *   - ce qui est VISIBLE est le fait, en une phrase ;
 *   - ce qui le JUSTIFIE — la mesure, le seuil, le piège écarté — vit dans
 *     `pourquoi`, et se lit au survol.
 *
 * Rien n'est perdu : le détail reste à portée, et il se signale par un souligné
 * pointillé plutôt que de s'imposer. Ce qui n'a PAS de justification n'en
 * reçoit pas de faux : `pourquoi` est facultatif.
 */
export function Note(
  { children, pourquoi, ton = 'neutre' }:
  { children: ReactNode; pourquoi?: string; ton?: 'neutre' | 'alerte' },
) {
  return (
    <p className={ton === 'alerte' ? 'panel-note is-warn' : 'panel-note'}>
      {pourquoi === undefined ? (
        children
      ) : (
        <Pourquoi texte={pourquoi}>{children}</Pourquoi>
      )}
    </p>
  )
}

/**
 * Le pied de notes d'un panneau : les faits s'y écoulent EN LIGNES.
 *
 * Empilées, ces notes coûtaient 385 px sur un écran de 1 515 — le quart de la
 * page — pour environ six cents caractères. Chacune occupait une bande pleine
 * largeur pour une demi-phrase, et la page devenait longue non pas à cause de
 * ce qu'elle dit mais de la façon dont elle le range. Mises bout à bout, elles
 * tiennent en deux ou trois lignes, séparées par un filet.
 *
 * Le conteneur s'efface quand aucune note ne s'affiche : `Children.toArray`
 * écarte les `false` et les `null` des conditions, donc un panneau sans note à
 * dire ne gagne pas un filet horizontal pour rien.
 */
export function Notes({ children }: { children: ReactNode }) {
  const items = Children.toArray(children)
  if (items.length === 0) return null
  return <div className="panel-notes">{items}</div>
}


/** Ce qu'on ne SAIT pas, écrit d'une seule façon.
 *
 *  Le cadratin gris était posé à la main en dix endroits. C'est la convention
 *  centrale de cet écran — « un tiret dit "on ne sait pas" » — et elle se
 *  maintenait donc en dix exemplaires : le jour où l'on veut distinguer
 *  visuellement « pas mesuré » de « pas mesurable », il faut les retrouver
 *  tous. */
export function Absent() {
  return <span className="absent">—</span>
}
