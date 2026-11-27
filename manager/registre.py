"""Registre des agents : fichier ``agents.toml`` (non versionné), modèle ``agents.example.toml``.

Le registre dit au superviseur quoi lancer (dossier, port, commande, mode) et, plus tard, dira au routeur qui sait
faire quoi (``domaine``) et quels secrets transmettre à chaque agent (``secrets``).
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
FICHIER = RACINE / "agents.toml"
EXEMPLE = RACINE / "agents.example.toml"

PORT_MANAGER = 8500
PORT_SUPERVISEUR = 8499
MODES = ("auto", "demande")      # auto : lancé avec le manager ; demande : lancé depuis l'onglet Agents
NOM_VALIDE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Commande par défaut d'un agent Streamlit ; « {python} » = python du venv de l'agent, « {port} » = son port.
COMMANDE_STREAMLIT = ("{python}", "-m", "streamlit", "run", "app.py",
                      "--server.port", "{port}", "--server.address", "127.0.0.1", "--server.headless", "true")


class RegistreErreur(Exception):
    pass


@dataclass(frozen=True)
class Agent:
    nom: str
    titre: str
    dossier: Path
    port: int
    mode: str = "auto"
    commande: tuple[str, ...] = COMMANDE_STREAMLIT
    domaine: str = ""                       # ce que l'agent sait faire (lu par le routeur, lot 4)
    mcp: tuple[str, ...] = ()               # commande de son serveur MCP (lot 4)
    secrets: tuple[str, ...] = ()           # variables transmises au lancement (lot 2)

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    @property
    def python(self) -> Path:
        """Python du venv de l'agent : venv/ ou .venv/, Windows sinon Linux / macOS."""
        candidats = [self.dossier / v / "Scripts" / "python.exe" for v in ("venv", ".venv")] + \
                    [self.dossier / v / "bin" / "python" for v in ("venv", ".venv")]
        existant = next((c for c in candidats if c.exists()), None)
        return existant or candidats[0 if sys.platform == "win32" else 2]

    def _formater(self, parties) -> list[str]:
        valeurs = {"python": str(self.python), "port": str(self.port), "dossier": str(self.dossier)}
        return [partie.format(**valeurs) for partie in parties]

    def ligne_de_commande(self) -> list[str]:
        return self._formater(self.commande)

    def ligne_mcp(self) -> list[str]:
        """Commande de son serveur MCP en lecture seule ([] s'il n'en a pas)."""
        return self._formater(self.mcp)


@dataclass
class Registre:
    agents: list[Agent] = field(default_factory=list)
    port_manager: int = PORT_MANAGER
    port_superviseur: int = PORT_SUPERVISEUR

    def agent(self, nom: str) -> Agent:
        for agent in self.agents:
            if agent.nom == nom:
                return agent
        raise RegistreErreur(f"agent inconnu : {nom}")


# Lecteur des données des agents : serveur MCP en lecture seule du manager (mcp_lecture.py), lancé avec le venv du
# manager dans AGENT_DATA_DIR. Ce n'est pas un agent du registre (ni IHM, ni lancement par le superviseur) : il
# remplace le serveur MCP qu'agent-scrum-master (supprimé) portait pour le manager.
NOM_LECTEUR = "donnees"
DOMAINE_LECTEUR = """Données des agents, en lecture seule (AGENT_DATA_DIR) : état des analyses de tickets
(agent-lancement), ticket Jira, clients Cucumber et builds Jenkins (agent-cucumber), patchs livrés, boucles de
correction Cucumber, rétrospectives (agent-retrospective)."""
SECRETS_LECTEUR = ("JIRA_API_TOKEN", "JIRA_READ_API_TOKEN", "JENKINS_API_TOKEN")


def dossier_donnees() -> Path:
    """AGENT_DATA_DIR : .env, .tmp et fichiers produits partagés par les agents (agent_socle.chemins)."""
    from agent_socle.chemins import DATA_DIR
    return Path(DATA_DIR)


def lecteur(dossier: Path | None = None) -> Agent:
    """Source « donnees » : lecteur MCP des données des agents (port 0 : aucune IHM). ``dossier`` : tests."""
    venv = [RACINE / "venv" / "Scripts" / "python.exe", RACINE / "venv" / "bin" / "python"]
    python = next((p for p in venv if p.exists()), Path(sys.executable))
    return Agent(nom=NOM_LECTEUR, titre="Données des agents", dossier=Path(dossier or dossier_donnees()), port=0,
                 mode="demande", commande=(), domaine=" ".join(DOMAINE_LECTEUR.split()),
                 mcp=(str(python), str(RACINE / "mcp_lecture.py")), secrets=SECRETS_LECTEUR)


def charger(fichier: Path = FICHIER) -> Registre:
    """Lit le registre ; sans ``agents.toml``, lit le modèle ``agents.example.toml``."""
    if not fichier.exists() and fichier == FICHIER:
        fichier = EXEMPLE
    try:
        donnees = tomllib.loads(fichier.read_text(encoding="utf-8"))
    except OSError as e:
        raise RegistreErreur(f"{fichier.name} introuvable : {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise RegistreErreur(f"{fichier.name} illisible : {e}") from e
    return lire(donnees, base=fichier.parent)


def lire(donnees: dict, base: Path = RACINE) -> Registre:
    """Construit et contrôle le registre. Les dossiers relatifs le sont au dossier du fichier."""
    manager = donnees.get("manager", {})
    registre = Registre(port_manager=_port(manager.get("port", PORT_MANAGER), "manager"),
                        port_superviseur=_port(manager.get("port_superviseur", PORT_SUPERVISEUR), "superviseur"))
    erreurs: list[str] = []
    for i, brut in enumerate(donnees.get("agents", []), start=1):
        try:
            registre.agents.append(_agent(brut, base))
        except RegistreErreur as e:
            erreurs.append(f"agent n°{i} : {e}")

    noms: dict[str, int] = {}
    ports = {registre.port_manager: "manager", registre.port_superviseur: "superviseur"}
    if registre.port_manager == registre.port_superviseur:
        erreurs.append(f"manager et superviseur sur le même port {registre.port_manager}")
    for agent in registre.agents:
        noms[agent.nom] = noms.get(agent.nom, 0) + 1
        if agent.port in ports:
            erreurs.append(f"port {agent.port} de « {agent.nom} » déjà pris par « {ports[agent.port]} »")
        else:
            ports[agent.port] = agent.nom
    erreurs += [f"nom « {nom} » en double" for nom, n in noms.items() if n > 1]
    if erreurs:
        raise RegistreErreur("registre invalide :\n- " + "\n- ".join(erreurs))
    return registre


def _agent(brut: dict, base: Path) -> Agent:
    nom = str(brut.get("nom", "")).strip()
    if not NOM_VALIDE.match(nom):
        raise RegistreErreur(f"nom invalide « {nom} » (minuscules, chiffres et tirets)")
    if not brut.get("dossier"):
        raise RegistreErreur(f"« {nom} » : dossier manquant")
    mode = brut.get("mode", "auto")
    if mode not in MODES:
        raise RegistreErreur(f"« {nom} » : mode « {mode} » inconnu ({' ou '.join(MODES)})")
    commande = brut.get("commande", COMMANDE_STREAMLIT)
    if not isinstance(commande, list | tuple) or not commande:
        raise RegistreErreur(f"« {nom} » : commande doit être une liste non vide")
    dossier = Path(brut["dossier"])
    return Agent(nom=nom, titre=str(brut.get("titre", nom)),
                 dossier=(dossier if dossier.is_absolute() else (base / dossier)).resolve(),
                 port=_port(brut.get("port"), nom), mode=mode, commande=tuple(str(c) for c in commande),
                 domaine=str(brut.get("domaine", "")).strip(), mcp=tuple(str(c) for c in brut.get("mcp", [])),
                 secrets=tuple(str(s) for s in brut.get("secrets", [])))


def _port(valeur, qui: str) -> int:
    if isinstance(valeur, bool) or not isinstance(valeur, int) or not 1024 <= valeur <= 65535:
        raise RegistreErreur(f"« {qui} » : port invalide {valeur!r} (entier entre 1024 et 65535)")
    return valeur
