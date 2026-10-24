import { Component, type ErrorInfo, type ReactNode } from 'react'

/**
 * Le filet d'un ÉCRAN — tableau de bord, fiche d'un lien, découverte.
 *
 * Seul le module d'historique en avait un (`ChartBoundary`). Partout ailleurs,
 * une erreur de rendu démontait l'arbre jusqu'à la racine : la page noire du
 * 19 septembre n'était pas un cas, c'était une classe — six accès sans repli
 * sur des champs que le serveur peut ne pas publier, et chacun emportait la
 * supervision entière. Le bandeau de tête et la navigation vivent AU-DESSUS
 * de cette limite : quand un écran tombe, on voit encore où l'on est et l'on
 * peut aller ailleurs.
 *
 * `key` sur l'identité de l'écran : changer d'équipement ou de lien remet la
 * limite à neuf, sinon l'écran suivant hériterait de la panne du précédent.
 */
export class EcranBoundary extends Component<
  { children: ReactNode; titre?: string },
  { erreur: Error | null }
> {
  state: { erreur: Error | null } = { erreur: null }

  static getDerivedStateFromError(erreur: Error) {
    return { erreur }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // La console du navigateur est le seul journal disponible sur un poste de
    // terrain : avaler la cause rendrait la panne indiagnosticable.
    console.error('Écran indisponible', error, info.componentStack)
  }

  render() {
    if (this.state.erreur === null) return this.props.children
    return (
      <section className="panel" role="alert">
        <div className="panel-head"><h2>{this.props.titre ?? 'Cet écran'}</h2></div>
        <div className="panel-body">
          <div className="notice is-warn">
            Écran non affiché : {this.state.erreur.message || 'erreur de rendu'}.
          </div>
        </div>
      </section>
    )
  }
}
