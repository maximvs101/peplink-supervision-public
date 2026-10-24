# Superviseur Peplink

Deux outils pour les routeurs Peplink (MAX, MBX, Dome…), qui lisent l'API locale du routeur
depuis un poste du même réseau. Une règle commune : **une grandeur absente n'est jamais un
zéro.**

- **Le superviseur** — la liaison 4G/5G agrégée : niveaux radio, porteuses, débits, tunnel
  SpeedFusion lien par lien, alertes, historique par stationnement, rapport.
- **Le témoin de lien Ethernet** — une seule question : *ce lien filaire est-il stable ?*
  Il suit un WAN Ethernet pendant quelques heures ou quelques jours, date chaque coupure
  avec sa durée et sa cause, et l'exporte en CSV.

Validés sur un MAX HD4 MBX, un Dome Pro Duo et deux MAX HD2, en firmware 8.5.

## Les utiliser

Un fichier par système dans les [Releases](../../releases), rien à installer, aucun droit
d'administrateur :

| | Windows | Mac Intel | Mac Apple silicon |
|---|---|---|---|
| Superviseur | `superviseur-windows-x64.exe` | `superviseur-macos-x64.tar.gz` | `superviseur-macos-arm64.tar.gz` |
| Témoin Ethernet | `temoin-ethernet-windows-x64.exe` | `temoin-ethernet-macos-x64.tar.gz` | `temoin-ethernet-macos-arm64.tar.gz` |

Double-clic : le navigateur s'ouvre, le Peplink est trouvé sur le réseau, une saisie
d'identifiants la première fois. Les fichiers ne sont pas signés : au premier lancement,
Windows demande « Informations complémentaires › Exécuter quand même », macOS un clic droit
› Ouvrir (décompresser d'abord l'archive sur Mac).

Vérifier un téléchargement : la Release porte `SHA256SUMS`. Sur Mac, dans le dossier des
fichiers, `shasum -a 256 -c SHA256SUMS --ignore-missing` ; sous Windows (PowerShell),
`Get-FileHash .\superviseur-windows-x64.exe` donne l'empreinte à comparer à sa ligne.

- Le superviseur écoute sur `http://127.0.0.1:8000`, le témoin sur `:8010` : les deux
  tournent ensemble.
- Les données vivent dans `data/`, **à côté du fichier**, avec une copie de sauvegarde à
  chaque lancement.
- Les identifiants du routeur vont dans le **trousseau du système** (Trousseau macOS,
  Gestionnaire d'identification Windows), jamais dans un fichier. Un refus n'est jamais
  rejoué : cinq refus bloquent l'accès admin du routeur cinq minutes.
- Un client API en lecture seule vaut mieux qu'un compte admin — voir la
  [référence](docs/REFERENCE.md#client-api-en-lecture-seule--recommandé). Un routeur en HTTP
  est accepté, mais les identifiants passent alors en clair sur le LAN.

Variables utiles : `PORT`, `OUVRIR_NAVIGATEUR=0`, et pour le superviseur `HOST=0.0.0.0`
(consulter depuis un téléphone du réseau).

> **Témoin — hypothèse en cours.** Les causes tirées du journal du routeur (câble, test de
> santé, désactivation, redémarrage) reposent sur des formats pas encore observés sur une
> vraie coupure Ethernet. L'état du lien, lui, se lit directement sur le WAN.

## Depuis les sources

Python 3.12, Node 24.

```bash
cd frontend && npm ci && npm run build
cd ../backend && python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python lancer.py          # le superviseur
.venv/bin/python -m temoin          # le témoin
```

Sous Windows, `.venv\Scripts\python.exe`. Sans routeur, `PEPLINK_MODE=mock` lance un
simulateur. `requirements-dev.txt` ajoute pytest, ruff, la couverture et pip-audit à l'exécution
(`requirements.txt`, seul suffisant pour lancer) ; toutes les versions sont épinglées.

## Vérifier

```bash
cd backend && .venv/bin/python -m pytest -q && .venv/bin/ruff check .
cd frontend && npm test && npx tsc --noEmit -p .
```

Aucun réseau n'est requis : les tests rejouent des captures réelles de routeurs, anonymisées.
Les exécutables sont construits par GitHub Actions (`.github/workflows/executable.yml`) sur
chaque système, puis lancés et vérifiés avant d'être livrés.

## Documentation

- [docs/REFERENCE.md](docs/REFERENCE.md) — ce que l'outil lit sur le routeur, l'API,
  l'architecture, la configuration.
- [docs/PROTOCOLE-NOUVEL-EQUIPEMENT.md](docs/PROTOCOLE-NOUVEL-EQUIPEMENT.md) — devant un
  équipement jamais vu.
- [CHANGELOG.md](CHANGELOG.md) — ce que chaque version change.

## Licence

MIT — voir [LICENSE](LICENSE).

## Limites

Quelques châssis, un firmware, deux opérateurs : les seuils radio et d'alerte sont calés sur
ce qui a été observé. La famille `config.*` de l'API n'est jamais lue, parce qu'elle expose
des secrets en clair. L'API locale n'a pas d'authentification : ne pas l'exposer au-delà du
poste ou du LAN.
