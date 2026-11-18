"""IHM du manager (port 8500), lancée par le superviseur : ``lancer.bat``.

Onglets : Assistant et Tableau de bord (lots suivants), Agents (état, lancement, arrêt, journal), Configuration.
"""

from pathlib import Path

import streamlit as st

from manager import registre as reg
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


def onglet_configuration() -> None:
    fichier = reg.FICHIER if reg.FICHIER.exists() else reg.EXEMPLE
    st.markdown(f"Registre des agents : `{fichier}`")
    if fichier == reg.EXEMPLE:
        st.info("Aucun `agents.toml` : le modèle `agents.example.toml` est utilisé. Copie-le en `agents.toml` "
                "pour l'adapter à ton poste (dossiers, ports), puis relance le manager.")
    try:
        registre = reg.charger()
    except reg.RegistreErreur as e:
        st.error(str(e))
        return
    st.dataframe([{"Agent": a.titre, "Port": a.port, "Mode": a.mode, "Dossier": str(a.dossier),
                   "Dossier présent": Path(a.dossier).is_dir()} for a in registre.agents],
                 hide_index=True, width="stretch")
    st.caption("La configuration partagée (accès, référentiel des applications Odatio) arrive au lot 2.")


st.title("🧭 Manager Savoye")
assistant, tableau, agents, configuration = st.tabs(["Assistant", "Tableau de bord", "Agents", "Configuration"])
with assistant:
    st.info("Bientôt : pose ta question ici ; le manager répond, interroge les agents ou te redirige (lot 4).")
with tableau:
    st.info("Bientôt : tickets en cours, analyses à valider, jobs Jenkins, patchs livrés (lot 6).")
with agents:
    onglet_agents()
with configuration:
    onglet_configuration()
