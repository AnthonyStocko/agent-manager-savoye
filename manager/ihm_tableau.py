"""Onglet Tableau de bord du manager (lot 6) : affichage des blocs de ``tableau.py``, sans appel au modèle.

En tête, le point du matin (lot 6 bis, ``initiatives.py``), une fois par jour jusqu'au clic sur « Vu ». Dans le
bloc Alertes, les relances (lot 6 bis), avec « Ignorer ». Point et relances sont recalculés hors du cache (lecture
du journal seule), pour qu'un clic sur « Vu » ou « Ignorer » prenne effet tout de suite.

Rafraîchi toutes les RAFRAICHISSEMENT_S secondes (``st.fragment(run_every=…)``). Le calcul est mis en cache
(CACHE_S) pour toutes les sessions : plusieurs onglets de navigateur ne relancent pas le serveur MCP de scrum-master
chacun de leur côté.
"""

from __future__ import annotations

import streamlit as st

from manager import configuration as conf
from manager import initiatives
from manager import journal as jr
from manager import registre as reg
from manager import superviseur as sup
from manager import tableau
from manager.pilote import Pilote

RAFRAICHISSEMENT_S = 60
CACHE_S = 50
RELANCES_AFFICHEES = 5   # au-delà : dans un dépliant
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


def _marquer_vu(journal: jr.Journal) -> None:
    """Rappel du bouton « Vu » : exécuté avant la réexécution, le point n'est donc plus affiché ensuite."""
    if not journal.noter_point():
        st.session_state["point_erreur"] = journal.derniere_erreur or "erreur inconnue"


def _point(point: initiatives.Point, journal: jr.Journal) -> None:
    titre, bouton = st.columns([5, 1])
    periode = (f"ces {initiatives.POINT_JOURS_MAX} derniers jours" if point.premier else
               f"depuis le {point.depuis[:10]} à {point.depuis[11:16]}")
    titre.markdown(f"**☀️ Point du matin** · :gray[{periode}]")
    bouton.button("Vu", width="stretch", help="Masque le point jusqu'à demain", on_click=_marquer_vu,
                  args=(journal,))
    if st.session_state.get("point_erreur"):
        st.caption(f"⚠️ Journal indisponible, point non enregistré : {st.session_state.pop('point_erreur')}")
    for r in point.rubriques:
        lignes = [f"[{l['texte']}]({l['lien']})" if l["lien"] else l["texte"] for l in r.lignes]
        if r.autres:
            lignes.append(f":gray[… et {r.autres} autre(s)]")
        st.markdown(f"*{r.titre}*  \n" + "  \n".join(lignes))


def _seuils() -> dict:
    """Seuils des relances de commun.toml ; valeurs par défaut si la configuration est illisible."""
    try:
        return conf.charger().seuils()
    except conf.ConfigurationErreur:
        return dict(conf.RELANCES)


def _ignorer(journal: jr.Journal, cle: str, jours: int) -> None:
    """Rappel du bouton « Ignorer » : la relance n'est plus affichée dès la réexécution."""
    if not journal.ignorer_relance(cle, jours):
        st.session_state["relance_erreur"] = journal.derniere_erreur or "erreur inconnue"


def _alertes(bloc: tableau.Bloc, relances: list, journal: jr.Journal, jours: int) -> None:
    if not bloc.lignes and not relances:
        st.success("Aucune alerte.", icon=":material/check_circle:")
        return
    for a in bloc.lignes:
        st.markdown(f"{NIVEAUX.get(a['niveau'], '🔵')} {a['texte']}")
    if st.session_state.get("relance_erreur"):
        st.caption(f"⚠️ Journal indisponible, relance non ignorée : {st.session_state.pop('relance_erreur')}")
    def relance(r) -> None:
        gauche, droite = st.columns([5, 1])
        gauche.markdown("🔔 " + (f"[{r.texte}]({r.lien})" if r.lien else r.texte))
        droite.button("Ignorer", key=f"ignorer-{r.cle}", width="stretch", on_click=_ignorer,
                      args=(journal, r.cle, jours), help=f"Masque cette relance {jours} jour(s) ; elle revient si "
                                                         "la situation change")
    for r in relances[:RELANCES_AFFICHEES]:
        relance(r)
    if len(relances) > RELANCES_AFFICHEES:
        with st.expander(f"{len(relances) - RELANCES_AFFICHEES} autre(s) relance(s)"):
            for r in relances[RELANCES_AFFICHEES:]:
                relance(r)


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


ICONES_BOUCLE = {"en cours": "🟡", "terminée": "🟢", "abandonnée": "🟠"}


def _boucles(bloc: tableau.Bloc) -> None:
    """Boucles de correction autonome d'agent-cucumber : information seulement, rien n'est à valider ici."""
    if not _entete(bloc):
        if not bloc.erreur and not bloc.note:
            st.caption("Aucune boucle de correction.")
        return
    st.caption("agent-dev-tool-savoye corrige l'agent Cucumber et fusionne sur main sans validation (tests et "
               "contrôle anti-triche) : affiché pour information.")
    for b in bloc.lignes[:5]:
        gauche, droite = st.columns([5, 1])
        restants = ", ".join(f"{c} {n}" for c, n in b["categories"].items() if n) or "aucun échec"
        texte = (f"{ICONES_BOUCLE.get(b['etat'], '⚪')} **{b['client']}** · {b['etat']}, itération "
                 f"{b['iteration']}/{b['iterations_max']} · échecs {b['echecs_avant']} → {b['echecs_apres']} "
                 f"({restants}) · {_dollars(b['cout_usd'])}")
        if b["raison"]:
            texte += f"  \n:gray[{b['raison']}]"
        for c in b["corrections"]:
            texte += f"  \n🛠 correction {c}"
        for a in b["annulations"]:
            texte += f"  \n↩️ retour arrière {a}"
        gauche.markdown(texte)
        if b.get("lien"):
            droite.link_button("Relancer", b["lien"], help="Demande préremplie vers dev-tool (échecs dus à l'agent) "
                                                            "ou agent Cucumber (défauts de l'application)")


STATUTS_PATCH = {"jointe": "🟢 en pièce jointe Jira", "non_jointe": "🟠 NON joint dans Jira",
                 "non_verifiee": "⚪ pièce jointe non vérifiée"}


def _patchs(bloc: tableau.Bloc) -> None:
    if not _entete(bloc):
        if not bloc.erreur and not bloc.note:
            st.caption("Aucun patch livré sur la période.")
        return
    for p in bloc.lignes:
        gauche, droite = st.columns([5, 1])
        etat = STATUTS_PATCH.get(p["statut"], p["statut"])
        if p["statut"] == "non_jointe":
            etat += " : " + ", ".join(p["manquants"])
        elif p["statut"] == "non_verifiee" and p["raison"]:
            etat += f" ({p['raison']})"
        gauche.markdown(f"**{p['cle']}** · {', '.join(p['fichiers'])}  \n"
                        f":gray[livré le {p['livre_le']}] · {etat}")
        if p.get("lien"):
            droite.link_button("Ouvrir", p["lien"], help="Scrum Master, onglet Lancement, ticket prérempli")


def _retrospectives(bloc: tableau.Bloc) -> None:
    if not _entete(bloc):
        if not bloc.erreur and not bloc.note:
            st.caption("Aucune rétrospective.")
        return
    for r in bloc.lignes[:5]:
        gauche, droite = st.columns([5, 1])
        ajuste = f" (ajusté {r['score_ajuste']}/100)" if r["score_ajuste"] is not None else ""
        gauche.markdown(f"**{r['cle']}** · score {r['score']}/100{ajuste}  \n"
                        f":gray[le {r['date']}] · " + (f"{r['ecarts']} écart(s) expliqué(s)" if r["analyse"]
                                                       else "analyse indisponible"))
        if r.get("lien"):
            droite.link_button("Ouvrir", r["lien"], help="Agent Rétrospective, ticket prérempli")
    if not bloc.lignes:
        st.caption("Aucune rétrospective.")
    propositions = bloc.resume.get("propositions") or []
    st.markdown(f"Propositions d'amélioration en attente : **{len(propositions)}**")
    for p in propositions[:5]:
        gauche, droite = st.columns([5, 1])
        gauche.markdown(f"{p['libelle']}  \n:gray[{p['levier']} · {p['tickets']} ticket(s) · en attente depuis "
                        f"{p['depuis']}]")
        if p.get("lien"):
            droite.link_button("Valider", p["lien"], help="Agent Rétrospective, onglet Propositions : valider, "
                                                         "rejeter ou reporter")


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
    journal = jr.Journal()
    point = initiatives.point_du_matin(journal, t)
    if point:
        with st.container(border=True):
            _point(point, journal)
    with st.container(border=True):
        seuils = _seuils()
        _alertes(t.alertes, initiatives.relances(journal, t, seuils), journal, seuils["ignorer_jours"])
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
            _boucles(t.boucles)
        with st.container(border=True):
            _couts(t.couts)
        with st.container(border=True):
            _patchs(t.patchs)
        with st.container(border=True):
            _retrospectives(t.retrospectives)
