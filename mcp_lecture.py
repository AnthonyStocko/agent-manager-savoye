"""
Serveur MCP (stdio) en LECTURE SEULE pour agent-manager-savoye : état des analyses, d'un ticket Jira, de Cucumber
et des derniers builds Jenkins, patchs livrés (tools/lecture_manager.py). Rien n'y écrit.

Lancé par le manager avec le python du venv de l'agent, dans le dossier de l'agent :
  .venv\\Scripts\\python.exe mcp_lecture.py

Ne pas le déclarer dans un .mcp.json à la racine : le CLI claude lancé par l'agent depuis cette racine le
chargerait à chaque appel de production.
"""
import contextlib
import os
import sys

from dotenv import load_dotenv
from agent_socle.chemins import DATA_DIR

RACINE = DATA_DIR  # dossier de travail et .env : AGENT_DATA_DIR (agent-scrum-master)
os.chdir(RACINE)
load_dotenv(os.path.join(RACINE, ".env"))   # sans override : les accès transmis par le manager l'emportent

from mcp.server.mcpserver import MCPServer  # noqa: E402

from manager import lecture_manager as lecture  # noqa: E402


def _appel(fonction, *args) -> str:
    # stdout est le canal du protocole MCP : tout affichage des outils de l'agent (ex. [JENKINS]) part sur stderr
    with contextlib.redirect_stdout(sys.stderr):
        try:
            return fonction(*args)
        except lecture.LectureErreur as e:
            return f"ERREUR : {e}"


def creer_serveur() -> MCPServer:
    serveur = MCPServer("scrum-master", instructions="Agent Scrum Master, en lecture seule : état des analyses de "
                                                     "tickets, de Cucumber, des builds Jenkins et des patchs livrés.")

    @serveur.tool(description="Analyses (cadrages) présentes dans l'agent, avec leur état : publiée dans Jira ou "
                              "brouillon à valider.")
    def analyses() -> str:
        return _appel(lecture.analyses)

    @serveur.tool(description="État de l'analyse d'un ticket (ex. ODA-1234) et titres de ses sections.")
    def analyse(ticket: str) -> str:
        return _appel(lecture.analyse, ticket)

    @serveur.tool(description="Ticket Jira (lecture) : titre, type, statut, responsable, épopée, et état de son "
                              "analyse dans l'agent.")
    def ticket(ticket: str) -> str:
        return _appel(lecture.ticket, ticket)

    @serveur.tool(description="Clients Cucumber de l'agent, génération en cours par client et jobs Jenkins.")
    def cucumber() -> str:
        return _appel(lecture.cucumber)

    @serveur.tool(description="Derniers builds des jobs Cucumber sur Jenkins (un seul à la fois) : numéro, résultat "
                              "(EN COURS si le build tourne), début, durée. 'job' vide : tous les jobs configurés.")
    def builds_jenkins(job: str = "", nombre: int = 5) -> str:
        return _appel(lecture.builds, job, nombre)

    @serveur.tool(description="Patchs livrés ces derniers jours (registre local de l'étape patch) : ticket, date, "
                              "fichiers et présence en pièce jointe Jira. 'jours' : de 1 à 90, 7 par défaut.")
    def patchs(jours: int = 7) -> str:
        return _appel(lecture.patchs, jours)

    @serveur.tool(description="Boucles de correction autonome d'agent-cucumber (agent-dev-tool-savoye corrige l'agent "
                              "et fusionne sans validation) : état, itération, échecs par catégorie, corrections et "
                              "retours arrière, coût. Information seulement.")
    def boucles_cucumber(nombre: int = 5) -> str:
        return _appel(lecture.boucles, lecture.WORKSPACE_DIR, nombre)

    @serveur.tool(description="Rétrospectives d'agent-retrospective (patch d'agent-lancement comparé au code fusionné) :"
                              " ticket, date, score, écarts expliqués, et propositions d'amélioration d'agent-lancement"
                              " en attente de validation. 'nombre' : de 1 à 50, 10 par défaut. Lecture seule.")
    def retrospectives(nombre: int = 10) -> str:
        return _appel(lecture.retrospectives, lecture.WORKSPACE_DIR, nombre)

    return serveur


if __name__ == "__main__":
    print("Serveur MCP en lecture seule de l'agent Scrum Master (stdio).", file=sys.stderr)
    creer_serveur().run("stdio")
