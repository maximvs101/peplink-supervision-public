import { describe, expect, it } from 'vitest'

import { pathDiversity } from './diversity'
import type { RadioSample } from '../types'

/** Relevé minimal : seuls les champs que `pathDiversity` regarde comptent. */
const lien = (
  wan_id: string,
  // Des CHAÎNES, comme le fil les envoie : les identifiants voyagent tels quels.
  over: { cell_id?: string | null; earfcn?: string | null; enb_id?: string | null;
          kind?: string; connected?: boolean; plmn?: string | null } = {},
): RadioSample =>
  ({
    wan: {
      wan_id,
      kind: over.kind ?? 'cellular',
      connected: over.connected ?? true,
      cell_id: over.cell_id ?? null,
      earfcn: over.earfcn ?? null,
      enb_id: over.enb_id ?? null,
      plmn: over.plmn ?? null,
    },
  }) as unknown as RadioSample

describe('diversité de trajet', () => {
  it('compte les cellules, pas les SIM', () => {
    // Le cas mesuré sur le matériel : quatre SIM, deux cellules, un seul site.
    const d = pathDiversity([
      lien('4', { cell_id: '15941386', earfcn: '3000', enb_id: '62271' }),
      lien('6', { cell_id: '15941386', earfcn: '3000', enb_id: '62271' }),
      lien('5', { cell_id: '14307075', earfcn: '3000', enb_id: '62271' }),
      lien('7', { cell_id: '14307075', earfcn: '3000', enb_id: '62271' }),
    ])
    expect(d.links).toBe(4)
    expect(d.cells).toBe(2)
    expect(d.sites).toBe(1)
    expect(d.sameCell.get('4')).toEqual(['6'])
    expect(d.sameCell.get('5')).toEqual(['7'])
  })

  it('distingue deux secteurs qui partagent un identifiant sur des porteuses différentes', () => {
    // L'identité d'une cellule n'est pas son seul `cell_id` : sur deux canaux,
    // ce sont des ressources radio distinctes.
    const d = pathDiversity([
      lien('4', { cell_id: '100', earfcn: '1300', enb_id: '1' }),
      lien('5', { cell_id: '100', earfcn: '3000', enb_id: '1' }),
    ])
    expect(d.cells).toBe(2)
    expect(d.sites).toBe(1)
    expect(d.sameCell.get('4')).toEqual([])
  })

  it('ignore le filaire et les modems déconnectés', () => {
    // Une fibre n'emprunte aucune cellule, et un modem déconnecté rapporte la
    // dernière qu'il a vue — la compter inventerait un trajet qui n'existe plus.
    const d = pathDiversity([
      lien('1', { kind: 'ethernet' }),
      lien('4', { cell_id: '100', earfcn: '3000', enb_id: '1' }),
      lien('5', { cell_id: '100', earfcn: '3000', enb_id: '1', connected: false }),
    ])
    expect(d.links).toBe(1)
    expect(d.cells).toBe(1)
    expect(d.sameCell.get('4')).toEqual([])
  })

  it('ne dit rien d’un modem dont la cellule est inconnue', () => {
    const d = pathDiversity([lien('4'), lien('5')])
    expect(d.links).toBe(2)
    expect(d.cells).toBe(0)
    expect(d.sameCell.get('4')).toBeUndefined()
  })

  it('sépare « cellule inconnue » de « cellule partagée »', () => {
    // `cells < links` mélangeait les deux : une SIM qui vient de se rattacher
    // sans `cellTower.cellId` faisait afficher « Des modems partagent une
    // cellule », affirmation FAUSSE, et sans qu'aucune tuile ne porte le badge
    // correspondant. Sur un modèle qui ne publie aucun `cellId`, l'écran
    // annonçait « 2 liens cellulaires · 0 cellules · 0 sites » assorti du même
    // avertissement — le papier peint permanent que ce bandeau évite.
    const d = pathDiversity([
      lien('4', { cell_id: '100', earfcn: '1300', enb_id: '1' }),
      lien('5', { cell_id: '200', earfcn: '1300', enb_id: '2' }),
      lien('6', { cell_id: '300', earfcn: '1300', enb_id: '3' }),
      lien('7'),                                   // vient de se rattacher
    ])
    expect(d.links).toBe(4)
    expect(d.located).toBe(3)
    expect(d.unknown).toBe(1)
    // Trois liens situés, trois cellules : rien n'est partagé.
    expect(d.cells).toBe(d.located)
  })

  it('compte encore un partage réel quand une cellule manque', () => {
    const d = pathDiversity([
      lien('4', { cell_id: '100', earfcn: '1300', enb_id: '1' }),
      lien('5', { cell_id: '100', earfcn: '1300', enb_id: '1' }),
      lien('7'),
    ])
    expect(d.located).toBe(2)
    expect(d.cells).toBe(1)
    expect(d.cells).toBeLessThan(d.located)
    expect(d.sameCell.get('4')).toEqual(['5'])
  })

  it('ne situe aucun lien quand le modèle ne publie pas de cellule', () => {
    const d = pathDiversity([lien('4'), lien('5')])
    expect(d.located).toBe(0)
    expect(d.unknown).toBe(2)
  })
})

describe('ce que l’écran affirme sur le partage', () => {
  it('donne la taille du PLUS GRAND groupe, et non une formule', () => {
    // L'écran recalculait « located − cells + 1 », juste seulement s'il existe
    // EXACTEMENT un groupe partagé. Sur deux paires partageant chacune sa
    // cellule, la formule annonçait trois modems quand le vrai maximum est deux.
    const d = pathDiversity([
      lien('4', { cell_id: '100', earfcn: '1300', enb_id: '1' }),
      lien('5', { cell_id: '100', earfcn: '1300', enb_id: '1' }),
      lien('6', { cell_id: '200', earfcn: '3000', enb_id: '2' }),
      lien('7', { cell_id: '200', earfcn: '3000', enb_id: '2' }),
    ])
    expect(d.located - d.cells + 1).toBe(3) // l'ancienne formule
    expect(d.largestSharedCell).toBe(2) // la vérité
  })

  it('distingue une station indéterminable d’une absence de station', () => {
    // Le parseur refuse de décomposer un identifiant 5G SA : `enb_id` est nul
    // alors que `cell_id` existe. `sites` valait alors 0, et l'écran écrivait
    // « 2 cellules · 0 site » — un zéro à la place d'une absence.
    const d = pathDiversity([
      lien('4', { cell_id: '100', earfcn: '1300', enb_id: null }),
      lien('5', { cell_id: '200', earfcn: '3000', enb_id: null }),
    ])
    expect(d.cells).toBe(2)
    expect(d.sites).toBe(0)
    expect(d.attached).toBe(0) // aucune station déterminable : l'écran doit le dire
  })
})

describe('une station et une cellule appartiennent à un opérateur (C-40)', () => {
  it('ne réunit pas deux opérateurs aux numéros identiques', () => {
    // Deux SIM, deux opérateurs, des eNB et des identifiants égaux par hasard :
    // la fiche les déclarait « même station » et « même cellule ».
    const d = pathDiversity([
      lien('4', { cell_id: '100', earfcn: '3000', enb_id: '1', plmn: '208-01' }),
      lien('5', { cell_id: '100', earfcn: '3000', enb_id: '1', plmn: '208-10' }),
    ])
    expect(d.cells).toBe(2)
    expect(d.sites).toBe(2)
    expect(d.sameCell.get('4')).toEqual([])
  })

  it('ne tient pas deux porteuses inconnues pour la même', () => {
    const d = pathDiversity([
      lien('4', { cell_id: '100', earfcn: null, enb_id: '1', plmn: '208-01' }),
      lien('5', { cell_id: '100', earfcn: null, enb_id: '1', plmn: '208-01' }),
    ])
    expect(d.sameCell.get('4')).toEqual([])
    expect(d.cells).toBe(2)
    // La station, elle, ne dépend pas de la porteuse.
    expect(d.sites).toBe(1)
  })
})
