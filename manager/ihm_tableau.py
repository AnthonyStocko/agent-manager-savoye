"""Onglet Tableau de bord du manager (lot 6) : affichage des blocs de ``tableau.py``, sans appel au modèle.

Rafraîchi toutes les RAFRAICHISSEMENT_S secondes (``st.fragment(run_every=…)``). Le calcul est mis en cache
(CACHE_S) pour toutes les sessions : plusieurs onglets de navigateur ne relancent pas le serveur MCP de scrum-master
chacun de leur côté.
"""

from __future__ import annotations

import streamlit as st

from manager import journal as jr
from manager import registre as reg
from manager import superviseur as sup
from manager import tableau
from manager.pilote import Pilote

RAFRAICHISSEMENT_S = 60
CACHE_S = 50
PASTILLES = {sup.EN_LIGNE: "🟢", sup.DEMARRAGE: "🟡", sup.EXTERNE: "🔵", sup.ARRETE: "⚪", sup.INJOIGNABLE: "🟠",
             sup.ECHEC: "🔴", sup.INSTALLATION: "🔴"}
NIVEAUX = {"erreur": "🔴", "attention": "🟠", "info": "🔵"}


@st.cache_data(ttl=CACHE_S, show_spinner=False)
def _calculer() -> tableau.Tableau:
    return tableau.construire(reg.charger(), jr.Journal(), Pilote())


def _dollars(valeur) -> str:
    return f"{valeur:.2f} $" if isinstance(valeur, int | float) else "—"


def _entete(bloc: tableau.Bloc) -> bool:
    """Titre, message d'erreur ou note ; False si le bloc n'a rien d'autre à afficher."""
    st.markdown(f"**{bloc.titre}**")
    if bloc.erreur:
        st.caption(f"⚠️ {bloc.erreur}")
    if bloc.note:
        st.caption(bloc.note)
    return bool(bloc.lignes or bloc.resume)


def _alertes(bloc: tableau.Bloc) -> None:
    if not bloc.lignes:
        st.success("Aucune alerte.", icon=":material/check_circle:")
        return
    for a in bloc.lignes:
        st.markdown(f"{NIVEAUX.get(a['niveau'], '🔵')} {a['texte']}")


def _agents(bloc: tableau.Bloc) -> None:
    if not _entete(bloc):
        return
    st.markdown("  \n".join(f"{PASTILLES.get(a['etat'], '⚪')} {a['titre']} — {a['etat']}" for a in bloc.lignes))
    st.caption("Détail, journal et commandes : onglet Agents.")


def _tickets(bloc: tableau.Bloc) -> None:
    if not _entete(bloc):
        st.caption("Aucun ticket vu ces 7 derniers jours.")
        return
    for t in bloc.lignes:
        gauche, droite = st.columns([5, 1])
        etat = t["analyse"] or "pas d'analyse connue"
        gauche.markdown(f"**{t['cle']}** · {etat}" + (" · ← à valider" if t["a_valider"] else "")
                        + f"  \n:gray[{t['derniere_activite'].replace('T', ' ')[:16]}"
                        + (f" · {t['nb_evenements']} échange(s) avec le manager" if t["nb_evenements"] else "") + "]")
        if t.get("lien"):
            droite.link_button("Ouvrir", t["lien"], help="Scrum Master, onglet Lancement, ticket prérempli")


def _analyses(bloc: tableau.Bloc) -> None:
    if not _entete(bloc):
        if not bloc.erreur:
            st.caption("Aucune analyse en attente de validation.")
        return
    for a in bloc.lignes:
        gauche, droite = st.columns([5, 1])
        gauche.markdown(f"**{a['cle']}** · {a['libelle']}  \n:gray[modifiée le {a['modifiee']}]")
        droite.link_button("Valider", a["lien"], help="Ouvre Scrum Master sur ce ticket ; la validation s'y fait")


def _jenkins(bloc: tableau.Bloc) -> None:
    if not _entete(bloc):
        return
    verrou = bloc.resume.get("verrou", "inconnu")
    st.markdown(f"Verrou Cucumber (un seul job à la fois, géré par Scrum Master) : **{verrou}**")
    clients = [l for l in bloc.lignes if l["type"] == "client"]
    if clients:
        st.markdown("  \n".join(f"{'🟡' if c['en_cours'] else '⚪'} {c['client']} : {c['etat']}" for c in clients))
    builds = [l for l in bloc.lignes if l["type"] == "build"]
    if builds:
        st.dataframe([{"Job": b["job"], "Build": f"#{b['numero']}", "Résultat": b["resultat"], "Début": b["debut"],
                       "Durée (min)": b["duree_min"]} for b in builds], hide_index=True, width="stretch")


def _couts(bloc: tableau.Bloc) -> None:
    _entete(bloc)
    r = bloc.resume
    colonnes = st.columns(4)
    for colonne, (cle, libelle) in zip(colonnes, (("manager_7j", "Manager · 7 j"), ("manager_30j", "Manager · 30 j"),
                                                  ("scrum_7j", "Scrum Master · 7 j"),
                                                  ("scrum_30j", "Scrum Master · 30 j"))):
        valeur = r.get(cle)
        if isinstance(valeur, dict):
            colonne.metric(libelle, _dollars(valeur["cout_usd"]), help=f"{valeur['questions']} question(s)"
                           + (f", dont {valeur['sans_cout']} sans coût connu" if valeur["sans_cout"] else ""))
        else:
            colonne.metric(libelle, _dollars(valeur))
    if bloc.lignes:
        with st.expander("Scrum Master : coût par étape (30 j)"):
            st.dataframe([{"Étape": l["etape"], "Appels": l["appels"], "Coût ($)": l["cout_usd"]}
                          for l in bloc.lignes], hide_index=True, width="stretch")


@st.fragment(run_every=RAFRAICHISSEMENT_S)
def afficher() -> None:
    entete, bouton = st.columns([5, 1])
    if bouton.button("Rafraîchir", width="stretch"):
        _calculer.clear()
    try:
        with st.spinner("Lecture des sources…"):
            t = _calculer()
    except reg.RegistreErreur as e:
        st.error(str(e))
        return
    entete.caption(f"Mis à jour le {t.genere_le.replace('T', ' à ')} · lecture seule, aucun appel au modèle · "
                   f"rafraîchi toutes les {RAFRAICHISSEMENT_S} s")
    with st.container(border=True):
        _alertes(t.alertes)
    gauche, droite = st.columns(2)
    with gauche:
        with st.container(border=True):
            _agents(t.agents)
        with st.container(border=True):
            _tickets(t.tickets)
        with st.container(border=True):
            _analyses(t.analyses)
    with droite:
        with st.container(border=True):
            _jenkins(t.jenkins)
        with st.container(border=True):
            _couts(t.couts)
        with st.container(border=True):
            _entete(t.patchs)
