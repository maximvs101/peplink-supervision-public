import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Le front parle au backend en chemins relatifs (/api, /ws) : le proxy de dev évite
// tout problème de CORS et permet d'ouvrir l'interface depuis un téléphone du LAN.
export default defineConfig({
  plugins: [react()],
  test: {
    // jsdom : les tests de composants vérifient des règles d'affichage
    // (une donnée absente n'est pas une donnée mauvaise), pas du pixel.
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    include: ['src/**/*.test.{ts,tsx}'],
    // Vitest vide les feuilles de style, `?raw` compris. `index.css` porte les
    // jetons des deux thèmes, et leurs contrastes se vérifient sur son texte
    // (R-0.1, `lib/theme.test.ts`) : on la laisse passer, elle seule.
    css: { include: [/index\.css/] },
  },
  server: {
    port: 5173,
    host: true,
    proxy: {
      // `xfwd` : le mandataire dit d'où vient la requête. Sans lui, un
      // téléphone du LAN se présente au serveur en 127.0.0.1 et franchit le
      // garde des routes locales — appairage, capture brute.
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true, xfwd: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
})
