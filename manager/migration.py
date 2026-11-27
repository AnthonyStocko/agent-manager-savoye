"""Import, une seule fois, des réglages existants des agents dans la configuration commune.

Lit le ``.env`` de chaque agent du registre et le ``profils.toml`` d'agent-odatio, puis PROPOSE une configuration
commune : accès, secrets et applications. Rien n'est écrit avant validation, et les fichiers des agents ne sont
jamais modifiés. L'import complète l'existant sans l'écraser : une valeur déjà présente dans la configuration
commune est gardée, une application de même nom n'est pas remplacée.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from manager import configuration as conf
from manager import registre as reg

# En cas de valeurs différentes, la première trouvée l'emporte : d'abord le .env partagé d'AGENT_DATA_DIR (lu par
# tous les agents issus d'agent-scrum-master, supprimé), puis ceux des agents du registre
ORDRE = (reg.NOM_LECTEUR, "odatio", "dev-tool")


@dataclass
class Proposition:
    acces: dict = field(default_factory=dict)           # variables non secrètes
    secrets: dict = field(default_factory=dict)
    applications: list = field(default_factory=list)    # [conf.Application]
    origines: dict = field(default_factory=dict)        # variable -> agent d'où vient la valeur retenue
    conflits: list = field(default_factory=list)        # textes lisibles, secrets masqués
    notes: list = field(default_factory=list)

    @property
    def vide(self) -> bool:
        return not (self.acces or self.secrets or self.applications)


def masquer(valeur: str) -> str:
    return "…" + valeur[-4:] if len(valeur) > 8 else "…"


def nom_application(profil: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", profil.lower()).strip("-")


def proposer(registre: reg.Registre, donnees: reg.Agent | None = None) -> Proposition:
    """``donnees`` : source du .env partagé (``registre.lecteur()`` par défaut ; tests)."""
    p = Proposition()
    if donnees is None:
        try:
            donnees = reg.lecteur()
        except Exception:  # noqa: BLE001 — agent-socle absent : .env des agents du registre seulement
            donnees = None
    sources = ([donnees] if donnees is not None else []) + list(registre.agents)
    agents = sorted(sources, key=lambda a: ORDRE.index(a.nom) if a.nom in ORDRE else len(ORDRE))
    valeurs: dict = {}                                  # variable -> [(agent, valeur)]
    envs: dict = {}
    for agent in agents:
        env = conf.lire_env(agent.dossier / ".env")
        envs[agent.nom] = env
        if not (agent.dossier / ".env").exists():
            p.notes.append(f"{agent.titre} : pas de .env dans {agent.dossier}")
        for variable in conf.NON_SECRETS + conf.SECRETS:
            if env.get(variable):
                valeurs.setdefault(variable, []).append((agent.nom, env[variable]))

    for variable, trouvees in valeurs.items():
        agent, valeur = trouvees[0]
        (p.secrets if variable in conf.SECRETS else p.acces)[variable] = valeur
        p.origines[variable] = agent
        differentes = {v for _, v in trouvees}
        if len(differentes) > 1:
            montrer = masquer if variable in conf.SECRETS else (lambda v: v)
            detail = ", ".join(f"{a} : {montrer(v)}" for a, v in trouvees)
            p.conflits.append(f"{variable} diffère selon les agents ({detail}) : la valeur de {agent} est retenue")

    odatio = next((a for a in registre.agents if a.nom == "odatio"), None)
    if odatio:
        _applications_odatio(p, odatio, envs.get("odatio", {}))
    p.notes.append("Restent dans le .env partagé (AGENT_DATA_DIR) ou dans chaque agent (réglages propres) : réglages "
                   "Cucumber, modèles et plafonds, base Cucumber, fichiers interdits et données réelles des projets "
                   "d'odatio.")
    return p


def _applications_odatio(p: Proposition, odatio: reg.Agent, env: dict) -> None:
    fichier = odatio.dossier / "profils.toml"
    if not fichier.exists():
        p.notes.append(f"Agent fonctionnel Odatio : pas de profils.toml dans {odatio.dossier}")
        return
    try:
        profils = tomllib.loads(fichier.read_text(encoding="utf-8")).get("profils", {})
    except (OSError, tomllib.TOMLDecodeError) as e:
        p.notes.append(f"profils.toml d'odatio illisible : {e}")
        return
    for nom_profil, brut in profils.items():
        nom = nom_application(nom_profil)
        if not nom:
            continue
        noms_secrets = conf.secrets_application(nom)
        sources = [conf.Source(nom=str(s.get("nom", "")), depot=str(s.get("depot", "")),
                               branche=str(s.get("branche", "")), chemin=str(s.get("chemin", "")))
                   for s in brut.get("sources", [])]
        base = {k: str(v) for k, v in brut.get("base", {}).items()
                if k in ("type", "hote", "port", "nom", "utilisateur", "schema") and v}
        _secret(p, env, brut.get("base", {}).get("mot_de_passe_env"), noms_secrets["base"])
        logs = {k: v for k, v in brut.get("logs", {}).items() if k in ("dossiers", "groundcover_filtre") and v}
        ssh = dict(brut.get("logs", {}).get("ssh", {}))
        _secret(p, env, ssh.pop("mot_de_passe_env", ""), noms_secrets["ssh"])
        if ssh.get("hote"):
            logs["ssh"] = ssh
        application = brut.get("application", {})
        _secret(p, env, application.get("mot_de_passe_env"), noms_secrets["application"])
        p.applications.append(conf.Application(
            nom=nom, description=str(brut.get("description", "")), sources=sources, base=base, logs=logs,
            url=str(application.get("url", "")), utilisateur_application=str(application.get("utilisateur", ""))))


def _secret(p: Proposition, env: dict, variable: str, cible: str) -> None:
    if variable and env.get(variable):
        p.secrets[cible] = env[variable]
        p.origines[cible] = f"odatio ({variable})"


def appliquer(p: Proposition, dossier: Path = conf.DOSSIER) -> list:
    """Complète la configuration commune avec la proposition ; renvoie ce qui a été gardé tel quel."""
    commun = conf.charger(dossier)
    gardes = []
    for variable, valeur in p.acces.items():
        if commun.acces.get(variable):
            if commun.acces[variable] != valeur:
                gardes.append(f"{variable} : valeur déjà présente gardée")
        else:
            commun.acces[variable] = valeur
    existantes = {a.nom for a in commun.applications}
    for application in p.applications:
        if application.nom in existantes:
            gardes.append(f"application « {application.nom} » déjà présente : gardée")
        else:
            commun.applications.append(application)
    conf.enregistrer(commun, dossier)
    presents = conf.secrets(dossier)
    nouveaux = {k: v for k, v in p.secrets.items() if not presents.get(k)}
    gardes += [f"{k} : secret déjà présent gardé" for k in p.secrets if presents.get(k) and presents[k] != p.secrets[k]]
    if nouveaux:
        conf.enregistrer_secrets(nouveaux, dossier)
    return gardes
