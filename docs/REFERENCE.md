# Référence technique

> L'ancien README, conservé en entier : tout ce que le README court ne dit plus.
> Certaines sections datent d'avant l'exécutable unique, la découverte et le rejeu de bout en bout ;
> le [README](../README.md) et le [protocole](PROTOCOLE-NOUVEL-EQUIPEMENT.md) font foi
> quand ils diffèrent.

# Supervision radio 4G/5G — équipements Peplink

Outil de supervision radio pour installations de connectivité 4G/5G basées sur des routeurs
Peplink (MBX, MAX…). Il récupère automatiquement les métriques des modems et affiche en
temps réel ce que vaut chaque lien — niveaux radio, porteuses agrégées, débits, état du
tunnel SpeedFusion — avec un historique glissant et un verdict de liaison en une phrase.

**Prototype v1** : supervision live d'une flotte (SINR / RSRP / RSRQ, technologie, opérateur,
bandes agrégées, débits, stabilité), tunnel SpeedFusion lien par lien, historique et verdict.
Les **liens Wi-Fi amont** (Starlink, hotspot) agrégés aux modems sont supervisés eux aussi.

---

## Démarrage rapide

Deux terminaux, pour le **développement** (rechargement à chaud, proxy de dev donc aucune
configuration CORS). Pour un déploiement sur un PC de terrain, voir la section dédiée plus
bas : un seul processus suffit.

### 1. Backend (Python 3.12)

```bash
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt      # Windows
# source .venv/bin/activate && pip install -r requirements-dev.txt   # Linux/macOS

.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --reload
```

Trois listes, toutes en versions exactes (`==`) : `requirements.txt` pour l'exécution
(ce que l'exécutable embarque, et ce qu'installe le PC de terrain),
`requirements-dev.txt` qui y ajoute pytest et ruff, `requirements-build.txt` qui y ajoute
PyInstaller.

Lancé ainsi, directement, le serveur démarre en **mode simulateur** (`PEPLINK_MODE=mock`,
le défaut de `config.py`) et **sans sauvegarde** de la base : c'est le mode du
développement. Pour superviser de vrais routeurs, poser `PEPLINK_MODE=router` — ou passer
par `lancer.py`, qui fait les deux, comme l'exécutable.

API sur <http://localhost:8000> · documentation interactive sur <http://localhost:8000/docs>

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Interface sur <http://localhost:5173>. Elle est accessible depuis un smartphone du même
réseau (Vite écoute sur toutes les interfaces).

Sans aucune configuration, l'application démarre en **mode simulateur** avec une flotte de
démonstration (2 routeurs, 4 SIM) : métriques réalistes qui évoluent, handovers, épisodes de
dégradation.

---

## L'exécutable unique — un fichier, un double-clic

La forme la plus simple à emporter : le serveur, ses dépendances, l'interpréteur
Python et l'interface compilée dans **un seul fichier**, qui ouvre le navigateur
sur le tableau de bord et range la base à côté de lui. Rien à installer, aucun
droit d'administrateur ; sur une clé USB, la base voyage avec la clé.

Construit **sur le système visé** (PyInstaller ne compile pas en croisé — un
fichier pour Windows, un pour macOS) :

```bash
cd frontend && npm run build
cd ../backend && .venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python tools/construire_executable.py
```

Produit `backend/dist/superviseur` (`superviseur.exe` sous Windows). Mesuré le
19 septembre 2026 sur macOS : 38 s de construction, 19 Mo, prêt 1,5 s après le
double-clic. `tools/verifier_executable.py dist/superviseur` le lance depuis un
dossier vide et vérifie serveur, interface et base.

**Sans PC à portée** : l'action GitHub « Exécutable »
(`.github/workflows/executable.yml`) construit les fichiers Windows, macOS Apple
silicon et macOS Intel sur les machines de GitHub — à la demande (onglet Actions › Run workflow, fichiers
en artefacts du run pendant quatorze jours) ou à chaque étiquette `v*` poussée
(`git tag v1.0.0 && git push --tags`, fichiers dans une Release). Rien n'est
produit tant que les suites ne sont pas vertes, et chaque fichier est lancé et
vérifié après construction.

Avant les constructions, un job léger (`garde`) vérifie les numéros de version :
sur une étiquette, celle-ci, la section du CHANGELOG qui la porte, les deux paquets
Python et `package.json` disent le même numéro ; en lancement manuel, les trois
numéros du code concordent entre eux (`.github/scripts/garde_version.py
--coherence`). En parallèle, le job `audits` passe `pip-audit` sur
`backend/requirements.txt` et `npm audit --omit=dev --audit-level=high` sur
l'interface : une faille connue dans une dépendance d'exécution fait échouer le run
et bloque la Release. La couverture des tests (serveur et interface) est affichée,
sans seuil, dans le journal et le résumé du run de la machine macOS Apple silicon.
La Release porte `SHA256SUMS`, les empreintes SHA-256 des six fichiers. Dependabot
(`.github/dependabot.yml`) propose chaque mois les montées d'actions, de paquets
Python et npm, regroupées par écosystème.

À côté de l'exécutable : `data/samples.db` (créée au premier lancement, copie de
sauvegarde datée à chaque lancement, deux gardées), et `routers.yaml` s'il y en a
un — sinon les équipements s'appairent depuis l'écran de découverte. Le serveur
n'écoute que sur `127.0.0.1:8000` ; `HOST=0.0.0.0` l'ouvre au réseau local pour un
téléphone (Windows demande alors une autorisation de pare-feu), `PORT=…` change le
port, `OUVRIR_NAVIGATEUR=0` n'ouvre pas le navigateur.

Un fichier non signé déclenche un avertissement au premier lancement — Gatekeeper
sur Mac (clic droit › Ouvrir), SmartScreen sur Windows (« Informations
complémentaires › Exécuter quand même ») — qui se lève sans droits
d'administrateur. Un poste géré par une DSI peut interdire tout exécutable d'un
support amovible : c'est à vérifier sur le poste, pas dans l'outil.

## Déploiement sur un PC de terrain

Quand le routeur n'est pas joignable depuis la machine de développement, l'application doit
tourner sur un PC du réseau routeur. Elle y fonctionne avec **Python seul** : l'interface
compilée est servie par le backend, donc ni Node, ni second serveur, ni CORS à gérer.

### 1. Compiler l'interface (sur la machine de développement)

```bash
cd frontend
npm run build
```

Produit `frontend/dist/`, que le backend sert automatiquement s'il le trouve.

### 2. Copier le projet

Tout le dossier **sauf** les répertoires régénérables ou spécifiques à la machine :

```bat
robocopy D:\CLAUDE\peplink-supervision E:\peplink-supervision /E /XD .venv node_modules data __pycache__ .pytest_cache .git
```

`frontend/dist` doit être copié (c'est l'interface). `frontend/src`, `package.json` et les
tests ne sont pas nécessaires au fonctionnement, mais restent utiles pour diagnostiquer.

### 3. Installer et lancer (sur le PC de terrain — Python 3.12)

```bat
cd peplink-supervision\backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe lancer.py
```

`lancer.py` est le même point d'entrée que l'exécutable : mode routeur, sauvegarde de la
base avant chaque démarrage, `.env` lu à côté, navigateur ouvert quand le serveur répond.

Sur **macOS / Linux**, mêmes étapes avec les chemins Unix :

```bash
cd peplink-supervision/backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python lancer.py
```

> **macOS** : `.venv` et `node_modules` sont propres à chaque machine — ne pas les copier,
> les recréer. Les outils de capture `tools/*.cmd` et `*.ps1` sont spécifiques à Windows :
> sur macOS, `curl` est natif et les commandes équivalentes figurent plus bas.

Interface et API sur <http://localhost:8000>.

Pour y accéder depuis un autre poste du même réseau, poser `HOST=0.0.0.0`. **L'API n'a
aucune authentification** : à ne faire que sur un réseau de confiance.

Les routes qui manipulent les identifiants des équipements — appairage, réappairage,
retrait, découverte, nommage de session — sont restreintes à la machine locale
(`app/api/security.py`). **Les routes de LECTURE ne le sont pas**, et ce n'est donc pas
une option sans conséquence : en écoutant sur `0.0.0.0`, `GET /api/routers` livre à
quiconque atteint le port le numéro de série de l'équipement, sa position GPS, et les noms
de WAN tels que l'administration du routeur les porte — lesquels contiennent souvent le
numéro de ligne et l'ICCID de chaque SIM. Le garde protège les accès, pas les données.

### En-tête Host (DNS rebinding)

Toute requête — HTTP comme WebSocket — dont l'en-tête `Host` n'est ni `localhost`, ni une
adresse IP littérale, ni un nom listé dans `ALLOWED_HOSTS` reçoit un 400. Une attaque par
DNS rebinding porte forcément un nom de domaine : sans cette garde, une page dont le domaine
est rebasculé vers 127.0.0.1 franchissait `require_loopback` (le navigateur est local) et
lisait toutes les routes, capture brute comprise. Avec `HOST=0.0.0.0`, un téléphone du LAN
qui ouvre l'écran par l'adresse IP du poste passe ; par le nom du poste, il faut le déclarer.

### CORS

`CORS_ORIGINS` vaut par défaut le serveur de développement Vite. En production l'interface
est servie par ce même backend, donc de même origine : aucune entrée n'est nécessaire.

`"*"` reste accepté mais **désactive alors les cookies inter-origines**. La spécification
CORS interdit la combinaison « toute origine » + identifiants ; Starlette la contourne en
renvoyant l'origine de la requête à la place de `*`. Mesuré avant correction : une requête
portant `Origin: https://exemple.invalid` recevait cette origine en réponse avec
`Allow-Credentials: true` — autrement dit, **toute page ouverte sur le portable pouvait
interroger l'API**. Seules les méthodes `GET` et `OPTIONS` sont exposées.

### 4. Configurer le routeur

Créer `backend/.env` :

```
PEPLINK_MODE=router
```

et `backend/routers.yaml` à partir de `routers.example.yaml` (adresse du routeur +
identifiants). Redémarrer.

Pour vérifier que l'installation est saine avant de brancher le routeur :
`.venv\Scripts\python.exe -m pytest -q` (aucun accès réseau requis).

## Première mise en service avec un vrai routeur

La séquence authentification + scrutation **a tourné contre un MAX HD4 MBX en ligne**
(login par cookie, 4 SIM, GPS, WebSocket, historique). Ordre conseillé pour isoler les
problèmes sur un nouvel équipement :

1. **Valider l'environnement d'abord** : `pytest -q` (aucun réseau requis). Si les tests
   passent, tout échec ultérieur vient du routeur ou des identifiants, pas de l'installation.
2. **Lancer en mode simulateur** (défaut) et ouvrir l'interface : confirme que le serveur,
   l'interface et le WebSocket fonctionnent sur cette machine.
3. **Vérifier l'accès au routeur** hors application, avec les commandes `curl` ci-dessus.
   Si elles échouent, inutile de chercher côté application.
4. **Passer en mode router** : `PEPLINK_MODE=router` dans `.env` + `routers.yaml` renseigné
   (ces deux fichiers n'existent pas au départ, les créer depuis les `.example`).
   Démarrer avec `POLL_INTERVAL_SECONDS=10` plutôt que 3, moins agressif pour une session
   admin ; abaisser ensuite si tout est stable.
5. **Lire les journaux du serveur** au premier cycle : ils indiquent le mode, le nombre de
   routeurs, et toute erreur d'authentification ou de lecture.

En cas d'écart entre les données réelles et l'affichage, capturer la réponse brute et
comparer avec `tests/fixtures/dome_pro_duo_8.5.4.json` : le mapping se corrige dans
`app/peplink/router_api.py` (fonction `parse_wan_connection`), seul fichier concerné.

## Configuration

Copier `backend/.env.example` en `backend/.env` et ajuster.

| Variable | Défaut | Rôle |
|---|---|---|
| `PEPLINK_MODE` | `mock` | `mock` (simulateur) ou `router` (API locale réelle) |
| `POLL_INTERVAL_SECONDS` | `3` | Période de scrutation |
| `DATABASE_PATH` | `./data/samples.db` | Base des mesures |
| `RETENTION_JOURS` | `30` | Jours gardés à pleine résolution ; `0` garde tout (voir ci-dessous) |
| `ROUTERS_CONFIG` | `./routers.yaml` | Fichier de flotte |
| `CORS_ORIGINS` | serveur Vite | Origines autorisées depuis un navigateur |
| `ALLOWED_HOSTS` | vide | Noms d'hôte admis en plus de `localhost` et des adresses IP |

Les chemins relatifs sont résolus depuis `backend/`, quel que soit le répertoire courant.

### Rétention de la base

Un relevé par lien toutes les cinq secondes fait des dizaines de millions de lignes par an.
Au-delà de `RETENTION_JOURS` (30 par défaut), une tâche de fond (`app/retention.py`), une
fois par heure et jamais au démarrage :

1. **fige** chaque session close : sa fiche (volumes, coupures et micro-coupures,
   disponibilité, latences…) et son journal sur la fenêtre que l'écran relit. Une session
   close s'affiche ensuite **à l'identique** ;
2. **regroupe à la minute** les relevés plus anciens que `RETENTION_JOURS` + 1 jour, et
   l'heure gardée par le routeur : pour chaque grandeur des courbes, nombre de mesures,
   minimum, maximum et somme ; pour la bande, les comptes (relevés, connectés, dans le
   bond…). Une minute sans relevé n'a pas de ligne, une grandeur non mesurée reste sans
   valeur — jamais un zéro. Les relevés bruts regroupés sont effacés ;
3. **compacte** le fichier (`incremental_vacuum` ; une base créée avant cette version est
   convertie une fois par un `VACUUM` complet, qui suspend les écritures le temps de
   réécrire les 30 jours restants).

Ce qui est perdu, et dit : au-delà de la rétention, une courbe a la résolution de la minute
(chaque minute rend son minimum et son maximum, `source = "minute"`, `grouped_before`), la
bande aussi, l'export CSV d'une session donne une ligne par minute et par lien (moyennes,
état connecté le plus défavorable, message « regroupé à la minute »), l'heure du routeur
n'est plus exportable à part, et un taux de perte n'est gardé que s'il était publiable
(200 paquets). Le journal d'une fenêtre qui n'est pas celle d'une session close ne garde
avant la borne que les épisodes figés des sessions (`grouped_before`). Le travail se fait
par lots d'une heure d'un équipement — une transaction de quelques dizaines de
millisecondes, sous le verrou d'écriture ; un arrêt au milieu n'annule que le lot en cours.
La toute première purge est précédée d'une copie `samples.db.avant-retention.bak`, jamais
élaguée.

### Brancher de vrais routeurs

1. Copier `backend/routers.example.yaml` en `backend/routers.yaml`.
2. Renseigner `host` et **une** méthode d'authentification :
   - compte admin : `username` / `password` (session par cookie) ;
   - ou client API : `client_id` / `client_secret` (voir ci-dessous).
3. Passer `PEPLINK_MODE=router` et redémarrer.

#### Client API en lecture seule — recommandé

L'application ne fait que des lectures. Plutôt que de conserver un mot de passe admin,
créer un client API depuis une session admin :

```bash
curl -k -s -b cookies.txt -X POST https://<ip>/api/auth.client \
  -H "Content-Type: application/json" \
  -d '{"action":"add","name":"peplink-supervision","scope":"api.read-only"}'
```

La réponse porte `clientId` et `clientSecret`, à mettre dans `routers.yaml`. Le jeton obtenu
vaut **48 h** et est renouvelé automatiquement.

Deux pièges, mesurés sur matériel — l'endpoint a la forme d'OAuth2 mais **pas ses
conventions de nommage** :

| | Fonctionne | Échoue |
|---|---|---|
| Demande de jeton | `clientId` / `clientSecret` en **form-urlencoded** | `client_id`/`client_secret`, `grant_type`, ou du JSON |
| Transmission | `?accessToken=` | `?access_token=`, `Authorization: Bearer` |

**Le scope `api.read-only` ne couvre pas tout** : `info.capability` et `config.wan` restent
refusés, alors que les métriques radio, le débit, l'identité, le GPS et SpeedFusion passent.
L'application le gère en reconstituant son profil de capacités par sondage des endpoints
accessibles — l'inventaire des WAN se limite alors aux WAN actifs, ce que le champ `source`
du profil signale (`declared` contre `probed`).

Les WAN cellulaires sont découverts automatiquement (identifiants numériques : `"2"`, `"3"`…).
Si le routeur a un GPS, `use_gps: true` (défaut) fait servir sa position comme position du
site — indispensable en véhicule mobile ; les coordonnées du fichier ne servent alors que de
repli sans point GPS fixé.

### Capturer les réponses d'un routeur (PC de terrain)

Si le routeur n'est pas sur le réseau de la machine de développement, inutile de déplacer
le projet : il suffit de **capturer les réponses brutes de l'API** sur un PC du réseau
routeur, puis de les ramener pour caler le mapping.

Trois méthodes, de la plus simple à la plus complète :

**1. Le navigateur** — zéro outil. Se connecter à l'interface d'admin du routeur
(`https://192.168.50.1`), puis, dans le **même onglet**, appeler directement :

```
https://192.168.50.1/api/status.wan.connection
```

Le JSON s'affiche (la session est portée par le cookie) : `Ctrl+S` pour l'enregistrer.
C'est l'endpoint le plus important. Idem pour `status.wan`, `info.location`, `info.system`.

**2. `tools/capture_router_api.cmd`** — capture les 6 endpoints d'un coup. Fichier batch
utilisant `curl.exe` (natif Windows 10+) : **pas soumis à la stratégie d'exécution
PowerShell**, rien à installer.

```bat
capture_router_api.cmd 192.168.50.1 admin
capture_router_api.cmd 192.168.50.1 -client CLIENT_ID
```

Le mot de passe, ou le secret du client, est **demandé sans écho** : sur la ligne de
commande il restait dans l'historique et la liste des processus. Les deux scripts parlent
au firmware comme le client : `clientId` / `clientSecret`, sans `grant_type`, et le jeton en
`accessToken`.

**3. `tools/capture_router_api.ps1`** — équivalent PowerShell (occulte les jetons
automatiquement). Nécessite que les scripts soient autorisés ; sinon :
`powershell -ExecutionPolicy Bypass -File capture_router_api.ps1 ...`

**Sur macOS / Linux**, `curl` est natif — deux commandes suffisent :

```bash
curl -k -s -c /tmp/pep.txt -X POST https://192.168.209.1/api/login \
  -H "Content-Type: application/json" -d '{"username":"admin","password":"MOTDEPASSE"}'
curl -k -s -b /tmp/pep.txt https://192.168.209.1/api/status.wan.connection -o status.wan.connection.json
```

> Les fichiers produits peuvent contenir IMEI, ICCID et position GPS : les relire avant
> transmission. Penser à compléter `contexte.txt` (modèle + version de firmware), car les
> champs cellulaires varient selon les deux.

### Inventaire des WAN

L'inventaire brut de `info.capability` ne correspond pas à ce que compte l'exploitant. Sur
le MAX HD4 MBX il déclare 11 entrées ; deux corrections le ramènent aux **8 WAN réels** :

- un port Ethernet peut être **basculé en port LAN** (`asLan` dans `status.port`) : il reste
  déclaré mais n'est plus un WAN. Sur l'équipement d'essai, le port 3 est dans ce cas, d'où
  2 WAN filaires et non 3 ;
- les **emplacements de modem USB** sont des logements d'accessoire, comptés à part et non
  supervisés tant qu'aucun périphérique n'y est reconnu.

Reste donc : 4 cellulaires, 2 filaires, 2 Wi-Fi. Les modems étant bi-SIM (`dualSim`), le
châssis offre 8 emplacements SIM.

La **priorité de règle sortante** de chaque WAN est également remontée : plusieurs WAN de
même priorité se partagent le trafic, une priorité supérieure n'est qu'un secours. Un lien
de secours est signalé comme tel, ce qui évite de chercher pourquoi son débit reste à zéro.

### Ce que l'application lit sur l'équipement

| Endpoint | Rôle | Authentification |
|---|---|---|
| `info.firmware` | version du firmware — sonde de détection | **non** |
| `info.time` | horloge de l'équipement | **non** |
| `status.wan.connection` | état radio de chaque WAN (porteuses, cellule, niveaux) | oui |
| `status.traffic` | débit instantané par WAN, mêmes identifiants de WAN | oui |
| `status.system.info` | modèle, code produit, **numéro de série**, firmware, CPU, alimentation, MAC | oui |
| `info.capability` | fonctionnalités déclarées + inventaire complet des WAN du châssis | oui |
| `info.location` | position GPS | oui |
| `status.port` | rôle des ports physiques (`asLan`) | oui |
| `status.pepvpn` | tunnel SpeedFusion : état, latence, perte et désordre **par lien** | oui |

Le **numéro de série** est la seule clé d'identité stable : l'adresse IP dépend du réseau
où l'on se branche et le nom est modifiable depuis l'administration web.

**Seuls `info.firmware` et `info.time` répondent sans session** — mesuré sur MAX HD4 MBX
8.5.4, tout le reste renvoie `401`. C'est toute la surface exploitable pour détecter un
équipement avant d'avoir ses identifiants. Ces refus **n'alimentent pas** le compteur
anti-force-brute (vérifié : 12 refus consécutifs, puis connexion admin réussie), donc une
sonde de découverte est sans risque. Les **tentatives de connexion ratées**, elles, comptent :
cinq suspendent l'accès admin depuis l'adresse pendant 5 minutes — d'où l'interdiction de
toute reconnexion automatique après un refus d'identifiants.

`status.system.info` et `info.capability` **ne figurent pas dans la référence officielle
de l'API** mais répondent sur les matériels validés. Leur absence est traitée comme un cas
normal : l'application continue sur `status.wan.connection` seul et affiche « modèle
inconnu ».

`info.capability` déclare l'inventaire matériel indépendamment de la configuration : sur le
MAX HD4 MBX il annonce 11 WAN (4 cellulaires, 3 Ethernet, 2 USB, 2 Wi-Fi) là où
`status.wan.connection` n'en expose que 4 exploitables.

### Matériel de référence

Le parsing est **validé contre deux captures réelles**, dont les fixtures (identifiants
anonymisés) servent de tests :

| Matériel | Firmware | Modem | Liaison | Fixture |
|---|---|---|---|---|
| Peplink Dome Pro Duo | 8.5.4 build 6264 | Quectel RM520N-GL | 2 SIM Orange, 5G NSA | `dome_pro_duo_8.5.4.json` |
| Pepwave MAX HD4 MBX | 8.5.4 build 6253 | Telit LM960 | 4 SIM Orange, LTE-A 4 porteuses | `max_hd4_mbx_8.5.4.json` |

Deux particularités communes aux deux firmwares :

- **Les niveaux radio ne sont pas plats.** Ils vivent sous `cellular.rat[].band[].signal`,
  une porteuse par entrée : le modem agrège plusieurs porteuses (5G n78 pour les données +
  ancres LTE). Chaque porteuse a ses propres RSRP/RSRQ/SINR — sur la capture, l'ancre LTE B1
  est à −97 dBm alors que la porteuse 5G n78 est à −108 dBm. L'application expose donc
  **toutes** les porteuses, et retient comme valeurs principales celles de la porteuse qui
  porte les données.
- **L'identité de cellule est sous `cellular.cellTower`** (`cellId`, `tac`), l'opérateur sous
  `cellular.carrier.name`, et `mcc`/`mnc` sont des chaînes (`"208"`, `"01"`).

Et trois écarts entre les deux modèles, qui ont dicté la forme du mapping :

- **`rat[].name` peut être vide.** Le HD4 ne nomme pas ses RAT : la famille de technologie
  est alors déduite du libellé de la porteuse (`"LTE Band 3 (1800 MHz)"` -> `LTE`).
- **`network` et le libellé de porteuse ne se comparent pas directement** : le HD4 annonce
  `"LTE-A"` côté WAN et `"LTE ..."` côté porteuse. La sélection de la porteuse principale
  compare donc des *familles* (`LTE-A` -> `LTE`, `5G NSA` -> `5G`).
- **`dataTechnology` n'est pas toujours une technologie.** Il vaut `"5G NSA"` sur le Dome
  Pro Duo (plus précis que `network`) mais `"FDD / CA Downlink"` sur le HD4 — un descripteur
  de duplex et d'agrégation. Il n'est retenu comme libellé que s'il nomme une génération.

En LTE-A pure, toutes les porteuses sont LTE et **la première listée est la primaire** :
c'est la seule dont le SINR soit renseigné, les secondaires remontant `0.0`.

- **`pci: 0` sur toutes les porteuses est une absence de mesure**, pas une cellule de PCI 0.
  Le Telit LM960 ne remonte pas le PCI ; le Quectel RM520N-GL si. Un PCI 0 réel existe, mais
  jamais simultanément sur toutes les porteuses agrégées — c'est le critère retenu.

Un repli existe pour les firmwares antérieurs à 8.1.2 (WAN de type `gobi`, porteuses listées
à plat sans `rat[]`). Il est écrit d'après la documentation et testé contre des charges utiles
synthétiques : **non validé sur matériel**, faute d'équipement assez ancien.

### WAN Wi-Fi amont

Un Peplink peut agréger un lien Wi-Fi (typiquement une **Starlink** ou un hotspot) à ses
modems cellulaires. Ces WAN sont supervisés au même titre : le routeur en expose SSID, BSSID,
canal, force du signal et niveau, sous `wifi.signal`. La bande (2,4 / 5 / 6 GHz) est déduite
du canal.

Ils n'ont ni RSRP/RSRQ/SINR, ni porteuses agrégées, ni Cell ID : l'interface leur réserve une
carte dédiée (force du signal + niveau, seuils Wi-Fi usuels) et un historique limité à la
force. Les WAN désactivés (`enable: false`) et les WAN ethernet sont ignorés.

> Sur d'autres modèles ou firmwares, les champs peuvent différer. En cas d'écart, capturer
> (voir ci-dessous) et compléter `tests/test_router_api.py` avec la nouvelle fixture.

---

## L'identité de cellule, et ce qu'on en tire

**La décomposition de l'ECI.** L'identifiant de cellule LTE n'est pas atomique : ses 20 bits
de poids fort désignent la station de base, les 8 de poids faible la cellule (secteur ×
porteuse). Quatre Cell ID différents peuvent donc désigner **une seule station** — c'est le
cas sur les deux matériels de référence, et l'interface le signale : quatre SIM sur un même
eNodeB n'offrent aucune diversité de trajet, quel qu'en soit le nombre.

En 5G SA, l'identifiant est un NCI dont la longueur du champ gNB est configurable par
l'opérateur : la décomposition serait une invention, elle n'est pas faite. En 5G NSA en
revanche, `cellTower` désigne l'ancre LTE, donc un ECI décomposable.

C'est tout ce que l'application tire de l'identité de cellule : elle ne nomme **aucune
antenne**. La section suivante dit pourquoi, et pourquoi il ne faut pas réessayer.

## Pourquoi on n'identifie pas l'antenne

L'enrichissement qui désignait une « antenne probable » — OpenCellID, index ANFR local,
convertisseur `tools/convert_anfr.py`, sites déclarés autour, carte Leaflet, route
`GET /api/antenna/resolve` — **a été entièrement retiré** : l'enquête ci-dessous a établi
qu'il ne pouvait pas aboutir. Elle est résumée ici pour n'avoir pas à la refaire.

**Aucun registre public ne relie une cellule à un site.** L'ANFR décrit des sites —
coordonnées, bandes autorisées, azimuts de panneaux — sans aucun identifiant réseau, là où le
routeur nomme des cellules (`eNodeB 100529, secteur 9`). L'ARCEP publie bien une
correspondance site opérateur ↔ station ANFR, mais son `num_site` n'est **pas** un eNodeB :
sur les 32 607 sites Orange, sa partie numérique va de 1 à **84 330** quand l'eNodeB observé
vaut **100 529**. Seul l'opérateur détient cette table.

**Les bases collaboratives rendent un barycentre de couverture, pas une position d'antenne.**
Vérifié en août 2026 : OpenCellID répond « Cell not found » sur les trois cellules relevées ;
beaconDB (successeur libre de MLS) en connaît une, mais son centroïde d'observations est
**annoncé à 232 m** quand les deux sites candidats sont à **236 m** et **255 m** — il ne
départage rien. Rien de gratuit ailleurs : CellMapper l'interdit par ses CGU, Mozilla Location
Service a fermé en 2024, radiocells.org n'expose plus d'API, et les sources couvrantes
(Unwired Labs, Google Geolocation, Combain) sont payantes.

**Le matériel ne donne pas de quoi trianguler.** Vérifié sur MAX HD4 MBX 8.5.4 : ni Timing
Advance, ni cellules voisines, ni PCI (le Telit LM960 renvoie 0 partout) ; ni SSH, ni SNMP,
ni Telnet, ni passthrough AT. `cmd.channelPci.scan` **existe** sur ce firmware mais les quatre
WAN cellulaires le refusent (`WAN connection does not support`) — le paramètre s'appelle
`connId` et non `wanId`, preuve que l'endpoint est câblé côté routeur et qu'il échoue en aval,
dans le module. **L'état du lien n'y change rien — mesuré, ne pas refaire ce test** : réponse
identique, mot pour mot, sur un WAN administrativement désactivé (`enable: false`) et sur un
WAN en service. La fonction existe chez Peplink sous le nom de **Site Survey**, et ses
prérequis sont publiés : firmware 8.5.3 ou plus, care plan actif, et **modèle 5GN ou 5GK** —
c'est-à-dire un modem **Qualcomm X62 ou X65**. Le frein est le module, pas le châssis ni le
firmware : un module d'extension à modems X62 (`EXM-MBX-T4-5GN`) rendrait ce MBX éligible.

**Depuis un point fixe, il n'existe donc aucun moyen d'identifier le site avec certitude.**
Les voies restantes demandent de se déplacer, ou un Timing Advance mesuré depuis au moins
trois positions — hors de portée de ce matériel. Le **GPS du routeur** reste lu et enregistré :
c'est une mesure de l'équipement, sans rapport avec l'ANFR.

---

## API

Toutes les routes du superviseur (`app.main`) ; `tests/test_reference_api.py` compare
cette table aux routes servies. `{id}` est l'identifiant d'équipement que rendent
`/api/devices` et `/api/routers` ; `{session}` le numéro d'une session. « Poste local » :
refusée à tout client qui n'est pas la machine elle-même, et à une page d'une autre origine.

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/health` | État, mode, nombre d'équipements, période de scrutation |
| `GET` | `/api/discover` | Équipements Peplink détectés sur le réseau (poste local) |
| `GET` | `/api/devices` | État de chaque équipement supervisé (en attente, connecté, identifiants refusés, injoignable…) |
| `POST` | `/api/devices/pair` | Met un équipement en supervision (poste local) |
| `GET` | `/api/devices/known` | Équipements déjà appairés depuis ce poste, et si le trousseau garde leurs identifiants — un booléen, jamais leur contenu (poste local) |
| `POST` | `/api/devices/known/{id}/reconnect` | Réappaire un équipement connu avec les identifiants du trousseau, en une tentative : 401 refus, 502 injoignable, 409 rien de retenu. `{id}` est son `device_id` opaque tel que `/api/devices/known` le rend, jamais sa série (poste local) |
| `POST` | `/api/devices/{id}/reauth` | Autorise une nouvelle tentative après un refus d'identifiants — rien ne retente sans cette action (poste local) |
| `DELETE` | `/api/devices/{id}` | Retire un équipement de la supervision ; les mesures enregistrées restent (poste local) |
| `GET` | `/api/devices/{id}/capture/{endpoint}` | Réponse BRUTE d'un endpoint de la liste de capture, pour archiver une fixture — non anonymisée (poste local) |
| `GET` | `/api/routers` | Flotte + dernier relevé par WAN |
| `GET` | `/api/routers/{id}` | Détail d'un routeur |
| `GET` | `/api/routers/{id}/history` | Série temporelle (`wan`, `metric`, `minutes` ou `since`/`until` ; `min_packets` pour un taux : la règle de publication de l’écran, appliquée avant l’allègement) |
| `GET` | `/api/routers/{id}/timeline` | Frise : ce que chaque lien a fait, en tranches égales (`minutes` ou `since`/`until`) |
| `GET` | `/api/routers/{id}/events` | Événements datés de la fenêtre : coupures, rétablissements, épisodes de latence |
| `GET` | `/api/routers/{id}/sessions` | Mises sous tension de l'équipement, de la plus récente à la plus ancienne (`limit`) |
| `GET` | `/api/sessions/{session}/summary` | Fiche d'une session : ce qui la rend comparable à une autre |
| `POST` | `/api/sessions/{session}/label` | Nomme une session (poste local) |
| `GET` | `/api/sessions/{session}/export.csv` | Relevés de la session en CSV, envoyés au fil de l'eau ; `complet=true` (SSID, BSSID, position exacte) réservé au poste local |
| `GET` | `/api/sessions/{session}/export-routeur.csv` | Historique gardé par le routeur sur la session, à 10 s, en CSV à part |
| `WS` | `/ws/routers/{id}` | Flux temps réel des relevés |

Métriques d'historique : `rsrp`, `rsrq`, `sinr`, `rssi`, `rx_kbps`, `tx_kbps`,
`rtt_ms`, `loss_percent`. Les deux dernières viennent du tunnel SpeedFusion et
n'existent que là : ce sont elles qui font geler une image en bonding.

---

## Témoin de lien Ethernet

Un second exécutable (`temoin-ethernet-*`, `backend/temoin/`), pour une seule question :
ce lien Ethernet est-il stable ? Depuis les sources : `cd backend && .venv/bin/python -m
temoin`.

- **Écoute** sur `127.0.0.1` seulement — même si `HOST` est posé pour le superviseur, le
  témoin ne s'ouvre jamais au réseau. Port **8010** par défaut (`PORT` le change), le
  superviseur gardant 8000 : les deux tournent ensemble. Port pris : le suivant libre (jusqu'à
  vingt plus loin), et il le dit. `OUVRIR_NAVIGATEUR=0` n'ouvre pas le navigateur. L'en-tête Host
  n'accepte que `localhost` et les adresses IP littérales (DNS rebinding).
- **Base** `data/temoin.db`, à côté de l'exécutable (sous `backend/data` depuis le dépôt),
  distincte de celle du superviseur ; copie `temoin.<horodatage>.db.bak` à chaque
  lancement, deux gardées, rien de copié si la base n'a pas bougé. Illisible : le témoin
  s'arrête et liste les copies disponibles.
- **Verrou** `data/temoin.db.verrou` (verrou du système, relâché à la mort du processus) :
  un seul témoin par base. Un second lancement depuis le même dossier lit le port du premier
  dans `data/temoin.db.port` et rouvre son écran au lieu de mesurer en double.
- **Identifiants** au trousseau du système (comme le superviseur), quand la case « Retenir sur ce
  poste » est cochée (elle l'est par défaut). Jamais de série, de nom de WAN ni d'identifiant dans une réponse ou le
  journal : le lien se dit « WAN n », l'équipement par son modèle.

Routes (`temoin.api`) ; l'écran statique (`temoin/ecran`) est servi à la racine. Celles qui
écrivent sont réservées au poste lui-même et refusées à une page d'une autre origine : une
connexion déclenchée par un tiers consommerait des essais d'identifiants (cinq refus
bloquent l'administration du routeur cinq minutes).

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/sante` | `status`, `version`, nom du trousseau — ce que vérifie la CI après construction |
| `GET` | `/api/etat` | Phase de l'écran : `recherche` (équipements trouvés), `identifiants`, `choix_lien` (WAN Ethernet proposés), `mesure` |
| `POST` | `/api/connexion` | Se connecte à un équipement (`hote`, identifiants facultatifs, `retenir`) : 401 refus, 502 injoignable, 422 identifiants incomplets — refusés avant le routeur (poste local) |
| `POST` | `/api/lien` | Choisit le WAN Ethernet à suivre (`wan_id`) et reprend ou ouvre son observation (poste local) |
| `POST` | `/api/observations/nouvelle` | Clôt l'observation en cours et en ouvre une neuve sur le même lien (poste local) |
| `GET` | `/api/observations` | Les observations : modèle, lien, début, dernier relevé, en cours ou non |
| `GET` | `/api/observation` | Verdict, bande et coupures d'une observation (`id`, la courante sinon ; `fenetre` : `tout`, `1h`, `24h`) |
| `GET` | `/api/observations/{id}/coupures.csv` | Export des coupures : début, fin, durée, type, cause |
| `GET` | `/api/observations/{id}/releves.csv` | Export des relevés : instant, mesuré, connecté, message, ancienneté, latence |

**Exports CSV** : même convention que le superviseur — BOM UTF-8, séparateur `;`, virgule
décimale, heure locale du poste (`# fuseau;UTC+02:00` en en-tête), pour s'ouvrir tel quel
dans Excel en français. L'en-tête ne porte que modèle, firmware (observation en cours
seulement), lien (« WAN n »), début et fin : ni série, ni position, ni nom de WAN. Une
grandeur absente est une cellule vide, jamais 0 ; une cellule de texte qui commencerait par
`= + - @` est neutralisée. Une coupure dont la fin n'a pas été vue est marquée
« (fin non observée) » — sa durée est un minimum —, une coupure ouverte « (en cours) ».

---

## Tests

```bash
cd backend && .venv/bin/python -m pytest -q
cd backend && .venv/bin/ruff check .
cd frontend && npm test
cd frontend && npx tsc --noEmit -p .
```

### Backend — sans aucun accès réseau :

- **parsing de l'API réelle** contre la fixture Dome Pro Duo (sélection des WAN, identité de
  cellule, agrégation de porteuses, porteuse principale, WAN Wi-Fi, GPS, cas dégradés) ;
- simulateur (bornes physiques, handovers, structure des porteuses, lien Wi-Fi, uptime,
  déterminisme par graine) ;
- stockage (écriture, historique, rejet des métriques inconnues, **migration** d'une base
  créée par une version antérieure) ;
- découverte, appairage, sécurité des routes locales, tunnel SpeedFusion, WebSocket.

### Frontend — Vitest + Testing Library

Portent sur les **règles de lecture**, pas sur du pixel — ce sont elles qui se sont
révélées être la source des défauts les plus trompeurs :

- **échelle des graphes** : la fenêtre minimale empêche un signal stable de paraître
  instable, l'axe d'un débit part de zéro, une interruption de collecte est coupée et non
  franchie, les graduations sont formatées sur l'amplitude de l'axe ;
- **verdict de liaison** : un lien filaire compte parmi les liens mais ne reçoit pas de
  qualité radio, et l'âge de la mesure est rapporté ;
- **notes de qualité** : une donnée absente n'est pas une donnée mauvaise, une grandeur
  sans seuils ne reçoit pas de note, et chaque couleur reste au-dessus de 4,5:1 de
  contraste sur le fond des cartes ;
- **cartes de lien** : une carte filaire n'affiche aucune grandeur radio, une SIM expose sa
  station de base et son secteur, un lien de secours est signalé ;
- **tri des WAN** : « 10 » ne passe pas avant « 2 » sur un châssis qui en expose onze.

---

## Architecture

```
backend/app/
  peplink/      base.py (interface) · mock.py (simulateur) · router_api.py (API réelle) · factory.py
  storage/      db.py (SQLite, repository)
  poller.py     boucle de scrutation → persiste → diffuse (WebSocket)
  api/          routes.py (REST) · ws.py (temps réel)
frontend/src/
  components/   VerdictBanner · WanRow (niveau 1) · DeviceCard · PairingForm
                SignalTile · WanCard · BandTable · HistoryChart
  pages/        DeviceShell (flux temps réel partagé) · Overview (niveau 1)
                WanDetail (niveau 2) · Discovery
  lib/          quality.ts (seuils radio) · verdict.ts · format.ts · chart.ts
                wanOrder.ts · useElementSize.ts
```

**Stockage** : SQLite via un repository, suffisant pour quelques routeurs scrutés à la
seconde. Le passage à TimescaleDB/InfluxDB ne touche que `storage/db.py`.

> **Les colonnes `ant_*` d'une base existante ne sont pas supprimées.** Le schéma des bases
> neuves ne les crée plus et l'`INSERT` ne les renseigne plus, mais elles portent trois
> semaines d'historique réel et un `DROP COLUMN` serait irréversible. L'`INSERT` nommant ses
> colonnes explicitement, des colonnes surnuméraires restent sans conséquence.

**Graphes d'historique.** L'axe vertical part d'une fenêtre minimale reprise des seuils de
qualité, jamais de l'amplitude des données : sans elle, un RSRP qui varie de 2 dB en trente
minutes — c'est-à-dire un signal parfaitement stable — remplissait toute la hauteur et se
lisait comme une instabilité. Une mesure hors fenêtre élargit l'axe, elle n'est jamais
tronquée. Les débits sont affichés en Mbit/s et leur axe part de zéro, qu'ils ont
naturellement.

L'axe des temps est **linéaire**, et non catégoriel : une interruption de collecte occupe la
largeur qui lui revient au lieu d'être écrasée à celle d'un intervalle normal. Elle est en
outre tracée comme un **trou** — relier les deux bords donnerait à voir une continuité qui
n'a pas existé.

**Notes de qualité.** Une grandeur sans seuils n'en reçoit pas : un débit de 0 Mbit/s sur un
lien au repos est normal, pas mauvais. Seuls RSRP, RSRQ, SINR et la force Wi-Fi sont notés.

**Seuils de qualité** (`frontend/src/lib/quality.ts`) :

| | Excellent | Bon | Moyen | Faible |
|---|---|---|---|---|
| RSRP (dBm) | ≥ −85 | ≥ −95 | ≥ −105 | < −105 |
| RSRQ (dB) | ≥ −10 | ≥ −13 | ≥ −16 | < −16 |
| SINR (dB) | ≥ 20 | ≥ 13 | ≥ 5 | < 5 |

---

## Trouver un équipement sur le réseau

`GET /api/discover`, ou « Rechercher un équipement » dans l'interface. **Aucun identifiant
n'est envoyé.**

Il n'existe aucun protocole de découverte Peplink — mDNS, SSDP, LLDP et diffusion dirigée
sont tous restés muets sur l'équipement d'essai. La recherche procède donc par une courte
liste d'adresses probables : passerelles déclarées par le système, bornes usuelles du
sous-réseau local, puis adresses d'usine des gammes Peplink. **Jamais un balayage de
sous-réseau complet** : c'est lent, bruyant, et inutile puisqu'un Peplink de terrain est
presque toujours la passerelle.

Trois indices, du moins au plus probant, tous obtenus sans authentification :

| Indice | Nature |
|---|---|
| préfixe MAC dans le cache ARP (`a8:c0:ea`, `00:1a:dd`) | indice — la liste n'est pas exhaustive |
| nom de constructeur dans le certificat TLS | indice — `captive-portal.peplink.com` observé |
| réponse de `GET /api/info.firmware` | **preuve** — l'API est là et le firmware exploitable |

Les refus provoqués par ce sondage **n'alimentent pas** le compteur anti-force-brute
(vérifié : douze refus consécutifs, puis connexion admin réussie). Les tentatives de
*connexion* ratées, elles, comptent : cinq suspendent l'accès pendant 5 minutes — d'où la
règle absolue de ce module, il n'envoie jamais d'identifiant.

La route est **restreinte au poste local** : elle déclenche une activité réseau et prépare
l'appairage, deux choses qui n'ont pas à être pilotables depuis le réseau.

## Mettre un équipement en supervision

Depuis l'écran de découverte : « Mettre en supervision », identifiant et mot de passe, et
c'est fait — **sans redémarrer, sans éditer `routers.yaml`**. L'équipement est immédiatement
scruté aux côtés des autres.

**Une seule tentative de connexion, celle que vous déclenchez.** Aucune relance automatique,
aucun essai d'identifiants d'usine : cinq échecs suspendent l'accès à l'équipement pendant
cinq minutes, y compris avec les bons identifiants. L'échec distingue deux cas, et
l'interface le montre — `401` pour des identifiants refusés, `502` pour un équipement
injoignable : le premier demande de corriger un mot de passe, le second de vérifier un
raccordement.

Le **numéro de série** sert de clé d'identité : rebrancher le même équipement sur une autre
adresse le reconnaît au lieu d'en créer un doublon.

### Ce qui est conservé, et ce qui ne l'est pas

| | |
|---|---|
| En base (`devices`) | série, nom, adresse, modèle, firmware, dates |
| En mémoire, pour la session | **les identifiants** |
| Au trousseau du système, si « Retenir sur ce poste » est coché | **les identifiants**, une entrée par numéro de série |

Les mots de passe ne sont **jamais** écrits en clair sur le disque. Cochée à l'appairage
(elle l'est par défaut), « Retenir sur ce poste » les confie au trousseau du système —
Trousseau sur macOS, Gestionnaire d'identification sur Windows (`app/coffre.py`, paquet
`keyring`) — sous le service `peplink-supervision`. Une veille, en tâche de fond, reconnecte
chaque équipement retenu au démarrage **et dès qu'il apparaît** sur le réseau : tant qu'il ne
répond pas, elle recule comme la scrutation (3, 6, 12, 24 s, puis toutes les 30 s). Un refus
d'identifiants l'arrête pour cet équipement — jamais d'essai répété, cinq échecs suspendent
l'accès admin — jusqu'à ce que le trousseau garde d'autres identifiants. Décochée, l'entrée est effacée ; retirer l'équipement de la supervision l'efface
aussi. `/api/health` publie le trousseau employé (`coffre`), ou `null` : sans trousseau, les
identifiants sont à ressaisir à chaque démarrage, comme avant.

### État de chaque équipement

`GET /api/devices` distingue cinq états, et c'est ce qui dit à l'exploitant quoi faire :
`en attente`, `connecté`, `identifiants refusés`, `injoignable`, `erreur de configuration`.

Un équipement mal configuré **n'empêche plus le démarrage** : il est enregistré en erreur et
reste visible. Le mode est propre à chaque équipement, ce qui permet à une flotte de
démonstration simulée et à un équipement de terrain réel de coexister.

Un équipement **injoignable recule** : la pause avant sa tentative suivante double à chaque
échec consécutif — 3, 6, 12, 24 s à la cadence par défaut — jusqu'à un plafond de 30 s
(`_RECUL_MAX_S`, `poller.py`), et revient à la cadence dès qu'un relevé aboutit. Les
équipements ne sont allumés qu'à l'arrêt : une flotte en majorité éteinte est l'état normal, et
chaque muet coûtait un délai de connexion à chaque tour. Le prix : un équipement rallumé peut
mettre jusqu'à 30 s à réapparaître. Un refus d'identifiants ne recule pas — il est déjà
arrêté jusqu'à l'action de l'exploitant. Un réappairage pendant le recul est repris à la
cadence : la pause se découpe en tranches et s'interrompt quand l'équipement change.

## Le tunnel, pas seulement les liens

C'est le tunnel SpeedFusion qui porte le flux, pas les WAN pris isolément. Un lien peut
être parfaitement « connecté » côté WAN et **écarté du bond** : il ne transporte alors rien,
et rien dans `status.wan.connection` ne le dit.

`GET /api/status.pepvpn` en trois formes, toutes couvertes par le scope `api.read-only`
(vérifié sur matériel) :

| `infoType` | Contenu |
|---|---|
| `peer` | pairs et leur état, avec le `peerId` nécessaire à la suite |
| `profile` | profils SpeedFusion configurés |
| `tunnel` + `tunnelOption=<peerId>` | **état, latence, perte et désordre par lien du bond** |

Relevé sur l'équipement d'essai : la fibre répond en 2 ms, les quatre SIM entre 20 et 56 ms.
Ce sont ces grandeurs — latence, perte, désordre de paquets — qui font geler une image en
bonding, et **aucune ne figure ailleurs dans l'API**.

Les compteurs du routeur sont **cumulés depuis l'établissement du tunnel** : ils donnent une
moyenne sur plusieurs heures, sans rapport avec l'instant. Les taux sont donc calculés par
différence entre deux relevés. Le premier relevé après un démarrage n'a pas de référence et
ne produit aucun taux — mieux vaut ne rien dire qu'annoncer une moyenne de six heures. Un
compteur qui recule signale un tunnel remonté : on repart de zéro plutôt que de produire un
taux négatif.

Un lien connecté mais hors du bond est signalé comme tel et compte comme une dégradation :
le compter parmi les liens porteurs annoncerait une redondance qui n'existe pas.

> `config.pepvpn.profile` **expose la clé pré-partagée en clair**. L'application ne lit pas
> cet endpoint, et ne doit pas le lire.

## Interface : deux niveaux

**Niveau 1 — « est-ce que ça marche ? »** en un coup d'œil : un verdict en une phrase,
l'alerte de diversité de trajet le cas échéant, puis une ligne par lien portant les trois
faits qui se balaient : le lien tient-il, à quelle qualité, à quel débit.

Trois garde-fous protègent ce verdict, chacun fermant une façon d'afficher du vert à tort :

- **l'état de l'équipement** remonte jusqu'au verdict. Les relevés survivent en mémoire à
  la panne du routeur : sans cet état, l'écran affichait « Liaison OK » sur une liaison
  morte. Un refus d'identifiants et une injoignabilité sont distingués — le remède n'est
  pas le même ;
- **l'âge du relevé est mesuré par le serveur.** Le calculer dans le navigateur revenait à
  comparer deux horloges : sur un portable dont l'horloge avance, l'écart devient négatif,
  se retrouve borné à zéro, et affiche « à l'instant » indéfiniment sur des données mortes.
  Au-delà de 45 s, les mesures sont déclarées périmées ;
- **une hystérésis** : trois constats pour alerter, cinq pour rassurer. Un bandeau qui
  clignote toutes les trois secondes est un bandeau qu'on cesse de regarder. Une rupture,
  elle, s'affiche immédiatement.

**Niveau 2 — le détail d'un lien** : porteuses agrégées, identité de cellule, débits et
historique. Accessible en cliquant une ligne.

Le routage utilise **`HashRouter`** et non `BrowserRouter`. Le backend sert l'interface
compilée via `StaticFiles` monté sur `/` : une URL profonde comme `/e/mbx-01/wan/5` y
renvoie `404` au rafraîchissement (vérifié), là où `#/e/mbx-01/wan/5` fonctionne sans rien
demander au serveur. Les liens profonds restent partageables et le bouton Précédent
fonctionne.

L'interface vise l'écran d'un portable ; aucune adaptation téléphone n'est prévue.

## Limites connues

- Parsing validé sur **deux** modèles (Dome Pro Duo 8.5.4, MAX HD4 MBX 8.5.4) : à
  reconfirmer sur d'autres modèles/firmwares, les champs cellulaires variant sensiblement
  de l'un à l'autre.
- **Aucune antenne n'est identifiée, et ce n'est pas un manque à combler** : voir « Pourquoi
  on n'identifie pas l'antenne ». Le GPS remonté reste celui du routeur.
- Le débit par WAN vient de `status.traffic`, endpoint distinct dont la présence n'est
  vérifiée que sur MAX HD4 MBX. Un modèle qui le refuse le signale via ses capacités et
  l'interface masque alors la tuile, plutôt que d'afficher un vide.
- Les WAN **Ethernet actifs sont supervisés** — état du lien, adresse, durée de session et
  débit — car ils participent à la liaison agrégée et en portent souvent l'essentiel du
  trafic. Aucune grandeur radio ne leur est inventée.
- Les emplacements de modem USB restent hors supervision tant qu'aucun périphérique n'y est
  reconnu ; ils figurent dans l'inventaire déclaré par `info.capability`.
- Tous les modems ne remontent pas les mêmes champs : le HD4 ne fournit ni PCI (0 partout)
  ni largeur de canal. Les colonnes correspondantes restent vides, sans erreur.
- Le bundle front est dominé par recharts — un découpage dynamique serait utile si le poids
  devient gênant.
- Pas d'authentification sur l'API : à ne pas exposer publiquement en l'état. Les origines
  autorisées sont désormais explicites et les routes sensibles limitées au poste local,
  mais cela ne remplace pas une authentification.

## Suite envisagée

Mode terrain (aide à l'orientation d'antenne en temps réel), tableau de bord multi-sites,
analyses d'historique (dégradation, instabilité cellulaire, saturation), rapports de recette
PDF, et support InControl 2 pour la supervision à distance.
