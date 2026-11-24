"""Récapitulatif des travaux en cours de tous les agents (registre .tmp/taches du socle), avec un lien vers chacun.

Lecture seule : la reprise et l'arrêt d'une tâche se font dans l'IHM de son agent (panneau « Travaux en cours »).
"""

import streamlit as st

from agent_socle import taches
from manager import registre as reg

STATUTS = {"en_cours": "⏳ en cours", "terminee": "✅ terminée", "echec": "❌ en échec", "interrompue": "⚠️ interrompue",
           "arretee": "⏹️ arrêtée"}


def urls() -> dict:
    try:
        return {a.nom: a.url for a in reg.charger().agents}
    except Exception:  # noqa: BLE001 — registre illisible : pas de lien
        return {}


@st.fragment(run_every=10)
def afficher() -> None:
    actives = [t for t in taches.lister() if t["statut"] == "en_cours"]
    st.subheader("Travaux en cours")
    if not actives:
        st.caption("Aucun traitement en cours dans les agents.")
        return
    adresses = urls()
    for tache in actives:
        lien = adresses.get(tache["agent"], "")
        st.markdown(f"**{tache['agent']}** · {tache['etape']} {tache.get('cible') or ''} — "
                    f"{STATUTS.get(tache['statut'], tache['statut'])} depuis {taches.duree(tache)}"
                    + (f" · {tache['auteur']}" if tache.get("auteur") else "")
                    + (f" · [ouvrir l'agent]({lien})" if lien else ""))
