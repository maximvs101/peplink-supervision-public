/**
 * Prévenir d'une alerte quand personne ne regarde l'onglet (A-2).
 *
 * Un stationnement dure des heures et l'écran n'est pas regardé en continu : le
 * panneau d'alertes n'a de valeur que s'il est vu. Trois modes, choisis par
 * l'exploitant et retenus sur ce poste : muet (par défaut), notification du
 * système, notification et un son bref.
 *
 * Ce qui prévient : une alerte EN COURS, NOUVELLE depuis l'ouverture de la page,
 * onglet caché. Une alerte déjà là à l'ouverture se voit ; une alerte résolue
 * ne demande plus rien ; onglet visible, le panneau suffit.
 */

import type { Alert } from '../types'
import { titreAlerte } from './alertes'

export type ModePrevenir = 'muet' | 'notification' | 'son'

const CLE = 'prevenir'

export function modeMemorise(): ModePrevenir {
  try {
    const v = localStorage.getItem(CLE)
    return v === 'notification' || v === 'son' ? v : 'muet'
  } catch {
    return 'muet'
  }
}

export function retenirMode(mode: ModePrevenir): void {
  try {
    if (mode === 'muet') localStorage.removeItem(CLE)
    else localStorage.setItem(CLE, mode)
  } catch {
    // Stockage refusé : le choix vaut pour cette page.
  }
}

const cle = (a: Alert) => `${a.kind}|${a.wan_id ?? ''}|${a.since}`

/** Un contexte audio créé au clic de l'exploitant : un navigateur refuse le son
 *  d'une page qui n'a jamais été touchée, et l'onglet caché ne le sera pas. */
let audio: AudioContext | null = null

export function preparerSon(): void {
  try {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (Ctx && audio === null) audio = new Ctx()
    void audio?.resume()
  } catch {
    audio = null
  }
}

function bip(): void {
  if (audio === null) return
  try {
    const osc = audio.createOscillator()
    const gain = audio.createGain()
    osc.frequency.value = 880
    gain.gain.setValueAtTime(0.15, audio.currentTime)
    gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.4)
    osc.connect(gain).connect(audio.destination)
    osc.start()
    osc.stop(audio.currentTime + 0.4)
  } catch {
    // Pas de son : la notification reste.
  }
}

/** Suit les alertes d'UN équipement et prévient des nouvelles. */
export class Veilleur {
  private vues: Set<string> | null = null

  constructor(private mode: ModePrevenir, private equipement: string) {}

  changerMode(mode: ModePrevenir): void {
    this.mode = mode
  }

  renommer(equipement: string): void {
    this.equipement = equipement
  }

  observer(alertes: Alert[]): void {
    const enCours = alertes.filter((a) => a.until === null)
    // Le premier état observé est la référence : ce qui y figure se voit déjà.
    if (this.vues === null) {
      this.vues = new Set(enCours.map(cle))
      return
    }
    const nouvelles = enCours.filter((a) => !this.vues!.has(cle(a)))
    for (const a of nouvelles) this.vues.add(cle(a))
    if (nouvelles.length === 0 || this.mode === 'muet') return
    if (document.visibilityState !== 'hidden') return
    if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
    for (const a of nouvelles) {
      try {
        new Notification(`Alerte — ${this.equipement}`, {
          body: titreAlerte(a) + (a.detail ? ` (${a.detail})` : ''),
          tag: `peplink-${cle(a)}`,
        })
      } catch {
        // Une notification refusée par le système ne doit rien casser.
      }
    }
    if (this.mode === 'son') bip()
  }
}
