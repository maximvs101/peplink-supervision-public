"""Règles du témoin Ethernet : coupures, bande, verdict (fonctions pures)."""

from datetime import datetime, timedelta, timezone

from temoin.regles import (
    Coupure,
    FaitRouteur,
    Releve,
    bande,
    chiffres,
    coupures,
    verdict,
)

T0 = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)


def t(s):
    return T0 + timedelta(seconds=s)


def r(t_s, connecte=True, anciennete=3600, message=None, desactive=False,
      joignable=True, anciennete_routeur=100000):
    if not joignable:
        return Releve(t(t_s), False, None, None, None, False, None)
    return Releve(t(t_s), True, connecte, message, anciennete, desactive, anciennete_routeur)


def serie(debut, fin, **kw):
    return [r(s, **kw) for s in range(debut, fin + 1, 3)]


def etats(segments):
    return [(s.etat, s.debut, s.fin) for s in segments]


def test_une_coupure_va_du_premier_releve_tombe_au_premier_revenu():
    rel = (serie(0, 30) + serie(33, 60, connecte=False, message="No Cable Detected")
           + serie(63, 72))
    assert coupures(rel, []) == [Coupure(t(33), t(63), "coupure", "câble")]
    assert coupures(rel, [])[0].duree_s == 30


def test_la_cause_test_de_sante_vient_du_journal():
    rel = serie(0, 30) + serie(33, 60, connecte=False) + serie(63, 72)
    faits = [FaitRouteur(t(35), "test de santé", "WAN failed DNS test")]
    assert coupures(rel, faits)[0].cause == "test de santé"


def test_sans_cause_connue_le_message_du_routeur_est_repris_tel_quel():
    rel = serie(0, 30) + serie(33, 60, connecte=False, message="Disconnected") + serie(63, 72)
    assert coupures(rel, [])[0].cause == "Disconnected"


def test_une_coupure_en_cours_n_a_ni_fin_ni_duree():
    rel = serie(0, 30) + serie(33, 60, connecte=False)
    [c] = coupures(rel, [])
    assert c.fin is None and c.duree_s is None


def test_une_micro_coupure_se_voit_a_l_anciennete_qui_recule():
    rel = [*serie(0, 30), r(33, anciennete=2), *serie(36, 45, anciennete=5)]
    [c] = coupures(rel, [])
    assert (c.genre, c.debut, c.fin, c.duree_s) == ("micro-coupure", t(33), None, None)


def test_le_passage_en_secours_n_est_pas_une_micro_coupure():
    rel = serie(0, 30) + serie(33, 60, anciennete=2, message="Standby")
    assert coupures(rel, []) == []
    assert "secours" in [s.etat for s in bande(rel, t(63))]


def test_une_desactivation_n_est_pas_une_coupure():
    rel = serie(0, 30) + serie(33, 60, desactive=True, connecte=False) + serie(63, 72)
    assert coupures(rel, []) == []
    assert ("desactive", t(33), t(63)) in etats(bande(rel, t(75)))


def test_un_trou_de_mesure_n_est_ni_coupure_ni_zero():
    rel = serie(0, 30) + serie(1830, 1860)
    assert coupures(rel, []) == []
    seg = bande(rel, t(1860))
    assert ("non_mesure", t(30), t(1830)) in etats(seg)
    ch = chiffres(seg, [])
    assert ch["mesure_s"] == 60
    assert ch["disponibilite"] == 1.0


def test_un_redemarrage_du_routeur_n_est_pas_une_coupure():
    rel = (serie(0, 30) + [r(s, joignable=False) for s in range(33, 91, 3)]
           + [r(93, anciennete_routeur=20, anciennete=15)])
    assert coupures(rel, []) == []
    assert ("redemarrage", t(33), t(93)) in etats(bande(rel, t(96)))


def test_un_routeur_injoignable_sans_redemarrage_est_non_mesure():
    rel = (serie(0, 30) + [r(s, joignable=False) for s in range(33, 91, 3)]
           + [r(93, anciennete_routeur=90000)])
    assert coupures(rel, []) == []
    assert ("non_mesure", t(33), t(93)) in etats(bande(rel, t(96)))


def test_hypothese_journal_desactivation_et_redemarrage():
    # HYPOTHÈSE — l'heure du journal (minute) prime à ±15 s sur celle du relevé
    rel = serie(0, 30) + serie(33, 60, desactive=True) + serie(63, 72)
    faits = [FaitRouteur(t(40), "désactivation", None)]
    assert ("desactive", t(40), t(63)) in etats(bande(rel, t(75), faits))
    assert ("debout", t(0), t(40)) in etats(bande(rel, t(75), faits))

    rel = (serie(0, 30) + [r(s, joignable=False) for s in range(33, 91, 3)]
           + [r(93, anciennete_routeur=20)])
    faits = [FaitRouteur(t(38), "redémarrage", None)]
    assert ("redemarrage", t(38), t(93)) in etats(bande(rel, t(96), faits))


def test_hypothese_un_fait_du_journal_trop_loin_n_aligne_rien():
    rel = serie(0, 30) + serie(33, 60, desactive=True) + serie(63, 72)
    faits = [FaitRouteur(t(120), "désactivation", None)]
    assert ("desactive", t(33), t(63)) in etats(bande(rel, t(75), faits))


def test_le_verdict_dit_depuis_quand():
    revenu = serie(0, 30) + serie(33, 60, connecte=False) + serie(63, 72)
    assert verdict(revenu, t(75)) == {"etat": "stable", "depuis": t(63)}
    en_cours = serie(0, 30) + serie(33, 45, connecte=False)
    assert verdict(en_cours, t(48)) == {"etat": "coupe", "depuis": t(33)}
    perdu = [*serie(0, 30), r(33, joignable=False)]
    assert verdict(perdu, t(36))["etat"] == "injoignable"
    assert verdict([], t(36)) == {"etat": "aucune_mesure", "depuis": None}
    off = serie(0, 30) + serie(33, 40, desactive=True)
    assert verdict(off, t(42)) == {"etat": "desactive", "depuis": t(33)}


def test_les_chiffres_comptent_coupures_et_disponibilite():
    rel = serie(0, 30) + serie(33, 60, connecte=False) + serie(63, 90)
    seg = bande(rel, t(93))
    ch = chiffres(seg, coupures(rel, []))
    assert ch["coupures"] == 1
    assert ch["coupe_s"] == 30
    assert ch["mesure_s"] == 93
    assert round(ch["disponibilite"], 4) == round(63 / 93, 4)


def test_rien_de_mesure_donne_une_disponibilite_vide():
    assert chiffres([], [])["disponibilite"] is None


def test_un_verdict_perime_dit_aucune_mesure():
    stable = serie(0, 30)
    assert verdict(stable, t(33))["etat"] == "stable"
    assert verdict(stable, t(30 + 16)) == {"etat": "aucune_mesure", "depuis": t(30)}
    tombe = serie(0, 30) + serie(33, 45, connecte=False)
    assert verdict(tombe, t(48))["etat"] == "coupe"
    assert verdict(tombe, t(45 + 60)) == {"etat": "aucune_mesure", "depuis": t(45)}


def test_une_coupure_interrompue_par_un_trou_finit_au_dernier_releve_tombe():
    rel = serie(0, 30) + serie(33, 60, connecte=False) + serie(1800, 1830)
    [c] = coupures(rel, [])
    assert (c.debut, c.fin, c.duree_s) == (t(33), t(60), 27)
    seg = bande(rel, t(1830))
    assert ("coupe", t(33), t(60)) in etats(seg)
    assert ("non_mesure", t(60), t(1800)) in etats(seg)
    assert chiffres(seg, [c])["coupe_s"] == 27


def test_un_trou_dans_une_suite_de_redemarrage_reste_non_mesure():
    rel = (serie(0, 30) + [r(s, joignable=False) for s in (33, 36, 39, 300, 303)]
           + [r(306, anciennete_routeur=20)])
    seg = etats(bande(rel, t(309)))
    assert ("redemarrage", t(33), t(39)) in seg
    assert ("non_mesure", t(39), t(300)) in seg
    assert ("redemarrage", t(300), t(306)) in seg


def test_un_redemarrage_au_recul_reel_reste_un_seul_segment():
    # recul de la collecte : 3, 6, 12, 24, puis 30 s entre deux essais
    rel = (serie(0, 30) + [r(s, joignable=False) for s in (33, 39, 51, 75, 105, 135)]
           + [r(138, anciennete_routeur=20)])
    seg = etats(bande(rel, t(141)))
    assert ("redemarrage", t(33), t(138)) in seg
    assert coupures(rel, []) == []


def test_un_trou_de_soixante_secondes_dans_la_suite_reste_non_mesure():
    rel = (serie(0, 30) + [r(s, joignable=False) for s in (33, 39, 51, 75, 105, 165)]
           + [r(168, anciennete_routeur=20)])
    seg = etats(bande(rel, t(171)))
    assert ("redemarrage", t(33), t(105)) in seg
    assert ("non_mesure", t(105), t(165)) in seg
    assert ("redemarrage", t(165), t(168)) in seg
