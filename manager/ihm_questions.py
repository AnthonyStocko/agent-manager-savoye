"""Onglet « Assistant des agents » : questions sur le fonctionnement et les résultats des agents, reprises
telles quelles de l'onglet Assistant d'agent-scrum-master (main.py --step ask du manager)."""
import os

import os
import streamlit as st
import sys
import time
from agent_socle.ihm import interruption_cause, run_main_step
from manager import assistant
from dotenv import load_dotenv

from agent_socle.chemins import DATA_DIR

# Réglages des agents (.env de AGENT_DATA_DIR) sans écraser ceux du manager
load_dotenv(os.path.join(DATA_DIR, ".env"))


ASSISTANT_EXAMPLES = ["Pourquoi les tests Cucumber d'Apparel encore en échec échouent-ils ?",
                      "Que fait le bouton « Lancer » de l'onglet Cucumber ?",
                      "Qu'a corrigé l'agent lors de la dernière correction ?",
                      "Que contiennent les modifications à appliquer actuelles ?"]


def ask_assistant(question: str) -> None:
    """Question ajoutée à la conversation, réponse de l'agent (lecture seule) ajoutée à son tour."""
    messages = assistant.load_history()
    messages.append({"role": "user", "content": question, "date": time.strftime("%Y-%m-%dT%H:%M:%S")})
    assistant.save_history(messages)
    answer_file = os.path.join(assistant.ROOT, "reponse.md")
    if os.path.exists(answer_file):
        os.remove(answer_file)
    with st.spinner("L'assistant cherche dans l'application et ses résultats…"):
        code = run_main_step([sys.executable, "main.py", "--step", "ask", "--question", question,
                              "--answer-file", answer_file])
    if code == 0 and os.path.exists(answer_file):
        with open(answer_file, encoding="utf-8") as f:
            answer = f.read()
    else:
        answer = f"⚠️ Pas de réponse : {interruption_cause(code)}."
    messages.append({"role": "assistant", "content": answer, "date": time.strftime("%Y-%m-%dT%H:%M:%S")})
    assistant.save_history(messages)


def render_assistant_tab() -> None:
    st.markdown("### Poser une question à l'agent")
    st.caption("Sur ce que fait l'application (onglets, boutons, réglages) ou sur ses résultats (tests en échec et "
               "pourquoi, corrections faites, patchs, runs Jenkins importés, revues de PR, consommation). L'assistant "
               "lit le code de l'application et ses résultats enregistrés ; il ne modifie et ne lance rien.")
    messages = assistant.load_history()
    with st.container(horizontal=True):
        if st.button("Nouvelle conversation", icon=":material/add_comment:", disabled=not messages,
                     key="assistant_clear"):
            assistant.clear_history()
            st.rerun()
    if not messages:
        st.caption("Exemples :")
        with st.container(horizontal=True):
            for index, example in enumerate(ASSISTANT_EXAMPLES):
                if st.button(example, type="tertiary", key=f"assistant_example_{index}"):
                    st.session_state["assistant_pending"] = example
    prepared = st.session_state.get("manager_question")
    if prepared:
        with st.container(border=True):
            st.caption("Question préparée par le manager (envoyée seulement si tu cliques) :")
            st.markdown(prepared)
            with st.container(horizontal=True):
                if st.button("Envoyer cette question", type="primary", key="manager_question_send"):
                    st.session_state["assistant_pending"] = st.session_state.pop("manager_question")
                if st.button("Ignorer", key="manager_question_drop"):
                    st.session_state.pop("manager_question", None)
                    st.rerun()
    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    question = st.chat_input("Pose ta question…", key="assistant_input") or st.session_state.pop("assistant_pending", "")
    if question:
        with st.chat_message("user"):
            st.markdown(question)
        ask_assistant(question)
        st.rerun()


def afficher() -> None:
    render_assistant_tab()
