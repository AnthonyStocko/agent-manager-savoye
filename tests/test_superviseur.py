import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from manager import configuration
from manager import registre as reg
from manager import superviseur as sup
from manager.pilote import Pilote, PiloteErreur

FAUX = Path(__file__).resolve().parent / "faux_agent.py"


def port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def faux(nom: str, dossier: Path, mode: str = "normal", lancement: str = "auto") -> reg.Agent:
    return reg.Agent(nom=nom, titre=nom.capitalize(), dossier=dossier, port=port_libre(), mode=lancement,
                     commande=(sys.executable, str(FAUX), "{port}", mode))


class TestSuperviseur(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dossier = Path(self.temp.name)
        self.superviseurs: list[sup.Superviseur] = []

    def tearDown(self):
        for s in self.superviseurs:
            s.arreter_tout()
        self.temp.cleanup()

    def superviseur(self, *agents, **options) -> sup.Superviseur:
        s = sup.Superviseur(list(agents), dossier_logs=self.dossier / "logs", **options)
        self.superviseurs.append(s)
        return s

    def attendre(self, s: sup.Superviseur, nom: str, etat: str, delai: float = 15.0) -> dict:
        fin = time.monotonic() + delai
        while time.monotonic() < fin:
            s.verifier()
            resume = s.suivis[nom].resume()
            if resume["etat"] == etat:
                return resume
            time.sleep(0.2)
        self.fail(f"{nom} : état « {s.suivis[nom].etat} » au lieu de « {etat} »")

    def test_demarrage_sante_et_arret(self):
        agent = faux("odatio", self.dossier)
        s = self.superviseur(agent)
        self.assertEqual(s.demarrer("odatio")["etat"], sup.DEMARRAGE)
        resume = self.attendre(s, "odatio", sup.EN_LIGNE)
        self.assertIsNotNone(resume["pid"])
        self.assertTrue(sup.sante(agent.port))
        self.assertEqual(s.arreter("odatio")["etat"], sup.ARRETE)
        self.assertFalse(sup.sante(agent.port))
        s.verifier()
        self.assertEqual(s.suivis["odatio"].etat, sup.ARRETE)      # arrêt voulu : pas de relance
        self.assertIn("lancement", s.journal("odatio").read_text(encoding="utf-8"))

    def test_relance_apres_un_arret_inattendu(self):
        s = self.superviseur(faux("odatio", self.dossier))
        s.demarrer("odatio")
        self.attendre(s, "odatio", sup.EN_LIGNE)
        s.suivis["odatio"].processus.kill()
        s.suivis["odatio"].processus.wait()
        s.verifier()
        self.assertEqual(s.suivis["odatio"].etat, sup.DEMARRAGE)
        self.assertIn("relancé", s.suivis["odatio"].message)
        self.assertEqual(self.attendre(s, "odatio", sup.EN_LIGNE)["relances"], 1)

    def test_echec_apres_trop_de_relances(self):
        s = self.superviseur(faux("odatio", self.dossier, mode="meurt"), max_relances=2)
        s.demarrer("odatio")
        resume = self.attendre(s, "odatio", sup.ECHEC)
        self.assertEqual(resume["relances"], 2)
        self.assertIn("code 3", resume["message"])
        self.assertFalse(s.suivis["odatio"].voulu)
        self.assertEqual(s.demarrer("odatio")["etat"], sup.DEMARRAGE)   # un démarrage manuel repart de zéro
        self.assertEqual(s.suivis["odatio"].relances, [])

    def test_processus_qui_ne_repond_pas(self):
        s = self.superviseur(faux("odatio", self.dossier, mode="muet"), delai_demarrage=0.5)
        s.demarrer("odatio")
        self.assertIn("journal", self.attendre(s, "odatio", sup.INJOIGNABLE)["message"])

    def test_agent_deja_lance_ailleurs(self):
        externe = self.superviseur(faux("odatio", self.dossier))
        externe.demarrer("odatio")
        self.attendre(externe, "odatio", sup.EN_LIGNE)
        s = self.superviseur(externe.suivis["odatio"].agent)
        resume = s.demarrer("odatio")
        self.assertEqual((resume["etat"], resume["pid"]), (sup.EXTERNE, None))
        externe.arreter("odatio")
        s.verifier()
        self.assertEqual(s.suivis["odatio"].etat, sup.ARRETE)

    def test_installation_incomplete(self):
        absent = reg.Agent(nom="absent", titre="Absent", dossier=self.dossier / "absent", port=port_libre())
        sans_venv = reg.Agent(nom="sans-venv", titre="Sans venv", dossier=self.dossier, port=port_libre())
        s = self.superviseur(absent, sans_venv)
        self.assertIn("dossier absent", s.demarrer("absent")["message"])
        resume = s.demarrer("sans-venv")
        self.assertEqual(resume["etat"], sup.INSTALLATION)
        self.assertIn("venv", resume["message"])

    def test_demarrer_auto_ignore_les_agents_a_la_demande(self):
        s = self.superviseur(faux("odatio", self.dossier), faux("dev-tool", self.dossier, lancement="demande"))
        s.demarrer_auto()
        self.assertEqual({a["nom"]: a["etat"] for a in s.etat()},
                         {"odatio": sup.DEMARRAGE, "dev-tool": sup.ARRETE})

    def test_configuration_commune_transmise_au_lancement(self):
        temoin = self.dossier / "env.txt"
        script = ("import os, pathlib, time; pathlib.Path(r'%s').write_text(os.environ.get('JIRA_API_TOKEN', '') + '|' "
                  "+ os.environ.get('SAVOYE_CONFIG_DIR', ''), encoding='utf-8'); time.sleep(30)" % temoin)
        agent = reg.Agent(nom="odatio", titre="Odatio", dossier=self.dossier, port=port_libre(),
                          commande=(sys.executable, "-c", script))
        s = self.superviseur(agent, environnement=lambda a: {"JIRA_API_TOKEN": "jeton", "SAVOYE_CONFIG_DIR": "/cfg"})
        s.demarrer("odatio")
        fin = time.monotonic() + 10
        while not temoin.exists() and time.monotonic() < fin:
            time.sleep(0.1)
        time.sleep(0.2)
        self.assertEqual(temoin.read_text(encoding="utf-8"), "jeton|/cfg")

    def test_configuration_commune_illisible(self):
        def illisible(agent):
            raise configuration.ConfigurationErreur("commun.toml illisible")
        s = self.superviseur(faux("odatio", self.dossier), environnement=illisible)
        resume = s.demarrer("odatio")
        self.assertEqual(resume["etat"], sup.INSTALLATION)
        self.assertIn("commun.toml illisible", resume["message"])
        self.assertIsNone(resume["pid"])

    def test_manager_supervise_comme_un_agent(self):
        manager = sup.agent_manager(reg.lire({}))
        self.assertEqual((manager.nom, manager.port, manager.dossier), ("manager", 8500, reg.RACINE))
        self.assertEqual(manager.ligne_de_commande()[0], sys.executable)


class TestCanalDeCommande(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        dossier = Path(self.temp.name)
        self.superviseur = sup.Superviseur([faux("odatio", dossier)], dossier_logs=dossier / "logs")
        self.port = port_libre()
        self.serveur = sup.serveur_commandes(self.superviseur, self.port, "jeton-de-test")
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()
        self.fichier = dossier / "superviseur.json"
        self.fichier.write_text(json.dumps({"port": self.port, "jeton": "jeton-de-test"}), encoding="utf-8")

    def tearDown(self):
        self.serveur.shutdown()
        self.serveur.server_close()
        self.superviseur.arreter_tout()
        self.temp.cleanup()

    def test_etat_et_ordres(self):
        pilote = Pilote(self.fichier)
        self.assertEqual(pilote.etat()[0]["etat"], sup.ARRETE)
        self.assertEqual(pilote.ordre("odatio", "demarrer")["etat"], sup.DEMARRAGE)
        self.assertEqual(pilote.ordre("odatio", "arreter")["etat"], sup.ARRETE)
        with self.assertRaises(PiloteErreur) as e:
            pilote.ordre("inconnu", "demarrer")
        self.assertIn("agent inconnu", str(e.exception))
        with self.assertRaises(PiloteErreur):
            pilote.ordre("odatio", "supprimer")

    def test_jeton_obligatoire(self):
        self.fichier.write_text(json.dumps({"port": self.port, "jeton": "faux"}), encoding="utf-8")
        with self.assertRaises(PiloteErreur) as e:
            Pilote(self.fichier).etat()
        self.assertIn("jeton", str(e.exception))

    def test_superviseur_absent(self):
        with self.assertRaises(PiloteErreur) as e:
            Pilote(self.fichier.with_name("absent.json")).etat()
        self.assertIn("lancer.bat", str(e.exception))


if __name__ == "__main__":
    unittest.main()
