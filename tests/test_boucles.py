"""Boucles de correction autonome d'agent-cucumber vues par le manager : lecture (outil boucles_cucumber), bloc du
tableau de bord (information, aucune validation) et relance. Données fictives."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from manager import initiatives
from manager import journal as jr
from manager import registre as reg
from manager import sources
from manager import tableau

BOUCLES = ("Boucles de correction Cucumber :\n"
           "- client-x-4-20261120-080000 [client-x] : abandonnée · itération 3/3 · échecs 5 → 2 · AGENT 1, "
           "APPLICATION 1, ENVIRONNEMENT 0, INCERTAIN 0 · coût 3.40 $ · relance oui · mis à jour 2026-11-20T08:50:00\n"
           "  raison : limite de 3 itération(s) atteinte\n"
           "  correction it1 abc1234567 : prompt de lot trop vague\n"
           "  retour arrière it2 abc1234567 → def7654321\n"
           "  demande : Boucle client-x-4 : corriger l'agent cucumber / - [AGENT] Créer — Then x : m\n"
           "- client-y-2-20261119-100000 [client-y] : terminée · itération 1/3 · échecs 2 → 0 · AGENT 0, "
           "APPLICATION 0, ENVIRONNEMENT 0, INCERTAIN 0 · coût 1.10 $ · relance non · mis à jour 2026-11-19T10:40:00")


def FauxJournal():
    """Journal réel dans un dossier temporaire (supprimé à la fin des tests)."""
    return jr.Journal(Path(_DOSSIER.name) / "manager.db", delai=0.2)


_DOSSIER = tempfile.TemporaryDirectory()


def registre():
    return reg.lire({"agents": [
        {"nom": "scrum-master", "titre": "Scrum Master", "dossier": ".", "port": 8501},
        {"nom": "cucumber", "titre": "Tests Cucumber", "dossier": ".", "port": 8507},
        {"nom": "dev-tool", "titre": "Outillage", "dossier": ".", "port": 8503, "mode": "demande"}]})


class FauxResultat:
    def __init__(self, textes, erreurs=None):
        self.textes, self.erreurs = textes, erreurs or {}


class FauxPilote:
    def etat(self):
        return []


class TestBoucles(unittest.TestCase):
    def test_lecture(self):
        boucles = sources.lire_boucles(BOUCLES)
        self.assertEqual([(b["client"], b["etat"], b["relance"]) for b in boucles],
                         [("client-x", "abandonnée", True), ("client-y", "terminée", False)])
        x = boucles[0]
        self.assertEqual((x["iteration"], x["iterations_max"], x["cout_usd"]), (3, 3, 3.4))
        self.assertEqual(x["categories"], {"AGENT": 1, "APPLICATION": 1, "ENVIRONNEMENT": 0, "INCERTAIN": 0})
        self.assertEqual(x["corrections"], ["it1 abc1234567 : prompt de lot trop vague"])
        self.assertEqual(x["annulations"], ["it2 abc1234567 → def7654321"])
        self.assertIn("\n- [AGENT]", x["demande"])
        self.assertEqual(sources.lire_boucles("Aucune boucle de correction Cucumber."), [])
        self.assertIn("boucles_cucumber", sources.LISTE_BLANCHE["scrum-master"])

    def construire(self, textes, erreurs=None):
        return tableau.construire(registre(), FauxJournal(), FauxPilote(),
                                  appeler_mcp=lambda agent, appels: FauxResultat(textes, erreurs),
                                  lire_csv=lambda *a: [], maintenant=datetime(2026, 11, 20, 9, 0))

    def test_bloc_et_liens_prerempli(self):
        t = self.construire({"boucles_cucumber": BOUCLES})
        self.assertIn(t.boucles, t.blocs())
        x, y = t.boucles.lignes
        self.assertRegex(x["lien"], r"^http://[\w.]+:8503/\?")                # échecs AGENT : vers dev-tool
        self.assertIn("depot=agent-cucumber", x["lien"])
        self.assertIn("demande=", x["lien"])
        self.assertEqual(y["lien"], "")                                          # terminée : aucune relance
        self.assertFalse([a for a in t.alertes.lignes if "boucle" in a["texte"].lower()])   # pas d'alerte

    def test_outil_absent_sans_alerte(self):
        t = self.construire({}, {"boucles_cucumber": "Unknown tool"})
        self.assertEqual(t.boucles.lignes, [])
        self.assertIn("indisponibles", t.boucles.note)
        self.assertFalse([a for a in t.alertes.lignes if "boucles_cucumber" in a["texte"]])

    def test_relance_seulement_si_echecs_restants(self):
        t = self.construire({"boucles_cucumber": BOUCLES})
        relances = [r for r in initiatives.regles(FauxJournal(), t, maintenant=datetime(2026, 11, 20, 9, 0))
                    if r.regle == "boucle_echecs"]
        self.assertEqual(len(relances), 1)
        self.assertIn("client-x", relances[0].texte)
        self.assertIn("AGENT 1", relances[0].texte)
        self.assertIn("8503", relances[0].lien)
        desactivee = initiatives.regles(FauxJournal(), t, {"boucle_echecs": False},
                                        maintenant=datetime(2026, 11, 20, 9, 0))
        self.assertFalse([r for r in desactivee if r.regle == "boucle_echecs"])
        self.assertIn("boucle_echecs", initiatives.URGENCE)


def tearDownModule():
    _DOSSIER.cleanup()


if __name__ == "__main__":
    unittest.main()
