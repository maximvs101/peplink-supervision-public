# Témoin de lien Ethernet — document de conception

Date : 17 octobre 2026. Statut : en attente d'approbation.

## 1. L'intention

Vérifier la **stabilité d'un lien Ethernet** — typiquement un lien MAN livré par un
opérateur — raccordé au WAN filaire d'un Peplink, **sans rien installer** et **sans
rien apprendre** : on lance un fichier, on voit si le lien tient, et quand il a lâché,
on sait à quelle heure, combien de temps, et pourquoi selon le routeur. Les coupures
s'exportent pour être remises à l'opérateur ou jointes à un rapport.

C'est un outil de **constat**, pas de supervision : on le lance pour observer un lien
pendant quelques heures ou quelques jours, puis on exporte. Il vise l'exploitant de
terrain qui n'a ni le temps ni l'envie d'ouvrir le superviseur complet pour une seule
question : « ce lien est-il stable ? ».

Il naît du superviseur Peplink (`peplink-supervision`), dont il reprend les briques
éprouvées sur du vrai matériel — MAX HD4 MBX, Dome Pro Duo, deux MAX HD2, firmwares
8.5.1 et 8.5.4 — et les leçons payées en septembre et octobre 2026.

## 2. Objectifs

1. **Démarrer seul.** Double-clic → le navigateur s'ouvre sur l'écran → le Peplink est
   trouvé → une seule saisie (le mot de passe, la première fois) → la mesure tourne.
2. **Dire l'essentiel d'un coup d'œil** : le lien est-il debout, depuis quand, combien
   de coupures depuis le début de l'observation.
3. **Montrer les coupures dans le temps** : une bande verte et rouge sur la période
   observée ; chaque coupure se voit, se survole, se clique.
4. **Lister chaque coupure** : début, fin, durée, cause donnée par le routeur.
5. **Exporter** la liste et les relevés en un clic, dans un format qu'Excel en
   français ouvre tel quel.
6. **Windows et macOS** (Intel et Apple), un fichier unique par système, sans
   installation ni droit d'administrateur.

## 3. Non-objectifs

- Les liens **cellulaires**, Wi-Fi WAN et le tunnel SpeedFusion. Seuls les WAN de type
  Ethernet sont proposés.
- La **flotte** : un seul Peplink à la fois.
- Les **alertes** (notifications, son) : l'outil s'observe, il ne réveille pas.
- La qualité **radio** et tout ce qui est propre au superviseur (bond, cellules,
  sessions d'alimentation, quotas, carte d'identité détaillée).
- L'usage sur téléphone : l'écran doit tenir de 1 024 px à un écran large ; pas en
  dessous de ~900 px.
- Toute **configuration** du routeur : l'outil lit, il ne modifie jamais rien.

## 4. Anti-objectifs — ce qui en ferait un échec même s'il « marche »

- **Un rouge qui ment.** Une désactivation volontaire du WAN, un PC en veille, un
  redémarrage de l'outil ne sont pas des coupures du lien. Une absence de mesure n'est
  jamais un zéro ni une panne : elle se dit comme telle (leçons C-20, A-6, A-4 du
  superviseur).
- **Verrouiller le routeur.** Cinq échecs de connexion suspendent l'accès admin cinq
  minutes. Une seule tentative par demande explicite ; jamais de relance après un refus
  d'identifiants (règle centrale du superviseur).
- **Lire des secrets.** La famille `config.*` de l'API n'est **jamais** interrogée ; les
  champs secrets de `status.*` (identifiants APN) ne sont ni stockés ni exportés.
- **Exporter ce qui identifie le site** : aucun numéro de série, aucune position GPS,
  aucun nom de WAN (il porte souvent un numéro de ligne) dans les exports. Le lien se
  nomme « WAN 1 », l'équipement par son modèle.
- **Un outil qu'il faut apprendre** : aucun réglage obligatoire, aucune page d'aide
  nécessaire pour lire l'écran.

## 5. Contraintes

- Pas d'installation, pas de droit d'administrateur, pas de pare-feu à ouvrir : le
  serveur n'écoute que sur `127.0.0.1` (comme `lancer.py`).
- Exécutable **non signé** : avertissement Windows / macOS au premier lancement,
  accepté (même situation que le superviseur, documentée dans son README).
- Fonctionne avec un Peplink en **HTTP** (le HD2 de terrain n'a pas HTTPS) — le mot de
  passe y circule alors en clair sur le LAN, et l'écran de connexion le dit (comme le
  formulaire du superviseur, C-43).
- Firmwares connus : 8.5.x. Un autre firmware doit dégrader proprement (une donnée
  absente se tait), jamais planter.
- Les données d'une observation sont **gardées à côté de l'exécutable**, pour relire et
  ré-exporter une observation passée.

## 6. Ce qui est décidé

### 6.1 Fabrication

- **Python + FastAPI + interface web dans le navigateur**, construits par **PyInstaller**
  en fichier unique, exactement comme le superviseur. Écarté : Java (JRE à installer,
  ou runtime embarqué de ~50 Mo par système ; tout le dialogue avec le Peplink à
  réécrire et re-valider).
- **Même dépôt** (`peplink-supervision`) : l'outil réutilise les modules existants
  plutôt que de les copier — client du routeur (`app/peplink/router_api.py`),
  découverte (`app/discovery`), coffre (`app/coffre.py`), anonymisation (`redact`).
- **Même chaîne de construction** : l'action GitHub `executable.yml` construit, lance et
  vérifie l'exécutable sur Windows, macOS arm64 et macOS Intel, et le publie avec la
  Release. Nom proposé des fichiers : `temoin-ethernet-windows-x64.exe`,
  `temoin-ethernet-macos-x64.tar.gz`, `temoin-ethernet-macos-arm64.tar.gz`.

### 6.2 Démarrage et connexion

1. L'exécutable démarre le serveur sur un port libre de `127.0.0.1` et ouvre le
   navigateur quand le serveur répond (même mécanique que `lancer.py`).
2. **Découverte sans identifiant** : passerelles du PC d'abord, puis le reste de la
   sonde existante (préfixes MAC Peplink, `info.firmware` sans session). Un seul
   Peplink trouvé → il est présélectionné ; plusieurs → liste d'un clic ; aucun → champ
   d'adresse.
3. **Identifiants** : si le trousseau du système garde ceux de cet équipement (clé =
   numéro de série, même service que le superviseur), connexion immédiate ; sinon
   formulaire (compte admin ou client API en lecture seule), case « Retenir sur ce
   poste » cochée. Une tentative ; un refus se dit et ne se rejoue pas.
4. **Choix du lien** : les WAN de type Ethernet du routeur. Un seul → sélectionné ;
   plusieurs → un clic. Le choix se rappelle pour cet équipement.

### 6.3 Mesure

| Lecture | Cadence | Sert à |
|---|---|---|
| `status.wan.connection` | toutes les 3 s | état connecté, message du routeur, ancienneté du lien |
| `status.log` (journal du routeur) | chaque minute | heure exacte et cause des coupures ; désactivations ; redémarrages |
| `status.wan.latency` | chaque minute | latence du lien (un point / 10 s), et l'heure précédant le lancement |

- Les lectures complémentaires suivent le budget et le recul déjà en place dans le
  client (un équipement injoignable est réinterrogé à 3, 6, 12, 24 s, puis 30 s).
- L'horloge de référence est celle du **poste** ; un écart de plus d'une minute avec le
  routeur se signale (règle du superviseur), et le journal du routeur n'est alors plus
  rapproché des coupures.

### 6.4 Ce qu'est une coupure

- **Coupure** : le WAN passe de connecté à non connecté entre deux lectures. Elle
  commence au premier relevé non connecté (ou à l'heure du journal du routeur si elle
  est connue, à ±15 s) et finit au premier relevé reconnecté. **Cause** : le mot du
  routeur — message de statut (« No Cable Detected »…) ou ligne du journal
  (« WAN failed DNS test »…).
- **Micro-coupure** : les deux relevés montrent le lien debout, mais son ancienneté a
  reculé — il est tombé et revenu entre deux lectures. Durée **inconnue** (au plus une
  cadence), dite comme telle ; heure et cause prises dans le journal du routeur s'il
  la donne.
- **N'est pas une coupure** : une désactivation par l'exploitant (relevé d'adieu
  « désactivé » / journal « Disabled ») — dite « désactivé », en gris ; un redémarrage
  du routeur — dit comme tel, avec sa durée ; un trou de mesure (outil fermé, PC en
  veille, routeur injoignable) — dit « non mesuré », hachuré, jamais rouge. Dans un
  trou de moins d'une heure, l'historique de latence du routeur indique si le lien
  répondait ; il ne fabrique pas d'état de connexion.
- **Test de santé en échec** : les captures 8.5.x ne publient aucun indicateur de test
  de santé dans `status.wan.connection` ; un Peplink dont le test échoue déclare
  d'ordinaire le WAN **non connecté** et cesse de l'employer. C'est donc une
  **coupure** (rouge), dont la **cause** distingue « câble » (« No Cable Detected ») et
  « test de santé » (journal : « WAN failed DNS/PING test »…) — c'est cette cause qui
  oriente vers le physique ou vers l'opérateur. *(Décidé par l'exploitant le 17/10.)*
  Pas d'état « dégradé » distinct en première version.
- **En secours** (« Standby ») : un WAN de secours est **debout**, simplement inemployé ;
  son ancienneté remise à zéro au passage en secours n'est pas une micro-coupure (même
  règle que `en_secours` dans le superviseur). Dit « en secours », en vert. *(Choix de
  conception.)*

### 6.5 L'écran (un seul)

- **En tête** : le verdict en gros — « Lien stable depuis 2 h 14 » (vert), « Coupé
  depuis 00:42 » (rouge), « Routeur injoignable » (gris) — et
  trois chiffres : coupures, durée cumulée coupée, disponibilité sur la période
  observée (sur le temps **mesuré**, avec son dénominateur).
- **Au milieu** : la **bande de temps** de l'observation, verte / rouge / grise
  (désactivé) / hachurée (non mesuré) ; chaque coupure se survole (heure, durée, cause) et se clique (elle
  s'éclaire dans la liste). Fenêtres : l'observation entière (défaut), dernière heure,
  dernières 24 h.
- **En bas** : la **liste des coupures**, la plus récente en haut — début, fin, durée,
  cause. Une coupure en cours dit « en cours ».
- **Bouton « Exporter »** et **« Rapport »** (page imprimable en PDF par le navigateur).
- Thème clair et sombre (réglage du système), virgule décimale, heures locales.

### 6.6 Données et export

- Une base SQLite à côté de l'exécutable (`data/temoin.db`, distincte de celle du
  superviseur), une copie de sauvegarde au lancement (comme le superviseur).
- Une **observation** = le suivi d'un lien (équipement + WAN). Relancer l'outil sur le
  même lien **moins d'une heure** après le dernier relevé **reprend** l'observation, le
  temps fermé devenant un trou « non mesuré » ; au-delà, une nouvelle commence. Un
  bouton « Nouvelle observation » la clôt à la main. Les observations passées se
  rouvrent et se ré-exportent ; chiffres et disponibilité portent sur l'observation
  affichée. *(Choix de conception.)*
- **Export CSV** (séparateur « ; », virgule décimale, BOM UTF-8, comme l'export du
  superviseur) : un fichier **coupures** (début, fin, durée en s, type, cause) et un
  fichier **relevés** (instant, connecté, message, ancienneté, latence). En-tête :
  modèle, firmware, « WAN n », période — ni série, ni position, ni nom de WAN.
- **Rapport** : une page imprimable — période, verdict, chiffres, bande, liste.

### 6.7 Séance de capture sur matériel — préalable au codage des règles

Les fixtures du dépôt ne contiennent **aucune** coupure Ethernet (ni « No Cable
Detected », ni ancienneté qui recule sur un WAN filaire, ni test de santé en échec), et
la lecture du journal du routeur ne reconnaît pas encore « Disabled », « failed DNS/PING
test » ni « No Cable ». Une séance de capture sur un Peplink réel, menée par
l'exploitant, est donc nécessaire ; **elle aura lieu plus tard** (décision du 17/10).

Conséquence sur l'ordre de travail : tout ce qui ne dépend pas des formats inconnus se
construit d'abord — découverte, connexion, trousseau, lectures, stockage, écran,
exports, exécutable — avec la détection fondée sur l'état connecté / non connecté et
l'ancienneté, déjà éprouvés. Les **causes** tirées du journal du routeur et la
reconnaissance des désactivations, tests de santé et redémarrages s'écrivent contre des
hypothèses explicites, marquées comme telles dans le code et les tests, puis se
confirment ou se corrigent à la séance. Gestes à capturer, l'outil de capture du
superviseur enregistrant les réponses : câble débranché puis rebranché, câble débranché moins de
3 s, WAN désactivé puis réactivé, passage en secours, test de santé en échec (cible de
test injoignable), redémarrage du routeur. Ces captures, anonymisées selon le
protocole du dépôt, deviennent les fixtures et le rejeu des tests.

## 7. Ce qui est laissé au constructeur

- L'organisation du code dans le dépôt (sous-paquet, point d'entrée, partage des
  modèles) et la part du superviseur à réutiliser telle quelle ou à extraire.
- Le framework d'écran (le superviseur emploie React + Vite ; un écran unique peut
  s'en passer) — tant que le fichier final reste unique et léger.
- La cadence exacte des lectures complémentaires, le seuil d'écart d'horloge, la
  rétention des observations anciennes.
- Le détail graphique, dans la famille visuelle du superviseur.
- Retrouver l'entrée du trousseau avant la connexion (la découverte sans session ne
  donne pas le numéro de série : une correspondance adresse → série gardée localement
  suffit).
- Le fonctionnement dégradé quand le journal du routeur est inaccessible (client API en
  lecture seule) : coupures datées par les relevés seuls, causes par le message de
  statut.
- Les tests : les règles de la section 6.4 sont le cœur et doivent être tenues par des
  tests, rejeu des captures réelles compris.

## 8. Risques connus

- Exécutable non signé : un poste géré par une DSI peut refuser de le lancer depuis
  une clé USB.
- PC en veille : l'outil ne voit rien ; seule la dernière heure se comble.
- Peplink en HTTP : mot de passe en clair sur le LAN.
- Firmware hors 8.5.x : champs publiés différents, à confronter sur matériel.
