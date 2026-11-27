"""
Consultations en LECTURE SEULE pour agent-manager-savoye (serveur MCP ``mcp_lecture.py``).

Le manager s'en sert pour répondre à une question rapide (« où en est l'analyse de ODA-1234 ? », « un job Cucumber
tourne-t-il ? ») sans ouvrir l'IHM. Rien ici n'écrit : ni Jira, ni Bitbucket, ni Jenkins, ni fichier de l'agent.
Tout le reste (cadrage, chiffrage, patch, Cucumber…) se fait dans l'IHM, vers laquelle le manager redirige.
"""
import glob
import os
import re
from datetime import datetime

import requests

from agent_socle import publication
from agent_socle import verrou
from manager import assistant
from agent_socle import livraisons
from agent_socle.jenkins_tools import recent_builds
from agent_socle.secret_tools import mask_secrets
from agent_socle.chemins import DATA_DIR

WORKSPACE_DIR = DATA_DIR  # données (.tmp, fichiers produits) : AGENT_DATA_DIR
CLE = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")
JOBS = ("JENKINS_CUCUMBER_JOB", "CUCUMBER_VERIFY_JOB", "CUCUMBER_RUN_JOB")
ANALYSES_MAX = 30
PATCHS_JOURS_MAX = 90


class LectureErreur(Exception):
    pass


def _masque(texte: str) -> str:
    return mask_secrets(texte)[0]


def _cle(ticket: str) -> str:
    cle = (ticket or "").strip().upper()
    if not CLE.match(cle):
        raise LectureErreur(f"Clé de ticket invalide : « {ticket} » (format attendu : PROJET-123).")
    return cle


def _date(horodatage: float) -> str:
    return datetime.fromtimestamp(horodatage).strftime("%Y-%m-%d %H:%M")


def analyses(racine: str = WORKSPACE_DIR) -> str:
    """Analyses (cadrages) présentes dans l'agent, de la plus récente à la plus ancienne, avec leur état."""
    fichiers = sorted(glob.glob(os.path.join(racine, "*-cadrage.md")), key=os.path.getmtime, reverse=True)
    if not fichiers:
        return "Aucune analyse dans les données des agents (agent-lancement)."
    lignes = []
    for chemin in fichiers[:ANALYSES_MAX]:
        cle = os.path.basename(chemin)[:-len("-cadrage.md")]
        with open(chemin, encoding="utf-8", errors="replace") as f:
            etat = publication.status(cle, f.read())
        a_valider = "" if etat["publiee"] else " ← à valider"
        lignes.append(f"- {cle} (modifiée le {_date(os.path.getmtime(chemin))}) : {etat['libelle']}{a_valider}")
    if len(fichiers) > ANALYSES_MAX:
        lignes.append(f"… {len(fichiers) - ANALYSES_MAX} analyse(s) plus ancienne(s)")
    return "Analyses (agent-lancement) :\n" + "\n".join(lignes)


def analyse(ticket: str, racine: str = WORKSPACE_DIR) -> str:
    """État de l'analyse d'un ticket et ses titres de sections."""
    cle = _cle(ticket)
    chemin = os.path.join(racine, publication.cadrage_path(cle))
    if not os.path.exists(chemin):
        return f"Aucune analyse de {cle} dans les données des agents (agent-lancement)."
    with open(chemin, encoding="utf-8", errors="replace") as f:
        texte = f.read()
    etat = publication.status(cle, texte)
    titres = [l.lstrip("#").strip() for l in texte.splitlines() if l.startswith("#")][:20]
    return _masque(f"Analyse de {cle} (modifiée le {_date(os.path.getmtime(chemin))}) : {etat['libelle']}.\n"
                   f"Sections : {' · '.join(titres) or '(aucun titre)'}")


def ticket(cle: str) -> str:
    """Titre, type, statut, responsable et épopée d'un ticket Jira (lecture ; compte de lecture s'il existe)."""
    cle = _cle(cle)
    domaine = os.getenv("JIRA_DOMAIN", "").replace("https://", "").replace("http://", "").strip("/")
    lecture = os.getenv("JIRA_READ_API_TOKEN", "").strip()
    email = (os.getenv("JIRA_READ_EMAIL") if lecture else os.getenv("JIRA_EMAIL", "")) or ""
    jeton = lecture or os.getenv("JIRA_API_TOKEN", "").strip()
    if not (domaine and email.strip() and jeton):
        raise LectureErreur("Jira n'est pas configuré dans le .env des agents (AGENT_DATA_DIR).")
    try:
        reponse = requests.get(f"https://{domaine}/rest/api/3/issue/{cle}", auth=(email.strip(), jeton), timeout=15,
                               headers={"Accept": "application/json"},
                               params={"fields": "summary,status,issuetype,assignee,parent,updated"})
    except requests.RequestException as e:
        raise LectureErreur(f"Jira injoignable : {_masque(str(e))[:200]}")
    if reponse.status_code != 200:
        raise LectureErreur(f"Ticket {cle} illisible dans Jira (HTTP {reponse.status_code}).")
    champs = reponse.json().get("fields", {})
    parent = champs.get("parent") or {}
    lignes = [f"{cle} : {champs.get('summary', '')}",
              f"Type : {(champs.get('issuetype') or {}).get('name', '')} · Statut : "
              f"{(champs.get('status') or {}).get('name', '')} · Responsable : "
              f"{(champs.get('assignee') or {}).get('displayName', 'non assigné')} · Mis à jour : "
              f"{str(champs.get('updated', ''))[:16].replace('T', ' ')}"]
    if parent:
        lignes.append(f"Épopée : {parent.get('key')} {(parent.get('fields') or {}).get('summary', '')}")
    return _masque("\n".join(lignes) + "\n" + analyse(cle))


def cucumber() -> str:
    """Clients Cucumber connus de l'agent et génération en cours pour chacun (verrou par client)."""
    clients = assistant.known_clients()
    clients = [c for c in clients if not c.startswith("_")]
    if not clients:
        return "Aucun client Cucumber dans les données des agents (agent-cucumber)."
    lignes = []
    for client in clients:
        detenteur = verrou.holder(client)
        lignes.append(f"- {client} : {verrou.describe(detenteur) if detenteur else 'aucune génération en cours'}")
    jobs = [f"{n} = {os.getenv(n)}" for n in JOBS if os.getenv(n, "").strip()]
    return _masque("Cucumber (agent-cucumber) :\n" + "\n".join(lignes)
                   + ("\nJobs Jenkins : " + ", ".join(jobs) if jobs else "\nAucun job Jenkins configuré."))


def builds(job: str = "", nombre: int = 5) -> str:
    """Derniers builds des jobs Cucumber configurés (un seul build Cucumber à la fois sur Jenkins)."""
    configures = [os.getenv(n, "").strip() for n in JOBS if os.getenv(n, "").strip()]
    configures = list(dict.fromkeys(configures))
    if job and job not in configures:
        raise LectureErreur(f"Job inconnu de l'agent : « {job} » (jobs configurés : {', '.join(configures) or 'aucun'}).")
    nombre = max(1, min(int(nombre or 5), 20))
    blocs = []
    for nom in [job] if job else configures:
        lignes = []
        for b in recent_builds(nom, nombre):
            debut = _date(b["debut"] / 1000) if b.get("debut") else "?"
            resultat = b.get("resultat") or "EN COURS"
            lignes.append(f"  #{b['numero']} {resultat} — {debut}, {round((b.get('duree') or 0) / 60000)} min")
        blocs.append(f"{nom} :\n" + ("\n".join(lignes) or "  (aucun build lisible)"))
    return _masque("\n".join(blocs) or "Aucun job Jenkins configuré dans le .env des agents.")


def patchs(jours: int = 7, chemin: str = "") -> str:
    """Patchs livrés ces derniers jours, d'après le registre local des livraisons écrit par l'étape patch.

    Format (contrat avec agent-manager-savoye, une ligne par livraison, de la plus récente à la plus ancienne) :
    ``- <CLÉ> (livré le AAAA-MM-JJ HH:MM) : <fichier.patch>[, …] — <état>`` où l'état est
    ``en pièce jointe Jira``, ``NON joint dans Jira : <fichiers>`` ou ``pièce jointe non vérifiée (<raison>)``.
    """
    try:
        jours = max(1, min(int(jours or 7), PATCHS_JOURS_MAX))
    except (TypeError, ValueError):
        raise LectureErreur(f"Nombre de jours invalide : « {jours} ».")
    try:
        entrees = livraisons.lire(jours, chemin or livraisons.FICHIER)
    except livraisons.RegistreErreur as e:
        raise LectureErreur(str(e))
    if not entrees:
        return (f"Aucun patch livré ces {jours} derniers jours (registre des livraisons, "
                f"rempli à chaque étape patch).")
    lignes = []
    for e in entrees:
        manquants = [f for f in e["fichiers"] if f not in e["pieces_jointes"]]
        if e["verification"] != "ok":
            etat = f"pièce jointe non vérifiée ({e['verification'] or 'raison inconnue'})"
        elif manquants:
            etat = "NON joint dans Jira : " + ", ".join(manquants)
        else:
            etat = "en pièce jointe Jira"
        lignes.append(f"- {e['ticket']} (livré le {e['date'][:16].replace('T', ' ')}) : {', '.join(e['fichiers'])}"
                      f" — {etat}")
    return _masque(f"Patchs livrés ces {jours} derniers jours (agent-lancement) :\n" + "\n".join(lignes))


BOUCLES_MAX = 5
ETATS_BOUCLE = {"en_cours": "en cours", "terminee": "terminée", "abandonnee": "abandonnée"}


def boucles(racine: str = WORKSPACE_DIR, nombre: int = BOUCLES_MAX) -> str:
    """Boucles de correction autonome d'agent-cucumber (.tmp/cucumber/<client>/boucles/<génération>.json), de la
    plus récente à la plus ancienne. Information seulement : aucune validation n'est attendue.

    Format (contrat avec agent-manager-savoye, sources.lire_boucles), par boucle :
    ``- <génération> [<client>] : <état> · itération <i>/<N> · échecs <avant> → <après> · AGENT <n>, APPLICATION <n>,
    ENVIRONNEMENT <n>, INCERTAIN <n> · coût <x.xx> $ · relance <oui|non> · mis à jour <ISO>`` puis, indentées :
    ``raison : …``, ``correction it<i> <commit> : <cause>``, ``retour arrière it<i> <commit> → <revert>``,
    ``demande : <demande prête à coller, sur une ligne>``.
    """
    import json
    fichiers = sorted(glob.glob(os.path.join(racine, ".tmp", "cucumber", "*", "boucles", "*.json")),
                      key=os.path.getmtime, reverse=True)[:max(1, min(int(nombre or BOUCLES_MAX), 20))]
    if not fichiers:
        return "Aucune boucle de correction Cucumber."
    lignes = []
    for chemin in fichiers:
        try:
            with open(chemin, encoding="utf-8") as f:
                b = json.load(f)
        except (OSError, ValueError):
            continue
        final = b.get("final") or {}
        categories = final.get("par_categorie") or (b.get("initial") or {}).get("par_categorie") or {}
        apres = final.get("echecs", "?") if final else "?"
        lignes.append(
            f"- {b.get('generation', '?')} [{b.get('projet', '?')}] : {ETATS_BOUCLE.get(b.get('etat'), b.get('etat'))}"
            f" · itération {b.get('iteration', 0)}/{b.get('iterations_max', 0)}"
            f" · échecs {(b.get('initial') or {}).get('echecs', '?')} → {apres}"
            f" · " + ", ".join(f"{c} {categories.get(c, 0)}" for c in ("AGENT", "APPLICATION", "ENVIRONNEMENT",
                                                                         "INCERTAIN"))
            + f" · coût {float(b.get('cout_usd') or 0):.2f} $ · relance {'oui' if b.get('relance') else 'non'}"
            f" · mis à jour {b.get('mis_a_jour', '')}")
        if b.get("raison"):
            lignes.append(f"  raison : {' '.join(str(b['raison']).split())}")
        for c in b.get("corrections") or []:
            lignes.append(f"  correction it{c.get('iteration')} {str(c.get('commit', ''))[:10]} : "
                          f"{' '.join(str(c.get('cause', '')).split())[:200]}")
        for a in b.get("annulations") or []:
            lignes.append(f"  retour arrière it{a.get('iteration')} {str(a.get('commit', ''))[:10]} → "
                          f"{str(a.get('revert', ''))[:10]}")
        if b.get("demande_texte"):
            lignes.append(f"  demande : {' / '.join(str(b['demande_texte']).splitlines())[:1400]}")
    return _masque("Boucles de correction Cucumber :\n" + "\n".join(lignes))


RETROS_MAX = 10
_SEPARATEUR = re.compile(r"\s*·\s*")


def _ligne(texte, limite: int = 150) -> str:
    """Texte d'une valeur sur une ligne, sans le séparateur « · » du format."""
    return _SEPARATEUR.sub(", ", " ".join(str(texte or "").split()))[:limite]


def retrospectives(racine: str = WORKSPACE_DIR, nombre: int = RETROS_MAX) -> str:
    """Rétrospectives d'agent-retrospective (.tmp/retrospectives/<CLÉ>.json, de la plus récente à la plus ancienne)
    et propositions d'amélioration d'agent-lancement en attente de validation (propositions.json).

    Format (contrat avec agent-manager-savoye, sources.lire_retrospectives) :
    ``Rétrospectives :`` puis une ligne par ticket
    ``- <CLÉ> (le AAAA-MM-JJ HH:MM) : score <n>/100 · ajusté <n|—>/100 · écarts expliqués <k> · analyse <ok|indisponible>``
    (ou ``- aucune``), puis ``Propositions en attente :`` et une ligne par proposition
    ``- <id> [<levier>] : <libellé> · tickets <k> · en attente depuis AAAA-MM-JJ HH:MM`` (ou ``- aucune``).
    « en attente depuis » : date du dernier report, sinon de création.
    """
    import json
    try:
        nombre = max(1, min(int(nombre or RETROS_MAX), 50))
    except (TypeError, ValueError):
        raise LectureErreur(f"Nombre invalide : « {nombre} ».")
    dossier = os.path.join(racine, ".tmp", "retrospectives")
    rapports = []
    for chemin in glob.glob(os.path.join(dossier, "*.json")):
        if not CLE.match(os.path.splitext(os.path.basename(chemin))[0]):
            continue
        try:
            with open(chemin, encoding="utf-8") as f:
                r = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(r, dict) and CLE.match(str(r.get("ticket", ""))):
            rapports.append(r)
    rapports.sort(key=lambda r: str(r.get("date", "")), reverse=True)
    lignes = ["Rétrospectives :"]
    for r in rapports[:nombre]:
        analyse = r.get("analyse") if isinstance(r.get("analyse"), dict) else None
        ajuste = r.get("score_ajuste")
        lignes.append(f"- {r['ticket']} (le {str(r.get('date', ''))[:16].replace('T', ' ')}) : "
                      f"score {int(r.get('score') or 0)}/100 · ajusté {ajuste if isinstance(ajuste, int) else '—'}/100"
                      f" · écarts expliqués {len((analyse or {}).get('ecarts') or [])}"
                      f" · analyse {'ok' if analyse else 'indisponible'}")
    if len(lignes) == 1:
        lignes.append("- aucune")
    try:
        with open(os.path.join(dossier, "propositions.json"), encoding="utf-8") as f:
            props = (json.load(f) or {}).get("propositions") or {}
    except (OSError, ValueError, AttributeError):
        props = {}
    attente = sorted((p for p in props.values() if isinstance(p, dict) and p.get("etat") == "proposee"),
                     key=lambda p: str(p.get("reportee_le") or p.get("cree_le") or ""))
    lignes.append("Propositions en attente :")
    for p in attente:
        depuis = str(p.get("reportee_le") or p.get("cree_le") or "")[:16].replace("T", " ")
        lignes.append(f"- {_ligne(p.get('id'), 40)} [{_ligne(p.get('levier'), 40)}] : {_ligne(p.get('libelle'))}"
                      f" · tickets {len(p.get('tickets') or [])} · en attente depuis {depuis}")
    if not attente:
        lignes.append("- aucune")
    return _masque("\n".join(lignes))
