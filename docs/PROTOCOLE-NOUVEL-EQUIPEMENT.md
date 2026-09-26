# Protocole de confrontation — nouvel équipement, nouveau réseau

Ce document sert le jour où l'outil rencontre un Peplink qu'il n'a jamais vu, sur
un réseau qui n'est pas celui du développement. Il est ordonné : chaque étape
suppose la précédente réussie, et chacune dit **ce qu'on attend**, **ce qui
échoue plausiblement** et **ce que l'échec prouve**.

Tout ce qui est validé jusqu'ici l'a été contre deux matériels — un MAX HD4 MBX
et un Dome Pro Duo, tous deux en firmware 8.5.4, tous deux cellulaires, tous deux
chez le même opérateur. C'est une base étroite. Les hypothèses qu'elle laisse
passer ne se verront que là-bas.

---

## Journal des confrontations

| date | matériel | réseau | ce qui a cédé | ce qui a tenu |
|---|---|---|---|---|
| 2026-09-19 | **Pepwave MAX HD2**, 8.5.4 build 5808, rév. 5 | `10.10.1.0/24`, HTTP sans TLS, Bouygues + Orange | le schéma d'URL figé en HTTPS (client **et** sonde) ; l'alerte « un seul lien » pour zéro ; `status.*` porte un secret ; **« Liaison rompue » sur une connectivité intacte** — puis un verdict qui **clignotait** sur le débit instantané ; séparateurs de milliers dans les identifiants de cellule ; « 2 sites » à côté d'une position immobile ; « 0 % de lien propre » sur un tunnel jamais monté. **Puis, à la revue des correctifs eux-mêmes** : un repli TLS → HTTP sur un simple délai dépassé, le garde loopback aveugle derrière le mandataire de dev, deux genres d'alerte qui remettaient le compteur à zéro, une paire non adjacente sans ligature, un NaN invisible au balayage de texte | découverte par MAC, appairage, fiche déclarée, 3 liens sur 7, seuils radio, quota, fiche de session, bascule de flotte, aucun mot interdit sur cinq écrans, fixture archivée |

---

## 0. Avant de brancher quoi que ce soit

**Les règles de sécurité du projet restent entières et ne se négocient pas
sur le terrain :**

- Le mot de passe du routeur est saisi **par l'exploitant**, jamais par moi.
- `.env` et `routers.yaml` restent en `chmod 600` et hors du dépôt.
- Les identifiants vivent en mémoire. La table `devices` ne doit **jamais**
  gagner une colonne de mot de passe ou de secret.
- **Ne jamais interroger la famille `config.*`.** `config.pepvpn.profile`
  expose la clé pré-partagée SpeedFusion **en clair** ; `config.wan.connection`
  expose les identifiants APN et PPP. `status.*` et `info.*` décrivent un état
  — **mais pas sans secret** : sur le MAX HD2 (8.5.4 build 5808),
  `status.wan.connection` porte `username` et `password` APN sous chaque SIM.
  Toute capture de cet endpoint doit **retirer ces deux clés** avant d'être
  archivée — retirées, pas remplacées par une valeur inventée.
- La découverte n'envoie **aucun identifiant**.
- **Jamais de relance automatique après un refus d'identifiants** : cinq échecs
  verrouillent le compte pour cinq minutes.
- Les routes d'écriture (`/discover`, `/devices/pair`, `DELETE /devices/{id}`,
  `/devices/{id}/reauth`, `/sessions/{id}/label`) sont restreintes au loopback.

**À préparer :**

```bash
cd backend && ./.venv/bin/python -m pytest -q
cd ../frontend && npx vitest run && npm run build
```

Les deux suites doivent être vertes **avant** le déplacement. Un échec constaté
sur le terrain doit pouvoir être imputé au matériel, pas à un code déjà cassé.

---

## 1. Trouver l'équipement sur le réseau

```bash
curl -s localhost:8000/api/discover | python3 -m json.tool
```

**Attendu** — une liste d'hôtes avec modèle et numéro de série.

**Échecs plausibles et ce qu'ils prouvent :**

| Symptôme | Cause probable | Action |
|---|---|---|
| Liste vide | La découverte balaie le sous-réseau local ; le routeur est ailleurs, ou le mDNS est filtré | Saisir l'hôte à la main dans `routers.yaml` |
| Hôte trouvé, modèle vide | Le firmware ne publie pas l'identité sans session | Normal — l'identité viendra après appairage |
| Timeout long | HTTPS sur port non standard, ou certificat auto-signé | Renseigner `verify_ssl: false` |
| Trouvé par la MAC, `api_reachable: false` | L'équipement ne parle qu'**HTTP**, sans TLS | La sonde essaie HTTPS puis HTTP et rend l'hôte **avec son schéma** (`http://…`) ; le client le respecte. Rencontré sur le MAX HD2 |

> La découverte n'est **pas** une condition d'usage. Un hôte écrit à la main dans
> `routers.yaml` suffit ; l'étape 1 est un confort, pas un passage obligé.

---

## 2. Appairer

Saisir les identifiants **côté exploitant**. Deux modes existent et le code les
gère tous deux :

- **client API** (`client_id` / `client_secret`) → jeton porteur ;
- **compte admin** (`username` / `password`) → session par cookie.

**Attendu** — `GET /api/devices` renvoie l'équipement avec `paired: true`.

**Si les identifiants sont refusés : s'arrêter.** Ne pas réessayer. Cinq échecs
verrouillent cinq minutes, et une relance automatique transformerait une faute de
frappe en incident d'exploitation.

---

## 3. Établir la fiche de capacités

C'est **l'étape qui décide de tout l'écran**. Le code tente `info.capability` ;
en cas de refus explicite du firmware (`stat: fail`, code 999), il reconstitue
la fiche **en sondant**.

**À vérifier dans le bandeau de l'équipement :**

- Badge **« fiche en cours »** → toutes les sondes n'ont pas abouti. Attendre un
  cycle. Ce badge n'est pas une erreur : il dit que ce qui n'est pas affiché
  n'est pas pour autant absent.
- Badge **« fiche reconstituée »** → `info.capability` est hors du périmètre des
  droits. **Conséquence à retenir : l'inventaire se limite alors aux WAN
  ACTIFS.** Un port désactivé manquera, sans rien qui le distingue d'un port
  absent. Si l'exploitant compte ses ports, c'est le moment de le lui dire.

**Le piège à surveiller :** un `999` veut dire « pas chez moi », pas « pas du
tout ». Une capacité qu'on n'a pas pu établir ne doit jamais s'afficher comme une
capacité niée.

---

## 4. Capturer la fixture — et l'anonymiser

Un nouveau matériel est une occasion rare. **Capturer avant de partir.**

La lecture passe par le serveur en marche — le client que l'appairage a
construit, sans que les identifiants transitent par personne — via une route
**restreinte au loopback** et à une liste blanche fermée :

```bash
curl -s localhost:8000/api/devices/<id>/capture/status.wan.connection
```

Ce qui en sort est **brut**. L'écrire dans le scratchpad en `chmod 600`, jamais
dans le dépôt, puis inventorier — par nom de champ ET par motif, **y compris
dans le texte libre** — avant d'anonymiser.

> Le texte libre est ce qui a laissé passer un numéro de ligne le 19 septembre :
> l'anonymiseur masquait les champs `name`, et `status.log` répète le nom de
> chaque WAN dans ses lignes de journal. Un motif ne connaît pas les clés ; il
> doit passer sur **tout** le texte de la capture, et le balayage de contrôle
> après anonymisation doit être celui-là — pas une relecture des champs. Le 19 septembre, l'inventaire par motif a remonté un « 15
chiffres » qui n'était que les décimales d'un pourcentage de CPU, et le
`username` du pair SpeedFusion, qui est la **série du concentrateur** : une
identité à anonymiser comme une série, pas un secret à supprimer.

Les endpoints à relever (et aucun autre) :

```
status.wan.connection   status.system.info     info.capability
status.traffic          status.port            info.location
status.pepvpn (peer|tunnel|profile)
status.wan.latency      status.wan.quality     status.log
```

**Anonymisation obligatoire, sans exception** — IMEI, IMSI, ICCID, numéros de
ligne et de téléphone, MAC, numéros de série, coordonnées GPS, adresses IP
opérateur.

> Attention particulière : sur le déploiement de référence, **les noms de WAN
> choisis par l'exploitant contiennent des ICCID complets et des numéros de
> ligne**. C'est vrai des captures comme des copies d'écran.

La fixture rejoint `backend/tests/fixtures/` et doit être un ajout **purement
additif** — `indent=2`, aucun reformatage du reste du fichier.

### 4 bis. Rejouer la capture de bout en bout

Une fixture n'éprouve pas que les parseurs. Inscrite dans `tests/rejeu.py`
(`CAPTURES`), elle traverse **la chaîne entière** — client réel avec sa gestion
des refus et des endpoints absents, collecteur, base, routes — dans
`test_rejeu_bout_en_bout.py`, et ce qui est vérifié n'est pas « le bon chiffre
sort » mais ce qu'un équipement inconnu ne doit jamais produire : une route qui
échoue, un nombre non fini, un identifiant devenu quantité, une clé secrète, une
absence écrite en toutes lettres, deux sessions pour une mise sous tension.

Puis l'écran monte son tableau de bord sur la **réponse réelle des routes** :

```bash
cd backend && .venv/bin/python tools/exporter_rejeu.py
```

écrit `frontend/src/test/reel/<equipement>.json`, que `reel.test.tsx` balaie.
Un test serveur vérifie que ces fichiers sont ceux que les routes rendent
aujourd'hui : un modèle qui change sans rejouer l'exportation fait tomber la
suite — c'est le cas même de la page noire du 19 septembre (un champ nouveau
côté écran, un serveur d'hier), rendu impossible à ignorer.

Ce que les trois captures ont en commun et ce qui les sépare, mesuré :

| endpoint | MBX | Dome | HD2 |
|---|---|---|---|
| `status.wan.connection`, `status.system.info`, `info.location` | ok | ok | ok |
| `info.capability`, `status.port`, `status.traffic`, `status.wan.latency` | ok | absent → **fiche sondée** | ok |
| `status.pepvpn.*` | ok | absent | ok, pair injoignable |

Quarante clés de `status.wan.connection` sont communes aux trois ; propres à un
seul châssis : `asLan`, `cellular.networkMode` (MBX), `cellular.eid`,
`cellular.speedfusionConnect5gLte`, `wifi` (Dome). Aucune n'est requise par un
parseur.

---

## 5. La liste de confrontation, panneau par panneau

Ce que l'écran doit faire selon ce que le matériel présente. Chaque ligne est
déjà couverte par un test automatique (`tests/test_confrontation.py` côté
serveur, `src/pages/confrontation.test.tsx` côté écran) — la colonne de droite
dit quoi regarder **en vrai**.

| Si le matériel… | …l'écran doit | Regarder |
|---|---|---|
| n'a **aucun modem** (Balance) | effacer les colonnes SINR / RSRP / RSRQ | pas une colonne de tirets |
| n'a **qu'un lien** (BR1) | écrire « un seul chemin physique » | le mot « bond » ne doit pas apparaître |
| a un **WAN Wi-Fi** | le tracer comme les autres, étiquette « Wi-Fi » | rail présent, ligne présente |
| est en **5G SA** | dire « station indéterminable » | **jamais « 0 site »** |
| n'a **pas de tunnel** | masquer les tuiles du bond | pas de zéro permanent |
| a un tunnel **muet** | tuiles présentes, valeurs en cadratin | pas de 0.0 |
| a **13 WAN** | tenir la mise en page sans barre de défilement horizontale | largeur de la table |
| ne publie **aucun débit** | **ne pas dessiner le brin de sortie** | absence ≠ zéro mesuré |
| publie des **nombres malformés** | n'écrire ni `NaN` ni `Infinity` | balayage du texte |
| a un WAN de type **`modem`** (USB) | le compter dans la fiche, pas dans la table | « 1 emplacement de modem USB, non supervisé » |
| a **tous ses liens hors du bond** | titrer « Tunnel SpeedFusion tombé », en ambre ; **aucun** brin de sortie | **jamais « Liaison rompue »** sur des liens debout : un état de tunnel seul ne fonde pas « plus rien ne transite » — et un débit instantané ne fonde aucun verdict non plus, il clignote |
| a des liens hors du bond pour **des causes différentes** | ne nommer aucune cause au niveau du bond | la cause n'est dite que si tous la partagent |
| a un **contrat de support expiré** | badge « support expiré » | un fait, pas une alarme |
| a des **identifiants à 6 chiffres et plus** (cellule, station, TAC) | les écrire **tels quels** | jamais de séparateur de milliers dans un nom |
| a un tunnel **jamais monté** sur la session | dire « le tunnel n'a porté aucun lien » | pas « disponible 0 % du temps » |
| voit **deux stations** sans bouger | dire « 2 stations », pas « 2 sites » | à côté d'une position GPS, « site » se lit comme un lieu |

---

## 6. Les contrôles à faire tourner sur place

**a. Le pipeline complet, contre une source indépendante.**
Comparer une grandeur lue par l'outil à la même grandeur lue dans l'interface web
du routeur — RSRP d'un modem, débit d'une interface. L'écart doit être nul ou
s'expliquer par le décalage d'un cycle.

**b. La cadence réelle.**
La fiche technique publie `période = cycle + pause`. Sur un châssis plus lent ou
un réseau plus chargé, la période dérive. C'est une mesure, pas un réglage : la
noter.

**c. Les trois règles d'alerte.**
Elles sont calées sur le déploiement de référence : hors bond 60 s, lien unique
15 s, interruption 300 s, rémanence 600 s. **Sur un autre usage, ces seuils sont
à rediscuter** — un équipement fixe n'a pas le même régime qu'un véhicule.

**d. La stabilité du bandeau.**
Lire le verdict plusieurs fois à quelques secondes d'écart, sur un réseau
calme. Il doit être **le même** : un verdict qui suit le débit d'un instant
clignote entre deux couleurs au gré des paquets. Un état de tunnel ou de lien
est stable ; un débit instantané ne l'est pas, et ne fonde aucun verdict.

**e. Le balayage des mots interdits.**
Sur la page réelle, dans la console du navigateur :

```js
const t = document.body.innerText
;['NaN','undefined','Infinity','Invalid Date','[object Object]']
  .filter(m => t.includes(m))
```

Doit renvoyer un tableau vide. Puis la même chose sur les **attributs** du
dessin, que le texte ne voit pas :

```js
[...document.querySelectorAll('[stroke-width],[d]')]
  .filter(e => /NaN|Infinity/.test(e.getAttribute('stroke-width') + e.getAttribute('d')))
```

C'est le contrôle le moins cher et le plus révélateur : chacun de ces mots est
une grandeur absente échappée d'un garde. Un NaN dans une largeur de trait ne
s'affiche nulle part — il efface le dessin, et le balayage de texte le manque.

**f. La largeur.**
La page a été réglée à 1 440 px. En dessous de 1 180 px, la grille latérale passe
en colonne ; en dessous de 820 px, le plan du bond se replie. Vérifier à la
largeur d'écran réellement utilisée sur place.

---

### Une fausse piste, pour ne pas la refaire

Le journal du serveur montrait chaque requête **deux fois** par cycle, et le
premier réflexe a été d'y voir un défaut du front. Mesuré ensuite : c'était un
**second client** — le volet Navigateur de l'assistant ouvert en même temps que
le navigateur de l'exploitant. Avant de chercher un doublon dans le code,
compter les abonnés (`len(poller._subscribers[router_id])`) : deux files, deux
onglets. Le seul vrai doublon de code était `/history` rechargé sur chaque
relève de la liste des sessions — corrigé.

## 7. Quand quelque chose ne colle pas

Dans l'ordre, et sans sauter d'étape :

1. **Le relevé brut d'abord.** `GET /api/devices` puis l'endpoint mis en cause.
   Ne jamais conclure depuis l'écran sans avoir lu ce que le routeur a répondu.
2. **Distinguer les trois natures d'échec**, que le code sépare déjà :
   refus explicite (`stat: fail`) = on peut conclure ; erreur de transport =
   panne passagère ; charge utile illisible = défaut de parseur.
3. **Un champ absent n'est pas un défaut.** Avant de « corriger », vérifier que
   le firmware publie bien le champ. Les modèles ne publient pas les mêmes.
4. **Ajouter le cas à la suite de confrontation avant de corriger.** Un test qui
   ne tombe pas quand on casse la chose ne compte pas : le vérifier par mutation.

---

## 8. Avant tout dépôt distant

Un dépôt privé n'est pas une anonymisation. Le jour où il devient public, ou
qu'un collaborateur clone, **tout l'historique part** — y compris les valeurs
qu'un commit ultérieur a corrigées.

Avant le premier `push`, balayer **chaque blob de chaque commit** et chaque
message de commit, par motif : séries `XXXX-XXXX-XXXX` hors forme synthétique,
MAC Peplink `a8:c0:ea:` hors `00:00:`, IMEI/IMSI/ICCID hors gabarits, numéros
de ligne, coordonnées du site, valeurs de `password` / `client_secret`. La
liste des fichiers ignorés (`git ls-files --others --ignored`) doit contenir
`.env`, `routers.yaml`, la base et les journaux.

Le 19 septembre, ce balayage a trouvé dans l'historique la série et les MAC
du MBX dans deux tests, la MAC du HD2 écrite dans un test le matin même, et
dix-neuf coordonnées à cent mètres du site — rien de tout cela dans les
fixtures, qui étaient propres. Corriger les fichiers ne suffit pas : les blobs
passés les portent encore. Le remède est `git filter-repo --replace-text`
avec une table `réel==>synthétique`, après une sauvegarde `git bundle` — puis
le **même balayage** sur l'historique réécrit, puis `git fsck --unreachable`
pour prouver qu'aucun ancien objet ne traîne, puis la **suppression de la
sauvegarde**, qui est devenue la seule copie des valeurs réelles. La table de
remplacement s'efface aussi : elle les contient.

Pour les coordonnées, un **même décalage** appliqué à toutes préserve les
distances — les tests de barycentre et de rayon restent vrais — et déplace le
site où l'on veut.

### Les alertes du bond et la mise sous tension

Les deux règles du bond — un lien hors du bond, plus aucun porteur — ne
parlent que si le bond a porté **depuis la mise sous tension**. Un tunnel qui
tombe est une nouvelle ; un tunnel qui n'a pas tenu depuis le démarrage est un
état, que le bandeau dit en ambre. C'est ce qui rend le MAX HD2 silencieux sur
un réseau où son concentrateur est injoignable, et le MBX loquace dès que son
tunnel a tenu une fois dans la session. La mémoire s'efface avec le contact :
un équipement qui change de réseau repart de « on ne sait pas ».

## 9. Ce qui reste faible, en toute franchise

- **Trois matériels, deux opérateurs, un firmware.** Le MAX HD2 (19 septembre
  2026) a ajouté Bouygues, l'HTTP sans TLS, un type de WAN `modem` et une
  latence Ethernet sous la milliseconde — vraie mesure, vérifiée sur 360 points
  avant de conclure. Les seuils éditoriaux de qualité
  radio (SINR 20/13/5, RSRP −85/−95/−105) sont des choix calés sur une plage
  observée de −75 à −105 dBm. Hors de cette plage, ils n'ont pas été éprouvés.
- **Le modèle tactile est incomplet.** Sur écran tactile, le premier appui sur un
  rail navigue ; seule l'invite a été corrigée, pas le geste.
- **Les zombies pytest — résolus le 19 septembre.** Le mécanisme était connu — le
  fil de travail `aiosqlite` n'est pas un démon, donc une connexion non fermée
  empêche l'interpréteur de sortir. Le déclencheur, reproduit trois fois de
  suite : une **exception entre `connect()` et `close()`**. La trace garde le
  cadre, le cadre garde le dépôt, le dépôt garde la connexion. Sous pytest,
  c'est un test qui *tombe* après un `connect()` nu — et trente-cinq tests de
  stockage ouvrent ainsi. `tests/conftest.py` ferme désormais toute connexion
  laissée ouverte à la fin de chaque test ; un test rouge rend la main. Le même
  piège vaut pour un script de diagnostic : `async with SampleRepository(...)`,
  jamais `connect()` nu.
- **Les seuils d'alerte sont calés sur un véhicule en stationnement.** Ils ne
  transposent pas tels quels.
