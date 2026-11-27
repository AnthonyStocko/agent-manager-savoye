"""Sources en LECTURE SEULE du tableau de bord (lot 6), sans appel au modèle (0 token).

- Outils MCP des agents, appelés directement avec le client MCP Python (paquet ``mcp``, déjà installé avec
  ``claude-agent-sdk``), pas par une session Claude : le serveur de l'agent est lancé comme le fait le routeur
  (commande ``mcp`` du registre, venv de l'agent, ``configuration.variables_pour``), interrogé puis arrêté.
  Seuls les outils de LISTE_BLANCHE sont appelables (jamais ``question`` d'odatio, qui appelle le modèle).
- Fichiers locaux des agents, lus sans les modifier (formats ci-dessous).

Formats lus (contrat avec le lecteur des données des agents, ``mcp_lecture.py`` du manager, source
``registre.NOM_LECTEUR`` ; un format inattendu donne une liste vide, jamais une exception) :
- outil ``analyses`` (tools/lecture_manager.py) : une ligne par analyse
  ``- <CLE> (modifiée le AAAA-MM-JJ HH:MM) : <libellé>[ ← à valider]`` ;
- outil ``cucumber`` : ``- <client> : <génération en cours (processus …, lancée le …)|aucune génération en cours>``,
  puis ``Jobs Jenkins : NOM = valeur, …`` ;
- outil ``builds_jenkins`` : ``<job> :`` puis ``  #<n> <RÉSULTAT|EN COURS> — <début>, <durée> min`` ;
- outil ``patchs`` (registre local des livraisons de l'étape patch) : une ligne par livraison
  ``- <CLE> (livré le AAAA-MM-JJ HH:MM) : <fichier.patch>[, …] — <état>``, état ``en pièce jointe Jira``,
  ``NON joint dans Jira : <fichiers>`` ou ``pièce jointe non vérifiée (<raison>)`` ;
- outil ``boucles_cucumber`` (boucles de correction autonome d'agent-cucumber, lecture_manager.boucles) : par boucle
  ``- <génération> [<client>] : <état> · itération <i>/<N> · échecs <a> → <b> · AGENT <n>, APPLICATION <n>,
  ENVIRONNEMENT <n>, INCERTAIN <n> · coût <x> $ · relance <oui|non> · mis à jour <ISO>``, puis lignes indentées
  ``raison : …``, ``correction it<i> <commit> : <cause>``, ``retour arrière it<i> <commit> → <revert>``,
  ``demande : <texte>`` ;
- outil ``retrospectives`` (agent-retrospective, lecture_manager.retrospectives) : ``Rétrospectives :`` puis
  ``- <CLE> (le AAAA-MM-JJ HH:MM) : score <n>/100 · ajusté <n|—>/100 · écarts expliqués <k> · analyse <ok|indisponible>``,
  puis ``Propositions en attente :`` et ``- <id> [<levier>] : <libellé> · tickets <k> · en attente depuis
  AAAA-MM-JJ HH:MM`` (« - aucune » si la liste est vide) ;
- fichier ``<AGENT_DATA_DIR>/.tmp/token_usage.csv`` (CLI des agents, TOKEN_USAGE_FIELDS) : séparateur « ; »,
  en-tête, colonnes lues ``date`` (ISO), ``ticket``, ``etape``, ``cout_usd`` (point ou virgule décimale).
"""

from __future__ import annotations

import asyncio
import csv
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from manager import configuration as conf
from manager import registre as reg

DELAI_MCP_S = 30.0
LISTE_BLANCHE = {
    reg.NOM_LECTEUR: ("analyses", "analyse", "ticket", "cucumber", "builds_jenkins", "patchs", "boucles_cucumber",
                      "retrospectives"),
    "odatio": ("projets",),
}
TOKEN_USAGE = Path(".tmp") / "token_usage.csv"


class SourceErreur(Exception):
    """Source indisponible : agent arrêté, serveur MCP en erreur, fichier illisible…"""


# --- appel MCP direct ------------------------------------------------------------------------------------------
@dataclass
class ResultatMCP:
    textes: dict = field(default_factory=dict)      # outil -> texte
    erreurs: dict = field(default_factory=dict)     # outil -> message


async def _appeler(commande: list[str], env: dict, appels: list[tuple], dossier: Path, delai: float) -> ResultatMCP:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    resultat = ResultatMCP()
    parametres = StdioServerParameters(command=commande[0], args=commande[1:], env=env, cwd=str(dossier),
                                       encoding_error_handler="replace")
    with open(os.devnull, "w", encoding="utf-8") as silence:
        async with stdio_client(parametres, errlog=silence) as (lecture, ecriture):
            async with ClientSession(lecture, ecriture) as session:
                await asyncio.wait_for(session.initialize(), delai)
                for outil, arguments in appels:
                    try:
                        r = await asyncio.wait_for(session.call_tool(outil, arguments), delai)
                    except Exception as e:  # noqa: BLE001 — un outil en erreur n'empêche pas les autres
                        resultat.erreurs[outil] = f"{type(e).__name__} : {e}"[:200]
                        continue
                    texte = "\n".join(getattr(b, "text", "") for b in getattr(r, "content", []) or [])
                    if getattr(r, "isError", False) or getattr(r, "is_error", False) or texte.startswith("ERREUR"):
                        resultat.erreurs[outil] = texte[:200] or "erreur de l'outil"
                    else:
                        resultat.textes[outil] = texte
    return resultat


def appeler_mcp(agent: reg.Agent, appels: list[tuple], dossier_config: Path = conf.DOSSIER,
                delai: float = DELAI_MCP_S) -> ResultatMCP:
    """Appelle des outils en lecture du serveur MCP de l'agent : [(outil, arguments)]. Lève SourceErreur si le
    serveur est absent ou ne démarre pas ; un outil en erreur est rapporté dans ``erreurs``."""
    interdits = [o for o, _ in appels if o not in LISTE_BLANCHE.get(agent.nom, ())]
    if interdits:
        raise SourceErreur(f"outil non autorisé pour le tableau de bord : {', '.join(interdits)}")
    commande = agent.ligne_mcp()
    if not commande:
        raise SourceErreur(f"{agent.titre} n'a pas de serveur MCP")
    if Path(commande[0]).is_absolute() and not Path(commande[0]).exists():
        raise SourceErreur(f"{agent.titre} : {Path(commande[0]).name} introuvable (venv à créer)")
    try:
        from mcp.client.stdio import get_default_environment     # PATH, SYSTEMROOT… sans l'environnement du manager
        env = {**get_default_environment(), **conf.variables_pour(agent, dossier_config), "PYTHONIOENCODING": "utf-8",
               "PYTHONUNBUFFERED": "1"}
    except (conf.ConfigurationErreur, OSError) as e:
        raise SourceErreur(f"configuration commune illisible : {e}") from e
    try:
        return asyncio.run(asyncio.wait_for(_appeler(commande, env, appels, agent.dossier, delai), delai * 3))
    except Exception as e:  # noqa: BLE001 — processus absent, protocole, délai dépassé…
        while isinstance(e, BaseExceptionGroup) and e.exceptions:
            e = e.exceptions[0]
        raise SourceErreur(f"serveur MCP de {agent.titre} injoignable ({type(e).__name__} : {e})"[:300]) from None


# --- analyse des textes des outils ------------------------------------------------------------------------------
_ANALYSE = re.compile(r"^- (?P<cle>[A-Z][A-Z0-9_]+-\d+) \(modifiée le (?P<date>[\d-]+ [\d:]+)\) : (?P<libelle>.*?)"
                      r"(?P<a_valider> ← à valider)?$")
_CLIENT = re.compile(r"^- (?P<client>[^:]+?) : (?P<etat>.+)$")
_LANCEE = re.compile(r"lancée le (\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?)")
_PATCH = re.compile(r"^- (?P<cle>[A-Z][A-Z0-9_]+-\d+) \(livré le (?P<date>[\d-]+ [\d:]+)\) : (?P<fichiers>.+?)"
                    r" — (?P<etat>.+)$")
_BUILD = re.compile(r"^\s+#(?P<numero>\d+) (?P<resultat>.+?) — (?P<debut>.+?), (?P<duree>\d+) min$")


def lire_analyses(texte: str) -> list[dict]:
    """[{cle, modifiee, libelle, a_valider}] depuis l'outil ``analyses`` du lecteur des données."""
    analyses = []
    for ligne in (texte or "").splitlines():
        m = _ANALYSE.match(ligne.strip())
        if m:
            analyses.append({"cle": m["cle"], "modifiee": m["date"], "libelle": m["libelle"],
                             "a_valider": bool(m["a_valider"])})
    return analyses


def lire_cucumber(texte: str) -> dict:
    """{"clients": [{client, en_cours, depuis, etat}], "jobs": [noms]} depuis l'outil ``cucumber`` ; ``depuis`` :
    début ISO de la génération en cours (verrou par client), vide sinon."""
    clients, jobs = [], []
    for ligne in (texte or "").splitlines():
        ligne = ligne.strip()
        if ligne.startswith("Jobs Jenkins :"):
            jobs = [j.split("=", 1)[-1].strip() for j in ligne.split(":", 1)[1].split(",") if j.strip()]
            continue
        m = _CLIENT.match(ligne)
        if m:
            etat = m["etat"].strip()
            debut = _LANCEE.search(etat)
            clients.append({"client": m["client"].strip(), "en_cours": etat.startswith("génération en cours"),
                            "depuis": debut[1].replace(" ", "T") if debut else "", "etat": etat})
    return {"clients": clients, "jobs": list(dict.fromkeys(jobs))}


def lire_builds(texte: str) -> list[dict]:
    """[{job, numero, resultat, en_cours, debut, duree_min}] depuis l'outil ``builds_jenkins``."""
    builds, job = [], ""
    for ligne in (texte or "").splitlines():
        if ligne and not ligne[0].isspace() and ligne.rstrip().endswith(" :"):
            job = ligne.rstrip()[:-2].strip()
            continue
        m = _BUILD.match(ligne)
        if m and job:
            builds.append({"job": job, "numero": int(m["numero"]), "resultat": m["resultat"],
                           "en_cours": m["resultat"] == "EN COURS", "debut": m["debut"],
                           "duree_min": int(m["duree"])})
    return builds


def lire_patchs(texte: str) -> list[dict]:
    """[{cle, livre_le, fichiers, statut, manquants, raison}] depuis l'outil ``patchs`` du lecteur des données ;
    ``statut`` : ``jointe`` (patch en pièce jointe du ticket Jira), ``non_jointe`` (``manquants`` : fichiers absents de Jira),
    ``non_verifiee`` (Jira non lu : ``raison``). Une ligne d'état inconnu est gardée en ``non_verifiee``."""
    patchs = []
    for ligne in (texte or "").splitlines():
        m = _PATCH.match(ligne.strip())
        if not m:
            continue
        etat = m["etat"].strip()
        fichiers = [f.strip() for f in m["fichiers"].split(",") if f.strip()]
        statut, manquants, raison = "non_verifiee", [], etat
        if etat == "en pièce jointe Jira":
            statut, raison = "jointe", ""
        elif etat.startswith("NON joint dans Jira"):
            statut, raison = "non_jointe", ""
            manquants = [f.strip() for f in etat.partition(":")[2].split(",") if f.strip()] or fichiers
        elif etat.startswith("pièce jointe non vérifiée"):
            raison = etat.removeprefix("pièce jointe non vérifiée").strip().removeprefix("(").removesuffix(")")
        patchs.append({"cle": m["cle"], "livre_le": m["date"], "fichiers": fichiers, "statut": statut,
                       "manquants": manquants, "raison": raison})
    return patchs


# --- fichiers locaux des agents ---------------------------------------------------------------------------------
def _nombre(valeur) -> float:
    try:
        return float(str(valeur).replace(",", ".")) if valeur not in ("", None) else 0.0
    except ValueError:
        return 0.0


def lire_token_usage(dossier_agent: Path, depuis: str = "", ticket: str = "") -> list[dict] | None:
    """Lignes de ``.tmp/token_usage.csv`` des agents (AGENT_DATA_DIR) : [{date, ticket, etape, cout_usd}], en lecture
    seule.
    None si le fichier n'existe pas (coût inconnu, pas nul) ; SourceErreur s'il est illisible."""
    chemin = Path(dossier_agent) / TOKEN_USAGE
    if not chemin.exists():
        return None
    try:
        with open(chemin, newline="", encoding="utf-8", errors="replace") as f:
            lignes = list(csv.DictReader(f, delimiter=";"))
    except (OSError, csv.Error) as e:
        raise SourceErreur(f"{chemin.name} illisible : {e}") from e
    resultat = []
    for l in lignes:
        date = (l.get("date") or "").strip()
        cle = (l.get("ticket") or "").strip().upper()
        if (depuis and date < depuis) or (ticket and cle != ticket.upper()):
            continue
        resultat.append({"date": date, "ticket": cle, "etape": (l.get("etape") or "?").strip() or "?",
                         "cout_usd": _nombre(l.get("cout_usd"))})
    return resultat


def couts_par_etape(lignes: list[dict]) -> list[dict]:
    """[{etape, appels, cout_usd}] trié par coût décroissant."""
    etapes: dict[str, dict] = {}
    for l in lignes:
        e = etapes.setdefault(l["etape"], {"etape": l["etape"], "appels": 0, "cout_usd": 0.0})
        e["appels"] += 1
        e["cout_usd"] += l["cout_usd"]
    return sorted(({**e, "cout_usd": round(e["cout_usd"], 4)} for e in etapes.values()),
                  key=lambda e: e["cout_usd"], reverse=True)


def cout_ticket_agent(registre: reg.Registre, cle: str, dossier: Path | None = None) -> float | None:
    """Coût cumulé connu d'un ticket chez les agents (token_usage.csv d'AGENT_DATA_DIR, ou ``dossier``) ; None si la
    source est absente. ``registre`` : gardé pour les appelants."""
    try:
        lignes = lire_token_usage(dossier or reg.dossier_donnees(), ticket=cle)
    except SourceErreur:
        return None
    return None if lignes is None else round(sum(l["cout_usd"] for l in lignes), 4)


def depuis_jours(jours: int) -> str:
    return (datetime.now() - timedelta(days=jours)).isoformat(timespec="seconds")


_BOUCLE = re.compile(r"^- (?P<generation>\S+) \[(?P<client>[^\]]*)\] : (?P<etat>[^·]+?) · itération (?P<i>\d+)/(?P<n>\d+)"
                     r" · échecs (?P<avant>\S+) → (?P<apres>\S+) · (?P<categories>[^·]+?) · coût (?P<cout>[\d.]+) \$"
                     r" · relance (?P<relance>oui|non) · mis à jour (?P<maj>\S*)\s*$")


def lire_boucles(texte: str) -> list[dict]:
    """[{generation, client, etat, iteration, iterations_max, echecs_avant, echecs_apres, categories, cout_usd,
    relance, mis_a_jour, raison, corrections, annulations, demande}] depuis l'outil ``boucles_cucumber``."""
    boucles = []
    for ligne in (texte or "").splitlines():
        m = _BOUCLE.match(ligne.strip())
        if m:
            categories = {}
            for morceau in m["categories"].split(","):
                nom, _, n = morceau.strip().rpartition(" ")
                if nom and n.isdigit():
                    categories[nom] = int(n)
            boucles.append({"generation": m["generation"], "client": m["client"], "etat": m["etat"].strip(),
                            "iteration": int(m["i"]), "iterations_max": int(m["n"]), "echecs_avant": m["avant"],
                            "echecs_apres": m["apres"], "categories": categories, "cout_usd": float(m["cout"]),
                            "relance": m["relance"] == "oui", "mis_a_jour": m["maj"], "raison": "",
                            "corrections": [], "annulations": [], "demande": ""})
            continue
        if not boucles or not ligne.startswith("  "):
            continue
        detail, b = ligne.strip(), boucles[-1]
        if detail.startswith("raison : "):
            b["raison"] = detail[len("raison : "):]
        elif detail.startswith("correction "):
            b["corrections"].append(detail[len("correction "):])
        elif detail.startswith("retour arrière "):
            b["annulations"].append(detail[len("retour arrière "):])
        elif detail.startswith("demande : "):
            b["demande"] = detail[len("demande : "):].replace(" / ", "\n")
    return boucles


_RETRO = re.compile(r"^- (?P<cle>[A-Z][A-Z0-9_]+-\d+) \(le (?P<date>[^)]*)\) : score (?P<score>\d+)/100"
                    r" · ajusté (?P<ajuste>\d+|—)/100 · écarts expliqués (?P<ecarts>\d+) · analyse (?P<analyse>\S+)\s*$")
_PROPOSITION = re.compile(r"^- (?P<id>\S+) \[(?P<levier>[^\]]*)\] : (?P<libelle>.*?) · tickets (?P<tickets>\d+)"
                          r" · en attente depuis (?P<depuis>.*?)\s*$")


def lire_retrospectives(texte: str) -> tuple[list[dict], list[dict]]:
    """([{cle, date, score, score_ajuste, ecarts, analyse}], [{id, levier, libelle, tickets, depuis}]) depuis l'outil
    ``retrospectives`` ; ([], []) si le format est inattendu."""
    retros, propositions, section = [], [], ""
    for ligne in (texte or "").splitlines():
        ligne = ligne.strip()
        if ligne in ("Rétrospectives :", "Propositions en attente :"):
            section = ligne
            continue
        m = _RETRO.match(ligne) if section == "Rétrospectives :" else _PROPOSITION.match(ligne) if section else None
        if not m:
            continue
        if section == "Rétrospectives :":
            retros.append({"cle": m["cle"], "date": m["date"], "score": int(m["score"]),
                           "score_ajuste": None if m["ajuste"] == "—" else int(m["ajuste"]),
                           "ecarts": int(m["ecarts"]), "analyse": m["analyse"] == "ok"})
        else:
            propositions.append({"id": m["id"], "levier": m["levier"], "libelle": m["libelle"],
                                 "tickets": int(m["tickets"]), "depuis": m["depuis"]})
    return retros, propositions
