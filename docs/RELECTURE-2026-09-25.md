# Relecture complète du 25 septembre 2026 — suivi

Sept relecteurs en parallèle, un par axe, en lecture seule — collecteur et client routeur, stockage et API,
sécurité et confidentialité, logique de l'écran, composants de l'écran, tests, exploitation et documentation —
puis un vérificateur qui a dédoublonné, tenté de réfuter chaque constat haut ou moyen à la source, et classé.
Aucun routeur ni le serveur en service n'ont été contactés ; la base n'a été lue qu'en lecture seule ; expériences
et mutations faites sur des copies.

**49 constats retenus** (5 hauts, 23 moyens, 21 bas), 9 écartés (doublons fusionnés). État au moment de la relecture : 697 tests backend et 507 frontend verts, tsc propre.

## Synthèse du vérificateur

J'ai vérifié 64 constats bruts et en retiens 49 après fusion des doublons. Aucun défaut de gravité haute ou moyenne n'a été réfuté à la source. J'ai relancé sans réseau les expériences du collecteur, de la logique d'écran, de la sécurité et du stockage (base synthétique ou copie), avec les mêmes résultats. Quatre gravités sont revues à la baisse : l'îlot de latence, la classe « active », les copies élaguées et les trous de tests. Deux passent de moyen à bas : le coût du journal et la base corrompue.

Les défauts les plus graves viennent de protections qui se retournent contre l'exploitant. La mise hors périmètre retient pour toujours l'endpoint principal, et un tunnel à terre finit affiché « Liaison OK », faute de débit mesuré. Le budget de 20 s jette des relevés WAN déjà lus. Deux autres défauts graves sortent de ce schéma : un DNS rebinding contourne la garde loopback, et un .env relatif envoie la base dans le dossier temporaire de l'exécutable.

Trois faiblesses reviennent d'un axe à l'autre :
- Des états mémorisés ne sont jamais remis à None : tunnel_uptime_s, lan_clients, la dernière latence non nulle, le message de reprise.
- Des absences sont converties en zéro ou en affirmation : un débit de 0 transforme le bond en « routes », « 0 / N liens qui portent », une panne affichée pour un lien désactivé, un échec de lecture imprimé comme une absence de mesure.
- Des taux sont publiés sans leur propre dénominateur.

Les règles les plus sensibles, le refus d'identifiants et config.*, ne sont presque pas gardées par les tests : les mutations survivent. Des données réelles (une série, une position) sont versionnées dans des tests.

Côté forces : 697 tests backend et 507 tests frontend passent, tsc est propre, les commentaires sont précis, redact est appliqué avec rigueur et la migration est additive. Le dépôt n'a pas été modifié et aucun routeur ni le port 8000 n'ont été contactés.

## Ordre proposé pour la reprise

1. Vie privée d'abord : C-14 (série et position réelles dans des tests) et C-45 (série dans l'historique poussé — réécriture côté Max).
2. Les cinq hauts, en commençant par C-1, C-2 et C-3.
3. Les moyens dans l'ordre du tableau, un test écrit d'abord pour chacun.

## Haut

- [x] **C-1** — Deux 401 de suite autour d'un login réussi condamnent status.wan.connection à vie : l'équipement devient « injoignable » et n'est plus jamais interrogé
  - Où : `backend/app/peplink/router_api.py:1010` · axes : Collecteur et client routeur
  - Constat : Dans _get, un refus qui survit à une ré-authentification est ajouté à _hors_perimetre, quel que soit l'endpoint, y compris l'endpoint principal. Dès lors, _get lève EndpointRefusedError sans rien envoyer (l.970). Le poller range cette erreur dans son except générique et affiche « injoignable » (poller.py:1136-1141). Seul allow_new_credentials() vide l'ensemble (l.866), et son unique déclencheur à l'écran, le bouton « Réessayer la connexion », n'apparaît que pour le statut « identifiants refusés ». Même mécanisme pour status.system.info : la fiche n'est plus relue et le journal s'éteint.
  - Scénario : La session de 48 h expire. Le relogin réussit, mais la relance prend encore un 401, par exemple parce que le serveur web du routeur redémarre. Le routeur n'est ensuite plus jamais interrogé, et l'écran le dit « injoignable » alors qu'il fonctionne, jusqu'à un ré-appairage ou un redémarrage du serveur.
  - Vérification : J'ai relu router_api.py l.960-1011 et 841-866, ainsi que poller.py l.1084-1141. J'ai relancé collecteur/exp1.py (MockTransport, sans réseau). Au cycle 2 : 401, login, 401. Aux cycles 3 à 5, « hors du périmètre » sans aucune requête (wan = 3 requêtes au total), avec _hors_perimetre = {'status.wan.connection'}.
  - Correctif proposé : Ne jamais retenir hors périmètre status.wan.connection ni status.system.info. N'y ranger un endpoint qu'après des refus sur des cycles distincts, ou vider _hors_perimetre à chaque authentification réussie.

- [x] **C-2** — Le rôle du bond passe à « routes » quand le tunnel tombe ou que la bande manque, et le bandeau repasse au vert « Liaison OK »
  - Où : `frontend/src/lib/redundancy.ts:93` · axes : Logique de l'écran
  - Constat : paquetsParSecondeRecents rend 0 quand aucune tranche n'est renseignée, et aussi quand la timeline vaut null (l.93 et l.106 : « Sans une seule tranche renseignée, zéro »). Le débit du tunnel s'éteint avec le tunnel lui-même, alors que le backend garde les routes annoncées. bondRole(routes, 0) rend alors 'routes' (bondRole.ts:61), puis verdict.ts:654 pose porteLeFlux = false et saute la branche « Tunnel SpeedFusion tombé ». Une absence de mesure devient « rien ne transite ».
  - Scénario : Un pair annonce 10.1.0.0/16 et porte 120 paquets/s. Le concentrateur tombe (PEER_DOWN). Pendant environ 10 minutes, le bandeau dit « Tunnel SpeedFusion tombé ». Puis, dès que la dernière tranche portante sort de la fenêtre de 600 s, il affiche « Liaison OK » en vert, tunnel toujours à terre. Même effet au chargement de la page, ou quand fetchTimeline échoue (timeline null).
  - Vérification : J'ai relu redundancy.ts l.85-107, bondRole.ts l.40-62 et verdict.ts l.645-680. J'ai relancé ecran-logique/exp1.js, un bundle des vrais modules. À 11 tranches portées : « rôle porte | degrade Tunnel SpeedFusion tombé ». À 10 : « pps 0.0 | rôle routes | ok Liaison OK ». Timeline null : « rôle routes | ok Liaison OK ».
  - Correctif proposé : Rendre null (inconnu) plutôt que 0 quand aucune tranche n'est observée, et le traiter comme 'inconnu' dans bondRole. Tant que les liens sont PEER_DOWN, garder le dernier rôle établi tunnel debout.

- [x] **C-3** — Aucun contrôle de l'en-tête Host : un DNS rebinding franchit la garde loopback et le contrôle d'origine de la WebSocket
  - Où : `backend/app/main.py:111` · axes : Sécurité et confidentialité
  - Constat : require_loopback ne juge que l'adresse TCP (security.py l.30-63). Or un navigateur local se présente toujours en 127.0.0.1. ws.py (l.43) tient pour « même origine » toute origine égale à l'en-tête Host, que l'attaquant choisit. Aucun TrustedHostMiddleware n'est en place : main.py n'ajoute que CORS et GZip. Une page dont le domaine est rebasculé vers 127.0.0.1 devient de même origine que l'API. Elle lit alors toutes les routes, y compris la capture brute (identifiants APN, IMEI, ICCID), et s'abonne à la WebSocket (position, noms de WAN).
  - Scénario : L'exploitant ouvre une page piégée pendant que le superviseur tourne. Le domaine de la page (port 8000) est ensuite résolu vers 127.0.0.1. La page lit /api/devices, puis /api/devices/<id>/capture/status.wan.connection, et récupère les secrets bruts de l'équipement.
  - Vérification : J'ai relancé securite/exp_rebind.py (TestClient, sans serveur réel). La capture avec Host tiers rend 200 et contient le mot de passe APN fictif. /api/devices rend 200. La WebSocket est acceptée avec Origin = Host tiers. grep « TrustedHost|allowed_hosts|Sec-Fetch » dans app/ ne trouve rien.
  - Correctif proposé : Ajouter TrustedHostMiddleware (127.0.0.1, localhost, [::1], plus l'adresse LAN si HOST=0.0.0.0). Dans ws.py, comparer l'origine à cette liste plutôt qu'à Host.

- [x] **C-4** — DATABASE_PATH relatif venu d'un .env : base écrite dans le dossier temporaire de l'exécutable, et sauvegarde silencieusement sautée
  - Où : `backend/lancer.py:183` · axes : Exploitation, lanceur et documentation
  - Constat : preparer() applique d'abord le .env (setdefault), puis ses propres chemins absolus, aussi en setdefault. Un DATABASE_PATH ou un ROUTERS_CONFIG relatif venu du .env l'emporte donc. L'application le résout ensuite depuis BASE_DIR, le parent de app/ (config.py l.12-14 et 80-83). Une fois gelé, c'est _MEIPASS, que l'amorce onefile efface à la sortie. De son côté, sauvegarder() résout le même chemin relatif depuis le répertoire courant. Elle ne trouve rien et ne copie rien, sans rien signaler. C'est aussi le cas depuis le dépôt quand on lance hors de backend/.
  - Scénario : L'exploitant pose à côté de l'exécutable le .env du dépôt ou .env.example, qui contiennent tous deux un DATABASE_PATH relatif. Il supervise une journée puis relance : l'historique a disparu, aucune sauvegarde n'a été faite, et son routers.yaml n'a pas été lu. Autre cas : backend/.venv/bin/python backend/lancer.py lancé depuis la racine. La base est bien écrite, mais aucune copie n'est faite.
  - Vérification : J'ai relu lancer.py l.160-200 et config.py l.12-14 et 76-83. backend/.env et .env.example portent bien un DATABASE_PATH (valeur non recopiée). Les simulations gel/sim.py et depot/ de l'axe exploitation concordent avec cette lecture. Je ne les ai pas relancées.
  - Correctif proposé : Dans preparer(), résoudre DATABASE_PATH et ROUTERS_CONFIG relatifs depuis le dossier du .env, et réécrire os.environ en absolu avant sauvegarder().

- [x] **C-5** — Le budget de 20 s par équipement couvre aussi les lectures complémentaires : deux compléments lents font jeter un relevé WAN déjà lu
  - Où : `backend/app/poller.py:1085` · axes : Collecteur et client routeur
  - Constat : asyncio.timeout(max(20, 4 × intervalle)) (l.300 et 1085) enveloppe tout get_radio_status, qui enchaîne en série WAN, traffic, latence, pepvpn, température, clients et qualité (router_api.py l.1045-1057). Chaque requête a un délai httpx de 15 s (l.769). Les compléments « ne doivent jamais détruire les états WAN déjà lus ». Pourtant, un dépassement cumulé annule tout : rien n'est publié, et l'équipement est dit « pas de réponse en 20 s ».
  - Scénario : status.wan.connection répond en 0,4 s. Puis status.traffic expire à 15 s, et status.wan.latency met 6 s. Le cycle entier est perdu et l'équipement est déclaré injoignable, cycle après cycle, tant que le routeur reste chargé.
  - Vérification : J'ai relu poller.py l.300 et 1084-1092, ainsi que router_api.py l.769 et 1045-1057. J'ai relancé collecteur/exp5.py (Poller réel, budget à 2 s, base jetable dans le brouillon) : « statut: injoignable | pas de réponse en 2 s | latest: None ».
  - Correctif proposé : Donner aux compléments un délai propre et court, vérifier le budget restant avant chacun, ou publier les états WAN avant les compléments.

## Moyen

- [x] **C-6** — Le refus du seul infoType=profile éteint toute la supervision du tunnel pour la vie du processus
  - Où : `backend/app/peplink/router_api.py:970` · axes : Collecteur et client routeur
  - Constat : _hors_perimetre est indexé par le nom d'endpoint sans ses paramètres. Or status.pepvpn sert trois lectures : peer, profile et tunnel. Un refus sur profile condamne donc les lectures de tunnel et de pairs. _disable_tunnel est appelé et _supports_tunnel passe à False. Les relevés portent alors tunnel=None, lu comme « inconnu », et aucune alerte de bond n'est possible. La route de capture partage ce _get : une capture de status.pepvpn.profile suffit à condamner la sonde.
  - Scénario : Un client API dont le périmètre refuse profile mais accepte peer et tunnel : le bond n'est plus supervisé, et la chute du concentrateur passe inaperçue.
  - Vérification : J'ai relancé collecteur/exp6.py : cycle 0 « hors: {'status.pepvpn'} », puis « supports_tunnel: False » aux cycles 1 et 2. J'ai relu router_api.py l.970 et 1010.
  - Correctif proposé : Indexer _hors_perimetre par (endpoint, infoType), ou exclure status.pepvpn de ce mécanisme.

- [x] **C-7** — « Monté depuis 2 h » reste affiché dans la carte alors que le bond est tombé
  - Où : `backend/app/peplink/router_api.py:1211` · axes : Collecteur et client routeur
  - Constat : tunnel_uptime_s n'est écrit que sur une lecture de tunnel réussie (l.1256), et n'est jamais remis à None. Ni la branche sans pair connecté (sentinelle PEER_DOWN, l.1200-1211), ni les erreurs ne le remettent à zéro. Le poller le recopie à chaque cycle (poller.py:1151). BondPlan affiche « Monté depuis » dès qu'un objet tunnel existe, et PEER_DOWN en est un.
  - Scénario : Le bond tient depuis 2 h 03, puis le concentrateur devient injoignable. La même carte dit alors « Tunnel tombé » et « Monté depuis 2 h 03 », et ce chiffre reste figé.
  - Vérification : J'ai relancé collecteur/exp3.py : « bond debout : ACTIVE tunnel_uptime_s = 7380 », puis « bond tombé : PEER_DOWN tunnel_uptime_s = 7380 ». grep tunnel_uptime_s ne trouve que les l.760 et 1256 et poller.py:1151.
  - Correctif proposé : Remettre tunnel_uptime_s = None dans chaque branche de _attach_tunnel qui ne lit pas de tunnel connecté.

- [x] **C-8** — Un épisode « latence haute » enjambe un trou de supervision et invente des heures de latence haute
  - Où : `backend/app/storage/db.py:57` · axes : Stockage, sessions et API
  - Constat : Dans _SQL_ILOTS, l'îlot est calculé par une différence de ROW_NUMBER, partitionnée par WAN puis par (WAN, haut), sans segment de trou. La garde _TROU_MAX_S ne s'applique qu'à la CTE bascules. Deux relevés hauts consécutifs forment donc un même îlot, quel que soit l'écart qui les sépare. Et _marquant classe aussi par durée.
  - Scénario : Un pic juste avant l'arrêt du superviseur, puis un autre au premier relevé de la reprise, 3 h plus tard. Le journal publie un épisode de 3 h fait de 2 relevés, classé en tête.
  - Vérification : J'ai relu db.py l.40-60. J'ai relancé stockage/synth.py (base synthétique) : « EV latence haute 10:00:50 → 13:00:00, 2 relevés, 1600.0 ». Gravité ramenée de haut à moyen : il faut un relevé haut de part et d'autre du trou.
  - Correctif proposé : Segmenter à chaque trou (SUM(écart > _TROU_MAX_S) OVER …) et partitionner les deux ROW_NUMBER par (wan_id, segment).

- [x] **C-9** — La latence du lien est effacée de l'historique et de la fiche après la toute première série du routeur
  - Où : `backend/app/storage/db.py:907` · axes : Stockage, sessions et API
  - Constat : history(path_latency_ms) n'utilise les relevés que si ts < MIN(ts) de router_history, un minimum pris sur toute la vie de l'équipement. latency_series et _resume_lien (sessions.py:393) suivent la même règle. Or la latence du relevé peut exister sans série, par exemple quand la série est écartée pour cause d'horloge décalée ou d'écriture ratée.
  - Scénario : Une série existe le 2 septembre. Le 5 septembre, une session sans série compte 30 relevés porteurs de latence. L'historique rend 0 point, et la fiche publie path_latency_samples=0 avec un p50 null. Une absence est ainsi affichée là où 30 mesures sont en base.
  - Vérification : J'ai relu db.py l.900-918. synth.py : « HIST day B path_latency: 0 points », « samples with path_latency in day B rows: 30 », « fiche path_latency_samples 0 None ».
  - Correctif proposé : N'écarter un relevé que si la série couvre effectivement son instant (point de série à ±10 s).

- [x] **C-10** — La latence du lien peut être une mesure vieille d'une heure, datée de l'instant de lecture
  - Où : `backend/app/peplink/router_api.py:2374` · axes : Collecteur et client routeur
  - Constat : parse_wan_latency remonte la série jusqu'au premier point renseigné, aussi loin soit-il. Le commentaire de la l.2371 promet pourtant l'inverse. Le poller date ensuite cette valeur de l'heure de lecture (poller.py:1399-1401) et l'écrit en base. WanCard l'affiche « il y a 3 s ».
  - Scénario : Un lien vient de se rétablir, avec 359 points non mesurés en fin de série. L'écran affiche « 42 ms · il y a 3 s » pour une mesure antérieure à la coupure.
  - Vérification : J'ai relu router_api.py l.2363-2379. collecteur/exp2.py : [42.0] + 359 × None donne {'1': 42.0}. Réserve : je n'ai pas vérifié que le firmware publie des null pendant un trou, et samples.db n'en contient aucun.
  - Correctif proposé : Ne retenir que le dernier point, ou un point récent daté par pointEnd, sinon None.

- [x] **C-11** — Calage sur la grille de 10 s : l'arrondi bancaire de round() fusionne un point sur deux quand la série commence à …5 s
  - Où : `backend/app/peplink/router_api.py:2430` · axes : Collecteur et client routeur
  - Constat : _sur_la_grille fait round(instant/pas)*pas, et round arrondit les demies au pair. Avec un début de série en …5, les points 15 et 25 tombent sur 20, 35 et 45 sur 40, etc. L'upsert de router_history ne garde que le dernier. La moitié des points de latence et de radio est perdue, et le reste décalé de 5 s. Or le commentaire constate que le début glisse d'une seconde d'une lecture à l'autre.
  - Scénario : Une lecture dont pointEnd finit en 5 : 360 points donnent 180 instants, et l'historique à 10 s prend des trous de 20 s.
  - Vérification : J'ai relancé collecteur/exp7.py : « points: 360 instants distincts: 180 ». J'ai relu l.2415-2430. Le cas n'a pas été observé sur les données réelles.
  - Correctif proposé : floor(instant/pas + 0.5)*pas, ou caler une fois sur pointStart puis ajouter i × pas.

- [x] **C-12** — Parts et taux publiés sans dénominateur visible : diversité, latence du bond, sauts de cellule, et un pied de rapport qui promet le contraire
  - Où : `backend/app/sessions.py:257` · axes : Stockage, sessions et API, Composants et expérience de l'écran
  - Constat : part() calcule chaque pourcentage de diversité sur ses propres cycles jugeables. Pourtant PathDiversity ne publie qu'un seul dénominateur, cycles, celui des cycles complets, et SessionSheet ne l'affiche même pas (l.209-225). L'écran écrit pourtant « chaque part a son propre dénominateur ». Latence du bond et latence du lien : aucun compte, ou un compte en title seulement. Sauts de cellule par heure : compte en title seulement. Un title ne s'imprime pas, alors que le pied du Rapport affirme que « les taux voyagent avec leur nombre de mesures ».
  - Scénario : Sur une session réelle, same_site_pct vaut 100 % alors que cycles=5912 est publié, et seuls 1999 cycles étaient jugeables pour la station. Le PDF remis à un tiers dit « même station 100 % du temps » sans base.
  - Vérification : J'ai relu sessions.py l.220-263, SessionSheet.tsx l.200-228 et Rapport.tsx l.134-137. Les chiffres viennent de stockage/div.py et fiches.py, que je n'ai pas relancés. Le code confirme le dénominateur unique.
  - Correctif proposé : Publier site_cycles, cell_cycles et earfcn_cycles, et rendre les comptes en texte visible, au moins dans le rapport.

- [x] **C-13** — CSRF sur POST /devices/{id}/reauth : une page tierce réarme une tentative de connexion à chaque requête
  - Où : `backend/app/api/routes.py:945` · axes : Sécurité et confidentialité
  - Constat : La route n'a pas de corps : un formulaire POST cross-site est une requête simple, sans requête préalable CORS. require_loopback laisse passer, puisque le navigateur est local. L'appel remet auth_blocked à faux et appelle allow_new_credentials(), et le cycle suivant retente la connexion avec les identifiants en mémoire. C'est un retentement automatique déclenché par un tiers. Les simples balises img déclenchent de la même façon GET /capture et GET /discover.
  - Scénario : Un équipement en « identifiants refusés » porte un identifiant lisible, par exemple dome-01. Une page ouverte chez l'exploitant poste cinq formulaires, un par cycle. Le routeur compte cinq échecs et suspend l'accès admin 5 min.
  - Vérification : exp_rebind.py relancé : POST form-urlencoded avec Origin tiers et corps vide → 200, allow_new_credentials() appelé, auth_blocked = False. Le blocage que Chrome prévoit pour l'accès au réseau local n'est pas pris en compte.
  - Correctif proposé : Refuser sur les routes require_loopback toute requête dont l'Origin n'est pas listée, ou dont Sec-Fetch-Site vaut cross-site, ou exiger un en-tête personnalisé.

- [x] **C-14** — Un vrai numéro de série d'équipement appairé est versionné dans Discovery.test.tsx, et une position réelle de site dans deux tests backend
  - Où : `frontend/src/pages/Discovery.test.tsx:26` · axes : Sécurité et confidentialité, Tests et garde-fous
  - Constat : La série réelle d'un équipement de la table devices figure 6 fois dans Discovery.test.tsx, présente dans 2 commits. Des coordonnées de test_poller_hotswap.py (l.1340 et 1344) coïncident au 1e-4 près avec des positions mesurées dans samples.db, et test_sessions.py s'en approche à ~5e-4. Le garde test_fixtures_anonymisees ne parcourt que backend/tests/fixtures/*.json. De plus, il ne contrôle que les latitudes positives, jamais la longitude.
  - Scénario : Tout partage du dépôt, ou son passage en public, livre la série d'un routeur de la flotte et le lieu de stationnement du véhicule à ~10 m près.
  - Vérification : J'ai lancé un script en lecture seule (samples.db en mode=ro, sans rien afficher) : une série trouvée dans 6 occurrences de Discovery.test.tsx à HEAD, et git log -S donne 2 commits. Écart minimal nul, à la 4e décimale, pour les l.1340 et 1344 de test_poller_hotswap.py. J'ai lu test_fixtures_anonymisees.py l.75.
  - Correctif proposé : Remplacer ces valeurs par les gabarits 0000-0000-NNNN et Paris. Étendre le garde à frontend/src, backend/tests/*.py et aux longitudes.

- [x] **C-15** — Un mot de passe numérique non guillemeté dans routers.yaml s'affiche en clair dans la trace, et le serveur ne démarre plus
  - Où : `backend/app/fleet.py:122` · axes : Sécurité et confidentialité
  - Constat : load_fleet construit RouterConfig(**r) sans filet. YAML lit un mot de passe fait de chiffres comme un entier. Pydantic le refuse, et son message inclut input_value, c'est-à-dire le mot de passe. L'exception remonte du lifespan et arrête tout le démarrage.
  - Scénario : L'exploitant écrit un mot de passe entièrement numérique sans guillemets : la console affiche le mot de passe, puis le serveur s'arrête.
  - Vérification : J'ai relu fleet.py l.116-122 et main.py l.35. Essai direct : RouterConfig(password=12345678), avec une valeur fictive, lève une ValidationError dont le message contient input_value=12345678.
  - Correctif proposé : Convertir en chaîne les champs d'identifiants avant validation (ou SecretStr), valider entrée par entrée, et journaliser errors(include_input=False).

- [x] **C-16** — La cause tirée des profils écrase la cause unanime des liens et peut désigner un profil étranger à la panne
  - Où : `frontend/src/lib/bondProfile.ts:47` · axes : Logique de l'écran
  - Constat : causeDuTunnel liste tous les profils non CONNECTED, même quand un autre profil est connecté. Dans linkVerdict (verdict.ts:674), tunnelCause ?? describeTunnelCause(outOfBond) donne toujours la priorité à cette chaîne.
  - Scénario : Profil A connecté, avec tous ses liens en WAN_DOWN. Profil B secondaire en START. Le bandeau accuse « profil B : le routeur tente de joindre le pair » au lieu de « (WAN_DOWN) ».
  - Vérification : J'ai recompilé et relancé ecran-logique/exp2c.ts (verification/exp2c.js) : les deux phrases sont bien obtenues. J'ai relu bondProfile.ts l.44-60.
  - Correctif proposé : N'employer causeDuTunnel que si aucun profil n'est CONNECTED, sinon garder describeTunnelCause.

- [x] **C-17** — BondPlan et le verdict appliquent deux règles différentes pour dire si le bond porte le flux
  - Où : `frontend/src/components/BondPlan.tsx:112` · axes : Logique de l'écran
  - Constat : verdict.ts:654 pose porteLeFlux = role.kind !== 'routes' && tunnelAttendu !== false. BondPlan.tsx:112 pose role.kind !== 'routes', sans tunnel_attendu. Or cette valeur pilote les libellés des l.217, 351, 440 et 568.
  - Scénario : Sur un MAX HD2 dont le bond n'a jamais tenu (tunnel_attendu false), le bandeau dit que le tunnel n'a jamais été établi. Le plan, lui, titre « 0 / 3 liens qui portent » et affirme que chaque paquet perdu l'est pour le flux.
  - Vérification : grep « porteLeFlux = » ne trouve que ces deux définitions, qui divergent. Je n'ai pas fait de rendu.
  - Correctif proposé : Exporter une seule fonction porteLeFlux(role, tunnelAttendu) et l'employer dans les deux fichiers.

- [x] **C-18** — keyFigures compte un état de tunnel inconnu comme « ne porte pas » : « 0 / N liens qui portent »
  - Où : `frontend/src/lib/dashboard.ts:67` · axes : Logique de l'écran
  - Constat : Dès qu'un objet tunnel existe, porteurs = liens in_bond. Or un lien dont l'état est null a in_bond=false, alors que le verdict pose qu'un état null n'est « ni porteur, ni écarté ». Cela affiche un zéro pour une absence de mesure.
  - Scénario : Liens sans état de tunnel : le bandeau affiche « Liaison OK — 2 liens sur 2 connectés », et le plan « 0 / 2 liens qui portent ».
  - Vérification : exp1.js relancé : « keyFigures état inconnu : carrying 0 total 2 », verdict « ok 2 liens sur 2 connectés ».
  - Correctif proposé : Afficher carrying = null (« — ») quand aucun lien connecté n'a d'état connu, ou exclure ces liens du dénominateur.

- [x] **C-19** — La grandeur, la fenêtre et le thème choisis ne sont jamais mis en évidence
  - Où : `frontend/src/components/HistoryChart.tsx:248` · axes : Composants et expérience de l'écran
  - Constat : HistoryChart (l.248, 261, 269) et ThemeToggle (l.21) posent la classe « active ». La seule règle CSS existante est « .segmented button.is-active » (index.css:775). Aucune feuille ne stylise .active ni aria-pressed, et HistoryChart n'a pas d'aria-pressed.
  - Scénario : L'exploitant clique sur « RSRP » puis sur « 3 h » : aucun bouton ne change d'aspect, et l'axe n'a pas d'unité. Il ne sait pas quelle grandeur il lit.
  - Vérification : J'ai lancé grep sur tous les fichiers .css de src : une seule règle « active », en is-active. Les TSX emploient 'active'. Gravité ramenée de haut à moyen : c'est un défaut de lisibilité, pas une donnée fausse.
  - Correctif proposé : Employer is-active dans HistoryChart et ThemeToggle, et ajouter aria-pressed.

- [x] **C-20** — Un lien désactivé par l'exploitant s'affiche comme une panne (rouge, « Modem déconnecté »)
  - Où : `frontend/src/components/WanCard.tsx:234` · axes : Composants et expérience de l'écran
  - Constat : La couleur ne dépend que de connected. WanCard l.234 et l.272-276 affichent une pastille rouge et « Modem déconnecté — aucune mesure disponible ». LinkTable l.337 et BondPlan l.275 tracent un rail rouge. Band colore en rouge. Seule l'étiquette « désactivé » de la table en tient compte. Cela enfreint la règle « absence ≠ mauvais ».
  - Scénario : L'exploitant désactive le WAN 3 : le rail, le plan, la bande et la fiche s'affichent en rouge, et il va chercher une panne qui n'existe pas.
  - Vérification : J'ai relu WanCard.tsx l.228-276 et LinkTable.tsx l.332-352 (colour sans disabled). L'expérience relecture1.test.tsx n'a pas été relancée.
  - Correctif proposé : Pour disabled, employer QUALITY_COLOR.inconnu et le texte « Lien désactivé par l'exploitant ».
  - Complément du 26/09 : la bande aussi. Chaque tranche compte ses relevés d'adieu d'un WAN désactivé (`disabled`) ; une tranche où ils sont les seuls relevés tombés se peint en neutre, plus en panne.

- [x] **C-21** — Après une reprise d'identifiants ratée, l'écran annonce encore une tentative « au prochain cycle » et invite à recliquer
  - Où : `frontend/src/pages/Overview.tsx:118` · axes : Composants et expérience de l'écran
  - Constat : L'état reprise n'est effacé qu'au clic suivant. Quand la tentative échoue et que le statut revient à « identifiants refusés », le message reste affiché et le bouton reste actif. L'écran dit « Corrigez les identifiants » sans offrir de moyen de le faire, alors que reauth rejoue les mêmes identifiants.
  - Scénario : Le mot de passe a changé sur le routeur. L'exploitant croit que la tentative n'a pas encore eu lieu et reclique : chaque clic consomme un des cinq échecs qui déclenchent la suspension de 5 min.
  - Vérification : J'ai relu Overview.tsx l.85 et 112-133 : setReprise n'est remis à null qu'au clic.
  - Correctif proposé : Effacer reprise au retour du statut refusé, désactiver le bouton tant que la tentative est en attente, et ajouter un lien vers la reconnexion (/decouverte).

- [x] **C-22** — Rapport imprimable : un échec de lecture est imprimé comme une absence de mesure ou comme une attente
  - Où : `frontend/src/pages/Rapport.tsx:60` · axes : Composants et expérience de l'écran
  - Constat : Un fetchHistory en échec donne serie: null (l.60), imprimé « Aucune mesure sur la session. » (l.156-157). Un fetchEvents en échec est avalé (l.50) et le journal reste sur « Relecture de la fenêtre en cours… ». Un fetchRouter en échec est avalé lui aussi (l.49).
  - Scénario : Une requête /history expire sur une longue session. Le PDF remis à un tiers affirme « Aucune mesure sur la session » pour ce lien.
  - Vérification : J'ai relu Rapport.tsx l.40-66 et 150-157.
  - Correctif proposé : Garder un état d'erreur par bloc, avec un texte explicite, et désactiver Imprimer tant que la lecture n'est pas finie.

- [x] **C-23** — Export CSV de session sans plafond, construit de façon synchrone sur la boucle d'événements
  - Où : `backend/app/api/routes.py:358` · axes : Stockage, sessions et API
  - Constat : export_rows relit la session entière, et la boucle Python qui construit le CSV tourne dans le handler async. Elle n'a ni to_thread ni le plafond de 24 h et 150 000 lignes posé sur session_rows. Pendant ce temps, la scrutation est figée. Or les budgets par équipement sont mesurés en temps réel : un gel long fait donc passer des équipements « injoignables ».
  - Scénario : Session de 10 h, 29 525 relevés : environ 680 ms de Python synchrone et 111 Mo de pic mémoire. Pour une session de trois semaines, l'extrapolation donne des dizaines de secondes de gel, ce qui n'est pas mesuré.
  - Vérification : J'ai relu routes.py l.351-380 : boucle for synchrone, sans to_thread. Les mesures viennent de l'axe stockage (exp2 et exp3), que je n'ai pas relancées.
  - Correctif proposé : StreamingResponse avec fetchmany par lots hors de la boucle, ou un plafond annoncé.

- [x] **C-24** — Un lancement refusé (port déjà pris) sauvegarde et élague quand même, et efface les copies d'avant l'incident
  - Où : `backend/lancer.py:206` · axes : Exploitation, lanceur et documentation
  - Constat : main() appelle preparer(), donc sauvegarder() et l'élagage à 2 copies, avant port_disponible(). Instance vivante en WAL : le fichier -wal existe, journal_chaud est vrai et la déduplication est sautée. Chaque relance refusée crée une copie de l'état courant et supprime la plus ancienne.
  - Scénario : Après un incident, deux doubles-clics de plus sur l'exécutable déjà lancé suffisent à remplacer les deux copies d'avant l'incident.
  - Vérification : J'ai relu lancer.py l.57-83 et 199-221 : preparer() est bien appelé avant port_disponible. Gravité ramenée de haut à moyen : la base principale n'est pas touchée.
  - Correctif proposé : Tester le port avant preparer(), ou ne pas élaguer quand le lancement est refusé.

- [x] **C-25** — Les scripts de capture Windows en mode client API emploient les noms de paramètres que le firmware refuse, et le .cmd prend le mot de passe en argument
  - Où : `backend/tools/capture_router_api.cmd:64` · axes : Sécurité et confidentialité, Exploitation, lanceur et documentation
  - Constat : Le .cmd (l.64) et le .ps1 (l.86-88) envoient client_id, client_secret et grant_type, puis ?access_token= (cmd l.95, ps1 l.141). Or le client (router_api.py l.787-788 et 932-939) consigne, mesures à l'appui, que seuls clientId et accessToken sont acceptés. En mode admin, le .cmd met le mot de passe sur la ligne de commande (%~3, l.51).
  - Scénario : Un technicien suit la documentation avec un client API et archive des « Client not found » ou « Unauthorized » à la place des captures.
  - Vérification : J'ai lancé grep sur les deux scripts et relu router_api.py l.928-940. Rien n'a été testé contre un routeur, ce qui est interdit ici.
  - Correctif proposé : Passer à clientId/clientSecret et accessToken, et demander le mot de passe de façon interactive.

- [x] **C-26** — La règle « un refus d'identifiants n'est jamais retenté seul » n'est gardée par presque aucun test
  - Où : `backend/app/api/routes.py:945` · axes : Tests et garde-fous
  - Constat : Aucun test n'appelle POST /reauth, et test_reauth_lifts_the_lock pose auth_blocked à la main. Les mutations B15 (pas d'allow_new_credentials) et B16 (auth_blocked non remis à faux) survivent. B3 et B4, qui suppriment le verrou sur un login ou un jeton en stat: fail, survivent aussi. Or la route de capture n'a que ce verrou comme protection, et on passe de 1 à 6 logins. Côté écran, aucun test ne monte Overview avec « identifiants refusés » : F9, reprise automatique à chaque rendu, survit.
  - Scénario : Une régression fait reboucler les logins, et la suite reste verte pendant que le routeur suspend l'accès admin au 5e échec.
  - Vérification : grep reauth dans backend/tests ne trouve que test_poller_hotswap et test_router_api, sans appel de la route. grep « identifiants refusés » dans les tests frontend ne trouve que verdict.test.ts. Les résultats des mutations viennent de l'axe tests : je ne les ai pas relancées. Gravité ramenée de haut à moyen, car il n'y a pas de défaut à l'exécution aujourd'hui.
  - Correctif proposé : Un test ASGI de /reauth, un test par mode (jeton, session) qui vérifie un seul POST d'authentification après un stat: fail, et un test Overview qui vérifie qu'aucun reauth ne part sans clic.

- [x] **C-27** — Aucun test ne garantit que le collecteur n'interroge jamais config.*
  - Où : `backend/tests/test_rejeu_bout_en_bout.py:174` · axes : Tests et garde-fous
  - Constat : Le rejeu tient endpoints_demandes, mais n'y vérifie que count <= 2. Un endpoint config.* demandé à chaque cycle, sur deux cycles, passe donc le test. Seule la liste de capture est gardée.
  - Scénario : Une évolution lit des clés pré-partagées via config.* à chaque cycle, et aucun test ne rougit.
  - Vérification : J'ai relu test_rejeu_bout_en_bout.py l.165-176. grep « config. » dans ce fichier ne trouve rien. La mutation B14 vient de l'axe tests, je ne l'ai pas relancée.
  - Correctif proposé : assert not any(e.startswith('config.') for e in endpoints_demandes), plus un MockTransport qui échoue sur /api/config*.

- [x] **C-28** — Les règles de dénominateur de l'écran et de la base ne sont pas figées par les tests
  - Où : `frontend/src/components/HistoryChart.tsx:197` · axes : Tests et garde-fous
  - Constat : Plusieurs mutations survivent. F4 : un taux à dénominateur null redevient publiable. F5, F7 et F8 : texte de disponibilité de SessionSheet, alors que tous les tests passent availability null. B11 : un dénominateur à 0 pour un compteur absent. Le test de bout en bout ne vérifie que les points de perte > 0, et les trois rejeux n'en ont aucun : il ne vérifie donc rien.
  - Scénario : Une régression republie une perte sans dénominateur, ou un « 0 paquet compté » pour une absence, et la suite reste verte.
  - Vérification : J'ai relu HistoryChart.tsx l.194-199. Les résultats des mutations et d'exp_loss viennent de l'axe tests, je ne les ai pas relancés.
  - Correctif proposé : Des tests ciblés : loss_percent avec denominator null, SessionSheet sur reel.summary, et un relevé sans packets qui doit rendre un denominator None.

## Bas

- [x] **C-29** — Une lecture complémentaire tombant sur une page non JSON de /api/login fait perdre le relevé
  - Où : `backend/app/peplink/router_api.py:917` · axes : Collecteur et client routeur
  - Constat : Les deux authentifications appellent resp.json() sans filet. Le JSONDecodeError qui en sort n'est ni RouterApiError ni httpx.HTTPError : il traverse les except des compléments.
  - Scénario : Le routeur, en fin de démarrage, sert une page HTML pile quand la session expire sur un complément : l'équipement est marqué injoignable avec un message de parseur.
  - Vérification : J'ai relancé collecteur/exp4.py : « relevé PERDU : JSONDecodeError ».
  - Correctif proposé : Convertir en RouterApiError, sans poser _credentials_refused.

- [x] **C-30** — Température des modems : sans valeur publiée, un POST par WAN cellulaire à chaque cycle, indéfiniment
  - Où : `backend/app/peplink/router_api.py:1379` · axes : Collecteur et client routeur
  - Constat : _temperature_at n'avance que si une valeur a été lue (l.1377-1380).
  - Scénario : Un firmware qui répond stat ok sans température ajoute un POST par modem à chaque cycle, ce qui rapproche du budget de C-5.
  - Vérification : exp4.py relancé : « 5 cycles -> POST température : 10 ». J'ai relu l.1370-1380.
  - Correctif proposé : Marquer l'instant après toute réponse lue.

- [x] **C-31** — Le journal coûte environ 1,8 s de SQL toutes les 30 s sur une session en cours
  - Où : `backend/app/storage/db.py:1420` · axes : Stockage, sessions et API
  - Constat : events() enchaîne cinq balayages fenêtrés sur l'unique connexion, relus toutes les 30 s par DeviceShell.
  - Scénario : Sur la plus longue session de la copie, events() coûte 1,75 à 1,84 s par appel : les écritures du poller attendent derrière.
  - Vérification : J'ai relancé stockage/ev.py sur la copie : 1839 ms pour le MAX HD4. Gravité ramenée de moyen à bas : cela représente environ 6 % d'occupation, sans donnée fausse.
  - Correctif proposé : Fusionner les passes, ou ne recalculer que la queue de fenêtre.
  - Complément du 26/09 : coût réduit, pas seulement isolé. Les îlots de latence se trouvent en une passe ordonnée (700 → 480 ms sur 60 000 relevés synthétiques, mêmes îlots), et l'écran ne relit plus bande ni journal onglet caché. Fusionner le fenêtrage du brassage n'a rien gagné (503 → 488 ms, du bruit) : écarté. Le recalcul de la seule queue de fenêtre reste possible, non fait — les agrégats par lien le rendent délicat.

- [x] **C-32** — L'alerte d'horloge accuse le routeur même quand c'est le poste qui dérive
  - Où : `backend/app/poller.py:404` · axes : Collecteur et client routeur
  - Constat : L'écart est mesuré contre l'horloge du PC, et le texte désigne le routeur sans confronter les équipements. L'historique et le journal sont alors coupés.
  - Scénario : Un PC sans NTP a 3 min d'avance : tous les routeurs sont déclarés décalés.
  - Vérification : Lu à la source dans poller.py l.394-413. Je ne l'ai pas reproduit.
  - Correctif proposé : Parler d'un écart entre routeur et poste, et désigner le poste si tous les équipements montrent le même écart.

- [x] **C-33** — Le décompte des clients du LAN n'a pas d'âge et reste figé après un échec
  - Où : `backend/app/peplink/router_api.py:1444` · axes : Collecteur et client routeur
  - Constat : lan_clients n'est remplacé que sur une lecture réussie, et LanClients n'est pas daté.
  - Scénario : « 12 appareils actifs » lu le matin reste affiché le soir alors que la lecture échoue.
  - Vérification : Lu à la source : l.1419-1445, sans remise à None.
  - Correctif proposé : Dater LanClients et l'effacer après deux intervalles sans lecture.

- [x] **C-34** — Alertes admin et horloge réécrites après un await sans vérifier _still_supervised
  - Où : `backend/app/poller.py:351` · axes : Collecteur et client routeur
  - Constat : Un forget() tombé pendant les awaits du journal ou de la fiche ressuscite _alerte_admin ou _alerte_horloge.
  - Scénario : Après un ré-appairage pendant une lecture du journal, le nouveau record hérite de l'alerte de l'ancien.
  - Vérification : Lu à la source (poller.py l.341-357). Je ne l'ai pas reproduit.
  - Correctif proposé : Ajouter une garde _still_supervised avant chaque écriture.

- [x] **C-35** — Manœuvres de la fiche comptées sur toute la session quand la fiche est partielle
  - Où : `backend/app/api/routes.py:430` · axes : Stockage, sessions et API
  - Constat : router_events reçoit started_at et last_seen, et non lu_de et lu_a.
  - Scénario : Session de 3 jours, fiche partielle sur les 24 dernières heures : une manœuvre du premier jour y est comptée.
  - Vérification : J'ai relu routes.py l.415-440 : lu_de et lu_a ne sont pas passés à router_events.
  - Correctif proposé : Passer la fenêtre relue.

- [x] **C-36** — Bornes extrêmes : /timeline et /events rendent 500 au lieu de 400
  - Où : `backend/app/api/routes.py:152` · axes : Stockage, sessions et API
  - Constat : _plafonnee (fin - 24 h) et la fenêtre de cause (debut - 15 s, l.682) n'interceptent pas l'OverflowError.
  - Scénario : ?since=0001-01-01…&until=0001-01-01T12:00Z rend 500.
  - Vérification : J'ai relu routes.py l.150-153 et 681-683 : aucun try.
  - Correctif proposé : Intercepter OverflowError, ou borner les dates dans _en_utc.

- [x] **C-37** — Export complet (SSID, BSSID, position exacte) et /devices/known ne sont pas réservés au poste local
  - Où : `backend/app/api/routes.py:351` · axes : Sécurité et confidentialité, Exploitation, lanceur et documentation
  - Constat : Ces routes n'ont pas require_loopback, alors que Vite écoute sur le LAN (host: true) et que HOST=0.0.0.0 est documenté. Le README omet d'ailleurs ces routes, et place à tort la WebSocket sous /api.
  - Scénario : Un téléphone du réseau télécharge l'export complet et la liste des séries appairées.
  - Vérification : J'ai relu routes.py l.250 et 351 : pas de dependencies.
  - Correctif proposé : Réserver complet=true et /devices/known au loopback, puis corriger le README.

- [x] **C-38** — Le rapport « à partager » imprime le libellé de session, qui nomme le lieu
  - Où : `frontend/src/pages/Rapport.tsx:100` · axes : Sécurité et confidentialité
  - Constat : s.label est affiché sans condition sur complet, sous la mention « position arrondie à ~100 m ».
  - Scénario : Une session nommée d'après l'adresse du client figure en tête de la version partagée.
  - Vérification : J'ai relu Rapport.tsx l.99-116.
  - Correctif proposé : N'afficher le libellé qu'en version complète.

- [x] **C-39** — Redondance « active » publiée sans dénominateur minimal ; débit récent sous-estimé par la tranche en cours
  - Où : `frontend/src/lib/redundancy.ts:65` · axes : Logique de l'écran
  - Constat : L'état 'on' est rendu dès repair > 0, sans le seuil PAQUETS_CONCLUANTS. Par ailleurs, la tranche en cours compte pour une tranche pleine dans paquetsParSecondeRecents, ce qui abaisse le débit vers le seuil de 10 paquets/s.
  - Scénario : Avec 3 paquets de réparation pour 5 transportés, l'écran titre « 37,5 % de la capacité part en redondance ».
  - Vérification : exp1.js relancé : {state:'on', share:0.375, packets:5}. Le second point est lu à la source (l.93-107), sans chiffrage.
  - Correctif proposé : Appliquer le seuil à 'on', et pondérer la dernière tranche par sa durée écoulée.
  - Complément du 26/09 : la pondération de la dernière tranche est faite — elle ne compte que sa durée écoulée jusqu'à la borne de fin.

- [x] **C-40** — Diversité : stations et cellules regroupées sans l'opérateur, porteuse inconnue tenue pour identique
  - Où : `frontend/src/lib/diversity.ts:95` · axes : Logique de l'écran
  - Constat : Les clés de regroupement ne contiennent pas le plmn, et un earfcn manquant devient « ? ».
  - Scénario : Deux SIM de deux opérateurs aux numéros eNB identiques sont déclarées « même station ».
  - Vérification : exp2c relancé : « cellules 1 sites 1 même cellule ».
  - Correctif proposé : Préfixer les clés par le plmn, et ne pas grouper sur une porteuse inconnue.

- [x] **C-41** — Historique : au changement de grandeur, l'ancienne série reste tracée sous le nouveau libellé
  - Où : `frontend/src/components/HistoryChart.tsx:129` · axes : Composants et expérience de l'écran
  - Constat : data n'est pas vidé quand metric ou minutes change, et les règles de la nouvelle grandeur s'appliquent aux anciens points.
  - Scénario : Passage de SINR à Perte : pendant le chargement, le pied annonce des points retirés qui n'existent pas.
  - Vérification : J'ai relu l.129-175 : aucun setData([]) au changement de dépendances.
  - Correctif proposé : Associer la série à sa clé (grandeur, fenêtre, session).

- [x] **C-42** — Textes d'état trompeurs : « ne répond pas » pour des identifiants refusés, « Trois règles » pour six, durée « dans cet état » depuis l'ouverture de la page
  - Où : `frontend/src/pages/Overview.tsx:140` · axes : Composants et expérience de l'écran
  - Constat : Plusieurs libellés contredisent l'état réel. Overview (l.139-142) affiche « ne répond pas » pour tout statut autre que connecté ou en attente. Masthead affiche « identité en cours de lecture » même si aucune lecture n'aura lieu. Alerts.tsx:104 affirme « Trois règles » alors que le fichier en traduit six. VerdictBanner mesure la durée depuis Date.now() au montage.
  - Scénario : Pour un équipement aux identifiants refusés, l'écran dit « l'équipement ne répond pas ». Une panne de 2 h est affichée « 1 min dans cet état ».
  - Vérification : Lu à la source : Overview.tsx l.139-142, et grep « Trois règles » dans Alerts.tsx l.104.
  - Correctif proposé : Adapter les libellés au statut, mettre à jour la note des alertes, et dire « observé depuis ».

- [x] **C-43** — Appairage en mode client API : ni avertissement « en clair » ni vérification TLS
  - Où : `frontend/src/components/PairingForm.tsx:94` · axes : Composants et expérience de l'écran
  - Constat : L'avertissement et la case TLS ne vivent que dans la branche admin, et verify_ssl reste à false en mode client.
  - Scénario : En mode client API vers un hôte http://, le secret circule en clair sans que rien ne le signale.
  - Vérification : Lu à la source d'après l'axe composants. Je ne l'ai pas relu en détail.
  - Correctif proposé : Sortir ce bloc de la branche admin.

- [x] **C-44** — Badge ambre « support expiré » allumé en permanence ; badge « principale » posé sur plusieurs porteuses d'une même bande
  - Où : `frontend/src/components/DeviceCard.tsx:176` · axes : Composants et expérience de l'écran
  - Constat : Le badge warn suit un état stable (care_plan_active false), ce qui contredit la règle « pas d'avertissement permanent ». BandTable reconnaît la porteuse principale par son seul code de bande.
  - Scénario : Un châssis hors contrat porte un badge d'alerte à chaque ouverture. En agrégation intra-bande, deux lignes sont marquées « principale ».
  - Vérification : J'ai relu DeviceCard.tsx l.174-183. BandTable n'est lu que par l'axe composants, et le cas matériel n'est pas vérifié.
  - Correctif proposé : Rendre le badge neutre, et comparer aussi le canal.

- [x] **C-45** — Numéro de série réel du Dome encore lisible dans l'historique git poussé
  - Où : `docs/REVUE-2026-09-24.md` · axes : Sécurité et confidentialité
  - Constat : La série a été retirée à HEAD, mais reste dans les blobs de commits déjà poussés sur un dépôt privé.
  - Scénario : Un git log -p la restitue.
  - Vérification : Script en lecture seule : aucune occurrence à HEAD, 3 commits via git log -S.
  - Correctif proposé : Réécrire l'historique si c'est jugé nécessaire (côté Max), sinon consigner l'exposition.
  - Fait le 26/09 : l'historique de main est ramené à un seul commit, sans la série ni la position, aux fichiers identiques ; les messages des 59 commits précédents sont dans `docs/HISTORIQUE-2026-09.md`. Les Releases et étiquettes v0.1.0 (ancienne histoire, archive source exposée) et v0.1.1 ont été supprimées, v0.1.1 reposée sur le nouveau commit. Les anciens commits restent lisibles par identifiant chez GitHub jusqu'à son nettoyage ; une purge peut être demandée à son support.

- [x] **C-46** — Tests fragiles ou peu hygiéniques : dépendance à la table de routage, vraies connexions TCP, dossiers temporaires laissés, api/client.ts jamais testé
  - Où : `backend/app/peplink/router_api.py:2627` · axes : Tests et garde-fous
  - Constat : Plusieurs défauts rendent les tests dépendants de la machine ou les laissent aveugles. ip_locale_vers fait un vrai connect UDP, et son résultat entre dans l'export dome.json. test_discovery sonde 203.0.113.7 sans moque. exporter_rejeu laisse un mkdtemp à chaque passage. openLiveSocket et pairDevice sont moqués partout (F14 à F16 survivent).
  - Scénario : Sur un portable hors ligne, test_the_screen_fixtures… échoue sans aucun changement de code.
  - Vérification : Résultats de l'axe tests, que je n'ai pas relancés. Le point sur ip_locale_vers est cohérent avec la ligne citée.
  - Correctif proposé : Figer l'IP locale dans le rejeu, ajouter un garde réseau autouse, employer TemporaryDirectory, et écrire un client.test.ts.

- [x] **C-47** — Démarrage : base illisible qui plante sur une trace, second double-clic muet, HOST précis jamais ouvert
  - Où : `backend/lancer.py:81` · axes : Exploitation, lanceur et documentation
  - Constat : Trois défauts au lancement. sauvegarder() n'intercepte pas sqlite3.DatabaseError. Quand le port est pris, le lanceur fait un sys.exit(2) immédiat, sans ouvrir l'instance existante. L'adresse ouverte est figée sur 127.0.0.1 alors qu'uvicorn écoute sur HOST.
  - Scénario : Sous Windows, après un arrachage de clé, la console affiche « file is not a database » puis se referme.
  - Vérification : J'ai relu lancer.py l.79-80 (backup sans try) et l.204-221. Gravité ramenée de moyen à bas : l'application échouerait de toute façon sur une base corrompue, c'est le message qui manque.
  - Correctif proposé : Afficher un message clair sur la base illisible et sur les .bak, ouvrir l'instance existante, et ouvrir l'hôte configuré.

- [x] **C-48** — CI : contents: write accordé à tous les jobs de construction
  - Où : `.github/workflows/executable.yml:34` · axes : Exploitation, lanceur et documentation
  - Constat : Le droit est posé au niveau du workflow, alors que seul publier en a besoin. Le jeton est persisté par checkout pendant npm ci et pip install (dépendances en >=).
  - Scénario : Une dépendance PyPI compromise pousse sur le dépôt ou modifie une Release.
  - Vérification : J'ai relu l.34-37 et grep persist-credentials : rien trouvé. Gravité ramenée à bas : l'attaque exige une dépendance compromise, et le lockfile npm fige ses versions.
  - Correctif proposé : contents: read au niveau global, write sur publier seul, et persist-credentials: false.

- [x] **C-49** — REFERENCE : Python 3.11 et lancement uvicorn direct (mode mock, sans sauvegarde), contre Python 3.12 et lancer.py dans le README
  - Où : `docs/REFERENCE.md:125` · axes : Exploitation, lanceur et documentation
  - Constat : Les deux documents décrivent deux comportements par défaut différents pour « lancer l'outil ».
  - Scénario : En suivant REFERENCE, on lance en mode mock et sans aucune sauvegarde.
  - Vérification : Rapport de l'axe exploitation. config.py (défaut 'mock') et lancer.py:188 ('router') sont confirmés.
  - Correctif proposé : Aligner REFERENCE sur lancer.py et sur Python 3.12.

## Écartés (fusionnés)

- Vrai numéro de série dans Discovery.test.tsx (axe Tests et garde-fous) — Doublon du constat de l'axe Sécurité, fusionné dans C-14.
- Le garde d'anonymisation des fixtures laisse passer une vraie position et un IMEI numérique — Fusionné dans C-14 : c'est la cause qui laisse passer les fuites constatées. Les fixtures actuelles sont propres.
- Lancé depuis le dépôt hors de backend/, la sauvegarde ne se fait pas — Même cause que le .env à chemins relatifs, fusionné dans C-4.
- Scripts de capture : access_token et mot de passe en argument (axe Sécurité) — Doublon du constat de l'axe Exploitation, fusionné dans C-25.
- Pied du rapport qui promet des comptes de mesures absents — Fusionné avec le dénominateur unique de la diversité dans C-12.
- README : WebSocket placé sous /api et routes omises — Fusionné dans C-37 : même sujet d'exposition des routes, défaut documentaire mineur.
- Second double-clic muet, et HOST sur une adresse précise — Fusionnés dans C-47 (démarrage).
- Débit récent sous-estimé par la tranche en cours — Fusionné dans C-39, même module, plausible et non chiffré.
- Identifiants refusés présentés comme « ne répond pas », « Trois règles », durée « dans cet état » — Fusionnés dans C-42 : libellés contradictoires, tous lus à la source.

## Couverture déclarée par axe

- **Logique de l'écran (frontend/src/lib + types.ts)** — J'ai lu en entier verdict.ts, dashboard.ts, quality.ts, bondProfile.ts, bondRole.ts, redundancy.ts, diversity.ts, chart.ts, session.ts, format.ts, themeChoice.ts, routes.ts, wanOrder.ts, useElementSize.ts et types.ts (moitié haute). J'ai aussi lu les appelants : DeviceShell.tsx (calcul du rôle, timeline), VerdictBanner.tsx (lissage), Overview.tsx et BondPlan.tsx (affichage des chiffres). Côté backend : models.py (Timeline, RouterSummary, sfc_expires_at), router_api.py (_retenir_routes, parse_bond_routes, sentinelle PEER_DOWN, échéance en UTC) et db.py (SQL de la bande et de l'historique des taux).
Contrôle types.ts contre models.py par script (champs et nullabilité, backend/.venv) : cohérents. Seul écart : Alert.absorbee, publié par le serveur et absent de types.ts, sans effet.
Base samples.db ouverte en lecture seule (mode=ro) : horodatages avec fuseau +00:00 ; aucune ligne avec repair_sent renseigné et packets_up absent ; répartition des états de tunnel (PEER_DOWN environ 30 000, état absent environ 7 000).
Commandes lancées : vitest sur src/lib (240 tests passent) et tsc -b --noEmit (sans erreur).
Expériences : esbuild en bundle depuis le dépôt, sortie dans mon brouillon ecran-logique/, fichiers exp1.ts, exp2c.ts et diff_types.py.
Non vérifié : le rendu réel dans le navigateur. Je n'ai touché ni au serveur ni à un routeur.
- **Composants et expérience de l'écran (frontend/src/components, pages, App.tsx, index.css)** — J'ai lu en entier App.tsx, les pages (DeviceShell, Overview, WanDetail, Rapport, Discovery) et les composants VerdictBanner, Masthead, LinkTable, BondPlan, WanCard, HistoryChart, EventLog, SessionSheet, DeviceCard, Band, BandTable, Alerts, PairingForm, Note, SignalTile, ThemeToggle, EcranBoundary et ChartBoundary. Dans index.css, j'ai lu les jetons, les deux thèmes, le bloc d'impression et les requêtes média ; aucune couleur n'est écrite en dur dans les TSX. J'ai aussi lu openLiveSocket et useElementSize pour les fuites, puis les routes d'export, de reauth et d'appairage du backend, pour recouper.

Suites lancées dans le dépôt : vitest (507 tests passent) et tsc -b --noEmit (aucune erreur). git status ne montre aucune modification.

Expériences faites sur une copie du frontend dans le brouillon (scratchpad/relecture/ecran-composants/fe), fichiers src/relecture1..3.test.tsx :
- rendu d'un lien désactivé dans LinkTable, WanCard et BondPlan ;
- classes de ThemeToggle comparées à la feuille de style ;
- message de reprise d'Overview après une tentative échouée ;
- changement de grandeur dans HistoryChart pendant qu'une requête est en vol.

Non fait : aucune vérification visuelle en navigateur. Le serveur de dev relaie vers 127.0.0.1:8000, que je n'avais pas le droit de toucher. La mise en page sous 1100 px est donc vérifiée seulement à la lecture du CSS : le bandeau passe sur deux rangées, et je n'y ai pas vu de défaut. Aucun contact avec un routeur ni avec la base.
- **Collecteur et client routeur (router_api.py, poller.py, pairing/, devices/, discovery/)** — J'ai lu en entier backend/app/peplink/router_api.py, poller.py, pairing/service.py, devices/registry.py, discovery/network.py et probe.py, ainsi que les routes reauth, unpair, pair et capture de api/routes.py, les passages utiles de storage/db.py (router_events, router_history) et les composants frontend qui affichent tunnel_uptime_s, path_latency_recent_ms et le bouton de reprise. Côté backend, pytest donne 697 passed. Les expériences sont dans le brouillon .../scratchpad/relecture/collecteur/ (exp1 à exp7) : un faux routeur httpx.MockTransport, sans aucun réseau, et un Poller réel avec une base jetable dans le brouillon. J'ai aussi lu les fixtures et interrogé samples.db en lecture seule (mode=ro) : 1044 points de latence, sans null ni collision. Je n'ai eu aucun contact avec un routeur ni avec 127.0.0.1:8000. Je n'ai lancé ni vitest ni tsc, faute de modification frontend dans cet axe.
- **Stockage, sessions et API (backend/app/storage/db.py, backend/app/sessions.py, backend/app/api/routes.py, backend/app/models.py)** — J'ai lu en entier db.py (1813 lignes), sessions.py, routes.py, ainsi que les modèles de session, de bande, de journal et d'historique dans models.py. J'ai aussi lu, en dehors du périmètre, ce qui me servait de preuve : parse_latency_series, parse_wan_latency, _sur_la_grille et parse_latency_cutoff dans router_api.py, l'écriture des séries dans poller.py, et la cadence de relecture dans DeviceShell.tsx et SessionSheet.tsx.

Base : copie par l'API backup() depuis une connexion en lecture seule (mode=ro), vers scratchpad/relecture/stockage/copie.db. Elle compte 97 664 relevés et 6 sessions. La base réelle n'a jamais été ouverte en écriture.

Expériences, toutes sur la copie ou sur une base synthétique, avec les scripts ev.py, prof.py, synth.py, exp*.py, hist.py, fiches.py et div.py du brouillon :
- chronométrage d'events(), timeline() et history() ;
- chronométrage de l'export CSV et de la fiche de session ;
- recalcul des six fiches ;
- dénominateurs de la diversité ;
- deux scénarios synthétiques : un îlot de latence de part et d'autre d'un trou, et une latence de lien après la première série du routeur.

Tests : backend, pytest -q, 697 passés. Le frontend n'a pas été lancé, il est hors de cet axe.

Aucun contact avec un routeur ni avec 127.0.0.1:8000.

Points vérifiés et trouvés sains : migration additive des colonnes ; purge hors grille sans effet quand l'intervalle vaut 10 s (toutes les fixtures sont à 10) ; seuil cutoff_ms, où 0 est ramené à NULL ; garde de trou dans les requêtes de voisins, de brassage et de changement d'opérateur ; fusion des tranches en borne haute de la bande ; bornes naïves ou décalées ramenées en UTC ; comparaisons de chaînes d'horodatage.
- **Exploitation, lanceur et documentation** — J'ai lu en entier backend/lancer.py, backend/tools/construire_executable.py, backend/tools/verifier_executable.py, .github/workflows/executable.yml, README.md, CHANGELOG.md, app/config.py (résolution des chemins), les lignes 525-560 de app/storage/db.py (WAL), les routes (routes.py, ws.py), les scripts tools/capture_router_api.cmd et .ps1, et les sections exécutable, déploiement et configuration de docs/REFERENCE.md. J'ai parcouru docs/AUDIT et docs/REVUE. Commandes lancées : pytest (697 passés), vitest (507 passés), ruff check et tsc --noEmit -p . (verts tous les deux). J'ai fait trois expériences sur une copie de lancer.py dans le brouillon exploitation/ : la rotation des sauvegardes pendant qu'une instance tourne (exp1.py), la sauvegarde d'une base corrompue (exp2.py), et une simulation du mode gelé (sys.frozen, _MEIPASS, cwd = dossier personnel) avec un .env à chemins relatifs posé à côté de l'exécutable (gel/sim.py), plus un lancement depuis le dépôt avec un autre répertoire courant (depot/). Je n'ai eu aucun contact avec un routeur ni avec 127.0.0.1:8000, et je n'ai pas ouvert samples.db.
- **Sécurité et confidentialité** — J'ai lu en entier backend/app/api/security.py, routes.py (routes 200-460 et 700-990 : exports, capture, appairage, reauth, suppression), ws.py (contrôle d'origine), main.py (CORS, montage), pairing/service.py, devices/registry.py (add, redact), fleet.py (load_fleet), ainsi que la couche HTTP et redact de router_api.py (lignes 140-1010) et parse_router_log. Dans poller.py, j'ai relu les chemins d'erreur et la gestion d'auth_blocked. Côté outils : tools/capture_router_api.ps1/.cmd, exporter_rejeu.py, construire_executable.py, puis .gitignore, routers.example.yaml, .github/workflows/executable.yml. Côté écran : frontend vite.config.ts, Rapport.tsx, SessionSheet.tsx, EventLog.tsx et PairingForm.tsx, où j'ai cherché les usages de localStorage.

Expériences, toutes dans le brouillon relecture/securite :
- exp_rebind.py monte les vraies routes avec un registre simulé dans une TestClient dont le client est 127.0.0.1. Il teste une attaque CSRF sur /reauth, les deux routes en POST text/plain, et une attaque par DNS rebinding sur la capture, sur /api/devices et sur la WebSocket.
- Un routers.yaml de test porte un mot de passe numérique fictif.
- scan.py balaie les 215 commits : tous les blobs, chaque valeur étant masquée à l'affichage. J'ai aussi comparé en lecture seule (mode=ro) les fichiers suivis et l'historique git aux valeurs réelles de samples.db : séries de la table devices, positions hors démo, SSID/BSSID.
- J'ai cherché le secret de routers.yaml dans l'historique git complet, les messages de commit et les journaux uvicorn : aucune occurrence.
- La suite backend passe : 697 tests.

Je n'ai touché ni au serveur en service ni à aucun routeur.
- **Tests et garde-fous** — Suites lancées sur le dépôt : backend `.venv/bin/python -m pytest -q -p no:cacheprovider` → 697 passed, 1 warning (StarletteDeprecationWarning httpx), 26,4 s. Frontend `npx vitest run < /dev/null` → 32 fichiers, 507 tests passés, sortie 0 (les deux traces « Error: champ absent » sur stderr sont voulues : c'est le test du périmètre d'erreur de DeviceShell.test.tsx:302). `npx tsc -b --noEmit` → sortie 0, aucune ligne. Lus : conftest.py, rejeu.py, test_rejeu_bout_en_bout.py, tools/exporter_rejeu.py, test_fixtures_anonymisees.py, des parties de test_discovery.py, test_poller_hotswap.py, test_router_api.py, test_alerts.py, HistoryChart.test.tsx, reel.test.tsx et Discovery.test.tsx, plus le code visé par les mutations (router_api.py, routes.py, alerts.py, storage/db.py, verdict.ts, quality.ts, format.ts, HistoryChart.tsx, SessionSheet.tsx, Overview.tsx, api/client.ts). À noter : tests/rejeu.py et tools/exporter_rejeu.py se trouvent sous backend/. Le dépôt a été copié dans le brouillon relecture/tests/repo (rsync sans node_modules, .venv ni data, avec des liens symboliques vers les deux), puis 32 mutations ciblées ont été appliquées une à une, chacune suivie de la suite complète et d'une restauration. Survivantes : B3, B4, B8 (équivalente), B9, B11, B14, B15, B16, F4, F5, F7, F8, F9, F10, F14, F15, F16. Tuées : B1, B2, B5, B6, B7, B10, B12, B13, F1, F2, F3, F6, F11, F12, F13. J'ai aussi lancé la suite backend avec un garde réseau posé sur socket.connect dans la copie, plus deux expériences dans le brouillon (le nombre de logins, et le contenu réel du rejeu). La base samples.db n'a été ouverte qu'en `mode=ro`, et seulement pour comparer des séries sans les afficher. Après coup, `git status` sur le dépôt est vide et `diff -rq` entre la copie et le dépôt ne montre plus rien. Aucun routeur ni le port 8000 n'ont été contactés. Pas d'outil de couverture installé (ni coverage ni pytest-cov) : les zones non couvertes ont été établies par grep et par mutation.
