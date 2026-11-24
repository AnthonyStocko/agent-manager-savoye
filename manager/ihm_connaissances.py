"""Onglet « Connaissances partagées » : repris tel quel de l'onglet Connaissances d'agent-scrum-master."""
import os

import os
import streamlit as st
import subprocess
import sys
from agent_socle import knowledge_tools, skills
from dotenv import load_dotenv

from agent_socle.chemins import DATA_DIR

# Réglages des agents (.env de AGENT_DATA_DIR) sans écraser ceux du manager
load_dotenv(os.path.join(DATA_DIR, ".env"))


def render_knowledge_outbox() -> None:
    """
    Documents en file locale (partage indisponible lors du dépôt) : nombre affiché, nouvel essai au premier
    affichage de la session puis à la demande.
    """
    if not knowledge_tools.enabled():
        return
    retry = not st.session_state.get("knowledge_outbox_tried") and knowledge_tools.outbox_count()
    with st.container(horizontal=True, vertical_alignment="center"):
        retry = st.button("Déposer les documents en attente", icon=":material/cloud_upload:",
                          disabled=not knowledge_tools.outbox_count()) or retry
        pending_caption = st.empty()
    if retry:
        st.session_state["knowledge_outbox_tried"] = True
        with st.spinner("Dépôt des documents en attente…"):
            flushed = knowledge_tools.flush_outbox()
        for message in flushed["messages"]:
            st.caption(message)
    pending = knowledge_tools.outbox_count()
    pending_caption.caption(f":orange[{pending} document(s) en attente de dépôt] (partage indisponible lors de "
                            f"l'analyse, file locale .tmp/knowledge_outbox)" if pending else
                            "Aucun document en attente de dépôt.")


KNOWLEDGE_ALL_TYPES = "Tous les types"


def open_document(path: str) -> None:
    """Ouvre un document avec l'application associée du poste (l'IHM tourne sur le poste de l'utilisateur)."""
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def render_knowledge_import() -> None:
    """Import ponctuel de .md existants (A_REVOIR.md, AUDIT.md, COUVERTURE.md...) : projet, type et tickets choisis."""
    with st.expander("Importer des analyses Markdown existantes", icon=":material/upload_file:"):
        with st.form("knowledge_import", clear_on_submit=True):
            uploaded = st.file_uploader("Fichiers .md", type=["md"], accept_multiple_files=True)
            with st.container(horizontal=True):
                import_project = st.text_input("Projet Jira", placeholder="PROJ")
                import_client = st.text_input("Client (dossier, facultatif)",
                                              help="Dossier du document : le client s'il est renseigné, sinon le "
                                                   "projet Jira.")
                import_type = st.selectbox("Type", knowledge_tools.KNOWLEDGE_TYPES,
                                           index=knowledge_tools.KNOWLEDGE_TYPES.index("autre"))
            import_tickets = st.text_input("Tickets", placeholder="PROJ-1, PROJ-2")
            import_subject = st.text_input("Sujet", placeholder="Vide = nom du fichier")
            import_clicked = st.form_submit_button("Importer", icon=":material/upload:")
        if not import_clicked:
            return
        if not uploaded or not import_project.strip():
            st.error("Choisissez au moins un fichier et renseignez le projet Jira.", icon=":material/error:")
            return
        for file in uploaded:
            result = knowledge_tools.import_text(
                file.name, file.getvalue().decode("utf-8-sig", errors="replace"), import_project.strip().upper(),
                import_type, [t for t in import_tickets.split(",")], import_client.strip(), import_subject.strip())
            ok = result["statut"] in (knowledge_tools.PUBLISHED, knowledge_tools.QUEUED)
            (st.success if ok else st.error)(f"{file.name} : {result['message']}",
                                             icon=":material/check_circle:" if ok else ":material/error:")
        st.session_state.pop("knowledge_index", None)


def render_knowledge_status(doc: dict, meta: dict, documents: list) -> None:
    """Action « marquer obsolète / remplacé par » : en-tête seul modifié, par écriture atomique."""
    folder = doc["chemin"].rsplit("/", 1)[0]
    # Documents du même projet et du même type d'abord
    others = sorted((d["chemin"] for d in documents if d["chemin"] != doc["chemin"]),
                    key=lambda p: (p.rsplit("/", 1)[0] != folder, p))
    current = meta.get("remplace_par", "")
    with st.form(f"knowledge_status_{doc['chemin']}"):
        new_status = st.segmented_control("Statut", knowledge_tools.STATUSES,
                                          default=meta.get("statut", knowledge_tools.STATUS_ACTIVE), required=True)
        replacement = st.selectbox("Remplacé par (statut « remplacé »)", [""] + others,
                                   index=([""] + others).index(current) if current in others else 0)
        save_clicked = st.form_submit_button("Enregistrer le statut", icon=":material/save:")
    if save_clicked:
        result = knowledge_tools.set_status(doc["chemin"], new_status or knowledge_tools.STATUS_ACTIVE, replacement)
        if result["ok"]:
            st.session_state["knowledge_index"] = knowledge_tools.load_index()
            st.toast(result["message"], icon=":material/check_circle:")
            st.rerun()
        st.error(result["message"], icon=":material/error:")


def render_knowledge_document(doc: dict, documents: list) -> None:
    """Aperçu d'un document : en-tête, résumé, corps ; ouverture, téléchargement et changement de statut."""
    try:
        path = knowledge_tools.absolute_path(doc["chemin"])
        meta, body = knowledge_tools.read_document(path)
    except (ValueError, OSError, UnicodeDecodeError) as e:
        st.error(f"Document illisible : {e}", icon=":material/error:")
        return
    with st.container(border=True):
        st.markdown(f"**{meta.get('sujet') or doc['chemin']}**")
        status = meta.get("statut", knowledge_tools.STATUS_ACTIVE)
        if meta.get("remplace_par"):
            status += f" par {meta['remplace_par']}"
        st.caption(f"`{doc['chemin']}` · {meta.get('type', '?')} · {str(meta.get('date', '?')).replace('T', ' ')} · "
                   f"{meta.get('auteur', '?')} · statut : {status} · tickets : "
                   f"{', '.join(meta.get('tickets') or []) or 'aucun'} · agent {meta.get('version_agent', '?')}")
        if meta.get("resume"):
            st.markdown(meta["resume"])
        with st.container(horizontal=True):
            if st.button("Ouvrir", icon=":material/open_in_new:", key="knowledge_open"):
                try:
                    open_document(path)
                except OSError as e:
                    st.error(f"Ouverture impossible : {e}", icon=":material/error:")
            st.download_button("Télécharger", data=knowledge_tools.render_document(meta, body),
                               file_name=os.path.basename(path), mime="text/markdown", icon=":material/download:",
                               key="knowledge_download")
        with st.container(height=420):
            st.markdown(body)
    render_knowledge_status(doc, meta, documents)


def render_skills() -> None:
    """Compétences acquises par l'agent (auto-amélioration) : liste, lecture, désactivation, suppression."""
    all_skills = skills.load_skills(include_disabled=True)
    active = sum(1 for k in all_skills if k["actif"])
    with st.expander(f"🧠 Compétences acquises par l'agent : {len(all_skills)} ({active} actives)",
                     expanded=False):
        st.caption("Après une tâche complexe réussie et PROUVÉE (test rendu vert sur Jenkins, initialisation repassée), "
                   "l'agent rédige la procédure qui lui a permis de réussir. Il la relit avant les tâches semblables "
                   f"suivantes. Rangées dans : {skills.skills_dir()}" + (" (partagé avec l'équipe)" if
                                                                      os.getenv("AGENT_KNOWLEDGE_DIR", "").strip()
                                                                      else " (local)") + "."
                   + ("" if skills.enabled() else " ⚠️ Apprentissage désactivé (SKILLS_AUTO=0)."))
        if not all_skills:
            st.caption("Aucune compétence pour l'instant : elles apparaissent après les premières corrections "
                       "réussies.")
            return
        for skill in sorted(all_skills, key=lambda k: (not k["actif"], -k["utilisations"], k.get("nom", ""))):
            with st.container(border=True):
                st.markdown(f"**{skill.get('nom', skill['fichier'])}** — {skill.get('description', '')}"
                            + ("" if skill["actif"] else " :gray[(désactivée)]"))
                st.caption(f"{skills.DOMAINS.get(skill.get('domaine', ''), skill.get('domaine', ''))} · utilisée "
                           f"{skill['utilisations']} fois · mise à jour le {skill.get('maj_le', '')[:16].replace('T', ' ')}"
                           f" · origine : {skill.get('origine', '')}")
                with st.container(horizontal=True):
                    show = st.toggle("Voir la procédure", key=f"skill_show_{skill['fichier']}")
                    if st.button("Réactiver" if not skill["actif"] else "Désactiver", type="tertiary",
                                 key=f"skill_toggle_{skill['fichier']}"):
                        skills.set_active(skill["chemin"], not skill["actif"])
                        st.rerun()
                    if st.button("Supprimer", type="tertiary", key=f"skill_delete_{skill['fichier']}"):
                        os.remove(skill["chemin"])
                        st.rerun()
                if show:
                    st.markdown(skill["corps"])


def render_knowledge_tab() -> None:
    """Onglet 📚 : état du répertoire partagé, recherche, aperçu, statut et import."""
    st.subheader("Connaissances partagées")
    st.caption("Analyses déposées par l'agent dans le répertoire partagé (AGENT_KNOWLEDGE_DIR), consultées avant "
               "chaque nouvelle analyse comme des données à vérifier. Tout utilisateur du partage lit tous les "
               "documents.")
    render_skills()
    if not knowledge_tools.enabled():
        st.info("AGENT_KNOWLEDGE_DIR est vide : renseignez le répertoire partagé dans l'onglet ⚙️ Configuration.",
                icon=":material/info:")
        return
    state_box = st.container()
    render_knowledge_outbox()
    render_knowledge_import()

    with st.form("knowledge_search"):
        with st.container(horizontal=True):
            search_project = st.text_input("Projet ou client", key="knowledge_project")
            search_type = st.selectbox("Type", [KNOWLEDGE_ALL_TYPES, *knowledge_tools.KNOWLEDGE_TYPES],
                                       key="knowledge_type")
            search_ticket = st.text_input("Ticket", key="knowledge_ticket")
            search_keyword = st.text_input("Mot-clé", key="knowledge_keyword")
        include_obsolete = st.checkbox("Inclure les documents obsolètes et remplacés", key="knowledge_obsolete")
        search_clicked = st.form_submit_button("Rechercher", icon=":material/search:")
    # Le partage n'est lu qu'à la demande : tous les onglets s'exécutent à chaque interaction
    if search_clicked:
        with st.spinner("Lecture des en-têtes du répertoire partagé…"):
            st.session_state["knowledge_index"] = knowledge_tools.load_index()
    index = st.session_state.get("knowledge_index")

    with state_box, st.container(horizontal=True):
        st.metric("Joignable", "non vérifié" if index is None else ("oui" if index["joignable"] else
                                                                    "non (dernier index connu)"), border=True)
        st.metric("Documents indexés", "—" if index is None else len(index["documents"]), border=True)
        st.metric("En attente de dépôt", knowledge_tools.outbox_count(), border=True)
    state_box.caption(f"Répertoire : {knowledge_tools.knowledge_dir()}")

    if index is None:
        st.caption("Lancez une recherche (critères vides : tous les documents actifs).")
        return
    documents = knowledge_tools.search_documents(
        index["documents"], search_project, "" if search_type == KNOWLEDGE_ALL_TYPES else search_type,
        search_ticket, search_keyword, include_obsolete)
    if not documents:
        st.info("Aucun document ne correspond à ces critères.", icon=":material/search_off:")
        return
    rows = [{"Date": str(d["meta"].get("date", "")).replace("T", " "), "Projet": d["chemin"].split("/", 1)[0],
             "Type": d["meta"].get("type", ""), "Sujet": d["meta"].get("sujet", ""),
             "Tickets": ", ".join(d["meta"].get("tickets") or []), "Auteur": d["meta"].get("auteur", ""),
             "Statut": d["meta"].get("statut", "")} for d in documents]
    st.caption(f"{len(documents)} document(s). Sélectionnez une ligne pour l'aperçu.")
    table = st.dataframe(rows, hide_index=True, on_select="rerun", selection_mode="single-row",
                         key="knowledge_table")
    selected = table.selection.rows
    if selected and selected[0] < len(documents):
        render_knowledge_document(documents[selected[0]], index["documents"])


def afficher() -> None:
    render_knowledge_tab()
