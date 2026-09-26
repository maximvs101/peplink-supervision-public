"""Les trois règles d'alerte, et surtout les cas où elles doivent se TAIRE.

Une alerte qu'on découvre injustifiée est une alerte qu'on n'écoutera plus. La
moitié de ce fichier éprouve donc le silence, pas le bruit.
"""

from datetime import datetime, timedelta, timezone

from app.alerts import (
    HORS_BOND_S,
    REMANENCE_S,
    SEUL_LIEN_S,
    evaluer,
    interruption,
    publiables,
)


def _publie(liens, suivies, instant):
    """Un cycle : on suit, puis on publie ce qui a franchi son seuil.

    Les deux sont séparés à dessein. Ne reporter que les alertes PUBLIÉES
    ferait perdre à chaque cycle l'instant où une condition a commencé, et
    aucune ne franchirait jamais son seuil — le défaut qu'avait la première
    version de ce module, et que rien dans son code ne signalait.
    """
    suivies = evaluer(liens, suivies, maintenant=instant)
    return suivies, publiables(suivies, maintenant=instant)
from app.models import TunnelLink, WanRadioStatus

T0 = datetime(2026, 9, 13, 14, 0, tzinfo=timezone.utc)


def _lien(wan_id: str, *, connecte=True, etat: str | None = "ACTIVE", ecarte=False):
    return WanRadioStatus(
        wan_id=wan_id,
        wan_name=f"SIM {wan_id}",
        connected=connecte,
        tunnel=None if etat is None and not connecte else TunnelLink(
            state=etat, beyond_cutoff=ecarte
        ),
    )


def _sans_tunnel(wan_id: str):
    return WanRadioStatus(wan_id=wan_id, wan_name=f"SIM {wan_id}", connected=True)


def _a(instant_s: float):
    return T0 + timedelta(seconds=instant_s)


# --- ce qui ne doit JAMAIS parler --------------------------------------------

def test_a_device_without_any_tunnel_raises_nothing():
    """L'absence de SpeedFusion n'est pas une panne.

    Les deux premières règles parlent du BOND. Les appliquer à un châssis qui
    n'en a pas signalerait une configuration comme un incident — et beaucoup
    d'équipements n'ont légitimement aucun profil.
    """
    liens = [_sans_tunnel("4"), _sans_tunnel("5")]
    assert _publie(liens, [], _a(0))[1] == []
    # Et même après une éternité : ce n'est pas une question de durée.
    assert _publie(liens, [], _a(10_000))[1] == []


def test_an_unknown_tunnel_state_is_never_an_alert():
    """`state is None` veut dire « pas sondé », pas « hors du bond ».

    La sonde de tunnel est plus espacée que la scrutation. Alerter sur une
    absence de mesure est le même défaut que le zéro pour une absence, et il
    coûte ici plus cher : une alerte injustifiée décrédibilise les deux autres.
    """
    liens = [_lien("4", etat=None), _lien("5", etat=None)]
    suivies: list = []
    for t in (0, 30, 120, 600):
        suivies, publiees = _publie(liens, suivies, _a(t))
    assert publiees == []


def test_a_disconnected_link_is_not_reported_as_out_of_bond():
    """Qu'un lien tombé ne porte plus rien n'apprend rien de neuf.

    Le dire deux fois — une fois comme coupure, une fois comme sortie du bond —
    dilue les deux.
    """
    liens = [_lien("4", connecte=False, etat="WAN_DOWN"), _lien("5"), _lien("6")]
    suivies: list = []
    for t in (0, 30, 120):
        suivies, publiees = _publie(liens, suivies, _a(t))
    assert [a.kind for a in publiees] == []


def test_a_brief_excursion_never_becomes_an_alert():
    """Un lien sort et rentre au gré des bascules de cellule.

    Une condition qui n'a jamais franchi son seuil ne doit pas apparaître au
    moment même où elle cesse — ce serait le pire des deux mondes : tardive ET
    fausse.
    """
    dehors = [_lien("4", etat="COLD"), _lien("5"), _lien("6")]
    dedans = [_lien("4"), _lien("5"), _lien("6")]

    suivies, publiees = _publie(dehors, [], _a(0))
    assert publiees == [], "rien n'est publié à chaud"
    suivies, publiees = _publie(dehors, suivies, _a(HORS_BOND_S / 2))
    assert publiees == [], "le seuil n'est pas franchi"
    suivies, publiees = _publie(dedans, suivies, _a(HORS_BOND_S - 5))
    assert publiees == [], "et l'épisode disparaît sans laisser de trace"
    assert suivies == [], "il ne reste rien à suivre non plus"

    # La bascule inverse : une excursion qui DÉPASSE le seuil est bien un fait,
    # même si elle s'est achevée avant qu'on regarde. La durée retenue est
    # majorée par le cycle de scrutation — on constate la fin au relevé suivant,
    # pas à la seconde près —, et c'est pourquoi le seuil vaut une minute et non
    # cinq secondes.
    suivies, _ = _publie(dehors, [], _a(0))
    _, publiees = _publie(dedans, suivies, _a(HORS_BOND_S + 5))
    assert len(publiees) == 1 and publiees[0].until == _a(HORS_BOND_S + 5)


# --- ce qui doit parler ------------------------------------------------------

def test_a_link_out_of_the_bond_is_reported_with_the_router_s_own_word():
    """Trois déclenchements en 14 h 30 de relevés réels, aucun faux positif."""
    liens = [_lien("4", etat="WAN_DISABLED"), _lien("5"), _lien("6")]
    suivies, publiees = _publie(liens, [], _a(0))
    assert publiees == [] and len(suivies) == 1, "suivie sans être annoncée"

    suivies, publiees = _publie(liens, suivies, _a(HORS_BOND_S + 1))
    assert len(publiees) == 1
    alerte = publiees[0]
    assert (alerte.kind, alerte.wan_id) == ("hors du bond", "4")
    # La CAUSE vient du routeur, pas d'une déduction de l'outil.
    assert alerte.detail == "WAN_DISABLED"
    # `since` porte le début de la CONDITION, pas celui de la publication.
    assert alerte.since == T0
    assert alerte.until is None
    assert alerte.sustained_s == HORS_BOND_S


def test_a_resolved_alert_stays_visible_for_a_while_then_goes():
    """Sans rémanence, une coupure survenue pendant qu'on préparait le matériel
    n'aurait jamais existé pour l'exploitant."""
    dehors = [_lien("4", etat="WAN_DOWN"), _lien("5"), _lien("6")]
    dedans = [_lien("4"), _lien("5"), _lien("6")]

    suivies, _ = _publie(dehors, [], _a(0))
    suivies, publiees = _publie(dehors, suivies, _a(HORS_BOND_S + 1))
    assert len(publiees) == 1

    fin = HORS_BOND_S + 120
    suivies, publiees = _publie(dedans, suivies, _a(fin))
    assert len(publiees) == 1 and publiees[0].until is not None, "close, mais encore là"
    assert publiees[0].until == _a(fin)

    # Elle vit sa rémanence…
    _, encore = _publie(dedans, suivies, _a(fin + REMANENCE_S - 10))
    assert len(encore) == 1
    # …puis c'est au journal de répondre : il date tout et ne s'efface pas.
    _, plus_tard = _publie(dedans, suivies, _a(fin + REMANENCE_S + 10))
    assert plus_tard == []


def test_a_condition_that_comes_back_is_a_NEW_alert():
    """Reprendre le `since` d'un épisode clos fusionnerait deux faits séparés
    en un seul, plus long qu'aucun des deux."""
    dehors = [_lien("4", etat="COLD"), _lien("5"), _lien("6")]
    dedans = [_lien("4"), _lien("5"), _lien("6")]

    suivies, _ = _publie(dehors, [], _a(0))
    suivies, _ = _publie(dehors, suivies, _a(HORS_BOND_S + 1))
    suivies, _ = _publie(dedans, suivies, _a(200))          # close
    suivies, _ = _publie(dehors, suivies, _a(300))          # reparaît
    suivies, publiees = _publie(dehors, suivies, _a(300 + HORS_BOND_S + 1))

    en_cours = [a for a in publiees if a.until is None]
    assert len(en_cours) == 1
    assert en_cours[0].since == _a(300), "le nouvel épisode part de son propre début"


def test_the_liaison_on_a_single_link_speaks_but_only_when_watched():
    """Zéro déclenchement en 14 h 30, et c'est ce qu'on attend d'elle.

    Ce qui la disqualifierait serait de parler pour rien, pas de se taire quand
    tout va bien. Mais son dénominateur doit être OBSERVÉ : zéro porteur
    constaté sur zéro lien regardé n'est pas une panne.
    """
    un_seul = [_lien("4"), _lien("5", etat="WAN_DOWN"), _lien("6", etat="COLD")]
    suivies, _ = _publie(un_seul, [], _a(0))
    suivies, publiees = _publie(un_seul, suivies, _a(SEUL_LIEN_S + 1))

    seule = [a for a in publiees if a.kind == "liaison sur un seul lien"]
    assert len(seule) == 1
    assert seule[0].wan_id is None, "la règle ne désigne aucun lien : elle parle du bond"
    assert seule[0].detail == "4 seul sur 3"

    # Aucun lien regardé : la règle se tait, quelle que soit la durée.
    aveugle = [_lien("4", etat=None), _lien("5", etat=None)]
    suivi: list = []
    for t in (0, 60, 600):
        suivi, muet = _publie(aveugle, suivi, _a(t))
    assert [a for a in muet if a.kind == "liaison sur un seul lien"] == []


def test_a_link_beyond_the_cutoff_no_longer_carries():
    """Le routeur a cessé d'employer ce lien : il est dans le bond et ne porte rien.

    Le compter comme porteur ferait dire à l'alerte le contraire de ce que la
    table marque à côté.
    """
    liens = [_lien("4"), _lien("5", ecarte=True), _lien("6", ecarte=True)]
    suivies, _ = _publie(liens, [], _a(0))
    _, publiees = _publie(liens, suivies, _a(SEUL_LIEN_S + 1))
    assert [a.detail for a in publiees if a.kind == "liaison sur un seul lien"] == ["4 seul sur 3"]


def test_no_carrying_link_at_all_is_said_as_such():
    liens = [_lien("4", etat="WAN_DOWN"), _lien("5", etat="WAN_DOWN")]
    suivies, _ = _publie(liens, [], _a(0))
    _, publiees = _publie(liens, suivies, _a(HORS_BOND_S + 1))
    # « Dit comme tel » : ce test portait le genre « un seul lien » avec le
    # détail « aucun sur 2 » — il documentait l'ambiguïté au lieu de la refuser.
    # Le genre est désormais distinct, et le genre « un seul lien » ne doit PAS
    # sortir ici : zéro n'est pas un.
    bond = [a for a in publiees if a.kind == "liaison sans aucun lien"]
    assert len(bond) == 1 and bond[0].detail == "aucun sur 2"
    assert [a for a in publiees if a.kind == "liaison sur un seul lien"] == []
    # Et les deux liens hors du bond sont signalés séparément : ce sont eux
    # qu'on va aller voir.
    assert sorted(a.wan_id for a in publiees if a.kind == "hors du bond") == ["4", "5"]


# --- l'angle mort ------------------------------------------------------------

def test_a_supervision_gap_is_reported_after_the_fact():
    """Quand le superviseur s'arrête, plus rien ne l'observe.

    Le routeur continue de fonctionner sans témoin, et le trou n'est constatable
    qu'APRÈS, en comparant le dernier relevé écrit à l'heure du redémarrage. La
    bande montre déjà ces creux, mais elle demande qu'on la regarde et qu'on
    sache les lire ; ici, l'outil le DIT.
    """
    alerte = interruption(T0, maintenant=T0 + timedelta(minutes=13))
    assert alerte is not None
    assert alerte.kind == "supervision interrompue"
    # PAS de `detail` : la durée est déjà portée par les deux bornes, et la
    # redire en toutes lettres produisait deux chiffres du même fait à un
    # centimètre l'un de l'autre — « 6 min » à côté de « 5 min », parce que
    # l'un arrondit et l'autre tronque. Vu à l'écran avant correction.
    assert alerte.detail is None
    assert (alerte.until - alerte.since).total_seconds() == 13 * 60
    # CLOSE d'emblée : prétendre qu'elle dure affirmerait que la supervision est
    # encore arrêtée alors qu'elle vient de reprendre.
    assert alerte.until is not None and alerte.since == T0


def test_an_ordinary_service_restart_is_not_a_gap():
    """En dessous du seuil, c'est notre propre redémarrage : le dire ferait une
    alerte à chaque mise à jour."""
    assert interruption(T0, maintenant=T0 + timedelta(seconds=20)) is None
    # Et sans aucun relevé antérieur, il n'y a pas de trou à mesurer : un
    # équipement neuf n'a pas été « interrompu ».
    assert interruption(None) is None


def test_une_cause_pair_commune_ne_fait_pas_une_alerte_par_lien():
    """Le MAX HD2 : concentrateur injoignable, `PEER_DOWN` sur les trois liens.

    L'écran empilait trois « Lien N hors du bond PEER_DOWN » au-dessus de
    « plus aucun lien dans le bond » — quatre alertes pour un fait, allumées
    tant que le pair reste hors d'atteinte. Une cause côté pair qui écarte
    tous les liens est UN événement : seule la règle de niveau bond parle.
    """
    liens = [_lien(w, etat="PEER_DOWN") for w in ("1", "4", "5")]
    suivies, _ = _publie(liens, [], _a(0))
    _, publiees = _publie(liens, suivies, _a(HORS_BOND_S + 1))
    assert [a for a in publiees if a.kind == "hors du bond"] == [], \
        "le pair est tombé, les liens n'ont rien fait"
    bond = [a for a in publiees if a.kind == "liaison sans aucun lien"]
    # La cause commune voyage avec l'alerte de bond : absorber les alertes
    # par lien ne fait pas disparaître le mot du routeur.
    assert len(bond) == 1 and bond[0].detail == "aucun sur 3 — PEER_DOWN"


def test_des_causes_cote_lien_gardent_leur_alerte_chacune():
    """`WAN_DOWN` sur un lien et `PEER_DOWN` sur l'autre : deux faits distincts.

    Le repli ne joue que si TOUS les liens sont écartés pour la MÊME cause et
    que cette cause nomme le pair. Une cause côté lien reste dite par lien.
    """
    liens = [_lien("4", etat="WAN_DOWN"), _lien("5", etat="PEER_DOWN")]
    suivies, _ = _publie(liens, [], _a(0))
    _, publiees = _publie(liens, suivies, _a(HORS_BOND_S + 1))
    assert sorted(a.wan_id for a in publiees if a.kind == "hors du bond") == ["4", "5"]


# --- un tunnel qui n'a jamais tenu est un état, pas une nouvelle ----------------

def test_un_bond_qui_n_a_jamais_porte_ne_fait_aucune_alerte_de_bond():
    """L'exploitant, sur le MAX HD2 : « SpeedFusion n'est pas censé fonctionner
    ici, c'est un état ». Un tunnel qui n'a jamais tenu n'est pas tombé.

    Les deux règles du bond se taisent tant que le bond n'a jamais porté ; le
    bandeau, lui, dit l'état. La troisième règle ne dépend pas du tunnel.
    """
    liens = [_lien(w, etat="PEER_DOWN") for w in ("1", "4", "5")]
    suivies = evaluer(liens, [], maintenant=_a(0), tunnel_attendu=False)
    suivies = evaluer(liens, suivies, maintenant=_a(HORS_BOND_S + 1), tunnel_attendu=False)
    assert publiables(suivies, maintenant=_a(HORS_BOND_S + 1)) == []


def test_des_que_le_bond_a_porte_sa_chute_est_une_nouvelle():
    """Le même châssis, emmené sur un réseau où le pair répond : le bond porte
    une fois, et à partir de là sa chute redevient une alerte."""
    tombes = [_lien(w, etat="PEER_DOWN") for w in ("4", "5")]
    suivies = evaluer(tombes, [], maintenant=_a(0), tunnel_attendu=True)
    suivies = evaluer(tombes, suivies, maintenant=_a(HORS_BOND_S + 1), tunnel_attendu=True)
    assert [a.kind for a in publiables(suivies, maintenant=_a(HORS_BOND_S + 1))] \
        == ["liaison sans aucun lien"]


# --- la durée ne se rompt ni entre les deux genres ni sous l'absorption ------

def test_un_dernier_porteur_qui_bat_finit_par_etre_dit():
    """Un lien qui entre et sort du bond toutes les cinq secondes.

    Les deux genres de la règle 2 séparés en deux clés, chaque passage 1 ↔ 0
    fermait l'un et repartait l'autre de zéro : après 55 s, rien n'était
    publié — exactement la situation « plus rien à perdre » où l'alerte
    compte. Reproduit par la revue. Ils sont désormais de la même famille.
    """
    suivies: list = []
    for i in range(12):                          # 60 s, bascule toutes les 5 s
        seul = i % 2 == 0
        liens = [_lien("4", etat="ACTIVE" if seul else "WAN_DOWN"),
                 _lien("5", etat="WAN_DOWN")]
        suivies = evaluer(liens, suivies, maintenant=_a(i * 5))
    publiees = publiables(suivies, maintenant=_a(55))
    genres = {a.kind for a in publiees if a.wan_id is None}
    assert genres & {"liaison sur un seul lien", "liaison sans aucun lien"}, \
        "la condition est vraie depuis 55 s sous deux noms : elle doit être dite"
    bond = [a for a in publiees if a.wan_id is None and a.until is None]
    assert bond and bond[0].since == _a(0), "et depuis le début, pas depuis la dernière bascule"


def test_la_chute_du_dernier_porteur_ne_s_affiche_pas_resolue():
    """Sur un seul lien depuis deux minutes, puis plus aucun.

    Le passage 1 → 0 fermait « un seul lien » — affiché RÉSOLU, comme un
    mieux, à l'instant où le bond perdait son dernier lien — et ouvrait
    « aucun » avec quinze secondes d'attente. C'est la même alerte qui
    s'aggrave, et elle garde son début.
    """
    un = [_lien("4", etat="ACTIVE"), _lien("5", etat="WAN_DOWN")]
    suivies = evaluer(un, [], maintenant=_a(0))
    suivies = evaluer(un, suivies, maintenant=_a(120))
    aucun = [_lien("4", etat="WAN_DOWN"), _lien("5", etat="WAN_DOWN")]
    suivies = evaluer(aucun, suivies, maintenant=_a(130))
    publiees = publiables(suivies, maintenant=_a(130))
    ouvertes = [a for a in publiees if a.wan_id is None and a.until is None]
    assert [a.kind for a in ouvertes] == ["liaison sans aucun lien"]
    assert ouvertes[0].since == _a(0), "la durée continue"
    assert not [a for a in publiees if a.kind == "liaison sur un seul lien"], \
        "et rien ne s'affiche résolu"


def test_les_alertes_absorbees_par_le_pair_reprennent_intactes_a_son_retour():
    """Deux liens hors du bond depuis une minute, puis le pair tombe.

    Fermer les alertes par lien à la chute du pair les affichait résolues
    pendant quinze secondes sans rien d'actif, et les faisait repartir de zéro
    à son retour. Absorbées, elles restent suivies : l'alerte de bond hérite
    du plus ancien début, et au retour du pair les liens toujours WAN_DOWN
    reprennent leur alerte avec leur durée d'origine.
    """
    avant = [_lien("4", etat="ACTIVE"), _lien("5", etat="WAN_DOWN"), _lien("6", etat="WAN_DOWN")]
    suivies = evaluer(avant, [], maintenant=_a(0))
    suivies = evaluer(avant, suivies, maintenant=_a(70))
    assert sorted(a.wan_id for a in publiables(suivies, maintenant=_a(70)) if a.kind == "hors du bond") == ["5", "6"]

    # Le pair tombe : PEER_DOWN partout.
    pair_tombe = [_lien(w, etat="PEER_DOWN") for w in ("4", "5", "6")]
    suivies = evaluer(pair_tombe, suivies, maintenant=_a(80))
    publiees = publiables(suivies, maintenant=_a(80))
    assert [a.kind for a in publiees if a.until is None] == ["liaison sans aucun lien"], \
        "une seule alerte active, tout de suite — pas quinze secondes de « résolues »"
    assert [a for a in publiees if a.kind == "hors du bond"] == [], "les alertes par lien sont absorbées, pas résolues"
    assert publiees[0].since == _a(0), "l'alerte de bond hérite du plus ancien lien absorbé"

    # Le pair revient ; 5 et 6 restent WAN_DOWN.
    suivies = evaluer(avant, suivies, maintenant=_a(90))
    publiees = publiables(suivies, maintenant=_a(90))
    par_lien = {a.wan_id: a for a in publiees if a.kind == "hors du bond" and a.until is None}
    assert set(par_lien) == {"5", "6"}
    assert par_lien["5"].since == _a(0), "reprise avec la durée d'origine, pas de zéro"


def test_a_cycle_without_any_probe_neither_opens_nor_resolves():
    """Quand la sonde du tunnel n'a rien rendu — aucun lien ne porte d'état —,
    un cycle CLÔTURAIT tout : une condition en attente repartait de zéro, une
    alerte publiée était déclarée résolue puis renaissait non publiée pendant
    soixante secondes. Le module promettait de ne jamais se déclencher sur une
    absence de mesure ; il se résolvait dessus. Même règle que l'équipement
    injoignable : l'état reste figé."""
    # Trois liens, un seul écarté : la seule condition est « hors du bond ».
    hors = [_lien("4", etat="WAN_DOWN"), _lien("5", etat="ACTIVE"), _lien("6", etat="ACTIVE")]
    muet = [_lien("4", etat=None), _lien("5", etat=None), _lien("6", etat=None)]

    # Une condition en attente survit à un cycle muet et se publie à l'heure.
    suivies, _ = _publie(hors, [], _a(0))
    suivies, _ = _publie(muet, suivies, _a(30))
    suivies, publiees = _publie(hors, suivies, _a(70))
    assert [a.kind for a in publiees] == ["hors du bond"]
    assert publiees[0].since == _a(0), "le cycle muet a remis le compteur à zéro"

    # Une alerte publiée n'est pas résolue par un cycle muet.
    suivies, publiees = _publie(muet, suivies, _a(100))
    assert [a.kind for a in publiees] == ["hors du bond"]
    assert publiees[0].until is None, "un cycle sans sonde a résolu une alerte publiée"

    # Et la résolution, elle, demande une sonde qui dit « dedans ».
    dedans = [_lien("4", etat="ACTIVE"), _lien("5", etat="ACTIVE"), _lien("6", etat="ACTIVE")]
    suivies, publiees = _publie(dedans, suivies, _a(130))
    assert publiees[0].until == _a(130)


def test_a_profile_removed_mid_session_resolves_the_bond_alerts():
    """« Aucun objet tunnel » a deux visages : la sonde sautée, qui gèle ; et
    le profil SpeedFusion RETIRÉ, que l'équipement déclare. Le second passait
    pour le premier, et une alerte de bond ouverte avant le retrait restait
    ouverte, durée croissante, jusqu'à la prochaine mise sous tension. C'est
    le poller qui dit alors que le bond n'est plus attendu."""
    hors = [_lien("4", etat="WAN_DOWN"), _lien("5", etat="ACTIVE"), _lien("6", etat="ACTIVE")]
    suivies, _ = _publie(hors, [], _a(0))
    suivies, publiees = _publie(hors, suivies, _a(70))
    assert [a.kind for a in publiees] == ["hors du bond"]

    # Le profil disparaît : plus aucun objet tunnel, et le bond n'est plus attendu.
    sans_profil = [_sans_tunnel("4"), _sans_tunnel("5"), _sans_tunnel("6")]
    suivies = evaluer(sans_profil, suivies, maintenant=_a(100), tunnel_attendu=False)
    assert suivies[0].until == _a(100), "l'alerte a survécu au retrait du profil"
