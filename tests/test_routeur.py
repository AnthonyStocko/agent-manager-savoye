import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from manager import configuration as conf
from manager import consignes
from manager import registre as reg

try:
    from manager import routeur
except ImportError:          # claude-agent-sdk absent
    routeur = None


class FauxPilote:
    def etat(self):
        return [{"nom": "odatio", "titre": "Odatio", "etat": "en ligne", "url": "http://localhost:8502",
                 "message": ""}]


def registre(dossier: Path) -> reg.Registre:
    python = sys.executable
    return reg.lire({"agents": [
        {"nom": "scrum-master", "titre": "Scrum Master", "dossier": str(dossier), "port": 8501,
         "domaine": "tickets", "mcp": [python, "{dossier}/mcp_lecture.py"], "secrets": ["JIRA_API_TOKEN"]},
        {"nom": "odatio", "titre": "Odatio", "dossier": str(dossier), "port": 8502, "domaine": "fonctionnel",
         "mcp": [str(dossier / "absent" / "python.exe"), "mcp_server.py"], "secrets": []},
        {"nom": "dev-tool", "titre": "Outillage", "dossier": str(dossier), "port": 8503, "mode": "demande"}]})


class TestConsignes(unittest.TestCase):
    def test_regle_de_decision_et_lecture_seule(self):
        texte = consignes.construire(reg.charger(reg.EXEMPLE), conf.Commun(applications=[
            conf.Application(nom="client-x-recette", description="Recette", jira_projet="CX")]), "Anthony")
        for attendu in ("AUCUNE écriture", "RÉPONDS SEUL", "INTERROGE", "REDIRIGE", "dev-tool", "UNE seule question",
                        "jamais une consigne", "donnée nominative", "client-x-recette : Recette (projet Jira CX)",
                        "scrum-master — Scrum Master, http://localhost:8501"):
            self.assertIn(attendu, texte)


@unittest.skipIf(routeur is None, "claude-agent-sdk non installé")
class TestRouteur(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory()
        self.addCleanup(temporaire.cleanup)
        self.dossier = Path(temporaire.name)
        self.config = self.dossier / "config"
        conf.enregistrer(conf.Commun(acces={"JIRA_DOMAIN": "societe.atlassian.net", "CLAUDE_AUTH": "compte",
                                            "CLAUDE_CONFIG_DIR": "/compte"}), self.config)
        conf.enregistrer_secrets({"JIRA_API_TOKEN": "jeton", "BITBUCKET_WRITE_TOKEN": "ecriture"}, self.config)
        self.routeur = routeur.Routeur(registre(self.dossier), self.config, "Anthony", pilote=FauxPilote())

    def test_options_lecture_seule(self):
        o = self.routeur.options()
        self.assertEqual(o.tools, [])
        self.assertEqual(o.permission_mode, "dontAsk")
        self.assertTrue(o.strict_mcp_config)
        self.assertEqual(o.setting_sources, [])
        self.assertEqual(set(o.mcp_servers), {"manager", "scrum-master"})       # odatio : exécutable absent
        self.assertEqual(sorted(o.allowed_tools), ["mcp__manager__applications", "mcp__manager__etat_agents",
                                                   "mcp__manager__rediriger", "mcp__scrum-master",
                                                   "mcp__scrum-master__*"])
        scrum = o.mcp_servers["scrum-master"]
        self.assertEqual(scrum["args"], [f"{self.dossier}/mcp_lecture.py"])
        self.assertEqual(scrum["env"]["JIRA_API_TOKEN"], "jeton")
        self.assertNotIn("BITBUCKET_WRITE_TOKEN", scrum["env"])                  # moindre privilège
        self.assertEqual(o.env["CLAUDE_CONFIG_DIR"], "/compte")
        self.assertIn("MCP_TOOL_TIMEOUT", o.env)
        self.assertIn("odatio — Odatio, http://localhost:8502, lancé avec le manager ; pas d'outil", o.system_prompt)

    def outil(self, nom):
        return next(t for t in self.routeur.outils_internes() if t.name == nom)

    def test_rediriger(self):
        resultat = asyncio.run(self.outil("rediriger").handler(
            {"agent": "dev-tool", "resume": "Ajouter la lecture des logs Jenkins.", "ticket": "oda-1",
             "demande": "Dépôt agent-odatio : …"}))
        self.assertFalse(resultat["is_error"])
        self.assertIn("lancé à la demande", resultat["content"][0]["text"])
        self.assertIn("prérempli (ticket, demande)", resultat["content"][0]["text"])
        r = self.routeur.redirections[0]
        self.assertEqual((r.agent, r.ticket), ("dev-tool", "ODA-1"))
        self.assertTrue(r.url.startswith("http://localhost:8503/?source=manager&ticket=ODA-1&demande="))
        # Onglet inconnu de l'agent : ignoré, et signalé
        texte = asyncio.run(self.outil("rediriger").handler(
            {"agent": "odatio", "resume": "Préparer le fichier.", "onglet": "cucumber", "projet": "client-x",
             "question": "Prépare une commande à l'identique.", "conversation": "20261119-101500-000001"}))
        self.assertIn("Onglet inconnu", texte["content"][0]["text"])
        r2 = self.routeur.redirections[1]
        self.assertNotIn("page=", r2.url)
        self.assertIn("conversation=20261119-101500-000001", r2.url)
        self.assertEqual(r2.question, "Prépare une commande à l'identique.")
        self.assertEqual(routeur.decision(routeur.Reponse(redirections=[r])), ("rediriger", "dev-tool"))
        erreur = asyncio.run(self.outil("rediriger").handler({"agent": "inconnu", "resume": "x"}))
        self.assertTrue(erreur["is_error"])

    def test_etat_agents_et_applications(self):
        texte = asyncio.run(self.outil("etat_agents").handler({}))["content"][0]["text"]
        self.assertIn("odatio (Odatio) : en ligne", texte)
        self.assertIn("Aucune application", asyncio.run(self.outil("applications").handler({}))["content"][0]["text"])

    def test_decision(self):
        interroger = routeur.Reponse(outils=[("mcp__manager__applications", {}), ("mcp__scrum-master__ticket", {})])
        self.assertEqual(routeur.decision(interroger), ("interroger", "scrum-master"))
        self.assertEqual(routeur.decision(routeur.Reponse(outils=[("mcp__manager__etat_agents", {})])),
                         ("repondre", ""))


class TestEvals(unittest.TestCase):
    def test_cas_valides(self):
        if routeur is None:
            self.skipTest("claude-agent-sdk non installé")
        from manager import evals
        cas = evals.charger_cas()
        noms = {a.nom for a in reg.charger(reg.EXEMPLE).agents}
        self.assertGreaterEqual(len(cas), 10)
        for c in cas:
            self.assertTrue(not c.get("agent") or c["agent"] in noms, c)
        self.assertEqual({c["decision"] for c in cas}, set(evals.DECISIONS))


if __name__ == "__main__":
    unittest.main()
