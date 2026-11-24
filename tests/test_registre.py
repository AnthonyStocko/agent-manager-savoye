import tempfile
import unittest
from pathlib import Path

from manager import registre as reg


def agent(nom="odatio", port=8502, **autres):
    return {"nom": nom, "dossier": f"../agent-{nom}", "port": port, **autres}


class TestRegistre(unittest.TestCase):
    def test_exemple_valide(self):
        registre = reg.charger(reg.EXEMPLE)
        self.assertEqual([a.nom for a in registre.agents], ["scrum-master", "odatio", "dev-tool", "projets", "integration",
                                                              "lancement", "cucumber", "revue-pr"])
        self.assertEqual([a.port for a in registre.agents], [8501, 8502, 8503, 8504, 8505, 8506, 8507, 8508])
        self.assertEqual(registre.agent("dev-tool").mode, "demande")
        self.assertTrue(all(a.domaine for a in registre.agents))
        self.assertEqual((registre.port_manager, registre.port_superviseur), (8500, 8499))
        self.assertEqual(registre.agent("odatio").dossier, (reg.RACINE.parent / "agent-odatio").resolve())

    def test_valeurs_par_defaut_et_commande(self):
        registre = reg.lire({"agents": [agent()]}, base=Path("/base/manager"))
        odatio = registre.agent("odatio")
        self.assertEqual((odatio.mode, odatio.titre, odatio.url), ("auto", "odatio", "http://localhost:8502"))
        commande = odatio.ligne_de_commande()
        self.assertEqual(commande[1:5], ["-m", "streamlit", "run", "app.py"])
        self.assertIn("8502", commande)
        self.assertEqual(commande[commande.index("--server.address") + 1], "127.0.0.1")
        self.assertIn("venv", commande[0])
        commande = reg.lire({"agents": [agent(commande=["{python}", "outil.py", "{port}"])]}).agents[0]
        self.assertEqual(commande.ligne_de_commande()[1:], ["outil.py", "8502"])

    def test_conflits_de_ports(self):
        for agents, attendu in (([agent("a", 8501), agent("b", 8501)], "déjà pris par « a »"),
                                ([agent("a", 8500)], "déjà pris par « manager »"),
                                ([agent("a", 8499)], "déjà pris par « superviseur »")):
            with self.subTest(attendu=attendu), self.assertRaises(reg.RegistreErreur) as e:
                reg.lire({"agents": agents})
            self.assertIn(attendu, str(e.exception))
        with self.assertRaises(reg.RegistreErreur):
            reg.lire({"manager": {"port": 8600, "port_superviseur": 8600}})

    def test_entrees_invalides(self):
        cas = {"en double": [agent("a", 8501), agent("a", 8502)],
               "nom invalide": [agent("Odatio")],
               "dossier manquant": [{"nom": "a", "port": 8501}],
               "mode": [agent(mode="parfois")],
               "port invalide": [agent(port="8502")],
               "port invalide ": [agent(port=80)],
               "commande": [agent(commande=[])]}
        for attendu, agents in cas.items():
            with self.subTest(attendu), self.assertRaises(reg.RegistreErreur) as e:
                reg.lire({"agents": agents})
            self.assertIn(attendu.strip(), str(e.exception))

    def test_toutes_les_erreurs_signalees_ensemble(self):
        with self.assertRaises(reg.RegistreErreur) as e:
            reg.lire({"agents": [agent("A"), agent("b", 8501), agent("c", 8501)]})
        self.assertIn("nom invalide", str(e.exception))
        self.assertIn("déjà pris", str(e.exception))

    def test_fichier_illisible_ou_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            fichier = Path(temp) / "agents.toml"
            fichier.write_text("[[agents]\n", encoding="utf-8")
            with self.assertRaises(reg.RegistreErreur):
                reg.charger(fichier)
            with self.assertRaises(reg.RegistreErreur):
                reg.charger(Path(temp) / "absent.toml")

    def test_agent_inconnu(self):
        with self.assertRaises(reg.RegistreErreur):
            reg.lire({}).agent("x")


if __name__ == "__main__":
    unittest.main()
