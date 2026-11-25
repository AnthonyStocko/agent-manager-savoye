"""Consultations en lecture seule pour agent-manager-savoye (tools/lecture_manager.py, mcp_lecture.py)."""
import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from unittest import mock

import pytest

from manager import lecture_manager as lecture
from agent_socle import livraisons
from agent_socle import publication


@pytest.fixture
def racine(tmp_path, monkeypatch):
    monkeypatch.setattr(publication, "PUBLICATION_DIR", str(tmp_path / "publication"))
    (tmp_path / "ODA-1-cadrage.md").write_text("# Besoin\ntexte\n# Solution\nfaite\n", encoding="utf-8")
    (tmp_path / "ODA-2-cadrage.md").write_text("# Besoin\npubliée\n", encoding="utf-8")
    publication.record_publication("ODA-2", "# Besoin\npubliée\n")
    return str(tmp_path)


def test_analyses_et_etat(racine):
    texte = lecture.analyses(racine)
    assert "ODA-1" in texte and "← à valider" in texte.split("ODA-1")[1].splitlines()[0]
    assert "Publiée dans Jira" in texte.split("ODA-2")[1].splitlines()[0]
    detail = lecture.analyse("oda-1", racine)
    assert "brouillon" in detail and "Besoin · Solution" in detail
    assert "Aucune analyse de ODA-3" in lecture.analyse("ODA-3", racine)
    with pytest.raises(lecture.LectureErreur):
        lecture.analyse("../secret", racine)


def test_ticket_avec_le_compte_de_lecture(monkeypatch, racine):
    monkeypatch.setattr(lecture, "WORKSPACE_DIR", racine)
    monkeypatch.setenv("JIRA_DOMAIN", "societe.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "ecriture@exemple.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "jeton-ecriture")
    monkeypatch.setenv("JIRA_READ_EMAIL", "lecture@exemple.com")
    monkeypatch.setenv("JIRA_READ_API_TOKEN", "jeton-lecture")
    reponse = mock.Mock(status_code=200)
    reponse.json.return_value = {"fields": {"summary": "Corriger la vague", "status": {"name": "En cours"},
                                            "issuetype": {"name": "Bug"}, "assignee": None,
                                            "parent": {"key": "ODA-9", "fields": {"summary": "Épopée"}}}}
    with mock.patch.object(lecture.requests, "get", return_value=reponse) as get:
        texte = lecture.ticket("ODA-1")
    assert get.call_args.kwargs["auth"] == ("lecture@exemple.com", "jeton-lecture")
    assert "Corriger la vague" in texte and "En cours" in texte and "non assigné" in texte and "ODA-9" in texte


def test_builds_des_jobs_configures(monkeypatch):
    monkeypatch.setenv("JENKINS_CUCUMBER_JOB", "JOB_NUIT")
    monkeypatch.setenv("CUCUMBER_VERIFY_JOB", "")
    monkeypatch.setenv("CUCUMBER_RUN_JOB", "JOB_NUIT")
    builds = [{"numero": 12, "resultat": None, "debut": 1763456400000, "duree": 0},
              {"numero": 11, "resultat": "SUCCESS", "debut": 1763452800000, "duree": 600000}]
    with mock.patch.object(lecture, "recent_builds", return_value=builds) as recents:
        texte = lecture.builds()
    recents.assert_called_once_with("JOB_NUIT", 5)          # job en double compté une fois
    assert "#12 EN COURS" in texte and "#11 SUCCESS" in texte and "10 min" in texte
    with pytest.raises(lecture.LectureErreur):
        lecture.builds("AUTRE_JOB")


def test_cucumber(monkeypatch):
    monkeypatch.setattr(lecture.assistant, "known_clients", lambda: ["_standard", "client-a", "client-b"])
    monkeypatch.setattr(lecture.verrou, "holder", lambda c: {"pid": 42, "debut": "2026-11-19T09:00:00"}
                        if c == "client-a" else {})
    texte = lecture.cucumber()
    assert "client-a : génération en cours (processus 42" in texte
    assert "client-b : aucune génération en cours" in texte and "_standard" not in texte


def test_patchs_livres(tmp_path):
    registre = str(tmp_path / "livraisons.jsonl")
    assert "Aucun patch livré ces 7 derniers jours" in lecture.patchs(7, registre)
    maintenant = datetime.now()
    livraisons.noter("ODA-1", ["ODA-1-std.patch"], {"ODA-1-std.patch"}, quand=maintenant - timedelta(hours=2),
                     chemin=registre)
    livraisons.noter("ODA-2", ["ODA-2-std.patch", "ODA-2-client-x.patch"], {"ODA-2-std.patch"},
                     quand=maintenant - timedelta(hours=1), chemin=registre)
    livraisons.noter("ODA-3", ["ODA-3-std.patch"], set(), "Jira illisible (Timeout)", chemin=registre)
    livraisons.noter("ODA-4", ["ODA-4-std.patch"], {"ODA-4-std.patch"}, quand=maintenant - timedelta(days=20),
                     chemin=registre)
    lignes = lecture.patchs(7, registre).splitlines()
    assert lignes[0] == "Patchs livrés ces 7 derniers jours (agent Scrum Master) :"
    assert lignes[1].startswith("- ODA-3 (livré le ") and lignes[1].endswith(
        ": ODA-3-std.patch — pièce jointe non vérifiée (Jira illisible (Timeout))")
    assert lignes[2].endswith(": ODA-2-std.patch, ODA-2-client-x.patch — NON joint dans Jira : ODA-2-client-x.patch")
    debut = (maintenant - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    assert lignes[3] == f"- ODA-1 (livré le {debut}) : ODA-1-std.patch — en pièce jointe Jira"
    assert len(lignes) == 4                                               # ODA-4 hors fenêtre
    assert "ODA-4" in lecture.patchs(30, registre)
    assert "ces 90 derniers jours" in lecture.patchs(1000, registre)      # borné
    with pytest.raises(lecture.LectureErreur):
        lecture.patchs("beaucoup", registre)


def test_patchs_outil_mcp(monkeypatch, tmp_path):
    """L'outil est exposé par le serveur MCP et lit le registre (sans passer par un client stdio)."""
    pytest.importorskip("mcp.server.mcpserver")
    monkeypatch.chdir(os.getcwd())                                   # mcp_lecture change de dossier à l'import
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)  # et charge le .env local : neutralisé
    import mcp_lecture
    registre = str(tmp_path / "livraisons.jsonl")
    livraisons.noter("ODA-1", ["ODA-1-std.patch"], {"ODA-1-std.patch"}, chemin=registre)
    monkeypatch.setattr(livraisons, "FICHIER", registre)
    serveur = mcp_lecture.creer_serveur()
    outils = {o.name: o for o in asyncio.run(serveur.list_tools())}
    assert "patchs" in outils and "jours" in json.dumps(outils["patchs"].input_schema)
    resultat = asyncio.run(serveur.call_tool("patchs", {"jours": 3}))
    assert "ODA-1 (livré le" in json.dumps(resultat, default=str, ensure_ascii=False)


def test_aucune_ecriture():
    """Le module ne fait que des lectures : aucun appel HTTP autre que GET, aucune fonction d'écriture de l'agent."""
    for fichier in ("manager/lecture_manager.py", "mcp_lecture.py"):
        # Code du manager (WORKSPACE_DIR désigne désormais les données, AGENT_DATA_DIR)
        source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), fichier),
                      encoding="utf-8").read()
        assert not re.search(r"requests\.(post|put|delete|patch)|trigger_|publish|validate\(|save_draft|"
                             r"record_publication|acquire\(|release\(|write_env|open\([^)]*['\"][wa]|"
                             r"livraisons\.noter", source), fichier


def test_boucles_cucumber(tmp_path):
    """Boucles de correction d'agent-cucumber : texte au format lu par sources.lire_boucles (contrat)."""
    from manager import sources
    assert lecture.boucles(str(tmp_path)) == "Aucune boucle de correction Cucumber."
    dossier = tmp_path / ".tmp" / "cucumber" / "client-x" / "boucles"
    dossier.mkdir(parents=True)
    (dossier / "client-x-4-1.json").write_text(json.dumps({
        "generation": "client-x-4-1", "projet": "client-x", "etat": "abandonnee", "iteration": 2, "iterations_max": 3,
        "initial": {"echecs": 4}, "final": {"echecs": 1, "par_categorie": {"AGENT": 1, "APPLICATION": 0}},
        "raison": "aucun échec en moins", "cout_usd": 2.5, "relance": True, "mis_a_jour": "2026-11-20T08:00:00",
        "corrections": [{"iteration": 1, "commit": "abcdef1234567", "cause": "prompt vague"}],
        "annulations": [{"iteration": 2, "commit": "abcdef1234567", "revert": "1234567abcdef"}],
        "demande_texte": "Boucle : corriger l'agent\n- [AGENT] S — Then x : m token=secret-fictif"}),
        encoding="utf-8")
    texte = lecture.boucles(str(tmp_path))
    boucle = sources.lire_boucles(texte)[0]
    assert (boucle["client"], boucle["etat"], boucle["iteration"], boucle["relance"]) == \
        ("client-x", "abandonnée", 2, True)
    assert boucle["echecs_avant"] == "4" and boucle["echecs_apres"] == "1"
    assert boucle["corrections"] == ["it1 abcdef1234 : prompt vague"]
    assert boucle["annulations"] == ["it2 abcdef1234 → 1234567abc"]
    assert boucle["demande"].startswith("Boucle : corriger l'agent\n- [AGENT]")


def _retro(dossier, ticket, date, score, ajuste=None, ecarts=2, analyse=True):
    rapport = {"ticket": ticket, "date": date, "score": score, "score_ajuste": ajuste,
               "analyse": {"ecarts": [{}] * ecarts} if analyse else None}
    (dossier / f"{ticket}.json").write_text(json.dumps(rapport), encoding="utf-8")


def test_retrospectives(tmp_path):
    """Rétrospectives d'agent-retrospective : texte au format lu par sources.lire_retrospectives (contrat)."""
    from manager import sources
    vide = lecture.retrospectives(str(tmp_path))
    assert vide == "Rétrospectives :\n- aucune\nPropositions en attente :\n- aucune"
    assert sources.lire_retrospectives(vide) == ([], [])
    dossier = tmp_path / ".tmp" / "retrospectives"
    dossier.mkdir(parents=True)
    _retro(dossier, "ODA-1", "2026-11-18T10:00:00", 62, 70)
    _retro(dossier, "ODA-2", "2026-11-19T11:30:00", 40, analyse=False)
    (dossier / "motifs.json").write_text("{}", encoding="utf-8")              # pas un rapport
    (dossier / "ODA-3.json").write_text("illisible", encoding="utf-8")
    (dossier / "propositions.json").write_text(json.dumps({"propositions": {
        "m-1": {"id": "m-1", "levier": "patch", "libelle": "Lire les tests · voisins", "tickets": ["ODA-1", "ODA-2"],
                "etat": "proposee", "cree_le": "2026-11-18T10:00:00", "reportee_le": ""},
        "m-2": {"id": "m-2", "levier": "cadrage", "libelle": "Rejetée", "tickets": ["ODA-1"], "etat": "rejetee",
                "cree_le": "2026-11-18T10:00:00"},
        "m-3": {"id": "m-3", "levier": "conventions", "libelle": "Reportée", "tickets": ["ODA-1", "ODA-2"],
                "etat": "proposee", "cree_le": "2026-11-10T10:00:00", "reportee_le": "2026-11-19T09:00:00"}}}),
        encoding="utf-8")
    texte = lecture.retrospectives(str(tmp_path))
    retros, props = sources.lire_retrospectives(texte)
    assert retros == [
        {"cle": "ODA-2", "date": "2026-11-19 11:30", "score": 40, "score_ajuste": None, "ecarts": 0, "analyse": False},
        {"cle": "ODA-1", "date": "2026-11-18 10:00", "score": 62, "score_ajuste": 70, "ecarts": 2, "analyse": True}]
    assert props == [
        {"id": "m-1", "levier": "patch", "libelle": "Lire les tests, voisins", "tickets": 2,
         "depuis": "2026-11-18 10:00"},
        {"id": "m-3", "levier": "conventions", "libelle": "Reportée", "tickets": 2, "depuis": "2026-11-19 09:00"}]
    assert len(sources.lire_retrospectives(lecture.retrospectives(str(tmp_path), 1))[0]) == 1
    with pytest.raises(lecture.LectureErreur):
        lecture.retrospectives(str(tmp_path), "beaucoup")


def test_retrospectives_outil_mcp(monkeypatch, tmp_path):
    pytest.importorskip("mcp.server.mcpserver")
    monkeypatch.chdir(os.getcwd())
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    import mcp_lecture
    monkeypatch.setattr(lecture, "WORKSPACE_DIR", str(tmp_path))
    serveur = mcp_lecture.creer_serveur()
    outils = {o.name: o for o in asyncio.run(serveur.list_tools())}
    assert "retrospectives" in outils and "nombre" in json.dumps(outils["retrospectives"].input_schema)
    resultat = asyncio.run(serveur.call_tool("retrospectives", {"nombre": 3}))
    assert "Propositions en attente" in json.dumps(resultat, default=str, ensure_ascii=False)
