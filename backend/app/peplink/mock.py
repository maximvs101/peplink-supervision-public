"""Simulateur de routeur Peplink : métriques radio réalistes et évolutives.

Chaque appel à get_radio_status() fait avancer la simulation d'un pas :
- marche aléatoire à retour à la moyenne (Ornstein-Uhlenbeck) sur RSRP/RSRQ/SINR ;
- handovers occasionnels (changement de cellule -> Cell ID/PCI/bande différents) ;
- épisodes de dégradation temporaires ; déconnexions rares.

Le générateur aléatoire est injectable (seed) pour des tests déterministes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from hashlib import sha1

from ..fleet import RouterConfig, WanConfig
from ..models import (
    BandMeasurement,
    GnssFix,
    DeviceCapabilities,
    DeviceInfo,
    PowerSource,
    WanRadioStatus,
    WifiLink,
)
from .router_api import _wifi_band

# Bandes plausibles par opérateur FR (n* = 5G NR).
_BANDS_BY_OPERATOR: dict[str, list[str]] = {
    "Orange": ["B20", "B3", "B7", "B28", "n78"],
    "SFR": ["B7", "B3", "B20", "n78"],
    "Bouygues": ["B20", "B3", "B7", "n78"],
    "Free": ["B28", "B3", "n78"],
}
_DEFAULT_BANDS = ["B20", "B3", "B7"]

# EARFCN/NR-ARFCN représentatif par bande (indicatif).
_ARFCN_BY_BAND: dict[str, int] = {
    "B20": 6300,
    "B3": 1575,
    "B7": 3100,
    "B28": 9410,
    "n78": 632628,
}


def _rat_for_band(band: str) -> str:
    """Technologie annoncée, alignée sur les valeurs réelles du firmware 8.5.x."""
    if band.startswith("n"):
        return "5G NSA"  # en NSA, une porteuse 5G s'appuie sur une ancre LTE
    if band in ("B7", "B3"):
        return "LTE-A"
    return "LTE"


def _bars_from_wifi(strength: float | None) -> int:
    """Barres Wi-Fi à partir de la force du signal (seuils usuels)."""
    if strength is None:
        return 0
    if strength >= -55:
        return 5
    if strength >= -65:
        return 4
    if strength >= -75:
        return 3
    if strength >= -85:
        return 2
    return 1


def _bars_from_rsrp(rsrp: float | None) -> int:
    if rsrp is None:
        return 0
    if rsrp >= -85:
        return 5
    if rsrp >= -95:
        return 4
    if rsrp >= -105:
        return 3
    if rsrp >= -115:
        return 2
    return 1


@dataclass
class _Cell:
    cell_id: int
    pci: int
    band: str
    earfcn: int
    # Ancre LTE des cellules 5G NSA (porteuse secondaire agrégée).
    anchor_band: str | None = None
    anchor_pci: int | None = None
    anchor_earfcn: int | None = None


@dataclass
class _WanState:
    cfg: WanConfig
    cells: list[_Cell]
    current: int  # index dans cells
    rsrp: float
    rsrq: float
    sinr: float
    connected: bool = True
    degraded: int = 0  # nb de pas restants en état dégradé
    down: int = 0  # nb de pas restants hors ligne
    uptime: int = 0  # secondes depuis la dernière connexion


class MockPeplinkClient:
    """Client Peplink simulé pour un routeur (implémente l'interface PeplinkClient)."""

    def __init__(self, router: RouterConfig, rng: random.Random | None = None) -> None:
        self.router_id = router.id
        self._name = router.name
        self._rng = rng or random.Random()
        self._wans: list[_WanState] = [self._init_wan(w) for w in router.wans]

    # --- initialisation ---
    def _init_wan(self, cfg: WanConfig) -> _WanState:
        if cfg.kind == "wifi":
            # Un lien Wi-Fi n'a ni cellule ni handover : seule la force varie.
            return _WanState(cfg=cfg, cells=[], current=0,
                             rsrp=cfg.base_rsrp, rsrq=-11.0, sinr=cfg.base_sinr)

        bands = _BANDS_BY_OPERATOR.get(cfg.operator, _DEFAULT_BANDS)
        # 3 cellules candidates (stations de base voisines) parmi lesquelles on bascule.
        cells: list[_Cell] = []
        for _ in range(3):
            enb = self._rng.randint(100_000, 999_999)
            sector = self._rng.randint(1, 3)
            band = self._rng.choice(bands)
            cell = _Cell(
                cell_id=enb * 256 + sector,
                pci=self._rng.randint(0, 503),
                band=band,
                earfcn=_ARFCN_BY_BAND.get(band, 0),
            )
            if band.startswith("n"):
                # 5G NSA : la porteuse 5G est agrégée à une ancre LTE.
                anchor = self._rng.choice([b for b in bands if not b.startswith("n")] or ["B3"])
                cell.anchor_band = anchor
                cell.anchor_pci = self._rng.randint(0, 503)
                cell.anchor_earfcn = _ARFCN_BY_BAND.get(anchor, 0)
            cells.append(cell)
        return _WanState(
            cfg=cfg,
            cells=cells,
            current=0,
            rsrp=cfg.base_rsrp,
            rsrq=-11.0,
            sinr=cfg.base_sinr,
        )

    # --- simulation ---
    def _ou(self, x: float, base: float, theta: float, sigma: float, lo: float, hi: float) -> float:
        """Un pas de marche aléatoire à retour à la moyenne, borné."""
        x = x + theta * (base - x) + self._rng.gauss(0.0, sigma)
        return max(lo, min(hi, x))

    def _step_wan(self, st: _WanState) -> WanRadioStatus:
        cfg = st.cfg

        # Déconnexion rare (~0.3%/pas), dure 1-3 pas.
        if st.down > 0:
            st.down -= 1
            st.connected = st.down == 0
        elif self._rng.random() < 0.003:
            st.down = self._rng.randint(1, 3)
            st.connected = False

        if not st.connected:
            st.uptime = 0  # une coupure remet le compteur de session à zéro
            return WanRadioStatus(
                wan_id=cfg.id,
                wan_name=cfg.name,
                connected=False,
                operator=cfg.operator,
                plmn=f"{cfg.mcc}-{cfg.mnc:02d}",
                signal_bars=0,
                uptime_s=0,
                status_message="Disconnected",
            )

        st.uptime += 3  # un pas de simulation ~ un cycle de scrutation

        if cfg.kind == "wifi":
            return self._step_wifi(st)

        # Handover occasionnel (~3%/pas) : bascule sur une autre cellule candidate.
        if len(st.cells) > 1 and self._rng.random() < 0.03:
            choices = [i for i in range(len(st.cells)) if i != st.current]
            st.current = self._rng.choice(choices)
            st.rsrp += self._rng.uniform(-3.0, 3.0)  # nouvelle cellule = léger saut

        # Épisode de dégradation (~1.5%/pas), dure 4-14 pas.
        if st.degraded == 0 and self._rng.random() < 0.015:
            st.degraded = self._rng.randint(4, 14)
        deg_rsrp = -8.0 if st.degraded > 0 else 0.0
        deg_sinr = -8.0 if st.degraded > 0 else 0.0
        if st.degraded > 0:
            st.degraded -= 1

        # Marches aléatoires bornées.
        st.rsrp = self._ou(st.rsrp, cfg.base_rsrp + deg_rsrp, 0.15, 1.3, -125.0, -65.0)
        st.rsrq = self._ou(st.rsrq, -11.0, 0.2, 0.4, -20.0, -5.0)
        base_sinr = cfg.base_sinr + deg_sinr + 0.2 * (st.rsrp - cfg.base_rsrp)
        st.sinr = self._ou(st.sinr, base_sinr, 0.18, 1.0, -10.0, 32.0)

        cell = st.cells[st.current]
        rat = _rat_for_band(cell.band)
        rssi = max(-110.0, min(-45.0, st.rsrp + 20.0 + self._rng.gauss(0.0, 1.0)))

        # Débits corrélés au SINR (5G plus haut).
        cap = 300_000.0 if rat.startswith("5G") else 150_000.0
        quality = max(0.0, min(1.0, (st.sinr + 5.0) / 30.0))
        rx = round(cap * quality * self._rng.uniform(0.7, 1.0), 1)
        tx = round(rx * self._rng.uniform(0.1, 0.25), 1)

        # Porteuse principale + éventuelle ancre LTE (structure du matériel réel).
        bands = [
            BandMeasurement(
                rat="5G" if cell.band.startswith("n") else "LTE",
                band=cell.band,
                label=f"{'5G' if cell.band.startswith('n') else 'LTE'} Band {cell.band}",
                channel=cell.earfcn,
                pci=cell.pci,
                channel_width="90 MHz" if cell.band.startswith("n") else "20 MHz",
                rsrp=round(st.rsrp, 1),
                rsrq=round(st.rsrq, 1),
                sinr=round(st.sinr, 1),
                rssi=None if cell.band.startswith("n") else round(rssi, 1),
            )
        ]
        if cell.anchor_band:
            # L'ancre est typiquement en bande basse : meilleure couverture, moins de débit.
            anchor_rsrp = round(min(-65.0, st.rsrp + self._rng.uniform(3.0, 9.0)), 1)
            bands.append(
                BandMeasurement(
                    rat="LTE",
                    band=cell.anchor_band,
                    label=f"LTE Band {cell.anchor_band}",
                    channel=cell.anchor_earfcn,
                    pci=cell.anchor_pci,
                    channel_width="20 MHz",
                    rsrp=anchor_rsrp,
                    rsrq=round(st.rsrq + self._rng.uniform(-0.5, 1.5), 1),
                    sinr=round(max(-10.0, st.sinr + self._rng.uniform(-2.0, 3.0)), 1),
                    rssi=round(anchor_rsrp + 20.0, 1),
                )
            )

        return WanRadioStatus(
            wan_id=cfg.id,
            wan_name=cfg.name,
            connected=True,
            operator=cfg.operator,
            plmn=f"{cfg.mcc}-{cfg.mnc:02d}",
            rat=rat,
            band=cell.band,
            earfcn=cell.earfcn,
            pci=cell.pci,
            cell_id=cell.cell_id,
            # La station et le secteur, comme le parseur les dérive d'un ECI
            # (`_split_eci`) : sans eux, la fiche de session ne pouvait jamais
            # dire « même station » en démonstration, et la diversité de trajet
            # promise par les handovers simulés n'existait pas.
            enb_id=cell.cell_id // 256,
            sector_id=cell.cell_id % 256,
            tac=10_000 + (cell.cell_id % 5000),
            rsrp=round(st.rsrp, 1),
            rsrq=round(st.rsrq, 1),
            sinr=round(st.sinr, 1),
            rssi=round(rssi, 1),
            signal_bars=_bars_from_rsrp(st.rsrp),
            bands=bands,
            tx_kbps=tx,
            rx_kbps=rx,
            uptime_s=st.uptime,
            status_message=f"Connected to {cfg.operator}",
        )

    def _step_wifi(self, st: _WanState) -> WanRadioStatus:
        """Un pas pour un lien Wi-Fi amont : seule la force du signal évolue."""
        cfg = st.cfg
        st.rsrp = self._ou(st.rsrp, cfg.base_rsrp, 0.15, 1.2, -90.0, -40.0)
        strength = round(st.rsrp, 1)
        # LA règle canal -> bande, celle de l'analyseur réel. Le simulateur en
        # avait sa propre copie, à deux paliers au lieu de trois : un canal 6 GHz
        # y ressortait « 5 GHz », et le simulateur montrait donc un écran que le
        # vrai routeur n'aurait jamais produit — ce qui lui retire sa seule
        # raison d'être.
        band = _wifi_band(cfg.channel)

        return WanRadioStatus(
            kind="wifi",
            wan_id=cfg.id,
            wan_name=cfg.name,
            connected=True,
            operator=cfg.ssid,
            rat="Wi-Fi",
            band=band,
            rssi=strength,
            signal_bars=_bars_from_wifi(strength),
            wifi=WifiLink(
                ssid=cfg.ssid,
                bssid="aa:bb:cc:dd:ee:ff",
                channel=cfg.channel,
                band=band,
                signal_dbm=strength,
                signal_level=_bars_from_wifi(strength),
            ),
            uptime_s=st.uptime,
            status_message=f"Connected to {cfg.ssid}",
        )

    async def get_radio_status(self, echeance: float | None = None) -> list[WanRadioStatus]:
        return [self._step_wan(st) for st in self._wans]

    async def get_location(self) -> GnssFix | None:
        # Le simulateur n'émule pas de GPS : la position vient de la configuration.
        return None

    async def get_device_info(self) -> DeviceInfo:
        """Identité plausible, pour que la démonstration montre le même écran."""
        return DeviceInfo(
            model="Pepwave MAX HD4 MBX (simulé)",
            product_code="MAX-HD4-MBX-SIMU",
            # `hash()` d'une chaîne est SALÉ par processus depuis Python 3.3 :
            # le même équipement simulé rendait `9272` puis `0943` au
            # redémarrage. Or le numéro de série est LA clé d'identité stable du
            # projet — celle qui survit à un changement d'adresse. Un simulateur
            # qui en change à chaque lancement fabrique un équipement neuf à
            # chaque fois, et l'appairage en démonstration accumulait les
            # doublons. Un condensé déterministe, donc.
            serial=f"0000-0000-{int(sha1(self.router_id.encode()).hexdigest()[:8], 16) % 10000:04d}",
            firmware="8.5.4 build 6253",
            hardware_revision="4",
            name=self._name,
            cpu_load_percent=round(self._rng.uniform(1.0, 9.0), 1),
            power_watts=round(self._rng.uniform(15.0, 25.0), 1),
            power_sources=[
                PowerSource(name="DC Source A", connected=True),
                PowerSource(name="DC Source B", connected=False),
            ],
            mac_lan="a8:c0:ea:00:00:00",
        )

    async def get_capabilities(self) -> DeviceCapabilities:
        """Capacités déduites de la flotte simulée."""
        return DeviceCapabilities(
            gps=False,  # le simulateur ne produit pas de position
            # Le simulateur ne produit AUCUN objet tunnel : `get_radio_status`
            # renvoie des WAN dont `tunnel` reste à None. Déclarer SpeedFusion
            # rallumait donc, en démonstration, les graphes de latence et de perte
            # vides que le garde-fou de `metricsFor` venait d'éteindre — la
            # capacité annoncée promettait une mesure que rien ne fournit. Même
            # règle que `gps` ci-dessus : on ne déclare que ce qu'on sait produire.
            pepvpn=False,
            wan_types={w.cfg.id: w.cfg.kind for w in self._wans},
            # Le simulateur fabrique des débits : autant l'assumer, l'écran
            # doit montrer ce qu'un vrai équipement montrerait.
            per_wan_throughput=True,
        )

    async def aclose(self) -> None:  # rien à libérer
        return None
