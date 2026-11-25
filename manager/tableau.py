"""Tableau de bord du manager (lot 6) : construction des blocs, SANS appel au modèle (0 token).

Sources, toutes en lecture : journal (``journal.py``), superviseur (``Pilote().etat()``), outils MCP de scrum-master
(``analyses``, ``cucumber``, ``builds_jenkins``, ``patchs``, appelés par ``sources.appeler_mcp``) et ``token_usage.csv`` de
scrum-master. Les relances (patch NON joint, génération bloquée…) sont calculées à part, par ``initiatives.relances``.
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

SCRUM = "scrum-master"
TICKETS_JOURS = 7
ERREURS_HEURES = 24
BUILDS = 10              # lus par job (point du matin : builds terminés sur la période)
BUILDS_AFFICHES = 3      # affichés par job dans le bloc Jenkins
ETATS_ALERTE = (sup.ECHEC, sup.INJOIGNABLE, sup.INSTALLATION)
PATCHS_JOURS = 7
PATCHS_ABSENT = ("Scrum Master n'a pas encore l'outil patchs (registre des livraisons, agent-scrum-master#49) : "
                 "le mettre à jour pour voir les patchs livrés.")


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

    def blocs(self) -> list[Bloc]:
        return [self.alertes, self.agents, self.tickets, self.analyses, self.jenkins, self.patchs, self.couts]


def _lien(agent: reg.Agent | None, ticket: str = "", page: str = "lancement") -> str:
    return liens.construire(agent.nom, agent.url, page=page, ticket=ticket) if agent else ""


def construire(registre: reg.Registre, journal: jr.Journal, pilote, appeler_mcp=sources.appeler_mcp,
               lire_csv=sources.lire_token_usage, maintenant: datetime | None = None) -> Tableau:
    """Construit tous les blocs. ``appeler_mcp(agent, appels)`` et ``lire_csv(dossier, depuis)`` sont injectables
    (tests). Ne lève pas : chaque source en erreur est rapportée dans son bloc."""
    maintenant = maintenant or datetime.now()
    try:
        scrum = registre.agent(SCRUM)
    except reg.RegistreErreur:
        scrum = None
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

    # --- scrum-master : analyses, cucumber, builds, patchs (un seul lancement du serveur MCP) -------------------
    textes, erreur_scrum, erreur_patchs = {}, "", ""
    if scrum is None:
        erreur_scrum = "scrum-master absent du registre"
    else:
        try:
            r = appeler_mcp(scrum, [("analyses", {}), ("cucumber", {}), ("builds_jenkins", {"nombre": BUILDS}),
                                    ("patchs", {"jours": PATCHS_JOURS})])
            textes, erreurs = r.textes, dict(r.erreurs)
            erreur_patchs = erreurs.pop("patchs", "")          # rapportée dans son bloc, sans alerte
            if erreurs:
                erreur_scrum = " ; ".join(f"{o} : {m}" for o, m in erreurs.items())
        except sources.SourceErreur as e:
            erreur_scrum = str(e)
    if erreur_scrum:
        alertes.append({"niveau": "attention", "texte": f"Scrum Master non consultable : {erreur_scrum}"[:300]})

    analyses = sources.lire_analyses(textes.get("analyses", ""))
    if "analyses" in textes:
        journal.observer_analyses(analyses)          # index par ticket : au mieux
        t.analyses.lignes = [{**a, "lien": _lien(scrum, a["cle"])} for a in analyses if a["a_valider"]]
    else:
        t.analyses.erreur = erreur_scrum or "analyses indisponibles"

    # --- tickets en cours (journal + analyses relevées) ---------------------------------------------------------
    try:
        recents = journal.tickets_recents(TICKETS_JOURS)
        t.tickets.lignes = [{**r, "lien": _lien(scrum, r["cle"])} for r in recents]
        if "analyses" not in textes:
            t.tickets.note = "État des analyses : dernier relevé connu (Scrum Master non consultable)."
    except jr.JournalErreur as e:
        t.tickets.erreur = str(e)
        t.tickets.lignes = [{"cle": a["cle"], "derniere_activite": a["modifiee"], "nb_evenements": 0,
                             "analyse": a["libelle"], "a_valider": a["a_valider"], "lien": _lien(scrum, a["cle"])}
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
        t.jenkins.resume = {"verrou": verrou, "proprietaire": "scrum-master", "jobs": cucumber["jobs"],
                            "builds": builds}
        affiches, par_job = [], {}
        for b in builds:
            par_job[b["job"]] = par_job.get(b["job"], 0) + 1
            if par_job[b["job"]] <= BUILDS_AFFICHES:
                affiches.append(b)
        t.jenkins.lignes = [{"type": "client", **c, "lien": _lien(scrum, page="cucumber")} for c in cucumber["clients"]] \
            + [{"type": "build", **b} for b in affiches]
        if "builds_jenkins" not in textes:
            t.jenkins.note = "Builds Jenkins indisponibles."
    else:
        t.jenkins.erreur = erreur_scrum or "Cucumber indisponible"

    # --- patchs livrés (registre des livraisons de scrum-master) -------------------------------------------------
    if "patchs" in textes:
        for p in sources.lire_patchs(textes["patchs"]):
            t.patchs.lignes.append({**p, "lien": _lien(scrum, p["cle"])})
        t.patchs.resume = {statut: sum(1 for p in t.patchs.lignes if p["statut"] == statut)
                           for statut in ("jointe", "non_jointe", "non_verifiee")}
    elif "unknown tool" in erreur_patchs.lower():               # scrum-master pas encore à jour
        t.patchs.note = PATCHS_ABSENT
    else:
        t.patchs.erreur = (erreur_patchs and f"patchs : {erreur_patchs}"[:300]) or erreur_scrum or "patchs indisponibles"

    # --- coûts --------------------------------------------------------------------------------------------------
    try:
        t.couts.resume["manager_7j"] = journal.couts(7)
        t.couts.resume["manager_30j"] = journal.couts(30)
    except jr.JournalErreur as e:
        t.couts.erreur = str(e)
    if scrum is not None:
        try:
            lignes = lire_csv(scrum.dossier, (maintenant - timedelta(days=30)).isoformat(timespec="seconds"))
        except sources.SourceErreur as e:
            t.couts.note = f"Coûts de Scrum Master illisibles : {e}"
            lignes = None
        if lignes is None and not t.couts.note:
            t.couts.note = "Coûts de Scrum Master inconnus : .tmp/token_usage.csv absent."
        if lignes is not None:
            depuis_7 = (maintenant - timedelta(days=7)).isoformat(timespec="seconds")
            t.couts.resume["scrum_7j"] = round(sum(l["cout_usd"] for l in lignes if l["date"] >= depuis_7), 4)
            t.couts.resume["scrum_30j"] = round(sum(l["cout_usd"] for l in lignes), 4)
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
