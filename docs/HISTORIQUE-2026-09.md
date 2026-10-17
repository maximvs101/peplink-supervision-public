# Historique des commits, 25 → 26 septembre 2026

C-45 : le numéro de série réel du Dome et une position GPS réelle, retirés
des tests par C-14, restaient lisibles dans les commits déjà poussés ;
l'historique a été ramené à un seul commit le 26 septembre 2026. Les messages des
59 commits de cette période sont conservés ici, du plus ancien au plus récent,
sans leurs identifiants, qui n'existent plus. Le constat de chaque correctif
reste dans `docs/RELECTURE-2026-09-25.md`.

### 2026-09-25 — Relecture complète du 25/09 : 49 constats consignés, à reprendre

Sept relecteurs en parallèle (un par axe, lecture seule) et un
vérificateur : 5 hauts, 23 moyens, 21 bas, chacun avec sa preuve, son
scénario et le correctif proposé. Rien n'est corrigé ici ; l'ordre de
reprise proposé est en tête du document.

### 2026-09-26 — C-14 : série et position réelles retirées des tests, garde étendu aux sources

La série d'un équipement appairé (six fois dans Discovery.test.tsx) passe
au gabarit 0000-0000-0063 ; les positions de stationnement de
test_poller_hotswap.py et test_sessions.py passent au centre de Paris.

Le garde d'anonymisation ne lisait que fixtures/*.json et les latitudes
positives. Il balaie désormais backend/tests (Python et JSON) et
frontend/src (ts, tsx, json) : séries hors gabarit, latitudes, longitudes et
couples sans nom de champ, en France et hors gabarit (Paris, Lyon, 0,02°).
Ses contre-exemples sont des centres-villes publics, jamais une valeur de la
flotte. Vérifié par mutation : sur les sources d'avant, trois fichiers
tombent.

Reste C-45 : la série demeure dans l'historique déjà poussé.

### 2026-09-26 — C-1 : un refus passager ne condamne plus l'endpoint principal

Deux 401 autour d'un login réussi (le serveur web du routeur qui redémarre
juste après un relogin) retenaient status.wan.connection hors du périmètre :
plus aucune requête, l'équipement « injoignable » pour la vie du processus,
et le bouton de reprise n'apparaissait pas pour ce statut.

- status.wan.connection et status.system.info ne sont jamais retenus hors du
  périmètre. Un vrai refus de droits sur eux coûte un login par cycle — un
  login qui réussit, donc hors des cinq échecs qui suspendent l'accès.
- Un complément retenu est redemandé passé dix minutes, au lieu de l'être
  jusqu'au redémarrage du serveur.

Tests écrits d'abord (rouges), puis vérifiés par mutation : sans la liste
des endpoints principaux, deux tests tombent ; sans l'expiration, un tombe.

### 2026-09-26 — C-2 : un tunnel tombé ne fait plus repasser le bandeau à « Liaison OK »

Le débit du tunnel s'éteint avec lui. paquetsParSecondeRecents rendait 0
faute de tranche renseignée (ou de bande relue) : le rôle passait à
`routes`, le verdict ne jugeait plus le bond, et dix minutes après la chute
du concentrateur le bandeau repassait au vert, tunnel toujours à terre.
Même effet au chargement de la page ou sur un échec de fetchTimeline.

- paquetsParSecondeRecents rend null quand rien n'a été observé ; un zéro
  mesuré reste un zéro.
- bondRole(routes, null) rend `inconnu` en gardant les routes annoncées :
  une absence de mesure ne fonde pas la négation « rien ne transite ».
- roleRetenu : DeviceShell garde, par équipement, le dernier rôle MESURÉ
  pour les mêmes routes ; une mesure fraîche l'emporte.
- La conséquence du verdict nomme les routes annoncées au lieu d'affirmer
  que le pair ne déclarait rien.

Le test bondRole « sans témoin de trafic → routes » encodait le défaut : il
est réécrit. Tests écrits d'abord (rouges), puis vérifiés par mutation sur
chacun des quatre points, câblage de DeviceShell compris.

### 2026-09-26 — C-3 : l'en-tête Host est contrôlé, le DNS rebinding ne franchit plus la garde

require_loopback ne juge que l'adresse TCP, et un navigateur local se
présente toujours en 127.0.0.1. Une page dont le domaine est rebasculé vers
127.0.0.1 devenait de même origine que l'API : elle lisait toutes les
routes, capture brute comprise (identifiants APN, IMEI, ICCID), et la
WebSocket l'admettait puisque son origine égalait l'en-tête Host.

- HoteDeConfianceMiddleware (HTTP et WebSocket) refuse tout Host qui n'est
  ni localhost, ni une adresse IP littérale, ni un nom déclaré dans
  ALLOWED_HOSTS. Un rebinding porte forcément un nom de domaine ; une IP ne
  se rebascule pas, ce qui garde HOST=0.0.0.0 utilisable depuis le LAN.
- La WebSocket n'admet plus « même origine » que si cet hôte est de
  confiance : la garde tient même si le middleware venait à manquer.
- conftest déclare les noms fictifs des clients de test.
- ALLOWED_HOSTS documenté (REFERENCE, .env.example).

Tests écrits d'abord, puis vérifiés par mutation : sans le middleware, sans
le contrôle de l'origine WebSocket, ou en admettant tout nom, un à huit
tests tombent.

### 2026-09-26 — Garde d'anonymisation : ruff (C416)

### 2026-09-26 — C-4 : les chemins relatifs du .env sont résolus à côté de lui, en absolu

preparer() appliquait le .env en setdefault avant ses propres chemins
absolus : un DATABASE_PATH ou un ROUTERS_CONFIG relatif (celui du dépôt, ou
de .env.example, posé à côté de l'exécutable) l'emportait. L'application le
résolvait depuis sa racine — _MEIPASS une fois gelée, effacé à la sortie —
et sauvegarder() depuis le répertoire courant : historique perdu à la
relance, aucune copie, routers.yaml jamais lu. Depuis le dépôt, lancé hors
de backend/, la base était écrite mais jamais copiée.

DATABASE_PATH, ROUTERS_CONFIG et FRONTEND_DIST relatifs sont désormais
résolus une fois, dans preparer(), et réécrits en absolu : relatifs au .env
qui les donne, ou au répertoire courant du shell qui les a posés.
Application et sauvegarde lisent donc le même fichier.

L'assertion existante `decision["base"] == "ailleurs/base.db"` encodait le
défaut : elle attend maintenant le chemin absolu. Deux tests ajoutés
(exécutable gelé avec le .env du dépôt ; chemin relatif du shell),
vérifiés par mutation.

### 2026-09-26 — C-5 : des compléments lents ne font plus jeter un relevé WAN déjà lu

Le budget de 20 s par équipement couvrait tout get_radio_status : WAN,
puis traffic, latence, tunnel, température, clients, qualité, chacun à
15 s de délai httpx. status.wan.connection lu en 0,4 s, status.traffic qui
expire, une latence à 6 s : le cycle était perdu et l'équipement dit « pas
de réponse en 20 s », cycle après cycle tant que le routeur restait chargé.

- get_radio_status(echeance) : passé l'échéance, les compléments restants
  sont abandonnés pour ce cycle (celui en cours est annulé) et les états WAN
  rendus tels quels. Contrat PeplinkClient, simulateur et doublures de test
  alignés.
- Le poller passe une échéance tirée de son budget, moins une réserve
  (3 s, ou le quart du budget) pour écrire et publier le relevé.
- L'identification et la relecture de la fiche, lectures réseau faites
  après le relevé, sont bornées par la même échéance ; une identification
  interrompue ne compte pas comme une tentative.

Tests : compléments lents côté client, échéance transmise par la boucle,
fiche lente ; chacun vérifié par mutation. Le test de la fiche lente a été
écrit après le code, les autres avant.

### 2026-09-26 — C-6 : un refus de infoType=profile n'éteint plus la supervision du tunnel

La mémoire des refus (_hors_perimetre) était indexée par le nom d'endpoint
seul. status.pepvpn sert trois lectures — peer, profile, tunnel : un refus
de profile, ou une simple capture de profil, condamnait peer et tunnel,
_supports_tunnel passait à False et la chute du concentrateur devenait
invisible pour la vie du processus.

La clé porte désormais l'infoType. Test écrit d'abord (rouge) : profile
reste retenu, peer et tunnel sont toujours lus.

### 2026-09-26 — C-7 : l'ancienneté du bond est oubliée quand le tunnel tombe

tunnel_uptime_s n'était écrit que sur une lecture de tunnel réussie et
jamais remis à None : ni la sentinelle PEER_DOWN, ni une erreur de lecture
ne l'effaçaient, et la carte affichait « Monté depuis 2 h 03 » sous
« Tunnel tombé », chiffre figé.

Remis à None à chaque passage dans _attach_tunnel ; seule une lecture de
tunnel connecté le réécrit. L'écran rend déjà « — » sur une absence.
Test écrit d'abord (rouge : 22435 au lieu de None).

### 2026-09-26 — C-8 : un épisode de latence haute ne franchit plus un trou de supervision

Les îlots se calculaient par différence de ROW_NUMBER, partitionnée par
lien, sans segment de trou : deux relevés hauts consécutifs en base
formaient un même épisode quel que soit l'écart. Un pic avant l'arrêt du
superviseur et un autre à la reprise, trois heures plus tard, devenaient
« 3 h de latence haute, 2 relevés », classé en tête par sa durée.

Un segment s'ouvre à chaque écart supérieur à _TROU_MAX_S, et les deux
ROW_NUMBER sont partitionnés par (lien, segment). Test écrit d'abord
(rouge : un épisode au lieu de deux) ; les tests de plan SQL suivent le
paramètre ajouté.

### 2026-09-26 — C-9 : la latence du lien n'est plus effacée après la première série

history(path_latency_ms), latency_series et le résumé de lien n'utilisaient
les relevés que s'ils précédaient le tout premier point de série de
l'équipement, un minimum pris sur toute sa vie. Un jour sans série (écartée
pour horloge décalée, écriture ratée) perdait ses mesures : 0 point à
l'historique, path_latency_samples = 0 sur la fiche, pour 30 relevés en
base.

Un relevé n'est désormais écarté que si la série couvre effectivement son
instant, un point à dix secondes près :
- history : NOT EXISTS sur la clé primaire de router_history, bornes en
  texte ISO pour que l'index serve ;
- latency_series rend les instants couverts de la fenêtre, et non plus le
  premier point de la vie de l'équipement ;
- _resume_lien teste la couverture relevé par relevé (bisect).

Test écrit d'abord (rouge : 0 point au lieu de 30). Deux tests existants
suivent la nouvelle forme de la série ; le test de fiche garde son
intention (les copies couvertes ne comptent pas), vérifié par mutation.

### 2026-09-26 — C-10 : la latence du lien n'est plus une mesure vieille d'une heure

parse_wan_latency remontait la série jusqu'au premier point renseigné, si
loin soit-il — le commentaire promettait l'inverse. Le poller datait cette
valeur de l'instant de lecture : un lien rétabli avec 359 points non
mesurés en fin de série affichait « 42 ms · il y a 3 s » pour une mesure
d'avant la coupure.

Seuls les six derniers points (la minute qui sépare deux lectures) sont
considérés ; au-delà, pas de valeur. Test écrit d'abord (rouge : 42 au
lieu de rien), avec ses deux bords : six points tenus, sept non.

Réserve de la relecture maintenue : on n'a pas vérifié que le firmware
publie des null pendant un trou.

### 2026-09-26 — C-11 : le calage sur la grille de 10 s ne fond plus un point sur deux

_sur_la_grille faisait round(instant / pas) * pas ; round arrondit les
demies au pair. Une série commençant à …5 s voyait 15 et 25 tomber sur 20,
35 et 45 sur 40 : 360 points pour 180 instants, l'upsert de router_history
n'en gardant qu'un — la moitié de la latence et de la radio perdue, le
reste décalé de 5 s.

floor(x + 0,5). Test écrit d'abord (rouge : 181 instants pour 360 points),
qui vérifie aussi que le calage reste stable d'une lecture décalée d'une
seconde à l'autre.

### 2026-09-26 — C-12 : chaque taux de la fiche publie et écrit son dénominateur

part() calculait chaque part de diversité sur ses propres cycles
jugeables, mais PathDiversity ne publiait qu'un `cycles` — celui des
cycles complets — que la fiche n'affichait même pas : « même station
100 % » se lisait sur 5 912 cycles quand il n'en jugeait que 1 999. La
latence du bond n'avait aucun compte, celle du lien et les sauts de
cellule n'en avaient qu'un dans un `title`, qui ne s'imprime pas — alors
que le pied du rapport promet que les taux voyagent avec leurs mesures.

- PathDiversity : site_cycles, cell_cycles, earfcn_cycles.
- SessionLink : rtt_samples.
- La fiche écrit « même station 100 % du temps sur 1 999 cycles », « 31 sur
  412 » sous chaque médiane, « 0.5 · 7 sauts ». Champs optionnels côté
  écran : un serveur d'une version en arrière n'efface rien.
- Fixtures d'écran régénérées par le rejeu (seuls les champs ajoutés
  changent).

Tests écrits d'abord (rouges) côté serveur et écran ; le test d'historique
qui vérifiait le `title` suit le texte visible.

### 2026-09-26 — C-13 : les routes locales refusent les requêtes d'une page tierce

POST /devices/{id}/reauth n'a pas de corps : un formulaire cross-site est
une requête simple, sans requête préalable CORS, et require_loopback la
laissait passer puisque le navigateur est local. Chaque envoi rejouait une
connexion avec les identifiants en mémoire — cinq suspendent l'accès admin
du routeur cinq minutes. Une balise <img> déclenchait de même GET /capture
et GET /discover.

require_loopback refuse désormais, en plus d'un client distant, toute
requête marquée Sec-Fetch-Site: cross-site, ou dont l'Origin n'est ni
listée dans CORS_ORIGINS, ni la même origine sur un hôte de confiance
(Origin: null comprise). Une requête sans Origin ni Sec-Fetch-Site — curl,
un script — n'est pas un navigateur et passe.

Tests écrits d'abord (4 rouges), avec leur pendant positif : interface
servie, serveur de développement, barre d'adresse, outil en ligne de
commande.

### 2026-09-26 — C-15 : un mot de passe numérique dans routers.yaml ne fuit plus

YAML lit un mot de passe fait de chiffres, sans guillemets, comme un
entier. RouterConfig le refusait ; le message de Pydantic porte
input_value — le mot de passe en clair dans la console —, et l'exception
remontait du lifespan : le serveur ne démarrait plus.

- Les champs d'identifiants (client_id, client_secret, username,
  password) lus comme des nombres sont rendus à leur nature de chaîne.
- Chaque entrée est validée à part : une entrée fautive est ignorée avec
  un avertissement qui nomme son rang et ses champs, jamais la valeur
  (errors(include_input=False)), et les autres équipements démarrent.

Tests écrits d'abord (rouges), mot de passe fictif absent du journal
vérifié.

### 2026-09-26 — C-16 : la cause tirée des profils n'écrase plus celle des liens

causeDuTunnel listait tous les profils non CONNECTED, même quand un autre
profil était connecté, et linkVerdict lui donnait toujours la priorité
(tunnelCause ?? describeTunnelCause). Profil A connecté avec tous ses liens
en WAN_DOWN, profil B secondaire en START : le bandeau accusait « profil
B : le routeur tente de joindre le pair » au lieu de « (WAN_DOWN) ».

causeDuTunnel se tait dès qu'un profil tient : le pair est joignable, la
cause se lit sur les liens. Le test « ne cite que les profils qui ne
tiennent pas » encodait ce cas et attendait l'accusation du profil
étranger : il est réécrit (rouge avant le correctif).

### 2026-09-26 — C-17 : le plan et le bandeau disent de la même règle si le bond porte

verdict.ts posait porteLeFlux = rôle ≠ routes ET tunnel_attendu ≠ false ;
BondPlan.tsx, rôle ≠ routes seulement. Sur un MAX HD2 dont le bond n'a
jamais tenu, le bandeau disait « jamais établi » et le plan titrait « 0 / 3
liens qui portent », affirmant que chaque paquet perdu l'était pour le
flux.

Une seule fonction, porteLeFlux(rôle, tunnelAttendu), dans bondRole.ts :
prédicat de type, pour que le switch exhaustif du verdict continue de
savoir que `routes` n'arrive pas dans sa branche. BondPlan reçoit
tunnelAttendu d'Overview.

Tests écrits d'abord (rouges) : la règle, et le plan d'un bond jamais
établi qui ne compte plus des liens « qui portent ».

### 2026-09-26 — C-18 : un état de tunnel inconnu ne donne plus « 0 / N liens qui portent »

Dès qu'un objet tunnel existait, keyFigures comptait comme porteurs les
liens in_bond — or un lien dont l'état est null a in_bond=false, alors que
le verdict pose qu'un état inconnu n'est ni porteur ni écarté. Le bandeau
disait « Liaison OK — 2 liens sur 2 connectés », le plan « 0 / 2 liens qui
portent ».

carrying vaut null quand un tunnel existe mais qu'aucun lien connecté n'en
a d'état connu ; le plan écrit alors « — » et « état du tunnel inconnu ».
Tests écrits d'abord (rouge sur le premier), avec le cas mixte qui compte
sur les liens connus.

### 2026-09-26 — C-19 : la grandeur, la fenêtre, la session et le thème choisis se voient

HistoryChart et ThemeToggle posaient la classe « active » ; la seule règle
existante est « .segmented button.is-active ». On cliquait sur « RSRP »
puis sur « 3 h » et aucun bouton ne changeait d'aspect ; HistoryChart
n'avait pas non plus d'aria-pressed.

- is-active partout, aria-pressed sur les boutons de grandeur et de
  fenêtre, type="button".
- La règle couvre aussi le sélecteur de session choisi.

Tests écrits d'abord (rouges) : les boutons choisis portent is-active et
aria-pressed, plus aucun « active », et la feuille stylise bien cette
classe.

### 2026-09-26 — C-20 : un lien désactivé par l'exploitant ne s'affiche plus comme une panne

La couleur ne dépendait que de `connected` : WanCard (pastille rouge,
« Modem déconnecté — aucune mesure disponible »), LinkTable et BondPlan
(rail rouge) traitaient un WAN désactivé comme un lien tombé, et l'on
cherchait une panne qui n'existait pas. Seule l'étiquette « désactivé » de
la table en tenait compte.

- couleurDuLien(wan) dans quality.ts : une règle pour le plan, la table
  et la carte ; un lien désactivé prend la teinte de l'inconnu.
- La carte dit « Lien désactivé par l'exploitant », sans bandeau rouge.

Reste : la bande de stationnement (Band) colore ses tranches d'après la
timeline, qui ne porte pas l'état « désactivé » ; il faudrait l'ajouter au
serveur.

Tests écrits d'abord (rouges) : la règle de couleur et la carte.

### 2026-09-26 — C-21 : une reprise d'identifiants ratée n'invite plus à recliquer

L'état de reprise n'était effacé qu'au clic suivant : après un échec,
l'écran disait encore « Nouvelle tentative autorisée : elle aura lieu au
prochain cycle » et le bouton restait actif. Chaque clic rejoue les MÊMES
identifiants et consomme l'un des cinq échecs qui suspendent l'accès admin
cinq minutes ; et « Corrigez les identifiants » n'offrait aucun moyen de
le faire.

Le bloc devient RepriseIdentifiants :
- bouton désactivé pendant la tentative ;
- échec constaté au retour du refus après un passage par « en attente »,
  ou, faute de l'avoir vu (l'écran relit l'état toutes les 15 s), quand le
  refus persiste 35 s après l'autorisation ;
- l'échec est dit, avec un lien vers la découverte pour ré-appairer.

Tests écrits d'abord : aucune reprise sans clic (la mutation F9 de C-26),
bouton désactivé, échec dit dans les deux cas, lien de correction.

### 2026-09-26 — C-22 : le rapport imprimable dit un échec de lecture comme tel

Un fetchHistory en échec donnait serie: null, imprimé « Aucune mesure sur
la session » ; un fetchEvents en échec était avalé et le journal restait
sur « Relecture de la fenêtre en cours… » ; un fetchRouter aussi. Le PDF
remis à un tiers affirmait une absence de mesure là où la requête avait
seulement expiré.

- Une courbe illisible s'imprime « Lecture impossible : la courbe n'a pas
  pu être relue », distincte de « Aucune mesure ».
- Un journal illisible : « Le journal n'a pas pu être relu : il n'est pas
  imprimé ici ».
- L'équipement illisible retombe sur « Équipement », qui ne prétend rien.
- « Imprimer » reste désactivé tant que les lectures ne sont pas finies.

Tests écrits d'abord (trois rouges) ; le test d'impression existant attend
désormais la fin des lectures.

### 2026-09-26 — C-23 : l'export CSV de session ne fige plus la scrutation

export_rows relisait toute la session et la boucle Python qui bâtissait le
CSV tournait dans le handler async, sans to_thread ni plafond : ~680 ms de
Python synchrone et 111 Mo de pic pour dix heures, des dizaines de
secondes pour trois semaines — pendant lesquelles la scrutation était
figée et les budgets des équipements expiraient.

- StreamingResponse : l'en-tête, puis un lot après l'autre.
- Lots de 5 000 relevés relus par clé (ts, wan_id, id) : une requête courte
  par lot, aucun curseur tenu ouvert sur l'unique connexion pendant qu'un
  client lent télécharge.
- Mise en forme de chaque lot par asyncio.to_thread.
- Le rangement des relevés sans code réseau ne relit plus toute la session :
  les couples (libellé, PLMN) distincts suffisent.

Test écrit d'abord (rouge) : lots de 2 sur la session de test, fichier
identique octet pour octet, mise en forme hors du fil de la boucle.

### 2026-09-26 — C-24 : un lancement refusé ne touche plus aux sauvegardes

main() appelait preparer(), donc sauvegarder() et l'élagage à deux copies,
avant port_disponible(). Instance vivante en WAL : le -wal existe, la
déduplication est sautée, et chaque double-clic refusé créait une copie de
l'état courant en supprimant la plus ancienne. Deux suffisaient à effacer
les copies d'avant un incident.

preparer(sauvegarde=False) pose l'environnement sans copier ; main() copie
la base une fois le port acquis. Test écrit d'abord (rouge) : port pris,
sortie 2, aucun appel à sauvegarder().

### 2026-09-26 — C-25 : les scripts de capture Windows parlent au firmware comme le client

Le .cmd et le .ps1 envoyaient client_id, client_secret et grant_type, puis
?access_token= ; le client consigne, mesures à l'appui, que seuls clientId,
clientSecret et accessToken sont acceptés. Un technicien qui suivait la
documentation archivait des « Client not found » à la place des captures.
Et le .cmd prenait le mot de passe en argument, visible dans l'historique
et la liste des processus.

- Noms de champs et paramètre de jeton alignés sur le client.
- Mot de passe et secret du client demandés sans écho (Read-Host
  -AsSecureString) ; dans le .cmd, le corps de la requête passe à curl par
  un fichier temporaire effacé aussitôt, jamais par la ligne de commande.
- REFERENCE.md suit le nouvel usage.

Tests écrits d'abord (six rouges) sur le TEXTE des scripts, contre le
contrat mesuré du client. Les scripts n'ont pas été exécutés : ni Windows
ni routeur ici. À essayer sur un poste Windows avant de s'y fier.

### 2026-09-26 — C-26 : la règle du refus d'identifiants est gardée par des tests

Aucun test n'appelait POST /reauth, et test_reauth_lifts_the_lock posait
auth_blocked à la main. Les mutants B15 (la route n'appelle plus
allow_new_credentials), B16 (auth_blocked n'est plus remis à faux), B3 et
B4 (le verrou ne s'arme plus sur un login ou un jeton en stat: fail)
survivaient — sur la règle qui empêche l'outil de suspendre lui-même
l'accès admin du routeur au cinquième échec.

tests/test_reauth.py :
- la route, montée en ASGI : verrou levé, identifiants rouverts, statut
  « en attente », 404 sur un inconnu ;
- par mode (jeton, session) : quatre relevés après un refus = un seul POST
  d'authentification ; une reprise explicite = un seul de plus.

Rejoué : B3, B4, B15 et B16 font désormais tomber leur test. Le mutant F9,
côté écran, est tenu depuis C-21 par RepriseIdentifiants.test.tsx.

### 2026-09-26 — C-27 : un test garantit que le collecteur n'interroge jamais config.*

La famille config.* restitue la clé pré-partagée SpeedFusion, les
identifiants APN et la clé Wi-Fi en clair. Seule la liste de capture était
gardée ; le rejeu tenait endpoints_demandes mais n'y vérifiait que
count <= 2 : un config.* demandé à chaque cycle, sur deux cycles, passait.

Le rejeu de bout en bout (MBX, Dome, HD2) vérifie désormais qu'aucune
lecture de cette famille n'a été demandée, et que la liste n'est pas vide.

Vérifié par mutation : un config.wan.connection lu dans un complément,
erreur avalée, faisait passer toute la suite ; il fait maintenant tomber ce
test sur les trois châssis.

### 2026-09-26 — C-28 : les règles de dénominateur sont figées par des tests

Cinq mutants survivaient sur les règles « un taux voyage avec son
dénominateur » et « une absence n'est pas un zéro » :
- F4 : un taux à dénominateur null redevenait publiable sur la courbe ;
- F5, F7, F8 : le texte de disponibilité de la fiche (cycles exclus faute
  de lecture, tunnel jamais porté, « au moins deux » sur un seul lien) —
  tous les tests passaient availability: null ;
- B11 : un compteur de paquets absent rendu comme un dénominateur à 0.

Un test par mutant (HistoryChart, SessionSheet, test_storage) ; rejoués,
les cinq tombent. Le contrôle de bout en bout des pertes est dit pour ce
qu'il est : les rejeux relisent deux fois la même capture et ne produisent
aucun taux, il ne prouve donc rien seul, et il renvoie aux tests qui
tiennent la règle.

### 2026-09-26 — C-29 : une page non JSON au login ne fait plus perdre le relevé

Les deux authentifications appelaient resp.json() sans filet. Un routeur
en fin de démarrage qui sert une page HTML pile quand la session expire
sur un complément levait un JSONDecodeError — ni RouterApiError ni
httpx.HTTPError — qui traversait les except des compléments : relevé
perdu, équipement « injoignable » avec un message de parseur.

_json_d_authentification convertit en RouterApiError, sans poser
_credentials_refused : c'est une panne passagère, pas un refus. Tests
écrits d'abord (rouges), en jeton comme en session.

### 2026-09-26 — C-30 : une réponse sans température ne coûte plus un POST par cycle

_temperature_at n'avançait que si une valeur avait été lue. Un firmware
qui répond stat: ok sans température ajoutait un POST par modem à chaque
cycle, indéfiniment, ce qui rapprochait du budget de C-5.

Toute réponse lue marque la minute ; seule une panne (exception) ne la
marque pas, comme avant. Test écrit d'abord (rouge : 10 POST sur 5 cycles).

### 2026-09-26 — C-31 : le journal ne fait plus attendre les écritures du collecteur

events() enchaîne cinq balayages fenêtrés — 1,75 à 1,84 s sur la plus
longue session de la copie — relus toutes les 30 s par l'écran, sur
l'unique connexion : les écritures du poller attendaient derrière, sur le
même thread aiosqlite.

Le dépôt ouvre une seconde connexion, en lecture seule (mode=ro), sur
laquelle events() lit ; la base est en WAL, un lecteur n'y bloque pas
l'écrivain. Une base :memory: garde la connexion unique.

Ce correctif isole le coût, il ne le réduit pas : les 1,8 s restent
dépensées, ailleurs. Fusionner les passes ou ne recalculer que la queue de
fenêtre reste possible si le coût lui-même gêne.

Test écrit d'abord (rouge) : aucune requête du journal ne passe par la
connexion de l'écrivain, et le lecteur voit les relevés validés.

### 2026-09-26 — C-32 : l'alerte d'horloge ne désigne plus le routeur à tort

L'écart est mesuré contre l'horloge du PC, et le texte accusait le routeur
sans confronter les équipements : un PC sans NTP, trois minutes d'avance,
et tous les routeurs étaient déclarés décalés — historique et journal
coupés.

Chaque alerte d'horloge est désormais requalifiée au vu de tous les
équipements : le même écart (à 30 s près) sur au moins deux d'entre eux
désigne l'horloge du poste ; sinon, le texte dit un écart entre le routeur
et ce poste, sans désigner personne. Le titre à l'écran devient « Horloges
du routeur et du poste en désaccord ».

Test écrit d'abord (rouge) : un seul équipement ne désigne personne ;
deux équipements au même écart désignent le poste, y compris dans
l'alerte déjà ouverte du premier.

### 2026-09-26 — C-33 : le décompte des clients du LAN est daté, et s'efface s'il vieillit

lan_clients n'était remplacé que sur une lecture réussie, et LanClients
n'était pas daté : « 12 appareils actifs » lu le matin restait affiché le
soir pendant que la lecture échouait.

- LanClients porte read_at, l'instant de sa lecture.
- Deux intervalles (2 min) sans lecture réussie : le décompte est effacé ;
  un échec isolé garde la dernière lecture, datée.
- La liste fermée des champs publiés (aucune identité) s'ouvre à read_at ;
  fixtures d'écran régénérées, type d'écran optionnel.

Test écrit d'abord (rouge).

### 2026-09-26 — C-34 : un retrait pendant la lecture ne ressuscite plus les alertes

_lire_journal_routeur réécrivait _alerte_admin après les awaits du journal
et de router_events, et _verifier_horloge _alerte_horloge après ceux de la
fiche, sans vérifier _still_supervised. Un forget() tombé entre-temps
ressuscitait l'alerte ; après un ré-appairage, le nouveau record héritait
de celle de l'ancien.

Une garde _still_supervised avant chaque écriture ; elle n'efface rien
(l'identifiant peut désigner déjà le nouveau record). Tests écrits
d'abord (deux rouges).

### 2026-09-26 — C-35 : une fiche partielle ne compte que les manœuvres de sa fenêtre

router_events recevait started_at et last_seen, et non la fenêtre relue
(lu_de, lu_a). Sur une session de trois jours dont la fiche ne porte que
sur les 24 dernières heures, une bascule du premier jour y était comptée.

La fenêtre relue est passée, comme à la série de latence et au RSRP
comblé. Test écrit d'abord (rouge : 2 au lieu de 1).

### 2026-09-26 — C-36 : des bornes au début du calendrier rendent 400, plus 500

?since=0001-01-01…&until=0001-01-01T12:00Z faisait déborder _plafonnee
(fin − 24 h) et la fenêtre de cause (début − 15 s) : OverflowError, donc
500 sur /timeline et /events.

_en_utc refuse toute borne antérieure à l'an 2000 — bien avant tout relevé
possible — avec un 400 qui parle de calendrier, comme les autres bornes
refusées. L'autre bord reste ouvert : le test existant tient l'an 9999 pour
une fin valide. Tests écrits d'abord (rouges : 500).

### 2026-09-26 — C-37 : l'export complet et les équipements connus sont réservés au poste

L'export complet (SSID, BSSID, position exacte) et /devices/known (les
séries appairées) n'avaient pas require_loopback, alors que le serveur de
développement écoute sur le LAN et que HOST=0.0.0.0 est documenté : un
téléphone du réseau téléchargeait l'un et l'autre.

- /devices/known : require_loopback.
- export.csv?complet=true : require_loopback ; l'export « à partager »
  reste ouvert, c'est son objet.
- README : la WebSocket n'est pas sous /api, les exports sont listés, et
  les routes réservées au poste sont marquées.

Test écrit d'abord (rouge) : un client du LAN reçoit 403 sur les deux,
200 sur l'export à partager.

### 2026-09-26 — C-38 : le rapport à partager n'imprime plus le libellé de la session

s.label était affiché sans condition, sous la mention « position arrondie
à ~100 m » — or une session nommée d'après l'adresse du client, ou
« Parking nord », dit le lieu que l'arrondi prétend taire.

Le libellé n'apparaît qu'en version complète, et la mention de la version
à partager le dit retiré. Le test existant affirmait que le libellé
s'imprimait en version à partager : il attend désormais l'inverse, et le
libellé en version complète (rouge avant le correctif).

### 2026-09-26 — C-39 : une redondance « active » ne se publie plus sur un keepalive

L'état 'on' était rendu dès repair > 0, sans le seuil PAQUETS_CONCLUANTS
qui gouverne déjà l'état 'off' : 3 paquets réparés pour 5 transportés
titraient « 37,5 % de la capacité part en redondance ».

En deçà de 1 000 paquets, l'état reste 'unknown'. Test écrit d'abord
(rouge), avec son pendant concluant.

Le second point du constat — la tranche en cours comptée pour une tranche
pleine dans le témoin de débit — n'est pas corrigé : la bande se termine à
l'instant de la requête, sa dernière tranche est donc écoulée ; l'écart
réel est le délai jusqu'au dernier relevé, une période de scrutation sur
une tranche de 144 s, de l'ordre de 3 %. Non chiffré par la relecture,
jugé négligeable ici.

### 2026-09-26 — C-40 : diversité de trajet — un opérateur par station, une porteuse inconnue ne réunit pas

Les clés de regroupement ne contenaient pas le PLMN, et une porteuse
manquante devenait « ? » : deux SIM de deux opérateurs aux numéros eNB
égaux étaient « même station », deux liens de porteuse inconnue « même
cellule ».

À l'écran (diversity.ts) comme sur la fiche (sessions.py), même règle :
- la station et la cellule sont préfixées par le PLMN ;
- une porteuse inconnue ne départage pas, mais ne réunit personne : la
  clé devient propre au lien.

Tests écrits d'abord (rouges) des deux côtés. Deux tests de la fiche
reposaient sur l'ancienne règle — l'un l'affirmait explicitement : ils
sont alignés et le disent.

### 2026-09-26 — C-41 : au changement de grandeur, l'ancienne série n'est plus jugée par la nouvelle

data n'était pas vidé quand la grandeur, la fenêtre ou la session
changeait : pendant le chargement, l'ancienne série restait tracée sous le
nouveau libellé, et les règles de la nouvelle grandeur s'appliquaient à
ses points — passé de SINR à Perte, le pied annonçait des points
« retirés » qui n'existaient pas.

La série lue porte la clé de sa requête (équipement, lien, grandeur,
fenêtre, session) ; une série d'une autre clé n'est ni tracée ni comptée.
Test écrit d'abord : rouge sans le correctif, vert avec.

### 2026-09-26 — C-42 : quatre libellés d'état ne contredisent plus l'état réel

- Overview : « l'équipement ne répond pas » pour tout statut autre que
  connecté ou en attente — y compris des identifiants refusés, qui
  envoyaient chercher une panne de réseau. Le refus est dit comme tel.
- Masthead : « identité en cours de lecture » même quand aucune lecture
  n'aura lieu ; injoignable ou refusé, « identité non lue ».
- Alerts : la note affirmait « Trois règles » ; le fichier en traduit six.
  Elle les compte, et dit d'où viennent les trois dernières.
- VerdictBanner : la durée partait du montage de la page et se lisait
  « 1 min dans cet état » pour une panne de 2 h ; elle dit « observé
  depuis ».

Tests écrits d'abord (trois rouges) ; le test du chronomètre suit le
nouveau libellé.

### 2026-09-26 — C-43 : le mode client API reçoit l'avertissement « en clair » et la case TLS

L'avertissement pour un hôte http:// et la case « Vérifier le certificat
TLS » ne vivaient que dans la branche du compte admin : en mode client
API, le secret partait en clair sans que rien ne le signale, et
verify_ssl restait à faux faute de commande.

Le bloc sort des deux branches et nomme ce qui part en clair selon le
mode (mot de passe ou secret du client). Tests écrits d'abord (rouges).

### 2026-09-26 — C-44 : badge « support expiré » neutre, « principale » par bande et canal

- DeviceCard : le badge « support expiré » suivait un état stable
  (care_plan_active false) en ambre, allumé à chaque ouverture — contre
  la règle « pas d'avertissement permanent ». Il devient neutre ; le
  détail reste dans son infobulle.
- BandTable : la porteuse principale se reconnaissait à son seul code de
  bande ; en agrégation intra-bande, deux lignes étaient « principale ».
  Elle se reconnaît désormais à la bande ET au canal (wan.earfcn) ; sans
  canal, on ne départage que si la bande est unique.

Tests écrits d'abord (trois rouges). Le cas matériel d'agrégation
intra-bande n'a pas été observé : c'est la lecture du code qui le fonde.

### 2026-09-26 — C-46 : des tests qui ne dépendent plus de la machine, et client.ts éprouvé

- Garde réseau autouse (conftest) : toute connexion TCP hors de la boucle
  locale échoue tout de suite, et les variables de proxy sont retirées
  pendant les tests — sans quoi httpx joignait le proxy local, qui joignait
  l'hôte. test_discovery sondait réellement 203.0.113.7 : vérifié au
  journal du proxy du conteneur, plus aucune tentative depuis. Le garde a
  ses propres tests (il refuse, la boucle locale passe, aucun proxy).
- Rejeu : l'adresse du poste vers le routeur (ip_locale_vers, un vrai
  connect UDP) entrait dans l'export de l'écran ; elle est figée pendant
  le rejeu, comme l'horloge, et restaurée ensuite.
- exporter_rejeu : TemporaryDirectory au lieu de mkdtemp, qui laissait un
  dossier à chaque passage de la suite.
- frontend/src/api/client.test.ts : la reprise du flux et son recul, l'arrêt
  sur un code applicatif, une trame illisible, la fermeture demandée, et la
  distinction refus d'identifiants / panne à l'appairage. Trois mutants de
  ma main, dans l'esprit de F14 à F16 (pas ceux de la relecture, que je
  n'ai pas), font tomber un test chacun.

### 2026-09-26 — C-47 : démarrage — base illisible dite, second double-clic utile, HOST ouvert

- sauvegarder() n'interceptait pas sqlite3.DatabaseError : après un
  arrachage de clé, « file is not a database » dans une trace, et la
  console de Windows se refermait. Le lanceur dit la base illisible, liste
  les copies .bak de la plus récente à la plus ancienne et comment repartir
  de l'une d'elles, attend Entrée une fois gelé, et sort en 3. Une
  sauvegarde ratée ne laisse plus de copie vide qui se ferait passer pour
  la plus récente (connexions fermées explicitement : sous Windows, une
  copie ouverte ne s'efface pas).
- Port pris : le lanceur sortait sans rien ouvrir ; il ouvre désormais
  l'instance qui tourne.
- L'adresse ouverte était figée sur 127.0.0.1 alors qu'uvicorn écoute sur
  HOST ; c'est HOST qui s'ouvre, le joker restant sur la boucle locale.

Tests écrits d'abord (trois rouges).

### 2026-09-26 — C-48 : la CI n'accorde l'écriture qu'au job de publication

contents: write était posé au niveau du workflow : chaque job de
construction le portait, et checkout persistait le jeton pendant npm ci et
pip install (dépendances en >=). Une dépendance PyPI compromise pouvait
pousser sur le dépôt ou modifier une Release.

- contents: read au niveau du workflow, contents: write sur publier seul
  (action-gh-release lit le jeton par son entrée, pas par .git/config).
- persist-credentials: false sur les deux checkout.

Test écrit d'abord (trois rouges), qui lit le workflow en YAML. Non
exécuté sur GitHub d'ici : à confirmer à la prochaine étiquette.

### 2026-09-26 — C-49 : REFERENCE décrit le même lancement que le README et l'exécutable

REFERENCE faisait installer Python 3.11 et lancer uvicorn directement —
donc en mode simulateur (le défaut de config.py) et sans aucune sauvegarde
de la base —, quand le README et l'exécutable passent par lancer.py, en
mode routeur, avec Python 3.12.

- Déploiement de terrain : lancer.py, et ce qu'il fait.
- Développement : uvicorn --reload reste, et dit qu'il tourne en
  simulateur et sans sauvegarde, avec la variable pour en sortir.
- Python 3.12 partout ; HOST=0.0.0.0 plutôt que --host, que lancer.py lit.

Tests écrits d'abord (trois rouges) sur le texte de la référence.

### 2026-09-26 — Action : l'exécutable Windows se construit de nouveau (fins de ligne)

Le job Windows de l'action échouait depuis la revue du 24/09 à l'étape de
l'interface : theme.test.ts cherche « @media print {\n  :root » dans
index.css, et le runner Windows extrait le dépôt en CRLF. Les deux Mac se
construisaient ; l'exécutable Windows non (runs 36182247371 et
36263315823, même annotation).

La feuille lue par le test est ramenée à LF. Pas de .gitattributes
global : les scripts .cmd doivent rester en CRLF sous Windows.

Vérifié par un checkout simulé en CRLF (core.autocrlf=true) : le test
tombe sans le correctif, et les deux suites passent avec.

### 2026-09-26 — Windows : chemins du lanceur et URI de la connexion de lecture

Le job Windows de l'action échoue à la suite serveur, sans détail lisible
d'ici (journaux servis par un domaine que le proxy du conteneur refuse).
Deux défauts plausibles, introduits cette semaine, corrigés :

- lancer.py (C-4) : resolve() développe sous Windows les noms courts
  (RUNNER~1) et suit les liens ; abspath garde le chemin tel qu'écrit.
- db.py (C-31) : la connexion de lecture s'ouvrait sur « file:C:\…?mode=ro »,
  que SQLite ne lit pas sous Windows. Path.as_uri() rend « file:///C:/… »,
  espaces encodés. Ce défaut aurait aussi empêché l'exécutable Windows de
  démarrer.

Et l'action transforme désormais chaque échec pytest en annotation, pour
que le prochain échec se lise par l'API.

### 2026-09-26 — Windows : garde de port sur le joker, et un test qui fermait mal sa base

Deux défauts que le job Windows de l'action cachait depuis le 24/09 — il
s'arrêtait à l'interface avant d'atteindre la suite serveur :

- port_disponible : sous Windows, le bind strict sur 0.0.0.0 passe même
  quand le port écoute déjà sur 127.0.0.1 ou sur l'adresse du LAN ; deux
  instances y coexisteraient. Pour le joker, sous Windows, une connexion
  vers ces deux adresses tranche (adresse du LAN trouvée par un connect UDP,
  sans résolution de nom). Deux tests tombaient.
- test de sauvegarde : le `with` de sqlite3 valide sans fermer ; sous
  Windows la copie restait ouverte et ne se renommait pas (WinError 32).
  closing().

Le garde réseau des tests laisse passer les adresses de la machine
elle-même (celles auxquelles on peut se lier) : le lanceur sonde l'adresse
du poste, ce n'est pas le réseau.

### 2026-09-26 — Windows : la garde de port couvre aussi l'écoute sur le joker

Sous Windows, un bind strict sur 127.0.0.1 passe même quand le port
écoute sur 0.0.0.0 — l'inverse du cas corrigé juste avant. Le commentaire
affirmait que le joker fait échouer le bind d'une adresse précise « sur les
trois systèmes » : faux sous Windows, vu dans l'action. Sous Windows, une
connexion tranche désormais pour toute adresse : l'adresse visée, ou
celles du poste pour le joker.

Les deux tests d'échéance de C-5 prennent un budget de 4 s (1 s de
réserve) : à 1 s, les 0,25 s de réserve ne suffisaient pas au runner
Windows pour écrire le relevé.

### 2026-09-26 — CHANGELOG : section v0.1.1, texte de la Release

### 2026-09-26 — Bande : un WAN désactivé n'est plus peint en panne (C-20, limite connue)

Le relevé d'adieu d'un WAN désactivé est écrit `connected = 0` : la tranche
qui le contenait se lisait comme un lien tombé, et la bande la peignait du
rouge d'une défaillance — le choix de l'exploitant dessiné comme une panne.

Chaque tranche compte désormais ses relevés d'adieu (`disabled`), reconnus
par leur message, `STATUT_DESACTIVE`, seule écriture partagée entre le
collecteur et la base. Une tranche dont tous les relevés tombés sont des
désactivations prend le fond neutre ; un seul relevé tombé d'une autre cause
suffit à la garder en panne. Fixtures d'écran régénérées.

### 2026-09-26 — Témoin de trafic : la dernière tranche compte pour sa durée écoulée (C-39)

Sur une fenêtre courte, la largeur des tranches est au plancher de la
cadence et la dernière s'arrête à la borne de fin : cent secondes font dix-
neuf tranches de 5,4 s, dont la dernière dure 2,8 s. Ses paquets étaient
divisés par un pas entier — 28 paquets en 2,8 s lus comme 5,2 par seconde,
sous le seuil de dix qui dit que le bond porte.

La durée observée compte désormais la dernière tranche pour ce qu'elle
couvre ; une borne incohérente la laisse entière.

### 2026-09-26 — Journal : îlots de latence en une passe, pas de relecture onglet caché (C-31)

Le coût du journal était isolé sur sa connexion, pas réduit. Deux gains :

- Les îlots se trouvaient par deux ROW_NUMBER sur un segment
  d'observation, soit deux tris de toute la fenêtre de plus. Un îlot s'ouvre
  sur un relevé haut dont le précédent ne l'est pas — NULL compris — ou
  appartient à une autre période observée ; son rang est le compte des
  ouvertures. 700 → 480 ms sur dix heures à cinq liens, mêmes îlots ; le
  plan est gardé par un test, et le relevé de hauteur inconnue qui ferme un
  îlot aussi (la mutation « = 0 » survivait sans lui).
- L'écran ne relit plus bande ni journal quand l'onglet est caché — le cas
  courant d'un stationnement de plusieurs heures — et reprend au retour.

Essayé et écarté : lire les voisins du brassage dans une seule fenêtre
(503 → 488 ms, du bruit).

### 2026-09-26 — Action : toutes les étapes sous Node 24

Le run de v0.1.1 annonçait le retrait de Node 20 pour checkout@v4,
setup-node@v4 et setup-python@v5. Passage aux dernières majeures, toutes
en `runs.using: node24` (lu dans leur action.yml) : checkout v7,
setup-node v7, setup-python v7, upload-artifact v7, download-artifact v8,
action-gh-release v3. Chaque entrée employée existe toujours ; les ruptures
annoncées (ESM, cache npm automatique, empreinte vérifiée au
téléchargement) ne touchent pas ce workflow. Un test garde le plancher.

### 2026-09-30 — Recette sur le Mac, face à un MAX HD2 réel

Clone neuf à v0.1.1, base recopiée de l'ancien poste (6 stationnements,
~99 000 relevés, lue en `mode=ro`). Python 3.12.7, Node 24.19. Suite backend
et interface vertes ; `test_a_port_bound_but_not_yet_listening_is_refused`,
rouge dans le conteneur cloud, passe sur le Mac. C-31 vérifié en simulant
l'onglet caché : aucune relecture de bande ni de journal pendant quatre
cadences, relecture immédiate au retour. C-20 non éprouvé (aucun WAN
désactivé ce jour-là).

Écarts constatés, et corrigés :

- **Tranches vides sur une fenêtre couverte.** Le plancher de largeur valait
  UNE période lissée ; sur le HD2 en HTTP, un écart sur cinq frôle le double
  de la médiane (5,4 s, jusqu'à 14,6 s). Jusqu'à 18 % de la fenêtre se disait
  « sans relevé », et chaque trou coupait en deux un bond resté à un lien sur
  trois : « redondance réduite 24 fois » en vingt minutes d'un état immobile.
  Plancher porté à deux périodes ; sur une copie de la base, 0 tranche vide.
- **Stationnements passés sans chemin** pour un équipement éteint : leur seul
  accès était l'historique d'un lien, qu'on n'atteint pas sans relevé. L'état
  « sans mesure » (vue d'ensemble et détail d'un lien) les liste, vers leur
  rapport.
- **« Première mesure en cours… »** sous « Équipement injoignable » dans le
  détail d'un lien : la correction de la vue d'ensemble n'y était pas. Les
  deux écrans partagent désormais `SansMesure`.
- **Bandeau contradictoire** sans relevé en mémoire : « les valeurs affichées
  datent d'avant » sur un écran vide, et « jamais mesuré » pour un équipement
  dont la base garde des stationnements. La demi-phrase tombe quand rien
  n'est affiché ; le tampon dit « aucun relevé depuis le lancement ».
- **Ouverture sur un équipement éteint** : `#/` menait au premier réel du
  fichier de flotte, injoignable, plutôt qu'au HD2 appairé qui répondait.
- **« 5 seul sur 3 »** se lisait comme une fraction : « lien 5 seul, sur 3
  liens ».
- **Tests et base de terrain** : avec un `backend/.env` en place, tout test
  important `app.main` en lisait la configuration, et le chemin par défaut
  est de toute façon `data/samples.db`. Aucun n'y écrivait ; `conftest.py`
  écarte désormais le `.env` et pose une base temporaire.
- **Pas d'icône d'onglet** : `favicon.ico` en 404 à chaque chargement. Une
  icône SVG neutre (barres de signal, sans couleur d'état) est posée dans
  `src/`, pas `public/` : Vite lui donne un nom à empreinte, et le serveur
  garde un an en cache tout ce qui n'est pas du HTML.

Laissé en l'état, à trancher :

- Un lien hors du bond dès le premier relevé n'entre pas au journal : il ne
  liste que des transitions. L'état est dit sur la ligne du lien et par
  l'alerte.
- `backend/.env` n'était pas dans le clone neuf (ignoré par git, à recopier).
- `StarletteDeprecationWarning` : `httpx` avec le client de test est déprécié
  au profit de `httpx2`.

### 2026-10-17 — Alertes : les résolues se masquent

Un bouton « Masquer » retire du panneau les alertes RÉSOLUES — le panneau
« Alertes récentes » disparaît, ou seules ses lignes résolues quand une
alerte est encore en cours (« Masquer les résolues »). Une alerte en cours
ne se masque jamais : elle demande une décision. Une alerte nouvelle n'a pas
été vue et se montre. Le choix tient pour la session du navigateur
(`sessionStorage`), pour qu'un retour depuis le détail d'un lien ne le
défasse pas ; un stockage refusé ne casse rien.

### 2026-10-17 — v0.1.2 : identifiants au trousseau, état d'ouverture au journal

- **Coffre** (`app/coffre.py`, paquet `keyring`) : les identifiants vont au
  trousseau du système quand « Retenir sur ce poste » est coché (par défaut),
  une entrée par numéro de série, sous le service `peplink-supervision`. Ils
  ne sortent ni par l'API — `/api/devices/known` ne publie qu'un booléen
  `credentials_stored` — ni par un journal. Au lancement, une tentative par
  équipement retenu, en tâche de fond ; `POST /api/devices/known/{serie}/reconnect`
  pour l'écran (401 refus, 502 injoignable, 409 rien de retenu). Décoché ou
  équipement retiré : entrée effacée. Trousseau absent : comportement d'avant.
  `/api/health` publie le trousseau employé (`coffre`) ; l'exécutable embarque
  les backends de `keyring` (points d'entrée et métadonnées) et le vérificateur
  exige un trousseau vu sur macOS et Windows. Les tests imposent le backend
  `fail` : ils ne touchent jamais le trousseau du poste.
- **Journal** : un lien debout et hors du bond au PREMIER relevé de la fenêtre
  n'avait pas de bascule, donc pas d'épisode — l'alerte le disait, le journal
  non (vu le 30/09). Il entre désormais, daté du premier relevé, avec
  « <mot du routeur> · dès le premier relevé ». Lu sur la connexion du journal
  (C-31). La fixture rejouée du HD2 (pair injoignable) en gagne trois lignes.
- **httpx2** : le client de test de Starlette 1.7 l'attend ; l'application
  parle toujours aux routeurs avec `httpx`. Plus d'avertissement.
- **Cases à cocher du formulaire d'appairage** : `.pairing input` leur donnait
  les 190 px d'un champ de saisie, la coche flottait loin de son libellé —
  vu sur « Retenir sur ce poste », et déjà vrai pour « Vérifier le certificat
  TLS ». Éprouvé en service : identifiants retenus à l'appairage, puis le HD2
  reconnecté seul 5 s après le redémarrage du serveur, sans saisie.

### 2026-10-17 — Recul sur équipement injoignable

La piste ouverte depuis l'exploration de septembre. La pause d'un équipement
« injoignable » double à chaque échec consécutif, de la cadence jusqu'à
`_RECUL_MAX_S` (30 s), et revient à la cadence au premier relevé abouti ; un
refus d'identifiants n'y entre pas (il a son propre arrêt). La pause se
découpe en tranches d'une cadence et s'interrompt si l'équipement n'est plus
celui du registre : un réappairage en plein recul est repris en trois
secondes, pas en trente. `forget()` efface le compte. Six tests, dont deux
mutations vérifiées (pause fixe, pause d'un seul tenant). En service, sur le
MBX injoignable : période lissée 3,9 → 6,4 → 11,7 s en une minute, le HD2
voisin à sa cadence.

### 2026-10-17 — C-20 éprouvé en service : la désactivation n'est plus une coupure

L'exploitant a désactivé la SIM 4 du HD2 à 13:51 et l'a réactivée à 13:52.
Base : relevé d'adieu « désactivé » à 13:51:23, aucun relevé jusqu'à 13:52:14
(la limite connue), « Connecting... », « Obtaining IP Address... », retour à
13:52:34 ; le journal du routeur a tracé les deux gestes (« Ordre des WAN
réévalué » à 13:51:11 et 13:52:06). La bande tenait : la tranche de l'adieu
est neutre. Mais le journal écrivait « Coupure — 1 min » sur un rail rouge, au
premier rang de gravité, et le compteur de coupures de la table comme la fiche
de session comptaient la désactivation. Elle a désormais son genre,
« désactivation » — « Désactivé par l'exploitant », gris, gravité d'un
signalement, durée fermée par le retour — et n'entre plus dans aucun compte de
coupures. Relu en service après redémarrage : la ligne de 13:51:23 est grise,
la SIM 4 compte 0 coupure. Reste dit tel quel : la tranche « Connecting... »
de la réactivation, quelques secondes, est peinte comme un lien tombé — le
lien ne l'est pas encore revenu.

### 2026-10-17 — Douze améliorations, suivies dans AMELIORATIONS-2026-10-17.md

Tour de l'outil sur le HD2 réel, douze propositions acceptées en bloc et faites
dans l'ordre A-11, A-12, A-6, A-8, A-7, A-9, A-10, A-5, A-2, A-1, A-3, A-4 —
le détail, la relecture en service et ce qui n'est tenu que par les tests sont
dans `docs/AMELIORATIONS-2026-10-17.md`. Deux points restent à voir en vrai :
une notification du système (A-2) et un équipement qui s'allume après l'outil
(A-1). Relu en service, A-4 annonçait onze trous dont trois expliqués par la
désactivation et deux d'un même redémarrage : fusionnés et tus, il en reste six.
