"""Journal du manager (lot 6) : SQLite dans ``.tmp/manager.db`` (jamais versionné).

Événements horodatés :
- ``question`` : question posée à l'assistant (extrait court et nettoyé), décision, agents interrogés, outils
  appelés (noms seuls), coût, tours, durée ;
- ``redirection`` : agent, ticket, projet, onglet (ni résumé, ni question, ni demande préremplis) ;
- ``etat`` : changement d'état d'un agent vu par le superviseur (avant → après) ;
- ``erreur`` : échec de l'assistant ou d'une source (message court nettoyé).

Index par ticket : table ``tickets`` (événement → clé Jira) et table ``etat_tickets`` (dernier état connu de
l'analyse d'agent-lancement, relevé par le tableau de bord à chaque rafraîchissement), lus par ``ticket(cle)`` et l'outil ``historique_ticket``.

Point du matin (lot 6 bis) : table ``points`` (une seule ligne, date du dernier « Vu ») et ``bilan(depuis)``.
Relances (lot 6 bis) : table ``relances_ignorees`` (clé de la relance → masquée jusqu'au …), purgée à échéance.

Règles :
- écriture AU MIEUX : ``noter*`` ne lève jamais, renvoie False en cas d'échec (base verrouillée, illisible…) ;
- AUCUNE donnée nominative ni secret : seules des métadonnées et un extrait court de la question, dont les
  adresses e-mail, téléphones, adresses web et suites ressemblant à des jetons sont masqués ;
- rétention bornée (RETENTION_JOURS et LIGNES_MAX), purge automatique au plus une fois par heure ;
- deux processus écrivent (IHM et superviseur) : une connexion courte par opération, mode WAL, délai d'attente
  court ; aucun verrou n'est gardé entre deux opérations.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from manager import registre as reg

FICHIER = reg.RACINE / ".tmp" / "manager.db"
RETENTION_JOURS = 90
LIGNES_MAX = 20_000
EXTRAIT_MAX = 160
MESSAGE_MAX = 200
DELAI_S = 2.0
PURGE_INTERVALLE_S = 3600
TYPES = ("question", "redirection", "etat", "erreur")

TICKET = re.compile(r"\b[A-Z][A-Z0-9_]+-\d+\b")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?")
_MASQUES = (
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[e-mail]"),
    (re.compile(r"https?://\S+"), "[adresse]"),
    # téléphone ; une date ISO (2026-11-19 14:58) n'en est pas un
    (re.compile(r"(?<![\w-])(?!\d{4}-\d{2}-\d{2})(\+?\d[\d .-]{7,}\d)(?![\w-])"), "[numéro]"),
    (re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{24,}\b"), "[masqué]"),
    (re.compile(r"(?i)\b(mot de passe|password|pwd|token|jeton|secret)\s*[:=]\s*\S+"), r"\1 [masqué]"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS evenements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    horodatage TEXT NOT NULL,
    type TEXT NOT NULL,
    agent TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT '',
    extrait TEXT NOT NULL DEFAULT '',
    ticket TEXT NOT NULL DEFAULT '',
    projet TEXT NOT NULL DEFAULT '',
    cout_usd REAL,
    tours INTEGER,
    duree_s REAL,
    details TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS evenements_horodatage ON evenements(horodatage);
CREATE TABLE IF NOT EXISTS tickets (
    evenement INTEGER NOT NULL REFERENCES evenements(id) ON DELETE CASCADE,
    cle TEXT NOT NULL,
    PRIMARY KEY (evenement, cle)
);
CREATE INDEX IF NOT EXISTS tickets_cle ON tickets(cle);
CREATE TABLE IF NOT EXISTS etat_tickets (
    cle TEXT PRIMARY KEY,
    analyse TEXT NOT NULL DEFAULT '',
    a_valider INTEGER NOT NULL DEFAULT 0,
    analyse_modifiee TEXT NOT NULL DEFAULT '',
    vu_le TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS points (
    vu_le TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relances_ignorees (
    cle TEXT PRIMARY KEY,
    jusqu_au TEXT NOT NULL
);
"""


class JournalErreur(Exception):
    pass


def nettoyer(texte: str, limite: int = EXTRAIT_MAX) -> str:
    """Extrait court, sur une ligne, sans e-mail, téléphone, adresse web ni suite ressemblant à un secret."""
    texte = " ".join(str(texte or "").split())
    for motif, remplacement in _MASQUES:
        texte = motif.sub(remplacement, texte)
    return texte if len(texte) <= limite else texte[:limite - 1].rstrip() + "…"


def cles(*textes) -> list[str]:
    """Clés Jira (PROJET-123) trouvées dans les textes, dans l'ordre, sans doublon."""
    vues: dict[str, None] = {}
    for texte in textes:
        for cle in TICKET.findall(str(texte or "").upper()):
            vues[cle] = None
    return list(vues)


def maintenant() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Evenement:
    id: int
    horodatage: str
    type: str
    agent: str = ""
    decision: str = ""
    extrait: str = ""
    ticket: str = ""
    projet: str = ""
    cout_usd: float | None = None
    tours: int | None = None
    duree_s: float | None = None
    details: dict = field(default_factory=dict)
    tickets: list = field(default_factory=list)


class Journal:
    def __init__(self, fichier: Path = FICHIER, retention_jours: int = RETENTION_JOURS, lignes_max: int = LIGNES_MAX,
                 delai: float = DELAI_S):
        self.fichier = Path(fichier)
        self.retention_jours = retention_jours
        self.lignes_max = lignes_max
        self.delai = delai
        self._prepare = False
        self._derniere_purge = 0.0
        self.derniere_erreur = ""

    # --- connexion ----------------------------------------------------------------------------------------------
    def _connexion(self) -> sqlite3.Connection:
        """Connexion courte (une par opération) ; crée la base au premier appel. Lève sqlite3.Error / OSError."""
        self.fichier.parent.mkdir(parents=True, exist_ok=True)
        connexion = sqlite3.connect(self.fichier, timeout=self.delai, isolation_level=None)
        try:
            connexion.execute(f"PRAGMA busy_timeout = {int(self.delai * 1000)}")
            connexion.execute("PRAGMA foreign_keys = ON")
            if not self._prepare:
                connexion.execute("PRAGMA journal_mode = WAL")
                connexion.executescript(SCHEMA)
                self._prepare = True
        except BaseException:
            connexion.close()
            raise
        return connexion

    def _au_mieux(self, operation) -> bool:
        try:
            with closing(self._connexion()) as c:
                operation(c)
            self.derniere_erreur = ""
        except (sqlite3.Error, OSError, ValueError, TypeError) as e:
            self.derniere_erreur = f"{type(e).__name__} : {e}"[:MESSAGE_MAX]
            return False
        self._purger_si_besoin()
        return True

    def _lire(self, operation):
        try:
            with closing(self._connexion()) as c:
                return operation(c)
        except (sqlite3.Error, OSError) as e:
            raise JournalErreur(f"journal illisible ({type(e).__name__} : {e})") from e

    # --- écriture (au mieux) ------------------------------------------------------------------------------------
    def noter(self, type: str, *, agent: str = "", decision: str = "", extrait: str = "", ticket: str = "",
              projet: str = "", cout_usd: float | None = None, tours: int | None = None,
              duree_s: float | None = None, details: dict | None = None, tickets=()) -> bool:
        """Ajoute un événement. Ne lève jamais : False si le journal est indisponible."""
        if type not in TYPES:
            return False
        ticket = ticket.upper() if TICKET.fullmatch((ticket or "").upper()) else ""
        lies = list(dict.fromkeys([ticket] * bool(ticket) + [c for c in tickets if TICKET.fullmatch(c)]))
        ligne = (maintenant(), type, nettoyer(agent, 40), nettoyer(decision, 20), nettoyer(extrait), ticket,
                 nettoyer(projet, 80), cout_usd, tours, duree_s, json.dumps(details or {}, ensure_ascii=False))

        def operation(c):
            c.execute("BEGIN IMMEDIATE")
            try:
                curseur = c.execute("INSERT INTO evenements (horodatage, type, agent, decision, extrait, ticket, "
                                    "projet, cout_usd, tours, duree_s, details) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                    ligne)
                c.executemany("INSERT OR IGNORE INTO tickets (evenement, cle) VALUES (?, ?)",
                              [(curseur.lastrowid, cle) for cle in lies])
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        return self._au_mieux(operation)

    def noter_question(self, question: str, *, decision: str, outils=(), redirections=(), cout_usd=None,
                       tours=None, duree_s=None, erreur: str = "") -> bool:
        """Une question à l'assistant et ses redirections. ``outils`` : [(nom, entrée)] de ``Reponse.outils`` ;
        seuls les noms d'outils et les clés de ticket des entrées sont gardés, jamais les entrées elles-mêmes.
        ``redirections`` : objets ``routeur.Redirection`` (agent, ticket, projet, onglet lus)."""
        noms = [str(nom) for nom, _ in outils]
        agents = list(dict.fromkeys(n.split("__")[1] for n in noms if n.startswith("mcp__") and
                                    len(n.split("__")) == 3 and n.split("__")[1] != "manager"))
        vus = cles(question, *[json.dumps(entree, ensure_ascii=False) for _, entree in outils],
                   *[getattr(r, "ticket", "") for r in redirections])
        details = {"agents": agents, "outils": [n.split("__")[-1] if n.startswith("mcp__") else n for n in noms]}
        if erreur:
            details["erreur"] = nettoyer(erreur, MESSAGE_MAX)
        ok = self.noter("question", agent=agents[0] if agents else "", decision=decision, extrait=question,
                        ticket=vus[0] if len(vus) == 1 else "", cout_usd=cout_usd, tours=tours, duree_s=duree_s,
                        details=details, tickets=vus)
        for r in redirections:
            ok &= self.noter("redirection", agent=getattr(r, "agent", ""), ticket=getattr(r, "ticket", ""),
                             projet=getattr(r, "projet", ""), details={"onglet": getattr(r, "onglet", "")},
                             tickets=cles(getattr(r, "ticket", "")))
        return ok

    def noter_etat(self, agent: str, avant: str, apres: str, message: str = "") -> bool:
        return self.noter("etat", agent=agent, details={"avant": avant, "apres": apres,
                                                        "message": nettoyer(message, MESSAGE_MAX)})

    def noter_erreur(self, origine: str, message: str, agent: str = "") -> bool:
        return self.noter("erreur", agent=agent, extrait=message,
                          details={"origine": nettoyer(origine, 40), "message": nettoyer(message, MESSAGE_MAX)})

    def observer_analyses(self, analyses: list[dict]) -> bool:
        """Dernier état connu des analyses (agent-lancement, lecteur des données) : [{cle, libelle, a_valider, modifiee}]
        (``sources.lire_analyses``). Ne lève jamais."""
        lignes = []
        for a in analyses:
            cle = str(a.get("cle", "")).upper()
            if TICKET.fullmatch(cle):
                modifiee = str(a.get("modifiee", "")).strip().replace(" ", "T")
                lignes.append((cle, nettoyer(a.get("libelle", ""), MESSAGE_MAX), int(bool(a.get("a_valider"))),
                               modifiee if DATE.fullmatch(modifiee) else "", maintenant()))
        if not lignes:
            return True

        def operation(c):
            c.executemany("INSERT INTO etat_tickets (cle, analyse, a_valider, analyse_modifiee, vu_le) "
                          "VALUES (?,?,?,?,?) ON CONFLICT(cle) DO UPDATE SET analyse=excluded.analyse, "
                          "a_valider=excluded.a_valider, analyse_modifiee=excluded.analyse_modifiee, "
                          "vu_le=excluded.vu_le", lignes)
        return self._au_mieux(operation)

    def noter_point(self, quand: str = "") -> bool:
        """Point du matin marqué « Vu » (une seule ligne gardée). Ne lève jamais."""
        quand = quand or maintenant()
        if not DATE.fullmatch(quand):
            return False

        def operation(c):
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute("DELETE FROM points")
                c.execute("INSERT INTO points (vu_le) VALUES (?)", (quand,))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        return self._au_mieux(operation)

    def ignorer_relance(self, cle: str, jours: int) -> bool:
        """Masque une relance ``jours`` jours (clé : règle, objet et état). Ne lève jamais."""
        cle = str(cle or "").strip()[:MESSAGE_MAX]
        if not cle or int(jours) < 1:
            return False
        jusqu_au = (datetime.now() + timedelta(days=int(jours))).isoformat(timespec="seconds")
        return self._au_mieux(lambda c: c.execute(
            "INSERT INTO relances_ignorees (cle, jusqu_au) VALUES (?, ?) ON CONFLICT(cle) DO UPDATE SET "
            "jusqu_au = excluded.jusqu_au", (cle, jusqu_au)))

    # --- rétention ----------------------------------------------------------------------------------------------
    def purger(self) -> int:
        """Supprime les événements plus vieux que la rétention, puis les plus anciens au-delà de LIGNES_MAX.
        Renvoie le nombre de lignes supprimées (0 si le journal est indisponible)."""
        limite = (datetime.now() - timedelta(days=self.retention_jours)).isoformat(timespec="seconds")
        supprimes = 0
        try:
            with closing(self._connexion()) as c:
                supprimes += c.execute("DELETE FROM evenements WHERE horodatage < ?", (limite,)).rowcount
                supprimes += c.execute("DELETE FROM evenements WHERE id <= (SELECT id FROM evenements ORDER BY id "
                                       "DESC LIMIT 1 OFFSET ?)", (self.lignes_max,)).rowcount
                c.execute("DELETE FROM tickets WHERE evenement NOT IN (SELECT id FROM evenements)")
                c.execute("DELETE FROM etat_tickets WHERE vu_le < ?", (limite,))
                c.execute("DELETE FROM relances_ignorees WHERE jusqu_au < ?", (maintenant(),))
        except (sqlite3.Error, OSError):
            return 0
        self._derniere_purge = time.monotonic()
        return supprimes

    def _purger_si_besoin(self) -> None:
        if not self._derniere_purge or time.monotonic() - self._derniere_purge > PURGE_INTERVALLE_S:
            self.purger()

    # --- lecture (lève JournalErreur) ---------------------------------------------------------------------------
    @staticmethod
    def _evenement(ligne, tickets: dict) -> Evenement:
        try:
            details = json.loads(ligne[11] or "{}")
        except ValueError:
            details = {}
        return Evenement(id=ligne[0], horodatage=ligne[1], type=ligne[2], agent=ligne[3], decision=ligne[4],
                         extrait=ligne[5], ticket=ligne[6], projet=ligne[7], cout_usd=ligne[8], tours=ligne[9],
                         duree_s=ligne[10], details=details, tickets=tickets.get(ligne[0], []))

    def evenements(self, depuis: str = "", types=(), limite: int = 200, cle: str = "") -> list[Evenement]:
        """Événements du plus récent au plus ancien ; ``depuis`` : horodatage ISO minimal ; ``cle`` : ticket."""
        conditions, valeurs = [], []
        if depuis:
            conditions.append("e.horodatage >= ?")
            valeurs.append(depuis)
        if types:
            conditions.append(f"e.type IN ({','.join('?' * len(types))})")
            valeurs += list(types)
        if cle:
            conditions.append("e.id IN (SELECT evenement FROM tickets WHERE cle = ?)")
            valeurs.append(cle.upper())
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        def operation(c):
            lignes = c.execute(f"SELECT e.id, e.horodatage, e.type, e.agent, e.decision, e.extrait, e.ticket, "
                               f"e.projet, e.cout_usd, e.tours, e.duree_s, e.details FROM evenements e {where} "
                               f"ORDER BY e.id DESC LIMIT ?", (*valeurs, int(limite))).fetchall()
            ids = [l[0] for l in lignes]
            tickets: dict[int, list] = {}
            if ids:
                for evenement, t in c.execute(f"SELECT evenement, cle FROM tickets WHERE evenement IN "
                                              f"({','.join('?' * len(ids))}) ORDER BY rowid", ids):
                    tickets.setdefault(evenement, []).append(t)
            return [self._evenement(l, tickets) for l in lignes]
        return self._lire(operation)

    def ticket(self, cle: str, limite: int = 10) -> dict:
        """Vue consolidée d'un ticket : dernières questions et redirections, dernier état connu de l'analyse
        d'agent-lancement (relevé par le tableau de bord), coût cumulé des questions au manager."""
        cle = (cle or "").strip().upper()
        if not TICKET.fullmatch(cle):
            raise JournalErreur(f"clé de ticket invalide : « {cle} » (format attendu : PROJET-123)")
        evenements = self.evenements(cle=cle, types=("question", "redirection"), limite=500)

        def etat(c):
            ligne = c.execute("SELECT analyse, a_valider, analyse_modifiee, vu_le FROM etat_tickets WHERE cle = ?",
                              (cle,)).fetchone()
            return {"analyse": ligne[0], "a_valider": bool(ligne[1]), "analyse_modifiee": ligne[2],
                    "vu_le": ligne[3]} if ligne else {}
        questions = [e for e in evenements if e.type == "question"]
        return {
            "cle": cle,
            "questions": questions[:limite],
            "redirections": [e for e in evenements if e.type == "redirection"][:limite],
            "nb_questions": len(questions),
            "cout_manager_usd": round(sum(e.cout_usd or 0 for e in questions), 4),
            "derniere_activite": evenements[0].horodatage if evenements else "",
            "analyse": self._lire(etat),
        }

    def tickets_recents(self, jours: int = 7, limite: int = 20) -> list[dict]:
        """Tickets vus récemment dans le journal ou dont l'analyse (agent-lancement) a été modifiée récemment, du plus
        récent au plus ancien : [{cle, derniere_activite, nb_evenements, analyse, a_valider}]."""
        depuis = (datetime.now() - timedelta(days=jours)).isoformat(timespec="seconds")

        def operation(c):
            vus: dict[str, dict] = {}
            for cle, dernier, nombre in c.execute(
                    "SELECT t.cle, MAX(e.horodatage), COUNT(*) FROM tickets t JOIN evenements e ON e.id = t.evenement "
                    "WHERE e.horodatage >= ? GROUP BY t.cle", (depuis,)):
                vus[cle] = {"cle": cle, "derniere_activite": dernier, "nb_evenements": nombre, "analyse": "",
                            "a_valider": False}
            for cle, analyse, a_valider, modifiee in c.execute(
                    "SELECT cle, analyse, a_valider, analyse_modifiee FROM etat_tickets"):
                if cle not in vus and modifiee < depuis:
                    continue
                v = vus.setdefault(cle, {"cle": cle, "derniere_activite": modifiee, "nb_evenements": 0})
                v.update(analyse=analyse, a_valider=bool(a_valider))
                v["derniere_activite"] = max(v["derniere_activite"], modifiee)
            return sorted(vus.values(), key=lambda v: v["derniere_activite"], reverse=True)[:limite]
        return self._lire(operation)

    def relances_ignorees(self) -> set:
        """Clés des relances masquées en ce moment."""
        return self._lire(lambda c: {cle for (cle,) in c.execute(
            "SELECT cle FROM relances_ignorees WHERE jusqu_au > ?", (maintenant(),))})

    def dernier_point(self) -> str:
        """Horodatage ISO du dernier point du matin marqué « Vu », "" s'il n'y en a jamais eu."""
        return self._lire(lambda c: (c.execute("SELECT MAX(vu_le) FROM points").fetchone() or [""])[0] or "")

    def bilan(self, depuis: str) -> dict:
        """Activité depuis ``depuis`` (ISO) : {questions, cout_usd, sans_cout, redirections: {agent: n},
        analyses_a_valider: [{cle, analyse, modifiee}] (analyses passées « à valider » depuis, dernier relevé)}."""
        def operation(c):
            n, total, inconnus = c.execute(
                "SELECT COUNT(*), COALESCE(SUM(cout_usd), 0), SUM(cout_usd IS NULL) FROM evenements "
                "WHERE type = 'question' AND horodatage >= ?", (depuis,)).fetchone()
            redirections = dict(c.execute(
                "SELECT agent, COUNT(*) FROM evenements WHERE type = 'redirection' AND horodatage >= ? "
                "GROUP BY agent ORDER BY COUNT(*) DESC, agent", (depuis,)).fetchall())
            analyses = [{"cle": cle, "analyse": analyse, "modifiee": modifiee} for cle, analyse, modifiee in c.execute(
                "SELECT cle, analyse, analyse_modifiee FROM etat_tickets WHERE a_valider = 1 AND "
                "analyse_modifiee >= ? ORDER BY analyse_modifiee DESC, cle", (depuis,))]
            return {"questions": n, "cout_usd": round(total, 4), "sans_cout": inconnus or 0,
                    "redirections": redirections, "analyses_a_valider": analyses}
        return self._lire(operation)

    def couts(self, jours: int) -> dict:
        """Coût des questions au manager sur la période : {questions, cout_usd, sans_cout}."""
        depuis = (datetime.now() - timedelta(days=jours)).isoformat(timespec="seconds")

        def operation(c):
            n, total, inconnus = c.execute(
                "SELECT COUNT(*), COALESCE(SUM(cout_usd), 0), SUM(cout_usd IS NULL) FROM evenements "
                "WHERE type = 'question' AND horodatage >= ?", (depuis,)).fetchone()
            return {"questions": n, "cout_usd": round(total, 4), "sans_cout": inconnus or 0}
        return self._lire(operation)
