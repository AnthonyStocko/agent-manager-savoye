"""Tableau de bord du manager (lot 6) : construction des blocs, SANS appel au modèle (0 token).

Sources, toutes en lecture : journal (``journal.py``), superviseur (``Pilote().etat()``), outils MCP du lecteur des
données des agents (``registre.lecteur()``, ``mcp_lecture.py`` : ``analyses``, ``cucumber``, ``builds_jenkins``,
``patchs``…, appelés par ``sources.appeler_mcp``) et ``token_usage.csv`` d'AGENT_DATA_DIR. Les liens vont à
agent-lancement (tickets, analyses, patchs) et à agent-cucumber (clients Cucumber). Les relances (patch NON joint,
génération bloquée…) sont calculées à part, par ``initiatives.relances``.
Chaque bloc tolère une source indisponible : il porte alors un message court (``erreur``), les
autres s'affichent. Ce module ne dépend pas de Streamlit (affichage : ``ihm_tableau.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from manager import journal as jr
from manager import liens
from manager import registre as reg
from manager import sources
from manager import superviseur as sup
from manager.pilote import PiloteErreur

LANCEMENT = "lancement"   # tickets, analyses et patchs : agent-lancement (nom au registre)
CUCUMBER = "cucumber"     # clients Cucumber et verrou Jenkins : agent-cucumber
TICKETS_JOURS = 7
ERREURS_HEURES = 24
BUILDS = 10              # lus par job (point du matin : builds terminés sur la période)
BUILDS_AFFICHES = 3      # affichés par job dans le bloc Jenkins
ETATS_ALERTE = (sup.ECHEC, sup.INJOIGNABLE, sup.INSTALLATION)
PATCHS_JOURS = 7
RETROS = 10             # rétrospectives lues (outil retrospectives du lecteur des données)
RETRO = "retrospective"  # nom de l'agent au registre
RETROS_ABSENT = ("Le lecteur des données (mcp_lecture.py du manager) n'a pas l'outil retrospectives : "
                 "mettre à jour le manager pour voir les rétrospectives.")
PATCHS_ABSENT = ("Le lecteur des données (mcp_lecture.py du manager) n'a pas l'outil patchs : "
                 "mettre à jour le manager pour voir les patchs livrés.")
NON_CONSULTABLES = "Données des agents non consultables"


@dataclass
class Bloc:
    titre: str
    lignes: list = field(default_factory=list)
    erreur: str = ""          # source indisponible : message court
    note: str = ""            # information (ex. source absente par conception)
    resume: dict = field(default_factory=dict)


@dataclass
class Tableau:
    agents: Bloc
    tickets: Bloc
    analyses: Bloc
    jenkins: Bloc
    patchs: Bloc
    couts: Bloc
    alertes: Bloc
    genere_le: str = ""
    boucles: Bloc = field(default_factory=lambda: Bloc("Boucles de correction Cucumber"))
    retrospectives: Bloc = field(default_factory=lambda: Bloc("Rétrospectives"))

    def blocs(self) -> list[Bloc]:
        return [self.alertes, self.agents, self.tickets, self.analyses, self.jenkins, self.boucles, self.patchs,
                self.retrospectives, self.couts]


def _lien(agent: reg.Agent | None, ticket: str = "", page: str = "") -> str:
    return liens.construire(agent.nom, agent.url, page=page, ticket=ticket) if agent else ""


def _agent(registre: reg.Registre, nom: str) -> reg.Agent | None:
    try:
        return registre.agent(nom)
    except reg.RegistreErreur:
        return None


def construire(registre: reg.Registre, journal: jr.Journal, pilote, appeler_mcp=sources.appeler_mcp,
               lire_csv=sources.lire_token_usage, maintenant: datetime | None = None,
               lecteur: reg.Agent | None = None) -> Tableau:
    """Construit tous les blocs. ``appeler_mcp(agent, appels)``, ``lire_csv(dossier, depuis)`` et ``lecteur`` (source
    des données des agents, ``registre.lecteur()`` par défaut) sont injectables (tests). Ne lève pas : chaque source en
    erreur est rapportée dans son bloc."""
    maintenant = maintenant or datetime.now()
    erreur_lecteur = ""
    if lecteur is None:
        try:
            lecteur = reg.lecteur()
        except Exception as e:  # noqa: BLE001 — agent-socle absent ou AGENT_DATA_DIR illisible
            erreur_lecteur = f"lecteur des données indisponible : {e}"[:300]
    lancement, cucumber_agent = _agent(registre, LANCEMENT), _agent(registre, CUCUMBER)
    t = Tableau(agents=Bloc("Agents"), tickets=Bloc("Tickets en cours"), analyses=Bloc("Analyses à valider"),
                jenkins=Bloc("Jenkins / Cucumber"), patchs=Bloc(f"Patchs livrés · {PATCHS_JOURS} j"),
                couts=Bloc("Coûts"), alertes=Bloc("Alertes"), genere_le=maintenant.isoformat(timespec="seconds"))
    alertes = t.alertes.lignes

    # --- agents (superviseur) -----------------------------------------------------------------------------------
    try:
        etats = pilote.etat()
    except PiloteErreur as e:
        t.agents.erreur = f"Superviseur injoignable : {e}"
        alertes.append({"niveau": "erreur", "texte": t.agents.erreur})
        etats = []
    for e in etats:
        t.agents.lignes.append({k: e.get(k, "") for k in ("nom", "titre", "etat", "message", "url")})
        if e.get("etat") in ETATS_ALERTE:
            alertes.append({"niveau": "erreur", "texte": f"{e.get('titre')} : {e.get('etat')}"
                                                         + (f" — {e['message']}" if e.get("message") else "")})
    t.agents.resume = {etat: sum(1 for e in etats if e.get("etat") == etat) for etat in
                       dict.fromkeys(e.get("etat") for e in etats)}

    # --- données des agents : analyses, cucumber, builds, patchs (un seul lancement du lecteur MCP) -------------
    textes, erreur_donnees, erreur_patchs, erreur_boucles, erreur_retros = {}, erreur_lecteur, "", "", ""
    if lecteur is not None:
        try:
            r = appeler_mcp(lecteur, [("analyses", {}), ("cucumber", {}), ("builds_jenkins", {"nombre": BUILDS}),
                                      ("patchs", {"jours": PATCHS_JOURS}), ("boucles_cucumber", {}),
                                      ("retrospectives", {"nombre": RETROS})])
            textes, erreurs = r.textes, dict(r.erreurs)
            erreur_patchs = erreurs.pop("patchs", "")          # rapportée dans son bloc, sans alerte
            erreur_boucles = erreurs.pop("boucles_cucumber", "")
            erreur_retros = erreurs.pop("retrospectives", "")
            if erreurs:
                erreur_donnees = " ; ".join(f"{o} : {m}" for o, m in erreurs.items())
        except sources.SourceErreur as e:
            erreur_donnees = str(e)
    if erreur_donnees:
        alertes.append({"niveau": "attention", "texte": f"{NON_CONSULTABLES} : {erreur_donnees}"[:300]})

    analyses = sources.lire_analyses(textes.get("analyses", ""))
    if "analyses" in textes:
        journal.observer_analyses(analyses)          # index par ticket : au mieux
        t.analyses.lignes = [{**a, "lien": _lien(lancement, a["cle"])} for a in analyses if a["a_valider"]]
    else:
        t.analyses.erreur = erreur_donnees or "analyses indisponibles"

    # --- tickets en cours (journal + analyses relevées) ---------------------------------------------------------
    try:
        recents = journal.tickets_recents(TICKETS_JOURS)
        t.tickets.lignes = [{**r, "lien": _lien(lancement, r["cle"])} for r in recents]
        if "analyses" not in textes:
            t.tickets.note = f"État des analyses : dernier relevé connu ({NON_CONSULTABLES.lower()})."
    except jr.JournalErreur as e:
        t.tickets.erreur = str(e)
        t.tickets.lignes = [{"cle": a["cle"], "derniere_activite": a["modifiee"], "nb_evenements": 0,
                             "analyse": a["libelle"], "a_valider": a["a_valider"], "lien": _lien(lancement, a["cle"])}
                            for a in analyses[:20]]

    # --- Jenkins / Cucumber --------------------------------------------------------------------------------------
    if "cucumber" in textes or "builds_jenkins" in textes:
        cucumber = sources.lire_cucumber(textes.get("cucumber", ""))
        builds = sources.lire_builds(textes.get("builds_jenkins", ""))
        en_cours = [b for b in builds if b["en_cours"]]
        generations = [c for c in cucumber["clients"] if c["en_cours"]]
        if en_cours:
            verrou = "occupé : " + ", ".join(f"{b['job']} #{b['numero']} en cours depuis {b['debut']}"
                                            for b in en_cours)
        elif generations:
            verrou = "génération en cours (" + ", ".join(c["client"] for c in generations) + "), aucun build en cours"
        else:
            verrou = "libre"
        t.jenkins.resume = {"verrou": verrou, "proprietaire": CUCUMBER, "jobs": cucumber["jobs"],
                            "builds": builds}
        affiches, par_job = [], {}
        for b in builds:
            par_job[b["job"]] = par_job.get(b["job"], 0) + 1
            if par_job[b["job"]] <= BUILDS_AFFICHES:
                affiches.append(b)
        t.jenkins.lignes = [{"type": "client", **c, "lien": _lien(cucumber_agent, page="cucumber")}
                            for c in cucumber["clients"]] \
            + [{"type": "build", **b} for b in affiches]
        if "builds_jenkins" not in textes:
            t.jenkins.note = "Builds Jenkins indisponibles."
    else:
        t.jenkins.erreur = erreur_donnees or "Cucumber indisponible"

    # --- boucles de correction autonome d'agent-cucumber (information, aucune validation) ----------------------
    if "boucles_cucumber" in textes:
        devtool = _agent(registre, "dev-tool")
        for b in sources.lire_boucles(textes["boucles_cucumber"]):
            lien = ""
            if b["relance"] and b["demande"] and devtool:
                lien = liens.construire(devtool.nom, devtool.url, page="developper", depot="agent-cucumber",
                                        demande=b["demande"], contexte=f"Boucle {b['generation']} : {b['raison']}")
            elif b["relance"] and cucumber_agent:
                lien = liens.construire(cucumber_agent.nom, cucumber_agent.url, page="cucumber", projet=b["client"])
            t.boucles.lignes.append({**b, "lien": lien})
    else:
        t.boucles.note = "Boucles de correction indisponibles" + (f" : {erreur_boucles}" if erreur_boucles else ".")

    # --- patchs livrés (registre des livraisons de l'étape patch d'agent-lancement) ------------------------------
    if "patchs" in textes:
        for p in sources.lire_patchs(textes["patchs"]):
            t.patchs.lignes.append({**p, "lien": _lien(lancement, p["cle"])})
        t.patchs.resume = {statut: sum(1 for p in t.patchs.lignes if p["statut"] == statut)
                           for statut in ("jointe", "non_jointe", "non_verifiee")}
    elif "unknown tool" in erreur_patchs.lower():               # lecteur pas encore à jour
        t.patchs.note = PATCHS_ABSENT
    else:
        t.patchs.erreur = (erreur_patchs and f"patchs : {erreur_patchs}"[:300]) or erreur_donnees or "patchs indisponibles"

    # --- rétrospectives d'agent-retrospective (lecteur des données) et propositions en attente -----------------
    if "retrospectives" in textes:
        retro = _agent(registre, RETRO)
        retros, propositions = sources.lire_retrospectives(textes["retrospectives"])
        t.retrospectives.lignes = [{**r, "lien": _lien(retro, r["cle"], page="")} for r in retros]
        lien_props = _lien(retro, page="")
        t.retrospectives.resume = {"propositions": [{**p, "lien": lien_props} for p in propositions]}
        if not retro:
            t.retrospectives.note = "Agent retrospective absent du registre : liens indisponibles."
    elif "unknown tool" in erreur_retros.lower():
        t.retrospectives.note = RETROS_ABSENT
    else:
        t.retrospectives.erreur = ((erreur_retros and f"retrospectives : {erreur_retros}"[:300]) or erreur_donnees
                                   or "rétrospectives indisponibles")

    # --- coûts --------------------------------------------------------------------------------------------------
    try:
        t.couts.resume["manager_7j"] = journal.couts(7)
        t.couts.resume["manager_30j"] = journal.couts(30)
    except jr.JournalErreur as e:
        t.couts.erreur = str(e)
    if lecteur is not None:
        try:
            lignes = lire_csv(lecteur.dossier, (maintenant - timedelta(days=30)).isoformat(timespec="seconds"))
        except sources.SourceErreur as e:
            t.couts.note = f"Coûts des agents illisibles : {e}"
            lignes = None
        if lignes is None and not t.couts.note:
            t.couts.note = "Coûts des agents inconnus : .tmp/token_usage.csv absent d'AGENT_DATA_DIR."
        if lignes is not None:
            depuis_7 = (maintenant - timedelta(days=7)).isoformat(timespec="seconds")
            t.couts.resume["agents_7j"] = round(sum(l["cout_usd"] for l in lignes if l["date"] >= depuis_7), 4)
            t.couts.resume["agents_30j"] = round(sum(l["cout_usd"] for l in lignes), 4)
            t.couts.lignes = sources.couts_par_etape(lignes)

    # --- alertes : erreurs récentes du journal ------------------------------------------------------------------
    try:
        depuis = (maintenant - timedelta(hours=ERREURS_HEURES)).isoformat(timespec="seconds")
        for e in journal.evenements(depuis=depuis, types=("erreur",), limite=5):
            alertes.append({"niveau": "attention", "texte": f"{e.horodatage.replace('T', ' ')[:16]} "
                            f"{e.details.get('origine', '')} : {e.details.get('message') or e.extrait}"})
        actuels = {e.get("nom") for e in etats if e.get("etat") in ETATS_ALERTE}     # déjà signalés ci-dessus
        for e in journal.evenements(depuis=depuis, types=("etat",), limite=50):
            if e.details.get("apres") in ETATS_ALERTE and e.agent not in actuels:
                alertes.append({"niveau": "info", "texte": f"{e.horodatage.replace('T', ' ')[:16]} {e.agent} : "
                                f"{e.details.get('avant') or '?'} → {e.details.get('apres')}"})
    except jr.JournalErreur as e:
        alertes.append({"niveau": "attention", "texte": f"Journal du manager : {e}"})
    return t
