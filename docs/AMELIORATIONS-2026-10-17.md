# Améliorations du 17 octobre 2026 — suivi

Tour de l'outil sur un MAX HD2 réel (vue d'ensemble, fiche de lien, équipements,
rapport), douze propositions acceptées en bloc par l'exploitant. Une case par point,
cochée quand le correctif est fait, testé et relu en service. Ordre de travail : les
petits correctifs d'abord, puis les fonctions.

## Défauts et vie privée

- [x] **A-11** — « Reconnecter » proposé dans « Déjà vus » pour un équipement déjà supervisé
  (la condition teste « détecté » avant « supervisé » ; un clic relance une connexion inutile).
- [x] **A-12** — Le rapport « à partager » garde une position arrondie à ~100 m : n'en garder aucune.
  Fait : ni dans la fiche du rapport, ni dans son CSV (colonnes de position réservées à l'export complet).

## Lisibilité et rendu

- [x] **A-6** — Un lien filaire jamais raccordé est peint en rouge sur toute sa bande : neutre.
- [x] **A-8** — Décimales en point (« 0.2 dB », « 87.7 Mo ») à côté d'entiers à la française :
  virgule décimale partout.
- [x] **A-7** — Colonnes sans objet : « Bond » sans tunnel, colonnes cellulaires sans modem ; la
  table des liens déborde à 1 024 px.
- [x] **A-9** — Fiche d'un lien : la fenêtre répétée dans chaque libellé, des libellés qui se
  chevauchent ; la dire une fois.
- [x] **A-10** — Rapport imprimable : colonnes serrées, chiffres collés, « WAN 1 » coupé.
- [x] **A-5** — Volume échangé depuis le début du stationnement sur la vue d'ensemble (l'écran
  disait 0,0 Mbit/s, le rapport 1,5 Go descendus).

## Fonctions

- [x] **A-2** — Prévenir quand une alerte s'ouvre et que l'onglet est caché (notification du
  système, son optionnel).
  Fait : liste « Alertes : muet · Notifier · Notifier + son » dans le bandeau, retenue sur le
  poste ; une alerte en cours, nouvelle depuis l'ouverture, onglet caché. Reste à voir une vraie
  notification du système : elle demande l'autorisation de l'exploitant dans le navigateur.
- [x] **A-1** — Reconnecter un équipement retenu quand il apparaît sur le réseau, pas seulement au
  lancement (même recul, jamais après un refus d'identifiants).
- [x] **A-3** — Journal : regrouper un geste de l'exploitant (désactivation → signalements →
  retour) en une ligne.
- [x] **A-4** — Courbe d'historique : y poser les événements du journal et les trous de supervision.

## Relecture en service (HD2, 17/10, après redémarrage du serveur)

| Point | Vu en service | Tenu par les tests seulement |
|---|---|---|
| A-11 | « Déjà vus » : le HD2 supervisé dit « déjà supervisé », sans bouton | — |
| A-12 | Rapport à partager sans position ni mention d'arrondi | CSV : colonnes de position réservées à l'export complet |
| A-6 | WAN 2 sans câble : rail, étiquette, bande et plan neutres | — |
| A-8 | « 0,0 », « 97,5 / 1 629 Mo », « 1 428 o » sur la même page | coordonnées GPS en point |
| A-7 | table des liens : 974 px pour 974 disponibles à 1 024 px ; rapport sans colonnes « Bond » | colonnes radio absentes sans modem |
| A-9 | « Comptes depuis 12:51 — sous tension depuis 09:52 » une fois, libellés courts | — |
| A-10 | rapport à 960 px, fiche stylée, colonnes alignées | rendu imprimé (à voir par l'exploitant) |
| A-5 | « 97,5 / 1 629 Mo ↑ / ↓ · depuis 12:51 » | volume absent sur un châssis sans débit par WAN |
| A-2 | liste « Alertes : muet · Notifier · Notifier + son » ; à 1 280 px, bandeau inchangé | **une vraie notification du système** : demande l'autorisation de l'exploitant |
| A-1 | au démarrage, « Équipement retenu reconnecté » 4 s après | **un équipement qui s'allume après l'outil** : recul et refus tenus par les tests |
| A-3 | — (aucun geste de l'exploitant dans la fenêtre relue) | les six lignes réelles du 17/10 rendues en une |
| A-4 | SIM 4 sur 3 h : 6 trous (les redémarrages), zone « désactivé » 13:51 → 13:52 | — |

Suites : 966 serveur, 595 écran, ruff et tsc propres ; exécutable construit et vérifié (1,6 s).

