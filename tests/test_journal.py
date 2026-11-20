import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from manager import journal as jr
from manager import superviseur as sup


@dataclass
class FausseRedirection:
    agent: str
    ticket: str = ""
    projet: str = ""
    onglet: str = ""
    resume: str = ""
    question: str = ""
    demande: str = ""


class TestNettoyage(unittest.TestCase):
    def test_masque_donnees_nominatives_et_secrets(self):
        texte = jr.nettoyer("Écris à jean.dupont@client-x.fr ou au 06 12 34 56 78, voir https://interne.exemple/x "
                            "token=abc123 et sk-ant-0123456789abcdefghijABCDEF")
        for interdit in ("jean.dupont", "@", "06 12", "https://", "abc123", "sk-ant-0123"):
            self.assertNotIn(interdit, texte)
        self.assertIn("[e-mail]", texte)

    def test_dates_iso_non_masquees(self):
        self.assertEqual(jr.nettoyer("Publiée dans Jira le 2026-11-19 à 14:58"),
                         "Publiée dans Jira le 2026-11-19 à 14:58")
        self.assertIn("2026-11-19T14:58:03", jr.nettoyer("vu le 2026-11-19T14:58:03"))
        self.assertNotIn("06 12", jr.nettoyer("le 2026-11-19, appeler le 06 12 34 56 78"))

    def test_garde_cles_et_borne(self):
        self.assertIn("ODA-1234", jr.nettoyer("Où en est ODA-1234 ?"))
        self.assertLessEqual(len(jr.nettoyer("x" * 1000)), jr.EXTRAIT_MAX)
        self.assertEqual(jr.cles("oda-1 et ODA-2, encore ODA-1", "CX-9"), ["ODA-1", "ODA-2", "CX-9"])


class TestJournal(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory()
        self.addCleanup(temporaire.cleanup)
        self.fichier = Path(temporaire.name) / ".tmp" / "manager.db"
        self.journal = jr.Journal(self.fichier, delai=0.2)

    def test_ecriture_et_lecture(self):
        self.assertTrue(self.journal.noter_question(
            "Où en est l'analyse de ODA-1 ?", decision="interroger",
            outils=[("mcp__scrum-master__analyse", {"ticket": "ODA-1"}), ("mcp__manager__etat_agents", {})],
            cout_usd=0.12, tours=3, duree_s=4.2))
        self.assertTrue(self.journal.noter_etat("odatio", "en ligne", "en échec", "arrêté 3 fois"))
        evenements = self.journal.evenements()
        self.assertEqual([e.type for e in evenements], ["etat", "question"])
        q = evenements[1]
        self.assertEqual((q.decision, q.agent, q.ticket, q.cout_usd, q.tours), ("interroger", "scrum-master", "ODA-1",
                                                                                0.12, 3))
        self.assertEqual(q.details, {"agents": ["scrum-master"], "outils": ["analyse", "etat_agents"]})
        self.assertEqual(q.tickets, ["ODA-1"])
        self.assertEqual(evenements[0].details["apres"], "en échec")
        self.assertTrue(self.fichier.exists())
        with closing(sqlite3.connect(self.fichier)) as c:
            self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_aucune_donnee_nominative_stockee(self):
        self.journal.noter_question(
            "Pourquoi la commande de M. Martin (martin@client-x.fr, 0612345678) bloque sur ODA-7 ?",
            decision="rediriger", outils=[("mcp__odatio__question", {"question": "M. Martin martin@client-x.fr"})],
            redirections=[FausseRedirection("odatio", ticket="ODA-7", projet="client-x-recette", onglet="assistant",
                                            resume="Commande de M. Martin, tél. 0612345678",
                                            question="Commande de M. Martin ?", demande="secret")])
        with closing(sqlite3.connect(self.fichier)) as c:
            contenu = " ".join(str(v) for ligne in c.execute("SELECT * FROM evenements") for v in ligne)
        for interdit in ("martin@", "0612345678", "tél.", "secret", "Commande de M. Martin ?"):
            self.assertNotIn(interdit, contenu)
        self.assertIn("client-x-recette", contenu)
        self.assertIn("ODA-7", contenu)

    def test_index_par_ticket(self):
        self.journal.noter_question("Qu'est-ce qu'on a fait sur ODA-1 et ODA-2 ?", decision="repondre",
                                    cout_usd=0.05, tours=2)
        self.journal.noter_question("Lance les tests Cucumber de ODA-1", decision="rediriger", cout_usd=0.10,
                                    redirections=[FausseRedirection("scrum-master", ticket="ODA-1",
                                                                    onglet="cucumber")])
        self.journal.observer_analyses([{"cle": "ODA-1", "libelle": "Analyse non publiée (brouillon)",
                                         "a_valider": True, "modifiee": "2026-11-19 10:00"},
                                        {"cle": "pas-une-cle"}])
        vue = self.journal.ticket("oda-1")
        self.assertEqual(vue["nb_questions"], 2)
        self.assertEqual(vue["cout_manager_usd"], 0.15)
        self.assertEqual([r.agent for r in vue["redirections"]], ["scrum-master"])
        self.assertEqual(vue["redirections"][0].details["onglet"], "cucumber")
        self.assertEqual(vue["analyse"]["analyse"], "Analyse non publiée (brouillon)")
        self.assertTrue(vue["analyse"]["a_valider"])
        self.assertEqual(self.journal.ticket("ODA-2")["nb_questions"], 1)
        self.assertEqual(self.journal.ticket("ODA-3")["nb_questions"], 0)
        with self.assertRaises(jr.JournalErreur):
            self.journal.ticket("pas une clé")
        recents = {t["cle"]: t for t in self.journal.tickets_recents()}
        self.assertEqual(set(recents), {"ODA-1", "ODA-2"})
        self.assertTrue(recents["ODA-1"]["a_valider"])

    def test_tickets_recents_ignore_analyses_anciennes(self):
        ancien = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M")
        recent = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        self.journal.observer_analyses([{"cle": "ODA-1", "libelle": f"Publiée dans Jira le {ancien}",
                                         "modifiee": ancien},
                                        {"cle": "ODA-2", "libelle": "brouillon", "modifiee": recent},
                                        {"cle": "ODA-3", "libelle": "date illisible", "modifiee": "hier"}])
        recents = {t["cle"]: t for t in self.journal.tickets_recents(jours=7)}
        self.assertEqual(set(recents), {"ODA-2"})
        self.assertEqual(recents["ODA-2"]["derniere_activite"], recent.replace(" ", "T"))
        self.assertEqual(self.journal.ticket("ODA-1")["analyse"]["analyse"], f"Publiée dans Jira le {ancien}")

    def test_couts(self):
        self.journal.noter_question("q1", decision="repondre", cout_usd=0.2)
        self.journal.noter_question("q2", decision="repondre", cout_usd=None)
        self.assertEqual(self.journal.couts(7), {"questions": 2, "cout_usd": 0.2, "sans_cout": 1})

    def test_purge_retention_et_nombre(self):
        journal = jr.Journal(self.fichier, retention_jours=30, lignes_max=3)
        for i in range(5):
            journal.noter("erreur", extrait=f"e{i}")
        ancien = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self.fichier)) as c:
            c.execute("UPDATE evenements SET horodatage = ? WHERE extrait = 'e4'", (ancien,))
            c.commit()
        self.assertEqual(journal.purger(), 2)        # e4 trop vieux, puis e0 au-delà des 3 plus récentes
        self.assertEqual([e.extrait for e in journal.evenements()], ["e3", "e2", "e1"])

    def test_purge_automatique(self):
        journal = jr.Journal(self.fichier, lignes_max=2)
        for i in range(4):
            journal.noter("erreur", extrait=f"e{i}")
        self.assertEqual(len(journal.evenements()), 2 + 2)       # purge au premier ajout, puis au plus une fois par heure
        journal._derniere_purge = time.monotonic() - jr.PURGE_INTERVALLE_S - 1
        journal.noter("erreur", extrait="e4")
        self.assertEqual(len(journal.evenements()), 2)

    def test_type_inconnu_refuse(self):
        self.assertFalse(self.journal.noter("autre"))

    def test_base_verrouillee_ne_bloque_pas(self):
        self.journal.noter("erreur", extrait="avant")
        verrou = sqlite3.connect(self.fichier, isolation_level=None)
        self.addCleanup(verrou.close)
        verrou.execute("BEGIN EXCLUSIVE")
        debut = time.monotonic()
        self.assertFalse(self.journal.noter_question("ODA-1 ?", decision="repondre"))
        self.assertFalse(self.journal.noter_etat("odatio", "", "en ligne"))
        self.assertLess(time.monotonic() - debut, 3)
        self.assertIn("locked", self.journal.derniere_erreur)
        verrou.execute("ROLLBACK")
        self.assertTrue(self.journal.noter("erreur", extrait="après"))

    def test_base_illisible(self):
        self.fichier.parent.mkdir(parents=True)
        self.fichier.write_bytes(b"ceci n'est pas une base SQLite" * 100)
        journal = jr.Journal(self.fichier, delai=0.2)
        self.assertFalse(journal.noter_question("ODA-1 ?", decision="repondre"))
        self.assertFalse(journal.observer_analyses([{"cle": "ODA-1"}]))
        self.assertEqual(journal.purger(), 0)
        for lecture in (journal.evenements, lambda: journal.ticket("ODA-1"), journal.tickets_recents,
                        lambda: journal.couts(7)):
            with self.assertRaises(jr.JournalErreur):
                lecture()

    def test_dossier_impossible(self):
        bloquant = self.fichier.parent.parent / "fichier"
        bloquant.parent.mkdir(parents=True, exist_ok=True)
        bloquant.write_text("x")
        journal = jr.Journal(bloquant / "manager.db")       # le parent est un fichier
        self.assertFalse(journal.noter("erreur", extrait="x"))


class TestSuperviseurJournal(unittest.TestCase):
    def test_changements_notes(self):
        with tempfile.TemporaryDirectory() as d:
            journal = jr.Journal(Path(d) / "manager.db")
            precedent = {}
            etats = [{"nom": "odatio", "etat": sup.DEMARRAGE, "message": ""}]
            self.assertEqual(len(sup.suivre_changements(etats, precedent, journal)), 1)
            self.assertEqual(sup.suivre_changements(etats, precedent, journal), [])
            etats[0].update(etat=sup.ECHEC, message="arrêté 3 fois")
            sup.suivre_changements(etats, precedent, journal)
            e = journal.evenements(types=("etat",))
            self.assertEqual([(x.details["avant"], x.details["apres"]) for x in e],
                             [(sup.DEMARRAGE, sup.ECHEC), ("", sup.DEMARRAGE)])

    def test_journal_indisponible_ne_bloque_pas(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "x").write_text("fichier")
            journal = jr.Journal(Path(d) / "x" / "manager.db")
            self.assertEqual(len(sup.suivre_changements([{"nom": "a", "etat": "en ligne", "message": ""}], {},
                                                        journal)), 1)


if __name__ == "__main__":
    unittest.main()
