import unittest
from urllib.parse import parse_qs, urlsplit

from manager import liens


def params(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


class TestLiens(unittest.TestCase):
    def test_lien_prerempli(self):
        url = liens.construire("scrum-master", "http://localhost:8501", page="cucumber", ticket="oda-12",
                               projet="client-x-recette", contexte="Lancer les tests Cucumber du ticket.")
        # Onglet Cucumber d'agent-scrum-master : directement agent-cucumber (registre)
        self.assertTrue(url.startswith("http://localhost:8507/?source=manager&page=cucumber"))
        self.assertEqual(params(url), {"source": "manager", "page": "cucumber", "ticket": "ODA-12",
                                       "projet": "client-x-recette",
                                       "contexte": "Lancer les tests Cucumber du ticket."})

    def test_redirection_sans_agent_au_registre(self):
        """Ancien lien vers agent-scrum-master (supprimé), agent cible absent du registre : adresse inchangée,
        onglet retiré (scrum-master n'a plus d'onglets connus)."""
        url = liens.construire("scrum-master", "http://localhost:8501", urls={}, page="lancement", ticket="ODA-3")
        self.assertEqual(url, "http://localhost:8501/?source=manager&ticket=ODA-3")
        self.assertNotIn("scrum-master", liens.ONGLETS)
        self.assertEqual(liens.rediriger("scrum-master", "u", "revue", {"revue-pr": "http://localhost:8508"}),
                         ("revue-pr", "http://localhost:8508", ""))

    def test_valeurs_invalides_retirees(self):
        p = liens.parametres("odatio", page="cucumber", ticket="pas un ticket", projet="a/b?c=d",
                             conversation="../x", depot="x" * 200)
        self.assertEqual(p, {})
        self.assertEqual(liens.construire("odatio", "http://localhost:8502", page="inconnue"),
                         "http://localhost:8502")
        # Agent inconnu des onglets : pas de page, le reste passe
        self.assertEqual(liens.parametres("autre", page="assistant", ticket="AB-1"),
                         {"source": "manager", "ticket": "AB-1"})

    def test_textes_bornes(self):
        p = liens.parametres("dev-tool", page="developper", demande="x" * 5000, contexte="y" * 2000,
                             depot="agent-odatio")
        self.assertEqual(len(p["demande"]), liens.TEXTE_MAX)
        self.assertEqual(len(p["contexte"]), liens.CONTEXTE_MAX)
        self.assertTrue(p["demande"].endswith("…"))
        self.assertEqual((p["page"], p["depot"]), ("developper", "agent-odatio"))

    def test_caracteres_speciaux_encodes(self):
        url = liens.construire("odatio", "http://localhost:8502/", question="Pourquoi l'OP « A&B » est bloqué ? #2")
        self.assertNotIn(" ", url)
        self.assertEqual(params(url)["question"], "Pourquoi l'OP « A&B » est bloqué ? #2")


if __name__ == "__main__":
    unittest.main()
