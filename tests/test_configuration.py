import tempfile
import tomllib
import unittest
from pathlib import Path

from manager import configuration as conf
from manager import registre as reg

EXEMPLE = reg.RACINE / "config" / "commun.example.toml"


class AvecDossier(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory()
        self.addCleanup(temporaire.cleanup)
        self.dossier = Path(temporaire.name) / "config"


class TestCommun(AvecDossier):
    def test_modele_valide_et_relu_a_l_identique(self):
        commun = conf.lire(tomllib.loads(EXEMPLE.read_text(encoding="utf-8")))
        self.assertEqual(commun.application("client-x-recette").jira_projet, "CX")
        conf.enregistrer(commun, self.dossier)
        relu = conf.charger(self.dossier)
        self.assertEqual(relu, commun)
        texte = (self.dossier / conf.FICHIER).read_text(encoding="utf-8")
        self.assertIn("[[applications.sources]]", texte)
        self.assertIn("[applications.logs.ssh]", texte)

    def test_absent_vide(self):
        self.assertEqual(conf.charger(self.dossier), conf.Commun())

    def test_valeurs_speciales_echappees(self):
        commun = conf.Commun(acces={"JIRA_EMAIL": 'a"b\\c'},
                             applications=[conf.Application(nom="app", description="ligne1\nligne2 « é »")])
        conf.enregistrer(commun, self.dossier)
        self.assertEqual(conf.charger(self.dossier), commun)

    def test_refus(self):
        cas = {"secrets dans commun.toml": {"acces": {"JIRA_API_TOKEN": "x"}},
               "nom d'application invalide": {"applications": [{"nom": "Canson Prod"}]},
               "en double": {"applications": [{"nom": "a"}, {"nom": "a"}]},
               "pas les deux": {"applications": [{"nom": "a", "sources": [{"depot": "d", "chemin": "c"}]}]}}
        for attendu, donnees in cas.items():
            with self.subTest(attendu), self.assertRaises(conf.ConfigurationErreur) as e:
                conf.lire(donnees)
            self.assertIn(attendu, str(e.exception))
        with self.assertRaises(conf.ConfigurationErreur):
            conf.enregistrer(conf.Commun(applications=[conf.Application(nom="A B")]), self.dossier)

    def test_noms_des_secrets_d_application(self):
        self.assertEqual(conf.secrets_application("canson-prod"),
                         {"base": "DB_PASSWORD_CANSON_PROD", "application": "APP_PASSWORD_CANSON_PROD",
                          "ssh": "SSH_PASSWORD_CANSON_PROD"})


class TestSecrets(AvecDossier):
    def test_lecture_ecriture_env(self):
        conf.enregistrer_secrets({"JIRA_API_TOKEN": "jeton", "DB_PASSWORD_A": "mdp=avec#diese"}, self.dossier)
        fichier = self.dossier / conf.FICHIER_SECRETS
        fichier.write_text(fichier.read_text(encoding="utf-8") + "# commentaire gardé\nAUTRE='entre guillemets'\n",
                           encoding="utf-8")
        conf.enregistrer_secrets({"JIRA_API_TOKEN": "nouveau", "DB_PASSWORD_A": ""}, self.dossier)
        self.assertEqual(conf.secrets(self.dossier), {"JIRA_API_TOKEN": "nouveau", "AUTRE": "entre guillemets"})
        self.assertIn("# commentaire gardé", fichier.read_text(encoding="utf-8"))
        for valeurs in ({"mauvais nom": "x"}, {"A": "deux\nlignes"}):
            with self.subTest(valeurs), self.assertRaises(conf.ConfigurationErreur):
                conf.enregistrer_secrets(valeurs, self.dossier)

    def test_variables_par_agent_moindre_privilege(self):
        conf.enregistrer(conf.Commun(acces={"JIRA_DOMAIN": "societe.atlassian.net"}), self.dossier)
        conf.enregistrer_secrets({"JIRA_API_TOKEN": "j", "BITBUCKET_WRITE_TOKEN": "w", "DB_PASSWORD_A": "d",
                                  "SSH_PASSWORD_A": "s"}, self.dossier)
        odatio = reg.Agent(nom="odatio", titre="o", dossier=self.dossier, port=8502,
                           secrets=("JIRA_API_TOKEN", "DB_PASSWORD_*"))
        variables = conf.variables_pour(odatio, self.dossier)
        self.assertEqual(variables, {"SAVOYE_CONFIG_DIR": str(self.dossier), "JIRA_DOMAIN": "societe.atlassian.net",
                                     "JIRA_API_TOKEN": "j", "DB_PASSWORD_A": "d"})
        rien = reg.Agent(nom="dev-tool", titre="d", dossier=self.dossier, port=8503)
        self.assertNotIn("JIRA_API_TOKEN", conf.variables_pour(rien, self.dossier))

    def test_registre_exemple_sans_jeton_d_ecriture_pour_odatio(self):
        odatio = reg.charger(reg.EXEMPLE).agent("odatio")
        for interdit in ("BITBUCKET_WRITE_TOKEN", "JENKINS_API_TOKEN"):
            self.assertFalse(conf.autorise(interdit, odatio.secrets))
        self.assertTrue(conf.autorise("SSH_PASSWORD_CANSON_PROD", odatio.secrets))


if __name__ == "__main__":
    unittest.main()
