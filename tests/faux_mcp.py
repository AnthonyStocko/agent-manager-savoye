"""Faux serveur MCP (stdio) de scrum-master pour les tests : mêmes outils, textes au format de
tools/lecture_manager.py, données fictives. Mode « erreur » en argument : l'outil cucumber renvoie une erreur ;
mode « ancien » : pas d'outil patchs (scrum-master antérieur au registre des livraisons)."""

import sys

try:
    from mcp.server.mcpserver import MCPServer as Serveur      # mcp 2.x
except ImportError:  # pragma: no cover — mcp 1.x
    from mcp.server.fastmcp import FastMCP as Serveur

ERREUR = "erreur" in sys.argv[1:]
ANCIEN = "ancien" in sys.argv[1:]
serveur = Serveur("scrum-master")


@serveur.tool(description="analyses")
def analyses() -> str:
    return ("Analyses de l'agent Scrum Master :\n"
            "- ODA-1 (modifiée le 2026-11-19 10:00) : Analyse non publiée (brouillon) ← à valider\n"
            "- ODA-2 (modifiée le 2026-11-18 09:30) : Publiée dans Jira le 2026-11-18 à 09:45")


@serveur.tool(description="cucumber")
def cucumber() -> str:
    if ERREUR:
        return "ERREUR : Cucumber illisible"
    return ("Cucumber dans l'agent Scrum Master :\n- client-x : aucune génération en cours\n"
            "Aucun job Jenkins configuré.")


@serveur.tool(description="builds")
def builds_jenkins(job: str = "", nombre: int = 5) -> str:
    return "Aucun job Jenkins configuré dans l'agent Scrum Master."


def patchs(jours: int = 7) -> str:
    return (f"Patchs livrés ces {jours} derniers jours (agent Scrum Master) :\n"
            "- ODA-2 (livré le 2026-11-19 16:00) : ODA-2-standard.patch — en pièce jointe Jira")


@serveur.tool(description="boucles de correction Cucumber")
def boucles_cucumber(nombre: int = 5) -> str:
    return ("Boucles de correction Cucumber :\n"
            "- client-x-4-20261120-080000 [client-x] : abandonnée · itération 3/3 · échecs 5 → 2 · AGENT 1, "
            "APPLICATION 1, ENVIRONNEMENT 0, INCERTAIN 0 · coût 3.40 $ · relance oui · mis à jour 2026-11-20T08:50:00\n"
            "  raison : limite de 3 itération(s) atteinte\n"
            "  correction it1 abc1234567 : prompt de lot trop vague sur les steps existants\n"
            "  demande : Boucle de correction client-x-4 : corriger l'agent cucumber / - [AGENT] Créer — Then x : m")


@serveur.tool(description="question coûteuse : ne doit jamais être appelée par le tableau de bord")
def question(texte: str = "") -> str:
    return "interdit"


if not ANCIEN:
    serveur.tool(description="patchs")(patchs)


if __name__ == "__main__":
    serveur.run("stdio")
