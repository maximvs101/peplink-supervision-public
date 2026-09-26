import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import './index.css'
import { appliquerThemeMemorise } from './lib/themeChoice'

// Le thème retenu sur ce poste, avant le premier rendu (R-0.1).
appliquerThemeMemorise()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
