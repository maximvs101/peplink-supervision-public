"""Les types MIME des interfaces servies, fixés plutôt que lus du système.

Sous Windows, `mimetypes` lit la base de registre, où un logiciel installé peut
avoir associé `.js` à `text/plain` : le navigateur refuse alors le script de
l'interface (strictement pour un module, et pour la feuille de style aussi) et
l'écran reste blanc. Le superviseur et le témoin Ethernet appellent
`fixer_types_mime()` avant de servir leurs fichiers.
"""

import mimetypes

_TYPES = (
    ("text/javascript", ".js"),
    ("text/css", ".css"),
    ("image/svg+xml", ".svg"),
    ("application/json", ".json"),
)


def fixer_types_mime() -> None:
    for type_, extension in _TYPES:
        mimetypes.add_type(type_, extension)
