"""Onglet Configuration › Réglages des agents : .env partagé d'AGENT_DATA_DIR et mémoire des agents.

Repris tel quel de la page « Configuration (.env) » d'agent-scrum-master (supprimé), qui écrivait ce même fichier ;
seules différences :
- le .env est lu dans un dictionnaire (_V), jamais chargé dans l'environnement du manager : ses valeurs (compte
  Claude, clés) ne se mêlent pas à celles du manager et de son assistant ;
- les variables de la configuration commune du manager (_G : accès et secrets transmis aux agents) l'emportent,
  sont affichées comme telles et ne sont jamais réécrites dans le .env (comme quand scrum-master était lancé par le
  manager) ;
- les secrets ne sont jamais renvoyés au navigateur : champ vide = valeur gardée, saisie = remplacement (comportement
  du socle quand la connexion OIDC est active ; l'IHM du manager n'a pas de connexion) ;
- le composant Groundcover du socle (agent_socle.ihm_groundcover) lit cette même vue (os, config_commune et
  auth_tools remplacés dans son seul module, le temps de l'appel ; il n'est utilisé par rien d'autre dans le
  manager).
"""

from __future__ import annotations

import getpass
import glob
import json
import os
import shutil
import subprocess
from types import SimpleNamespace
from unittest import mock

import streamlit as st
from dotenv import dotenv_values

from agent_socle import env_tools, ihm_groundcover, knowledge_tools, reglages
from agent_socle.db_tools import DB_TYPES
from agent_socle.memory_tools import (load_ticket_memory, get_all_memories, clear_memory, delete_ticket_memory,
                                      import_existing_deliverables)

from manager import configuration as conf
from manager import registre as reg

DOSSIER = reg.dossier_donnees()
ENV_PATH = str(DOSSIER / ".env")
_V: dict = {}        # valeurs affichées : .env partagé, puis variables transmises par le manager
_G: dict = {}        # variables transmises par le manager (configuration commune) : jamais réécrites


def _charger() -> None:
    """Relit le .env partagé et la configuration commune du manager (à chaque affichage)."""
    try:
        commun, secrets = conf.charger(), conf.secrets()
        geres = {k: v for k, v in {**commun.acces, **secrets}.items() if v}
    except Exception:  # noqa: BLE001 — configuration commune illisible : rien n'est géré par le manager
        geres = {}
    env = {k: v or "" for k, v in dotenv_values(ENV_PATH).items()} if os.path.exists(ENV_PATH) else {}
    _G.clear()
    _G.update(geres)
    _V.clear()
    _V.update({**env, **geres})


def _get(nom: str, defaut=None):
    valeur = _V.get(nom)
    return defaut if valeur is None else valeur


def env_int(name: str, default: int) -> int:
    try:
        return int((_get(name, "") or "").strip() or default)
    except ValueError:
        return default


def secret_input(name: str, label: str = "", **kwargs) -> str:
    """Secret du .env : jamais renvoyé au navigateur ; champ vide = valeur gardée, saisie = remplacement."""
    current = _get(name, "")
    entered = st.text_input(label or name, value="", type="password", key=f"reglages_secret_{name}",
                            placeholder="renseigné — non affiché (saisir pour remplacer)" if current else "", **kwargs)
    return entered or current


def _agent_runs_tests() -> bool:
    """reglages.agent_runs_tests() lu dans le .env partagé (CUCUMBER_AGENT_JENKINS, sinon accès Jenkins renseignés)."""
    valeur = (_get("CUCUMBER_AGENT_JENKINS", "") or "").strip()
    if valeur in ("0", "1"):
        return valeur == "1"
    return bool((_get("JENKINS_USER", "") or "").strip() and (_get("JENKINS_API_TOKEN", "") or "").strip())


def _auteur() -> str:
    return (_get("AGENT_KNOWLEDGE_AUTHOR", "") or "").strip() or getpass.getuser()


def _groundcover(fonction, *args, **kwargs):
    """Composant Groundcover du socle, avec la vue du .env partagé à la place de l'environnement du processus."""
    vue_os = SimpleNamespace(getenv=_get, environ=os.environ, path=os.path)
    vue_commune = SimpleNamespace(geree=lambda nom: nom in _G, gerees=lambda: sorted(_G))
    vue_auth = SimpleNamespace(secret_field=lambda _st, nom, label="", **kw: secret_input(nom, label, **kw))
    with mock.patch.object(ihm_groundcover, "os", vue_os), \
            mock.patch.object(ihm_groundcover, "config_commune", vue_commune), \
            mock.patch.object(ihm_groundcover, "auth_tools", vue_auth):
        return fonction(*args, **kwargs)


def _url_agent(nom: str, defaut: str) -> str:
    try:
        return reg.charger().agent(nom).url
    except reg.RegistreErreur:
        return defaut


def claude_config_dirs() -> list:
    """Dossiers de configuration Claude Code du poste (un par compte) : ~/.claude et ~/.claude-*."""
    home = os.path.expanduser("~")
    dirs = [os.path.join(home, ".claude")] + sorted(glob.glob(os.path.join(home, ".claude-*")))
    return [d for d in dirs if os.path.isdir(d)]


@st.cache_data(ttl=300, show_spinner=False)
def claude_account(config_dir: str) -> str:
    """Compte connecté dans ce dossier de configuration (`claude auth status`), ou '' s'il est inconnu."""
    env = os.environ.copy()
    env.pop("CLAUDE_CONFIG_DIR", None)
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(config_dir)
    try:
        result = subprocess.run([shutil.which("claude") or "claude", "auth", "status"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", env=env, timeout=20,
                                shell=(os.name == "nt"))
        status = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    if not status.get("loggedIn"):
        return "non connecté"
    return " · ".join(v for v in (status.get("email"), status.get("orgName")) if v)


def afficher() -> None:
    _charger()
    if not os.path.exists(ENV_PATH):
        st.warning(f"{ENV_PATH} introuvable : vérifier AGENT_DATA_DIR. Le formulaire créera le fichier.",
                   icon=":material/warning:")
    _formulaire()
    st.divider()
    _memoire()


def _formulaire() -> None:
    st.subheader("Réglages des agents (.env partagé)")
    st.caption(f"Fichier : {ENV_PATH} (AGENT_DATA_DIR), lu par agent-lancement, agent-cucumber, agent-integration, "
               "agent-revue-pr… Repris de la page Configuration d'agent-scrum-master (supprimé).")
    st.caption("Chiffrage des analyses par projet : réglages dans l'IHM Lancement (8506).")
    st.caption("Modifiez l'ensemble des variables d'environnement utilisées par les agents. "
               "Le fichier .env.example d'agent-socle donne les valeurs usuelles.")
    if _G:
        st.info("Transmises aux agents par le manager : ces variables viennent de sa configuration commune et se "
                "modifient dans l'onglet Accès ; elles ne sont pas réécrites dans le .env : "
                + ", ".join(sorted(_G)) + ".", icon=":material/hub:")

    with st.form("env_form_global"):
        # Valeurs enregistrées dans le .env à la validation du formulaire
        env_values = {}

        st.markdown("#### 📌 Identifiants Jira")
        env_values["JIRA_DOMAIN"] = st.text_input("JIRA_DOMAIN", value=_get("JIRA_DOMAIN", ""),
                                                  help="Ex: monentreprise.atlassian.net")
        env_values["JIRA_EMAIL"] = st.text_input("JIRA_EMAIL", value=_get("JIRA_EMAIL", ""))
        env_values["JIRA_API_TOKEN"] = secret_input("JIRA_API_TOKEN",
                                                     help="À créer sur https://id.atlassian.com/manage-profile/security/api-tokens")
        env_values["JIRA_PROJECT_KEY"] = st.text_input("JIRA_PROJECT_KEY (Par défaut)", value=_get("JIRA_PROJECT_KEY", ""))
        with st.container(horizontal=True):
            env_values["JIRA_READ_EMAIL"] = st.text_input(
                "JIRA_READ_EMAIL (facultatif)", value=_get("JIRA_READ_EMAIL", ""),
                help="Compte Jira en lecture seule transmis à l'agent pendant l'analyse. Vide : l'agent n'a aucun "
                     "jeton Jira pendant l'analyse (le contexte du ticket lui est fourni dans le prompt).")
            env_values["JIRA_READ_API_TOKEN"] = secret_input(
                "JIRA_READ_API_TOKEN", help="Jeton du compte JIRA_READ_EMAIL, sans droit d'écriture.")

        st.markdown("---")
        st.markdown("#### 🔎 Recherche Jira (valeurs par défaut de l'onglet Lancement)")
        with st.container(horizontal=True):
            env_values["JIRA_SEARCH_TEAM"] = st.text_input(
                "JIRA_SEARCH_TEAM", value=_get("JIRA_SEARCH_TEAM", ""),
                help="Équipe / composant pré-rempli dans la recherche (composant Jira ou équipe Atlassian).")
            env_values["JIRA_SEARCH_STATUSES"] = st.text_input(
                "JIRA_SEARCH_STATUSES", value=_get("JIRA_SEARCH_STATUSES", ""),
                help="Statuts pré-remplis, séparés par une virgule. Ex: Analysée, Analysé")
            env_values["JIRA_SEARCH_LABELS"] = st.text_input(
                "JIRA_SEARCH_LABELS", value=_get("JIRA_SEARCH_LABELS", ""),
                help="Labels pré-remplis, séparés par une virgule")
        with st.container(horizontal=True):
            env_values["JIRA_OPEN_EPICS_TEAM"] = st.text_input(
                "JIRA_OPEN_EPICS_TEAM", value=_get("JIRA_OPEN_EPICS_TEAM", ""),
                help="Équipe Atlassian de la recherche rapide « Épopées ouvertes de mon équipe ». Ex: oda_Prenom_N")
            env_values["JIRA_OPEN_EPICS_STATUS"] = st.text_input(
                "JIRA_OPEN_EPICS_STATUS", value=_get("JIRA_OPEN_EPICS_STATUS", ""),
                help="Statut des épopées de cette recherche rapide. Ex: Ouvert")
        with st.container(horizontal=True):
            env_values["JIRA_APPLICATION_FIELD"] = st.text_input(
                "JIRA_APPLICATION_FIELD", value=_get("JIRA_APPLICATION_FIELD", ""),
                help="Champ Jira « Projet / Application » (ex. customfield_10268). Onglet Intégration : l'agent "
                     "consulte tous les tickets ayant la même valeur que le ticket (ex. APPLI). Vide = désactivé.")
            application_max = st.number_input(
                "JIRA_APPLICATION_MAX_TICKETS", min_value=0, step=50,
                value=env_int("JIRA_APPLICATION_MAX_TICKETS", 300),
                help="Nombre maximal de tickets de référence (les plus récemment mis à jour).")
        env_values["JIRA_APPLICATION_MAX_TICKETS"] = str(int(application_max))

        st.markdown("---")
        st.markdown("#### 🎫 Tickets Jira créés par l'agent (tâches d'épopée)")
        with st.container(horizontal=True):
            env_values["POSTMERGE_JIRA_COMPONENT"] = st.text_input(
                "POSTMERGE_JIRA_COMPONENT", value=_get("POSTMERGE_JIRA_COMPONENT", ""),
                help="Composant Jira ou équipe Atlassian posé sur les tâches créées sous une épopée. Vide = aucun.")
            env_values["JIRA_EPIC_TASK_ISSUE_TYPE"] = st.text_input(
                "JIRA_EPIC_TASK_ISSUE_TYPE", value=_get("JIRA_EPIC_TASK_ISSUE_TYPE", ""),
                help="Type des tâches créées sous une épopée. Vide = premier type existant parmi Tâche, Task, "
                     "Development, Story.")
            env_values["JIRA_TEAM_FIELD"] = st.text_input(
                "JIRA_TEAM_FIELD", value=_get("JIRA_TEAM_FIELD", "customfield_10001"),
                help="Champ Jira de l'équipe Atlassian (Team).")

        st.markdown("---")
        st.markdown("#### 🐙 Connexion Bitbucket / KISCloud")
        env_values["BITBUCKET_URL"] = st.text_input("BITBUCKET_URL", value=_get("BITBUCKET_URL", ""),
                                                    help="Ex: https://git.kiscloud.io")
        env_values["BITBUCKET_HTTP_TOKEN"] = secret_input("BITBUCKET_HTTP_TOKEN",
                                                           help="Jeton d'accès HTTP personnel Bitbucket (lecture des dépôts).")
        env_values["BITBUCKET_WRITE_TOKEN"] = secret_input(
            "BITBUCKET_WRITE_TOKEN", help="Jeton réservé aux écritures de l'onglet « Cucumber » sur le dépôt "
                                          "<client>-cucumber (création du dépôt, branche de la version, pushes). "
                                          "BITBUCKET_HTTP_TOKEN reste en lecture.")
        env_values["REVIEW_PROJECTS"] = st.text_input(
            "REVIEW_PROJECTS", value=_get("REVIEW_PROJECTS", ""), placeholder="OXY, APPAREL",
            help="Projets Bitbucket proposés dans la vue « Par projet » de l'onglet Revue de PR (toutes leurs PR "
                 "ouvertes).").strip().upper()

        st.markdown("---")
        st.markdown("#### 🏗️ Connexion Jenkins (Cucumber / Tirs de tests)")
        env_values["JENKINS_URL"] = st.text_input("JENKINS_URL", value=_get("JENKINS_URL", ""),
                                                  help="Ex: https://jenkins.kiscloud.io")
        env_values["JENKINS_USER"] = st.text_input("JENKINS_USER", value=_get("JENKINS_USER", ""))
        env_values["JENKINS_API_TOKEN"] = secret_input("JENKINS_API_TOKEN")
        with st.container(horizontal=True):
            env_values["MAVEN_REPOSITORY_URL"] = st.text_input(
                "MAVEN_REPOSITORY_URL", value=_get("MAVEN_REPOSITORY_URL", ""),
                help="Dépôt Maven où est publié l'artefact monté par le job (maven-metadata.xml) : sa date de "
                     "publication, comparée aux commits des tâches, dit quel code a été testé.")
            env_values["MAVEN_REPOSITORY_USER"] = st.text_input(
                "MAVEN_REPOSITORY_USER", value=_get("MAVEN_REPOSITORY_USER", ""))
            env_values["MAVEN_REPOSITORY_PASSWORD"] = secret_input(
                "MAVEN_REPOSITORY_PASSWORD")
        env_values["JENKINS_CUCUMBER_JOB"] = st.text_input("JENKINS_CUCUMBER_JOB", value=_get("JENKINS_CUCUMBER_JOB", ""),
                                                           help="Job pré-rempli après génération d'un patch. "
                                                                "Ex: ODATIO_CUCUMBER_NIGHTLY")

        st.markdown("---")
        st.markdown("#### 📁 Dépôts, Conventions & Prompts")
        env_values["BITBUCKET_STD_REPO_PATH"] = st.text_input(
            "BITBUCKET_STD_REPO_PATH (Lien ou chemin du Socle Standard)",
            value=_get("BITBUCKET_STD_REPO_PATH", ""),
            help="URL du projet Bitbucket/KISCloud (ex: https://git.kiscloud.io/projects/COMPONENT) "
                 "ou chemin local d'un clone (ex: C:\\development\\branch_dev)."
        )
        env_values["STD_FETCH_MAX_AGE_MINUTES"] = str(int(st.number_input(
            "STD_FETCH_MAX_AGE_MINUTES (réutilisation des copies du Standard)", min_value=0, step=60,
            value=env_int("STD_FETCH_MAX_AGE_MINUTES", 1440),
            help="Les copies locales du Standard (lues par Claude pendant les analyses) ne sont récupérées de "
                 "nouveau que si leur dernier fetch date de plus de ce délai, en minutes. 1440 = un jour ; "
                 "0 = à chaque analyse.")))
        env_values["ODATIO_VERSION"] = st.text_input(
            "ODATIO_VERSION (facultatif)", value=_get("ODATIO_VERSION", ""), placeholder="ex. 26.16",
            help="Repli seulement : chaque client Cucumber a sa propre version du Standard, détectée dans le code de "
                 "son application, ses builds Jenkins et sa bibliothèque Cucumber (profil client, « Données du "
                 "projet »). Vide : lue dans le pom du Socle Standard (propriété odatio / kis, parent Odatio) ; une "
                 "version de dépôt comme 1.0.0 est ignorée.").strip()
        env_values["BITBUCKET_CLIENT_REPO_PATH"] = st.text_input(
            "BITBUCKET_CLIENT_REPO_PATH (Dépôt client par défaut)",
            value=_get("BITBUCKET_CLIENT_REPO_PATH", ""),
            help="Pré-remplit le dépôt client de l'onglet Lancement. "
                 "Ex: https://git.kiscloud.io/projects/CLIENT ou C:\\development\\CLIENT"
        )
        env_values["BITBUCKET_CUCUMBER_STD_REPO"] = st.text_input(
            "BITBUCKET_CUCUMBER_STD_REPO",
            value=_get("BITBUCKET_CUCUMBER_STD_REPO", ""),
            help="URL du dépôt Bitbucket contenant les scénarios Cucumber du socle Standard."
        )
        env_values["DEV_CONVENTIONS_PATH"] = st.text_input(
            "DEV_CONVENTIONS_PATH",
            value=_get("DEV_CONVENTIONS_PATH", ""),
            help="Fichier Markdown des conventions de développement. "
                 "Vide = odatio-specific-code-conventions-dev.md du projet."
        )
        env_values["ODATIO_DOCS_URL"] = st.text_input(
            "ODATIO_DOCS_URL",
            value=_get("ODATIO_DOCS_URL", ""),
            help="Page d'accueil de la documentation développeur ODATiO (https), consultée en lecture seule par "
                 "l'agent (cadrage, chiffrage, patch, revue de PR, tests Cucumber), limitée à ce site. "
                 "Vide = documentation non consultée."
        )
        env_values["PROMPTS_DIR"] = st.text_input(
            "PROMPTS_DIR",
            value=_get("PROMPTS_DIR", ""),
            help="Dossier de prompts personnalisés : un fichier <nom>.md présent ici remplace le modèle du même nom "
                 "du dossier prompts/ du projet. Vide = modèles du projet."
        )

        st.markdown("---")
        st.markdown("#### 🥒 Tests Cucumber d'un client (onglet Cucumber)")
        with st.container(horizontal=True):
            cucumber_batch = st.number_input(
                "CUCUMBER_BATCH_SIZE", min_value=1, step=1, value=env_int("CUCUMBER_BATCH_SIZE", 3),
                help="Nombre de spécifiques par lot : un appel Claude neuf par lot.")
            cucumber_turns = st.number_input(
                "CUCUMBER_MAX_TURNS", min_value=5, step=5, value=env_int("CUCUMBER_MAX_TURNS", 60),
                help="Nombre maximal de tours de l'agent par lot (--max-turns). À 75 %, l'agent est invité à "
                     "finaliser ce qu'il a écrit et à rendre son JSON final.")
        env_values["CUCUMBER_BATCH_SIZE"] = str(int(cucumber_batch))
        env_values["CUCUMBER_MAX_TURNS"] = str(int(cucumber_turns))
        with st.container(horizontal=True):
            env_values["CUCUMBER_MAX_BUDGET_USD"] = st.text_input(
                "CUCUMBER_MAX_BUDGET_USD", value=_get("CUCUMBER_MAX_BUDGET_USD", ""), placeholder="Vide = aucun",
                help="Budget maximal par appel Claude d'un lot, d'une correction ou d'une revue (--max-budget-usd). "
                     "Dépassement : lot en échec « budget atteint », fichiers annulés, repris par « Reprendre ».")
            inline_max = st.number_input(
                "CUCUMBER_INLINE_MAX_CHARS", min_value=0, step=2000,
                value=env_int("CUCUMBER_INLINE_MAX_CHARS", reglages.DEFAULT_INLINE_MAX_CHARS),
                help="Taille maximale (caractères) du contenu injecté dans le prompt d'un lot au lieu d'être lu par "
                     "l'agent : partie fixe (sommaire du catalogue, Step Definitions exemplaire, exemples des "
                     "recettes, runner à créer) et, séparément, extraits Standard et Jira du lot. Au-delà, le "
                     "chemin est donné pour lecture. 0 = rien d'injecté.")
        env_values["CUCUMBER_INLINE_MAX_CHARS"] = str(int(inline_max))
        env_values["CUCUMBER_INLINE_STUDIO_LINES"] = str(int(st.number_input(
            "CUCUMBER_INLINE_STUDIO_LINES", min_value=0, step=10,
            value=env_int("CUCUMBER_INLINE_STUDIO_LINES", reglages.DEFAULT_INLINE_STUDIO_LINES),
            help="Lignes au plus, par customized.xml, des parties modifiées (nœuds modifiés et leur contexte) "
                 "injectées dans le prompt du lot. Le reste du fichier est lu à la demande. 0 = rien d'injecté.")))
        env_values["CUCUMBER_INLINE_STEP_CODE_CHARS"] = str(int(st.number_input(
            "CUCUMBER_INLINE_STEP_CODE_CHARS", min_value=0, step=2000,
            value=env_int("CUCUMBER_INLINE_STEP_CODE_CHARS", reglages.DEFAULT_INLINE_STEP_CODE_CHARS),
            help="Taille maximale (caractères) du code des steps Standard candidats injecté dans le prompt d'un lot "
                 "(signature et corps : colonnes des tables, clés de contexte, helpers appelés), 8 steps au plus "
                 "par spécifique. 0 = rien d'injecté (l'agent lit le code dans la copie des sources du Standard).")))
        lean = st.checkbox(
            "CLAUDE_LEAN_SESSION", value=_get("CLAUDE_LEAN_SESSION", "1").strip() != "0",
            help="Sessions Cucumber allégées : seules les définitions des outils autorisés sont envoyées (--tools), "
                 "sans serveur MCP (--strict-mcp-config), commandes ni skills (--disable-slash-commands), sections "
                 "propres au poste hors du prompt système. Décoché = comportement antérieur.")
        env_values["CLAUDE_LEAN_SESSION"] = "1" if lean else "0"
        env_values["CUCUMBER_EXCLUDE_GLOBS"] = st.text_input(
            "CUCUMBER_EXCLUDE_GLOBS", value=_get("CUCUMBER_EXCLUDE_GLOBS", ""),
            placeholder="Vide = exclusions par défaut (pom.xml, tests unitaires, ressources statiques…)",
            help="Motifs (séparés par des virgules) des fichiers techniques exclus de l'inventaire des spécifiques. "
                 "Une valeur remplace entièrement la liste par défaut : tools/cucumber_tools.py, DEFAULT_EXCLUDE_GLOBS.")
        env_values["CUCUMBER_EXCLUDE_REPOS"] = st.text_input(
            "CUCUMBER_EXCLUDE_REPOS", value=_get("CUCUMBER_EXCLUDE_REPOS", ""),
            placeholder="Vide = *-frontend, *-front, *-ui",
            help="Motifs des dépôts clients exclus de l'inventaire : les dépôts front (écrans) ne sont pas testables "
                 "avec les steps du Cucumber Standard.")
        env_values["CUCUMBER_PARAM_GLOBS"] = st.text_input(
            "CUCUMBER_PARAM_GLOBS", value=_get("CUCUMBER_PARAM_GLOBS", ""),
            placeholder="Vide = *.properties, *i18n*, *messages*, *.sql, *param*.xml, *parameter*, *.csv",
            help="Motifs des fichiers classés « parametrage » (paramètres et messages).")
        try:
            match_min = float(_get("CUCUMBER_STD_MATCH_MIN", "").strip() or reglages.DEFAULT_STD_MATCH_MIN)
        except ValueError:
            match_min = reglages.DEFAULT_STD_MATCH_MIN
        env_values["CUCUMBER_STD_MATCH_MIN"] = str(st.number_input(
            "CUCUMBER_STD_MATCH_MIN", min_value=0.0, max_value=1.0, step=0.05, value=match_min,
            help="Score minimal d'une feature du Cucumber Standard pour être rapprochée d'un spécifique (au plus 3, "
                 "dont l'agent part pour écrire ses tests). Plus haut = moins de rapprochements, plus sûrs. Pris en "
                 "compte au prochain inventaire."))
        try:
            dup_threshold = float(_get("CUCUMBER_DUP_THRESHOLD", "").strip()
                                  or reglages.DEFAULT_DUP_THRESHOLD)
        except ValueError:
            dup_threshold = reglages.DEFAULT_DUP_THRESHOLD
        with st.container(horizontal=True):
            dup_value = st.number_input(
                "CUCUMBER_DUP_THRESHOLD", min_value=0.5, max_value=1.0, step=0.05, value=dup_threshold,
                help="Similarité (titre ou steps) au-delà de laquelle un scénario généré est signalé comme doublon "
                     "d'un scénario Standard ou client, puis envoyé à la revue.")
            review_turns = st.number_input(
                "CUCUMBER_REVIEW_MAX_TURNS", min_value=3, step=1, value=env_int("CUCUMBER_REVIEW_MAX_TURNS", 8),
                help="Nombre maximal de tours de l'appel de revue des scénarios signalés d'un lot (--max-turns).")
        env_values["CUCUMBER_DUP_THRESHOLD"] = str(dup_value)
        env_values["CUCUMBER_REVIEW_MAX_TURNS"] = str(int(review_turns))
        env_values["CUCUMBER_COMPILE_CMD"] = st.text_input(
            "CUCUMBER_COMPILE_CMD", value=_get("CUCUMBER_COMPILE_CMD", reglages.DEFAULT_COMPILE_CMD),
            placeholder="Vide = pas de compilation",
            help="Commande de compilation lancée après chaque lot (en Python, sans token). Dépôt client sans pom.xml : "
                 "compilé par-dessus une copie du Cucumber Standard. Hors ligne (-o) en échec faute de dépendances : "
                 "relancée une fois en ligne.")
        with st.container(horizontal=True):
            env_values["CUCUMBER_JAVA21_HOME"] = st.text_input(
                "CUCUMBER_JAVA21_HOME", value=_get("CUCUMBER_JAVA21_HOME", ""),
                help="Dossier d'un JDK 21 pour la compilation et le dry-run locaux de la migration Java 21 (vide : "
                     "JDK courant de Maven, qui doit être en 21).")
            java21_days = st.number_input(
                "CUCUMBER_JAVA21_REFERENCE_DAYS", min_value=0, step=1,
                value=env_int("CUCUMBER_JAVA21_REFERENCE_DAYS", reglages.DEFAULT_REFERENCE_DAYS),
                help="Âge maximal (jours) d'un run complet vert réutilisé comme run AVANT de la migration Java 21 "
                     "(0 : toujours relancé).")
        env_values["CUCUMBER_JAVA21_REFERENCE_DAYS"] = str(int(java21_days))
        with st.container(horizontal=True):
            fix_attempts = st.number_input(
                "CUCUMBER_FIX_ATTEMPTS", min_value=0, step=1, value=env_int("CUCUMBER_FIX_ATTEMPTS", 2),
                help="Appels de correction au plus par lot si la compilation échoue (0 = aucun).")
            fix_turns = st.number_input(
                "CUCUMBER_FIX_MAX_TURNS", min_value=3, step=1, value=env_int("CUCUMBER_FIX_MAX_TURNS", 10),
                help="Nombre maximal de tours de l'agent par appel de correction (--max-turns).")
        with st.container(horizontal=True):
            fix_max = st.number_input(
                "CUCUMBER_FIX_MAX_ATTEMPTS", min_value=0, step=1, value=env_int("CUCUMBER_FIX_MAX_ATTEMPTS", 6),
                help="Corrections au plus par lot, au total : au-delà des CUCUMBER_FIX_ATTEMPTS premières, avec le "
                     "modèle du lot, tant que les erreurs évoluent (arrêt si les mêmes erreurs reviennent).")
            fix_lot_turns = st.number_input(
                "CUCUMBER_FIX_LOT_MAX_TURNS", min_value=3, step=1, value=env_int("CUCUMBER_FIX_LOT_MAX_TURNS", 25),
                help="Tours de l'agent par correction faite avec le modèle du lot.")
        env_values["CUCUMBER_FIX_ATTEMPTS"] = str(int(fix_attempts))
        env_values["CUCUMBER_FIX_MAX_TURNS"] = str(int(fix_turns))
        with st.container(horizontal=True):
            env_values["CLAUDE_MODEL_CUCUMBER_VERIFY_FIX"] = st.text_input(
                "CLAUDE_MODEL_CUCUMBER_VERIFY_FIX", value=_get("CLAUDE_MODEL_CUCUMBER_VERIFY_FIX", ""),
                help="Modèle des corrections après exécution Jenkins. Vide : modèle du lot, en reprenant sa session "
                     "(tout le contexte du lot est relu à chaque correction : le plus cher). Renseigné (ex. sonnet) : "
                     "appel neuf avec ce modèle, sur le seul prompt de correction.")
            env_values["CLAUDE_EFFORT_CUCUMBER_VERIFY_FIX"] = st.text_input(
                "CLAUDE_EFFORT_CUCUMBER_VERIFY_FIX", value=_get("CLAUDE_EFFORT_CUCUMBER_VERIFY_FIX", ""),
                help="Effort de ces corrections (low, medium, high…). Vide : CLAUDE_EFFORT_CUCUMBER_FIX.")
        env_values["CUCUMBER_FIX_MAX_ATTEMPTS"] = str(int(fix_max))
        env_values["CUCUMBER_FIX_LOT_MAX_TURNS"] = str(int(fix_lot_turns))
        dry_run = st.checkbox(
            "CUCUMBER_DRY_RUN", value=_get("CUCUMBER_DRY_RUN", "1").strip() != "0",
            help="Après une compilation réussie, dry-run Cucumber des features du lot : vérifie que chaque step a "
                 "une définition (mvn test -Dcucumber.execution.dry-run=true), sans rien exécuter.")
        env_values["CUCUMBER_DRY_RUN"] = "1" if dry_run else "0"
        agent_compile = st.checkbox(
            "CUCUMBER_AGENT_COMPILE", value=_get("CUCUMBER_AGENT_COMPILE", "1").strip() != "0",
            help="L'agent d'un lot (et de la revue / des corrections) compile lui-même le worktree pendant qu'il "
                 "écrit (CUCUMBER_COMPILE_CMD, test-compile : rien n'est exécuté) et corrige ses erreurs avec tout le "
                 "contexte du lot. Décoché = compilation après le lot seulement, corrigée par CLAUDE_MODEL_CUCUMBER_FIX.")
        env_values["CUCUMBER_AGENT_COMPILE"] = "1" if agent_compile else "0"
        agent_jenkins = st.checkbox(
            "CUCUMBER_AGENT_JENKINS", value=_agent_runs_tests(),
            help="Coché par défaut dès que JENKINS_USER et JENKINS_API_TOKEN sont renseignés (boucle fermée). "
                 "Décoché : l'agent livre un patch non vérifié, ne pousse rien et ne lance aucun test ; vous appliquez le "
                 "patch, lancez votre job complet et importez son résultat. Coché : l'agent vérifie en autonomie "
                 "pendant la génération — il pousse ses tests sur sa branche de travail (profil client), lance le "
                 "job du client (profil ou CUCUMBER_VERIFY_JOB) avec les paramètres imposés du profil, un build à la "
                 "fois, corrige et revérifie, puis livre un patch vérifié à appliquer sur votre branche.")
        env_values["CUCUMBER_AGENT_JENKINS"] = "1" if agent_jenkins else "0"
        st.markdown("##### Vérification de la pertinence par exécution")
        st.caption("Sans job ni commande locale, la génération s'arrête au contrôle statique et affiche « pertinence "
                   "non vérifiée par exécution ».")
        env_values["CUCUMBER_VERIFY_JOB"] = st.text_input(
            "CUCUMBER_VERIFY_JOB", value=_get("CUCUMBER_VERIFY_JOB", ""),
            placeholder="Vide = pas de job (ex. CUCUMBER-VERIFY-PATCH)",
            help="Job Jenkins de vérification. Avec un paramètre fichier PATCH (.patch du lot), TAGS et ENV : aucun "
                 "push. Sans PATCH : le job lit la branche de travail du profil client, où l'agent pousse ses tests "
                 "(jamais la branche d'intégration) ; les rôles de ses paramètres (branche, dépôt, arguments, "
                 "attente) sont détectés et corrigeables dans le profil client.")
        with st.container(horizontal=True):
            env_values["CUCUMBER_VERIFY_ENV"] = st.text_input(
                "CUCUMBER_VERIFY_ENV", value=_get("CUCUMBER_VERIFY_ENV", ""),
                help="Environnement client (paramètre ENV du job, {env} de la commande locale).")
            env_values["CUCUMBER_VERIFY_ENV_STD"] = st.text_input(
                "CUCUMBER_VERIFY_ENV_STD", value=_get("CUCUMBER_VERIFY_ENV_STD", ""),
                help="Environnement Standard de même version, pour la contre-épreuve des overrides (vide = pas de "
                     "contre-épreuve Standard).")
        env_values["CUCUMBER_INIT_TAGS"] = st.text_input(
            "CUCUMBER_INIT_TAGS", value=_get("CUCUMBER_INIT_TAGS", ""), placeholder="Ex. @INIT_CLIENT_WMS",
            help="Initialisation du client, exécutée une fois par génération avant de juger les lots. En échec : "
                 "la vérification s'arrête, les scénarios sont « non vérifiés — environnement non initialisé ». "
                 "Désigne aussi les features d'init dont les codes créés sont imposés aux lots (défaut : tag "
                 "@INIT_*, dossier 0x-Contexte). Vide = pas d'initialisation préalable.")
        env_values["CUCUMBER_DETECTION_INTERVAL"] = st.text_input(
            "CUCUMBER_DETECTION_INTERVAL", value=_get("CUCUMBER_DETECTION_INTERVAL", ""), placeholder="900",
            help="Onglet Cucumber : délai en secondes entre deux contrôles des commits des dépôts du projet ; la "
                 "détection automatique (inventaire, profil, état de l'init) n'est refaite que s'ils ont changé.")
        env_values["CUCUMBER_LOCAL_SCHEMA"] = st.text_input(
            "CUCUMBER_LOCAL_SCHEMA", value=_get("CUCUMBER_LOCAL_SCHEMA", ""),
            placeholder="cucumber_local_{client}",
            help="Schéma des essais locaux, cible de la copie de la config client (étape 3) ; {client} = nom du client. "
                 "Jamais un schéma du pool des runs Jenkins. L'agent ne le lit que s'il est choisi comme schéma du "
                 "projet.")
        env_values["CUCUMBER_POOL_SCHEMA_PATTERN"] = st.text_input(
            "CUCUMBER_POOL_SCHEMA_PATTERN", value=_get("CUCUMBER_POOL_SCHEMA_PATTERN", ""),
            placeholder=r"cucumber_customer_\d+",
            help="Expression régulière des schémas des runs Jenkins (un par slot) : aucune copie de base n'y écrit.")
        env_values["CUCUMBER_REFERENCE_SCHEMA"] = st.text_input(
            "CUCUMBER_REFERENCE_SCHEMA", value=_get("CUCUMBER_REFERENCE_SCHEMA", ""),
            help="Diagnostic de l'environnement (clear en échec reproductible) : schéma de référence de la version, "
                 "dont les contraintes de clé étrangère servent de comparaison. Vide : un autre slot du pool.")
        env_values["CLAUDE_MODEL_ENV_DIAGNOSIS"] = st.text_input(
            "CLAUDE_MODEL_ENV_DIAGNOSIS", value=_get("CLAUDE_MODEL_ENV_DIAGNOSIS", ""), placeholder="haiku",
            help="Modèle qui résume les logs serveur de la purge via Groundcover (lecture seule) pendant le "
                 "diagnostic de l'environnement.")
        env_values["CUCUMBER_RUN_JOB"] = st.text_input(
            "CUCUMBER_RUN_JOB", value=_get("CUCUMBER_RUN_JOB", ""), placeholder="DOSSIER/JOB",
            help="Run Jenkins automatique (onglet Cucumber, mode expert, « Résultats ») : job qui exécute <client>-cucumber, si "
                 "le profil client n'en donne pas. Il doit lire la branche du profil et accepter TAGS.")
        env_values["CUCUMBER_VERIFY_LOCAL_CMD"] = st.text_input(
            "CUCUMBER_VERIFY_LOCAL_CMD", value=_get("CUCUMBER_VERIFY_LOCAL_CMD", ""),
            placeholder='mvn -q test "-Dcucumber.filter.tags={tags}" -Denv={env}',
            help="Repli local (si pas de job) : commande lancée dans le dossier de compilation ; {tags} et {env} "
                 "sont remplacés ; le rapport target/**/cucumber*.json est lu.")
        with st.container(horizontal=True):
            verify_timeout = st.number_input(
                "CUCUMBER_VERIFY_TIMEOUT", min_value=60, step=60, value=env_int("CUCUMBER_VERIFY_TIMEOUT", 3600),
                help="Durée maximale d'une exécution (build Jenkins ou commande locale), en secondes.")
            verify_fixes = st.number_input(
                "CUCUMBER_VERIFY_FIX_MAX", min_value=0, step=1, value=env_int("CUCUMBER_VERIFY_FIX_MAX", 4),
                help="Corrections au plus par lot après exécution. L'agent continue tant que l'échec change et "
                     "s'arrête pour un scénario dès que son échec revient à l'identique. 0 : aucune correction.")
            review_sample = st.number_input(
                "CUCUMBER_REVIEW_SAMPLE", min_value=0, step=1, value=env_int("CUCUMBER_REVIEW_SAMPLE", 0),
                help="Scénarios proposés à la revue humaine de l'onglet Cucumber : d'abord les « non vérifié », "
                     "puis des « pertinent » tirés au hasard.")
        env_values["CUCUMBER_VERIFY_TIMEOUT"] = str(int(verify_timeout))
        env_values["CUCUMBER_VERIFY_FIX_MAX"] = str(int(verify_fixes))
        env_values["CUCUMBER_VERSION_BRANCH_WRITE"] = "1" if st.checkbox(
            "Écrire directement sur les branches de version du dépôt <client>-cucumber (v1.1, v1.2…)",
            value=_get("CUCUMBER_VERSION_BRANCH_WRITE", "1").strip() != "0",
            help="Étape 4 de l'onglet Cucumber : diff validé puis poussé sur la branche de la version (des tests "
                 "seulement), jamais sur la branche d'intégration ni en forçant. Décoché : branche de l'agent et patch "
                 "à appliquer (CUCUMBER_VERSION_BRANCH_WRITE).") else "0"
        # Ancien réglage (plafond fixe) : remplacé par le plafond adaptatif, 0 conservé comme « aucune correction »
        env_values["CUCUMBER_VERIFY_FIX_ATTEMPTS"] = "0" if int(verify_fixes) == 0 else ""
        env_values["CUCUMBER_REVIEW_SAMPLE"] = str(int(review_sample))
        with st.container(horizontal=True):
            slow_seconds = st.number_input(
                "CUCUMBER_SLOW_SCENARIO_SECONDS", min_value=10, step=30,
                value=env_int("CUCUMBER_SLOW_SCENARIO_SECONDS", 300),
                help="Import d'un run : un scénario est anormalement long au-delà de cette durée (ou de 3 fois la "
                     "durée médiane du run).")
            export_cap = st.number_input(
                "CUCUMBER_LOG_EXPORT_MAX_LINES", min_value=0, step=1000,
                value=env_int("CUCUMBER_LOG_EXPORT_MAX_LINES", 0),
                help="Plafond de lignes de l'outil d'export des logs serveur (0 : inconnu) : rappelé dans les "
                     "fenêtres à exporter ; un export qui l'atteint est signalé.")
            max_cause = st.number_input(
                "CUCUMBER_MAX_CAUSE_ATTEMPTS", min_value=1, step=1, value=env_int("CUCUMBER_MAX_CAUSE_ATTEMPTS", 3),
                help="Corrections infructueuses sur une même cause (même message, valeurs variables masquées) avant "
                     "que l'agent arrête de deviner et propose une méthode de preuve.")
        env_values["CUCUMBER_MAX_CAUSE_ATTEMPTS"] = str(int(max_cause))
        env_values["CUCUMBER_SERVER_LOG_DIR"] = st.text_input(
            "CUCUMBER_SERVER_LOG_DIR", value=_get("CUCUMBER_SERVER_LOG_DIR", ""),
            help="Répertoire où sont déposés les logs applicatifs du serveur de l'environnement de test (*.log, "
                 "rotations, .gz) : à chaque import d'un run, la fenêtre de chaque échec y est extraite (secrets "
                 "masqués) et donnée au correcteur, sans export ni réimport manuel. Vide : non utilisé.").strip()
        # Accès à l'API groundcover : composant commun (agent_socle.ihm_groundcover)
        env_values.update(_groundcover(ihm_groundcover.champs_acces_api))
        env_values["CUCUMBER_SLOW_SCENARIO_SECONDS"] = str(int(slow_seconds))
        env_values["CUCUMBER_LOG_EXPORT_MAX_LINES"] = str(int(export_cap)) if export_cap else ""
        jacoco = st.checkbox(
            "CUCUMBER_JACOCO", value=_get("CUCUMBER_JACOCO", "").strip() == "1",
            help="Environnement client lancé avec l'agent JaCoCo : part des classes de chaque spécifique exécutées "
                 "(jacoco.exec archivé par le job, ou CUCUMBER_JACOCO_EXEC en local).")
        env_values["CUCUMBER_JACOCO"] = "1" if jacoco else "0"
        keep_tags = st.checkbox(
            "CUCUMBER_KEEP_AGENT_TAGS", value=_get("CUCUMBER_KEEP_AGENT_TAGS", "").strip() == "1",
            help="Conserve les tags de vérification @agent_<client>_<lot>[_<n>] dans le patch livré (par défaut, "
                 "ils sont retirés du patch et gardés seulement dans le worktree).")
        env_values["CUCUMBER_KEEP_AGENT_TAGS"] = "1" if keep_tags else "0"
        env_values["CUCUMBER_JACOCO_EXEC"] = st.text_input(
            "CUCUMBER_JACOCO_EXEC", value=_get("CUCUMBER_JACOCO_EXEC", ""),
            help="Mode local : chemin du jacoco.exec produit par l'environnement client.")

        st.markdown("---")
        # Serveurs MCP (Groundcover seul) : composant commun (agent_socle.ihm_groundcover)
        env_values.update(_groundcover(ihm_groundcover.champs_serveurs_mcp, compte="compte Claude ci-dessous"))

        st.markdown("---")
        st.markdown("#### 🔑 Compte Claude")
        # Valeur du .env seule : celle héritée du shell n'est pas enregistrée par défaut
        saved_config_dir = (dotenv_values(ENV_PATH).get("CLAUDE_CONFIG_DIR") or "").strip()
        config_dir_options = [""] + claude_config_dirs()
        if saved_config_dir and saved_config_dir not in config_dir_options:
            config_dir_options.append(saved_config_dir)
        shell_config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "")

        def config_dir_label(config_dir: str) -> str:
            label = config_dir or ("Défaut du poste" + (f" ({shell_config_dir})" if shell_config_dir else " (~/.claude)"))
            account = claude_account(config_dir or shell_config_dir)
            return f"{label} — {account}" if account else label

        env_values["CLAUDE_CONFIG_DIR"] = st.selectbox(
            "CLAUDE_CONFIG_DIR", config_dir_options, index=config_dir_options.index(saved_config_dir),
            format_func=config_dir_label, accept_new_options=True,
            help="Dossier de configuration de Claude Code du compte à utiliser (abonnement Pro / Max / Team). "
                 "Proposés : ~/.claude et ~/.claude-* du poste ; saisissez un autre chemin si besoin. "
                 "Pour connecter un nouveau compte : CLAUDE_CONFIG_DIR=<dossier> puis `claude auth login`.")
        env_values["ANTHROPIC_API_KEY"] = secret_input(
            "ANTHROPIC_API_KEY", label="ANTHROPIC_API_KEY (Laissez vide pour utiliser le compte Claude ci-dessus)",
            help="Renseignée, la clé API est utilisée à la place du compte Claude.")

        st.markdown("---")
        st.markdown("#### 💸 Optimisation des tokens")
        jira_context_max = st.number_input(
            "JIRA_CONTEXT_MAX_CHARS", min_value=0, max_value=200000, step=1000,
            value=env_int("JIRA_CONTEXT_MAX_CHARS", 12000),
            help="Taille maximale du contexte Jira (description, Épopée, COST) injecté dans le prompt de cadrage. "
                 "0 = pas d'injection : l'agent interroge Jira lui-même."
        )
        memory_max = st.number_input(
            "MEMORY_MAX_CHARS", min_value=0, max_value=100000, step=1000,
            value=env_int("MEMORY_MAX_CHARS", 6000),
            help="Taille maximale des retours d'expérience injectés dans les prompts. 0 = désactivé."
        )
        env_values["JIRA_CONTEXT_MAX_CHARS"] = str(int(jira_context_max))
        env_values["MEMORY_MAX_CHARS"] = str(int(memory_max))
        model_help = ("Alias (sonnet, opus, haiku) ou identifiant complet du modèle, transmis avec --model. "
                      "Vide = modèle par défaut de Claude Code.")
        with st.container(horizontal=True):
            env_values["CLAUDE_MODEL_CADRAGE"] = st.text_input("CLAUDE_MODEL_CADRAGE", value=_get("CLAUDE_MODEL_CADRAGE", ""), help=model_help)
            env_values["CLAUDE_MODEL_REFINE"] = st.text_input("CLAUDE_MODEL_REFINE", value=_get("CLAUDE_MODEL_REFINE", ""), help=model_help)
        with st.container(horizontal=True):
            env_values["CLAUDE_MODEL_PATCH"] = st.text_input("CLAUDE_MODEL_PATCH", value=_get("CLAUDE_MODEL_PATCH", ""), help=model_help)
        with st.container(horizontal=True):
            env_values["CLAUDE_MODEL_BESOIN"] = st.text_input("CLAUDE_MODEL_BESOIN", value=_get("CLAUDE_MODEL_BESOIN", ""),
                                                              help=model_help + " Utilisé par la rédaction du besoin (onglet Intégration).")
            env_values["CLAUDE_MODEL_CUCUMBER"] = st.text_input("CLAUDE_MODEL_CUCUMBER", value=_get("CLAUDE_MODEL_CUCUMBER", ""),
                                                                help=model_help.replace("Vide = modèle par défaut de Claude Code.", "Vide = sonnet.")
                                                                + " Utilisé par chaque lot de la génération des tests Cucumber d'un client (onglet Cucumber).")
        env_values["CLAUDE_MODEL_CUCUMBER_FIX"] = st.text_input("CLAUDE_MODEL_CUCUMBER_FIX", value=_get("CLAUDE_MODEL_CUCUMBER_FIX", ""),
                                                                placeholder="Vide = haiku",
                                                                help=model_help.replace("Vide = modèle par défaut de Claude Code.", "Vide = haiku.")
                                                                + " Utilisé par les corrections de compilation et de dry-run des lots Cucumber ; si elles échouent, "
                                                                  "une dernière tentative est faite avec CLAUDE_MODEL_CUCUMBER.")
        effort_help = ("Niveau d'effort transmis avec --effort : low, medium, high, xhigh ou max. "
                       "Vide = réglage de Claude Code sur le poste.")
        with st.container(horizontal=True):
            env_values["CLAUDE_EFFORT_CADRAGE"] = st.text_input("CLAUDE_EFFORT_CADRAGE", value=_get("CLAUDE_EFFORT_CADRAGE", ""), help=effort_help)
            env_values["CLAUDE_EFFORT_REFINE"] = st.text_input("CLAUDE_EFFORT_REFINE", value=_get("CLAUDE_EFFORT_REFINE", ""), help=effort_help)
        with st.container(horizontal=True):
            env_values["CLAUDE_EFFORT_PATCH"] = st.text_input("CLAUDE_EFFORT_PATCH", value=_get("CLAUDE_EFFORT_PATCH", ""), help=effort_help)
        with st.container(horizontal=True):
            env_values["CLAUDE_EFFORT_BESOIN"] = st.text_input("CLAUDE_EFFORT_BESOIN", value=_get("CLAUDE_EFFORT_BESOIN", ""),
                                                               help=effort_help + " Utilisé par la rédaction du besoin (onglet Intégration).")
            env_values["CLAUDE_EFFORT_CUCUMBER"] = st.text_input("CLAUDE_EFFORT_CUCUMBER", value=_get("CLAUDE_EFFORT_CUCUMBER", "medium"),
                                                                 help=effort_help + " Utilisé par chaque lot de la génération des tests Cucumber d'un client.")
        env_values["CLAUDE_EFFORT_CUCUMBER_FIX"] = st.text_input("CLAUDE_EFFORT_CUCUMBER_FIX", value=_get("CLAUDE_EFFORT_CUCUMBER_FIX", "low"),
                                                                 help=effort_help.replace("Vide = réglage de Claude Code sur le poste.", "Vide = low.")
                                                                 + " Utilisé par les appels de correction et de revue des lots Cucumber.")
        st.caption("La consommation de chaque exécution est affichée (ligne [TOKENS]) et ajoutée à .tmp/token_usage.csv, "
                   "avec le modèle et l'effort utilisés.")

        st.markdown("---")
        st.markdown("#### 🗄️ Connexion Base de données (lecture seule)")
        current_db_type = _get("DB_TYPE", "postgresql")
        env_values["DB_TYPE"] = st.selectbox("DB_TYPE", DB_TYPES,
                                             index=DB_TYPES.index(current_db_type) if current_db_type in DB_TYPES else 0)
        with st.container(horizontal=True):
            env_values["DB_HOST"] = st.text_input("DB_HOST", value=_get("DB_HOST", ""))
            env_values["DB_PORT"] = st.text_input("DB_PORT", value=_get("DB_PORT", ""), help="Vide = port par défaut du SGBD.")
        env_values["DB_NAME"] = st.text_input("DB_NAME (base, ou service name Oracle)", value=_get("DB_NAME", ""))
        with st.container(horizontal=True):
            env_values["DB_USER"] = st.text_input("DB_USER", value=_get("DB_USER", ""))
            env_values["DB_PASSWORD"] = secret_input("DB_PASSWORD")
        env_values["DB_SCHEMA"] = st.text_input(
            "DB_SCHEMA (valeur par défaut)", value=_get("DB_SCHEMA", ""),
            help="Pré-remplit le champ « Schéma BDD cible » de l'onglet Lancement."
        )
        env_values["DB_ODBC_DRIVER"] = st.text_input(
            "DB_ODBC_DRIVER", value=_get("DB_ODBC_DRIVER", "ODBC Driver 18 for SQL Server"),
            help="SQL Server via pyodbc uniquement : nom du pilote ODBC installé sur le poste."
        )
        st.caption("Utilisez de préférence un compte base en lecture seule : le mot de passe reste dans le .env "
                   "et n'est jamais transmis à l'agent.")

        st.markdown("#### 🥒 Base de l'environnement de test Cucumber (lecture seule)")
        st.caption("Base de l'environnement où Jenkins joue les tests (CUCUMBER_VERIFY_ENV) : l'agent des lots Cucumber "
                   "y prend ses données de référence réelles. Vide = base ci-dessus (DB_*). Ne mettez pas ces "
                   "informations dans CUCUMBER_VERIFY_ENV, transmis tel quel à Jenkins.")
        current_cuc_type = _get("CUCUMBER_DB_TYPE", "") or current_db_type
        env_values["CUCUMBER_DB_TYPE"] = st.selectbox(
            "CUCUMBER_DB_TYPE", DB_TYPES, index=DB_TYPES.index(current_cuc_type) if current_cuc_type in DB_TYPES else 0)
        with st.container(horizontal=True):
            env_values["CUCUMBER_DB_HOST"] = st.text_input("CUCUMBER_DB_HOST", value=_get("CUCUMBER_DB_HOST", ""))
            env_values["CUCUMBER_DB_PORT"] = st.text_input("CUCUMBER_DB_PORT", value=_get("CUCUMBER_DB_PORT", ""),
                                                           help="Vide = port par défaut du SGBD.")
        env_values["CUCUMBER_DB_NAME"] = st.text_input("CUCUMBER_DB_NAME (base, ou service name Oracle)",
                                                       value=_get("CUCUMBER_DB_NAME", ""))
        with st.container(horizontal=True):
            env_values["CUCUMBER_DB_USER"] = st.text_input("CUCUMBER_DB_USER", value=_get("CUCUMBER_DB_USER", ""))
            env_values["CUCUMBER_DB_PASSWORD"] = secret_input("CUCUMBER_DB_PASSWORD")
        env_values["CUCUMBER_DB_SCHEMA"] = st.text_input(
            "CUCUMBER_DB_SCHEMA (valeur par défaut)", value=_get("CUCUMBER_DB_SCHEMA", ""),
            help="Pré-remplit le champ « Schéma BDD » de l'onglet Cucumber.")

        st.markdown("**Base client source de la config recopiée dans la base des tests** (vide : connexion DB_*)")
        with st.container(horizontal=True):
            for key, default, kind in (("CLIENT_SOURCE_DB_HOST", "", "default"), ("CLIENT_SOURCE_DB_PORT", "", "default"),
                                       ("CLIENT_SOURCE_DB_NAME", "", "default"), ("CLIENT_SOURCE_DB_USER", "", "default"),
                                       ("CLIENT_SOURCE_DB_PASSWORD", "", "password")):
                env_values[key] = secret_input(key) if kind == "password" else                     st.text_input(key, value=_get(key, default))
        with st.container(horizontal=True):
            env_values["PG_BIN_DIR"] = st.text_input(
                "PG_BIN_DIR", value=_get("PG_BIN_DIR", ""), placeholder=r"C:\Program Files\PostgreSQL\16\bin",
                help="Dossier de pg_dump, pg_restore et psql. Vide : ceux du PATH.").strip()
            env_values["RESTORE_POST_SQL"] = st.text_input(
                "RESTORE_POST_SQL", value=_get("RESTORE_POST_SQL", ""),
                help="Script SQL passé après la recopie (comme Script_a_passer_apres_import.sql), précédé de "
                     "« set schema '<schéma des tests>'; ». Vide : aucun.").strip()
            env_values["RESTORE_EXCLUDE_DATA"] = st.text_input(
                "RESTORE_EXCLUDE_DATA", value=_get("RESTORE_EXCLUDE_DATA", ""),
                help="Tables dont les DONNÉES ne sont pas recopiées, en plus de celles du script (historiques, "
                     "journaux), séparées par des virgules.").strip()

        st.markdown("---")
        st.markdown("#### :material/folder_shared: Connaissances partagées")
        with st.container(horizontal=True):
            env_values["SKILLS_AUTO"] = "1" if st.checkbox(
                "Auto-amélioration (compétences)", value=_get("SKILLS_AUTO", "1").strip() != "0",
                help="Après une tâche complexe réussie et vérifiée, l'agent enregistre la procédure suivie comme "
                     "compétence et la relit avant les tâches semblables (SKILLS_AUTO).") else "0"
            env_values["SKILLS_MIN_TURNS"] = str(int(st.number_input(
                "SKILLS_MIN_TURNS", min_value=0, step=1, value=env_int("SKILLS_MIN_TURNS", 8),
                help="Complexité minimale (tours de l'agent) pour qu'une réussite devienne une compétence.")))
            env_values["CLAUDE_MODEL_SKILLS"] = st.text_input(
                "CLAUDE_MODEL_SKILLS", value=_get("CLAUDE_MODEL_SKILLS", ""), placeholder="sonnet",
                help="Modèle de l'appel court qui rédige la compétence.").strip()
        env_values["AGENT_KNOWLEDGE_DIR"] = st.text_input(
            "AGENT_KNOWLEDGE_DIR", value=_get("AGENT_KNOWLEDGE_DIR", ""),
            placeholder=r"\\serveur\partage\agent-savoye, X:\agent-savoye ou /mnt/partage/agent-savoye",
            help="Répertoire commun où chaque analyse est déposée en Markdown et consultée avant les suivantes. "
                 "Chemin UNC, lecteur mappé ou chemin POSIX. Vide = fonction désactivée. Tout utilisateur du partage "
                 "lit tous les documents.")
        with st.container(horizontal=True):
            env_values["AGENT_KNOWLEDGE_AUTHOR"] = st.text_input(
                "AGENT_KNOWLEDGE_AUTHOR", value=_get("AGENT_KNOWLEDGE_AUTHOR", ""),
                placeholder=f"Vide = {_auteur()}",
                help="Auteur inscrit dans les documents déposés. Vide = utilisateur système.")
            knowledge_max = st.number_input(
                "AGENT_KNOWLEDGE_MAX_CONTEXT", min_value=0, max_value=100000, step=1000,
                value=env_int("AGENT_KNOWLEDGE_MAX_CONTEXT", knowledge_tools.DEFAULT_MAX_CONTEXT),
                help="Taille maximale (caractères) des connaissances partagées injectées dans un prompt. 0 = aucune.")
        env_values["AGENT_KNOWLEDGE_MAX_CONTEXT"] = str(int(knowledge_max))

        st.markdown("---")
        submit_save = st.form_submit_button("💾 Enregistrer la configuration complète", type="primary", use_container_width=True)

        # « Connect to MCP » collé tel quel : adresse et en-têtes non secrets extraits, jetons refusés ; variables du
        # manager écartées. None : adresse refusée, rien d'enregistré
        to_save = _groundcover(ihm_groundcover.valider, env_values) if submit_save else None
        if to_save is not None:
            # Les secrets sont enregistrés tels quels ; les autres valeurs sans espaces superflus. Écriture unique
            # (une ligne par valeur, échappements, relecture contrôlée)
            try:
                env_tools.write_env(ENV_PATH, {
                    name: value if name.endswith(("_TOKEN", "_SECRET", "_PASSWORD", "_API_KEY")) else value.strip()
                    for name, value in to_save.items() if name not in _G})
            except ValueError as e:
                st.error(f"Configuration non enregistrée correctement : {e}.")
            else:
                st.success("Toute la configuration a été enregistrée dans le .env partagé : prise en compte au prochain lancement d'une étape d'un agent.")
                st.rerun()

    # Tests de connexion (base, groundcover, MCP, répertoire partagé) : agent-projets (IHM 8504)
    st.link_button("Tests de connexion : Projets et connexions", _url_agent("projets", "http://localhost:8504"),
                   icon=":material/lan:")
    st.caption("Recherche, aperçu, statut des documents, import de .md existants et documents en attente : onglet "
               "📚 Connaissances partagées.")


def _memoire() -> None:
    st.subheader("Mémoire des agents")
    memories = get_all_memories()

    with st.container(horizontal=True):
        if st.button("Importer l'historique local", icon=":material/download:",
                     help="Ajoute les *-cadrage.md et *.patch présents dans AGENT_DATA_DIR",
                 key="reglages_memoire_import"):
            count = import_existing_deliverables(str(DOSSIER))
            st.toast(f"{count} ticket(s) importé(s) en mémoire.")
            st.rerun()

    if not memories:
        st.info("La mémoire est vide.")
    else:
        st.dataframe(memories, hide_index=True, width="stretch")

        selected_mem = st.selectbox("Consulter un ticket mémorisé", [m["issue_key"] for m in memories],
                                key="reglages_memoire_ticket")
        mem = load_ticket_memory(selected_mem)
        with st.expander(f"Détail de {selected_mem}"):
            for fb in mem.get("feedbacks", []):
                st.markdown(f"- `{fb.get('source')}` {fb.get('feedback')}")
                if fb.get("diff"):
                    st.code(fb["diff"], language="diff")
            st.markdown(mem.get("final_cadrage") or "_Pas de cadrage._")
        if st.button(f"Supprimer {selected_mem} de la mémoire", icon=":material/delete:",
                     key="reglages_memoire_supprimer"):
            delete_ticket_memory(selected_mem)
            st.rerun()

        with st.container(border=True):
            st.markdown("**Purger la mémoire**")
            scope = st.selectbox("Périmètre", ["Tous les projets"] + sorted({m["project_key"] for m in memories}),
                         key="reglages_memoire_perimetre")
            confirm = st.checkbox("Je confirme la suppression définitive", key="reglages_memoire_confirmer")
            if st.button("Purger", type="primary", disabled=not confirm, icon=":material/delete_sweep:",
                      key="reglages_memoire_purger"):
                count = clear_memory("" if scope == "Tous les projets" else scope)
                st.toast(f"{count} ticket(s) supprimé(s) de la mémoire.")
                st.rerun()
