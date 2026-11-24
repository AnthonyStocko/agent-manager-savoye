import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from manager import journal as jr
from manager import registre as reg
from manager import sources
from manager import superviseur as sup
from manager import tableau
from manager.pilote import PiloteErreur

FAUX_MCP = Path(__file__).resolve().parent / "faux_mcp.py"
MAINTENANT = datetime(2026, 11, 20, 9, 0)

ANALYSES = ("Analyses de l'agent Scrum Master :\n"
            "- ODA-1 (modifiée le 2026-11-19 10:00) : Analyse non publiée (brouillon) ← à valider\n"
            "- ODA-2 (modifiée le 2026-11-18 09:30) : Publiée dans Jira le 2026-11-18 à 09:45\n"
            "- ODA-3 (modifiée le 2026-11-17 08:00) : Analyse non publiée (brouillon) : révisée depuis sa "
            "publication du 2026-11-16 à 10:00 ← à valider\n"
            "… 2 analyse(s) plus ancienne(s)")
CUCUMBER = ("Cucumber dans l'agent Scrum Master :\n"
            "- client-x : génération en cours (processus 4242, lancée le 2026-11-20T01:00:00 : "
            "generate-cucumber-client)\n"
            "- client-y : aucune génération en cours\n"
            "Jobs Jenkins : JENKINS_CUCUMBER_JOB = CUCUMBER-RUN, CUCUMBER_VERIFY_JOB = CUCUMBER-VERIFY")
BUILDS = ("CUCUMBER-RUN :\n  #12 EN COURS — 2026-11-20 08:40, 20 min\n  #11 SUCCESS — 2026-11-19 17:00, 35 min\n"
          "CUCUMBER-VERIFY :\n  (aucun build lisible)")
PATCHS = ("Patchs livrés ces 7 derniers jours (agent Scrum Master) :\n"
          "- ODA-2 (livré le 2026-11-19 16:00) : ODA-2-standard.patch, ODA-2-specifique.patch — en pièce jointe Jira\n"
          "- ODA-4 (livré le 2026-11-18 11:05) : ODA-4-standard.patch, ODA-4-specifique.patch — NON joint dans "
          "Jira : ODA-4-specifique.patch\n"
          "- ODA-5 (livré le 2026-11-17 09:00) : ODA-5.patch — pièce jointe non vérifiée (Jira illisible "
          "(ConnectionError))")


class FauxPilote:
    def __init__(self, etats=None, erreur=False):
        self.etats, self.erreur = etats, erreur

    def etat(self):
        if self.erreur:
            raise PiloteErreur("superviseur introuvable : lance le manager avec lancer.bat")
        return self.etats if self.etats is not None else [
            {"nom": "scrum-master", "titre": "Scrum Master", "etat": sup.EN_LIGNE, "message": "", "url": "u"},
            {"nom": "odatio", "titre": "Odatio", "etat": sup.ECHEC, "message": "arrêté 3 fois", "url": "u"}]


def registre(dossier: Path) -> reg.Registre:
    return reg.lire({"agents": [
        {"nom": "scrum-master", "titre": "Scrum Master", "dossier": str(dossier), "port": 8501,
         "mcp": [sys.executable, str(FAUX_MCP)]},
        {"nom": "odatio", "titre": "Odatio", "dossier": str(dossier), "port": 8502}]})


class TestLectures(unittest.TestCase):
    def test_analyses(self):
        analyses = sources.lire_analyses(ANALYSES)
        self.assertEqual([(a["cle"], a["a_valider"]) for a in analyses],
                         [("ODA-1", True), ("ODA-2", False), ("ODA-3", True)])
        self.assertEqual(analyses[1]["libelle"], "Publiée dans Jira le 2026-11-18 à 09:45")
        self.assertEqual(sources.lire_analyses("Aucune analyse dans l'agent Scrum Master."), [])

    def test_cucumber_et_builds(self):
        c = sources.lire_cucumber(CUCUMBER)
        self.assertEqual([(x["client"], x["en_cours"], x["depuis"]) for x in c["clients"]],
                         [("client-x", True, "2026-11-20T01:00:00"), ("client-y", False, "")])
        self.assertEqual(c["jobs"], ["CUCUMBER-RUN", "CUCUMBER-VERIFY"])
        b = sources.lire_builds(BUILDS)
        self.assertEqual([(x["job"], x["numero"], x["en_cours"]) for x in b],
                         [("CUCUMBER-RUN", 12, True), ("CUCUMBER-RUN", 11, False)])
        self.assertEqual(sources.lire_builds("ERREUR : Jenkins"), [])

    def test_patchs(self):
        patchs = sources.lire_patchs(PATCHS)
        self.assertEqual([(p["cle"], p["statut"]) for p in patchs],
                         [("ODA-2", "jointe"), ("ODA-4", "non_jointe"), ("ODA-5", "non_verifiee")])
        self.assertEqual(patchs[0]["fichiers"], ["ODA-2-standard.patch", "ODA-2-specifique.patch"])
        self.assertEqual(patchs[0]["livre_le"], "2026-11-19 16:00")
        self.assertEqual(patchs[1]["manquants"], ["ODA-4-specifique.patch"])
        self.assertEqual(patchs[2]["raison"], "Jira illisible (ConnectionError)")
        self.assertEqual(sources.lire_patchs("Aucun patch livré ces 7 derniers jours dans l'agent Scrum Master."),
                         [])

    def test_token_usage(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(sources.lire_token_usage(Path(d)))
            (Path(d) / ".tmp").mkdir()
            (Path(d) / ".tmp" / "token_usage.csv").write_text(
                "date;ticket;etape;modele;cout_usd;tours\n2026-11-01T10:00:00;ODA-1;cadrage;m;0,50;3\n"
                "2026-11-19T10:00:00;oda-1;patch;m;1.25;5\n2026-11-19T11:00:00;ODA-2;cadrage;m;;2\n",
                encoding="utf-8")
            lignes = sources.lire_token_usage(Path(d), depuis="2026-11-10")
            self.assertEqual([(l["ticket"], l["etape"], l["cout_usd"]) for l in lignes],
                             [("ODA-1", "patch", 1.25), ("ODA-2", "cadrage", 0.0)])
            self.assertEqual(sources.couts_par_etape(sources.lire_token_usage(Path(d)))[0],
                             {"etape": "patch", "appels": 1, "cout_usd": 1.25})
            r = reg.lire({"agents": [{"nom": "scrum-master", "dossier": d, "port": 8501}]})
            self.assertEqual(sources.cout_ticket_agent(r, "ODA-1"), 1.75)
            self.assertIsNone(sources.cout_ticket_agent(reg.lire({}), "ODA-1"))


class TestAppelMCP(unittest.TestCase):
    """Vrai client MCP contre un faux serveur scrum-master (aucun appel au modèle)."""

    def setUp(self):
        temporaire = tempfile.TemporaryDirectory()
        self.addCleanup(temporaire.cleanup)
        self.dossier = Path(temporaire.name)
        self.config = self.dossier / "config"

    def agent(self, *args):
        a = registre(self.dossier).agent("scrum-master")
        return reg.Agent(nom=a.nom, titre=a.titre, dossier=a.dossier, port=a.port, mcp=a.mcp + args)

    def test_appel_direct(self):
        r = sources.appeler_mcp(self.agent(), [("analyses", {}), ("builds_jenkins", {"nombre": 3}),
                                               ("patchs", {"jours": 7})], self.config, delai=20)
        self.assertEqual(r.erreurs, {})
        self.assertEqual([a["cle"] for a in sources.lire_analyses(r.textes["analyses"])], ["ODA-1", "ODA-2"])
        self.assertEqual([p["statut"] for p in sources.lire_patchs(r.textes["patchs"])], ["jointe"])

    def test_outil_en_erreur(self):
        r = sources.appeler_mcp(self.agent("erreur"), [("cucumber", {}), ("analyses", {})], self.config, delai=20)
        self.assertIn("cucumber", r.erreurs)
        self.assertIn("analyses", r.textes)

    def test_scrum_master_sans_outil_patchs(self):
        r = sources.appeler_mcp(self.agent("ancien"), [("analyses", {}), ("patchs", {"jours": 7})], self.config,
                                delai=20)
        self.assertIn("analyses", r.textes)
        self.assertIn("unknown tool", r.erreurs["patchs"].lower())      # message reconnu par tableau.construire

    def test_outils_couteux_refuses(self):
        with self.assertRaises(sources.SourceErreur):
            sources.appeler_mcp(self.agent(), [("question", {"texte": "x"})], self.config)

    def test_serveur_absent(self):
        absent = reg.Agent(nom="scrum-master", titre="Scrum Master", dossier=self.dossier, port=8501,
                           mcp=(str(self.dossier / "venv" / "python.exe"), "mcp_lecture.py"))
        with self.assertRaises(sources.SourceErreur):
            sources.appeler_mcp(absent, [("analyses", {})], self.config)
        plante = reg.Agent(nom="scrum-master", titre="Scrum Master", dossier=self.dossier, port=8501,
                           mcp=(sys.executable, "-c", "import sys; sys.exit(3)"))
        with self.assertRaises(sources.SourceErreur):
            sources.appeler_mcp(plante, [("analyses", {})], self.config, delai=10)


class TestTableau(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory()
        self.addCleanup(temporaire.cleanup)
        self.dossier = Path(temporaire.name)
        self.journal = jr.Journal(self.dossier / "manager.db", delai=0.2)
        self.registre = registre(self.dossier)
        self.appels = []

    def mcp_ok(self, agent, appels):
        self.appels.append((agent.nom, [o for o, _ in appels]))
        return sources.ResultatMCP(textes={"analyses": ANALYSES, "cucumber": CUCUMBER, "builds_jenkins": BUILDS,
                                           "patchs": PATCHS})

    @staticmethod
    def mcp_ko(agent, appels):
        raise sources.SourceErreur("serveur MCP de Scrum Master injoignable (agent arrêté)")

    @staticmethod
    def csv(dossier, depuis):
        return [{"date": "2026-11-19T10:00:00", "ticket": "ODA-1", "etape": "cadrage", "cout_usd": 0.4},
                {"date": "2026-11-01T10:00:00", "ticket": "ODA-2", "etape": "patch", "cout_usd": 1.0}]

    def construire(self, **options):
        valeurs = {"pilote": FauxPilote(), "appeler_mcp": self.mcp_ok, "lire_csv": self.csv,
                   "maintenant": MAINTENANT, **options}
        return tableau.construire(self.registre, self.journal, **valeurs)

    def test_tous_les_blocs(self):
        self.journal.noter_question("Qu'est-ce qu'on a fait sur ODA-9 ?", decision="repondre", cout_usd=0.3)
        t = self.construire()
        self.assertEqual(self.appels, [("scrum-master", ["analyses", "cucumber", "builds_jenkins", "patchs"])])
        self.assertEqual(t.agents.resume, {sup.EN_LIGNE: 1, sup.ECHEC: 1})
        self.assertEqual([a["cle"] for a in t.analyses.lignes], ["ODA-1", "ODA-3"])
        self.assertIn("localhost:8506", t.analyses.lignes[0]["lien"])   # onglet Lancement : agent-lancement
        self.assertIn("ticket=ODA-1", t.analyses.lignes[0]["lien"])
        self.assertIn("ODA-9", [x["cle"] for x in t.tickets.lignes])
        self.assertTrue(t.jenkins.resume["verrou"].startswith("occupé : CUCUMBER-RUN #12"))
        self.assertEqual(t.couts.resume["manager_7j"]["cout_usd"], 0.3)
        self.assertEqual((t.couts.resume["scrum_7j"], t.couts.resume["scrum_30j"]), (0.4, 1.4))
        self.assertEqual([p["cle"] for p in t.patchs.lignes], ["ODA-2", "ODA-4", "ODA-5"])
        self.assertEqual(t.patchs.resume, {"jointe": 1, "non_jointe": 1, "non_verifiee": 1})
        self.assertIn("ticket=ODA-4", t.patchs.lignes[1]["lien"])
        alertes = " | ".join(a["texte"] for a in t.alertes.lignes)
        self.assertIn("ODA-4 : patch livré le 2026-11-18 11:05 NON joint dans Jira (ODA-4-specifique.patch)", alertes)
        self.assertNotIn("ODA-5", alertes)                                  # non vérifié : dans le bloc seulement
        self.assertIn("Odatio : en échec", alertes)
        self.assertIn("client-x en cours depuis plus de", alertes)       # génération bloquée (8 h)
        self.assertEqual(self.journal.ticket("ODA-1")["analyse"]["a_valider"], True)   # index alimenté

    def test_builds_lus_et_affiches(self):
        appels = []

        def mcp(agent, liste):
            appels.extend(liste)
            return sources.ResultatMCP(textes={"builds_jenkins": "CUCUMBER-RUN :\n" + "\n".join(
                f"  #{n} SUCCESS — 2026-11-{n:02d} 10:00, 3 min" for n in range(15, 10, -1))})
        t = self.construire(appeler_mcp=mcp)
        self.assertIn(("builds_jenkins", {"nombre": tableau.BUILDS}), appels)
        self.assertEqual(len(t.jenkins.resume["builds"]), 5)                             # point du matin
        self.assertEqual([l["numero"] for l in t.jenkins.lignes if l["type"] == "build"], [15, 14, 13])

    def test_verrou_libre(self):
        def mcp(agent, appels):
            return sources.ResultatMCP(textes={"analyses": "", "cucumber": "- client-x : aucune génération en cours",
                                               "builds_jenkins": "CUCUMBER-RUN :\n  #11 SUCCESS — 2026-11-19, 3 min"})
        self.assertEqual(self.construire(appeler_mcp=mcp).jenkins.resume["verrou"], "libre")

    def test_agent_arrete_mcp_en_erreur(self):
        self.journal.observer_analyses(sources.lire_analyses(ANALYSES))
        t = self.construire(appeler_mcp=self.mcp_ko)
        self.assertIn("injoignable", t.analyses.erreur)
        self.assertIn("injoignable", t.jenkins.erreur)
        self.assertIn("dernier relevé", t.tickets.note)
        self.assertIn("ODA-1", [x["cle"] for x in t.tickets.lignes])          # dernier état connu
        self.assertEqual(len(t.agents.lignes), 2)                             # le reste s'affiche
        self.assertIn("scrum_30j", t.couts.resume)
        self.assertTrue(any("non consultable" in a["texte"] for a in t.alertes.lignes))

    def test_patchs_outil_absent_ou_en_erreur(self):
        def mcp(erreur):
            return lambda agent, appels: sources.ResultatMCP(
                textes={"analyses": ANALYSES, "cucumber": CUCUMBER, "builds_jenkins": BUILDS},
                erreurs={"patchs": erreur})
        t = self.construire(appeler_mcp=mcp("Unknown tool: patchs"))         # scrum-master sans agent-scrum-master#49
        self.assertIn("pas encore l'outil patchs", t.patchs.note)
        self.assertFalse(t.patchs.erreur)
        self.assertFalse(any("non consultable" in a["texte"] for a in t.alertes.lignes))
        self.assertEqual(len(t.analyses.lignes), 2)
        t = self.construire(appeler_mcp=mcp("ERREUR : registre des livraisons illisible"))
        self.assertIn("registre des livraisons illisible", t.patchs.erreur)
        self.assertFalse(any("non consultable" in a["texte"] for a in t.alertes.lignes))
        t = self.construire(appeler_mcp=self.mcp_ko)
        self.assertIn("injoignable", t.patchs.erreur)
        t = self.construire(appeler_mcp=lambda agent, appels: sources.ResultatMCP(textes={
            "patchs": "Aucun patch livré ces 7 derniers jours dans l'agent Scrum Master."}))
        self.assertEqual((t.patchs.lignes, t.patchs.erreur), ([], ""))

    def test_un_outil_en_erreur(self):
        def mcp(agent, appels):
            return sources.ResultatMCP(textes={"analyses": ANALYSES}, erreurs={"cucumber": "ERREUR : x",
                                                                              "builds_jenkins": "délai"})
        t = self.construire(appeler_mcp=mcp)
        self.assertEqual(len(t.analyses.lignes), 2)
        self.assertTrue(t.jenkins.erreur)

    def test_superviseur_et_journal_indisponibles(self):
        (self.dossier / "illisible.db").write_bytes(b"pas une base" * 100)
        journal = jr.Journal(self.dossier / "illisible.db", delai=0.2)
        t = tableau.construire(self.registre, journal, FauxPilote(erreur=True), appeler_mcp=self.mcp_ok,
                               lire_csv=self.csv, maintenant=MAINTENANT)
        self.assertIn("Superviseur injoignable", t.agents.erreur)
        self.assertIn("journal illisible", t.tickets.erreur)
        self.assertEqual([x["cle"] for x in t.tickets.lignes], ["ODA-1", "ODA-2", "ODA-3"])   # repli : analyses
        self.assertTrue(t.couts.erreur)
        self.assertEqual(len(t.analyses.lignes), 2)

    def test_sans_scrum_master_ni_csv(self):
        r = reg.lire({"agents": [{"nom": "odatio", "dossier": str(self.dossier), "port": 8502}]})

        t = tableau.construire(r, self.journal, FauxPilote(etats=[]), appeler_mcp=self.mcp_ok,
                               maintenant=MAINTENANT)
        self.assertEqual(self.appels, [])
        self.assertIn("absent du registre", t.analyses.erreur)
        self.assertNotIn("scrum_30j", t.couts.resume)

    def test_couts_agent_inconnus_ou_illisibles(self):
        t = self.construire(lire_csv=lambda dossier, depuis: None)
        self.assertNotIn("scrum_30j", t.couts.resume)                       # inconnu, pas 0 $
        self.assertIn("absent", t.couts.note)

        def csv_ko(dossier, depuis):
            raise sources.SourceErreur("token_usage.csv illisible")
        t = self.construire(lire_csv=csv_ko)
        self.assertIn("illisibles", t.couts.note)
        self.assertIn("manager_7j", t.couts.resume)

    def test_alertes_du_journal(self):
        self.journal.noter_erreur("assistant", "RuntimeError : CLI absente")
        self.journal.noter_etat("scrum-master", "en ligne", sup.INJOIGNABLE)
        t = self.construire(maintenant=datetime.now())
        textes = " | ".join(a["texte"] for a in t.alertes.lignes)
        self.assertIn("assistant : RuntimeError : CLI absente", textes)
        self.assertIn("scrum-master : en ligne → injoignable", textes)


if __name__ == "__main__":
    unittest.main()
