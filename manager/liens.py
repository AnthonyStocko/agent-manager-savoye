"""Liens de redirection préremplis vers les agents (lot 5).

Le manager ne fait rien à la place d'un agent : il ouvre l'agent au bon endroit, avec ce qu'il sait déjà. Le lien
porte des paramètres d'adresse que chaque agent lit à l'ouverture (et ignore s'il ne les connaît pas) :

    ?source=manager&page=<onglet>&ticket=<CLE>&projet=<application>&conversation=<identifiant>
     &question=<texte>&demande=<texte>&depot=<agent à faire évoluer>&contexte=<résumé>

- ``page`` : onglet de l'agent (ONGLETS) ; un onglet inconnu de l'agent est ignoré ;
- ``ticket`` : clé Jira (PROJET-123) ; ``projet`` : application de la configuration commune ;
- ``conversation`` : conversation enregistrée par l'agent Odatio (réponse de son outil ``question``) ;
- ``question`` (agents à assistant) et ``demande`` (dev-tool) : texte PRÉREMPLI, jamais envoyé sans l'utilisateur ;
- ``depot`` : dépôt cible de dev-tool ; ``contexte`` : résumé du manager, affiché par l'agent.

Aucun secret ni donnée nominative dans un lien : il passe par l'historique du navigateur. Les textes sont bornés
(TEXTE_MAX) pour garder l'adresse courte.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

# Onglets connus de chaque agent (nom du registre) : valeur du paramètre page
ONGLETS = {
    "scrum-master": ("integration", "lancement", "cucumber", "cucumber-avance", "revue", "assistant",
                     "connaissances"),
    "odatio": ("assistant",),
    "dev-tool": ("developper",),
}
PARAMETRES = ("page", "ticket", "projet", "conversation", "question", "demande", "depot", "contexte")
TEXTE_MAX = 1500
CONTEXTE_MAX = 600
_TICKET = re.compile(r"[A-Z][A-Z0-9_]+-\d+")
_SIMPLE = re.compile(r"[\w .@-]{1,80}")
_CONVERSATION = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9]{6}")


def _texte(valeur: str, limite: int) -> str:
    valeur = (valeur or "").strip()
    return valeur if len(valeur) <= limite else valeur[:limite - 1].rstrip() + "…"


def parametres(agent: str, page: str = "", ticket: str = "", projet: str = "", conversation: str = "",
               question: str = "", demande: str = "", depot: str = "", contexte: str = "") -> dict:
    """Paramètres validés du lien (valeurs vides ou invalides retirées)."""
    ticket = (ticket or "").strip().upper()
    valeurs = {
        "page": page if page in ONGLETS.get(agent, ()) else "",
        "ticket": ticket if _TICKET.fullmatch(ticket) else "",
        "projet": projet.strip() if _SIMPLE.fullmatch((projet or "").strip()) else "",
        "conversation": conversation.strip() if _CONVERSATION.fullmatch((conversation or "").strip()) else "",
        "question": _texte(question, TEXTE_MAX),
        "demande": _texte(demande, TEXTE_MAX),
        "depot": depot.strip() if _SIMPLE.fullmatch((depot or "").strip()) else "",
        "contexte": _texte(contexte, CONTEXTE_MAX),
    }
    retenus = {k: v for k, v in valeurs.items() if v}
    return {"source": "manager", **retenus} if retenus else {}


def construire(agent: str, url: str, **valeurs) -> str:
    """Adresse de l'agent complétée des paramètres préremplis (l'adresse seule s'il n'y a rien à transmettre)."""
    params = parametres(agent, **valeurs)
    return f"{url.rstrip('/')}/?{urlencode(params)}" if params else url
