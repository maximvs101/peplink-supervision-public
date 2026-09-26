# Superviseur Peplink

Supervision radio 4G/5G d'une liaison agrégée Peplink (MAX, MBX, Dome…), lue sur l'API
locale du routeur, à chaque cycle — trois secondes de pause entre deux, par défaut. Un écran qui dit ce que vaut la liaison —
niveaux radio, porteuses, débits, tunnel SpeedFusion lien par lien, historique par
stationnement — et une règle : **une grandeur absente n'est jamais un zéro.**

Validé sur trois châssis réels — MAX HD4 MBX, Dome Pro Duo, MAX HD2, tous en 8.5.4 — et
rejoué de bout en bout sur leurs captures à chaque test.

## L'emporter sur le terrain

Un seul fichier, rien à installer, aucun droit d'administrateur : le serveur, ses
dépendances, Python et l'interface. Double-clic, le navigateur s'ouvre sur le tableau de
bord ; la base de relevés se range dans `data/` **à côté du fichier** — sur une clé USB, elle
voyage avec la clé — avec une copie de sauvegarde à chaque lancement.

Les fichiers sont dans les [Releases](../../releases) : `superviseur-windows-x64.exe`,
`superviseur-macos-x64.tar.gz` (Intel), `superviseur-macos-arm64.tar.gz` (Apple silicon) —
sur Mac, une archive à décompresser d'un double-clic, parce qu'un fichier nu perdrait son
droit d'exécution en route. Non signés : au premier lancement, Windows demande
« Informations complémentaires › Exécuter quand même », macOS un clic droit › Ouvrir.

Le serveur n'écoute que sur `http://127.0.0.1:8000`. Variables utiles : `HOST=0.0.0.0`
pour consulter depuis un téléphone du même réseau, `PORT=…`, `OUVRIR_NAVIGATEUR=0`.

Chaque fichier est construit par GitHub sur la machine de son système, après les deux suites
de tests, puis **lancé et vérifié** avant d'être livré (`.github/workflows/executable.yml` —
à la demande, ou une Release à chaque étiquette `v*`, dont le texte est la section de
`CHANGELOG.md` qui porte l'étiquette). Pour le construire soi-même :
`backend/tools/construire_executable.py`.

## Mettre un routeur en supervision

1. Ouvrir l'écran de découverte (`/#/decouverte`) : les Peplink du réseau sont reconnus par
   leur préfixe MAC et leur certificat.
2. « Mettre en supervision », saisir les identifiants admin — ou, mieux, un client API en
   lecture seule (voir la [référence](docs/REFERENCE.md#client-api-en-lecture-seule--recommandé)).
3. C'est tout. Les identifiants ne sont **jamais écrits sur le disque** : ils vivent en
   mémoire et se ressaisissent à chaque lancement.

Un routeur qui ne parle qu'HTTP est accepté, et l'écran le dit : le mot de passe passe alors
en clair sur le LAN. Activer HTTPS dans l'admin du routeur reste préférable.

## Depuis le dépôt

Node 24 (le `package-lock.json` est écrit par son npm 11), Python 3.12.

```bash
cd frontend && npm ci && npm run build          # l'interface, servie par le serveur
cd ../backend && python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python lancer.py                      # même comportement que l'exécutable
```

Sous Windows, `.venv\Scripts\python.exe` à la place de `.venv/bin/python`. Pour développer
l'interface avec rechargement à chaud : `npm run dev` dans `frontend/` (proxy vers le
serveur, rien d'autre à configurer). Sans routeur, `PEPLINK_MODE=mock` lance un
simulateur.

## Vérifier

```bash
cd backend && .venv/bin/python -m pytest -q     # aucun réseau requis
cd backend && .venv/bin/ruff check .
cd frontend && npm test
cd frontend && npx tsc --noEmit -p .
```

Trois familles de tests valent d'être nommées. Les **captures réelles**
(`backend/tests/fixtures/`, anonymisées motif par motif) traversent le client, le
collecteur, la base et les routes réels ; l'écran monte son tableau de bord sur la réponse
réelle des routes (`frontend/src/test/reel/`). La **confrontation** pousse des profils
d'équipements jamais rencontrés — sans modem, une seule SIM, Wi-Fi, 5G SA, nombres
malformés. Et chaque garde est **testé par mutation** : réintroduire le défaut fait tomber
son test.

## L'API

Toutes sous `/api`, sauf la WebSocket. Les routes d'écriture, et celles qui identifient la
flotte (◆), n'acceptent que le poste local — et jamais une requête venue d'une autre page.

| Route | Rôle |
|---|---|
| `GET /health` · `GET /routers` · `GET /routers/{id}` | État, flotte, dernier relevé par lien |
| `WS /ws/routers/{id}` (hors `/api`) | Flux des relevés, à chaque cycle |
| `GET /routers/{id}/sessions` · `GET /sessions/{id}/summary` · `POST /sessions/{id}/label` ◆ | Stationnements, fiche, nom |
| `GET /sessions/{id}/export.csv` · `?complet=true` ◆ · `/export-routeur.csv` | Relevés « à partager », complets, heure du routeur |
| `GET /routers/{id}/history` · `/timeline` · `/events` | Série d'une grandeur, bande d'état, journal |
| `GET /discover` ◆ · `GET /devices` · `GET /devices/known` ◆ · `POST /devices/pair` ◆ · `POST /devices/{id}/reauth` ◆ · `DELETE /devices/{id}` ◆ | Découverte et appairage |
| `GET /devices/{id}/capture/{endpoint}` ◆ | Capture d'un endpoint, pour une fixture |

## Aller plus loin

- [docs/PROTOCOLE-NOUVEL-EQUIPEMENT.md](docs/PROTOCOLE-NOUVEL-EQUIPEMENT.md) — la marche
  à suivre devant un équipement jamais vu, de la découverte à la capture rejouée, et ce qui
  reste faible.
- [docs/REFERENCE.md](docs/REFERENCE.md) — la référence technique : ce que l'application lit
  sur l'équipement, l'identité de cellule, pourquoi on n'identifie pas l'antenne, le tunnel,
  l'architecture, la configuration par fichier.

## Ce qui reste faible

Trois châssis, un firmware, deux opérateurs. Les seuils radio sont calés sur une plage
observée de −75 à −105 dBm ; les seuils d'alerte, sur un véhicule en stationnement. La
famille `config.*` de l'API n'est jamais lue — elle expose des secrets en clair — donc les
adresses des LAN ne sont pas connues, seulement leurs noms. Pas d'authentification sur
l'API : ne pas l'exposer au-delà du poste ou du LAN.
