# Témoin de lien Ethernet — plan de construction

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** un exécutable unique, sans installation, qui trouve le Peplink du réseau, observe la stabilité d'un WAN Ethernet et liste ses coupures, exportables.

**Architecture:** un nouveau paquet Python `backend/temoin/`, à côté de `backend/app/` dont il réutilise le client du routeur, la découverte, le coffre et les modèles. Toute la logique (coupures, bande, verdict) vit côté serveur dans des fonctions pures ; l'écran est une page statique sans framework qui dessine ce que l'API lui donne. Même chaîne de construction que le superviseur (PyInstaller, action GitHub).

**Tech Stack:** Python 3.12, FastAPI, uvicorn, aiosqlite, httpx, keyring (déjà dans `backend/requirements.txt`) ; HTML/CSS/JS sans dépendance ; PyInstaller.

**Spec:** `docs/superpowers/specs/2026-10-17-lien-ethernet-design.md`

## Global Constraints

- Le serveur n'écoute que sur `127.0.0.1` ; aucun droit d'administrateur, aucune installation.
- **Jamais** un endpoint `config.*` ; les secrets de `status.*` ne sont ni stockés ni exportés.
- **Une** tentative de connexion par demande explicite ; jamais de relance après un refus d'identifiants (cinq échecs verrouillent l'accès admin cinq minutes).
- Exports : séparateur `;`, virgule décimale, BOM UTF-8 ; **ni numéro de série, ni position, ni nom de WAN** — le lien se nomme `WAN <id>`, l'équipement par son modèle.
- Une absence de mesure n'est jamais un zéro ni une coupure : elle se dit « non mesuré ».
- Une désactivation par l'exploitant, un passage en secours, un redémarrage du routeur ne sont pas des coupures.
- Test de santé en échec = coupure, cause « test de santé » ; câble absent = coupure, cause « câble » (décidé par l'exploitant).
- Cadences : `status.wan.connection` toutes les 3 s ; `status.log` et `status.wan.latency` chaque minute ; recul sur injoignable 3, 6, 12, 24, puis 30 s.
- Reprise d'observation : même équipement et même WAN, dernier relevé il y a **moins d'une heure**.
- Base : `data/temoin.db` à côté de l'exécutable, copie de sauvegarde au lancement.
- Écran en français, thème clair et sombre du système, lisible de 1 024 px à un écran large.
- Fichiers livrés : `temoin-ethernet-windows-x64.exe`, `temoin-ethernet-macos-x64.tar.gz`, `temoin-ethernet-macos-arm64.tar.gz`.
- Les règles tirées du **journal du routeur** (causes, désactivation, redémarrage) sont des **hypothèses** tant que la séance de capture n'a pas eu lieu : chaque motif porte le commentaire `# HYPOTHÈSE — à confirmer à la séance de capture (spec §6.7)`, et ses tests sont nommés `test_hypothese_*`.
- L'exploitant commite et pousse lui-même (GitHub Desktop) ; chaque tâche se termine par un message de commit proposé, pas par un `git commit`.

## Review Focus

1. **Le routeur redémarre** pendant l'observation (relevés en échec, puis ancienneté de tous les WAN qui repart de zéro) → le lien n'est pas « coupé » : un segment « redémarrage du routeur ». Test : Tâche 1, `test_un_redemarrage_du_routeur_n_est_pas_une_coupure`.
2. **Le PC se met en veille** une demi-heure → un trou « non mesuré », jamais une coupure, et la disponibilité porte sur le temps mesuré. Test : Tâche 1, `test_un_trou_de_mesure_n_est_ni_coupure_ni_zero`.
3. **Le WAN observé disparaît** du routeur (reconfiguré, renuméroté) → l'outil ne plante pas, le verdict dit « lien introuvable ». Test : Tâche 3, `test_un_wan_disparu_se_dit_sans_planter`.
4. **Une coupure est en cours** au moment de l'export → la ligne porte une fin vide et une durée « en cours », pas une durée fausse. Test : Tâche 5, `test_une_coupure_en_cours_s_exporte_sans_fin`.
5. **Aucune coupure** sur l'observation → les exports restent des fichiers valides (en-tête seul), le verdict dit « Lien stable ». Test : Tâche 5, `test_aucune_coupure_donne_un_fichier_valide`.

---

### Task 1: Les règles — coupures, bande, verdict (fonctions pures)

**Files:**
- Create: `backend/temoin/__init__.py` (vide, sauf `__version__ = "0.1.0"`)
- Create: `backend/temoin/regles.py`
- Test: `backend/tests/temoin/test_regles.py`

**Interfaces:**
- Consumes: `app.models.en_secours(message: object) -> bool`.
- Produces (dans `temoin/regles.py`) :
  - `@dataclass(frozen=True) Releve(ts: datetime, joignable: bool, connecte: bool | None, message: str | None, anciennete_s: int | None, desactive: bool, anciennete_routeur_s: int | None, latence_ms: float | None = None)` — `joignable=False` quand la lecture a échoué (alors `connecte=None`) ; `latence_ms` est la latence propre du lien que publie le routeur (minute), `None` hors mesure.
  - `@dataclass(frozen=True) FaitRouteur(ts: datetime, genre: str, detail: str | None)` — `genre` ∈ `{"coupure", "test de santé", "désactivation", "redémarrage"}`.
  - `@dataclass(frozen=True) Coupure(debut: datetime, fin: datetime | None, genre: str, cause: str | None)` — `genre` ∈ `{"coupure", "micro-coupure"}` ; `duree_s` propriété (`None` si `fin` est `None` ou si micro-coupure).
  - `@dataclass(frozen=True) Segment(debut: datetime, fin: datetime, etat: str)` — `etat` ∈ `{"debout", "coupe", "desactive", "secours", "redemarrage", "non_mesure"}`.
  - `TROU_S = 15.0` (écart au-delà duquel deux relevés ne se suivent pas).
  - `coupures(releves: list[Releve], faits: list[FaitRouteur]) -> list[Coupure]`
  - `bande(releves: list[Releve], fin: datetime) -> list[Segment]`
  - `verdict(releves: list[Releve], maintenant: datetime) -> dict` → `{"etat": "stable"|"coupe"|"desactive"|"injoignable"|"aucune_mesure", "depuis": datetime | None}`
  - `chiffres(segments: list[Segment], coupures: list[Coupure]) -> dict` → `{"coupures": int, "coupe_s": float, "mesure_s": float, "disponibilite": float | None}` (disponibilité = temps debout ou secours / temps mesuré ; `None` si rien de mesuré).

- [ ] **Step 1: Écrire les tests** dans `test_regles.py`, avec une fabrique `r(t_s, connecte=True, anciennete=..., message=None, desactive=False, joignable=True)` partant de `T0 = 2026-10-17T10:00:00Z` et une cadence de 3 s :
  - `test_une_coupure_va_du_premier_releve_tombe_au_premier_revenu` : debout 0–30 s, tombé 33–60 s (`message="No Cable Detected"`), revenu à 63 s → une `Coupure(debut=T0+33s, fin=T0+63s, genre="coupure", cause="câble")`, `duree_s == 30`.
  - `test_la_cause_test_de_sante_vient_du_journal` : même séquence sans message, `FaitRouteur(T0+35s, "test de santé", "WAN failed DNS test")` → `cause == "test de santé"` (rapprochement à ±15 s).
  - `test_une_micro_coupure_se_voit_a_l_anciennete_qui_recule` : debout partout, anciennete 3600 puis 2 à 33 s → `Coupure(genre="micro-coupure", debut=T0+33s, fin=None)`, `duree_s is None`.
  - `test_le_passage_en_secours_n_est_pas_une_micro_coupure` : anciennete qui recule avec `message="Standby"` → aucune coupure ; `bande` contient un segment `"secours"`.
  - `test_une_desactivation_n_est_pas_une_coupure` : `desactive=True` 33–60 s → aucune coupure, segment `"desactive"`.
  - `test_un_trou_de_mesure_n_est_ni_coupure_ni_zero` : relevés 0–30 s puis 1830–1860 s (trou de 30 min) → aucune coupure, segment `"non_mesure"` de T0+30s à T0+1830s, `chiffres(...)["mesure_s"]` exclut le trou, `disponibilite == 1.0`.
  - `test_un_redemarrage_du_routeur_n_est_pas_une_coupure` : `joignable=False` 33–90 s, puis `anciennete_routeur_s=20` à 93 s → aucune coupure, segment `"redemarrage"` de T0+33s à T0+93s.
  - `test_hypothese_journal_desactivation_et_redemarrage` : `FaitRouteur("désactivation")` et `FaitRouteur("redémarrage")` alignent les segments correspondants sur l'heure du journal à ±15 s.
  - `test_le_verdict_dit_depuis_quand` : debout depuis la dernière coupure finie à T0+63s → `{"etat": "stable", "depuis": T0+63s}` ; coupure en cours depuis T0+33s → `{"etat": "coupe", "depuis": T0+33s}` ; dernier relevé non joignable → `"injoignable"` ; liste vide → `"aucune_mesure"`.

- [ ] **Step 2: Lancer** `cd backend && .venv/bin/python -m pytest -q tests/temoin/test_regles.py` → échec à l'import de `temoin.regles`.

- [ ] **Step 3: Implémenter** `temoin/regles.py`. Les causes : message contenant « No Cable » → `"câble"` ; fait `"test de santé"` à ±15 s → `"test de santé"` ; sinon le message du routeur tel quel. Redémarrage : une suite de relevés non joignables suivie d'un relevé dont `anciennete_routeur_s` est inférieure à la durée de la suite. Chaque motif tiré du journal porte le commentaire HYPOTHÈSE des Global Constraints.

- [ ] **Step 4: Lancer** la même commande → tout passe ; puis `.venv/bin/python -m ruff check temoin tests/temoin`.

- [ ] **Step 5: Commit proposé** : `Témoin Ethernet : règles des coupures, de la bande et du verdict`

---

### Task 2: Stockage des observations

**Files:**
- Create: `backend/temoin/stockage.py`
- Test: `backend/tests/temoin/test_stockage.py`

**Interfaces:**
- Consumes: `Releve`, `FaitRouteur` (Task 1).
- Produces : `class Depot` dans `temoin/stockage.py` :
  - `Depot(chemin: str)`, `async connect()`, `async close()` ; WAL comme le superviseur.
  - `async observation_courante(serie: str, wan_id: str, maintenant: datetime) -> int` — reprend l'observation de ce lien si son dernier relevé date de moins de **3 600 s**, sinon en crée une ; rend son `id`.
  - `async nouvelle_observation(serie: str, wan_id: str, maintenant: datetime) -> int`
  - `async ajouter_releve(observation: int, r: Releve)`, `async ajouter_faits(observation: int, faits: list[FaitRouteur])` (un fait déjà vu — même `ts`, `genre`, `detail` — n'est pas dupliqué)
  - `async releves(observation: int, depuis: datetime | None = None) -> list[Releve]`, `async faits(observation: int) -> list[FaitRouteur]`
  - `async observations() -> list[dict]` → `{"id", "modele", "wan_id", "debut", "dernier"}` (jamais la série)
  - `async memoriser_equipement(hote: str, serie: str, modele: str | None)`, `async serie_de(hote: str) -> str | None` — la correspondance adresse → série qui permet de retrouver le trousseau avant toute connexion.

- [ ] **Step 1: Écrire les tests** : `test_une_relance_dans_l_heure_reprend_l_observation` (dernier relevé à T, relance à T+59 min → même id ; à T+61 min → nouvel id) ; `test_nouvelle_observation_clot_la_precedente` ; `test_un_fait_relu_n_est_pas_double` ; `test_les_observations_ne_publient_pas_la_serie` (`"serie" not in observations()[0]`) ; `test_l_adresse_retrouve_la_serie`.
- [ ] **Step 2: Lancer** `pytest -q tests/temoin/test_stockage.py` → échec à l'import.
- [ ] **Step 3: Implémenter** `Depot` (tables `equipements`, `observations`, `releves`, `faits` ; index `(observation, ts)`).
- [ ] **Step 4: Lancer** → passe.
- [ ] **Step 5: Commit proposé** : `Témoin Ethernet : stockage des observations`

---

### Task 3: La collecte

**Files:**
- Create: `backend/temoin/collecte.py`
- Test: `backend/tests/temoin/test_collecte.py`

**Interfaces:**
- Consumes: `app.peplink.router_api.RouterApiPeplinkClient` — `get_radio_status(echeance=None) -> list[WanRadioStatus]` (champs utilisés : `wan_id`, `kind == "ethernet"`, `connected`, `status_message`, `uptime_s`, `disabled`, `path_latency_recent_ms` — la latence de `status.wan.latency`, que le client relit déjà chaque minute), `get_device_info() -> DeviceInfo | None` (ancienneté du châssis), `get_router_events(noms) -> list[RouterEvent]` ; `Depot` (Task 2) ; `Releve`, `FaitRouteur` (Task 1).
- Produces : `class Collecte(client, depot: Depot, observation: int, wan_id: str, *, cadence: float = 3.0, sommeil=asyncio.sleep, horloge=utcnow)` avec `async tour() -> Releve` (une lecture, stockée), `async boucle()` (tours à la cadence, recul 3/6/12/24/30 s quand injoignable, journal chaque minute), `etat_lien: str` (`"ok"` ou `"introuvable"`), `horloge_decalee: bool` (écart routeur/poste > 60 s d'après `DeviceInfo.clock_offset_s` : les faits du journal ne sont alors plus rapprochés des coupures).

- [ ] **Step 1: Écrire les tests** avec un faux client : `test_un_tour_stocke_un_releve_du_wan_choisi` (deux WAN Ethernet, seul `wan_id="1"` est stocké) ; `test_une_lecture_en_echec_est_un_releve_non_joignable` ; `test_le_journal_est_lu_chaque_minute_et_traduit` (faits du routeur → `FaitRouteur`, motifs HYPOTHÈSE) ; `test_un_wan_disparu_se_dit_sans_planter` (le WAN choisi absent de la réponse → `etat_lien == "introuvable"`, aucun relevé faux) ; `test_injoignable_recule` (avec `sommeil` espion : pauses 3, 6, 12, 24, 30) ; `test_la_latence_du_lien_est_gardee` (`path_latency_recent_ms=6.0` → `Releve.latence_ms == 6.0`) ; `test_une_horloge_decalee_coupe_le_rapprochement_du_journal` (`clock_offset_s=-754` → `horloge_decalee`, aucun `FaitRouteur` stocké).
- [ ] **Step 2: Lancer** `pytest -q tests/temoin/test_collecte.py` → échec.
- [ ] **Step 3: Implémenter** `Collecte`. Le journal se lit par `get_router_events` ; la traduction `RouterEvent.kind` → `FaitRouteur.genre` est une table nommée `_FAITS_DU_JOURNAL`, marquée HYPOTHÈSE.
- [ ] **Step 4: Lancer** → passe.
- [ ] **Step 5: Commit proposé** : `Témoin Ethernet : collecte`

---

### Task 4: Trouver le Peplink et s'y connecter

**Files:**
- Create: `backend/temoin/connexion.py`
- Test: `backend/tests/temoin/test_connexion.py`

**Interfaces:**
- Consumes: `app.discovery.probe.discover() -> list[DiscoveredDevice]` (passerelles d'abord, aucun identifiant envoyé) ; `app.coffre` (`lire`, `retenir`, `oublier`, `Identifiants`) ; `RouterApiPeplinkClient(RouterConfig)` et `identify() -> DeviceInfo | None` ; `Depot.memoriser_equipement`, `Depot.serie_de` (Task 2).
- Produces : `async chercher() -> list[dict]` (`{"hote", "firmware", "retenu": bool}`) ; `async connecter(hote: str, ident: Identifiants | None, retenir: bool, depot: Depot) -> tuple[RouterApiPeplinkClient, DeviceInfo]` — `ident=None` lit le trousseau via `serie_de(hote)` ; une seule tentative ; lève `RefusIdentifiants` ou `Injoignable` ; `async liens_ethernet(client) -> list[dict]` (`{"wan_id", "connecte"}`, jamais le nom du WAN).

- [ ] **Step 1: Écrire les tests** : `test_un_seul_peplink_est_preselectionne` ; `test_le_trousseau_evite_la_saisie` (série connue pour l'hôte, identifiants retenus → aucun formulaire) ; `test_un_refus_ne_se_rejoue_pas` (un appel d'`identify` en échec → `RefusIdentifiants`, un seul appel) ; `test_seuls_les_wan_ethernet_sont_proposes` ; `test_les_tests_ne_touchent_jamais_le_vrai_trousseau` (backend `fail` du `conftest.py`, déjà en place).
- [ ] **Step 2: Lancer** → échec.
- [ ] **Step 3: Implémenter.**
- [ ] **Step 4: Lancer** → passe.
- [ ] **Step 5: Commit proposé** : `Témoin Ethernet : découverte et connexion`

---

### Task 5: Exports et données du rapport

**Files:**
- Create: `backend/temoin/export.py`
- Test: `backend/tests/temoin/test_export.py`

**Interfaces:**
- Consumes: `Coupure`, `Releve`, `Segment`, `chiffres` (Task 1).
- Produces : `coupures_csv(entete: dict, coupures: list[Coupure]) -> str` ; `releves_csv(entete: dict, releves: list[Releve]) -> str` ; `entete` = `{"modele", "firmware", "wan", "debut", "fin"}`. Colonnes coupures : `debut;fin;duree_s;type;cause`. Colonnes relevés : `instant;mesure;connecte;message;anciennete_s;latence_ms`. Lignes d'en-tête préfixées `#`.

- [ ] **Step 1: Écrire les tests** : `test_le_csv_s_ouvre_dans_excel_en_francais` (BOM, `;`, `12,5`) ; `test_une_coupure_en_cours_s_exporte_sans_fin` (fin vide, `duree_s` vide, type `coupure (en cours)`) ; `test_aucune_coupure_donne_un_fichier_valide` (en-tête et ligne de colonnes seulement) ; `test_l_export_ne_porte_ni_serie_ni_position_ni_nom_de_wan` (une série, des coordonnées et un nom de WAN fictifs passés en entrée n'apparaissent nulle part dans la sortie ; le lien s'écrit `WAN 1`).
- [ ] **Step 2: Lancer** → échec.
- [ ] **Step 3: Implémenter.**
- [ ] **Step 4: Lancer** → passe.
- [ ] **Step 5: Commit proposé** : `Témoin Ethernet : exports CSV`

---

### Task 6: L'API

**Files:**
- Create: `backend/temoin/api.py`
- Test: `backend/tests/temoin/test_api.py`

**Interfaces:**
- Consumes: Tasks 1 à 5 ; `app.api.security` (garde d'hôte et d'origine du superviseur, réutilisée telle quelle).
- Produces : `creer_app(depot: Depot, *, interface: Path) -> FastAPI` avec :
  - `GET /api/sante` → `{"status": "ok", "version": temoin.__version__, "coffre": coffre.nom_du_trousseau()}`
  - `GET /api/etat` → `{"phase": "recherche"|"identifiants"|"choix_lien"|"mesure", ...}` (équipements trouvés, liens proposés, ou observation en cours)
  - `POST /api/connexion` `{"hote", "username"?, "password"?, "client_id"?, "client_secret"?, "retenir": bool}` → 401 refus / 502 injoignable
  - `POST /api/lien` `{"wan_id"}` → démarre la `Collecte`
  - `GET /api/observation?id=&fenetre=tout|1h|24h` → `{"verdict", "chiffres", "bande", "coupures", "lien", "modele"}`
  - `GET /api/observations`, `POST /api/observations/nouvelle`
  - `GET /api/observations/{id}/coupures.csv`, `GET /api/observations/{id}/releves.csv` (`Content-Disposition: attachment`)
  - fichiers statiques de `interface` à `/`.

- [ ] **Step 1: Écrire les tests** (`httpx.ASGITransport`, faux client) : `test_le_parcours_va_de_la_recherche_a_la_mesure` ; `test_un_refus_rend_401_et_ne_retente_rien` ; `test_l_observation_rend_verdict_bande_et_coupures` ; `test_les_csv_se_telechargent` ; `test_un_hote_rebascule_est_refuse` (garde d'hôte du superviseur, comme `test_rebinding.py`).
- [ ] **Step 2: Lancer** → échec.
- [ ] **Step 3: Implémenter.** La boucle de collecte vit dans une tâche annulée à l'arrêt de l'application.
- [ ] **Step 4: Lancer** → passe ; `ruff check temoin tests/temoin`.
- [ ] **Step 5: Commit proposé** : `Témoin Ethernet : API`

---

### Task 7: L'écran et le rapport

**Files:**
- Create: `backend/temoin/ecran/index.html`, `backend/temoin/ecran/temoin.js`, `backend/temoin/ecran/temoin.css`, `backend/temoin/ecran/rapport.html`
- Test: `backend/tests/temoin/test_ecran.py`

**Interfaces:**
- Consumes: l'API de la Task 6, et rien d'autre (aucune dépendance npm, aucune construction).
- Produces : une page unique qui suit `GET /api/etat` (relue toutes les 3 s pendant la mesure, jamais onglet caché), et `rapport.html?id=` imprimable.

- [ ] **Step 1: Écrire les tests** (servis par `creer_app`) : `test_la_page_et_ses_fichiers_sont_servis` ; `test_l_ecran_ne_charge_rien_de_l_exterieur` (aucune URL `http` dans les trois fichiers) ; `test_les_libelles_sont_en_francais` (présence de « Lien stable », « Exporter », « Nouvelle observation », « non mesuré »).
- [ ] **Step 2: Lancer** → échec.
- [ ] **Step 3: Implémenter** dans la famille visuelle du superviseur (mêmes jetons de couleur que `frontend/src/index.css`, thèmes clair et sombre) :
  - en tête : verdict en gros (vert / rouge / gris), « depuis HH:MM », trois chiffres (coupures, durée coupée, disponibilité « sur Xh mesurées ») ;
  - bande SVG : vert debout et secours, rouge coupé, gris désactivé, hachuré non mesuré et redémarrage ; survol (heure, durée, cause) ; clic → ligne éclairée dans la liste ; fenêtres « Tout · 1 h · 24 h » ;
  - liste des coupures, la plus récente en haut, « en cours » quand `fin` est nulle ;
  - boutons « Exporter les coupures », « Exporter les relevés », « Rapport », « Nouvelle observation » ;
  - un sélecteur des observations passées (`GET /api/observations`) pour en rouvrir une, relue sans mise à jour ;
  - une ligne « Horloges du routeur et du poste en désaccord » quand `horloge_decalee` ;
  - parcours de démarrage : recherche → (formulaire, avec l'avertissement « en clair » sur un hôte `http://`) → choix du lien → mesure ;
  - nombres en virgule décimale (`toLocaleString('fr-FR')`).
- [ ] **Step 4: Lancer** → passe ; puis vérifier à l'écran : `.venv/bin/python -m temoin` contre le HD2 du poste, capture d'écran de la mesure.
- [ ] **Step 5: Commit proposé** : `Témoin Ethernet : écran et rapport`

---

### Task 8: L'exécutable, sa vérification et sa livraison

**Files:**
- Create: `backend/temoin/__main__.py`
- Modify: `backend/tools/construire_executable.py`, `backend/tools/verifier_executable.py`, `.github/workflows/executable.yml`, `README.md`
- Test: `backend/tests/temoin/test_lancement.py`, `backend/tests/test_ci.py`

**Interfaces:**
- Consumes: `creer_app` (Task 6) ; dans `backend/lancer.py` : `port_disponible(hote, port) -> bool`, `ouvrir_quand_pret(adresse, delai_s=15.0)`, `sauvegarder(base: Path) -> Path | None`.
- Produces : `python -m temoin` (et l'exécutable) qui sert sur `127.0.0.1`, port par défaut **8010** (le superviseur garde 8000 : les deux peuvent tourner ensemble), base `data/temoin.db` à côté de l'exécutable ; `construire_executable.py --produit temoin` → `dist/temoin-ethernet[.exe]` ; `verifier_executable.py dist/temoin-ethernet --sante /api/sante`.

- [ ] **Step 1: Écrire les tests** : `test_le_temoin_n_ecoute_que_sur_le_poste` ; `test_la_base_vit_a_cote_de_l_executable` (chemins comme `test_lancer.py`) ; dans `test_ci.py`, `test_l_action_construit_aussi_le_temoin` (les trois noms de fichiers livrés figurent dans `executable.yml`).
- [ ] **Step 2: Lancer** → échec.
- [ ] **Step 3: Implémenter** : `__main__.py` ; option `--produit {superviseur,temoin}` dans le script de construction (le témoin embarque `temoin/ecran` et `--collect-all temoin`, `--collect-submodules keyring`, `--copy-metadata keyring`) ; option `--sante` du vérificateur ; deux étapes de plus par système dans l'action (construire, vérifier le témoin) et ses trois fichiers dans la Release ; une section « Témoin de lien Ethernet » dans le README.
- [ ] **Step 4: Lancer** la suite entière (`pytest -q`, `ruff check .`), puis construire et vérifier l'exécutable sur le Mac : `PYTHONUTF8=1 .venv/bin/python tools/construire_executable.py --produit temoin && .venv/bin/python tools/verifier_executable.py dist/temoin-ethernet --sante /api/sante` → « prêt en … · trousseau : macOS Keyring ».
- [ ] **Step 5: Commit proposé** : `Témoin Ethernet : exécutable, vérification et livraison`

---

## Après le plan

- **Reporté, choix du plan** : combler un trou de moins d'une heure par l'historique de latence du routeur (spec §6.4, dernière phrase). La première version dit le trou « non mesuré » sans le combler ; le superviseur sait déjà lire cette série (`parse_latency_series`), à brancher en seconde version.

- La **séance de capture** (spec §6.7) confirme ou corrige les motifs marqués HYPOTHÈSE ; les captures, anonymisées, deviennent des fixtures, et les tests `test_hypothese_*` sont réécrits contre elles.
- La première version livrée suit la règle du dépôt : section de CHANGELOG, numéro de version, étiquette.
