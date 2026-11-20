"""Initiatives du manager (lot 6 bis) : point du matin, SANS appel au modèle (0 token), en lecture seule.

Le point du matin résume ce qui s'est passé depuis le dernier « Vu » (au plus POINT_JOURS_MAX jours) : échanges avec
le manager, analyses passées « à valider », patchs livrés, builds Cucumber terminés, pannes d'agents et erreurs.
Il s'affiche une fois par jour, tant que le point du jour n'a pas été marqué « Vu », et seulement s'il y a du nouveau.

Sources : le journal (``bilan``, ``evenements``) et le tableau de bord déjà construit (``tableau.construire``), sans
nouvel appel aux agents. Rien n'est envoyé hors du manager : pas de notification, pas d'e-mail. Ce module ne dépend
pas de Streamlit (affichage : ``ihm_tableau.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from manager import journal as jr
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

    # analyses passées « à valider » (dernier relevé de scrum-master)
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
