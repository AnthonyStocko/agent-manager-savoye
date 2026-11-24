"""IHM du manager (port 8500), lancée par le superviseur : ``lancer.bat``.

Onglets : Assistant (questions au manager), Tableau de bord (lot 6 : journal, superviseur et sources des agents en
lecture, sans appel au modèle), Agents (état, lancement, arrêt, journal), Configuration
(accès communs, applications, import des réglages des agents, registre).
"""

import streamlit as st

from manager import ihm_assistant, ihm_configuration, ihm_connaissances, ihm_questions, ihm_tableau, ihm_travaux
from manager import superviseur as sup
from manager.pilote import Pilote, PiloteErreur

st.set_page_config(page_title="Manager Savoye", page_icon="🧭", layout="wide")

PASTILLES = {sup.EN_LIGNE: "🟢", sup.DEMARRAGE: "🟡", sup.EXTERNE: "🔵", sup.ARRETE: "⚪", sup.INJOIGNABLE: "🟠",
             sup.ECHEC: "🔴", sup.INSTALLATION: "🔴"}
ACTIONS = {"demarrer": "Démarrer", "arreter": "Arrêter", "redemarrer": "Redémarrer"}


def fin_du_journal(nom: str, lignes: int = 40) -> str:
    try:
        texte = (sup.DOSSIER_TMP / "logs" / f"{nom}.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(aucun journal)"
    return "\n".join(texte.splitlines()[-lignes:])


@st.fragment(run_every=5)
def onglet_agents() -> None:
    pilote = Pilote()
    try:
        agents = pilote.etat()
    except PiloteErreur as e:
        st.error(str(e))
        return
    for agent in agents:
        manager = agent["nom"] == sup.NOM_MANAGER
        with st.container(border=True):
            gauche, droite = st.columns([3, 2])
            pastille = PASTILLES.get(agent["etat"], "⚪")
            mode = "" if manager else (" · lancé à la demande" if agent["mode"] == "demande" else " · lancé avec le manager")
            gauche.markdown(f"**{agent['titre']}** {pastille} {agent['etat']}  \n"
                            f"[{agent['url']}]({agent['url']}){mode}")
            if agent["message"]:
                gauche.caption(agent["message"])
            if not manager:
                boutons = droite.columns(3)
                for colonne, (action, libelle) in zip(boutons, ACTIONS.items()):
                    if colonne.button(libelle, key=f"{action}-{agent['nom']}", width="stretch"):
                        try:
                            with st.spinner(f"{libelle} {agent['titre']}…"):
                                pilote.ordre(agent["nom"], action)
                        except PiloteErreur as e:
                            st.error(str(e))
                        st.rerun(scope="fragment")
            with st.expander("Journal"):
                st.code(fin_du_journal(agent["nom"]), language="text")


st.title("🧭 Manager Savoye")
assistant, tableau, agents, questions, connaissances, configuration = st.tabs(
    ["Assistant", "Tableau de bord", "Agents", "Assistant des agents", "Connaissances partagées", "Configuration"])
with assistant:
    ihm_assistant.afficher()
with tableau:
    ihm_travaux.afficher()
    ihm_tableau.afficher()
with agents:
    onglet_agents()
with questions:
    ihm_questions.afficher()
with connaissances:
    ihm_connaissances.afficher()
with configuration:
    ihm_configuration.afficher()
