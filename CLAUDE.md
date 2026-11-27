# CLAUDE.md — agent-manager-savoye

Fichier de contexte du manager : ce que le code ne dit pas. Il fait autorité sur l'interprétation du code ; en cas
de contradiction entre ce fichier et le code, la signaler au lieu de trancher seul.

## 1. Rôle et périmètre

Le manager est le **chef d'orchestre** de la suite d'agents Savoye, sur le poste de l'utilisateur (Windows).

| Agent | Dépôt | Port | Lancement | Domaine |
|---|---|---|---|---|
| Manager | `agent-manager-savoye` | 8500 | `lancer.bat` | Ce dépôt |
| Agent fonctionnel Odatio | `agent-odatio` | 8502 | avec le manager | Questions fonctionnelles, diagnostic (sources, base, logs), fichiers d'interface de test |
| Outillage de développement | `agent-dev-tool-savoye` | 8503 | à la demande | Évolution du code des agents, dont le manager |
| Projets et connexions | `agent-projets` | 8504 | avec le manager | Registre des projets en lecture, tests de connexion |
| Intégration du besoin | `agent-integration` | 8505 | avec le manager | Besoin reformulé, questions ouvertes, cadrage complété |
| Lancement d'un ticket | `agent-lancement` | 8506 | avec le manager | Cadrage, chiffrage, révision, épopées, publication Jira, patch, release note |
| Tests Cucumber | `agent-cucumber` | 8507 | avec le manager | Génération, runs Jenkins, correction, livraison, post-merge |
| Revue de PR | `agent-revue-pr` | 8508 | avec le manager | Relecture des PR Bitbucket (lecture seule) |
| Rétrospective | `agent-retrospective` | 8509 | avec le manager | Patch d'agent-lancement comparé au code fusionné, propositions |
| DevOps | `agent-devops-savoye` | — | — | **Hors périmètre pour l'instant** : ses missions seront définies plus tard |

`agent-scrum-master` (8501) a été découpé entre ces agents puis **supprimé** (27 novembre 2026) : chaque agent a
son venv, ses données partagées sont dans `AGENT_DATA_DIR` (`C:\agent-data` : `.env`, `.tmp`, fichiers produits),
son lecteur MCP est la source « donnees » du manager et sa page Configuration est l'onglet Configuration ›
Réglages des agents.

Ses missions :

1. **Lanceur et superviseur** : un double-clic sur `lancer.bat` lance le superviseur, qui démarre les agents en
   mode « auto » puis l'IHM du manager. Il les surveille, relance un agent tombé et arrête tout à la fermeture.
2. **Point d'entrée unique** (onglet Assistant) : l'utilisateur pose ses questions au manager, qui applique la
   règle de décision (§ 5).
3. **Tableau de bord** (lot 6) : tickets en cours, analyses à valider, jobs Jenkins, coûts, patchs livrés, alertes ;
   journal des questions, redirections et changements d'état. Point du matin en tête (lot 6 bis).
4. **Configuration partagée** : accès et référentiel des applications Odatio, communs à tous les agents (lot 2 ;
   les agents la liront en priorité au lot 3).

Le manager **ne fait aucune écriture métier** : ni Jira, ni push, ni Jenkins. Ces gestes restent dans les agents
spécialisés, avec leurs propres validations. Il ne les contourne jamais.

## 2. Stack, structure et commandes

- Python 3.12 ; `python` n'est pas dans le PATH de PowerShell : utiliser `py -3.12` ou le venv du dépôt.
- Streamlit (IHM, port 8500). Claude Agent SDK (assistant), client MCP `mcp` (installé avec le SDK : tableau de
  bord), SQLite (journal), `tomllib` et `.env`.
- Installation : `py -3.12 -m venv venv` puis `venv\Scripts\python.exe -m pip install -r requirements.txt`.
- Lancement : `lancer.bat` (= `venv\Scripts\python.exe -m manager.superviseur`).
- Tests : `venv\Scripts\python.exe -m pytest -q` (requirements-dev.txt ; les tests unittest y passent aussi) ou
  `venv\Scripts\python.exe -m unittest discover -s tests` (tests unittest seuls) (les tests de l'assistant sont ignorés sans
  `claude-agent-sdk`). Aucun linter.
- Évaluation de la règle de décision : `venv\Scripts\python.exe -m manager.evals` (simulation : les agents ne sont
  pas interrogés ; `--reel` pour les interroger). Coûte un appel au modèle par cas.

```
lancer.bat               point d'entrée : lance le superviseur
agents.example.toml      modèle du registre des agents (agents.toml, non versionné, l'adapte au poste)
config/commun.example.toml, config/.env.example   modèles fictifs de la configuration commune
app.py                   IHM : Assistant · Tableau de bord · Agents · Configuration
manager/registre.py      lecture et contrôle du registre
manager/superviseur.py   processus séparé : lancement, santé, relance, arrêt, canal de commande local
manager/pilote.py        client du canal de commande, utilisé par l'IHM
manager/configuration.py configuration commune : accès, applications, secrets, variables transmises à chaque agent
manager/migration.py     import, une fois, des .env des agents et des projets d'agent-odatio
manager/ihm_configuration.py  onglet Configuration
manager/ihm_reglages.py  onglet Configuration › Réglages des agents : .env partagé d'AGENT_DATA_DIR et mémoire des
                         agents (repris de la page Configuration d'agent-scrum-master)
manager/consignes.py     prompt système de l'assistant (règle de décision)
manager/routeur.py       assistant : session Agent SDK, outils internes, serveurs MCP des agents
manager/liens.py         liens de redirection préremplis vers les agents (lot 5)
manager/journal.py       journal SQLite (.tmp/manager.db) : événements, index par ticket, rétention (lot 6)
manager/doc_odatio.py    documentation Odatio en lecture seule, lue dans Bitbucket par son API (sans clone)
mcp_doc_odatio.py        serveur MCP de la documentation, partagé par tous les agents (lecture seule)
manager/sources.py       sources en lecture du tableau de bord : client MCP direct, token_usage.csv (lot 6)
manager/tableau.py       blocs du tableau de bord, sans Streamlit ni modèle (lot 6)
main.py                  CLI --step ask : assistant des agents (repris d'agent-scrum-master, supprimé)
manager/assistant.py, lecture_manager.py, poller_control.py   repris d'agent-scrum-master (données : AGENT_DATA_DIR)
manager/ihm_questions.py, ihm_connaissances.py  onglets « Assistant des agents » et « Connaissances partagées »
webhook_server.py        webhook Bitbucket / poller : lance la post-merge d'agent-cucumber (AGENT_CUCUMBER_DIR, son venv)
mcp_lecture.py           source « donnees » (registre.lecteur()) : lecteur MCP en lecture des données des agents,
                         lancé avec le venv du manager dans AGENT_DATA_DIR
manager/initiatives.py   point du matin et relances, sans Streamlit ni modèle (lot 6 bis)
manager/ihm_tableau.py   onglet Tableau de bord
manager/ihm_assistant.py onglet Assistant
manager/evals.py         évaluation de la règle de décision sur tests/evals/decisions.toml
tests/                   tests unitaires ; tests/faux_agent.py simule un agent Streamlit, tests/faux_mcp.py le
                         lecteur des données (source « donnees »)
```

### Configuration commune

- `config/commun.toml` (sans secret) : `[acces]` (variables non secrètes : `JIRA_DOMAIN`, `BITBUCKET_URL`,
  `JENKINS_URL`…) et `[[applications]]` (nom, projet Jira, sources, dépôt Cucumber, job Jenkins, application web,
  base, logs dont SSH). `config/.env` : les secrets. Les deux sont écrits par l'onglet Configuration, jamais
  versionnés ; liste des accès dans `configuration.ACCES`.
- Noms de variables : **ceux que les agents utilisent déjà** (`JIRA_API_TOKEN`, `BITBUCKET_HTTP_TOKEN`…). Secrets
  d'une application : `DB_PASSWORD_<NOM>`, `APP_PASSWORD_<NOM>`, `SSH_PASSWORD_<NOM>` (convention d'agent-odatio).
- Au lancement d'un agent, le superviseur ajoute à son environnement : les accès non secrets, les secrets que le
  registre lui autorise (`secrets`, motifs acceptés), `SAVOYE_VARIABLES` (la liste de ces variables) et
  `SAVOYE_CONFIG_DIR`. Relu à chaque lancement : après une modification, redémarrer l'agent.
- Les agents adaptés (lot 3) font passer les variables de `SAVOYE_VARIABLES` avant leur propre `.env` et ne les
  y réécrivent pas ; odatio charge aussi les applications de `commun.toml` comme projets. Un agent non adapté
  garde la priorité à son `.env`.
- L'import (`migration.py`) complète sans écraser et ne modifie jamais les fichiers des agents ; il lit d'abord le
  `.env` partagé d'`AGENT_DATA_DIR`, puis ceux des agents du registre.
- Réglages des agents (`ihm_reglages.py`) : le `.env` partagé d'`AGENT_DATA_DIR` (réglages Cucumber, modèles,
  plafonds, base, connaissances…), lu dans un dictionnaire et jamais chargé dans l'environnement du manager. Les
  variables de la configuration commune y sont affichées comme transmises et ne sont jamais réécrites ; les secrets
  ne sont jamais renvoyés au navigateur (champ vide = valeur gardée).

### Documentation Odatio

- Le site publié est derrière une connexion Microsoft (Entra ID) : on lit ses **sources** dans Bitbucket, par
  l'API REST, **sans clone** (décision du 25 novembre 2026). Accès non secrets `ODATIO_DOC_DEPOT` (URL ou
  `PROJET/depot`), `ODATIO_DOC_BRANCHE`, `ODATIO_DOC_DOSSIER`, transmis à tous les agents ; jeton : le
  `BITBUCKET_HTTP_TOKEN` existant (lecture), selon le registre. Aucun nouveau secret.
- `manager/doc_odatio.py` : sommaire (`files`, paginé, 10 min en mémoire), lecture (`raw`, 800 lignes et 60 000
  caractères au plus), recherche (API `search`, seul POST, en lecture ; n'indexe que la branche par défaut ; repli
  sur les noms de fichiers). Chemins bornés au dossier de la doc (ni `..` ni absolu) ; le jeton n'est envoyé qu'à
  l'hôte de `BITBUCKET_URL`. Une page publiée (`…/development/index.html`) est ramenée à sa source, y compris
  pour un site **Antora** (`modules/<module>/pages/<page>.adoc` ; version et module ROOT omis dans l'adresse :
  `doc_odatio.candidats_antora`).
- `mcp_doc_odatio.py` : serveur partagé, lancé par chaque agent avec son python. Il relit les accès non secrets
  manquants dans `commun.toml`, **jamais** le jeton dans `config/.env` (moindre privilège : l'agent le lui passe).

### Superviseur

- Processus séparé, car Streamlit réexécute son script à chaque clic et peut ouvrir plusieurs sessions : lancer
  des processus depuis l'IHM créerait des doublons et des orphelins.
- Santé : `GET http://127.0.0.1:<port>/_stcore/health` doit répondre « ok ». États : démarrage (90 s de grâce),
  en ligne, injoignable (le processus tourne mais ne répond pas : pas de relance automatique), arrêté, en échec
  (plus de 3 relances en 10 min), lancé hors superviseur (le port répondait déjà : rien n'est lancé),
  installation incomplète (dossier ou venv absent).
- Journal de chaque agent : `.tmp/logs/<nom>.log` (sa console y est redirigée).
- Chaque changement d'état d'un agent est aussi noté dans le journal du manager (`suivre_changements`).

### Journal et tableau de bord (lot 6)

- `.tmp/manager.db`, SQLite, jamais versionné. Deux processus y écrivent (IHM et superviseur) : une connexion
  courte par opération, mode WAL, `busy_timeout` de 2 s, aucun verrou gardé. Écriture **au mieux** : `noter*` ne
  lève jamais (False en cas d'échec) ; les lectures lèvent `JournalErreur`, que chaque bloc rattrape.
- Événements : `question` (extrait nettoyé de 160 caractères, décision, agents et noms d'outils, clés de ticket des
  entrées d'outils, coût, tours, durée), `redirection` (agent, ticket, projet, onglet), `etat` (avant → après),
  `erreur`. Jamais les réponses, les entrées d'outils, ni le résumé, la question ou la demande d'une redirection.
  `journal.nettoyer` masque e-mails, téléphones, adresses web et suites ressemblant à des jetons.
- Index par ticket : table `tickets` (événement → clé) et `etat_tickets` (dernier état de l'analyse d'agent-lancement,
  relevé à chaque rafraîchissement du tableau de bord). `journal.ticket(cle)` ; outil `historique_ticket`.
- Rétention : 90 jours et 20 000 événements, purge au premier écrit puis au plus une fois par heure.
- Tableau de bord : `tableau.construire` (0 token) lit le superviseur, le journal, les outils `analyses`, `cucumber`,
  `builds_jenkins` (10 derniers builds par job, 3 affichés) et `patchs` (7 jours) de la source « donnees » (un
  lancement de `mcp_lecture.py` par rafraîchissement, résultat en cache 50 s pour toutes les sessions) et le
  `.tmp/token_usage.csv` d'`AGENT_DATA_DIR`. Le client MCP est appelé directement
  (`sources.appeler_mcp`, liste blanche d'outils : jamais `question` d'odatio), sans session Claude. Liens : tickets,
  analyses et patchs vers agent-lancement ; clients Cucumber vers agent-cucumber.
- Patchs livrés : outil `patchs`, adossé au registre local `.tmp/livraisons.jsonl`, écrit par l'étape patch
  d'agent-lancement avec l'état de la pièce jointe Jira relu en GET. Un patch NON
  joint dans Jira lève une alerte ; « non vérifiée » (Jira illisible au moment de la livraison) reste dans le bloc.
  Un lecteur sans cet outil (« Unknown tool ») affiche une invitation à le mettre à jour, sans alerte.
- Rétrospectives : outil `retrospectives` de `mcp_lecture.py` (source « donnees », `AGENT_DATA_DIR`), qui lit
  les rapports `.tmp/retrospectives/<CLÉ>.json` et `propositions.json` d'agent-retrospective (port 8509, `mcp = []`,
  contrat de format : `agent-retrospective/README.md`). Bloc « Rétrospectives » : 5 derniers tickets avec leur score,
  propositions d'amélioration d'agent-lancement en attente, liens vers l'agent ; relance
  `proposition_en_attente_jours`. Le manager ne valide rien : la validation et l'envoi à dev-tool se font dans l'IHM
  d'agent-retrospective.
- Formats lus (contrat, documenté dans `sources.py`) : texte des outils de `manager/lecture_manager.py` et colonnes
  `date`, `ticket`, `etape`, `cout_usd` de `token_usage.csv` (« ; », écrit par les CLI des agents). Un changement de
  ces formats vide le bloc concerné sans erreur : le signaler dans les dépôts concernés.
- **Point du matin** (lot 6 bis, `initiatives.point_du_matin`, 0 token) : encadré en tête du Tableau de bord, une
  fois par jour tant qu'il n'est pas marqué « Vu », et seulement s'il y a du nouveau. Période : depuis le dernier
  « Vu » (table `points`, une seule ligne), au plus 7 jours. Rubriques : échanges avec le manager (questions, coût,
  redirections par agent, `journal.bilan`), analyses passées « à valider », patchs livrés, builds Cucumber terminés,
  pannes d'agents et erreurs. Sources : le journal et le tableau déjà construit, sans nouvel appel aux agents. Ni
  notification, ni e-mail, ni phrase générée par le modèle, ni tickets Jira modifiés (décision du 20 novembre 2026).
- **Relances** (lot 6 bis, `initiatives.relances`, 0 token) : règles évaluées à chaque affichage, hors du cache du
  tableau, et affichées dans le bloc Alertes de la plus urgente à la moins urgente (5 visibles, le reste dans un
  dépliant), avec un lien prérempli vers agent-lancement (ou agent-cucumber) et un bouton « Ignorer ». Seuils : `configuration.RELANCES`,
  remplacés par `[relances]` de `commun.toml` (onglet Configuration › Relances ; seules les valeurs différentes des
  défauts sont écrites ; lu par le manager seul). Délais en jours ouvrés (lundi à vendredi, sans jours fériés),
  0 désactive une règle à délai :

  | Règle (clé) | Défaut | Source |
  |---|---|---|
  | `patch_non_joint` | immédiat | outil `patchs` |
  | `build_en_echec` (FAILURE, UNSTABLE) | jusqu'à un build réussi | `builds_jenkins` (dernier build terminé du job) |
  | `boucle_echecs` | immédiat | `boucles_cucumber` (boucle arrêtée avec des échecs AGENT non résolus ou APPLICATION) |
  | `generation_bloquee_heures` | 6 h | outil `cucumber` |
  | `patch_non_verifie_jours` | 1 j ouvré | outil `patchs` |
  | `redirection_sans_suite_jours` | 2 j ouvrés | journal (redirection vers lancement, ou vers l'ancien scrum-master, 30 j) et `etat_tickets` |
  | `analyse_a_valider_jours` | 3 j ouvrés | outil `analyses` (date de modification) |
  | `proposition_en_attente_jours` | 3 j ouvrés | outil `retrospectives` (proposition non validée, date du dernier report sinon de création) |

  « Ignorer » masque `ignorer_jours` jours (7 par défaut, table `relances_ignorees`, purgée à échéance). La clé
  combine la règle, l'objet et son état (ex. `analyse:ODA-1:<date de modification>`) : si l'objet change, la relance
  revient. Aucune action automatique ni message hors du manager.
- **Boucles de correction Cucumber** (25 novembre 2026) : agent-cucumber, à la fin d'un run, envoie ses échecs
  dus à l'agent à dev-tool, qui corrige l'agent et fusionne sur `main` **sans validation humaine** (contrat :
  `agent-cucumber/CLAUDE.md` § 6). Le manager n'y prend aucune part active : il **affiche** l'état (outil
  `boucles_cucumber` de `mcp_lecture.py`, qui lit `.tmp/cucumber/<client>/boucles/*.json` ; bloc « Boucles de
  correction Cucumber » : en cours, itération x/N, terminée, abandonnée, corrections et retours arrière, coût) et
  lève la relance `boucle_echecs` seulement si la boucle s'arrête avec des échecs AGENT non résolus (lien vers
  dev-tool, demande préremplie) ou APPLICATION (lien vers l'agent Cucumber). Le démarrage de dev-tool par
  agent-cucumber passe par le canal de commande ci-dessous (`POST /agents/dev-tool/demarrer`).
- Canal de commande : HTTP sur 127.0.0.1:8499, en-tête `X-Jeton` obligatoire ; port et jeton dans
  `.tmp/superviseur.json`, recréé à chaque lancement et supprimé à l'arrêt.
- Le superviseur lance la commande de l'agent dans son dossier avec son propre venv. Il ne modifie jamais le
  dépôt, la branche ni les fichiers d'un agent.

## 3. Prompts, outils et évals

- Prompt système : `manager/consignes.py` (`MODELE`, `construire`), avec les agents du registre, la source
  « donnees » (consultable, jamais cible d'une redirection) et les applications de la configuration commune.
- Outils (`manager/routeur.py`), tous en lecture seule ; aucun outil intégré de Claude Code (`tools=[]`),
  `permission_mode="dontAsk"`, `strict_mcp_config`, `setting_sources=[]` :
  - internes (serveur « manager ») : `etat_agents`, `applications`, `historique_ticket` (journal du manager et
    dernier état relevé de l'analyse, sans réinterroger les agents, lot 6), `rediriger` (prépare un lien prérempli
    affiché sous la réponse, n'agit pas), `doc_sommaire`, `doc_lire`, `doc_chercher` (documentation Odatio, si
    configurée ; non simulés par les évals, ils appellent Bitbucket) ;
  - serveurs MCP des agents (champ `mcp` du registre), lancés avec leur venv et `configuration.variables_pour` :
    odatio `mcp_server.py --manager` (projets, question : réponse en lecture seule, conversation reprise dans l'IHM
    d'odatio) ;
  - source « donnees » du manager (`registre.lecteur()`, hors registre) : `mcp_lecture.py` lancé avec le venv du
    manager dans `AGENT_DATA_DIR` (analyses, ticket, cucumber, builds_jenkins, patchs, boucles_cucumber,
    retrospectives).
- **Liens préremplis** (`manager/liens.py`) : `rediriger` ouvre l'agent au bon endroit avec
  `?source=manager&page=…&ticket=…&projet=…&conversation=…&question=…&demande=…&depot=…&contexte=…` (onglets
  connus par agent dans `liens.ONGLETS` ; valeurs invalides retirées, textes bornés). Chaque agent applique le lien
  une fois par session et ne lance rien : agent-lancement (ticket posé et contexte), agent-cucumber (page et
  client), odatio (`odatio_agent/lien_manager.py` : projet, tâche,
  conversation reprise, question), dev-tool (`agent_dev/lien_manager.py` : dépôt cible et demande). Une question ou
  une demande est PRÉREMPLIE, jamais envoyée sans clic. Aucun secret ni donnée nominative dans un lien.
- Évals : `tests/evals/decisions.toml` (question → décision et agent attendus), `manager/evals.py`.

## 4. Glossaire

- **Odatio** : le WMS (et TMS / OMS) édité ; **Standard** : le produit commun ; **spécifique** : le code propre à
  un client.
- **Cucumber** : tests BDD d'une application client (dépôt `<client>-cucumber`), joués sur Jenkins.
- **Cadrage** : analyse fonctionnelle et technique d'un ticket Jira, produite par agent-lancement.
- **Chantier** : worktree isolé dans lequel l'agent de dev-tool développe une demande.
- **Registre** : `agents.toml`, la liste des agents connus du manager.
- **Application** (référentiel commun, lot 2) : un client Odatio avec sa clé Jira, ses dépôts Standard /
  spécifique / cucumber, sa base, ses logs, son filtre groundcover et ses jobs Jenkins.

## 5. Règles sensibles — à ne jamais modifier sans accord de l'utilisateur

1. **Lecture seule envers les agents** : le manager consulte, il n'écrit pas à leur place. Les outils MCP exposés
   au manager sont en lecture seule.
2. **Règle de décision** :

   | Cas | Comportement |
   |---|---|
   | Question générale ou état global | Répondre seul (tableau de bord) |
   | Question ponctuelle, en lecture seule, rapide (ex. score d'une rétrospective, propositions en attente → outil `retrospectives` de la source « donnees ») | Interroger un ou plusieurs agents (ou la source « donnees ») et faire la synthèse |
   | Validation humaine, écriture, travail long ou ressource partagée (ex. job Jenkins unique) | Rediriger vers l'agent, avec un lien direct prérempli et un résumé du contexte (ex. tests Cucumber → cucumber ; cadrage, chiffrage, publication → lancement ; lancer une rétrospective, valider ou rejeter une proposition → retrospective) |
   | Amélioration d'un agent | Rediriger vers dev-tool avec une demande prérédigée |
   | Demande ambiguë | Poser une seule question |

3. **Moindre privilège** : chaque agent ne reçoit que les secrets listés pour lui dans le registre (ex. odatio ne
   reçoit jamais `BITBUCKET_WRITE_TOKEN` ni `JENKINS_API_TOKEN`). Le jeton Jira est unique et personnel : odatio
   le reçoit et ne s'en sert qu'en lecture (garanti par son code, pas par le jeton).
4. **Dépôt public** : aucun secret, aucune donnée réelle de client, d'entrepôt ou d'infrastructure (URL internes,
   noms de clients, schémas de base) dans les commits, exemples et tests. `agents.toml`, `config/commun.toml` et
   `.env` ne sont jamais versionnés.
5. **Verrou Jenkins** : un seul job Cucumber à la fois. Le verrou appartient à agent-cucumber ; le manager l'affiche
   et le respecte, il ne lance rien sur Jenkins.

## 6. Décisions prises (18 novembre 2026)

- Le manager lance tous les agents « auto » quand on le lance ; dev-tool est dans le registre mais lancé à la
  demande.
- Superviseur dans un processus séparé.
- Communication avec les agents : un serveur MCP en lecture seule par agent (odatio en a déjà un).
- **Configuration partagée** (lot 2) : le commun (accès Jira, Bitbucket, Jenkins, groundcover, compte Claude et
  référentiel des applications) vit dans le manager (`config/commun.toml` sans secret, `config/.env` pour les
  secrets) ; chaque agent garde ses réglages métier (`CUCUMBER_*`, modèles par étape, plafonds…). Les accès et
  secrets sont injectés en variables d'environnement au lancement ; le référentiel est lu dans le fichier commun
  via `SAVOYE_CONFIG_DIR`, avec repli sur la configuration locale de l'agent (il reste lançable seul).
  L'onglet Configuration du manager devient la référence ; un assistant importe une fois les `.env` et
  `profils.toml` existants.
- Même compte Claude que les autres agents.
- IHM Streamlit sur le port 8500, liens directs vers les agents (8502 à 8509).

Lots : 0 cible dans dev-tool (fait) · 1 structure, registre, superviseur, onglet Agents (fait) · 2 configuration
commune et migration (fait) · 3 lecture du commun par odatio et scrum-master (fait) · 4 serveurs MCP, routeur, règle
de décision (fait) ·
5 liens de redirection préremplis (fait) · 6 journal, index par ticket, tableau de bord (fait) · 6 bis-a point du
matin (fait) · 6 bis-b relances (fait ; les alertes « patch NON joint » et « génération bloquée » du lot 6 sont
devenues des relances).

## 7. Questions ouvertes

- L'écouteur de merges (`webhook_server.py --poll`, repris d'agent-scrum-master) n'est pas une IHM Streamlit :
  faut-il le lancer avec le manager ? Le registre accepte une commande libre, mais la santé suppose aujourd'hui
  `/_stcore/health` (prévoir un champ `sante` : streamlit, http ou processus seul).
- Lecteur des données (`mcp_lecture.py`) : il ne doit pas être déclaré dans une configuration chargée par le CLI
  `claude` que les agents lancent depuis `AGENT_DATA_DIR` (pas de `.mcp.json`) ; le manager le lance lui-même.
- Ports : odatio fixe 8502 dans son `.streamlit/config.toml`. Le superviseur passe toujours `--server.port` : le
  registre fait foi.
- Coûts : le journal note le coût de chaque question (lot 6, bloc Coûts du tableau de bord) ; un modèle léger pour
  le routage ? À trancher sur quelques semaines de journal.
- **Patchs livrés** : le registre des livraisons ne couvre que les livraisons faites depuis
  agent-scrum-master#49 (pas d'historique). Si Jira répond autre chose que 200 à la relecture des pièces jointes, la
  livraison apparaît « NON jointe » au lieu de « non vérifiée » (`jira_tools.get_jira_attachment_names`, côté
  agent-lancement).
- Tableau de bord : seule la source « donnees » est interrogée ; odatio `projets` n'y apporte rien d'utile
  aujourd'hui.
- **Documentation Odatio** : chaque agent doit déclarer `mcp_doc_odatio.py` dans ses sessions Claude (demande à
  dev-tool, dépôt par dépôt ou dans agent-socle). dev-tool et projets n'ont pas le jeton Bitbucket de lecture : à
  leur ajouter seulement si le besoin se confirme. La recherche plein texte dépend de l'index de Bitbucket.
