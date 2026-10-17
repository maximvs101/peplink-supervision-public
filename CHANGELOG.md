# Versions

Le texte de chaque Release GitHub est la section de ce fichier qui porte son étiquette :
l'action l'extrait au moment de publier. Écrit à la main, au moment où l'on sait ce que la
version change.

## Prochaine version — Témoin de lien Ethernet

Un second exécutable, `temoin-ethernet-*`, à côté du superviseur et dans la même Release,
pour une seule question : ce lien Ethernet est-il stable ? Voir la section « Témoin de lien
Ethernet » du README.

- **Un WAN, une bande.** Il suit un WAN Ethernet d'un Peplink trouvé sur le réseau : verdict
  en tête (« Lien stable depuis 2 h 14 », « Coupé depuis 00:42 »), bande verte et rouge,
  liste des coupures avec début, fin, durée et cause (« câble », « test de santé »).
- **Ce qui n'est pas une coupure ne le devient pas** : PC en veille ou routeur injoignable
  (« non mesuré »), WAN désactivé, passage en secours, redémarrage du routeur. Une coupure
  dont on n'a pas vu la fin le dit (« au moins … »).
- **Exports CSV** pour Excel en français (coupures, relevés) et rapport imprimable, sans
  numéro de série, position ni nom de WAN.
- **Sans installation**, port 8010 à côté du superviseur, base à côté du fichier,
  identifiants dans le trousseau du système.
- Les causes lues dans le journal du routeur restent une **hypothèse** jusqu'à la séance de
  capture sur matériel.

Côté superviseur : la série n'apparaît plus dans les messages du trousseau, et les types
des fichiers de l'interface sont fixés — une base de registre Windows qui associait `.js` à
`text/plain` laissait l'écran blanc.

Construction : le lancement à la demande choisit *les deux*, *superviseur* ou *témoin*.

À l'étiquetage : renommer cette section du nom de l'étiquette, et le titre de la Release
(« Superviseur vX » dans `executable.yml`) pour qu'il dise les deux produits.

## v0.1.2 — Recette face à un MAX HD2 réel

Mêmes fichiers, même usage. Ce qui change pour l'exploitant :

- **La bande ne s'invente plus de trous.** Sur un routeur en HTTP, la cadence
  de scrutation varie du simple au double ; une tranche de bande plus fine que
  l'écart réel entre deux relevés restait vide, et chaque vide coupait un état
  immobile en épisodes — « redondance réduite 24 fois » en vingt minutes. Les
  tranches valent désormais au moins deux périodes de scrutation.
- **Un équipement éteint reste lisible.** Ses stationnements enregistrés sont
  listés, vers leur rapport ; le bandeau ne renvoie plus à des « valeurs
  affichées » qui n'existent pas, et ne dit plus « jamais mesuré » d'un
  équipement dont la base garde l'historique. Le détail d'un lien ne promet
  plus de « première mesure » à un équipement injoignable.
- **L'ouverture mène à l'équipement qui répond**, plus au premier du fichier
  de flotte quand il est éteint.
- **Alertes.** « 5 seul sur 3 » devient « lien 5 seul, sur 3 liens » ; les
  alertes résolues se masquent d'un bouton, le temps de la session du
  navigateur — une alerte en cours, jamais.
- **Les identifiants se retiennent.** « Retenir sur ce poste », coché par
  défaut à l'appairage, les range dans le trousseau du système — Trousseau sur
  macOS, Gestionnaire d'identification sur Windows —, jamais en clair sur le
  disque. Au lancement, chaque équipement retenu reçoit une tentative de
  connexion ; « Reconnecter » se fait sans formulaire. Décocher, ou retirer
  l'équipement, efface ce qui était retenu.
- **Un équipement éteint coûte moins.** Ses tentatives s'espacent — 3, 6, 12,
  24 s, puis toutes les 30 s — au lieu d'une à chaque cadence ; dès qu'il
  répond, la cadence normale reprend. Rallumé, il peut mettre jusqu'à trente
  secondes à réapparaître.
- **Un lien désactivé exprès n'est plus une panne, nulle part.** Éprouvé sur
  un HD2 : le journal écrivait « Coupure » en rouge pour une SIM désactivée
  par l'exploitant, et la table comme la fiche de session la comptaient
  parmi les coupures. Elle s'écrit « Désactivé par l'exploitant », en gris,
  et ne compte plus.
- **Lisibilité.** Virgule décimale partout ; un lien jamais raccordé n'est
  plus peint en rouge ; les colonnes sans objet (bond sans tunnel, radio sans
  modem) disparaissent ; la table des liens tient à 1 024 px ; la fiche d'un
  lien dit sa fenêtre une fois ; le rapport est mis en page et ne porte plus
  aucune position dans sa version à partager, CSV compris.
- **Le volume du stationnement** s'affiche à côté du débit de l'instant.
- **Prévenir d'une alerte.** « Alertes : muet · Notifier · Notifier + son »
  dans le bandeau : une notification du système quand une alerte s'ouvre et
  que l'onglet est caché.
- **Un équipement retenu est repris dès qu'il apparaît**, et non plus
  seulement au lancement ; un refus d'identifiants arrête la veille.
- **Le journal et la courbe s'expliquent.** Un geste de l'exploitant tient en
  une ligne ; la courbe d'un lien grise les trous de supervision et marque
  désactivations et coupures.
- **Le journal dit l'état d'ouverture.** Un lien déjà hors du bond au premier
  relevé y entre, daté, avec la cause donnée par le routeur.
- **Une ouverture plus légère** : 292 Ko de code au lieu de 688, la
  bibliothèque de courbes n'étant chargée qu'à l'ouverture d'une courbe ou d'un
  rapport.
- **Icône d'onglet.**

Détail : `docs/HISTORIQUE-2026-09.md`, entrées du 30/09 et du 17/10.

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
