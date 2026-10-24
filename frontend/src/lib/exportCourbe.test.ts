import { describe, expect, it } from 'vitest'

import { type CourbeExportee, courbeCsv, nombre, nomFichierCourbe, texte } from './exportCourbe'

const t0 = new Date(2026, 7, 30, 16, 0, 5).getTime()

const courbe = (over: Partial<CourbeExportee> = {}): CourbeExportee => ({
  equipement: 'Véhicule École',
  lien: 'Cellulaire 4',
  grandeur: { key: 'tunnel_rx_kbps', label: 'Bond ↓', unit: 'Mbit/s' },
  fenetre: { libelle: '30 min', jeton: '30min' },
  enveloppe: false,
  disponibles: 3,
  seuilPaquets: 200,
  lignes: [
    { t: t0, value: 1.5, comble: null, retire: false },
    { t: t0 + 5_300, value: null, comble: null, retire: false },
    { t: t0 + 10_600, value: 0, comble: null, retire: false },
  ],
  ...over,
})

describe('export CSV de la courbe affichée', () => {
  it('suit la convention des exports du projet', () => {
    const csv = courbeCsv(courbe())
    // BOM UTF-8, séparateur « ; », fins de ligne CRLF comme `temoin/export.py`.
    expect(csv.startsWith('\ufeff# équipement;Véhicule École\r\n')).toBe(true)
    const lignes = csv.slice(1).split('\r\n')
    expect(lignes).toContain('# grandeur;Bond ↓ (Mbit/s)')
    expect(lignes).toContain('# fenêtre;30 min')
    expect(lignes).toContain('# série;complète')
    expect(lignes.some((l) => /^# fuseau;UTC[+-]\d\d:\d\d$/.test(l))).toBe(true)
    expect(lignes).toContain('instant;valeur (Mbit/s);source;remarque')
    // Horodatage local lisible, virgule décimale.
    expect(lignes).toContain('2026-08-30 16:00:05;1,5;superviseur;')
  })

  it('laisse vide une valeur absente, et garde un vrai zéro', () => {
    const lignes = courbeCsv(courbe()).split('\r\n')
    expect(lignes).toContain('2026-08-30 16:00:10;;superviseur;')
    expect(lignes).toContain('2026-08-30 16:00:15;0;superviseur;')
  })

  it('dit pourquoi un taux a été retiré, sans inventer de valeur', () => {
    const csv = courbeCsv(courbe({
      grandeur: { key: 'loss_percent', label: 'Perte', unit: '%' },
      lignes: [{ t: t0, value: null, comble: null, retire: true }],
    }))
    expect(csv).toContain('2026-08-30 16:00:05;;superviseur;retiré : moins de 200 paquets comptés')
  })

  it('distingue les points comblés par le routeur', () => {
    const csv = courbeCsv(courbe({ lignes: [{ t: t0, value: null, comble: -97.25, retire: false }] }))
    expect(csv).toContain('2026-08-30 16:00:05;-97,25;routeur;')
  })

  it('dit en tête qu’une enveloppe n’est pas la série complète', () => {
    const csv = courbeCsv(courbe({ enveloppe: true, disponibles: 4147 }))
    expect(csv).toContain('# série;enveloppe : minimum et maximum par tranche, 3 points pour 4147 mesures')
  })

  it('nomme le fichier par l’équipement, la grandeur et la fenêtre', () => {
    expect(nomFichierCourbe(courbe())).toBe('courbe-Vehicule-Ecole-Cellulaire-4-tunnel-rx-kbps-30min.csv')
    expect(nomFichierCourbe(courbe({
      enveloppe: true, fenetre: { libelle: 'session du …', jeton: 'session-12' },
    }))).toBe('courbe-Vehicule-Ecole-Cellulaire-4-tunnel-rx-kbps-session-12-enveloppe.csv')
  })

  it('neutralise séparateurs et amorces de formule dans les textes', () => {
    expect(texte('=SOMME(A1);x\n"y"')).toBe("'=SOMME(A1) x y")
    expect(nombre(null)).toBe('')
    expect(nombre(Number.NaN)).toBe('')
    expect(nombre(-0.0000004)).toBe('0')
  })

  it('dit une courbe entièrement regroupée à la minute', () => {
    const csv = courbeCsv(courbe({
      lignes: [{ t: t0, value: 2, comble: null, retire: false, minute: true }],
    }))
    expect(csv).toContain('# série;regroupée à la minute')
    expect(csv).toContain('2026-08-30 16:00:05;2;minute;')
  })
})
