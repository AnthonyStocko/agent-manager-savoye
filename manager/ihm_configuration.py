"""Onglet Configuration du manager : accès communs, applications Odatio, import des réglages des agents, registre."""

from __future__ import annotations

import streamlit as st

from manager import configuration as conf
from manager import migration
from manager import registre as reg

NOUVELLE = "➕ Nouvelle application"
TYPES_BASE = ["", "postgresql", "oracle", "sqlserver", "mysql"]
RAPPEL = ("Les agents reçoivent la configuration à leur lancement : redémarre ceux qui sont concernés (onglet "
          "Agents). Tant qu'un agent n'a pas été adapté pour la lire en priorité (lot 3), son propre .env l'emporte.")


def afficher() -> None:
    try:
        commun = conf.charger()
    except conf.ConfigurationErreur as e:
        st.error(f"{e}\n\nCorrige `{conf.DOSSIER / conf.FICHIER}` à la main.")
        return
    acces, applications, importer, registre = st.tabs(["Accès", "Applications", "Import", "Registre des agents"])
    with acces:
        _acces(commun)
    with applications:
        _applications(commun)
    with importer:
        _import()
    with registre:
        _registre()


def _lignes(texte: str) -> list:
    return [l.strip() for l in (texte or "").splitlines() if l.strip()]


def _acces(commun: conf.Commun) -> None:
    st.caption(f"Accès partagés par les agents. Sans secret : `{conf.DOSSIER / conf.FICHIER}` ; secrets : "
               f"`{conf.DOSSIER / conf.FICHIER_SECRETS}`. Chaque agent ne reçoit que les secrets que le registre lui "
               f"autorise.")
    presents = conf.secrets()
    with st.form("acces"):
        valeurs, groupe = {}, None
        for a in conf.ACCES:
            if a.groupe != groupe:
                groupe = a.groupe
                st.markdown(f"**{groupe}**")
            if a.secret:
                valeurs[a.variable] = st.text_input(a.libelle, type="password", key=f"acces-{a.variable}",
                                                    placeholder="inchangé" if presents.get(a.variable) else "",
                                                    help=f"{a.variable} (secret). Vide : inchangé.")
            else:
                valeurs[a.variable] = st.text_input(a.libelle, value=commun.acces.get(a.variable, ""),
                                                    key=f"acces-{a.variable}", help=f"{a.variable}. {a.aide}".strip())
        effacer = st.multiselect("Secrets à effacer", [v for v in conf.SECRETS if presents.get(v)])
        if st.form_submit_button("Enregistrer", type="primary"):
            commun.acces = {v: valeurs[v].strip() for v in conf.NON_SECRETS if valeurs[v].strip()}
            try:
                conf.enregistrer(commun)
                conf.enregistrer_secrets({v: valeurs[v] for v in conf.SECRETS if valeurs[v].strip()}
                                         | {v: "" for v in effacer})
            except conf.ConfigurationErreur as e:
                st.error(str(e))
                return
            st.success("Accès enregistrés. " + RAPPEL)


def _applications(commun: conf.Commun) -> None:
    st.caption("Le référentiel des applications Odatio, commun aux agents. Leurs mots de passe vont dans config/.env "
               "(DB_PASSWORD_<NOM>, APP_PASSWORD_<NOM>, SSH_PASSWORD_<NOM>).")
    noms = [a.nom for a in commun.applications]
    choix = st.selectbox("Application", noms + [NOUVELLE])
    existante = None if choix == NOUVELLE else commun.application(choix)
    a = existante or conf.Application(nom="")
    presents = conf.secrets()
    noms_secrets = conf.secrets_application(a.nom) if a.nom else {}
    connu = {k: bool(a.nom and presents.get(v)) for k, v in noms_secrets.items()}
    base, logs = dict(a.base), dict(a.logs)
    ssh = dict(logs.get("ssh") or {})

    with st.form(f"application-{choix}"):
        c1, c2, c3 = st.columns([1, 2, 1])
        nom = c1.text_input("Nom court", value=a.nom, help="Minuscules, chiffres et tirets (ex. canson-prod).")
        description = c2.text_input("Description", value=a.description)
        jira = c3.text_input("Projet Jira", value=a.jira_projet, placeholder="ABC")
        st.markdown("**Sources** (une ligne par dépôt : Standard et spécifiques ; un dépôt ou un dossier local)")
        sources = st.data_editor(
            [{"nom": s.nom, "depot": s.depot, "branche": s.branche, "chemin": s.chemin} for s in a.sources]
            or [{"nom": "", "depot": "", "branche": "", "chemin": ""}],
            num_rows="dynamic", width="stretch", key=f"sources-{choix}",
            column_config={"nom": "Nom", "depot": st.column_config.TextColumn("Dépôt Git (adresse de clonage)"),
                           "branche": "Branche", "chemin": "Dossier local"})
        c1, c2 = st.columns(2)
        cucumber = c1.text_input("Dépôt Cucumber", value=a.depot_cucumber, placeholder="…/scm/abc/abc-cucumber.git")
        jenkins = c2.text_input("Job Jenkins des tests Cucumber", value=a.jenkins_job)

        st.markdown("**Application web**")
        c1, c2, c3 = st.columns([2, 1, 1])
        url = c1.text_input("Adresse", value=a.url)
        utilisateur_appli = c2.text_input("Compte", value=a.utilisateur_application)
        mdp_appli = c3.text_input("Mot de passe du compte", type="password",
                                  placeholder="inchangé" if connu.get("application") else "")

        st.markdown("**Base de données** (compte en lecture seule)")
        c1, c2, c3, c4 = st.columns([1, 2, 1, 1])
        type_base = c1.selectbox("Type", TYPES_BASE, index=TYPES_BASE.index(base.get("type", ""))
                                 if base.get("type", "") in TYPES_BASE else 0, format_func=lambda t: t or "Aucune")
        hote = c2.text_input("Hôte", value=base.get("hote", ""))
        port = c3.text_input("Port", value=base.get("port", ""))
        nom_base = c4.text_input("Base", value=base.get("nom", ""))
        c1, c2, c3 = st.columns(3)
        utilisateur_base = c1.text_input("Utilisateur", value=base.get("utilisateur", ""))
        schema = c2.text_input("Schéma", value=base.get("schema", ""))
        mdp_base = c3.text_input("Mot de passe", type="password", placeholder="inchangé" if connu.get("base") else "")

        st.markdown("**Logs**")
        dossiers = st.text_area("Dossiers de logs (poste ou partage)", value="\n".join(logs.get("dossiers", [])),
                                height=68)
        groundcover = st.text_input("Filtre groundcover", value=logs.get("groundcover_filtre", ""))
        c1, c2, c3 = st.columns(3)
        ssh_hote = c1.text_input("Serveur SSH (logs)", value=ssh.get("hote", ""))
        ssh_utilisateur = c2.text_input("Utilisateur SSH", value=ssh.get("utilisateur", ""))
        mdp_ssh = c3.text_input("Mot de passe SSH", type="password", placeholder="inchangé" if connu.get("ssh") else "")
        ssh_dossiers = st.text_area("Dossiers de logs sur le serveur SSH", value="\n".join(ssh.get("dossiers", [])),
                                    height=68)
        enregistrer = st.form_submit_button("Enregistrer", type="primary")

    if enregistrer:
        nouvelle = conf.Application(
            nom=nom.strip(), description=description.strip(), jira_projet=jira.strip(),
            sources=[conf.Source(**{k: str(v or "").strip() for k, v in s.items()}) for s in sources
                     if any(str(v or "").strip() for v in s.values())],
            depot_cucumber=cucumber.strip(), jenkins_job=jenkins.strip(), url=url.strip(),
            utilisateur_application=utilisateur_appli.strip(),
            base={"type": type_base, "hote": hote.strip(), "port": port.strip(), "nom": nom_base.strip(),
                  "utilisateur": utilisateur_base.strip(), "schema": schema.strip()} if type_base else {},
            logs={"dossiers": _lignes(dossiers), "groundcover_filtre": groundcover.strip(),
                  "ssh": {**{k: v for k, v in ssh.items() if k not in ("hote", "utilisateur", "dossiers")},
                          "hote": ssh_hote.strip(), "utilisateur": ssh_utilisateur.strip(),
                          "dossiers": _lignes(ssh_dossiers)}})
        autres = [x for x in commun.applications if x.nom != choix]
        if any(x.nom == nouvelle.nom for x in autres):
            st.error(f"Une application s'appelle déjà « {nouvelle.nom} ».")
            return
        erreurs = conf.verifier(conf.Commun(applications=[nouvelle]))
        if erreurs:
            st.error("\n".join(erreurs))
            return
        index = noms.index(choix) if existante else len(autres)
        commun.applications = autres[:index] + [nouvelle] + autres[index:]
        nouveaux = conf.secrets_application(nouvelle.nom)
        secrets = {}
        for cle, saisi in (("base", mdp_base), ("application", mdp_appli), ("ssh", mdp_ssh)):
            if saisi:
                secrets[nouveaux[cle]] = saisi
            elif existante and noms_secrets[cle] != nouveaux[cle] and presents.get(noms_secrets[cle]):
                secrets[nouveaux[cle]] = presents[noms_secrets[cle]]     # application renommée : le secret suit
                secrets[noms_secrets[cle]] = ""
        try:
            conf.enregistrer(commun)
            if secrets:
                conf.enregistrer_secrets(secrets)
        except conf.ConfigurationErreur as e:
            st.error(str(e))
            return
        st.success(f"Application « {nouvelle.nom} » enregistrée. " + RAPPEL)

    if existante:
        with st.popover("Supprimer cette application"):
            st.markdown(f"Supprimer « {choix} » et ses mots de passe de la configuration commune ? Les agents et "
                        f"leurs propres réglages ne sont pas touchés.")
            if st.button("Supprimer", type="primary", key=f"supprimer-{choix}"):
                commun.applications = [x for x in commun.applications if x.nom != choix]
                conf.enregistrer(commun)
                conf.enregistrer_secrets({v: "" for v in conf.secrets_application(choix).values()})
                st.rerun()


def _import() -> None:
    st.caption("Reprend une fois les réglages actuels des agents : le .env de chacun et les projets d'agent-odatio "
               "(profils.toml). Rien n'est écrit avant ta validation ; les fichiers des agents ne sont pas modifiés ; "
               "ce qui existe déjà dans la configuration commune est gardé.")
    if st.button("Analyser les réglages des agents"):
        try:
            st.session_state.proposition = migration.proposer(reg.charger())
        except reg.RegistreErreur as e:
            st.error(str(e))
    p = st.session_state.get("proposition")
    if not p:
        return
    if p.vide:
        st.info("Rien à importer.")
    if p.acces or p.secrets:
        st.markdown("**Accès**")
        st.dataframe([{"Variable": k, "Valeur": v, "Depuis": p.origines.get(k, "")} for k, v in p.acces.items()]
                     + [{"Variable": k, "Valeur": migration.masquer(v) + " (secret)", "Depuis": p.origines.get(k, "")}
                        for k, v in p.secrets.items()], hide_index=True, width="stretch")
    if p.applications:
        st.markdown("**Applications** (depuis les projets d'agent-odatio)")
        st.dataframe([{"Nom": a.nom, "Description": a.description, "Sources": len(a.sources),
                       "Base": a.base.get("type", ""), "Logs SSH": bool(a.logs.get("ssh"))} for a in p.applications],
                     hide_index=True, width="stretch")
    for conflit in p.conflits:
        st.warning(conflit)
    for note in p.notes:
        st.caption(note)
    if not p.vide and st.button("Importer dans la configuration commune", type="primary"):
        try:
            gardes = migration.appliquer(p)
        except conf.ConfigurationErreur as e:
            st.error(str(e))
            return
        st.session_state.proposition = None
        st.success("Import terminé. " + RAPPEL)
        for g in gardes:
            st.caption(g)


def _registre() -> None:
    fichier = reg.FICHIER if reg.FICHIER.exists() else reg.EXEMPLE
    st.markdown(f"Registre des agents : `{fichier}`")
    if fichier == reg.EXEMPLE:
        st.info("Aucun `agents.toml` : le modèle `agents.example.toml` est utilisé. Copie-le en `agents.toml` "
                "pour l'adapter à ton poste (dossiers, ports, secrets transmis), puis relance le manager.")
    try:
        registre = reg.charger()
    except reg.RegistreErreur as e:
        st.error(str(e))
        return
    st.dataframe([{"Agent": a.titre, "Port": a.port, "Mode": a.mode, "Dossier": str(a.dossier),
                   "Dossier présent": a.dossier.is_dir(), "Secrets transmis": ", ".join(a.secrets) or "aucun"}
                  for a in registre.agents], hide_index=True, width="stretch")
