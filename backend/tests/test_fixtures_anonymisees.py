"""Ce qu'aucune fixture n'a le droit de contenir — TOUTES, présentes et futures.

Les deux gardes vivaient dans `test_router_api_hd2.py`, liés à la seule
fixture du HD2. Or c'est ce même fichier qui établit que `status.*` peut
porter des identifiants APN : une re-capture du MBX ou du Dome qui les
ramènerait serait archivée verte. Le glob prend chaque `*.json` du dossier,
y compris celui qui n'existe pas encore.

Les gabarits sont ceux du protocole (`docs/PROTOCOLE-NOUVEL-EQUIPEMENT.md`,
étape 4) : IMEI `3500000000000NN`, IMSI au MCC-MNC conservé et au reste nul,
ICCID au préfixe `8933011` et au reste nul, séries `0000-0000-NNNN`, MAC
`a8:c0:ea:00:00:NN` ou `52:00:00:00:00:NN`, IPv4 renumérotées en `10.0.0.N`,
position ramenée au centre de Paris. Le Dome, anonymisé avant ces gabarits,
porte des zéros purs : accepté nommément, pas par un gabarit plus lâche.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("*.json"))


@pytest.fixture(params=FIXTURES, ids=[f.stem for f in FIXTURES])
def texte(request) -> str:
    return request.param.read_text(encoding="utf-8")


def test_la_liste_des_fixtures_n_est_pas_vide():
    """Sans ce garde, un dossier vide ferait passer tout le fichier."""
    assert len(FIXTURES) >= 3


def test_aucun_identifiant_apn(texte):
    """`status.wan.connection` PEUT porter `username`/`password` sous chaque SIM.

    Les clés sont retirées — pas remplacées par une valeur inventée, qui
    ferait croire au champ une forme qu'il n'a pas.
    """
    corps = json.loads(texte).get("status.wan.connection")
    if corps is None:
        pytest.skip("pas de status.wan.connection dans cette fixture")
    t = json.dumps(corps)
    assert '"username"' not in t and '"password"' not in t


def test_aucune_valeur_reelle(texte):
    for imei in re.findall(r'"imei":\s*"(\d+)"', texte):
        assert imei.startswith("35000000000000") or set(imei) == {"0"}, imei
    for imsi in re.findall(r'"imsi":\s*"(\d+)"', texte):
        # Deux gabarits synthétiques coexistent — un chiffre de séquence en
        # fin (HD2), deux (MBX) — et le Dome porte des zéros purs. L'invariant
        # commun : après le MCC-MNC, rien que des zéros jusqu'au suffixe.
        assert set(imsi[5:-2]) <= {"0"}, "IMSI : MCC-MNC, des zéros, un suffixe de séquence"
    for iccid in re.findall(r'"iccid":\s*"(\d+)"', texte):
        assert (iccid.startswith("8933011") and set(iccid[7:-2]) <= {"0"}) or set(iccid) == {"0"}, iccid
    for mac in re.findall(r'\b([0-9a-f]{2}(?::[0-9a-f]{2}){5})\b', texte, re.I):
        assert mac.lower().startswith(("a8:c0:ea:00:00:", "52:00:00:00:00:", "00:00:00:00:00:", "aa:bb:cc:dd:ee:")), mac
    for serie in re.findall(r'\b[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}\b', texte):
        assert serie.startswith("0000-0000-"), serie
    # Renumérotées en `10.0.0.N` (HD2), ou tirées des plages de DOCUMENTATION
    # de la RFC 5737 — 192.0.2/24, 198.51.100/24, 203.0.113/24 — et du bloc
    # 192.0.0/24 (MBX, Dome) : synthétiques par définition.
    # `10.0.` couvre aussi le gabarit du MBX, `10.0.<n° de WAN>.2`.
    SYNTHETIQUES = ("10.0.", "0.0.0.0", "127.", "255.255.255.",
                    "192.0.2.", "198.51.100.", "203.0.113.", "192.0.0.")
    for ip in re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', texte):
        assert ip.startswith(SYNTHETIQUES) or ip == "192.168.50.1", f"IPv4 non renumérotée : {ip}"
    assert re.search(r'\b0[67]\d{8}\b', texte) is None, "aucun numéro de téléphone"
    assert not re.search(r'\b(?!0{13})1\d{12}\b', texte), "aucun numéro de ligne, texte libre compris"
    for lat in re.findall(r'"latitude":\s*([\d.]+)', texte):
        assert lat.startswith("48.8566"), f"position réelle : {lat}"


# ---------------------------------------------------------------------------
# Au-delà des fixtures : les SOURCES de test, des deux côtés.
#
# Le garde ci-dessus ne parcourait que `fixtures/*.json`. La relecture du 25/09
# (C-14) a trouvé une série réelle six fois dans `Discovery.test.tsx` et une
# position de stationnement dans deux tests Python — hors de son champ. Et il
# ne lisait que les latitudes positives, jamais la longitude.
# ---------------------------------------------------------------------------

RACINE = Path(__file__).resolve().parents[2]
# Ce fichier-ci est exclu : il porte les contre-exemples du balayage — des
# valeurs publiques choisies pour tomber, jamais une valeur de la flotte.
SOURCES = sorted(
    [p for p in (RACINE / "backend" / "tests").rglob("*.py") if p.name != Path(__file__).name]
    + list((RACINE / "backend" / "tests" / "fixtures").glob("*.json"))
    + [p for ext in ("*.ts", "*.tsx", "*.json") for p in (RACINE / "frontend" / "src").rglob(ext)]
)

# Une série Peplink isolée : pas un morceau d'UUID, pas une empreinte.
_SERIE = re.compile(r"(?<![0-9A-Fa-f-])([0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4})(?![0-9A-Fa-f-])")
_LAT = re.compile(r"(?:\blat(?:itude)?|_lat)[\s\"':=]{1,5}(-?\d{1,3}\.\d+)", re.I)
_LON = re.compile(r"(?:\blon(?:gitude)?|\blng|_lon)[\s\"':=]{1,5}(-?\d{1,3}\.\d+)", re.I)
_COUPLE = re.compile(r"[(\[]\s*(-?\d{1,2}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})\s*[)\]]")

# Les gabarits : centre de Paris (protocole, étape 4), et Lyon pour la flotte
# de démonstration. À moins de 0,02° — deux kilomètres — de l'un d'eux.
_GABARITS = ((48.8566, 2.3522), (45.7640, 4.8357))
_TOLERANCE = 0.02


def _en_france(lat: float | None, lon: float | None) -> bool:
    return (lat is None or 41.0 <= lat <= 51.6) and (lon is None or -5.6 <= lon <= 9.7)


def _gabarit(lat: float | None, lon: float | None) -> bool:
    return any(
        (lat is None or abs(lat - g_lat) <= _TOLERANCE) and (lon is None or abs(lon - g_lon) <= _TOLERANCE)
        for g_lat, g_lon in _GABARITS
    )


def _precise(valeur: str) -> bool:
    """Trois décimales et plus : ~100 m. En deçà, une valeur ronde (48.0) est
    manifestement synthétique et ne désigne aucun lieu."""
    return len(valeur.split(".")[1]) >= 3


def positions_suspectes(texte: str) -> list[str]:
    """Toute position précise en France qui n'est pas un gabarit."""
    suspectes = []
    for brut in _LAT.findall(texte):
        if _precise(brut) and _en_france(float(brut), None) and not _gabarit(float(brut), None):
            suspectes.append(f"latitude {brut}")
    for brut in _LON.findall(texte):
        if _precise(brut) and _en_france(None, float(brut)) and not _gabarit(None, float(brut)):
            suspectes.append(f"longitude {brut}")
    for b_lat, b_lon in _COUPLE.findall(texte):
        lat, lon = float(b_lat), float(b_lon)
        if _en_france(lat, lon) and not _gabarit(lat, lon):
            suspectes.append(f"couple ({b_lat}, {b_lon})")
    return suspectes


# Deux séries anciennes, synthétiques à l'évidence, antérieures au gabarit.
_SERIES_SYNTHETIQUES = {"AAAA-BBBB-CCCC", "1234-5678-9ABC"}


def series_suspectes(texte: str) -> list[str]:
    return [s for s in _SERIE.findall(texte)
            if not s.startswith("0000-0000-") and s.upper() not in _SERIES_SYNTHETIQUES]


def test_les_sources_parcourues_couvrent_les_deux_cotes():
    """Sans ce garde, un chemin mal écrit ferait passer tout le balayage."""
    noms = {p.name for p in SOURCES}
    assert "Discovery.test.tsx" in noms and "test_sessions.py" in noms
    assert len([p for p in SOURCES if p.suffix == ".tsx"]) >= 10


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: str(p.relative_to(RACINE)))
def test_aucune_serie_ni_position_reelle_dans_les_sources(source):
    texte = source.read_text(encoding="utf-8")
    assert series_suspectes(texte) == [], "série hors gabarit 0000-0000-NNNN"
    assert positions_suspectes(texte) == [], "position hors gabarit"


@pytest.mark.parametrize("texte", [
    # Valeurs publiques (centres de Nantes et de Lille), jamais de la flotte.
    "serial: 'ABCD-1234-EF56'",                   # C-14 : une série dans un test d'écran
    "gps['mbx'] = ((47.2184, -1.5536), 0.0)",     # C-14 : un couple sans nom de champ
    '"latitude": 50.6292, "longitude": 2.3522',   # une latitude hors gabarit
    '"latitude": 48.8566, "longitude": -1.5536',  # la longitude, que le garde ignorait
    "site_lat=48.8566, site_lon=3.0573",
])
def test_le_balayage_attrape_ce_qu_il_doit(texte):
    """Le balayage n'est une garde que s'il tombe sur un vrai cas."""
    assert series_suspectes(texte) or positions_suspectes(texte)


@pytest.mark.parametrize("texte", [
    "serial: '0000-0000-0063'",
    "id: 'a1b2c3d4-5678-9abc-def0-123456789abc'",  # un UUID n'est pas une série
    "_barycentre([(48.8566, 2.3522), (48.8576, 2.3522)])",
    "_barycentre([(-16.5, 179.9), (-16.5, -179.9)])",
    "site_lat=45.7640, site_lon=4.8357",
    "(48.0, -1.70)",
])
def test_le_balayage_laisse_passer_les_gabarits(texte):
    assert series_suspectes(texte) == [] and positions_suspectes(texte) == []
