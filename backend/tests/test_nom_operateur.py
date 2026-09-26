"""R-2.6 : nommer l'opérateur par son code réseau, pas par le libellé du modem.

Le modem du Dome publie « Mobile S Mobile » pour 208-10 : c'est SFR. Le libellé
reste la donnée enregistrée (le rapprochement des relevés sans code en dépend,
R-3.4) ; le NOM se calcule à la sortie, sur une table fermée. Un code absent de
la table garde le libellé plutôt qu'un nom deviné.
"""

from app.models import WanRadioStatus, nom_du_reseau


def test_the_four_national_networks_are_named_by_their_code():
    assert nom_du_reseau("Mobile S Mobile", "208-10") == "SFR"
    assert nom_du_reseau("Orange F", "208-01") == "Orange"
    assert nom_du_reseau("Free", "208-15") == "Free"
    assert nom_du_reseau("Bouygues", "208-20") == "Bouygues"


def test_an_unknown_code_or_no_code_keeps_the_modem_label():
    assert nom_du_reseau("Opérateur X", "208-99") == "Opérateur X"
    assert nom_du_reseau("Mobile S Mobile", None) == "Mobile S Mobile"
    assert nom_du_reseau(None, None) is None
    assert nom_du_reseau(None, "208-10") == "SFR"


def test_the_name_is_published_beside_the_label_which_stays_untouched():
    wan = WanRadioStatus(wan_id="3", wan_name="W", operator="Mobile S Mobile", plmn="208-10")
    assert (wan.operator, wan.operator_name) == ("Mobile S Mobile", "SFR")
    assert wan.model_dump()["operator_name"] == "SFR"
    # Un WAN Wi-Fi porte son SSID en guise de réseau : pas de code, rien ne change.
    wifi = WanRadioStatus(wan_id="6", wan_name="W", kind="wifi", operator="MonSSID")
    assert wifi.operator_name == "MonSSID"
    # Relu depuis sa propre sérialisation, le modèle ne s'y trompe pas.
    assert WanRadioStatus.model_validate(wan.model_dump()).operator_name == "SFR"
