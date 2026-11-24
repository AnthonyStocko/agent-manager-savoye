"""Récapitulatif des travaux en cours de tous les agents (registre du socle) : tâches en cours seulement, avec lien."""
import sys

from streamlit.testing.v1 import AppTest

from agent_socle import taches


def _app():
    import streamlit as st
    from manager import ihm_travaux
    ihm_travaux.afficher()
    st.write("fin")


def test_recapitulatif_des_taches_en_cours(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TACHES_DIR", str(tmp_path))
    _, tache = taches.lancer("cucumber", [sys.executable, "-c", "import time; time.sleep(30)", "--client-name", "acme"],
                             str(tmp_path / "run.log"), str(tmp_path / "tech.log"))
    try:
        at = AppTest.from_function(_app, default_timeout=30).run()
        assert not at.exception
        texte = " ".join(m.value for m in at.markdown)
        assert "**cucumber**" in texte and "acme" in texte and "localhost:8507" in texte
    finally:
        taches.arreter("cucumber", tache["id"])
