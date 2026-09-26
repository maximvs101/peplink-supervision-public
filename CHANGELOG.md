# Versions

Le texte de chaque Release GitHub est la section de ce fichier qui porte son étiquette :
l'action l'extrait au moment de publier. Écrit à la main, au moment où l'on sait ce que la
version change.

## v0.1.1 — Revue d'exploitation, relecture complète, Windows rétabli

Mêmes fichiers, même usage que la version précédente. Ce qui change pour l'exploitant :

- **Ce que l'écran affirme.** Un tunnel à terre ne repasse plus au vert faute de
  débit mesuré ; un lien désactivé n'est plus une panne rouge ; un état inconnu
  s'écrit « — », jamais « 0 » ; chaque taux de la fiche écrit son nombre de
  mesures ; le rapport imprimable distingue « lecture impossible » d'« aucune
  mesure » et tait le nom de session en version à partager.
- **Ce que le routeur disait et que l'écran taisait.** Journal du routeur,
  cause d'un tunnel à terre, latence de chaque lien à 10 s, température des
  modems, clients du LAN, historique radio du routeur dans les trous de
  supervision, changement d'opérateur, export CSV d'une session.
- **Robustesse.** Un refus passager ne condamne plus un équipement à
  « injoignable » ; des lectures lentes ne font plus perdre le relevé ; le
  journal ne fait plus attendre la scrutation ; une base illisible au
  démarrage est dite en clair, avec ses copies de sauvegarde ; un second
  double-clic ouvre l'instance qui tourne.
- **Sécurité.** L'API refuse un nom d'hôte rebasculé (DNS rebinding) — depuis
  un téléphone du LAN, ouvrir l'écran par l'adresse IP du poste, ou déclarer
  son nom dans `ALLOWED_HOSTS` ; les routes réservées au poste refusent les
  requêtes d'une autre page ; un mot de passe de `routers.yaml` n'apparaît
  plus dans la console.
- **Windows.** L'exécutable Windows ne se construisait plus depuis le 24/09 :
  rétabli, avec une garde de port corrigée pour ce système.

Détail constat par constat : `docs/REVUE-2026-09-24.md` et
`docs/RELECTURE-2026-09-25.md`.

## v0.1.0 — Première version emportable

*Release retirée le 26 septembre 2026 (C-45) : son archive source portait le numéro de
série et une position réels d'un équipement. La v0.1.1 la remplace, et l'historique git
est ramené à un seul commit ; voir `docs/HISTORIQUE-2026-09.md`.*

Un fichier par système, rien à installer, aucun droit d'administrateur : le
serveur, ses dépendances, Python et l'interface. Double-clic, le navigateur
s'ouvre sur le tableau de bord ; la base de relevés se range dans `data/` à
côté du fichier — sur une clé USB, elle voyage avec la clé — avec une copie
de sauvegarde à chaque lancement.

Fichiers : `superviseur-windows-x64.exe`, `superviseur-macos-x64.tar.gz` (Intel),
`superviseur-macos-arm64.tar.gz` (Apple silicon — sur Mac, une archive à décompresser
d'un double-clic : un fichier nu perd son droit d'exécution en route, vu à la v0.1.0).
Non signés — au premier lancement,
Windows demande « Informations complémentaires › Exécuter quand même »,
macOS un clic droit › Ouvrir. Le serveur n'écoute que sur 127.0.0.1:8000 ;
`HOST=0.0.0.0` l'ouvre au réseau local pour un téléphone.

Ce que cette version sait faire, validé sur trois châssis réels (MAX HD4 MBX,
Dome Pro Duo, MAX HD2, firmware 8.5.4) et rejoué de bout en bout sur leurs
captures à chaque test :

- découverte des Peplink du réseau et appairage depuis l'écran, identifiants
  jamais écrits sur le disque ;
- niveaux radio, porteuses agrégées, identité de cellule et de station,
  débits, tunnel SpeedFusion lien par lien, verdict de liaison en une phrase ;
- sessions d'alimentation ancrées sur l'uptime du châssis, bande d'état,
  journal des épisodes, historique par lien, fiche de stationnement ;
- alertes à mémoire, bornées à la session, sans papier peint ;
- un équipement sans modem, sans tunnel, sans débit par WAN, ou qui ne parle
  qu'HTTP, est affiché tel qu'il est — jamais un zéro pour une absence.

Ce qui reste faible : trois châssis, un firmware, deux opérateurs ; seuils
radio calés sur −75 à −105 dBm, seuils d'alerte sur un véhicule en
stationnement ; pas d'authentification sur l'API — ne pas l'exposer au-delà
du poste ou du LAN.
