"""Configuration commune de la suite d'agents, tenue par le manager.

Deux fichiers dans ``config/``, jamais versionnés :

- ``commun.toml`` (sans secret) : les accès partagés (Jira, Bitbucket, Jenkins, groundcover, compte Claude) et le
  référentiel des applications Odatio (sources, base, logs, application web, projet Jira, job Jenkins), et les
  seuils des relances du tableau de bord (``[relances]``, lus par le manager seul) ;
- ``.env`` : les secrets (jetons, clé API, mots de passe des bases, des comptes applicatifs et des logs SSH).

Au lancement d'un agent, le superviseur lui transmet en variables d'environnement les accès non secrets, les
secrets que le registre lui autorise (champ ``secrets``, motifs acceptés : ``DB_PASSWORD_*``), ``SAVOYE_VARIABLES``,
la liste de ces variables (l'agent les fait passer avant son propre .env), et ``SAVOYE_CONFIG_DIR``, le dossier
où il peut lire ``commun.toml``. Les noms de variables sont ceux que les agents
utilisent déjà (``JIRA_API_TOKEN``, ``BITBUCKET_HTTP_TOKEN``…).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from manager import registre as reg

DOSSIER = reg.RACINE / "config"
FICHIER = "commun.toml"
FICHIER_SECRETS = ".env"
VARIABLE_DOSSIER = "SAVOYE_CONFIG_DIR"
VARIABLE_LISTE = "SAVOYE_VARIABLES"     # noms des variables transmises : l'agent les fait passer avant son .env


@dataclass(frozen=True)
class Acces:
    variable: str
    libelle: str
    groupe: str
    secret: bool = False
    aide: str = ""


ACCES = (
    Acces("CLAUDE_AUTH", "Mode de connexion", "Compte Claude", aide="compte (abonnement) ou cle (clé API)"),
    Acces("CLAUDE_CONFIG_DIR", "Dossier du compte Claude", "Compte Claude", aide="vide = dossier par défaut"),
    Acces("ANTHROPIC_API_KEY", "Clé API Anthropic", "Compte Claude", secret=True),
    Acces("JIRA_DOMAIN", "Domaine", "Jira", aide="ex. societe.atlassian.net"),
    Acces("JIRA_EMAIL", "E-mail", "Jira"),
    Acces("JIRA_API_TOKEN", "Jeton API", "Jira", secret=True),
    Acces("JIRA_READ_EMAIL", "E-mail du compte de lecture", "Jira",
          aide="facultatif : utilisé par scrum-master pendant les étapes qui ne doivent pas écrire dans Jira"),
    Acces("JIRA_READ_API_TOKEN", "Jeton du compte de lecture", "Jira", secret=True),
    Acces("BITBUCKET_URL", "Adresse", "Bitbucket", aide="ex. https://git.exemple.io"),
    Acces("BITBUCKET_HTTP_TOKEN", "Jeton de lecture", "Bitbucket", secret=True),
    Acces("BITBUCKET_WRITE_TOKEN", "Jeton d'écriture (Cucumber)", "Bitbucket", secret=True),
    Acces("JENKINS_URL", "Adresse", "Jenkins"),
    Acces("JENKINS_USER", "Utilisateur", "Jenkins"),
    Acces("JENKINS_API_TOKEN", "Jeton API", "Jenkins", secret=True),
    Acces("GROUNDCOVER_API_URL", "Adresse de l'API", "groundcover"),
    Acces("GROUNDCOVER_BACKEND_ID", "Identifiant du backend", "groundcover"),
    Acces("GROUNDCOVER_API_KEY", "Clé API", "groundcover", secret=True),
    Acces("ODATIO_DOC_DEPOT", "Dépôt des sources", "Documentation Odatio",
          aide="dépôt Bitbucket des sources de la doc (URL ou PROJET/depot), lu par l'API avec le jeton de lecture "
               "Bitbucket, sans clone"),
    Acces("ODATIO_DOC_BRANCHE", "Branche", "Documentation Odatio", aide="vide = branche par défaut du dépôt"),
    Acces("ODATIO_DOC_DOSSIER", "Dossier de la doc", "Documentation Odatio",
          aide="sous-dossier des sources dans le dépôt ; vide = tout le dépôt"),
)
NON_SECRETS = tuple(a.variable for a in ACCES if not a.secret)
SECRETS = tuple(a.variable for a in ACCES if a.secret)

# Relances du tableau de bord (lot 6 bis-b) : seuils par défaut, remplacés par la section [relances] de commun.toml.
# Délais en jours ouvrés (lundi à vendredi) ; 0 désactive une règle à délai.
RELANCES = {
    "analyse_a_valider_jours": 3,         # analyse à valider non publiée
    "redirection_sans_suite_jours": 2,    # ticket redirigé vers scrum-master, analyse non modifiée depuis
    "patch_non_verifie_jours": 1,         # pièce jointe d'un patch livré non vérifiée (Jira illisible)
    "generation_bloquee_heures": 6,       # génération Cucumber en cours (heures)
    "patch_non_joint": True,              # patch livré NON joint dans Jira (immédiat)
    "build_en_echec": True,               # dernier build Cucumber d'un job en échec, jusqu'à un succès
    "ignorer_jours": 7,                   # durée du masquage par « Ignorer » (jours calendaires, au moins 1)
}

_NOM = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_VARIABLE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ConfigurationErreur(Exception):
    pass


def suffixe(application: str) -> str:
    """Suffixe des secrets d'une application : « canson-prod » → « CANSON_PROD »."""
    return re.sub(r"[^A-Z0-9]+", "_", application.upper()).strip("_")


def secrets_application(application: str) -> dict:
    """Variables du .env propres à une application (mêmes noms que ceux d'agent-odatio)."""
    s = suffixe(application)
    return {"base": f"DB_PASSWORD_{s}", "application": f"APP_PASSWORD_{s}", "ssh": f"SSH_PASSWORD_{s}"}


@dataclass
class Source:
    nom: str = ""
    depot: str = ""          # adresse de clonage Bitbucket
    branche: str = ""
    chemin: str = ""         # ou dossier local


@dataclass
class Application:
    nom: str
    description: str = ""
    jira_projet: str = ""                      # clé du projet Jira, ex. ABC
    sources: list = field(default_factory=list)          # [Source] : Standard et spécifiques
    depot_cucumber: str = ""                   # dépôt <client>-cucumber
    jenkins_job: str = ""                      # job qui joue ses tests Cucumber
    url: str = ""                              # application web
    utilisateur_application: str = ""
    base: dict = field(default_factory=dict)   # type, hote, port, nom, utilisateur, schema
    logs: dict = field(default_factory=dict)   # dossiers, groundcover_filtre, ssh {hote, utilisateur, port, dossiers…}


@dataclass
class Commun:
    acces: dict = field(default_factory=dict)            # variables non secrètes
    applications: list = field(default_factory=list)     # [Application]
    relances: dict = field(default_factory=dict)         # seuils différents des valeurs par défaut (RELANCES)

    def seuils(self) -> dict:
        """Seuils des relances : valeurs par défaut, remplacées par celles de commun.toml."""
        return {**RELANCES, **self.relances}

    def application(self, nom: str) -> Application:
        for a in self.applications:
            if a.nom == nom:
                return a
        raise ConfigurationErreur(f"application inconnue : {nom}")


# ---------------------------------------------------------------------------------------------------------------
# Lecture et écriture de commun.toml
# ---------------------------------------------------------------------------------------------------------------

def charger(dossier: Path = DOSSIER) -> Commun:
    fichier = dossier / FICHIER
    if not fichier.exists():
        return Commun()
    try:
        donnees = tomllib.loads(fichier.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigurationErreur(f"{fichier.name} illisible : {e}") from e
    return lire(donnees)


def lire(donnees: dict) -> Commun:
    acces = {k: str(v) for k, v in donnees.get("acces", {}).items() if k in NON_SECRETS and str(v).strip()}
    inconnus = [k for k in donnees.get("acces", {}) if k not in NON_SECRETS]
    if inconnus:
        raise ConfigurationErreur(f"accès inconnus ou secrets dans commun.toml : {', '.join(inconnus)} "
                                  f"(les secrets vont dans config/.env)")
    applications = []
    for brut in donnees.get("applications", []):
        sources = [Source(**{k: str(v) for k, v in s.items() if k in Source.__dataclass_fields__})
                   for s in brut.get("sources", [])]
        champs = {k: v for k, v in brut.items() if k in Application.__dataclass_fields__ and k != "sources"}
        applications.append(Application(**champs, sources=sources))
    relances = donnees.get("relances", {})
    if not isinstance(relances, dict):
        raise ConfigurationErreur("relances : une table [relances] est attendue dans commun.toml")
    commun = Commun(acces=acces, applications=applications, relances=dict(relances))
    erreurs = verifier(commun)
    if erreurs:
        raise ConfigurationErreur("configuration commune invalide :\n- " + "\n- ".join(erreurs))
    commun.relances = {k: v for k, v in commun.relances.items() if v != RELANCES[k]}   # types déjà vérifiés
    return commun


def verifier(commun: Commun) -> list:
    erreurs, vus = [], set()
    for a in commun.applications:
        if not _NOM.match(a.nom or ""):
            erreurs.append(f"nom d'application invalide « {a.nom} » (minuscules, chiffres et tirets)")
        elif a.nom in vus:
            erreurs.append(f"application « {a.nom} » en double")
        vus.add(a.nom)
        for i, s in enumerate(a.sources, start=1):
            if bool(s.depot) == bool(s.chemin):
                erreurs.append(f"« {a.nom} », source n° {i} : un dépôt ou un dossier local, pas les deux ni aucun")
    for cle, valeur in commun.relances.items():
        if cle not in RELANCES:
            erreurs.append(f"relances : seuil inconnu « {cle} » (connus : {', '.join(RELANCES)})")
        elif isinstance(RELANCES[cle], bool):
            if not isinstance(valeur, bool):
                erreurs.append(f"relances : « {cle} » doit valoir true ou false")
        elif isinstance(valeur, bool) or not isinstance(valeur, int) or valeur < (1 if cle == "ignorer_jours" else 0):
            erreurs.append(f"relances : « {cle} » doit être un entier "
                           f"{'supérieur ou égal à 1' if cle == 'ignorer_jours' else 'positif ou nul'}")
    return erreurs


def enregistrer(commun: Commun, dossier: Path = DOSSIER) -> None:
    erreurs = verifier(commun)
    if erreurs:
        raise ConfigurationErreur("configuration commune invalide :\n- " + "\n- ".join(erreurs))
    donnees = {"acces": {k: v for k, v in commun.acces.items() if k in NON_SECRETS and v},
               "relances": {k: v for k, v in commun.relances.items() if RELANCES.get(k) != v},
               "applications": [_application_en_dict(a) for a in commun.applications]}
    if not donnees["relances"]:
        del donnees["relances"]
    dossier.mkdir(parents=True, exist_ok=True)
    entete = ("# Configuration commune des agents Savoye, tenue par le manager (onglet Configuration).\n"
              "# Aucun secret ici : ils sont dans config/.env. Fichier non versionné.\n\n")
    (dossier / FICHIER).write_text(entete + toml(donnees), encoding="utf-8")


def _application_en_dict(a: Application) -> dict:
    d = {k: v for k, v in vars(a).items() if k not in ("sources", "base", "logs") and v}
    if a.sources:
        d["sources"] = [{k: v for k, v in vars(s).items() if v} for s in a.sources]
    base = {k: v for k, v in a.base.items() if v not in ("", None, [], {})}
    if base:
        d["base"] = base
    logs = {k: v for k, v in a.logs.items() if k != "ssh" and v not in ("", None, [], {})}
    ssh = {k: v for k, v in (a.logs.get("ssh") or {}).items() if v not in ("", None, [], {})}
    if ssh.get("hote"):
        logs["ssh"] = ssh
    if logs:
        d["logs"] = logs
    return d


def toml(donnees: dict, chemin: tuple = ()) -> str:
    """Écrit un dictionnaire en TOML (chaînes, nombres, booléens, listes simples, tables, listes de tables)."""
    lignes, tables, listes = [], [], []
    for cle, valeur in donnees.items():
        if isinstance(valeur, dict):
            tables.append((cle, valeur))
        elif isinstance(valeur, list) and valeur and all(isinstance(v, dict) for v in valeur):
            listes.append((cle, valeur))
        else:
            lignes.append(f"{_cle(cle)} = {_valeur(valeur)}")
    texte = "\n".join(lignes) + ("\n" if lignes else "")
    for cle, valeur in tables:
        sous = chemin + (cle,)
        texte += f"\n[{'.'.join(_cle(c) for c in sous)}]\n" + toml(valeur, sous)
    for cle, valeurs in listes:
        sous = chemin + (cle,)
        for valeur in valeurs:
            texte += f"\n[[{'.'.join(_cle(c) for c in sous)}]]\n" + toml(valeur, sous)
    return texte


def _cle(cle: str) -> str:
    return cle if re.fullmatch(r"[A-Za-z0-9_-]+", cle) else _valeur(cle)


def _valeur(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_valeur(x) for x in v) + "]"
    texte = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return f'"{texte}"'


# ---------------------------------------------------------------------------------------------------------------
# Secrets : config/.env
# ---------------------------------------------------------------------------------------------------------------

def lire_env(fichier: Path) -> dict:
    """Lit un fichier .env simple (NOM=valeur, commentaires #, guillemets facultatifs)."""
    valeurs = {}
    if not fichier.exists():
        return valeurs
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        nom, valeur = ligne.split("=", 1)
        nom, valeur = nom.strip().removeprefix("export ").strip(), valeur.strip()
        if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "\"'":
            valeur = valeur[1:-1]
        elif " #" in valeur:
            valeur = valeur.split(" #", 1)[0].rstrip()
        if _VARIABLE.match(nom):
            valeurs[nom] = valeur
    return valeurs


def secrets(dossier: Path = DOSSIER) -> dict:
    return {k: v for k, v in lire_env(dossier / FICHIER_SECRETS).items() if v}


def enregistrer_secrets(valeurs: dict, dossier: Path = DOSSIER) -> None:
    """Met à jour config/.env : une valeur vide supprime la variable ; les autres lignes sont gardées."""
    for nom, valeur in valeurs.items():
        if not _VARIABLE.match(nom):
            raise ConfigurationErreur(f"nom de secret invalide : {nom}")
        if "\n" in (valeur or "") or "\r" in (valeur or ""):
            raise ConfigurationErreur(f"{nom} : une valeur sur une seule ligne est attendue")
    dossier.mkdir(parents=True, exist_ok=True)
    fichier = dossier / FICHIER_SECRETS
    lignes = fichier.read_text(encoding="utf-8").splitlines() if fichier.exists() else [
        "# Secrets communs des agents Savoye, tenus par le manager. Jamais versionné, jamais partagé."]
    restants = dict(valeurs)
    resultat = []
    for ligne in lignes:
        nom = ligne.split("=", 1)[0].strip() if "=" in ligne and not ligne.lstrip().startswith("#") else ""
        if nom in restants:
            valeur = (restants.pop(nom) or "").strip()
            if valeur:
                resultat.append(f"{nom}={valeur}")
            continue
        resultat.append(ligne)
    resultat += [f"{nom}={v.strip()}" for nom, v in restants.items() if (v or "").strip()]
    fichier.write_text("\n".join(resultat) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------------
# Variables transmises à un agent
# ---------------------------------------------------------------------------------------------------------------

def autorise(nom: str, motifs) -> bool:
    return any(fnmatchcase(nom, m) for m in motifs)


def variables_pour(agent: reg.Agent, dossier: Path = DOSSIER) -> dict:
    """Variables d'environnement d'un agent : accès non secrets, secrets autorisés par le registre (moindre
    privilège) et dossier de la configuration commune."""
    commun = charger(dossier)
    variables = {k: v for k, v in commun.acces.items() if v}
    variables.update({k: v for k, v in secrets(dossier).items() if autorise(k, agent.secrets)})
    return {**variables, VARIABLE_LISTE: ",".join(sorted(variables)), VARIABLE_DOSSIER: str(dossier)}


def env_claude(dossier: Path = DOSSIER) -> dict:
    """Variables du compte Claude pour l'assistant du manager (même compte que les agents)."""
    commun, valeurs = charger(dossier), secrets(dossier)
    cle = valeurs.get("ANTHROPIC_API_KEY", "")
    mode = commun.acces.get("CLAUDE_AUTH") or ("cle" if cle else "compte")
    if mode == "cle" and cle:
        return {"ANTHROPIC_API_KEY": cle}
    dossier_compte = commun.acces.get("CLAUDE_CONFIG_DIR", "")
    return {"CLAUDE_CONFIG_DIR": str(Path(dossier_compte).expanduser())} if dossier_compte else {}
