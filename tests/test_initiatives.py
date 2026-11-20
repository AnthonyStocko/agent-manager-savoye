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


class TestJoursOuvres(unittest.TestCase):
    def test_ajouter(self):
        vendredi, samedi, jeudi = datetime(2026, 11, 20, 10), datetime(2026, 11, 21, 10), datetime(2026, 11, 19, 10)
        self.assertEqual(ini.ajouter_jours_ouvres(vendredi, 3), datetime(2026, 11, 25, 10))      # mercredi
        self.assertEqual(ini.ajouter_jours_ouvres(samedi, 1), datetime(2026, 11, 23, 10))        # lundi
        self.assertEqual(ini.ajouter_jours_ouvres(jeudi, 1), vendredi)
        self.assertEqual(ini.ajouter_jours_ouvres(jeudi, 0), jeudi)


class TestRelances(unittest.TestCase):
    LUNDI = datetime(2026, 11, 23, 10, 0)

    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporaire.cleanup)
        self.journal = jr.Journal(Path(temporaire.name) / "manager.db", delai=0.2)
        self.t = tableau_vide()
        self.t.agents.lignes = [{"nom": "scrum-master", "url": "http://localhost:8501"}]

    def cles(self, maintenant=None, **seuils):
        return [r.cle for r in ini.relances(self.journal, self.t, seuils, maintenant or self.LUNDI)]

    def test_analyse_a_valider(self):
        self.t.analyses.lignes = [
            {"cle": "ODA-1", "modifiee": "2026-11-18 10:00", "libelle": "brouillon", "lien": "lien-ODA-1"},  # mercredi
            {"cle": "ODA-2", "modifiee": "2026-11-20 10:00", "libelle": "brouillon", "lien": "lien-ODA-2"}]  # vendredi
        relances = ini.relances(self.journal, self.t, {}, self.LUNDI)
        self.assertEqual([(r.cle, r.lien) for r in relances], [("analyse:ODA-1:2026-11-18T10:00", "lien-ODA-1")])
        self.assertIn("ODA-1 : analyse à valider depuis le 2026-11-18 10:00", relances[0].texte)
        self.assertEqual(self.cles(analyse_a_valider_jours=0), [])                             # désactivée
        self.assertEqual(len(self.cles(analyse_a_valider_jours=1)), 2)

    def test_redirection_sans_suite(self):
        redirection = SimpleNamespace(agent="scrum-master", ticket="ODA-7", projet="", onglet="lancement")
        self.journal.noter_question("ODA-7 ?", decision="rediriger", redirections=[redirection])
        self.journal.noter_question("ODA-8 ?", decision="rediriger", redirections=[
            SimpleNamespace(agent="odatio", ticket="ODA-8", projet="", onglet="assistant")])     # autre agent
        apres = ini.ajouter_jours_ouvres(datetime.now(), 2) + timedelta(minutes=1)
        self.assertEqual(self.cles(datetime.now() + timedelta(hours=1)), [])                    # trop tôt
        relances = ini.relances(self.journal, self.t, {}, apres)
        self.assertEqual([r.regle for r in relances], ["redirection_sans_suite_jours"])
        self.assertIn("page=lancement", relances[0].lien)
        self.assertIn("ticket=ODA-7", relances[0].lien)
        self.journal.observer_analyses([{"cle": "ODA-7", "libelle": "brouillon", "a_valider": True,
                                         "modifiee": (datetime.now() + timedelta(minutes=5)).isoformat()[:16]}])
        self.assertEqual(self.cles(apres), [])                            # analyse modifiée depuis : suivie

    def test_patchs(self):
        self.t.patchs.lignes = [
            {"cle": "ODA-4", "livre_le": "2026-11-23 09:00", "fichiers": ["ODA-4.patch"], "statut": "non_jointe",
             "manquants": ["ODA-4.patch"], "raison": "", "lien": "lien-ODA-4"},
            {"cle": "ODA-5", "livre_le": "2026-11-20 09:00", "fichiers": ["ODA-5.patch"], "statut": "non_verifiee",
             "manquants": [], "raison": "Jira illisible (ConnectionError)", "lien": "lien-ODA-5"},
            {"cle": "ODA-6", "livre_le": "2026-11-23 08:00", "fichiers": ["ODA-6.patch"], "statut": "non_verifiee",
             "manquants": [], "raison": "", "lien": "lien-ODA-6"},
            {"cle": "ODA-2", "livre_le": "2026-11-16 09:00", "fichiers": ["ODA-2.patch"], "statut": "jointe",
             "manquants": [], "raison": "", "lien": "lien-ODA-2"}]
        self.assertEqual(self.cles(), ["patch_non_joint:ODA-4:2026-11-23T09:00",
                                       "patch_non_verifie:ODA-5:2026-11-20T09:00"])   # ODA-6 : pas encore 1 j
        self.assertEqual(self.cles(patch_non_joint=False, patch_non_verifie_jours=0), [])

    def test_build_en_echec(self):
        def build(job, numero, resultat):
            return {"job": job, "numero": numero, "resultat": resultat, "en_cours": resultat == "EN COURS",
                    "debut": "2026-11-23 08:00", "duree_min": 20}
        self.t.jenkins.resume["builds"] = [build("RUN", 13, "EN COURS"), build("RUN", 12, "FAILURE"),
                                           build("RUN", 11, "SUCCESS"), build("VERIFY", 5, "SUCCESS"),
                                           build("VERIFY", 4, "FAILURE")]
        relances = ini.relances(self.journal, self.t, {}, self.LUNDI)
        self.assertEqual([r.cle for r in relances], ["build:RUN:12"])        # VERIFY : réussi depuis
        self.assertIn("page=cucumber", relances[0].lien)
        self.assertEqual(self.cles(build_en_echec=False), [])

    def test_generation_bloquee(self):
        self.t.jenkins.lignes = [
            {"type": "client", "client": "client-x", "en_cours": True, "depuis": "2026-11-23T03:00:00", "lien": "l"},
            {"type": "client", "client": "client-y", "en_cours": True, "depuis": "2026-11-23T06:00:00", "lien": "l"},
            {"type": "client", "client": "client-z", "en_cours": False, "depuis": "", "lien": "l"}]
        self.assertEqual(self.cles(), ["generation:client-x:2026-11-23T03:00:00"])       # 7 h ; client-y : 4 h
        self.assertEqual(self.cles(generation_bloquee_heures=0), [])

    def test_ignorer_puis_situation_changee(self):
        self.t.analyses.lignes = [{"cle": "ODA-1", "modifiee": "2026-11-16 10:00", "libelle": "b", "lien": "l"}]
        cle = self.cles()[0]
        self.assertTrue(self.journal.ignorer_relance(cle, 7))
        self.assertEqual(self.cles(), [])
        self.assertEqual(len(ini.regles(self.journal, self.t, {}, self.LUNDI)), 1)     # toujours active
        self.t.analyses.lignes[0]["modifiee"] = "2026-11-17 15:00"                     # analyse retouchée
        self.assertEqual(self.cles(), ["analyse:ODA-1:2026-11-17T15:00"])

    def test_sans_url_ni_journal(self):
        self.t.agents.lignes = []                                              # superviseur injoignable
        self.t.tickets.lignes = [{"cle": "ODA-4", "lien": "lien-du-tableau"}]
        self.t.patchs.lignes = [{"cle": "ODA-4", "livre_le": "2026-11-23 09:00", "fichiers": ["a.patch"],
                                 "statut": "non_jointe", "manquants": ["a.patch"], "raison": "", "lien": ""}]
        temporaire = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporaire.cleanup)
        fichier = Path(temporaire.name) / "illisible.db"
        fichier.write_bytes(b"pas une base" * 100)
        relances = ini.relances(jr.Journal(fichier, delai=0.2), self.t, {}, self.LUNDI)
        self.assertEqual([(r.regle, r.lien) for r in relances], [("patch_non_joint", "lien-du-tableau")])


if __name__ == "__main__":
    unittest.main()
