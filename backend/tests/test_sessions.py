"""Fiche d'une session : ce qui rend deux stationnements comparables."""

from datetime import datetime, timedelta, timezone

from app.models import Session
from app.sessions import _percentile, build_summary

T0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


def _session() -> Session:
    return Session(
        id=1, router_id="mbx", started_at=T0, last_seen=T0 + timedelta(hours=1), samples=0
    )


def _ligne(wan, i, **over):
    """Une ligne de `session_rows`, aux MÊMES clés que la requête réelle.

    `enb_id` en fait partie : la fiche lit désormais la station décomposée par
    `_split_eci` à l'analyse, au lieu de la refabriquer par `cell_id >> 8` sans
    le garde-fou qui refuse un NCI 5G SA. Un faux qui omettrait la colonne ferait
    passer un test que la production fait échouer — c'est la leçon du faux poller
    qui déclarait `forget` asynchrone.
    """
    base = dict(
        wan_id=wan, ts=(T0 + timedelta(seconds=5 * i)).isoformat(), connected=1,
        tx_kbps=0.0, rx_kbps=0.0, rtt_ms=None, rsrp=None, cell_id=None, enb_id=None,
        earfcn=None, tunnel_state=None,
        uptime_s=1000 + 5 * i, site_lat=None, site_lon=None, kind="cellular", operator="Orange",
        plmn="208-01", status_message=None, path_latency_ms=None,
    )
    base.update(over)
    # La station suit la cellule, comme le parseur la calcule — sauf si l'appelant
    # la pose lui-même (le cas 5G SA, où elle vaut None sur une cellule connue).
    if base["enb_id"] is None and base["cell_id"] is not None and "enb_id" not in over:
        base["enb_id"] = int(base["cell_id"]) >> 8
    return base


def test_percentile_separates_a_steady_link_from_one_that_spikes():
    """C'est tout l'intérêt : deux médianes identiques, deux liens différents."""
    regulier = [30.0] * 99 + [35.0]
    capricieux = [30.0] * 99 + [1929.0]
    assert _percentile(regulier, 0.50) == _percentile(capricieux, 0.50) == 30.0
    assert _percentile(regulier, 0.90) == 30.0
    assert _percentile(capricieux, 0.90) == 30.0
    assert max(capricieux) == 1929.0


def test_volumes_are_integrated_over_the_real_intervals():
    """Le routeur ne publie qu'un cumul depuis son installation : il faut intégrer."""
    # 1 000 kbit/s pendant 10 intervalles de 5 s = 50 000 kbit = 6,25 Mo.
    lignes = [_ligne("4", i, tx_kbps=1000.0) for i in range(11)]
    fiche = build_summary(_session(), lignes)
    assert fiche.links[0].mb_up == 6.2
    assert fiche.mb_up == 6.2


def test_a_supervision_gap_does_not_invent_megabytes():
    """Prolonger le dernier débit connu sur un trou inventerait du trafic.

    Le superviseur s'est arrêté cinq minutes : le lien a peut-être continué, mais
    on n'en sait rien. Compter comme si le débit s'était maintenu fabriquerait
    des mégaoctets qui n'ont jamais circulé.
    """
    lignes = [
        _ligne("4", 0, tx_kbps=1000.0),
        dict(_ligne("4", 1, tx_kbps=1000.0), ts=(T0 + timedelta(minutes=5)).isoformat()),
        dict(_ligne("4", 2, tx_kbps=1000.0), ts=(T0 + timedelta(minutes=5, seconds=5)).isoformat()),
    ]
    fiche = build_summary(_session(), lignes)
    # Seul le dernier intervalle de 5 s compte : 1 000 kbit/s x 5 s / 8000 = 0,6 Mo.
    assert fiche.links[0].mb_up == 0.6


def test_a_micro_outage_is_counted_even_though_both_samples_show_the_link_up():
    """Une coupure plus courte que le cycle ne se voit que par l'uptime."""
    lignes = [
        _ligne("4", 0, uptime_s=3600),
        _ligne("4", 1, uptime_s=12),      # le lien est reparti entre les deux
        _ligne("4", 2, uptime_s=17),
    ]
    assert build_summary(_session(), lignes).links[0].outages == 1


def test_the_barycentre_places_the_stop_without_naming_any_antenna():
    """Le rayon dit la dérive du récepteur ; il sépare deux stationnements."""
    lignes = [
        _ligne("4", 0, site_lat=48.8565, site_lon=2.3522),
        _ligne("4", 1, site_lat=48.8566, site_lon=2.3523),
        _ligne("4", 2, site_lat=48.8565, site_lon=2.3522),
    ]
    fiche = build_summary(_session(), lignes)
    assert fiche.lat is not None and 48.844 < fiche.lat < 48.8573
    assert fiche.radius_m is not None and fiche.radius_m < 30


def test_the_sheet_counts_base_stations_not_links():
    """Quatre liens sur un seul site, c'est de la redondance, pas de la capacité."""
    #  ECI >> 8 : deux ECI voisins partagent la même station.
    lignes = [
        _ligne("4", 0, cell_id=25735436), _ligne("5", 0, cell_id=25735433),
        _ligne("6", 0, cell_id=25735426), _ligne("7", 0, cell_id=25735436),
    ]
    fiche = build_summary(_session(), lignes)
    assert len(fiche.links) == 4
    assert fiche.sites == 1


def test_a_throughput_never_measured_is_not_a_volume_of_zero():
    """« 0.0 Mo montants », en gras, sur un équipement qui ne mesure rien.

    Le Dome Pro Duo ne publie aucun débit par WAN : `capabilities
    .per_wan_throughput` existe précisément pour ne pas présenter cette absence
    comme une mesure, et la fiche de session était le seul écran du dépôt à
    l'ignorer. Sept cent vingt relevés à `None` donnaient un volume nul.
    """
    lignes = [_ligne("4", i, tx_kbps=None, rx_kbps=None) for i in range(720)]
    fiche = build_summary(_session(), lignes)
    assert fiche.links[0].mb_up is None, "une absence de mesure s'est écrite zéro"
    assert fiche.links[0].mb_down is None
    assert fiche.mb_up is None and fiche.mb_down is None


def test_a_link_that_carried_nothing_still_reports_zero():
    """Zéro reste zéro quand il a été MESURÉ : c'est l'autre moitié de la règle."""
    lignes = [_ligne("4", i, tx_kbps=0.0, rx_kbps=0.0) for i in range(20)]
    fiche = build_summary(_session(), lignes)
    assert fiche.links[0].mb_up == 0.0
    assert fiche.mb_up == 0.0


def test_one_link_without_throughput_does_not_erase_the_others():
    """La somme ignore les absences plutôt que de les compter pour zéro."""
    lignes = [_ligne("4", i, tx_kbps=1000.0) for i in range(11)]
    lignes += [_ligne("10", i, tx_kbps=None) for i in range(11)]
    fiche = build_summary(_session(), lignes)
    assert fiche.mb_up == 6.2
    # Et l'ordre est naturel, pas lexicographique : « 10 » vient après « 4 ».
    assert [l.wan_id for l in fiche.links] == ["4", "10"]


def test_a_partial_window_is_declared_as_such():
    """Une fiche muette sur la fenêtre qu'elle a lue se lit comme une fiche complète."""
    lignes = [_ligne("4", i, tx_kbps=1000.0) for i in range(11)]
    fenetre = (T0 + timedelta(hours=2), T0 + timedelta(hours=3))
    fiche = build_summary(_session(), lignes, fenetre)
    assert fiche.partial is True
    assert fiche.window_start == fenetre[0] and fiche.window_end == fenetre[1]
    assert build_summary(_session(), lignes).partial is False


def test_the_radius_needs_two_points_to_exist():
    """`ecarts[int(0.90 * (n - 1))]` rendait 0,0 sur un point unique.

    Un zéro pour une absence : la fiche affirmait un rayon de zéro mètre, donc
    une immobilité parfaite, là où un seul relevé ne permet de mesurer aucune
    dispersion. Sur deux points, l'index tronqué rendait le PLUS PETIT des deux
    écarts. `_percentile`, qui interpole correctement, était déclaré juste en
    dessous et n'était pas appelé.
    """
    from app.sessions import _barycentre

    lat, lon, rayon = _barycentre([(48.8566, 2.3522)])
    assert (lat, lon) == (48.8566, 2.3522)
    assert rayon is None, "un rayon demande au moins deux points"

    assert _barycentre([]) == (None, None, None)

    # Deux points distants : le rayon est une vraie dispersion, pas un zéro.
    _, _, rayon = _barycentre([(48.8566, 2.3522), (48.8576, 2.3522)])
    assert rayon is not None and rayon > 50.0


def test_the_barycentre_survives_the_antimeridian():
    """La moyenne arithmétique de −179,9 et +179,9 vaut 0 : le golfe de Guinée.

    Deux points distants de 22 km à cheval sur l'antiméridien donnaient un
    barycentre à 20 000 km des deux. Le cas ne se présente pas en Bretagne ; il
    se présenterait au premier déploiement dans le Pacifique, et il serait alors
    indétectable à l'écran — une position plausible, simplement fausse.
    """
    from app.sessions import _barycentre

    lat, lon, rayon = _barycentre([(-16.5, 179.9), (-16.5, -179.9)])
    assert lat == -16.5
    # Le vrai centre est sur la ligne, pas à l'opposé du globe.
    assert abs(abs(lon) - 180.0) < 0.01, f"barycentre aberrant : {lon}"
    # Et le RAYON aussi : `_distance_m` prenait 359,8° d'écart de longitude
    # plutôt que 0,2°, soit le tour du globe par l'autre côté. Mesuré à
    # l'exécution après correction du seul barycentre : 34 573 km de rayon sur
    # deux points distants de onze. Les deux points sont à ~10,6 km du centre.
    assert rayon is not None and 10_000 < rayon < 13_000, f"rayon aberrant : {rayon}"

    # Le cas ordinaire n'est pas dégradé pour autant.
    _, lon, _ = _barycentre([(48.8566, 2.3512), (48.8566, 2.3532)])
    assert abs(lon - 2.3522) < 1e-6


def test_a_5g_sa_cell_never_yields_an_invented_station():
    """Le parseur refuse de décomposer un NCI ; deux autres modules le faisaient.

    En 5G SA, l'identifiant de cellule est un NCI de 36 bits dont la longueur du
    champ gNB est configurable par l'opérateur : `_split_eci` s'en abstient
    explicitement — « le découpage serait une invention ». Mais `sessions.py`
    refabriquait la station par `cell_id >> 8` et `events()` par `cell_id / 256`,
    tous deux sans ce garde-fou. Sur un équipement 5G SA, `enb_id` rendait `null`
    honnêtement pendant que la fiche de session et le journal publiaient une
    station FABRIQUÉE : le projet enfreignait deux fois sur trois la règle qu'il
    avait écrite.

    La règle est calculée une fois, à l'analyse, et relue partout ailleurs.
    """
    from app.peplink.router_api import _split_eci

    # Ce que le parseur décide, sur une cellule 5G SA et sur une cellule LTE.
    assert _split_eci(25735433, "5G SA") == (None, None)
    assert _split_eci(25735433, "5G") == (None, None)
    assert _split_eci(25735433, "LTE-A")[0] == 100529
    # La 5G NSA se décompose : `cellTower` y référence l'ancre LTE.
    assert _split_eci(25735433, "5G NSA")[0] == 100529

    # Et la fiche ne compte QUE ce que le parseur a bien voulu décomposer.
    lignes = [
        _ligne("4", 0, cell_id=25735433, enb_id=None),   # 5G SA : station inconnue
        _ligne("5", 0, cell_id=25735426, enb_id=None),
    ]
    fiche = build_summary(_session(), lignes, None)
    assert fiche.sites is None, "aucune station déterminable : la fiche ne doit rien inventer"

    # Deux SIM sur la même station LTE : une seule station, et elle est comptée.
    lignes = [_ligne("4", 0, cell_id=25735433), _ligne("5", 0, cell_id=25735436)]
    assert build_summary(_session(), lignes, None).sites == 1


# --- les trois analyses qui ne coûtent aucune requête -------------------------

def test_latency_percentiles_are_cut_per_cell():
    """La médiane d'un lien ne bouge pas quand sa queue se multiplie par quatre.

    Mesuré sur une cellule tenue constante de 13 h à 21 h : le RSRP varie de
    1,4 dB, la médiane passe de 31 à 40 ms — et le p90 de 119 à 435 ms. C'est la
    queue qui fait geler une image, et elle ne se voit qu'à cellule FIXE : un
    percentile calculé sur une fenêtre qui chevauche deux cellules mélange deux
    populations et ne décrit ni l'une ni l'autre.
    """
    from app.sessions import _RELEVES_PAR_CELLULE

    # Deux cellules : l'une régulière, l'autre à longue queue. Mêmes médianes.
    calme = [_ligne("4", i, cell_id=100, rtt_ms=30.0) for i in range(_RELEVES_PAR_CELLULE)]
    agitee = [
        _ligne("5", i, cell_id=200, rtt_ms=30.0 if i % 10 else 900.0)
        for i in range(_RELEVES_PAR_CELLULE)
    ]
    fiche = build_summary(_session(), calme + agitee, None)

    par_id = {c.cell_id: c for c in fiche.cells}
    assert set(par_id) == {100, 200}
    # Les deux médianes sont IDENTIQUES : c'est tout le propos.
    assert par_id[100].rtt_p50_ms == par_id[200].rtt_p50_ms == 30.0
    # Et les queues n'ont rien à voir. Un relevé sur dix à 900 ms place le p90
    # dans la montée — 117 ms — et le p99 en plein dedans.
    assert par_id[100].rtt_p90_ms == par_id[100].rtt_p99_ms == 30.0
    assert par_id[200].rtt_p90_ms > 100.0
    assert par_id[200].rtt_p99_ms == 900.0, "la queue ne se voit que là"
    # La station suit ce que le parseur a décomposé.
    assert par_id[100].enb_id == 100 >> 8

    # Et une cellule qui n'a pas réuni assez de relevés n'est PAS publiée : un
    # p90 sur vingt mesures désigne l'avant-dernière valeur, pas un percentile.
    maigre = [_ligne("6", i, cell_id=300, rtt_ms=30.0) for i in range(20)]
    assert 300 not in {c.cell_id for c in build_summary(_session(), maigre, None).cells}


def test_cell_latency_is_per_link_and_per_network():
    """Une même cellule vue par quatre WAN : p90 de 223 à 397 ms selon le WAN,
    publiée « 337 ms sur 6 238 relevés » — un chiffre qui ne décrivait aucun
    modem. Et sur deux opérateurs, un ECI n'est unique que dans son PLMN."""
    from app.sessions import _RELEVES_PAR_CELLULE

    n = _RELEVES_PAR_CELLULE
    rapide = [_ligne("4", i, cell_id=100, rtt_ms=30.0) for i in range(n)]
    lent = [_ligne("5", i, cell_id=100, rtt_ms=300.0) for i in range(n)]
    autre_reseau = [_ligne("6", i, cell_id=100, rtt_ms=60.0, plmn="208-20") for i in range(n)]
    fiche = build_summary(_session(), rapide + lent + autre_reseau, None)

    lignes = {(c.wan_id, c.plmn, c.cell_id): c for c in fiche.cells}
    assert set(lignes) == {("4", "208-01", 100), ("5", "208-01", 100), ("6", "208-20", 100)}
    assert lignes[("4", "208-01", 100)].rtt_p90_ms == 30.0
    assert lignes[("5", "208-01", 100)].rtt_p90_ms == 300.0
    assert lignes[("6", "208-20", 100)].rtt_p90_ms == 60.0
    assert all(c.samples == n for c in fiche.cells), "aucune mesure mélangée"

    # Un identifiant de WAN qui n'est pas un nombre — le simulateur, ou tout
    # châssis qui nomme autrement — passait par un type ENTIER et faisait
    # lever la fiche entière (500 sur la route).
    simule = [_ligne("cellular-1", i, cell_id=100, rtt_ms=30.0) for i in range(n)]
    assert [c.wan_id for c in build_summary(_session(), simule, None).cells] == ["cellular-1"]


def test_availability_never_counts_an_unwatched_cycle_as_an_outage():
    """LE piège de ce calcul, et il n'est pas théorique.

    La sonde de tunnel est plus espacée que la scrutation : un cycle où aucun
    lien ne porte d'état de tunnel n'est pas un cycle en panne, c'est un cycle
    NON OBSERVÉ. Mesuré sur la base, une halte passait de 99,8 % à 63,8 % en les
    comptant — soit une heure et demie de panne entièrement fabriquée par un
    défaut d'observation.
    """
    # Dix cycles : cinq observés et propres, cinq sans aucune lecture de tunnel.
    vus = [
        _ligne("4", i, rtt_ms=30.0, tunnel_state="ACTIVE")
        for i in range(0, 5)
    ]
    muets = [_ligne("4", i, rtt_ms=None, tunnel_state=None) for i in range(20, 25)]
    dispo = build_summary(_session(), vus + muets, None).availability

    assert dispo is not None
    assert dispo.at_least_one_pct == 100.0, "aucun des cycles observés n'était en panne"
    # Le dénominateur voyage AVEC le taux, comme partout ailleurs ici.
    assert dispo.observed_cycles == 5
    assert dispo.total_cycles == 10
    assert dispo.threshold_ms == 150.0

    # Un lien ACTIVE mais trop lent n'est pas propre, et le cycle le dit.
    lents = [_ligne("4", i, rtt_ms=900.0, tunnel_state="ACTIVE") for i in range(0, 5)]
    assert build_summary(_session(), lents, None).availability.at_least_one_pct == 0.0


def test_diversity_ignores_wired_links_and_disconnected_modems():
    """Le filaire compté dans la diversité — et `connected` retiré — ne
    faisaient tomber que le test « golden ». Un lien filaire n'emprunte aucune
    cellule ; un modem déconnecté rapporte la dernière qu'il a vue."""
    deux_sims = [
        _ligne("4", 0, cell_id=25735433, earfcn=3000), _ligne("5", 0, cell_id=25735433, earfcn=3000),
        _ligne("4", 1, cell_id=25735433, earfcn=3000), _ligne("5", 1, cell_id=25735433, earfcn=3000),
    ]
    filaire = [_ligne("1", i, kind="ethernet", cell_id=None, operator=None) for i in range(2)]
    d = build_summary(_session(), deux_sims + filaire, None).diversity
    assert d is not None and d.cycles == 2, "le filaire ne rend pas les cycles injugeables"
    assert d.same_cell_pct == 100.0

    # Un troisième modem, déconnecté, campe sur une autre cellule : il ne juge
    # pas — un modem déconnecté rapporte la dernière cellule qu'il a vue.
    tombe = [_ligne("6", i, connected=0, cell_id=99) for i in range(2)]
    d = build_summary(_session(), deux_sims + tombe, None).diversity
    assert d is not None and d.same_cell_pct == 100.0


def test_a_single_modem_cannot_share_anything():
    """`len(groupe) > 1` mis à `>= 1` : un cycle à un seul modem comptait
    comme « 100 % même cellule » — un partage avec personne."""
    seul = [_ligne("4", i, cell_id=25735433) for i in range(3)]
    d = build_summary(_session(), seul, None).diversity
    assert d is not None
    assert d.cycles == 0, "un modem seul n'a personne avec qui partager"
    assert d.same_cell_pct is None


def test_availability_only_counts_links_that_are_up():
    """`connected` retiré du prédicat « dans le bond » ne faisait tomber aucun
    test : le relevé d'adieu d'un lien tombé garde un état ACTIVE et une
    latence, et comptait comme un lien propre."""
    tombe_mais_actif = [
        _ligne("4", i, connected=0, rtt_ms=20.0, tunnel_state="ACTIVE") for i in range(5)
    ]
    dispo = build_summary(_session(), tombe_mais_actif, None).availability
    assert dispo is not None
    assert dispo.observed_cycles == 5
    # Aucun lien n'a été dans le bond : le taux est sans objet, pas nul (R-3.6).
    assert dispo.at_least_one_pct is None, "un lien tombé n'est pas un lien propre"
    assert dispo.cycles_in_bond == 0


def test_the_same_cell_means_the_same_thing_on_the_sheet_and_on_the_screen():
    """La cellule, c'est l'identifiant ET la porteuse — comme à l'écran.

    La fiche jugeait sur `cell_id` seul : même identifiant sur deux canaux,
    « 100 % même cellule » ici, « 2 cellules » à l'écran. Deux définitions
    d'un même mot, à deux clics d'écart.
    """
    deux_canaux = [
        _ligne("4", 0, cell_id=25735433, earfcn=1300),
        _ligne("5", 0, cell_id=25735433, earfcn=6400),
    ]
    assert build_summary(_session(), deux_canaux, None).diversity.same_cell_pct == 0.0

    meme_canal = [
        _ligne("4", 0, cell_id=25735433, earfcn=1300),
        _ligne("5", 0, cell_id=25735433, earfcn=1300),
    ]
    assert build_summary(_session(), meme_canal, None).diversity.same_cell_pct == 100.0

    # Porteuse inconnue des deux côtés : elle ne départage pas, mais elle ne
    # réunit pas non plus — même règle que l'écran. Ce test affirmait l'inverse
    # (« même cellule » sur deux porteuses inconnues), c'est C-40.
    sans_canal = [
        _ligne("4", 0, cell_id=25735433, earfcn=None),
        _ligne("5", 0, cell_id=25735433, earfcn=None),
    ]
    assert build_summary(_session(), sans_canal, None).diversity.same_cell_pct == 0.0


def test_a_switch_to_standby_is_not_counted_as_an_outage():
    """Passage en secours : « Standby », uptime remis à 0, lien connecté. La
    fiche comptait une coupure pour une manœuvre de l'exploitant."""
    # Entrée en secours (seul le relevé d'après dit « Standby ») puis sortie
    # (seul celui d'avant le dit) : chacune remet l'uptime à zéro.
    secours = [
        _ligne("3", 0, uptime_s=78, status_message="Connected to SFR"),
        _ligne("3", 1, uptime_s=0, status_message="Standby"),
        _ligne("3", 2, uptime_s=40, status_message="Standby"),
        _ligne("3", 3, uptime_s=0, status_message="Connected to SFR"),
    ]
    vraie = [
        _ligne("2", 0, uptime_s=500, status_message="Connected"),
        _ligne("2", 1, uptime_s=2, status_message="Connected"),
    ]
    liens = {l.wan_id: l for l in build_summary(_session(), secours + vraie, None).links}
    assert liens["3"].outages == 0
    assert liens["2"].outages == 1


def test_a_cell_change_across_an_outage_is_counted_on_the_sheet():
    """La fiche disait « 0 changement » d'un lien revenu sur une autre cellule."""
    lignes = [_ligne("3", 0, cell_id=25698582)]
    lignes += [_ligne("3", i, connected=0, cell_id=None) for i in range(1, 17)]
    lignes += [_ligne("3", 18, cell_id=2227220), _ligne("3", 19, cell_id=2227220)]
    lien = build_summary(_session(), lignes, None).links[0]
    assert lien.cell_changes == 1
    # Au-delà d'un trou de supervision, rien n'est affirmé.
    trou = [_ligne("2", 0, cell_id=111), _ligne("2", 60, cell_id=222)]
    assert build_summary(_session(), trou, None).links[0].cell_changes == 0


def test_operators_are_counted_by_network_not_by_label():
    """Session 9 de la revue : « Orange » et « Orange F » (même PLMN 208-01)
    comptaient deux opérateurs, SFR un troisième. Un opérateur, c'est un PLMN ;
    le libellé ne sert qu'à défaut."""
    lignes = [
        _ligne("2", 0, operator="Orange", plmn="208-01", cell_id=1),
        _ligne("2", 1, operator="Orange F", plmn="208-01", cell_id=1),
        _ligne("3", 0, operator="Orange F", plmn="208-01", cell_id=2),
        _ligne("3", 1, operator="Mobile S Mobile", plmn="208-10", cell_id=3),
        _ligne("3", 2, operator="Sans réseau", plmn=None, cell_id=3),
    ]
    assert build_summary(_session(), lignes, None).diversity.operators == 3
    lignes[-1]["operator"] = "Mobile S Mobile"
    assert build_summary(_session(), lignes, None).diversity.operators == 2


def test_a_link_that_changed_operator_says_so():
    """Session 9 de la revue : le WAN 3 a passé 146 relevés chez SFR après la
    bascule de SIM, et la fiche le donnait « Orange F ». Un lien porte la
    SUCCESSION de ses opérateurs, dans l'ordre, un libellé par réseau."""
    lignes = [
        _ligne("3", 0, operator="Orange", plmn="208-01"),
        _ligne("3", 1, operator="Orange F", plmn="208-01"),
        _ligne("3", 2, connected=0, operator=None, plmn=None),
        _ligne("3", 3, operator="Mobile S Mobile", plmn="208-10"),
        _ligne("3", 4, operator="Mobile S Mobile", plmn="208-10"),
    ]
    lien = build_summary(_session(), lignes, None).links[0]
    assert lien.operators == ["Orange", "SFR"]
    # Un seul réseau : un seul nom, le dernier libellé vu.
    seul = build_summary(_session(), lignes[:2], None).links[0]
    assert seul.operators == ["Orange"]


def test_operators_without_a_network_code_are_matched_to_the_known_network():
    """Relevé en direct, session 9 : à chaque reconnexion le routeur publie le
    libellé SANS PLMN, et la succession se répétait (« Orange F » ×15) ; « Orange »,
    jamais vu avec un PLMN, comptait un troisième opérateur. Un libellé sans PLMN
    se range sous le PLMN du libellé qu'il prolonge, ou qui le prolonge."""
    lignes = [
        _ligne("2", 0, operator="Orange F", plmn="208-01", cell_id=1),
        _ligne("2", 1, operator="Orange F", plmn=None, cell_id=1),
        _ligne("2", 2, operator="Orange", plmn=None, cell_id=1),
        _ligne("2", 3, operator="Orange F", plmn="208-01", cell_id=1),
        _ligne("3", 0, operator="Orange F", plmn="208-01", cell_id=2),
        _ligne("3", 1, operator="Mobile S Mobile", plmn=None, cell_id=3),
        _ligne("3", 2, operator="Mobile S Mobile", plmn="208-10", cell_id=3),
    ]
    fiche = build_summary(_session(), lignes, None)
    liens = {l.wan_id: l for l in fiche.links}
    assert liens["2"].operators == ["Orange"]
    # Nommés par leur code réseau (R-2.6) : 208-01 Orange, 208-10 SFR.
    assert liens["3"].operators == ["Orange", "SFR"]
    assert fiche.diversity.operators == 2

    # Le rattachement EXACT passe avant le préfixe : « SFR » et « SFR Pro » sont
    # deux réseaux, et « SFR » sans PLMN est SFR, pas SFR Pro.
    deux = [
        _ligne("4", 0, operator="SFR Pro", plmn="208-13", cell_id=5),
        _ligne("4", 1, operator="SFR", plmn="208-10", cell_id=6),
        _ligne("4", 2, operator="SFR", plmn=None, cell_id=6),
    ]
    assert build_summary(_session(), deux, None).links[0].operators == ["SFR Pro", "SFR"]


def test_diversity_excludes_the_cycles_it_cannot_judge():
    """Un champ inconnu n'est pas une diversité constatée.

    Le cas n'est pas théorique : la colonne `enb_id` est récente, toutes les
    lignes écrites avant elle la portent à NULL, et la première version de ce
    calcul rendait 3,9 % de partage de station sur une halte où la mesure
    indépendante en trouve 99,7 % — un zéro pour une absence, exactement.
    """
    # Deux liens sur la même station, sur deux cycles ; un troisième cycle où
    # l'un des deux ne publie pas sa station.
    partage = [
        _ligne("4", 0, cell_id=25735433), _ligne("5", 0, cell_id=25735436),
        _ligne("4", 1, cell_id=25735433), _ligne("5", 1, cell_id=25735436),
    ]
    inconnu = [
        _ligne("4", 40, cell_id=25735433),
        _ligne("5", 40, cell_id=99, enb_id=None),
    ]
    d = build_summary(_session(), partage + inconnu, None).diversity

    assert d is not None
    assert d.same_site_pct == 100.0, "le cycle injugeable doit être EXCLU, pas compté contre"
    # La cellule, elle, est connue partout : le troisième cycle y compte.
    assert d.same_cell_pct == 0.0
    assert d.cycles == 3
    # C-12 : chaque part publie SON dénominateur. Un seul `cycles` était
    # publié — celui des cycles complets — et « même station 100 % » se lisait
    # sur 3 cycles quand il ne portait que sur 2.
    assert (d.site_cycles, d.cell_cycles) == (2, 3)
    assert d.operators == 1, "une seule PLMN : une panne d'opérateur emporte tout"

    # Aucun cycle jugeable sur ce champ : `None`, et surtout pas zéro.
    aveugle = [_ligne("4", 0, cell_id=1, enb_id=None), _ligne("5", 0, cell_id=2, enb_id=None)]
    assert build_summary(_session(), aveugle, None).diversity.same_site_pct is None


def test_the_bond_latency_travels_with_its_count():
    """C-12 : la latence du bond n'avait aucun compte publié — un p90 sur trois
    points se lisait comme un p90 sur trois heures."""
    lignes = [_ligne("4", i, rtt_ms=30.0 + i) for i in range(5)] + [_ligne("4", 5)]
    lien = build_summary(_session(), lignes, None).links[0]
    assert lien.rtt_samples == 5


def test_diversity_keeps_operators_and_unknown_carriers_apart():
    """C-40, côté fiche : même règle qu'à l'écran. Deux opérateurs aux numéros
    égaux ne sont pas « même station » ; deux porteuses inconnues ne sont pas
    « même cellule »."""
    deux_reseaux = [
        _ligne("4", 0, cell_id=25735433, earfcn=3000, plmn="208-01"),
        _ligne("5", 0, cell_id=25735433, earfcn=3000, plmn="208-10"),
    ]
    d = build_summary(_session(), deux_reseaux, None).diversity
    assert (d.same_site_pct, d.same_cell_pct) == (0.0, 0.0)
    sans_porteuse = [
        _ligne("4", 0, cell_id=25735433, earfcn=None),
        _ligne("5", 0, cell_id=25735433, earfcn=None),
    ]
    d = build_summary(_session(), sans_porteuse, None).diversity
    assert d.same_cell_pct == 0.0 and d.same_site_pct == 100.0


def test_a_wired_link_has_no_cell_churn_rate_at_all():
    """Zéro par heure s'y lirait comme une stabilité exemplaire.

    Un lien filaire n'a pas de cellule et ne peut donc pas en changer : le taux
    n'est pas nul, il n'existe pas. C'est le zéro pour une absence, dans le
    calcul même qui sert à comparer des modems entre eux — et la première
    version le commettait, vérifié à l'API.
    """
    filaire = [_ligne("1", i, kind="ethernet", cell_id=None) for i in range(400)]
    lien = build_summary(_session(), filaire, None).links[0]
    assert lien.cells == 0 and lien.cell_changes == 0
    assert lien.cell_changes_per_hour is None

    # Un modem qui brasse, lui, porte son taux — et son compte brut à côté.
    modem = [
        _ligne("4", i, cell_id=25735426 + (i // 20) * 256, rtt_ms=30.0)
        for i in range(400)
    ]
    m = build_summary(_session(), modem, None).links[0]
    assert m.cell_changes == 19
    assert m.cell_changes_per_hour is not None and m.cell_changes_per_hour > 0

    # Et sous un quart d'heure observé, le taux se tait : une seule bascule sur
    # cinq minutes vues vaudrait « 12 par heure », un chiffre qui ne décrit rien.
    court = [_ligne("4", i, cell_id=100 + (i // 30) * 256) for i in range(60)]
    assert build_summary(_session(), court, None).links[0].cell_changes_per_hour is None


def test_availability_distingue_jamais_propre_de_jamais_monte():
    """« 0 % de lien propre » ne juge pas la liaison quand le bond n'a rien porté.

    Le MAX HD2 du 19 septembre : concentrateur injoignable, `PEER_DOWN` sur
    tous les liens pendant toute la session, et 200 Mo sortis en direct par le
    filaire. La fiche affichait « au moins un lien propre 0 % du temps » —
    vrai par définition, et lu comme « jamais disponible ». `cycles_in_bond`
    est ce qui permet à l'écran de dire le fait avant le taux.
    """
    # Tunnel jamais monté : observé, mais aucun lien dans le bond.
    tombes = [_ligne("4", i, rtt_ms=None, tunnel_state="PEER_DOWN") for i in range(0, 5)]
    dispo = build_summary(_session(), tombes, None).availability
    assert dispo.observed_cycles == 5
    # R-3.6 : l'API disait 0 % là où l'écran disait « sans objet ». Un taux de
    # lien propre sur un bond qui n'a rien porté est une absence, pas un zéro.
    assert (dispo.at_least_one_pct, dispo.at_least_two_pct) == (None, None)
    assert dispo.cycles_in_bond == 0, "aucun cycle n'a vu le bond porter un lien"

    # Tunnel monté mais toujours trop lent : observé, dans le bond, jamais propre.
    lents = [_ligne("4", i, rtt_ms=900.0, tunnel_state="ACTIVE") for i in range(0, 5)]
    dispo = build_summary(_session(), lents, None).availability
    assert dispo.at_least_one_pct == 0.0
    assert dispo.cycles_in_bond == 5, "même taux, mais le bond a porté : ce n'est pas la même nouvelle"


def test_the_sheet_summarises_the_link_latency_with_its_count():
    """R-3.3, scénario 9 : la fiche ne résumait que la latence du TUNNEL — « — »
    sur un Dome dont le tunnel n'a jamais tenu, alors que le routeur mesure
    chaque SIM. Une mesure par minute : le compte voyage avec les percentiles."""
    lignes = [_ligne("3", i, path_latency_ms=None if i % 12 else 20.0 + i / 12) for i in range(120)]
    lignes += [_ligne("3", 120, path_latency_ms=900.0),
               _ligne("3", 121, connected=0, path_latency_ms=5000.0)]
    lien = build_summary(_session(), lignes, None).links[0]
    assert lien.path_latency_samples == 11
    assert (lien.path_latency_p50_ms, lien.path_latency_max_ms) == (25.0, 900.0)
    assert lien.path_latency_p90_ms is not None and lien.path_latency_p90_ms > 25.0
    # Aucune mesure : des absences, pas des zéros.
    sans = build_summary(_session(), [_ligne("2", i) for i in range(5)], None).links[0]
    assert (sans.path_latency_samples, sans.path_latency_p50_ms, sans.path_latency_max_ms) == (0, None, None)


def test_the_sheet_joins_older_samples_to_the_router_series_without_double_counting():
    """R-3.2 : là où la série du routeur ne couvre pas leur instant, les
    relevés comptent ; là où elle le couvre, ils n'en portent qu'une copie et
    ne comptent plus (C-9 : la règle était « avant le premier point »)."""
    lignes = [_ligne("3", i, path_latency_ms=100.0) for i in range(4)]         # 0 → 15 s
    lignes += [_ligne("3", 10 + i, path_latency_ms=999.0) for i in range(4)]   # 50 → 65 s, copies
    couverts = [lignes[4]["ts"], (T0 + timedelta(seconds=60)).isoformat()]
    lien = build_summary(_session(), lignes, None, {"3": (couverts, [20.0] * 8)}).links[0]
    assert lien.path_latency_samples == 12
    assert lien.path_latency_max_ms == 100.0, "les copies postérieures ne comptent pas"


def test_a_link_disabled_by_the_operator_is_not_an_outage():
    """C-20, vu en service le 17/10 : la SIM 4 d'un HD2 désactivée à 13:51 puis
    réactivée à 13:52 comptait une coupure dans la fiche. Une décision de
    l'exploitant n'est pas une panne ; une vraie chute, si."""
    from app.models import STATUT_DESACTIVE

    desactive = [
        _ligne("4", 0, uptime_s=3600),
        _ligne("4", 1, connected=0, status_message=STATUT_DESACTIVE, uptime_s=None),
        _ligne("4", 2, connected=0, status_message="Connecting...", uptime_s=None),
        _ligne("4", 3, uptime_s=5),
    ]
    assert build_summary(_session(), desactive).links[0].outages == 0
    tombe = [
        _ligne("4", 0, uptime_s=3600),
        _ligne("4", 1, connected=0, status_message="No Signal", uptime_s=None),
        _ligne("4", 2, uptime_s=5),
    ]
    assert build_summary(_session(), tombe).links[0].outages == 1
