"""Initiatives du manager (lot 6 bis) : point du matin et relances, SANS appel au modèle (0 token), en lecture seule.

Le point du matin résume ce qui s'est passé depuis le dernier « Vu » (au plus POINT_JOURS_MAX jours) : échanges avec
le manager, analyses passées « à valider », patchs livrés, builds Cucumber terminés, pannes d'agents et erreurs.
Il s'affiche une fois par jour, tant que le point du jour n'a pas été marqué « Vu », et seulement s'il y a du nouveau.

Les relances sont des règles évaluées à chaque affichage du tableau de bord (seuils : ``configuration.RELANCES`` et
``[relances]`` de commun.toml, délais en jours ouvrés). Chacune porte un lien prérempli vers l'agent et une clé
(règle, objet, état) : « Ignorer » la masque quelques jours, et elle revient d'elle-même si l'objet change. Aucune
action automatique : la relance ne fait que montrer où agir.

Sources : le journal (``bilan``, ``evenements``, ``ticket``) et le tableau de bord déjà construit
(``tableau.construire``), sans nouvel appel aux agents. Rien n'est envoyé hors du manager : pas de notification, pas
d'e-mail. Ce module ne dépend pas de Streamlit (affichage : ``ihm_tableau.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from manager import configuration as conf
from manager import journal as jr
from manager import liens as lk
from manager import tableau as tb

POINT_JOURS_MAX = 7
LIGNES_MAX = 5           # par rubrique ; au-delà : « … et N autre(s) »


@dataclass
class Rubrique:
    titre: str
    lignes: list = field(default_factory=list)      # [{texte, lien}]
    autres: int = 0                                 # lignes non affichées (au-delà de LIGNES_MAX)


@dataclass
class Point:
    depuis: str                                     # ISO : début de la période couverte
    premier: bool                                   # aucun « Vu » encore enregistré
    rubriques: list = field(default_factory=list)


def _iso(texte: str) -> str:
    """« 2026-11-19 16:00 » → « 2026-11-19T16:00 » (comparable aux horodatages ISO du journal)."""
    return str(texte or "").strip().replace(" ", "T")


def _rubrique(titre: str, lignes: list) -> Rubrique:
    return Rubrique(titre, lignes[:LIGNES_MAX], max(0, len(lignes) - LIGNES_MAX))


def _fin_build(b: dict) -> str:
    try:
        return (datetime.fromisoformat(_iso(b["debut"])) + timedelta(minutes=b["duree_min"])).isoformat(
            timespec="minutes")
    except (KeyError, TypeError, ValueError):
        return ""


def _liens(t: tb.Tableau) -> dict:
    """Liens préremplis déjà construits par le tableau de bord, par ticket."""
    return {l["cle"]: l["lien"] for l in t.tickets.lignes + t.analyses.lignes if l.get("lien")}


def periode(dernier: str, maintenant: datetime) -> tuple[str, bool]:
    """(début de la période couverte, point à afficher ?) : affiché si aucun « Vu » aujourd'hui."""
    plancher = (maintenant - timedelta(days=POINT_JOURS_MAX)).isoformat(timespec="seconds")
    a_afficher = not dernier or dernier[:10] < maintenant.date().isoformat()
    return max(dernier, plancher), a_afficher


def point_du_matin(journal: jr.Journal, t: tb.Tableau, maintenant: datetime | None = None) -> Point | None:
    """Point du matin, ou None s'il est déjà vu aujourd'hui, s'il n'y a rien de nouveau ou si le journal est
    illisible (le tableau de bord le signale déjà). Ne lève pas."""
    maintenant = maintenant or datetime.now()
    try:
        dernier = journal.dernier_point()
        depuis, a_afficher = periode(dernier, maintenant)
        if not a_afficher:
            return None
        bilan = journal.bilan(depuis)
        evenements = journal.evenements(depuis=depuis, types=("etat", "erreur"), limite=100)
    except jr.JournalErreur:
        return None
    rubriques, liens = [], _liens(t)

    # échanges avec le manager
    if bilan["questions"] or bilan["redirections"]:
        texte = f"{bilan['questions']} question(s), {bilan['cout_usd']:.2f} $"
        if bilan["sans_cout"]:
            texte += f" (dont {bilan['sans_cout']} sans coût connu)"
        if bilan["redirections"]:
            texte += " ; redirections : " + ", ".join(f"{a or '?'} {n}" for a, n in bilan["redirections"].items())
        rubriques.append(_rubrique("Échanges avec le manager", [{"texte": texte, "lien": ""}]))

    # analyses passées « à valider » (dernier relevé du lecteur des données)
    analyses = [{"texte": f"{a['cle']} · {a['analyse']} (modifiée le {a['modifiee'].replace('T', ' ')[:16]})",
                 "lien": liens.get(a["cle"], "")} for a in bilan["analyses_a_valider"]]
    if analyses:
        rubriques.append(_rubrique("Analyses à valider", analyses))

    # patchs livrés
    etats = {"jointe": "en pièce jointe Jira", "non_jointe": "NON joint dans Jira", "non_verifiee": "non vérifié"}
    patchs = [{"texte": f"{p['cle']} · {', '.join(p['fichiers'])} · {etats.get(p['statut'], p['statut'])}",
               "lien": p.get("lien", "")} for p in t.patchs.lignes if _iso(p["livre_le"]) >= depuis[:16]]
    if patchs:
        rubriques.append(_rubrique("Patchs livrés", patchs))

    # builds Cucumber terminés pendant la période
    builds = [b for b in t.jenkins.resume.get("builds", []) if not b["en_cours"] and _fin_build(b) >= depuis[:16]]
    if builds:
        rubriques.append(_rubrique("Builds Cucumber terminés", [
            {"texte": f"{b['job']} #{b['numero']} {b['resultat']} ({b['debut']}, {b['duree_min']} min)", "lien": ""}
            for b in builds]))

    # pannes d'agents et erreurs
    incidents = []
    for e in evenements:
        heure = e.horodatage.replace("T", " ")[:16]
        if e.type == "etat" and e.details.get("apres") in tb.ETATS_ALERTE:
            incidents.append({"texte": f"{heure} {e.agent} : {e.details.get('avant') or '?'} → "
                                       f"{e.details.get('apres')}", "lien": ""})
        elif e.type == "erreur":
            incidents.append({"texte": f"{heure} {e.details.get('origine', '')} : "
                                       f"{e.details.get('message') or e.extrait}", "lien": ""})
    if incidents:
        rubriques.append(_rubrique("Pannes et erreurs", incidents))

    return Point(depuis=depuis, premier=not dernier, rubriques=rubriques) if rubriques else None


# --- relances ---------------------------------------------------------------------------------------------------
REDIRECTIONS_JOURS = 30          # redirections examinées pour « sans suite »
ECHECS = ("FAILURE", "UNSTABLE")
URGENCE = ("patch_non_joint", "build_en_echec", "boucle_echecs", "generation_bloquee_heures", "patch_non_verifie_jours",
           "redirection_sans_suite_jours", "analyse_a_valider_jours", "proposition_en_attente_jours")  # ordre d'affichage


@dataclass
class Relance:
    regle: str                   # clé de configuration.RELANCES
    cle: str                     # règle, objet et état : « Ignorer » ne masque que cet état-là
    texte: str
    lien: str = ""


def ajouter_jours_ouvres(debut: datetime, jours: int) -> datetime:
    """``debut`` + ``jours`` jours ouvrés (lundi à vendredi, sans jours fériés), à la même heure."""
    fin, restants = debut, jours
    while restants > 0:
        fin += timedelta(days=1)
        if fin.weekday() < 5:
            restants -= 1
    return fin


def _date(texte: str) -> datetime | None:
    try:
        return datetime.fromisoformat(_iso(texte))
    except ValueError:
        return None


def _echu(texte: str, jours: int, maintenant: datetime) -> datetime | None:
    """Date de ``texte`` si le délai de ``jours`` jours ouvrés est dépassé ; None sinon (ou date illisible)."""
    debut = _date(texte)
    return debut if debut and jours > 0 and ajouter_jours_ouvres(debut, jours) <= maintenant else None


def _jour(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M")


def _url(t: tb.Tableau, nom: str) -> str:
    return next((a.get("url", "") for a in t.agents.lignes if a.get("nom") == nom), "")


# Redirections suivies par « sans suite » : vers agent-lancement, et celles du journal faites vers l'ancien
# agent-scrum-master (supprimé), qui portait alors l'onglet Lancement
REDIRIGES = (tb.LANCEMENT, "scrum-master")


def regles(journal: jr.Journal, t: tb.Tableau, seuils: dict | None = None,
           maintenant: datetime | None = None) -> list[Relance]:
    """Toutes les relances actives, ignorées comprises. Ne lève pas : une source illisible ne donne rien."""
    s = {**conf.RELANCES, **(seuils or {})}
    maintenant = maintenant or datetime.now()
    urls, liens = {nom: _url(t, nom) for nom in (tb.LANCEMENT, tb.CUCUMBER)}, _liens(t)

    def lien(cle: str = "", page: str = "") -> str:
        """Ticket : agent-lancement ; page « cucumber » : agent-cucumber."""
        agent = tb.CUCUMBER if page == "cucumber" else tb.LANCEMENT
        if urls[agent]:
            return lk.construire(agent, urls[agent], page=page, ticket=cle)
        return liens.get(cle, "") if cle else ""

    relances = []
    # analyse à valider non publiée
    for a in t.analyses.lignes:
        debut = _echu(a["modifiee"], s["analyse_a_valider_jours"], maintenant)
        if debut:
            relances.append(Relance("analyse_a_valider_jours", f"analyse:{a['cle']}:{_iso(a['modifiee'])}",
                                    f"{a['cle']} : analyse à valider depuis le {_jour(debut)}, non publiée dans Jira",
                                    a.get("lien") or lien(a["cle"])))

    # ticket redirigé vers agent-lancement, analyse non modifiée depuis
    if s["redirection_sans_suite_jours"] > 0:
        try:
            depuis = (maintenant - timedelta(days=REDIRECTIONS_JOURS)).isoformat(timespec="seconds")
            vues = {}
            for e in journal.evenements(depuis=depuis, types=("redirection",), limite=500):
                if e.agent in REDIRIGES and e.ticket and e.ticket not in vues:
                    vues[e.ticket] = e.horodatage                        # la plus récente par ticket
            for cle, quand in vues.items():
                debut = _echu(quand, s["redirection_sans_suite_jours"], maintenant)
                if debut and journal.ticket(cle)["analyse"].get("analyse_modifiee", "") < quand:
                    relances.append(Relance("redirection_sans_suite_jours", f"redirection:{cle}:{quand}",
                                            f"{cle} : redirigé vers Lancement le {_jour(debut)}, analyse non "
                                            "modifiée depuis", lien(cle)))
        except jr.JournalErreur:
            pass

    # patchs livrés : NON joints, ou pièce jointe non vérifiée
    for p in t.patchs.lignes:
        cle_p = f"{p['cle']}:{_iso(p['livre_le'])}"
        if p["statut"] == "non_jointe" and s["patch_non_joint"]:
            relances.append(Relance("patch_non_joint", f"patch_non_joint:{cle_p}",
                                    f"{p['cle']} : patch livré le {p['livre_le']} NON joint dans Jira "
                                    f"({', '.join(p['manquants'])})", p.get("lien") or lien(p["cle"])))
        elif p["statut"] == "non_verifiee" and _echu(p["livre_le"], s["patch_non_verifie_jours"], maintenant):
            relances.append(Relance("patch_non_verifie_jours", f"patch_non_verifie:{cle_p}",
                                    f"{p['cle']} : pièce jointe du patch livré le {p['livre_le']} non vérifiée "
                                    f"({p['raison'] or 'raison inconnue'}) : la contrôler dans Jira",
                                    p.get("lien") or lien(p["cle"])))

    # dernier build terminé de chaque job en échec, jusqu'à un build réussi
    if s["build_en_echec"]:
        vus = set()
        for b in t.jenkins.resume.get("builds", []):                 # du plus récent au plus ancien
            if b["en_cours"] or b["job"] in vus:
                continue
            vus.add(b["job"])
            if b["resultat"] in ECHECS:
                relances.append(Relance("build_en_echec", f"build:{b['job']}:{b['numero']}",
                                        f"Cucumber : dernier build {b['job']} #{b['numero']} en échec "
                                        f"({b['resultat']}, {b['debut']})", lien(page="cucumber")))

    # boucle de correction Cucumber arrêtée avec des échecs dus à l'agent non résolus, ou des défauts applicatifs
    if s.get("boucle_echecs", True):
        for b in t.boucles.lignes:
            if not b.get("relance") or b.get("etat") == "en cours":
                continue
            restants = ", ".join(f"{c} {n}" for c, n in b.get("categories", {}).items() if n and c in
                                 ("AGENT", "INCERTAIN", "APPLICATION"))
            relances.append(Relance("boucle_echecs", f"boucle:{b['generation']}:{b.get('mis_a_jour', '')}",
                                    (f"Cucumber {b['client']} : boucle de correction {b['etat']} avec des échecs "
                                     f"({restants or 'à voir'}) — {b.get('raison', '')}")[:300], b.get("lien", "")))

    # proposition d'amélioration d'agent-lancement (agent-retrospective) en attente de validation
    for p in t.retrospectives.resume.get("propositions", []):
        debut = _echu(p["depuis"], s["proposition_en_attente_jours"], maintenant)
        if debut:
            relances.append(Relance("proposition_en_attente_jours", f"proposition:{p['id']}:{_iso(p['depuis'])}",
                                    f"Rétrospective : proposition « {p['libelle']} » ({p['tickets']} tickets) en "
                                    f"attente depuis le {_jour(debut)} : valider, rejeter ou reporter",
                                    p.get("lien", "")))

    # génération Cucumber en cours depuis trop longtemps
    heures = s["generation_bloquee_heures"]
    for c in t.jenkins.lignes:
        debut = _date(c.get("depuis", "")) if c.get("type") == "client" and c.get("en_cours") else None
        if heures > 0 and debut and maintenant - debut > timedelta(hours=heures):
            relances.append(Relance("generation_bloquee_heures", f"generation:{c['client']}:{c['depuis']}",
                                    f"Génération Cucumber de {c['client']} en cours depuis plus de {heures} h "
                                    f"({_jour(debut)}) : bloquée ?", c.get("lien") or lien(page="cucumber")))
    return relances


def relances(journal: jr.Journal, t: tb.Tableau, seuils: dict | None = None,
             maintenant: datetime | None = None) -> list[Relance]:
    """Relances à afficher, de la plus urgente à la moins urgente (URGENCE) : ``regles`` sans celles que
    l'utilisateur a ignorées (toutes si le journal est illisible)."""
    actives = regles(journal, t, seuils, maintenant)
    try:
        ignorees = journal.relances_ignorees()
    except jr.JournalErreur:
        ignorees = set()
    return sorted((r for r in actives if r.cle not in ignorees), key=lambda r: URGENCE.index(r.regle))
