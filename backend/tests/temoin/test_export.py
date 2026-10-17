"""Exports CSV du témoin Ethernet : français, Excel, sans donnée d'identité."""

from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, timezone

from temoin.export import coupures_csv, releves_csv
from temoin.regles import Coupure, Releve

UTC = timezone.utc
PARIS = timezone(timedelta(hours=2))
T0 = datetime(2026, 10, 17, 10, 0, 0, tzinfo=UTC)

ENTETE = {
    "modele": "Modèle-Test",
    "firmware": "8.5.0",
    "wan": "1",
    "debut": T0,
    "fin": T0 + timedelta(hours=1),
}


def lignes(texte):
    return texte.lstrip("﻿").split("\r\n")


def test_le_csv_s_ouvre_dans_excel_en_francais():
    c = Coupure(T0, T0 + timedelta(seconds=12.5), "coupure", "câble")
    texte = coupures_csv(ENTETE, [c], fuseau=PARIS)
    assert texte.startswith("﻿")
    ls = lignes(texte)
    assert "# fuseau;UTC+02:00" in ls
    assert "# lien;WAN 1" in ls
    assert "# modèle;Modèle-Test" in ls
    assert "# début;2026-10-17 12:00:00" in ls
    assert "debut;fin;duree_s;type;cause" in ls
    assert "2026-10-17 12:00:00;2026-10-17 12:00:12;12,5;coupure;câble" in ls


def test_une_coupure_en_cours_s_exporte_sans_fin():
    c = Coupure(T0, None, "coupure", "câble")
    m = Coupure(T0, None, "micro-coupure", None)
    ls = lignes(coupures_csv(ENTETE, [c, m], fuseau=UTC))
    assert "2026-10-17 10:00:00;;;coupure (en cours);câble" in ls
    assert "2026-10-17 10:00:00;;;micro-coupure;" in ls


def test_aucune_coupure_donne_un_fichier_valide():
    for texte in (coupures_csv(ENTETE, [], fuseau=UTC), releves_csv(ENTETE, [], fuseau=UTC)):
        ls = [x for x in lignes(texte) if x]
        assert ls[-1] in ("debut;fin;duree_s;type;cause",
                          "instant;mesure;connecte;message;anciennete_s;latence_ms")
        assert all(x.startswith("#") for x in ls[:-1])


def test_duree_entiere_sans_decimale():
    c = Coupure(T0, T0 + timedelta(seconds=30), "coupure", None)
    assert ";30;coupure;" in coupures_csv(ENTETE, [c], fuseau=UTC)


def test_l_export_ne_porte_ni_serie_ni_position_ni_nom_de_wan():
    entete = dict(ENTETE, serie="SERIE-TEST-1", position="48.0000,2.0000", nom="LIGNE-FICTIVE")
    c = Coupure(T0, T0 + timedelta(seconds=5), "coupure", "câble")
    r = Releve(T0, True, True, None, 10, False, 20, 12.5)
    for texte in (coupures_csv(entete, [c], fuseau=UTC), releves_csv(entete, [r], fuseau=UTC)):
        for interdit in ("SERIE-TEST-1", "48.0000", "LIGNE-FICTIVE"):
            assert interdit not in texte
        assert "WAN 1" in texte


def test_releves_mesure_connecte_et_absence_de_mesure():
    rels = [
        Releve(T0, True, True, None, 3600, False, 9000, 12.5),
        Releve(T0 + timedelta(seconds=3), True, False, "Link down", None, False, 9003),
        Releve(T0 + timedelta(seconds=6), False, None, None, None, False, None),
    ]
    ls = lignes(releves_csv(ENTETE, rels, fuseau=UTC))
    assert "2026-10-17 10:00:00;oui;oui;;3600;12,5" in ls
    assert "2026-10-17 10:00:03;oui;non;Link down;;" in ls
    assert "2026-10-17 10:00:06;non;;;;" in ls


def test_les_messages_sont_assainis():
    rels = [
        Releve(T0, True, True, 'a;b\n"c"', 1, False, 1),
        Releve(T0, True, True, "=SOMME(A1)", 1, False, 1),
        Releve(T0, True, True, "-1+2", 1, False, 1),
    ]
    texte = releves_csv(ENTETE, rels, fuseau=UTC)
    ls = lignes(texte)
    assert "2026-10-17 10:00:00;oui;oui;a b c;1;" in ls
    assert "2026-10-17 10:00:00;oui;oui;'=SOMME(A1);1;" in ls
    assert "2026-10-17 10:00:00;oui;oui;'-1+2;1;" in ls


def test_une_micro_coupure_n_est_jamais_en_cours():
    m = Coupure(T0, None, "micro-coupure", None)
    texte = coupures_csv(ENTETE, [m], fuseau=UTC)
    assert "en cours" not in texte
    assert "2026-10-17 10:00:00;;;micro-coupure;" in lignes(texte)


def test_le_fuseau_est_celui_du_debut_d_observation():
    paris = ZoneInfo("Europe/Paris")
    hiver = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
    ls = lignes(coupures_csv(dict(ENTETE, debut=hiver), [], fuseau=paris))
    assert "# fuseau;UTC+01:00" in ls
    ete = datetime(2026, 7, 15, 10, 0, 0, tzinfo=UTC)
    assert "# fuseau;UTC+02:00" in lignes(coupures_csv(dict(ENTETE, debut=ete), [], fuseau=paris))


def test_la_latence_est_arrondie_a_une_decimale():
    r = Releve(T0, True, True, None, 1, False, 1, 12.5000000001)
    r2 = Releve(T0, True, True, None, 1, False, 1, 0.1 + 0.2)
    ls = lignes(releves_csv(ENTETE, [r, r2], fuseau=UTC))
    assert "2026-10-17 10:00:00;oui;oui;;1;12,5" in ls
    assert "2026-10-17 10:00:00;oui;oui;;1;0,3" in ls


def test_une_coupure_a_fin_non_observee_le_dit():
    c = Coupure(T0, T0 + timedelta(seconds=27), "coupure", None, fin_observee=False)
    ls = lignes(coupures_csv(ENTETE, [c], fuseau=UTC))
    assert "2026-10-17 10:00:00;2026-10-17 10:00:27;27;coupure (fin non observée);" in ls


def test_un_releve_desactive_le_dit_dans_la_colonne_connecte():
    r = Releve(T0, True, False, None, None, True, 9000)
    ls = lignes(releves_csv(ENTETE, [r], fuseau=UTC))
    assert "2026-10-17 10:00:00;oui;désactivé;;;" in ls
