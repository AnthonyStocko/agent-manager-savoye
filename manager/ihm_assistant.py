"""Onglet Assistant du manager : questions à l'assistant (routeur.py), réponses et redirections vers les agents."""

from __future__ import annotations

import asyncio

import streamlit as st

from manager import configuration as conf
from manager import registre as reg
from manager import routeur

EXEMPLES = ("Où en est l'analyse de ODA-1234 ?", "Un job Cucumber tourne-t-il sur Jenkins ?",
            "Quels agents tournent en ce moment ?")


def _libelle(nom: str, titres: dict) -> str:
    morceaux = nom.split("__")
    if len(morceaux) == 3:
        serveur = "Manager" if morceaux[1] == routeur.SERVEUR else titres.get(morceaux[1], morceaux[1])
        return f"{serveur} · {morceaux[2].replace('_', ' ')}"
    return nom


def _redirection(r: routeur.Redirection) -> None:
    with st.container(border=True):
        details = " · ".join(x for x in (r.ticket, r.projet) if x)
        st.markdown(f"**→ À faire dans {r.titre}**" + (f" — {details}" if details else ""))
        if r.resume:
            st.markdown(r.resume)
        if r.question:
            st.caption("Question préremplie dans l'agent (à envoyer toi-même) :")
            st.code(r.question, language="text")
        if r.demande:
            st.caption("Demande préremplie (et prête à coller) :")
            st.code(r.demande, language="text")
        st.link_button(f"Ouvrir {r.titre}", r.url, icon=":material/open_in_new:",
                       help="L'agent s'ouvre au bon endroit, prérempli ; il ne lance rien sans toi.")


def _afficher(echange: dict) -> None:
    with st.chat_message(echange["role"]):
        st.markdown(echange["texte"])
        for r in echange.get("redirections", []):
            _redirection(r)
        if echange.get("outils"):
            st.caption("Consulté : " + ", ".join(echange["outils"]))
        if echange.get("pied"):
            st.caption(echange["pied"])


def afficher() -> None:
    etat = st.session_state
    etat.setdefault("echanges", [])
    etat.setdefault("reprise", "")
    try:
        titres = {a.nom: a.titre for a in reg.charger().agents}
    except reg.RegistreErreur as e:
        st.error(str(e))
        return

    entete, bouton = st.columns([4, 1])
    entete.caption("Pose ta question : le manager répond, interroge les agents en lecture seule ou te redirige vers "
                   "le bon agent. Il ne modifie jamais rien lui-même.")
    if bouton.button("Nouvelle conversation", width="stretch", disabled=not etat.echanges):
        etat.echanges, etat.reprise = [], ""
        st.rerun()
    if not etat.echanges:
        st.caption("Exemples : " + " · ".join(f"« {e} »" for e in EXEMPLES))
    for echange in etat.echanges:
        _afficher(echange)

    question = st.chat_input("Ta question…")
    if not question:
        return
    etat.echanges.append({"role": "user", "texte": question})
    _afficher(etat.echanges[-1])
    consultes = []
    with st.chat_message("assistant"):
        with st.status("Le manager s'en occupe…", expanded=False) as statut:
            def sur_outil(nom, entree):
                consultes.append(_libelle(nom, titres))
                statut.update(label=consultes[-1] + "…")
                statut.write(consultes[-1])
            try:
                reponse = asyncio.run(routeur.Routeur().demander(question, etat.reprise, sur_outil))
            except (conf.ConfigurationErreur, reg.RegistreErreur) as e:
                statut.update(label="Configuration à corriger", state="error")
                st.error(str(e))
                etat.echanges.pop()
                return
            except Exception as e:  # CLI Claude absente, compte non connecté…
                statut.update(label="Échec", state="error")
                st.error(f"Le manager n'a pas pu répondre : {e}. Vérifie le compte Claude dans Configuration › Accès.")
                etat.echanges.pop()
                return
            statut.update(label="Terminé" if not reponse.erreur else f"Interrompu : {reponse.message}",
                          state="complete" if not reponse.erreur else "error")
    etat.reprise = reponse.session_id or etat.reprise
    cout = f"{reponse.cout_usd:.3f} $" if reponse.cout_usd is not None else "coût inconnu"
    etat.echanges.append({"role": "assistant", "texte": reponse.texte or "(pas de réponse)",
                          "redirections": reponse.redirections, "outils": list(dict.fromkeys(consultes)),
                          "pied": f"{reponse.tours} étape(s) · {cout}"})
    st.rerun()
