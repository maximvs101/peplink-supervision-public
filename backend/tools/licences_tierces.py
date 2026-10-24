"""Écrit LICENCES-TIERCES.txt : les mentions des composants tiers que les
exécutables embarquent.

    .venv/bin/python tools/licences_tierces.py                      # dist/LICENCES-TIERCES.txt
    .venv/bin/python tools/licences_tierces.py --sans-interface     # témoin : serveur seul
    .venv/bin/python tools/licences_tierces.py --sortie autre/chemin.txt

Le superviseur et le témoin sont sous licence MIT, mais leurs exécutables
embarquent du code d'autrui — FastAPI, pydantic, React, recharts, les polices
@fontsource, l'interpréteur Python… — sous MIT, BSD, Apache, PSF, OFL, MPL, dont
la plupart demandent que leur texte accompagne toute distribution binaire.
`construire_executable.py` appelle ce module avant PyInstaller et embarque le
fichier ; la Release le publie.

Ce qui est relevé, et d'où :

  - Python : EXACTEMENT la fermeture des dépendances d'exécution de
    `requirements.txt` — ses lignes, puis ce qu'elles requièrent, marqueurs
    évalués pour CE système (pywin32-ctypes de keyring n'existe que sous
    Windows), extras non suivis sauf demandés. Ni les outils de test, ni ceux
    de construction. Textes : les fichiers LICENSE*/LICENCE*/COPYING*/NOTICE*
    que le paquet installé déclare (`dist-info/licenses/`, son RECORD).
  - CPython : le LICENSE.txt de l'installation, cherché comme le fait
    `license()` (module `site`), et la section « Licenses and Acknowledgements
    for Incorporated Software » de sa documentation (Doc/license.rst),
    vendorisée par majeure.mineure dans tools/licences/ — le LICENSE.txt de
    python.org pour macOS n'en a rien. Les bibliothèques natives que
    l'interpréteur lie (OpenSSL, Expat…) y renvoient ; celles qu'elle ne cite
    pas (bzip2, liblzma, SQLite) ont leur texte vendorisé.
  - Le chargeur de PyInstaller : GPL avec exception — mention de courtoisie.
  - L'interface : les dépendances de production du verrou npm (`dev` exclues,
    fermeture comprise : c'est ce que liste `npm ls --omit=dev --all`), et le
    code que la compilation inline sans qu'il soit une dépendance de
    production (le petit moteur de modules de Rolldown, l'amorce de
    préchargement de Vite) — vérifié dans les cartes de sources du bundle le
    24/10/2026 : rien d'autre n'y entre.

Rien n'est écrit de mémoire : un texte de licence que le paquet ne distribue
pas est repris de sa source officielle, à l'étiquette de la version embarquée,
et vendorisé dans tools/licences/ avec l'URL et l'étiquette en tête (victory-
vendor) ; sans cela, il est SIGNALÉ (« texte non distribué avec le paquet, voir
<URL> »), jamais reconstitué. Une licence hors de la liste blanche — une GPL qu'une montée de
Dependabot ferait entrer en silence — fait échouer la génération, donc la
construction. Sortie déterministe (tri, aucune date) : un diff entre deux
constructions montre ce qui a changé.

Bibliothèque standard seule : il tourne là où tourne la construction.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
FRONTEND = RACINE.parent / "frontend"
NOM = "LICENCES-TIERCES.txt"
# Les textes qu'aucun paquet installé ne distribue, repris de leur source
# officielle à l'étiquette de la version embarquée, URL et étiquette en tête.
LICENCES = Path(__file__).resolve().parent / "licences"

# Les licences acceptées, en identifiants SPDX : toutes permissives, ou à
# copyleft limité au fichier (MPL-2.0 : les certificats de certifi, embarqués
# tels quels, dont la source est indiquée). Une licence absente d'ici arrête la
# génération : l'ajouter est une décision, pas un effet de bord.
LISTE_BLANCHE = frozenset({
    "MIT", "ISC", "0BSD", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0",
    "PSF-2.0", "Python-2.0", "OFL-1.1", "MPL-2.0", "Zlib", "blessing",
    "bzip2-1.0.6",
    # liblzma de XZ Utils avant 5.6 : dédié au domaine public, sans condition.
    "LicenseRef-domaine-public",
})

# Le champ `License` libre des métadonnées anciennes, quand il n'est pas déjà un
# identifiant SPDX. Court seulement : un texte entier n'y est pas une licence.
_NOMS_LIBRES = {
    "mit license": "MIT", "the mit license": "MIT", "apache 2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0", "apache software license": "Apache-2.0",
    "bsd 3-clause": "BSD-3-Clause", "new bsd": "BSD-3-Clause", "isc license": "ISC",
}
# Les classificateurs « License :: … », en dernier recours. « BSD License »
# n'y est pas : il ne dit pas combien de clauses.
_CLASSIFICATEURS = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
}

# Les fichiers de mentions, à la racine d'un paquet npm ou déclarés par un
# paquet Python. AUTHORS n'en est pas : une liste de noms, pas une licence.
_MENTION = re.compile(r"^(licen[cs]e|copying|notice)", re.IGNORECASE)


class LicencesRefusees(Exception):
    """Une licence hors liste blanche, ou inconnue : la construction s'arrête."""


@dataclass
class Composant:
    nom: str
    version: str
    licence: str | None
    source: str
    textes: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""
    # Le chargeur de PyInstaller seul : mentionné, hors liste blanche.
    courtoisie: bool = False


# --- Python : la fermeture des dépendances d'exécution -----------------------

def normaliser(nom: str) -> str:
    """Le nom canonique d'un paquet (PEP 503) : `PyYAML` = `pyyaml`, `jaraco.classes` = `jaraco-classes`."""
    return re.sub(r"[-_.]+", "-", nom).lower()


def exigences(fichier: Path) -> list[str]:
    """Les noms de paquets d'un requirements.txt — ni options, ni inclusions."""
    noms = []
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        ligne = ligne.split("#", 1)[0].strip()
        if not ligne or ligne.startswith("-"):
            continue
        noms.append(re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", ligne).group(0))
    return noms


def _environnement() -> dict[str, str]:
    """Les variables des marqueurs (PEP 508) pour l'interpréteur qui tourne."""
    return {
        "os_name": os.name,
        "sys_platform": sys.platform,
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "platform_python_implementation": platform.python_implementation(),
        "implementation_name": sys.implementation.name,
        "implementation_version": platform.python_version(),
        "python_version": ".".join(platform.python_version_tuple()[:2]),
        "python_full_version": platform.python_version(),
    }


_VERSIONNEES = {"python_version", "python_full_version", "implementation_version"}
_JETON = re.compile(r"\s*(\(|\)|===|==|!=|~=|<=|>=|<|>|not\s+in\b|in\b|and\b|or\b|'[^']*'|\"[^\"]*\"|[A-Za-z_][A-Za-z0-9_.]*)")


def _version(texte: str) -> tuple[int, ...]:
    return tuple(int(m) for m in re.findall(r"\d+", texte.split("+")[0])[:4]) or (0,)


def _comparer(gauche: str, op: str, droite: str, versionnee: bool) -> bool:
    if op in ("in", "not in"):
        dedans = gauche in droite
        return dedans if op == "in" else not dedans
    if op == "===":
        return gauche == droite
    if versionnee:
        a, b = _version(gauche), _version(droite)
        if op == "~=":
            # « compatible » : au moins b, et même préfixe sauf le dernier rang.
            return a >= b and a[: max(len(b) - 1, 1)] == b[: max(len(b) - 1, 1)]
        n = max(len(a), len(b))
        a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    else:
        a, b = gauche, droite  # type: ignore[assignment]
        if op == "~=":
            raise ValueError(f"~= sur une chaîne : {gauche!r}")
    return {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def evaluer_marqueur(marqueur: str, extras: frozenset[str] = frozenset(),
                     env: dict[str, str] | None = None) -> bool:
    """Un marqueur d'environnement (PEP 508), évalué sans `packaging`.

    `extra == "x"` est vrai si l'extra x a été demandé. Un marqueur que ce
    petit analyseur ne comprend pas lève une erreur : mieux vaut une
    construction arrêtée qu'une dépendance écartée à tort."""
    env = _environnement() if env is None else env
    jetons, pos = [], 0
    while pos < len(marqueur):
        m = _JETON.match(marqueur, pos)
        if not m:
            if marqueur[pos:].strip() == "":
                break
            raise ValueError(f"marqueur illisible : {marqueur!r}")
        jetons.append(re.sub(r"\s+", " ", m.group(1)))
        pos = m.end()
    i = 0

    def valeur() -> tuple[str, str | None]:
        nonlocal i
        jeton = jetons[i]
        i += 1
        if jeton[0] in "'\"":
            return jeton[1:-1], None
        if jeton == "extra":
            return "", "extra"
        if jeton not in env:
            raise ValueError(f"variable de marqueur inconnue : {jeton!r}")
        return env[jeton], jeton

    def comparaison() -> bool:
        nonlocal i
        if jetons[i] == "(":
            i += 1
            r = disjonction()
            i += 1  # « ) »
            return r
        gauche, vg = valeur()
        op = jetons[i]
        i += 1
        droite, vd = valeur()
        if "extra" in (vg, vd):
            demande = normaliser(droite if vg == "extra" else gauche)
            if op not in ("==", "!="):
                raise ValueError(f"opérateur {op!r} sur extra")
            present = demande in {normaliser(e) for e in extras}
            return present if op == "==" else not present
        return _comparer(gauche, op, droite, bool({vg, vd} & _VERSIONNEES))

    def conjonction() -> bool:
        nonlocal i
        r = comparaison()
        while i < len(jetons) and jetons[i] == "and":
            i += 1
            r = comparaison() and r
        return r

    def disjonction() -> bool:
        nonlocal i
        r = conjonction()
        while i < len(jetons) and jetons[i] == "or":
            i += 1
            r = conjonction() or r
        return r

    resultat = disjonction()
    if i != len(jetons):
        raise ValueError(f"marqueur illisible : {marqueur!r}")
    return resultat


def _exigence(texte: str) -> tuple[str, frozenset[str], str | None]:
    """« nom[extras] (version) ; marqueur » → (nom, extras, marqueur)."""
    tete, _, marqueur = texte.partition(";")
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[([^\]]*)\])?", tete)
    extras = frozenset(e.strip() for e in (m.group(2) or "").split(",") if e.strip())
    return m.group(1), extras, marqueur.strip() or None


def _index(chemins: list[str] | None) -> dict[str, metadata.Distribution]:
    """Les paquets installés, par nom canonique ; le premier du chemin l'emporte, comme à l'import."""
    index: dict[str, metadata.Distribution] = {}
    distributions = metadata.distributions(path=chemins) if chemins is not None else metadata.distributions()
    for dist in distributions:
        nom = dist.metadata["Name"]
        if nom:
            index.setdefault(normaliser(nom), dist)
    return index


def fermeture_python(racines: list[str], chemins: list[str] | None = None,
                     env: dict[str, str] | None = None) -> dict[str, metadata.Distribution]:
    """Les racines et tout ce qu'elles requièrent ici, extras demandés compris."""
    index = _index(chemins)
    demandes: dict[str, set[str]] = {}
    a_voir = [(r, frozenset()) for r in racines]
    absents = []
    while a_voir:
        nom, extras = a_voir.pop()
        cle = normaliser(nom)
        deja = demandes.get(cle)
        if deja is not None and extras <= deja:
            continue
        demandes.setdefault(cle, set()).update(extras)
        dist = index.get(cle)
        if dist is None:
            absents.append(nom)
            continue
        for exigence in dist.requires or []:
            sous, sous_extras, marqueur = _exigence(exigence)
            if marqueur is None or evaluer_marqueur(marqueur, frozenset(demandes[cle]), env):
                a_voir.append((sous, sous_extras))
    if absents:
        raise LicencesRefusees(
            "requis mais absents de l'environnement : " + ", ".join(sorted(set(absents)))
            + " — installer requirements.txt avant de générer")
    return {cle: index[cle] for cle in demandes}


def licence_python(dist: metadata.Distribution) -> str | None:
    """L'identifiant SPDX déclaré : `License-Expression`, puis `License` court, puis classificateurs."""
    md = dist.metadata
    expression = (md.get("License-Expression") or "").strip()
    if expression:
        return expression
    libre = (md.get("License") or "").strip()
    if libre and "\n" not in libre and len(libre) <= 60:
        if libre in LISTE_BLANCHE or re.fullmatch(r"[A-Za-z0-9.+-]+(?: (?:AND|OR) [A-Za-z0-9.+-]+)*", libre):
            return _NOMS_LIBRES.get(libre.lower(), libre)
        if libre.lower() in _NOMS_LIBRES:
            return _NOMS_LIBRES[libre.lower()]
    # Un classificateur hors table (GPL, « BSD License » sans nombre de
    # clauses…) devient un `LicenseRef-` à son nom : hors liste blanche, il
    # arrête la génération en disant lequel.
    trouves = sorted({
        _CLASSIFICATEURS.get(c) or "LicenseRef-" + re.sub(r"[^A-Za-z0-9.]+", "-", c.split("::")[-1].strip())
        for c in md.get_all("Classifier") or [] if c.startswith("License ::")
    })
    return " AND ".join(trouves) if trouves else None


_ETIQUETTES_SOURCE = ("source", "source code", "repository", "github", "homepage", "home")


def source_python(dist: metadata.Distribution) -> str:
    md = dist.metadata
    urls = {}
    for entree in md.get_all("Project-URL") or []:
        etiquette, _, url = entree.partition(",")
        urls.setdefault(etiquette.strip().lower(), url.strip())
    for etiquette in _ETIQUETTES_SOURCE:
        if urls.get(etiquette):
            return urls[etiquette]
    return (md.get("Home-page") or "").strip() or f"https://pypi.org/project/{md['Name']}/"


def _lire(chemin: Path) -> str:
    texte = chemin.read_bytes().decode("utf-8-sig", errors="replace")
    return texte.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"


def textes_python(dist: metadata.Distribution) -> list[tuple[str, str]]:
    """Les fichiers de mentions que le paquet déclare dans son RECORD, sans doublon de contenu."""
    textes, vus = [], set()
    for fichier in sorted(dist.files or [], key=str):
        if not _MENTION.match(fichier.name):
            continue
        chemin = Path(dist.locate_file(fichier))
        if not chemin.is_file():
            continue
        texte = _lire(chemin)
        if texte not in vus:
            vus.add(texte)
            textes.append((str(fichier).replace("\\", "/"), texte))
    return textes


def composants_python(requirements: Path, chemins: list[str] | None = None,
                      env: dict[str, str] | None = None) -> list[Composant]:
    fermeture = fermeture_python(exigences(requirements), chemins, env)
    return [Composant(nom=dist.metadata["Name"], version=dist.version,
                      licence=licence_python(dist), source=source_python(dist),
                      textes=textes_python(dist))
            for _, dist in sorted(fermeture.items())]


# --- CPython, ses bibliothèques natives, le chargeur de PyInstaller ----------

def licence_cpython() -> Path | None:
    """Le LICENSE.txt de l'installation, cherché comme `license()` (module `site`)."""
    ici = Path(os.__file__).resolve().parent
    for dossier in (ici.parent, ici, Path(sys.base_prefix)):
        for nom in ("LICENSE.txt", "LICENSE"):
            if (dossier / nom).is_file():
                return dossier / nom
    return None


_SECTION_CPYTHON = "Licenses and Acknowledgements for Incorporated Software"


def section_cpython() -> Path:
    """La section des logiciels incorporés de la documentation de CPython,
    vendorisée pour la majeure.mineure de l'interpréteur qui génère."""
    majeure_mineure = ".".join(map(str, sys.version_info[:2]))
    fichier = LICENCES / f"cpython-{majeure_mineure}.txt"
    if not fichier.is_file():
        raise LicencesRefusees(
            f"{fichier} absent pour Python {majeure_mineure} : reprendre la section « "
            f"{_SECTION_CPYTHON} » de Doc/license.rst à l'étiquette v{majeure_mineure}.x "
            "du dépôt python/cpython (celle du Python embarqué), URL et étiquette en tête, "
            "comme le fichier de la version précédente")
    return fichier


def composant_cpython() -> Composant:
    texte = licence_cpython()
    section = section_cpython()
    textes = [("LICENSE.txt de l'installation", _lire(texte))] if texte else []
    textes.append((f"Doc/license.rst — {_SECTION_CPYTHON} (tools/licences/{section.name})",
                   _lire(section)))
    return Composant(
        nom="CPython (interpréteur et bibliothèque standard)",
        version=platform.python_version(), licence="Python-2.0",
        source="https://www.python.org/psf/license/",
        textes=textes,
        note="Python-2.0 : la PSF License Agreement version 2 et les licences historiques "
             "(BeOpen, CNRI, CWI) qu'elle accompagne. Le LICENSE.txt installé ne reproduit "
             "pas toujours les mentions des logiciels incorporés (celui de python.org pour "
             "macOS n'en a aucune) : la section de la documentation officielle les donne, "
             "reprise à l'étiquette citée en tête.")


def _version_native(module: str, attribut: str) -> str:
    try:
        valeur = getattr(__import__(module), attribut)
    except (ImportError, AttributeError):
        return "inconnue"
    return str(valeur).split()[1] if module == "ssl" else str(valeur).removeprefix("expat_")


def composants_natifs(texte_cpython: str) -> list[Composant]:
    """Les bibliothèques C que l'interpréteur lie, et que l'exécutable emporte avec ses modules.

    `texte_cpython` : la section des logiciels incorporés de la documentation
    de CPython. Une bibliothèque qu'elle ne cite pas (bzip2, liblzma, SQLite en
    3.12) prend son texte vendorisé dans tools/licences/, repris de sa source
    officielle à la version que CPython embarque ; à défaut, l'adresse."""
    # (nom, module d'extension, attribut de version, licence, mot cherché dans le texte de CPython, adresse)
    table = [
        ("OpenSSL", "ssl", "OPENSSL_VERSION", "Apache-2.0", "openssl", "https://openssl-library.org/source/license/"),
        ("SQLite", "sqlite3", "sqlite_version", "blessing", "sqlite", "https://sqlite.org/copyright.html"),
        ("zlib", "zlib", "ZLIB_RUNTIME_VERSION", "Zlib", "zlib", "https://zlib.net/zlib_license.html"),
        ("Expat", "pyexpat", "EXPAT_VERSION", "MIT", "expat", "https://github.com/libexpat/libexpat/blob/master/expat/COPYING"),
        ("mpdecimal", "decimal", "__libmpdec_version__", "BSD-2-Clause", "libmpdec", "https://www.bytereef.org/mpdecimal/"),
        ("libffi", "_ctypes", "", "MIT", "libffi", "https://github.com/libffi/libffi/blob/master/LICENSE"),
        ("bzip2", "_bz2", "", "bzip2-1.0.6", "bzip2", "https://sourceware.org/bzip2/"),
        # liblzma est dans le domaine public jusqu'à XZ 5.4 (0BSD depuis 5.6) :
        # CPython 3.12 embarque 5.2.5 (Windows) ou 5.2.3 (macOS, python.org).
        ("liblzma (XZ Utils)", "_lzma", "", "LicenseRef-domaine-public", "lzma", "https://github.com/tukaani-project/xz/blob/v5.2.5/COPYING"),
    ]
    bas = texte_cpython.lower()
    composants = []
    for nom, module, attribut, licence, mot, adresse in table:
        try:
            __import__(module)
        except ImportError:
            continue
        version = _version_native(module, attribut) if attribut else "inconnue"
        textes = []
        if mot in bas:
            note = f"Texte : reproduit dans la section « {_SECTION_CPYTHON} » de CPython ci-dessus."
        elif nom in _VENDORISES_NATIFS:
            fichier = LICENCES / _VENDORISES_NATIFS[nom]
            textes = [(f"tools/licences/{fichier.name}", _lire(fichier))]
            note = "Texte : repris de la source officielle citée en tête, à la version que CPython embarque."
        else:
            note = f"Texte non distribué avec l'installation de CPython de ce poste, voir {adresse}"
        if nom == "OpenSSL" and _version(version) < (3,):
            licence = "OpenSSL"
        composants.append(Composant(nom=nom, version=version, licence=licence, source=adresse,
                                    textes=textes,
                                    note=note + " Liée par l'interpréteur, embarquée avec son module "
                                    f"`{module}` si l'application l'importe (sous macOS, zlib, "
                                    "bzip2 et libffi sont celles du système, non embarquées)."))
    return composants


# Les textes que la documentation de CPython ne donne pas, vendorisés.
_VENDORISES_NATIFS = {
    "bzip2": "bzip2-1.0.8.txt",
    "liblzma (XZ Utils)": "xz-5.2.5.txt",
    "SQLite": "sqlite-3.45.3.txt",
}


def composant_pyinstaller(chemins: list[str] | None = None) -> Composant:
    dist = _index(chemins).get("pyinstaller")
    note = ("Mention de courtoisie, rien d'obligatoire : l'« exception du chargeur » de la GPL "
            "permet d'embarquer le chargeur et ses fichiers dans un programme et de distribuer "
            "l'ensemble sans contrainte venue d'eux ; les modules d'exécution (rthooks) sont "
            "sous Apache-2.0.")
    if dist is None:
        return Composant(nom="PyInstaller (chargeur de l'exécutable)", version="inconnue",
                         licence="GPL-2.0-or-later WITH Bootloader-exception",
                         source="https://pyinstaller.org", courtoisie=True,
                         note=note + " PyInstaller est absent de l'environnement où ce fichier a été produit.")
    return Composant(nom="PyInstaller (chargeur de l'exécutable)", version=dist.version,
                     licence="GPL-2.0-or-later WITH Bootloader-exception",
                     source=source_python(dist), textes=textes_python(dist), courtoisie=True, note=note)


# --- Interface : le verrou npm ------------------------------------------------

def _source_npm(paquet: dict, nom: str) -> str:
    depot = paquet.get("repository")
    url = depot.get("url") if isinstance(depot, dict) else depot
    if isinstance(url, str) and url:
        url = re.sub(r"^git\+", "", url).removesuffix(".git")
        url = re.sub(r"^git://", "https://", url)
        url = re.sub(r"^github:", "https://github.com/", url)
        if "://" not in url:
            url = f"https://github.com/{url}"
        return url
    return paquet.get("homepage") or f"https://www.npmjs.com/package/{nom}"


def _textes_npm(dossier: Path) -> list[tuple[str, str]]:
    return [(f.name, _lire(f)) for f in sorted(dossier.iterdir(), key=lambda f: f.name)
            if f.is_file() and _MENTION.match(f.name)]


def _composant_npm(dossier: Path, nom: str, licence: str | None, note: str = "") -> Composant:
    paquet = json.loads((dossier / "package.json").read_text(encoding="utf-8"))
    licence = paquet.get("license") or licence
    if isinstance(licence, dict):  # ancienne forme { "type": "MIT" }
        licence = licence.get("type")
    return Composant(nom=paquet.get("name") or nom, version=str(paquet.get("version") or "inconnue"),
                     licence=licence, source=_source_npm(paquet, nom), textes=_textes_npm(dossier), note=note)


# Ce que la compilation INLINE dans le bundle sans être une dépendance de
# production : relevé dans les cartes de sources et le bundle le 24/10/2026.
# Rolldown y écrit son moteur de modules (`__commonJS`, `__toESM`… : React est
# publié en CommonJS) ; Vite, l'amorce `modulepreload` et le préchargement des
# morceaux. Du LICENSE.md de Vite, la seule section de Vite lui-même : le reste
# y concerne les dépendances qu'il embarque pour SA ligne de commande, qui
# n'entrent pas dans le bundle.
_INLINES = {
    "rolldown": "Moteur de modules inliné dans le bundle par la compilation.",
    "vite": "Amorce de préchargement des modules inlinée dans le bundle par la compilation.",
}
_VITE_DEPENDANCES = "# Licenses of bundled dependencies"
# Les paquets qui ne distribuent pas leur licence : (version, texte vendorisé).
# Pour une autre version, le texte n'est pas repris — signalé, et le test de
# la version vendorisée échoue.
_VENDORISES_NPM = {
    "victory-vendor": ("37.3.6", "victory-vendor-37.3.6.txt"),
}


def composants_interface(frontend: Path) -> list[Composant]:
    """Les dépendances de production du verrou, fermeture comprise, plus le code inliné."""
    verrou = json.loads((frontend / "package-lock.json").read_text(encoding="utf-8"))
    modules = frontend / "node_modules"
    if not modules.is_dir():
        raise LicencesRefusees(f"{modules} absent — `npm ci` d'abord : les textes de licence y sont")
    vus: dict[tuple[str, str], Composant] = {}
    for chemin, entree in sorted(verrou.get("packages", {}).items()):
        if not chemin or entree.get("dev") or entree.get("link"):
            continue
        dossier = frontend / chemin
        nom = chemin.rsplit("node_modules/", 1)[-1]
        if not (dossier / "package.json").is_file():
            if entree.get("optional"):
                continue  # dépendance optionnelle d'un autre système : non installée, non embarquée
            raise LicencesRefusees(f"{chemin} absent — `npm ci` d'abord")
        composant = _composant_npm(dossier, nom, entree.get("license"))
        vendorise = _VENDORISES_NPM.get(composant.nom)
        if not composant.textes and vendorise and vendorise[0] == composant.version:
            # Sa licence, vendorisée depuis son dépôt ; puis celles des modules
            # qu'il vendorise lui-même, qu'il distribue dans lib-vendor/.
            fichier = LICENCES / vendorise[1]
            composant.textes = [(f"tools/licences/{fichier.name}", _lire(fichier))] + [
                (f"lib-vendor/{sous.name}/{t}", texte)
                for sous in sorted((dossier / "lib-vendor").iterdir(), key=lambda d: d.name)
                if sous.is_dir() for t, texte in _textes_npm(sous)]
        vus.setdefault((composant.nom, composant.version), composant)
    for nom, note in _INLINES.items():
        dossier = modules / nom
        if not (dossier / "package.json").is_file():
            raise LicencesRefusees(f"{dossier} absent — `npm ci` d'abord")
        composant = _composant_npm(dossier, nom, None, note)
        if nom == "vite":
            composant.textes = [(t, c.split(_VITE_DEPENDANCES, 1)[0].rstrip() + "\n")
                                for t, c in composant.textes]
        vus.setdefault((composant.nom, composant.version), composant)
    return [vus[cle] for cle in sorted(vus, key=lambda c: (c[0].lower(), c[1]))]


# --- Liste blanche, mise en forme --------------------------------------------

def identifiants(expression: str) -> set[str]:
    """Les identifiants SPDX d'une expression — `MIT AND ISC`, `(A OR B)`, `X WITH exception`."""
    sans_exception = re.sub(r"\bWITH\s+\S+", "", expression)
    return {j for j in re.split(r"[()\s]+", sans_exception) if j and j not in ("AND", "OR")}


def verifier(composants: list[Composant]) -> None:
    """Toute licence connue et dans la liste blanche ; sinon, la liste des fautes."""
    fautes = []
    for c in composants:
        if c.courtoisie:
            continue
        if not c.licence:
            fautes.append(f"{c.nom} {c.version} : licence inconnue")
            continue
        if "WITH" in c.licence:
            fautes.append(f"{c.nom} {c.version} : exception de licence non prévue ({c.licence})")
        hors = sorted(identifiants(c.licence) - LISTE_BLANCHE)
        if hors:
            fautes.append(f"{c.nom} {c.version} : {c.licence} hors liste blanche ({', '.join(hors)})")
    if fautes:
        raise LicencesRefusees(
            "licences refusées — vérifier la compatibilité avec une distribution MIT, puis, "
            "si elle est acquise, étendre LISTE_BLANCHE :\n  " + "\n  ".join(fautes))


_TRAIT = "=" * 78
_FILET = "-" * 78


def _bloc(c: Composant) -> list[str]:
    lignes = [_FILET, f"{c.nom} {c.version}", f"Licence : {c.licence or 'inconnue'}", f"Source : {c.source}"]
    if c.note:
        lignes.append(f"Note : {c.note}")
    if c.textes:
        for titre, texte in c.textes:
            lignes += ["", f"--- {titre} ---", texte.rstrip("\n")]
    elif "Texte" not in c.note:
        lignes += ["", f"Texte de la licence non distribué avec le paquet, voir {c.source}"]
    lignes.append("")
    return lignes


def rendre(python: list[Composant], natifs: list[Composant], interface: list[Composant] | None,
           produit: str) -> str:
    entete = [
        "Composants tiers des exécutables — superviseur et témoin Ethernet Peplink",
        _TRAIT,
        "",
        "Le superviseur et le témoin Ethernet sont publiés sous licence MIT (fichier LICENSE",
        "du dépôt). Leurs exécutables embarquent aussi du code d'autres auteurs : l'interpréteur",
        "Python, des bibliothèques Python et, pour le superviseur, l'interface web compilée et",
        "ses polices. Ce fichier reproduit, pour chacun, son nom, sa version, sa licence, sa",
        "source, et le texte complet de sa licence et de sa notice tels que le composant les",
        "distribue. Un texte que le composant ne distribue pas est repris de sa source",
        "officielle, citée avec son étiquette en tête de ce texte ; à défaut, il est signalé.",
        "",
        f"Produit : {produit}",
        f"Système de construction : {sys.platform}",
        "Généré par backend/tools/licences_tierces.py à chaque construction.",
        "",
    ]
    sections = [("Python — dépendances d'exécution (backend/requirements.txt)", python),
                ("Interpréteur, bibliothèques natives, chargeur", natifs)]
    if interface is not None:
        sections.append(("Interface web — dépendances de production (frontend/package-lock.json)", interface))
    resume = ["Résumé", _FILET]
    for titre, liste in sections:
        resume.append(f"{titre} :")
        resume += [f"  {c.nom} {c.version} — {c.licence or 'inconnue'}" for c in liste]
    corps: list[str] = []
    for titre, liste in sections:
        corps += ["", _TRAIT, titre, _TRAIT, ""]
        for c in liste:
            corps += _bloc(c)
    return "\n".join([*entete, *resume, *corps]).rstrip("\n") + "\n"


def manquants(composants: list[Composant]) -> list[str]:
    """Les composants dont aucun texte de licence n'a été trouvé — à signaler."""
    return [f"{c.nom} {c.version}" for c in composants
            if not c.textes and "Texte : reproduit" not in c.note]


def generer(interface: bool = True, frontend: Path = FRONTEND,
            requirements: Path = RACINE / "requirements.txt",
            chemins: list[str] | None = None) -> tuple[str, list[str]]:
    """(contenu du fichier, composants sans texte). Lève LicencesRefusees."""
    python = composants_python(requirements, chemins)
    cpython = composant_cpython()
    natifs = [cpython, *composants_natifs(cpython.textes[-1][1]),
              composant_pyinstaller(chemins)]
    ecran = composants_interface(frontend) if interface else None
    tous = [*python, *natifs, *(ecran or [])]
    verifier(tous)
    produit = ("superviseur (serveur et interface web)" if interface
               else "témoin Ethernet (serveur et écran statique ; sans l'interface web du superviseur)")
    return rendre(python, natifs, ecran, produit), manquants(tous)


def ecrire(sortie: Path, interface: bool = True, frontend: Path = FRONTEND) -> list[str]:
    """Écrit le fichier (UTF-8, fins de ligne LF sur tout système) ; rend les composants sans texte."""
    contenu, sans_texte = generer(interface, frontend)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(contenu, encoding="utf-8", newline="\n")
    return sans_texte


def main(argv: list[str] | None = None) -> int:
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")
    lecteur = argparse.ArgumentParser(description="Écrit LICENCES-TIERCES.txt.")
    lecteur.add_argument("--sortie", type=Path, default=RACINE / "dist" / NOM)
    lecteur.add_argument("--sans-interface", action="store_true",
                         help="le témoin : sans les dépendances de l'interface web")
    args = lecteur.parse_args(argv)
    try:
        sans_texte = ecrire(args.sortie, interface=not args.sans_interface)
    except LicencesRefusees as e:
        print(f"LICENCES-TIERCES : {e}")
        return 1
    print(f"{args.sortie} écrit")
    for nom in sans_texte:
        print(f"  texte de licence non distribué : {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
