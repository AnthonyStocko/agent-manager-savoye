import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from manager import configuration as conf
from manager import doc_odatio as doc
from manager import registre as reg

CONFIG = {"ODATIO_DOC_DEPOT": "https://git.exemple.io/scm/doc/odatio-docs.git", "ODATIO_DOC_BRANCHE": "master",
          "ODATIO_DOC_DOSSIER": "docs", "BITBUCKET_URL": "https://git.exemple.io", "BITBUCKET_HTTP_TOKEN": "jeton"}

FICHIERS = ["index.rst", "development/index.rst", "development/api.md", "development/schema.png",
            "user/stock/inventaire.rst", "user/stock/reappro.rst", "user/expedition/colis.adoc"]
CONTENUS = {"docs/development/index.rst": "Développement\n=============\n\n" + "\n".join(f"ligne {i}" for i in
                                                                                         range(4, 1001))}


class FauxBitbucket:
    """Répond comme Bitbucket Server ; garde les requêtes reçues."""

    def __init__(self, recherche: bool = True, pages: int = 2):
        self.requetes, self.recherche, self.pages = [], recherche, pages

    def __call__(self, requete, delai):
        self.requetes.append(requete)
        u = urlsplit(requete.full_url)
        params = {k: v[0] for k, v in parse_qs(u.query).items()}
        if "/rest/api/1.0/projects/doc/repos/odatio-docs/files/docs" == u.path:
            taille = -(-len(FICHIERS) // self.pages)
            debut = int(params.get("start", 0))
            valeurs = FICHIERS[debut:debut + taille]
            fin = debut + taille >= len(FICHIERS)
            return self._json({"values": valeurs, "isLastPage": fin, "nextPageStart": None if fin else debut + taille})
        if u.path.startswith("/projects/doc/repos/odatio-docs/raw/"):
            fichier = unquote(u.path.split("/raw/", 1)[1])
            if fichier in CONTENUS:
                return io.BytesIO(CONTENUS[fichier].encode("utf-8"))
            raise urllib.error.HTTPError(requete.full_url, 404, "Not Found", {}, None)
        if u.path == "/rest/search/latest/search":
            if not self.recherche:
                raise urllib.error.HTTPError(requete.full_url, 404, "Not Found", {}, None)
            corps = json.loads(requete.data)
            assert "repo:doc/odatio-docs" in corps["query"]
            return self._json({"code": {"values": [
                {"file": "docs/user/stock/inventaire.rst", "hitContexts": [[{"line": 12, "text": "un <em>inventaire</em> tournant"}]]},
                {"file": "README.md", "hitContexts": []}]}})                       # hors du dossier : ignoré
        raise AssertionError(f"appel inattendu : {requete.full_url}")

    @staticmethod
    def _json(donnees):
        return io.BytesIO(json.dumps(donnees).encode("utf-8"))


class TestConfiguration(unittest.TestCase):
    def test_formes_d_adresse(self):
        cas = {"https://git.exemple.io/scm/doc/odatio-docs.git": ("https://git.exemple.io", "projects/doc"),
               "https://git.exemple.io/projects/DOC/repos/odatio-docs/browse/docs": ("https://git.exemple.io",
                                                                                     "projects/DOC"),
               "https://git.exemple.io/bitbucket/scm/doc/odatio-docs.git": ("https://git.exemple.io/bitbucket",
                                                                            "projects/doc"),
               "https://git.exemple.io/users/anthony/repos/odatio-docs": ("https://git.exemple.io", "users/anthony"),
               "https://git.exemple.io/scm/~anthony/odatio-docs.git": ("https://git.exemple.io", "users/anthony"),
               "ssh://git@git.exemple.io:7999/doc/odatio-docs.git": ("https://git.exemple.io", "projects/doc"),
               "DOC/odatio-docs": ("https://git.exemple.io", "projects/DOC")}
        for brut, (base, proprietaire) in cas.items():
            with self.subTest(brut):
                d = doc.depot({**CONFIG, "ODATIO_DOC_DEPOT": brut})
                self.assertEqual((d.base, d.proprietaire, d.nom), (base, proprietaire, "odatio-docs"))

    def test_refus(self):
        cas = {"non configurée": {**CONFIG, "ODATIO_DOC_DEPOT": ""},
               "jeton n'y est pas envoyé": {**CONFIG, "ODATIO_DOC_DEPOT": "https://autre.exemple/scm/a/b.git"},
               "dépôt introuvable": {**CONFIG, "ODATIO_DOC_DEPOT": "https://git.exemple.io/rien"},
               "chemin refusé": {**CONFIG, "ODATIO_DOC_DOSSIER": "../secret"}}
        for attendu, variables in cas.items():
            with self.subTest(attendu), self.assertRaises(doc.DocErreur) as e:
                doc.depot(variables)
            self.assertIn(attendu, str(e.exception))

    def test_acces_communs_transmis_a_tous_les_agents(self):
        with tempfile.TemporaryDirectory() as t:
            dossier = Path(t)
            conf.enregistrer(conf.Commun(acces={k: CONFIG[k] for k in ("ODATIO_DOC_DEPOT", "ODATIO_DOC_BRANCHE",
                                                                       "ODATIO_DOC_DOSSIER")}), dossier)
            conf.enregistrer_secrets({"BITBUCKET_HTTP_TOKEN": "jeton"}, dossier)
            for agent in reg.charger(reg.EXEMPLE).agents:
                variables = conf.variables_pour(agent, dossier)
                self.assertEqual(variables["ODATIO_DOC_DEPOT"], CONFIG["ODATIO_DOC_DEPOT"])
                # le jeton suit le registre (moindre privilège)
                self.assertEqual("BITBUCKET_HTTP_TOKEN" in variables, conf.autorise("BITBUCKET_HTTP_TOKEN",
                                                                                    agent.secrets))
            texte = (dossier / conf.FICHIER).read_text(encoding="utf-8")
            self.assertNotIn("jeton", texte)


class TestDocumentation(unittest.TestCase):
    def setUp(self):
        self.faux = FauxBitbucket()
        self.doc = doc.Documentation(CONFIG, ouvrir=self.faux)

    def test_sommaire_pagine_et_en_cache(self):
        texte = self.doc.sommaire()
        self.assertIn("7 fichiers", texte)
        self.assertIn("development/index.rst", texte)
        self.assertIn("user/stock/ (2 fichiers)", texte)
        self.assertIn("user/stock/inventaire.rst", self.doc.sommaire("user", 3))
        self.assertEqual(len(self.faux.requetes), 2)                     # deux pages, puis le cache
        for r in self.faux.requetes:
            self.assertEqual(r.get_method(), "GET")
            self.assertEqual(r.get_header("Authorization"), "Bearer jeton")
            self.assertIn("at=master", r.full_url)

    def test_lire_par_chemin_ou_par_page_publiee(self):
        for chemin in ("development/index.rst", "development/index.html", "development",
                       "https://site.exemple/doc/odatio-docs/development/index.html#section"):
            with self.subTest(chemin):
                texte = self.doc.lire(chemin, 1, 3)
                self.assertTrue(texte.startswith("development/index.rst (doc/odatio-docs@master), lignes 1-3 sur 1000"))
                self.assertIn("    1  Développement", texte)
                self.assertIn("[suite : debut=4]", texte)
        self.assertIn("lignes 998-1000 sur 1000", self.doc.lire("development/index.rst", 998, 50))
        self.assertIn("lignes 1-800", self.doc.lire("development/index.rst", 1, 5000))   # borné
        self.assertIn("non textuel", self.doc.lire("development/schema.png"))

    def test_lire_refuse_hors_documentation(self):
        for chemin in ("../config/.env", "/etc/passwd", "C:/Windows", "absent.md"):
            with self.subTest(chemin), self.assertRaises(doc.DocErreur):
                self.doc.lire(chemin)

    def test_chercher_plein_texte(self):
        texte = self.doc.chercher("inventaire")
        self.assertIn("plein texte", texte)
        self.assertIn("- user/stock/inventaire.rst", texte)
        self.assertIn("12: un inventaire tournant", texte)
        self.assertNotIn("README", texte)

    def test_chercher_par_nom_si_recherche_indisponible(self):
        d = doc.Documentation(CONFIG, ouvrir=FauxBitbucket(recherche=False))
        texte = d.chercher("stock reappro")
        self.assertIn("indisponible", texte)
        self.assertIn("- user/stock/reappro.rst", texte)

    def test_erreurs_lisibles(self):
        def refus(requete, delai):
            raise urllib.error.HTTPError(requete.full_url, 401, "Unauthorized", {}, None)

        with self.assertRaises(doc.DocErreur) as e:
            doc.Documentation(CONFIG, ouvrir=refus).sommaire()
        self.assertIn("BITBUCKET_HTTP_TOKEN", str(e.exception))
        with self.assertRaises(doc.DocErreur) as e:
            doc.Documentation({**CONFIG, "BITBUCKET_HTTP_TOKEN": ""}, ouvrir=self.faux).sommaire()
        self.assertIn("registre", str(e.exception))
        self.assertEqual(self.faux.requetes, [])                         # rien n'est parti sans jeton

    def test_lecture_seule(self):
        source = Path(doc.__file__).read_text(encoding="utf-8")
        # seul POST : l'API de recherche (lecture) ; aucune écriture de fichier
        self.assertEqual(source.count('"POST"'), 1)
        for interdit in ("PUT", "DELETE", "PATCH", "write_text", "write_bytes"):
            self.assertNotIn(interdit, source)
        self.assertNotRegex(source, r"(?<!url)open\(")


class TestAntora(unittest.TestCase):
    """Site Antora : la page publiée …/<composant>/[<version>/][<module>/]<page>.html vient de
    modules/<module>/pages/<page>.adoc."""
    ANTORA = ["antora.yml", "modules/ROOT/nav.adoc", "modules/ROOT/pages/index.adoc",
              "modules/ROOT/pages/guide/stock.adoc", "modules/development/pages/index.adoc",
              "modules/development/pages/api/rest.adoc", "modules/development/images/schema.png"]

    def documentation(self, fichiers):
        d = doc.Documentation({**CONFIG, "ODATIO_DOC_DOSSIER": ""}, ouvrir=FauxBitbucket())
        d.fichiers = lambda: sorted(fichiers)
        return d

    def test_pages_publiees(self):
        d = self.documentation(self.ANTORA)
        cas = {"https://site.exemple/doc/odatio-docs/development/index.html": "modules/development/pages/index.adoc",
               "development/api/rest.html": "modules/development/pages/api/rest.adoc",
               "https://site.exemple/doc/odatio-docs/index.html": "modules/ROOT/pages/index.adoc",
               "guide/stock.html": "modules/ROOT/pages/guide/stock.adoc",
               "https://site.exemple/doc/odatio-docs/1.0/guide/stock.html": "modules/ROOT/pages/guide/stock.adoc",
               "modules/ROOT/pages/index.adoc": "modules/ROOT/pages/index.adoc"}
        for chemin, attendu in cas.items():
            with self.subTest(chemin):
                self.assertEqual(d.resoudre(chemin), attendu)

    def test_version_dans_l_adresse(self):
        # « development » est une version : la page vient du module ROOT
        d = self.documentation(["modules/ROOT/pages/index.adoc", "modules/ROOT/pages/guide/stock.adoc"])
        self.assertEqual(d.resoudre("https://site.exemple/doc/odatio-docs/development/index.html"),
                         "modules/ROOT/pages/index.adoc")

    def test_sources_dans_un_sous_dossier(self):
        d = self.documentation(["docs/modules/development/pages/index.adoc", "docs/antora.yml"])
        self.assertEqual(d.resoudre("development/index.html"), "docs/modules/development/pages/index.adoc")

    def test_introuvable(self):
        with self.assertRaises(doc.DocErreur):
            self.documentation(self.ANTORA).resoudre("development/absente.html")


class TestServeurMcp(unittest.TestCase):
    def test_ne_relit_aucun_secret_sur_disque(self):
        with tempfile.TemporaryDirectory() as t:
            dossier = Path(t)
            conf.enregistrer(conf.Commun(acces={"ODATIO_DOC_DEPOT": CONFIG["ODATIO_DOC_DEPOT"]}), dossier)
            conf.enregistrer_secrets({"BITBUCKET_HTTP_TOKEN": "jeton-du-manager"}, dossier)
            env = {k: v for k, v in os.environ.items() if k not in doc.VARIABLES}
            script = ("import mcp_doc_odatio as m, json; print(json.dumps(m.variables()))")
            sortie = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
                                    cwd=reg.RACINE, env={**env, "SAVOYE_CONFIG_DIR": str(dossier)})
            if "No module named 'mcp'" in sortie.stderr:
                self.skipTest("mcp non installé")
            variables = json.loads(sortie.stdout.strip().splitlines()[-1])
            self.assertEqual(variables["ODATIO_DOC_DEPOT"], CONFIG["ODATIO_DOC_DEPOT"])
            self.assertEqual(variables["BITBUCKET_HTTP_TOKEN"], "")


if __name__ == "__main__":
    unittest.main()
