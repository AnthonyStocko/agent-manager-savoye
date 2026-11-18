"""Superviseur : processus séparé, lancé par ``lancer.bat`` (``python -m manager.superviseur``).

Il démarre les agents du registre en mode « auto » puis l'IHM du manager, surveille leur adresse de santé Streamlit
(``/_stcore/health``), relance un agent tombé (3 fois au plus en 10 minutes) et arrête tout proprement quand on
ferme la fenêtre. L'IHM lui envoie ses ordres (démarrer, arrêter, redémarrer) par un petit serveur HTTP local
(127.0.0.1), protégé par un jeton écrit dans ``.tmp/superviseur.json``.

Il ne touche jamais au code ni aux données des agents : il lance leur commande dans leur dossier, avec en variables
d'environnement la configuration commune qui leur revient (``configuration.variables_pour`` : accès, secrets
autorisés par le registre, dossier de la configuration). Elle est relue à chaque lancement : après un changement
dans l'onglet Configuration, il suffit de redémarrer l'agent.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from manager import configuration
from manager import registre as reg

DOSSIER_TMP = reg.RACINE / ".tmp"
FICHIER_ETAT = DOSSIER_TMP / "superviseur.json"
NOM_MANAGER = "manager"

ARRETE, DEMARRAGE, EN_LIGNE, INJOIGNABLE, ECHEC, EXTERNE, INSTALLATION = (
    "arrêté", "démarrage", "en ligne", "injoignable", "en échec", "lancé hors superviseur", "installation incomplète")


def sante(port: int, delai: float = 2.0) -> bool:
    """Vrai si l'application Streamlit du port répond « ok » sur son adresse de santé."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=delai) as reponse:
            return reponse.status == 200 and reponse.read().strip() == b"ok"
    except OSError:
        return False


def agent_manager(registre: reg.Registre) -> reg.Agent:
    """L'IHM du manager, supervisée comme un agent (avec le python qui fait tourner le superviseur)."""
    commande = tuple(sys.executable if c == "{python}" else c for c in reg.COMMANDE_STREAMLIT)
    return reg.Agent(nom=NOM_MANAGER, titre="Manager", dossier=reg.RACINE, port=registre.port_manager,
                     commande=commande)


@dataclass
class Suivi:
    agent: reg.Agent
    etat: str = ARRETE
    message: str = ""
    voulu: bool = False                       # l'utilisateur veut qu'il tourne : relance s'il tombe
    processus: subprocess.Popen | None = None
    depuis: float = 0.0                       # heure du dernier lancement
    relances: list[float] = field(default_factory=list)

    def resume(self) -> dict:
        a = self.agent
        return {"nom": a.nom, "titre": a.titre, "port": a.port, "url": a.url, "mode": a.mode, "etat": self.etat,
                "message": self.message, "pid": self.processus.pid if self.processus else None,
                "relances": len(self.relances), "dossier": str(a.dossier)}


class Superviseur:
    def __init__(self, agents: list[reg.Agent], dossier_logs: Path = DOSSIER_TMP / "logs", sante=sante,
                 delai_demarrage: float = 90.0, max_relances: int = 3, fenetre_relances: float = 600.0,
                 delai_arret: float = 10.0, env: dict[str, str] | None = None, environnement=None):
        self.suivis = {a.nom: Suivi(a) for a in agents}
        self.dossier_logs = dossier_logs
        self.sante = sante
        self.delai_demarrage = delai_demarrage
        self.max_relances = max_relances
        self.fenetre_relances = fenetre_relances
        self.delai_arret = delai_arret
        self.env = env
        self.environnement = environnement      # agent -> variables propres à lui (configuration commune)
        self.verrou = threading.RLock()

    # --- ordres -------------------------------------------------------------------------------------------------
    def demarrer(self, nom: str) -> dict:
        with self.verrou:
            suivi = self._suivi(nom)
            suivi.voulu = True
            if suivi.processus and suivi.processus.poll() is None:
                return suivi.resume()
            if self.sante(suivi.agent.port):
                suivi.etat, suivi.message = EXTERNE, "le port répond déjà : lancé ailleurs (ex. son lancer.bat)"
                return suivi.resume()
            suivi.relances.clear()
            self._lancer(suivi)
            return suivi.resume()

    def arreter(self, nom: str) -> dict:
        with self.verrou:
            suivi = self._suivi(nom)
            suivi.voulu = False
            self._tuer(suivi)
            if suivi.etat == EXTERNE:
                suivi.message = "lancé hors superviseur : à arrêter depuis sa propre fenêtre"
            else:
                suivi.etat, suivi.message = ARRETE, ""
            return suivi.resume()

    def redemarrer(self, nom: str) -> dict:
        with self.verrou:
            self.arreter(nom)
            return self.demarrer(nom)

    def demarrer_auto(self) -> None:
        for suivi in list(self.suivis.values()):
            if suivi.agent.mode == "auto":
                self.demarrer(suivi.agent.nom)

    def arreter_tout(self) -> None:
        with self.verrou:
            for suivi in self.suivis.values():
                suivi.voulu = False
                self._tuer(suivi)

    def etat(self) -> list[dict]:
        with self.verrou:
            return [s.resume() for s in self.suivis.values()]

    # --- surveillance -------------------------------------------------------------------------------------------
    def verifier(self) -> None:
        """Un tour de surveillance : état de chaque agent, relance de ceux qui sont tombés."""
        with self.verrou:
            maintenant = time.monotonic()
            for suivi in self.suivis.values():
                if suivi.etat == EXTERNE:
                    if not self.sante(suivi.agent.port):
                        suivi.etat, suivi.message, suivi.voulu = ARRETE, "arrêté hors superviseur", False
                    continue
                if not suivi.voulu:
                    continue
                if suivi.processus is None or suivi.processus.poll() is not None:
                    self._relancer(suivi, maintenant)
                elif self.sante(suivi.agent.port):
                    suivi.etat, suivi.message = EN_LIGNE, ""
                elif maintenant - suivi.depuis < self.delai_demarrage:
                    suivi.etat = DEMARRAGE
                else:
                    suivi.etat, suivi.message = INJOIGNABLE, "le processus tourne mais ne répond pas ; voir son journal"

    def _relancer(self, suivi: Suivi, maintenant: float) -> None:
        code = suivi.processus.returncode if suivi.processus else None
        suivi.processus = None
        suivi.relances = [t for t in suivi.relances if maintenant - t < self.fenetre_relances]
        if len(suivi.relances) >= self.max_relances:
            suivi.etat, suivi.voulu = ECHEC, False
            suivi.message = (f"arrêté (code {code}) {len(suivi.relances)} fois en "
                             f"{int(self.fenetre_relances // 60)} min : plus de relance ; voir son journal")
            return
        suivi.relances.append(maintenant)
        self._lancer(suivi)
        if suivi.etat == DEMARRAGE:
            suivi.message = f"relancé après un arrêt (code {code})"

    # --- processus ----------------------------------------------------------------------------------------------
    def _lancer(self, suivi: Suivi) -> None:
        agent = suivi.agent
        if not agent.dossier.is_dir():
            suivi.etat, suivi.message, suivi.voulu = INSTALLATION, f"dossier absent : {agent.dossier}", False
            return
        commande = agent.ligne_de_commande()
        executable = Path(commande[0])
        if executable.is_absolute() and not executable.exists():
            suivi.etat, suivi.voulu = INSTALLATION, False
            suivi.message = f"introuvable : {executable} (venv à créer dans le dossier de l'agent)"
            return
        env = dict(self.env if self.env is not None else os.environ)
        if self.environnement:
            try:
                env.update(self.environnement(agent))
            except (configuration.ConfigurationErreur, OSError) as e:
                suivi.etat, suivi.voulu = INSTALLATION, False
                suivi.message = f"configuration commune illisible : {e}"
                return
        self.dossier_logs.mkdir(parents=True, exist_ok=True)
        journal = open(self.journal(agent.nom), "a", encoding="utf-8")
        journal.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} lancement : {' '.join(commande)}\n")
        journal.flush()
        env["PYTHONUNBUFFERED"] = "1"
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {}
        try:
            suivi.processus = subprocess.Popen(commande, cwd=agent.dossier, env=env, stdin=subprocess.DEVNULL,
                                               stdout=journal, stderr=subprocess.STDOUT, **options)
        except OSError as e:
            suivi.etat, suivi.message, suivi.voulu = INSTALLATION, f"lancement impossible : {e}", False
            return
        finally:
            journal.close()          # le processus garde sa propre copie du descripteur
        suivi.etat, suivi.message, suivi.depuis = DEMARRAGE, "", time.monotonic()

    def _tuer(self, suivi: Suivi) -> None:
        processus, suivi.processus = suivi.processus, None
        if processus is None or processus.poll() is not None:
            return
        processus.terminate()
        try:
            processus.wait(self.delai_arret)
        except subprocess.TimeoutExpired:
            processus.kill()
            processus.wait(5)

    def journal(self, nom: str) -> Path:
        return self.dossier_logs / f"{nom}.log"

    def _suivi(self, nom: str) -> Suivi:
        if nom not in self.suivis:
            raise reg.RegistreErreur(f"agent inconnu : {nom}")
        return self.suivis[nom]


# --- canal de commande local -----------------------------------------------------------------------------------
ACTIONS = {"demarrer": Superviseur.demarrer, "arreter": Superviseur.arreter, "redemarrer": Superviseur.redemarrer}


def serveur_commandes(superviseur: Superviseur, port: int, jeton: str) -> ThreadingHTTPServer:
    """Serveur HTTP local : ``GET /etat``, ``POST /agents/<nom>/<demarrer|arreter|redemarrer>``, en-tête
    ``X-Jeton`` obligatoire. N'écoute que sur 127.0.0.1."""

    class Gestionnaire(BaseHTTPRequestHandler):
        def _repondre(self, code: int, contenu) -> None:
            corps = json.dumps(contenu, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)

        def _autorise(self) -> bool:
            if secrets.compare_digest(self.headers.get("X-Jeton", ""), jeton):
                return True
            self._repondre(403, {"erreur": "jeton absent ou invalide"})
            return False

        def do_GET(self):  # noqa: N802
            if not self._autorise():
                return
            if self.path == "/etat":
                self._repondre(200, {"agents": superviseur.etat()})
            else:
                self._repondre(404, {"erreur": "adresse inconnue"})

        def do_POST(self):  # noqa: N802
            if not self._autorise():
                return
            morceaux = self.path.strip("/").split("/")
            if len(morceaux) != 3 or morceaux[0] != "agents" or morceaux[2] not in ACTIONS:
                self._repondre(404, {"erreur": "adresse inconnue"})
                return
            try:
                self._repondre(200, ACTIONS[morceaux[2]](superviseur, morceaux[1]))
            except reg.RegistreErreur as e:
                self._repondre(404, {"erreur": str(e)})

        def log_message(self, *args):  # pas de bruit dans la console
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Gestionnaire)


# --- point d'entrée --------------------------------------------------------------------------------------------
def _ouvrir_navigateur(superviseur: Superviseur, port: int, delai: float = 120.0) -> None:
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        if superviseur.sante(port):
            webbrowser.open(f"http://localhost:{port}")
            return
        time.sleep(1)


def _arret_demande(*_):
    raise KeyboardInterrupt


def main(intervalle: float = 5.0) -> int:
    try:
        registre = reg.charger()
    except reg.RegistreErreur as e:
        print(e)
        return 1
    if sante(registre.port_manager):
        print(f"Le manager tourne déjà : http://localhost:{registre.port_manager}")
        webbrowser.open(f"http://localhost:{registre.port_manager}")
        return 0

    superviseur = Superviseur(registre.agents + [agent_manager(registre)],
                              environnement=lambda a: {} if a.nom == NOM_MANAGER else configuration.variables_pour(a))
    jeton = secrets.token_urlsafe(24)
    try:
        serveur = serveur_commandes(superviseur, registre.port_superviseur, jeton)
    except OSError as e:
        print(f"Port {registre.port_superviseur} du superviseur indisponible ({e}) : un superviseur tourne déjà ?")
        return 1
    DOSSIER_TMP.mkdir(exist_ok=True)
    FICHIER_ETAT.write_text(json.dumps({"port": registre.port_superviseur, "jeton": jeton, "pid": os.getpid()}),
                            encoding="utf-8")
    threading.Thread(target=serveur.serve_forever, daemon=True).start()

    for sig in ("SIGTERM", "SIGBREAK"):     # SIGBREAK : fermeture de la fenêtre sous Windows
        if hasattr(signal, sig):
            signal.signal(getattr(signal, sig), _arret_demande)

    print("Superviseur des agents Savoye. Laisse cette fenêtre ouverte ; la fermer arrête tous les agents.")
    superviseur.demarrer(NOM_MANAGER)
    superviseur.demarrer_auto()
    threading.Thread(target=_ouvrir_navigateur, args=(superviseur, registre.port_manager), daemon=True).start()
    precedent: dict[str, str] = {}
    try:
        while True:
            superviseur.verifier()
            for agent in superviseur.etat():
                if precedent.get(agent["nom"]) != agent["etat"]:
                    precedent[agent["nom"]] = agent["etat"]
                    detail = f" : {agent['message']}" if agent["message"] else ""
                    print(f"{time.strftime('%H:%M:%S')} {agent['titre']} ({agent['port']}) {agent['etat']}{detail}")
            time.sleep(intervalle)
    except KeyboardInterrupt:
        print("Arrêt des agents…")
    finally:
        serveur.shutdown()
        superviseur.arreter_tout()
        FICHIER_ETAT.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
