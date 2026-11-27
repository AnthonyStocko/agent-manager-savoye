"""Assistant du manager : une session Claude Agent SDK qui applique la règle de décision (consignes.py).

Outils, tous en LECTURE SEULE :
- outils internes (serveur MCP dans le processus, « manager ») : etat_agents, applications, historique_ticket
  (journal du manager, lot 6 : sans réinterroger les agents), rediriger, et doc_sommaire, doc_lire, doc_chercher
  (documentation Odatio lue dans Bitbucket, doc_odatio.py) quand ODATIO_DOC_DEPOT est configuré ;
- serveurs MCP des agents, déclarés dans le registre (champ ``mcp``) et lancés avec leur venv et la configuration
  commune qui leur revient (``configuration.variables_pour``, moindre privilège) ;
- source « donnees » du manager (``registre.lecteur()``, mcp_lecture.py) : données des agents en lecture seule
  (analyses, Cucumber, builds, patchs, rétrospectives). Consultable, mais pas une cible de « rediriger ».
Aucun outil intégré de Claude Code (ni Bash, ni Read, ni Edit) ; réglages et CLAUDE.md du poste ignorés.

« rediriger » n'agit pas : il prépare un lien vers l'agent, affiché à l'utilisateur sous la réponse, prérempli
(onglet, ticket, projet, question ou demande : liens.py) ; l'agent ne fait rien sans l'utilisateur.
"""

from __future__ import annotations

import asyncio
import dataclasses
import getpass
from dataclasses import dataclass, field
from pathlib import Path

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, ResultMessage,
                              ToolUseBlock, create_sdk_mcp_server, tool)

from manager import configuration as conf
from manager import consignes
from manager import doc_odatio
from manager import journal as jr
from manager import liens
from manager import registre as reg
from manager import sources
from manager.pilote import Pilote, PiloteErreur

SERVEUR = "manager"
DELAI_OUTIL_MS = 15 * 60 * 1000        # une question à l'agent Odatio peut prendre plusieurs minutes
TOURS_MAX = 20
DOSSIER_TRAVAIL = reg.RACINE / ".tmp" / "assistant"


@dataclass
class Redirection:
    agent: str
    titre: str
    url: str
    resume: str
    ticket: str = ""
    projet: str = ""
    demande: str = ""      # demande prérédigée (ex. pour dev-tool)
    onglet: str = ""
    question: str = ""     # question préremplie dans l'agent (jamais envoyée sans l'utilisateur)
    depot: str = ""        # dépôt cible de dev-tool
    conversation: str = ""  # conversation de l'agent Odatio à reprendre


@dataclass
class Reponse:
    texte: str = ""
    redirections: list = field(default_factory=list)
    outils: list = field(default_factory=list)          # [(nom, entrée)] dans l'ordre d'appel
    cout_usd: float | None = None
    tours: int = 0
    erreur: bool = False
    message: str = ""
    session_id: str = ""


def _texte(texte: str, erreur: bool = False) -> dict:
    return {"content": [{"type": "text", "text": texte}], "is_error": erreur}


class Routeur:
    def __init__(self, registre: reg.Registre | None = None, dossier_config: Path = conf.DOSSIER,
                 utilisateur: str = "", simulation: bool = False, pilote: Pilote | None = None,
                 journal: jr.Journal | None = None, lecteur: reg.Agent | None = None):
        """simulation : les appels aux agents sont refusés et seulement notés (évaluation de la règle de décision
        sans faire travailler les agents). lecteur : source des données des agents (``registre.lecteur()`` par
        défaut ; tests)."""
        self.registre = registre or reg.charger()
        self.dossier_config = dossier_config
        self.commun = conf.charger(dossier_config)
        self.utilisateur = utilisateur or getpass.getuser()
        self.simulation = simulation
        self.pilote = pilote or Pilote()
        self.journal = journal or jr.Journal()
        self.redirections: list = []
        self.simules: list = []
        # agents réellement consultables : serveur MCP déclaré et exécutable présent
        self.agents = [a if self._mcp_disponible(a) else dataclasses.replace(a, mcp=()) for a in self.registre.agents]
        # sources du manager consultables (lecteur des données), hors registre : jamais cibles de « rediriger »
        if lecteur is None:
            try:
                lecteur = reg.lecteur()
            except Exception:  # noqa: BLE001 — agent-socle absent : pas de lecteur, le reste fonctionne
                lecteur = None
        self.lecteur = lecteur
        self.sources = [lecteur] if lecteur is not None and self._mcp_disponible(lecteur) else []

    @staticmethod
    def _mcp_disponible(agent: reg.Agent) -> bool:
        commande = agent.ligne_mcp()
        return bool(commande) and (not Path(commande[0]).is_absolute() or Path(commande[0]).exists())

    # --- outils internes ----------------------------------------------------------------------------------------
    def outils_internes(self) -> list:
        noms = [a.nom for a in self.agents]

        @tool("etat_agents", "État des agents de la suite (en ligne, arrêté, en échec…) et leur adresse.",
              {"type": "object", "properties": {}})
        async def etat_agents(args):
            try:
                etats = self.pilote.etat()
            except PiloteErreur as e:
                return _texte(f"Superviseur injoignable : {e}", True)
            return _texte("\n".join(f"- {e['nom']} ({e['titre']}) : {e['etat']}, {e['url']}"
                                    + (f" — {e['message']}" if e.get("message") else "") for e in etats))

        @tool("applications", "Applications Odatio de la configuration commune : sources, base, logs, application "
              "web, projet Jira, job Jenkins (sans secret).", {"type": "object", "properties": {}})
        async def applications(args):
            lignes = []
            for a in self.commun.applications:
                lignes.append(f"- {a.nom} : {a.description or 'sans description'}"
                              f"\n  sources : {', '.join(s.nom or s.depot or s.chemin for s in a.sources) or 'aucune'}"
                              f" · base : {a.base.get('type') or 'aucune'}"
                              f" · logs : {'SSH ' if a.logs.get('ssh') else ''}{'groundcover' if a.logs.get('groundcover_filtre') else ''}"
                              f"{'fichiers' if a.logs.get('dossiers') else ''}"
                              f" · Jira : {a.jira_projet or '—'} · Jenkins : {a.jenkins_job or '—'}")
            return _texte("\n".join(lignes) or "Aucune application dans la configuration commune.")

        @tool("historique_ticket", "Ce que le manager sait déjà d'un ticket Jira (ex. ODA-1234), sans interroger les "
              "agents : questions posées au manager et redirections (journal), dernier état connu de son analyse "
              "dans agent-lancement (relevé par le tableau de bord, avec sa date) et coût cumulé connu.",
              {"type": "object", "properties": {"ticket": {"type": "string"}}, "required": ["ticket"]})
        async def historique_ticket(args):
            return _texte(*historique(self.journal, self.registre, str(args.get("ticket") or ""),
                                      dossier=self.lecteur.dossier if self.lecteur else None))

        onglets = "; ".join(f"{nom} : {', '.join(o)}" for nom, o in liens.ONGLETS.items() if nom in noms)
        @tool("rediriger", "Redirige l'utilisateur vers un agent pour une demande qu'il doit y faire lui-même "
              f"(validation, écriture, travail long, ressource partagée, amélioration d'un agent). Le lien ouvre "
              f"l'agent prérempli ; il n'y lance rien. agent : un de {', '.join(noms)}. resume : ce qui est demandé "
              "et ce que tu sais déjà (2 à 5 phrases). onglet : page de l'agent à ouvrir "
              f"({onglets}). ticket : clé Jira si connue. projet : application si connue. question : question "
              "prête à envoyer dans l'assistant de l'agent (odatio). conversation : identifiant de la "
              "conversation enregistrée par l'agent Odatio, pour la reprendre. demande : texte prêt pour dev-tool "
              "(obligatoire pour dev-tool), et depot : agent à faire évoluer.",
              {"type": "object", "properties": {"agent": {"type": "string", "enum": noms},
                                                "resume": {"type": "string"}, "onglet": {"type": "string"},
                                                "ticket": {"type": "string"}, "projet": {"type": "string"},
                                                "question": {"type": "string"}, "conversation": {"type": "string"},
                                                "demande": {"type": "string"}, "depot": {"type": "string"}},
               "required": ["agent", "resume"]})
        async def rediriger(args):
            try:
                agent = self.registre.agent(args.get("agent", ""))
            except reg.RegistreErreur as e:
                return _texte(str(e), True)
            valeurs = {k: str(args.get(k) or "").strip() for k in ("onglet", "ticket", "projet", "question",
                                                                    "conversation", "demande", "depot")}
            resume = str(args.get("resume") or "").strip()
            url = liens.construire(agent.nom, agent.url, page=valeurs["onglet"], ticket=valeurs["ticket"],
                                   projet=valeurs["projet"], conversation=valeurs["conversation"],
                                   question=valeurs["question"], demande=valeurs["demande"], depot=valeurs["depot"],
                                   contexte=resume)
            r = Redirection(agent=agent.nom, titre=agent.titre, url=url, resume=resume,
                            ticket=valeurs["ticket"].upper(), projet=valeurs["projet"], demande=valeurs["demande"],
                            onglet=valeurs["onglet"], question=valeurs["question"], depot=valeurs["depot"],
                            conversation=valeurs["conversation"])
            self.redirections.append(r)
            demarrage = " Il est lancé à la demande : l'utilisateur le démarre depuis l'onglet Agents." \
                if agent.mode == "demande" else ""
            prerempli = [k for k in ("page", "ticket", "projet", "conversation", "question", "demande", "depot")
                         if k in liens.parametres(agent.nom, page=valeurs["onglet"], ticket=valeurs["ticket"],
                                                  projet=valeurs["projet"], conversation=valeurs["conversation"],
                                                  question=valeurs["question"], demande=valeurs["demande"],
                                                  depot=valeurs["depot"])]
            ignore = " Onglet inconnu de cet agent : ignoré." if valeurs["onglet"] and "page" not in prerempli else ""
            return _texte(f"Lien vers {agent.titre} affiché à l'utilisateur sous ta réponse, avec ton résumé"
                          + (f", prérempli ({', '.join(prerempli)})" if prerempli else "") + f".{ignore}{demarrage} "
                          "Termine ta réponse en une ou deux phrases.")

        return [etat_agents, applications, historique_ticket, rediriger] + self.outils_doc()

    def variables_doc(self) -> dict:
        """Accès de la documentation Odatio pour l'assistant : configuration commune et jeton de lecture Bitbucket."""
        jeton = conf.secrets(self.dossier_config).get("BITBUCKET_HTTP_TOKEN", "")
        return {**self.commun.acces, "BITBUCKET_HTTP_TOKEN": jeton}

    def outils_doc(self) -> list:
        """Documentation Odatio en lecture seule (aucun outil si elle n'est pas configurée)."""
        if not doc_odatio.configuree(self.commun.acces):
            return []
        etat = {}

        async def _appel(methode: str, *args) -> dict:
            try:
                if "doc" not in etat:
                    etat["doc"] = doc_odatio.Documentation(self.variables_doc())
                return _texte(await asyncio.to_thread(getattr(etat["doc"], methode), *args))
            except doc_odatio.DocErreur as e:
                return _texte(f"Documentation Odatio : {e}", True)

        @tool("doc_sommaire", "Sommaire de la documentation Odatio (produit Standard, lue dans ses sources sur "
              "Bitbucket) : sous-dossiers et fichiers d'un dossier ('' = racine) sur 'profondeur' niveaux (1 à 6).",
              {"type": "object", "properties": {"dossier": {"type": "string"}, "profondeur": {"type": "integer"}}})
        async def doc_sommaire(args):
            return await _appel("sommaire", str(args.get("dossier") or ""), args.get("profondeur") or 2)

        @tool("doc_lire", "Lit un fichier de la documentation Odatio, lignes numérotées : chemin donné par "
              "doc_sommaire ou doc_chercher, ou adresse d'une page du site publié. 'debut' et 'nombre' (400 par "
              "défaut, 800 au plus) pour paginer.",
              {"type": "object", "properties": {"chemin": {"type": "string"}, "debut": {"type": "integer"},
                                                "nombre": {"type": "integer"}}, "required": ["chemin"]})
        async def doc_lire(args):
            return await _appel("lire", str(args.get("chemin") or ""), args.get("debut") or 1,
                                args.get("nombre") or 400)

        @tool("doc_chercher", "Cherche un texte dans la documentation Odatio (plein texte si Bitbucket le permet, "
              "sinon dans les noms de fichiers) : fichiers et extraits.",
              {"type": "object", "properties": {"texte": {"type": "string"}, "nombre": {"type": "integer"}},
               "required": ["texte"]})
        async def doc_chercher(args):
            return await _appel("chercher", str(args.get("texte") or ""), args.get("nombre") or 15)

        return [doc_sommaire, doc_lire, doc_chercher]

    # --- options ------------------------------------------------------------------------------------------------
    def serveurs_agents(self) -> dict:
        serveurs = {}
        for a in self.agents + self.sources:
            if not a.mcp:
                continue
            commande = a.ligne_mcp()
            env = {**conf.variables_pour(a, self.dossier_config), "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
            serveurs[a.nom] = {"type": "stdio", "command": commande[0], "args": commande[1:], "env": env}
        return serveurs

    def options(self, reprise: str = "") -> ClaudeAgentOptions:
        internes = self.outils_internes()
        serveurs = {SERVEUR: create_sdk_mcp_server(SERVEUR, tools=internes), **self.serveurs_agents()}
        # tous les outils (en lecture seule) des serveurs des agents : règle par serveur et motif, selon la version
        autorises = [f"mcp__{SERVEUR}__{t.name}" for t in internes] + \
                    [r for nom in serveurs if nom != SERVEUR for r in (f"mcp__{nom}", f"mcp__{nom}__*")]
        hooks = {}
        if self.simulation:
            hooks = {"PreToolUse": [HookMatcher(matcher="|".join(f"mcp__{n}__.*" for n in serveurs if n != SERVEUR)
                                                or "^$", hooks=[self._simuler])]}
        DOSSIER_TRAVAIL.mkdir(parents=True, exist_ok=True)
        registre = dataclasses.replace(self.registre, agents=self.agents)
        return ClaudeAgentOptions(
            system_prompt=consignes.construire(registre, self.commun, self.utilisateur, sources=self.sources),
            tools=[],                                 # aucun outil intégré : ni Bash, ni Read, ni Edit, ni Write
            allowed_tools=autorises,                  # autorisés sans confirmation
            permission_mode="dontAsk",                # tout le reste est refusé
            mcp_servers=serveurs,
            strict_mcp_config=True,                   # ignore les serveurs MCP configurés sur le poste
            setting_sources=[],                       # ignore les réglages et CLAUDE.md du poste
            cwd=str(DOSSIER_TRAVAIL),
            hooks=hooks,
            max_turns=TOURS_MAX,
            env={**conf.env_claude(self.dossier_config), "MCP_TOOL_TIMEOUT": str(DELAI_OUTIL_MS)},
            resume=reprise or None,
        )

    async def _simuler(self, entree, tool_use_id, contexte) -> dict:
        self.simules.append((entree.get("tool_name", ""), entree.get("tool_input") or {}))
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": "Simulation : l'agent n'est pas interrogé. "
                                                                   "Termine ta réponse."}}

    # --- question -----------------------------------------------------------------------------------------------
    async def demander(self, question: str, reprise: str = "", sur_outil=None) -> Reponse:
        self.redirections, self.simules = [], []
        reponse = Reponse()
        client = ClaudeSDKClient(self.options(reprise))
        await client.connect()
        try:
            await client.query(question)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for bloc in message.content:
                        if isinstance(bloc, ToolUseBlock):
                            reponse.outils.append((bloc.name, bloc.input))
                            if sur_outil:
                                sur_outil(bloc.name, bloc.input)
                elif isinstance(message, ResultMessage):
                    reponse.texte = message.result or ""
                    reponse.cout_usd, reponse.tours = message.total_cost_usd, message.num_turns
                    reponse.erreur = message.is_error
                    reponse.message = "; ".join(message.errors or []) or (message.terminal_reason or "")
                    reponse.session_id = message.session_id or ""
        finally:
            await client.disconnect()
        reponse.redirections = list(self.redirections)
        return reponse


def historique(journal: jr.Journal, registre: reg.Registre, cle: str, dossier: Path | None = None) -> tuple[str, bool]:
    """Texte de l'outil historique_ticket et indicateur d'erreur (lecture du journal, 0 token). ``dossier`` :
    AGENT_DATA_DIR du lecteur des données (coût des agents, token_usage.csv) ; par défaut, celui du socle."""
    try:
        vue = journal.ticket(cle)
    except jr.JournalErreur as e:
        return f"Historique indisponible : {e}", True
    lignes = [f"Historique de {vue['cle']} dans le journal du manager (90 jours au plus) :"]
    if not vue["nb_questions"] and not vue["redirections"] and not vue["analyse"]:
        lignes.append("- rien n'est noté sur ce ticket.")
    if vue["nb_questions"]:
        lignes.append(f"- {vue['nb_questions']} question(s) au manager, dernière activité le "
                      f"{vue['derniere_activite'].replace('T', ' ')[:16]} :")
        for e in vue["questions"]:
            consultes = ", ".join(e.details.get("agents") or []) or "aucun agent"
            lignes.append(f"  · {e.horodatage.replace('T', ' ')[:16]} « {e.extrait} » → {e.decision or '?'} "
                          f"({consultes})")
    for e in vue["redirections"]:
        onglet = e.details.get("onglet")
        lignes.append(f"- {e.horodatage.replace('T', ' ')[:16]} redirigé vers {e.agent}"
                      + (f", onglet {onglet}" if onglet else "") + (f", projet {e.projet}" if e.projet else ""))
    if vue["analyse"]:
        a = vue["analyse"]
        lignes.append(f"- Analyse dans agent-lancement (relevé du {a['vu_le'].replace('T', ' ')[:16]}) : {a['analyse']}"
                      + (" — à valider" if a["a_valider"] else ""))
    else:
        lignes.append("- Aucun état d'analyse relevé par le tableau de bord.")
    cout_agent = sources.cout_ticket_agent(registre, vue["cle"], dossier=dossier)
    lignes.append(f"- Coût connu : {vue['cout_manager_usd']:.3f} $ de questions au manager"
                  + (f", {cout_agent:.3f} $ chez les agents" if cout_agent is not None else ""))
    return "\n".join(lignes), False


def decision(reponse: Reponse) -> tuple:
    """(décision, agent) d'une réponse : rediriger, interroger ou repondre (évaluations)."""
    if reponse.redirections:
        return "rediriger", reponse.redirections[0].agent
    agents = [nom.split("__")[1] for nom, _ in reponse.outils if nom.startswith("mcp__") and
              nom.split("__")[1] != SERVEUR]
    if agents:
        return "interroger", agents[0]
    return "repondre", ""
