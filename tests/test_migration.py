import tempfile
import unittest
from pathlib import Path

from manager import configuration as conf
from manager import migration
from manager import registre as reg

PROFILS = """
[agent]
modele = "sonnet"

[profils.Canson-prod]
description = "Production Canson"

[[profils.Canson-prod.sources]]
nom = "standard"
depot = "https://git.exemple.io/scm/std/standard.git"
branche = "integration"

[profils.Canson-prod.base]
type = "postgresql"
hote = "base.exemple.io"
utilisateur = "lecture"
mot_de_passe_env = "DB_PASSWORD_CANSON_PROD"

[profils.Canson-prod.logs]
groundcover_filtre = "namespace:canson"

[profils.Canson-prod.logs.ssh]
hote = "10.0.0.1"
utilisateur = "lecteur"
dossiers = ["/var/log/odatio"]
mot_de_passe_env = "SSH_PASSWORD_CANSON_PROD"

[profils.local]
[[profils.local.sources]]
chemin = "C:/dev/standard"
"""


class TestMigration(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory()
        self.addCleanup(temporaire.cleanup)
        racine = Path(temporaire.name)
        self.config = racine / "manager" / "config"
        # agent-data : .env partagé d'AGENT_DATA_DIR (source « donnees », lu en premier)
        dossiers = {nom: racine / nom for nom in ("agent-data", "agent-odatio", "agent-dev-tool")}
        for d in dossiers.values():
            d.mkdir()
        (dossiers["agent-data"] / ".env").write_text(
            "JIRA_DOMAIN=societe.atlassian.net\nJIRA_EMAIL=moi@societe.com\nJIRA_API_TOKEN=jeton-scrum-1234\n"
            "BITBUCKET_WRITE_TOKEN=ecriture-5678\nCUCUMBER_BATCH_SIZE=3\n", encoding="utf-8")
        (dossiers["agent-odatio"] / ".env").write_text(
            "JIRA_DOMAIN=societe.atlassian.net\nJIRA_API_TOKEN=jeton-odatio-9999\n"
            "DB_PASSWORD_CANSON_PROD=mdp-base\nSSH_PASSWORD_CANSON_PROD=mdp-ssh\n", encoding="utf-8")
        (dossiers["agent-odatio"] / "profils.toml").write_text(PROFILS, encoding="utf-8")
        self.registre = reg.lire({"agents": [
            {"nom": "odatio", "dossier": str(dossiers["agent-odatio"]), "port": 8502},
            {"nom": "dev-tool", "dossier": str(dossiers["agent-dev-tool"]), "port": 8503}]})
        self.donnees = reg.lecteur(dossiers["agent-data"])

    def test_proposition(self):
        p = migration.proposer(self.registre, self.donnees)
        self.assertEqual(p.acces, {"JIRA_DOMAIN": "societe.atlassian.net", "JIRA_EMAIL": "moi@societe.com"})
        self.assertEqual(p.secrets["JIRA_API_TOKEN"], "jeton-scrum-1234")          # .env partagé d'abord
        self.assertEqual(p.origines["JIRA_API_TOKEN"], reg.NOM_LECTEUR)
        self.assertEqual(p.secrets["BITBUCKET_WRITE_TOKEN"], "ecriture-5678")
        self.assertNotIn("CUCUMBER_BATCH_SIZE", p.acces)                            # réglage propre, non importé
        self.assertEqual(len(p.conflits), 1)
        self.assertIn("…1234", p.conflits[0])
        self.assertNotIn("jeton-odatio", p.conflits[0])                             # secret masqué
        canson = next(a for a in p.applications if a.nom == "canson-prod")
        self.assertEqual((canson.base["type"], canson.logs["groundcover_filtre"], canson.logs["ssh"]["hote"]),
                         ("postgresql", "namespace:canson", "10.0.0.1"))
        self.assertNotIn("mot_de_passe_env", canson.logs["ssh"])
        self.assertEqual((p.secrets["DB_PASSWORD_CANSON_PROD"], p.secrets["SSH_PASSWORD_CANSON_PROD"]),
                         ("mdp-base", "mdp-ssh"))
        self.assertIn("local", [a.nom for a in p.applications])
        self.assertTrue(any("dev-tool" in n or "Dev" in n or "pas de .env" in n for n in p.notes))

    def test_application_complete_sans_ecraser(self):
        conf.enregistrer(conf.Commun(acces={"JIRA_EMAIL": "deja@societe.com"},
                                     applications=[conf.Application(nom="local", description="gardée")]), self.config)
        conf.enregistrer_secrets({"JIRA_API_TOKEN": "deja-present"}, self.config)
        gardes = migration.appliquer(migration.proposer(self.registre, self.donnees), self.config)
        commun = conf.charger(self.config)
        self.assertEqual(commun.acces["JIRA_EMAIL"], "deja@societe.com")
        self.assertEqual(commun.acces["JIRA_DOMAIN"], "societe.atlassian.net")
        self.assertEqual(commun.application("local").description, "gardée")
        self.assertEqual(commun.application("canson-prod").jira_projet, "")
        secrets = conf.secrets(self.config)
        self.assertEqual(secrets["JIRA_API_TOKEN"], "deja-present")
        self.assertEqual(secrets["SSH_PASSWORD_CANSON_PROD"], "mdp-ssh")
        self.assertTrue(any("local" in g for g in gardes))
        self.assertNotIn("mdp", (self.config / conf.FICHIER).read_text(encoding="utf-8"))
        # les fichiers des agents ne sont pas modifiés
        self.assertIn("CUCUMBER_BATCH_SIZE", (self.donnees.dossier / ".env").read_text())


if __name__ == "__main__":
    unittest.main()
