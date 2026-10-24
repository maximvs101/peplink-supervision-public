import { Component, type ErrorInfo, type ReactNode } from 'react'

/**
 * Le filet du module d'historique, et le seul endroit du projet qui en ait un.
 *
 * `recharts` pèse les deux tiers du code compilé et part donc en chargement
 * différé. `<Suspense>` couvre l'ATTENTE, jamais l'ÉCHEC : si le fichier
 * manque — un déploiement partiel, un cache vidé pendant une mise à jour, un
 * disque de terrain qui rend une lecture — la promesse est rejetée, et React
 * démonte l'arbre jusqu'à la racine faute de limite. L'écran entier devenait
 * blanc : plus de bandeau, plus de table des liens, plus rien. C'est-à-dire
 * qu'un module de SECOND niveau emportait la supervision.
 *
 * Une erreur de rendu du graphe est traitée pareil : mieux vaut un panneau qui
 * dit ce qui manque que la disparition de tout ce qui allait bien.
 */
export class ChartBoundary extends Component<
  { children: ReactNode },
  { tombe: boolean }
> {
  state = { tombe: false }

  static getDerivedStateFromError() {
    return { tombe: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // La console du navigateur est le seul journal disponible sur un poste de
    // terrain : avaler la cause rendrait la panne indiagnosticable.
    console.error('Historique indisponible', error, info.componentStack)
  }

  render() {
    if (!this.state.tombe) return this.props.children
    return (
      <section className="panel">
        <div className="panel-head"><h2>Historique</h2></div>
        <div className="panel-body">
          <div className="notice">
            Tracé non chargé.
          </div>
        </div>
      </section>
    )
  }
}
