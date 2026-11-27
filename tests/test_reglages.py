"""Onglet Configuration › Réglages des agents (ihm_reglages) : .env partagé d'AGENT_DATA_DIR, repris de la page
Configuration d'agent-scrum-master. Données fictives, dossier temporaire (jamais le vrai AGENT_DATA_DIR)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dotenv import dotenv_values
from streamlit.testing.v1 import AppTest

from manager import configuration as conf
from manager import ihm_reglages

SCRIPT = """
from manager import ihm_reglages
ihm_reglages.afficher()
"""


class TestReglages(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporaire.cleanup)
        self.env = Path(temporaire.name) / ".env"
        self.env.write_text("JIRA_DOMAIN=ancien.atlassian.net\nJIRA_API_TOKEN=jeton-du-env\nCUCUMBER_BATCH_SIZE=7\n"
                            "MEMORY_MAX_CHARS=4000\n", encoding="utf-8")
        # .env et configuration commune du test : le manager transmet JIRA_DOMAIN et BITBUCKET_HTTP_TOKEN
        patch = mock.patch.object(ihm_reglages, "ENV_PATH", str(self.env))
        patch.start()
        self.addCleanup(patch.stop)
        for nom, retour in (("charger", conf.Commun(acces={"JIRA_DOMAIN": "commun.atlassian.net"})),
                            ("secrets", {"BITBUCKET_HTTP_TOKEN": "jeton-du-manager"})):
            patch = mock.patch.object(conf, nom, return_value=retour)
            patch.start()
            self.addCleanup(patch.stop)
        self.app = AppTest.from_string(SCRIPT, default_timeout=120)

    def champ(self, libelle):
        return next(t for t in self.app.text_input if t.label == libelle)

    def test_affichage(self):
        self.app.run()
        self.assertFalse(self.app.exception, [e.value for e in self.app.exception])
        self.assertEqual(self.champ("JIRA_DOMAIN").value, "commun.atlassian.net")    # transmise par le manager
        self.assertEqual(self.champ("JIRA_API_TOKEN").value, "")                     # secret jamais renvoyé
        self.assertIn("non affiché", self.champ("JIRA_API_TOKEN").placeholder)
        self.assertEqual(next(n for n in self.app.number_input if n.label == "CUCUMBER_BATCH_SIZE").value, 7)
        self.assertTrue(any("JIRA_DOMAIN" in i.value for i in self.app.info))        # gérées : signalées
        self.assertEqual([s.value for s in self.app.subheader], ["Réglages des agents (.env partagé)",
                                                                 "Mémoire des agents"])

    def test_enregistrement_sans_reecrire_les_variables_du_manager(self):
        self.app.run()
        next(n for n in self.app.number_input if n.label == "MEMORY_MAX_CHARS").set_value(5000)
        next(b for b in self.app.button if "Enregistrer la configuration complète" in b.label).click()
        self.app.run()
        self.assertFalse(self.app.exception, [e.value for e in self.app.exception])
        ecrit = dotenv_values(self.env)
        self.assertEqual(ecrit["MEMORY_MAX_CHARS"], "5000")
        self.assertEqual(ecrit["CUCUMBER_BATCH_SIZE"], "7")
        self.assertEqual(ecrit["JIRA_DOMAIN"], "ancien.atlassian.net")       # gérée par le manager : non réécrite
        self.assertEqual(ecrit["JIRA_API_TOKEN"], "jeton-du-env")            # secret non saisi : gardé
        self.assertNotIn("BITBUCKET_HTTP_TOKEN", ecrit)                      # secret du manager : jamais écrit


if __name__ == "__main__":
    unittest.main()
