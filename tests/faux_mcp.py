"""Faux serveur MCP (stdio) de scrum-master pour les tests : mêmes outils, textes au format de
tools/lecture_manager.py, données fictives. Mode « erreur » en argument : l'outil cucumber renvoie une erreur."""

import sys

try:
    from mcp.server.mcpserver import MCPServer as Serveur      # mcp 2.x
except ImportError:  # pragma: no cover — mcp 1.x
    from mcp.server.fastmcp import FastMCP as Serveur

ERREUR = "erreur" in sys.argv[1:]
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


@serveur.tool(description="question coûteuse : ne doit jamais être appelée par le tableau de bord")
def question(texte: str = "") -> str:
    return "interdit"


if __name__ == "__main__":
    serveur.run("stdio")
