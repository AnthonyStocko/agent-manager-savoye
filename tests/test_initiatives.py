import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from manager import initiatives as ini
from manager import journal as jr
from manager import superviseur as sup
from manager import tableau as tb


def iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def minute(d: datetime) -> str:
    """Format des outils de scrum-master : « AAAA-MM-JJ HH:MM »."""
    return d.strftime("%Y-%m-%d %H:%M")


def tableau_vide() -> tb.Tableau:
    return tb.Tableau(*(tb.Bloc(n) for n in ("agents", "tickets", "analyses", "jenkins", "patchs", "couts",
                                             "alertes")))


class TestPeriode(unittest.TestCase):
    MAINTENANT = datetime(2026, 11, 23, 8, 30)          # un lundi

    def test_premier_point(self):
        self.assertEqual(ini.periode("", self.MAINTENANT), ("2026-11-16T08:30:00", True))

    def test_depuis_le_dernier_vu(self):
        self.assertEqual(ini.periode("2026-11-20T09:05:00", self.MAINTENANT), ("2026-11-20T09:05:00", True))

    def test_deja_vu_aujourd_hui(self):
        self.assertFalse(ini.periode("2026-11-23T08:00:00", self.MAINTENANT)[1])

    def test_retour_de_conges_plafonne(self):
        self.assertEqual(ini.periode("2026-11-02T09:00:00", self.MAINTENANT)[0], "2026-11-16T08:30:00")


class TestPointDuMatin(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporaire.cleanup)
        self.journal = jr.Journal(Path(temporaire.name) / "manager.db", delai=0.2)
        self.maintenant = datetime.now()
        self.hier = self.maintenant - timedelta(days=1)
        self.journal.noter_point(iso(self.hier))

    def test_rubriques(self):
        m, avant = self.maintenant, self.hier - timedelta(days=2)          # avant : déjà couvert par un point
        self.journal.noter_question("Où en est ODA-1 ?", decision="rediriger", cout_usd=0.25,
                                    redirections=[SimpleNamespace(agent="scrum-master", ticket="ODA-1", projet="",
                                                                  onglet="lancement")])
        self.journal.noter_question("Combien de clients ?", decision="repondre")
        self.journal.observer_analyses([
            {"cle": "ODA-1", "libelle": "Analyse non publiée (brouillon)", "a_valider": True,
             "modifiee": minute(m - timedelta(hours=1))},
            {"cle": "ODA-2", "libelle": "Publiée dans Jira", "a_valider": False, "modifiee": minute(m)},
            {"cle": "ODA-3", "libelle": "Analyse non publiée (brouillon)", "a_valider": True,
             "modifiee": minute(avant)}])
        self.journal.noter_etat("odatio", sup.EN_LIGNE, sup.ECHEC, "arrêté 3 fois")
        self.journal.noter_etat("odatio", sup.ECHEC, sup.EN_LIGNE)                          # retour : pas une panne
        self.journal.noter_erreur("assistant", "RuntimeError : CLI absente")
        t = tableau_vide()
        t.tickets.lignes = [{"cle": "ODA-1", "lien": "http://localhost:8501/?ticket=ODA-1"}]
        t.patchs.lignes = [
            {"cle": "ODA-4", "livre_le": minute(m - timedelta(hours=2)), "fichiers": ["ODA-4.patch"],
             "statut": "non_jointe", "manquants": ["ODA-4.patch"], "raison": "", "lien": "lien-ODA-4"},
            {"cle": "ODA-5", "livre_le": minute(avant), "fichiers": ["ODA-5.patch"], "statut": "jointe",
             "manquants": [], "raison": "", "lien": "lien-ODA-5"}]
        t.jenkins.resume["builds"] = [
            {"job": "CUCUMBER-RUN", "numero": 12, "resultat": "EN COURS", "en_cours": True,
             "debut": minute(m - timedelta(minutes=10)), "duree_min": 10},
            {"job": "CUCUMBER-RUN", "numero": 11, "resultat": "FAILURE", "en_cours": False,
             "debut": minute(self.hier - timedelta(minutes=30)), "duree_min": 40},     # fini pendant la période
            {"job": "CUCUMBER-RUN", "numero": 10, "resultat": "SUCCESS", "en_cours": False,
             "debut": minute(avant), "duree_min": 30},
            {"job": "CUCUMBER-RUN", "numero": 9, "resultat": "SUCCESS", "en_cours": False, "debut": "?",
             "duree_min": 0}]

        p = ini.point_du_matin(self.journal, t, m)
        self.assertFalse(p.premier)
        self.assertEqual(p.depuis, iso(self.hier))
        rubriques = {r.titre: r.lignes for r in p.rubriques}
        self.assertEqual(list(rubriques), ["Échanges avec le manager", "Analyses à valider", "Patchs livrés",
                                           "Builds Cucumber terminés", "Pannes et erreurs"])
        self.assertEqual(rubriques["Échanges avec le manager"][0]["texte"],
                         "2 question(s), 0.25 $ (dont 1 sans coût connu) ; redirections : scrum-master 1")
        self.assertEqual([(l["texte"][:5], l["lien"]) for l in rubriques["Analyses à valider"]],
                         [("ODA-1", "http://localhost:8501/?ticket=ODA-1")])
        self.assertEqual([(l["texte"], l["lien"]) for l in rubriques["Patchs livrés"]],
                         [("ODA-4 · ODA-4.patch · NON joint dans Jira", "lien-ODA-4")])
        self.assertEqual([l["texte"][:23] for l in rubriques["Builds Cucumber terminés"]],
                         ["CUCUMBER-RUN #11 FAILUR"])
        incidents = [l["texte"][17:] for l in rubriques["Pannes et erreurs"]]
        self.assertEqual(incidents, ["assistant : RuntimeError : CLI absente", f"odatio : {sup.EN_LIGNE} → {sup.ECHEC}"])

    def test_rien_de_nouveau(self):
        self.assertIsNone(ini.point_du_matin(self.journal, tableau_vide(), self.maintenant))

    def test_vu_masque_jusqu_a_demain(self):
        self.journal.noter_erreur("assistant", "RuntimeError : CLI absente")
        self.assertIsNotNone(ini.point_du_matin(self.journal, tableau_vide(), self.maintenant))
        self.assertTrue(self.journal.noter_point(iso(self.maintenant - timedelta(seconds=1))))
        self.assertIsNone(ini.point_du_matin(self.journal, tableau_vide(), self.maintenant))   # vu aujourd'hui
        self.journal.noter_erreur("assistant", "RuntimeError : encore")
        p = ini.point_du_matin(self.journal, tableau_vide(), self.maintenant + timedelta(days=1))
        self.assertIn("assistant : RuntimeError : encore", [l["texte"][17:] for l in p.rubriques[0].lignes])
        self.assertEqual(p.depuis, iso(self.maintenant - timedelta(seconds=1)))

    def test_premier_point(self):
        temporaire = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporaire.cleanup)
        journal = jr.Journal(Path(temporaire.name) / "manager.db", delai=0.2)
        journal.noter_question("Bonjour", decision="repondre", cout_usd=0.01)
        p = ini.point_du_matin(journal, tableau_vide(), self.maintenant)
        self.assertTrue(p.premier)
        self.assertEqual(p.depuis, iso(self.maintenant - timedelta(days=ini.POINT_JOURS_MAX)))

    def test_lignes_bornees(self):
        for i in range(ini.LIGNES_MAX + 2):
            self.journal.noter_erreur("assistant", f"erreur {i}")
        r = ini.point_du_matin(self.journal, tableau_vide(), self.maintenant).rubriques[0]
        self.assertEqual((len(r.lignes), r.autres), (ini.LIGNES_MAX, 2))

    def test_journal_illisible(self):
        temporaire = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporaire.cleanup)
        fichier = Path(temporaire.name) / "illisible.db"
        fichier.write_bytes(b"pas une base" * 100)
        self.assertIsNone(ini.point_du_matin(jr.Journal(fichier, delai=0.2), tableau_vide(), self.maintenant))


if __name__ == "__main__":
    unittest.main()
