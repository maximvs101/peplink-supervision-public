"""Le journal du routeur (`status.log`), lu comme une liste FERMÉE de faits.

Endpoint non documenté : c'est celui de l'interface web du routeur. Les lignes
ci-dessous reprennent la FORME des lignes réelles d'un Dome Pro Duo 8.5.1 ; les
noms de WAN, qui portent des numéros de ligne, et l'IP du poste sont remplacés.
"""

from datetime import datetime, timezone

from app.peplink.router_api import horloge_routeur, parse_router_log

HORLOGE = {"stat": "ok", "response": {"systemTime": {
    "string": "Thu Sep 24 18:10:00 CEST 2026", "timestamp": 1790266200, "timezone": "Europe/Paris",
}}}
NOMS = {"Or 1 - SFR 2": "3", "Bo 3F - Or 4": "2", "WAN EXT PEP": "1"}


def _journal(*lignes: str) -> dict:
    return {"stat": "ok", "response": {"log": list(lignes), "ref_key": {}, "is_end_of_log": False}}


def test_the_router_clock_gives_the_offset_without_a_timezone_database():
    local, utc = horloge_routeur(HORLOGE)
    assert local == datetime(2026, 9, 24, 18, 10, 0)
    assert utc == datetime(2026, 9, 24, 16, 10, 0, tzinfo=timezone.utc)
    assert horloge_routeur({"stat": "ok", "response": {}}) is None


def test_each_recognised_form_becomes_a_typed_fact():
    faits = parse_router_log(_journal(
        "Sep 24 18:02:45 WAN: Or 1 - SFR 2 (Priority 1) connected to Orange F (192.0.0.2) in SIM slot A",
        "Sep 24 18:01:03 WAN: Or 1 - SFR 2 (Priority 1) disconnected (Disabled) in SIM slot A",
        "Sep 24 18:06:05 WAN: Or 1 - SFR 2 detected SIM available, switching to SIM B",
        "Sep 24 18:10:15 WAN: Bo 3F - Or 4 (Priority 1) disconnected (WAN failed PING test) in SIM slot B",
        "Sep 24 18:09:58 WAN: Bo 3F - Or 4 changes applied",
        "Sep 24 18:08:46 WAN: Priority changed (Priority 1 - WAN EXT PEP, Bo 3F - Or 4 / Priority 2 - Or 1 - SFR 2)",
        "Sep 24 18:21:30 System: Started up (8.5.1 build 5714)",
        "Sep 24 17:28:08 Admin: admin (192.0.2.10) login failed",
        "Sep 24 17:28:13 Admin: admin (192.0.2.10) login successful",
        "Sep 24 18:00:00 Web blocking: Activated",                 # hors de la liste
        "Sep 24 18:00:01 WAN: Inconnu (Priority 1) disconnected",  # nom qui ne désigne rien
        "ligne illisible",
    ), horloge_routeur(HORLOGE), NOMS)
    vus = [(f.kind, f.wan_id, f.detail, f.ts.strftime("%H:%M:%S")) for f in faits]
    assert vus == [
        ("retour", "3", "Orange F · SIM A", "16:02:45"),
        ("coupure", "3", "Disabled", "16:01:03"),
        ("bascule de SIM", "3", "vers la SIM B", "16:06:05"),
        ("coupure", "2", "WAN failed PING test", "16:10:15"),
        ("configuration modifiée", "2", None, "16:09:58"),
        ("priorités modifiées", None, None, "16:08:46"),
        ("routeur démarré", None, None, "16:21:30"),
        ("connexion admin refusée", None, None, "15:28:08"),
        ("connexion admin réussie", None, None, "15:28:13"),
    ]
    # Aucune donnée personnelle ne traverse : ni nom de WAN, ni IP.
    texte = repr(faits)
    assert "SFR 2" not in texte and "192.0.2" not in texte


def test_the_same_line_always_has_the_same_fingerprint():
    ligne = "Sep 24 18:01:03 WAN: Or 1 - SFR 2 (Priority 1) disconnected (Disabled) in SIM slot A"
    a = parse_router_log(_journal(ligne), horloge_routeur(HORLOGE), NOMS)[0]
    b = parse_router_log(_journal(ligne, "Sep 24 18:09:58 WAN: Bo 3F - Or 4 changes applied"), horloge_routeur(HORLOGE), NOMS)[0]
    assert a.empreinte == b.empreinte and len(a.empreinte) >= 16


def test_a_line_from_last_december_read_in_january_keeps_its_year():
    janvier = {"stat": "ok", "response": {"systemTime": {
        "string": "Fri Jan 1 00:05:00 CET 2027", "timestamp": 1798758300, "timezone": "Europe/Paris"}}}
    fait = parse_router_log(_journal("Dec 31 23:59:00 System: Started up"), horloge_routeur(janvier), NOMS)[0]
    assert fait.ts == datetime(2026, 12, 31, 22, 59, 0, tzinfo=timezone.utc)


def test_an_unexpected_payload_yields_nothing_and_never_raises():
    for charge in ({}, {"stat": "fail"}, {"response": {"log": "texte"}}, {"response": {"log": [None, 3]}}):
        assert parse_router_log(charge, horloge_routeur(HORLOGE), NOMS) == []
    assert parse_router_log(_journal("Sep 24 18:01:03 System: Started up"), None, NOMS) == []
