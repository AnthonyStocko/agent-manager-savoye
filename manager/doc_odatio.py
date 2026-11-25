"""Documentation Odatio en LECTURE SEULE, lue directement dans Bitbucket (API REST), sans clone.

Le site publié est derrière une connexion Microsoft que les agents ne peuvent pas franchir : on lit donc ses sources,
dans le dépôt Bitbucket qui les porte, avec le jeton de lecture que les agents ont déjà (``BITBUCKET_HTTP_TOKEN``).

Configuration (accès non secrets de la configuration commune, transmis à tous les agents) :
  ODATIO_DOC_DEPOT    dépôt des sources : URL Bitbucket (clonable ou de navigation) ou « PROJET/depot »
  ODATIO_DOC_BRANCHE  branche ou tag lu (vide = branche par défaut du dépôt)
  ODATIO_DOC_DOSSIER  sous-dossier de la documentation dans le dépôt (vide = tout le dépôt)
  BITBUCKET_URL       adresse du serveur ; le jeton n'est jamais envoyé à un autre hôte

Appels utilisés, tous en GET sauf la recherche de code (POST en lecture, API « search » de Bitbucket) :
  /rest/api/1.0/{projects/P | users/U}/repos/R/files/<dossier>   sommaire (paginé)
  /{projects/P | users/U}/repos/R/raw/<fichier>                   contenu
  /rest/search/latest/search                                      recherche plein texte, si elle est activée (elle
                                                                  n'indexe que la branche par défaut)
Le sommaire est gardé 10 minutes en mémoire. Aucune écriture, aucun fichier sur le disque.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit

VARIABLES = ("ODATIO_DOC_DEPOT", "ODATIO_DOC_BRANCHE", "ODATIO_DOC_DOSSIER", "BITBUCKET_URL", "BITBUCKET_HTTP_TOKEN")
TEXTE = {".md", ".markdown", ".rst", ".adoc", ".asciidoc", ".txt", ".html", ".htm", ".yml", ".yaml", ".json",
         ".csv", ".xml", ".puml", ".sql", ".properties", ".ini", ".toml", ".py", ".java", ".js", ".ts", ".css"}
SOURCES_PAGES = (".md", ".rst", ".adoc", ".asciidoc", ".html", ".txt")    # sources possibles d'une page publiée
DELAI = 20                       # secondes par appel
CACHE_S = 600                    # durée de vie du sommaire en mémoire
LIGNES_MAX = 800                 # lignes au plus par lecture
CARACTERES_MAX = 60_000          # caractères au plus par lecture
FICHIERS_MAX = 20_000            # garde-fou du sommaire


class DocErreur(Exception):
    pass


@dataclass(frozen=True)
class Depot:
    base: str          # adresse du serveur, sans / final (avec son éventuel chemin de contexte)
    proprietaire: str  # « projects/PROJ » ou « users/nom »
    nom: str           # slug du dépôt
    branche: str = ""
    dossier: str = ""

    @property
    def libelle(self) -> str:
        return f"{self.proprietaire.split('/', 1)[1]}/{self.nom}" + (f"@{self.branche}" if self.branche else "")


# ---------------------------------------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------------------------------------

def _proprietaire(segment: str) -> str:
    return f"users/{segment[1:]}" if segment.startswith("~") else f"projects/{segment}"


def depot(variables: dict | None = None) -> Depot:
    """Lit la configuration (environnement par défaut) et vérifie que le jeton ne partira que vers BITBUCKET_URL."""
    v = {k: (variables if variables is not None else os.environ).get(k, "").strip() for k in VARIABLES}
    brut, serveur = v["ODATIO_DOC_DEPOT"], v["BITBUCKET_URL"].rstrip("/")
    if not brut:
        raise DocErreur("documentation Odatio non configurée : renseigner ODATIO_DOC_DEPOT dans l'onglet "
                        "Configuration du manager (groupe « Documentation Odatio »), puis redémarrer l'agent")
    if re.fullmatch(r"~?[\w.-]+/[\w.-]+", brut):                  # forme courte « PROJET/depot »
        if not serveur:
            raise DocErreur("ODATIO_DOC_DEPOT en forme courte : BITBUCKET_URL est nécessaire")
        proprietaire, nom = brut.split("/")
        base = serveur
    else:
        u = urlsplit(brut)
        if u.scheme not in ("https", "http", "ssh"):
            raise DocErreur(f"ODATIO_DOC_DEPOT : adresse non reconnue « {brut} »")
        chemin = [s for s in u.path.split("/") if s]
        m = None
        for i, s in enumerate(chemin):                            # /scm/P/r.git · /projects/P/repos/r · /users/u/repos/r
            if s == "scm" and len(chemin) > i + 2:
                m = (i, chemin[i + 1], chemin[i + 2])
            elif s in ("projects", "users") and len(chemin) > i + 3 and chemin[i + 2] == "repos":
                m = (i, ("~" if s == "users" else "") + chemin[i + 1], chemin[i + 3])
            if m:
                break
        if u.scheme == "ssh":                                     # ssh://git@hote:7999/p/r.git
            if len(chemin) < 2:
                raise DocErreur(f"ODATIO_DOC_DEPOT : adresse SSH non reconnue « {brut} »")
            m = (None, chemin[-2], chemin[-1])
        if not m:
            raise DocErreur(f"ODATIO_DOC_DEPOT : dépôt introuvable dans « {brut} » (attendu …/scm/PROJET/depot.git, "
                            "…/projects/PROJET/repos/depot ou PROJET/depot)")
        indice, proprietaire, nom = m
        if u.scheme == "ssh":
            if not serveur:
                raise DocErreur("ODATIO_DOC_DEPOT en SSH : BITBUCKET_URL est nécessaire pour l'API")
            base = serveur
        else:
            base = f"{u.scheme}://{u.netloc.rsplit('@', 1)[-1]}" + "".join("/" + s for s in chemin[:indice])
            if serveur and urlsplit(serveur).netloc.lower() != urlsplit(base).netloc.lower():
                raise DocErreur("ODATIO_DOC_DEPOT n'est pas sur le serveur BITBUCKET_URL : le jeton n'y est pas envoyé")
    nom = nom[:-4] if nom.endswith(".git") else nom
    dossier = _normaliser(v["ODATIO_DOC_DOSSIER"]) if v["ODATIO_DOC_DOSSIER"] else ""
    return Depot(base=base, proprietaire=_proprietaire(proprietaire), nom=nom, branche=v["ODATIO_DOC_BRANCHE"],
                 dossier=dossier)


def _normaliser(chemin: str) -> str:
    """Chemin relatif propre, sans remontée ni chemin absolu."""
    brut = (chemin or "").replace("\\", "/").strip()
    if brut.startswith("/") or re.match(r"^[A-Za-z]:", brut) or ".." in PurePosixPath(brut).parts:
        raise DocErreur(f"chemin refusé : « {chemin} » (relatif au dossier de la documentation, sans ..)")
    propre = posixpath.normpath(brut) if brut else ""
    return "" if propre == "." else propre


def candidats_antora(page: str) -> list[str]:
    """Sources Antora possibles d'une page publiée, de la plus probable à la moins probable.

    Antora publie ``modules/<module>/pages/<page>.adoc`` sous ``[<version>/][<module>/]<page>.html`` (module ROOT
    et version par défaut omis). « development/index.html » peut donc venir de modules/ROOT/pages/development/
    index.adoc, de modules/development/pages/index.adoc, ou d'une version « development » (modules/ROOT/pages/
    index.adoc). Les chemins rendus sont relatifs : ils se comparent à la fin des fichiers du dépôt."""
    parties = [p for p in re.sub(r"\.html?$", "", page).split("/") if p]
    if not parties:
        parties = ["index"]
    candidats = []
    for saut in range(0, min(2, len(parties) - 1) + 1):            # 0, 1 ou 2 segments de tête : version…
        reste = parties[saut:]
        candidats.append("modules/ROOT/pages/" + "/".join(reste) + ".adoc")
        if len(reste) > 1:
            candidats.append(f"modules/{reste[0]}/pages/" + "/".join(reste[1:]) + ".adoc")
        candidats.append("modules/ROOT/pages/" + "/".join(reste) + "/index.adoc")
    return list(dict.fromkeys(candidats))


# ---------------------------------------------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------------------------------------------

def _ouvrir(requete: urllib.request.Request, delai: int):
    return urllib.request.urlopen(requete, timeout=delai)


class Documentation:
    """Lecture seule de la documentation. ``ouvrir`` est remplaçable en test."""

    def __init__(self, variables: dict | None = None, ouvrir=_ouvrir):
        self.depot = depot(variables)
        self.jeton = (variables if variables is not None else os.environ).get("BITBUCKET_HTTP_TOKEN", "").strip()
        self.ouvrir = ouvrir
        self._sommaire: tuple[float, list[str]] | None = None

    # --- HTTP ---------------------------------------------------------------------------------------------------
    def _appel(self, chemin: str, parametres: dict | None = None, corps: dict | None = None) -> bytes:
        url = f"{self.depot.base}/{chemin}"
        if parametres:
            url += "?" + "&".join(f"{k}={quote(str(v), safe='')}" for k, v in parametres.items() if v not in ("", None))
        if not self.jeton:
            raise DocErreur("jeton BITBUCKET_HTTP_TOKEN absent : l'agent doit le recevoir du manager (champ secrets du "
                            "registre) et le transmettre au serveur MCP de la documentation")
        entetes = {"Accept": "application/json, text/plain, */*", "Authorization": f"Bearer {self.jeton}"}
        donnees = None
        if corps is not None:
            donnees, entetes["Content-Type"] = json.dumps(corps).encode("utf-8"), "application/json"
        requete = urllib.request.Request(url, data=donnees, headers=entetes, method="POST" if corps else "GET")
        try:
            with self.ouvrir(requete, DELAI) as reponse:
                return reponse.read()
        except urllib.error.HTTPError as e:
            raison = {401: "jeton BITBUCKET_HTTP_TOKEN absent ou refusé", 403: "accès refusé au dépôt",
                      404: "introuvable (dépôt, branche ou fichier)"}.get(e.code, e.reason)
            raise DocErreur(f"Bitbucket {e.code} : {raison}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise DocErreur(f"Bitbucket injoignable : {getattr(e, 'reason', e)}") from None

    def _json(self, chemin: str, parametres: dict | None = None, corps: dict | None = None) -> dict:
        try:
            return json.loads(self._appel(chemin, parametres, corps).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DocErreur("réponse de Bitbucket illisible (le serveur a peut-être demandé une connexion)") from None

    def _chemin_api(self, quoi: str, fichier: str = "") -> str:
        d = self.depot
        segments = "/".join(quote(s, safe="") for s in fichier.split("/") if s)
        prefixe = "rest/api/1.0/" if quoi == "files" else ""
        return f"{prefixe}{d.proprietaire}/repos/{quote(d.nom, safe='')}/{quoi}" + (f"/{segments}" if segments else "")

    # --- sommaire -----------------------------------------------------------------------------------------------
    def fichiers(self) -> list[str]:
        """Tous les fichiers du dossier de la documentation (chemins relatifs à ce dossier), en cache 10 min."""
        if self._sommaire and time.monotonic() - self._sommaire[0] < CACHE_S:
            return self._sommaire[1]
        valeurs, debut = [], 0
        while True:
            page = self._json(self._chemin_api("files", self.depot.dossier),
                              {"at": self.depot.branche, "start": debut, "limit": 1000})
            valeurs += [str(v) for v in page.get("values", [])]
            if page.get("isLastPage", True) or len(valeurs) >= FICHIERS_MAX:
                break
            debut = page.get("nextPageStart", debut + 1000)
        valeurs.sort()
        self._sommaire = (time.monotonic(), valeurs)
        return valeurs

    def sommaire(self, dossier: str = "", profondeur: int = 2) -> str:
        """Arborescence d'un dossier de la doc : sous-dossiers (avec leur nombre de fichiers) et fichiers."""
        dossier = _normaliser(dossier)
        profondeur = max(1, min(int(profondeur or 2), 6))
        prefixe = f"{dossier}/" if dossier else ""
        dedans = [f[len(prefixe):] for f in self.fichiers() if f.startswith(prefixe)]
        if not dedans:
            return f"Aucun fichier dans « {dossier or '/'} » ({self.depot.libelle})."
        lignes, comptes = [], {}
        for f in dedans:
            parties = f.split("/")
            if len(parties) <= profondeur:
                lignes.append(prefixe + f)                         # chemin complet : utilisable par lire()
            else:
                cle = prefixe + "/".join(parties[:profondeur]) + "/"
                comptes[cle] = comptes.get(cle, 0) + 1
        lignes += [f"{d} ({n} fichiers)" for d, n in comptes.items()]
        lignes.sort()
        entete = f"Documentation Odatio ({self.depot.libelle}), dossier « {dossier or '/'} » : {len(dedans)} fichiers."
        coupe = lignes[:400]
        suite = f"\n… {len(lignes) - 400} entrées de plus : préciser le dossier." if len(lignes) > 400 else ""
        return entete + "\n" + "\n".join(coupe) + suite

    # --- lecture ------------------------------------------------------------------------------------------------
    def resoudre(self, chemin: str) -> str:
        """Fichier source d'un chemin : chemin exact, URL ou chemin d'une page publiée (…/index.html → index.rst…)."""
        brut = (chemin or "").strip()
        if re.match(r"^https?://", brut):
            brut = urlsplit(brut).path
            for marqueur in ("/doc/", "/docs/"):                   # site publié : garder ce qui suit le nom du site
                if marqueur in brut:
                    brut = brut.split(marqueur, 1)[1].split("/", 1)[-1]
                    break
        brut = brut.split("#", 1)[0].split("?", 1)[0].lstrip("/")
        propre = _normaliser(brut)
        tous = self.fichiers()
        if propre in tous:
            return propre
        souche = re.sub(r"\.(html?|md|rst|adoc|asciidoc|txt)$", "", propre)
        candidats = [souche + ext for ext in SOURCES_PAGES] + [f"{souche}/index{ext}" for ext in SOURCES_PAGES]
        for c in candidats:
            if c in tous:
                return c
        for antora in candidats_antora(propre):                     # site Antora : modules/<m>/pages/<page>.adoc
            trouves = [f for f in tous if f == antora or f.endswith("/" + antora)]
            if len(trouves) == 1:
                return trouves[0]
            if trouves:
                raise DocErreur(f"« {chemin} » est ambigu : " + ", ".join(trouves[:10]))
        suffixes = tuple("/" + c for c in candidats)                # site publié dans un sous-dossier des sources
        trouves = [f for f in tous if f.endswith(suffixes)]
        if len(trouves) == 1:
            return trouves[0]
        if trouves:
            raise DocErreur(f"« {chemin} » est ambigu : " + ", ".join(trouves[:10]))
        raise DocErreur(f"« {chemin} » introuvable dans la documentation : utiliser doc_sommaire ou doc_chercher")

    def lire(self, chemin: str, debut: int = 1, nombre: int = 400) -> str:
        """Contenu d'un fichier, lignes ``debut`` à ``debut + nombre - 1``, numérotées."""
        fichier = self.resoudre(chemin)
        if PurePosixPath(fichier).suffix.lower() not in TEXTE:
            return f"{fichier} : fichier non textuel (image, binaire), non lu."
        complet = posixpath.join(self.depot.dossier, fichier) if self.depot.dossier else fichier
        brut = self._appel(self._chemin_api("raw", complet), {"at": self.depot.branche})
        texte = brut.decode("utf-8", errors="replace")
        lignes = texte.splitlines()
        debut = max(1, int(debut or 1))
        nombre = max(1, min(int(nombre or 400), LIGNES_MAX))
        extrait = lignes[debut - 1:debut - 1 + nombre]
        if not extrait:
            return f"{fichier} : {len(lignes)} lignes, rien à partir de la ligne {debut}."
        corps, taille = [], 0
        for i, ligne in enumerate(extrait, start=debut):
            taille += len(ligne) + 8
            if taille > CARACTERES_MAX:
                break
            corps.append(f"{i:>5}  {ligne}")
        fin = debut + len(corps) - 1
        suite = f"\n[suite : debut={fin + 1}]" if fin < len(lignes) else ""
        return f"{fichier} ({self.depot.libelle}), lignes {debut}-{fin} sur {len(lignes)} :\n" + "\n".join(corps) + suite

    # --- recherche ----------------------------------------------------------------------------------------------
    def chercher(self, texte: str, nombre: int = 15) -> str:
        """Recherche plein texte (API de recherche de Bitbucket, branche par défaut seulement), sinon par nom de
        fichier."""
        texte = " ".join((texte or "").split())[:200]
        if not texte:
            raise DocErreur("texte à chercher vide")
        nombre = max(1, min(int(nombre or 15), 50))
        resultats, motif = [], ""
        projet = self.depot.proprietaire.split("/", 1)[1]
        requete = f'{texte} repo:{"~" if self.depot.proprietaire.startswith("users/") else ""}{projet}/{self.depot.nom}'
        try:
            reponse = self._json("rest/search/latest/search", corps={
                "query": requete, "entities": {"code": {"start": 0, "limit": nombre}},
                "limits": {"primary": nombre, "secondary": 0}})
            prefixe = f"{self.depot.dossier}/" if self.depot.dossier else ""
            for v in (reponse.get("code") or {}).get("values", []):
                fichier = str(v.get("file") or "")
                if prefixe and not fichier.startswith(prefixe):
                    continue
                extraits = []
                for contexte in (v.get("hitContexts") or [])[:2]:
                    for ligne in contexte[:3]:
                        t = re.sub(r"<[^>]+>", "", str(ligne.get("text", "")))
                        extraits.append(f"    {ligne.get('line', '')}: {t.strip()[:200]}")
                resultats.append(f"- {fichier[len(prefixe):]}" + ("\n" + "\n".join(extraits) if extraits else ""))
            motif = "recherche plein texte de Bitbucket (branche par défaut)"
        except DocErreur as e:
            motif = f"recherche plein texte indisponible ({e}) : recherche dans les noms de fichiers"
        if not resultats:
            mots = [m.lower() for m in texte.split()]
            noms = [f for f in self.fichiers() if all(m in f.lower() for m in mots)]
            resultats = [f"- {f}" for f in noms[:nombre]]
            motif = motif if motif.startswith("recherche plein texte indisponible") else \
                "aucun résultat en plein texte : recherche dans les noms de fichiers"
        if not resultats:
            return f"Aucun résultat pour « {texte} » ({motif}). Essayer doc_sommaire."
        return f"« {texte} » dans la documentation Odatio ({motif}) :\n" + "\n".join(resultats)


def configuree(variables: dict | None = None) -> bool:
    return bool((variables if variables is not None else os.environ).get("ODATIO_DOC_DEPOT", "").strip())
