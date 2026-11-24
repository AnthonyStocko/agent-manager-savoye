# CLAUDE.md — agent-manager-savoye

Fichier de contexte du manager : ce que le code ne dit pas. Il fait autorité sur l'interprétation du code ; en cas
de contradiction entre ce fichier et le code, la signaler au lieu de trancher seul.

## 1. Rôle et périmètre

Le manager est le **chef d'orchestre** de la suite d'agents Savoye, sur le poste de l'utilisateur (Windows).

| Agent | Dépôt | Port | Lancement | Domaine |
|---|---|---|---|---|
| Manager | `agent-manager-savoye` | 8500 | `lancer.bat` | Ce dépôt |
| Scrum Master | `agent-scrum-master` | 8501 | avec le manager | Cycle d'un ticket Jira : besoin, cadrage, chiffrage, patch, release note, Cucumber |
| Agent fonctionnel Odatio | `agent-odatio` | 8502 | avec le manager | Questions fonctionnelles, diagnostic (sources, base, logs), fichiers d'interface de test |
| Outillage de développement | `agent-dev-tool-savoye` | 8503 | à la demande | Évolution du code des agents, dont le manager |
| DevOps | `agent-devops-savoye` | — | — | **Hors périmètre pour l'instant** : ses missions seront définies plus tard |

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
manager/consignes.py     prompt système de l'assistant (règle de décision)
manager/routeur.py       assistant : session Agent SDK, outils internes, serveurs MCP des agents
manager/liens.py         liens de redirection préremplis vers les agents (lot 5)
manager/journal.py       journal SQLite (.tmp/manager.db) : événements, index par ticket, rétention (lot 6)
manager/sources.py       sources en lecture du tableau de bord : client MCP direct, token_usage.csv (lot 6)
manager/tableau.py       blocs du tableau de bord, sans Streamlit ni modèle (lot 6)
main.py                  CLI --step ask : assistant des agents (repris d'agent-scrum-master, qui y délègue)
manager/assistant.py, lecture_manager.py, poller_control.py   repris d'agent-scrum-master (données : AGENT_DATA_DIR)
manager/ihm_questions.py, ihm_connaissances.py  onglets « Assistant des agents » et « Connaissances partagées »
webhook_server.py, mcp_lecture.py  webhook Bitbucket / poller et lecteur MCP en lecture (repris d'agent-scrum-master)
manager/initiatives.py   point du matin, sans Streamlit ni modèle (lot 6 bis)
manager/ihm_tableau.py   onglet Tableau de bord
manager/ihm_assistant.py onglet Assistant
manager/evals.py         évaluation de la règle de décision sur tests/evals/decisions.toml
tests/                   tests unitaires ; tests/faux_agent.py simule un agent Streamlit, tests/faux_mcp.py le
                         serveur MCP de scrum-master
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
- L'import (`migration.py`) complète sans écraser et ne modifie jamais les fichiers des agents.

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
- Index par ticket : table `tickets` (événement → clé) et `etat_tickets` (dernier état de l'analyse scrum-master,
  relevé à chaque rafraîchissement du tableau de bord). `journal.ticket(cle)` ; outil `historique_ticket`.
- Rétention : 90 jours et 20 000 événements, purge au premier écrit puis au plus une fois par heure.
- Tableau de bord : `tableau.construire` (0 token) lit le superviseur, le journal, les outils `analyses`, `cucumber`,
  `builds_jenkins` (10 derniers builds par job, 3 affichés) et `patchs` (7 jours) de scrum-master (un lancement de
  son serveur MCP par rafraîchissement, résultat en cache 50 s pour toutes les sessions) et son `.tmp/token_usage.csv`. Le client MCP est appelé directement
  (`sources.appeler_mcp`, liste blanche d'outils : jamais `question` d'odatio), sans session Claude.
- Patchs livrés : outil `patchs` de scrum-master (agent-scrum-master#49), adossé à son registre local
  `.tmp/livraisons.jsonl`, écrit par son étape patch avec l'état de la pièce jointe Jira relu en GET. Un patch NON
  joint dans Jira lève une alerte ; « non vérifiée » (Jira illisible au moment de la livraison) reste dans le bloc.
  Un scrum-master sans cet outil (« Unknown tool ») affiche une invitation à le mettre à jour, sans alerte.
- Formats lus chez scrum-master (contrat, documenté dans `sources.py`) : texte des outils de
  `tools/lecture_manager.py` et colonnes `date`, `ticket`, `etape`, `cout_usd` de `token_usage.csv` (« ; »). Un
  changement de ces formats vide le bloc concerné sans erreur : le signaler dans les deux dépôts.
- **Point du matin** (lot 6 bis, `initiatives.point_du_matin`, 0 token) : encadré en tête du Tableau de bord, une
  fois par jour tant qu'il n'est pas marqué « Vu », et seulement s'il y a du nouveau. Période : depuis le dernier
  « Vu » (table `points`, une seule ligne), au plus 7 jours. Rubriques : échanges avec le manager (questions, coût,
  redirections par agent, `journal.bilan`), analyses passées « à valider », patchs livrés, builds Cucumber terminés,
  pannes d'agents et erreurs. Sources : le journal et le tableau déjà construit, sans nouvel appel aux agents. Ni
  notification, ni e-mail, ni phrase générée par le modèle, ni tickets Jira modifiés (décision du 20 novembre 2026).
- Canal de commande : HTTP sur 127.0.0.1:8499, en-tête `X-Jeton` obligatoire ; port et jeton dans
  `.tmp/superviseur.json`, recréé à chaque lancement et supprimé à l'arrêt.
- Le superviseur lance la commande de l'agent dans son dossier avec son propre venv. Il ne modifie jamais le
  dépôt, la branche ni les fichiers d'un agent.

## 3. Prompts, outils et évals

- Prompt système : `manager/consignes.py` (`MODELE`, `construire`), avec les agents du registre et les
  applications de la configuration commune.
- Outils (`manager/routeur.py`), tous en lecture seule ; aucun outil intégré de Claude Code (`tools=[]`),
  `permission_mode="dontAsk"`, `strict_mcp_config`, `setting_sources=[]` :
  - internes (serveur « manager ») : `etat_agents`, `applications`, `historique_ticket` (journal du manager et
    dernier état relevé de l'analyse, sans réinterroger les agents, lot 6), `rediriger` (prépare un lien prérempli
    affiché sous la réponse, n'agit pas) ;
  - serveurs MCP des agents (champ `mcp` du registre), lancés avec leur venv et `configuration.variables_pour` :
    scrum-master `mcp_lecture.py` (analyses, ticket, cucumber, builds_jenkins, patchs), odatio `mcp_server.py --manager`
    (projets, question : réponse en lecture seule, conversation reprise dans l'IHM d'odatio).
- **Liens préremplis** (`manager/liens.py`) : `rediriger` ouvre l'agent au bon endroit avec
  `?source=manager&page=…&ticket=…&projet=…&conversation=…&question=…&demande=…&depot=…&contexte=…` (onglets
  connus par agent dans `liens.ONGLETS` ; valeurs invalides retirées, textes bornés). Chaque agent applique le lien
  une fois par session et ne lance rien : scrum-master (`tools/lien_manager.py` : page, ticket posé dans
  « Lancement », client Cucumber, question de l'Assistant), odatio (`odatio_agent/lien_manager.py` : projet, tâche,
  conversation reprise, question), dev-tool (`agent_dev/lien_manager.py` : dépôt cible et demande). Une question ou
  une demande est PRÉREMPLIE, jamais envoyée sans clic. Aucun secret ni donnée nominative dans un lien.
- Évals : `tests/evals/decisions.toml` (question → décision et agent attendus), `manager/evals.py`.

## 4. Glossaire

- **Odatio** : le WMS (et TMS / OMS) édité ; **Standard** : le produit commun ; **spécifique** : le code propre à
  un client.
- **Cucumber** : tests BDD d'une application client (dépôt `<client>-cucumber`), joués sur Jenkins.
- **Cadrage** : analyse fonctionnelle et technique d'un ticket Jira, produite par scrum-master.
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
   | Question ponctuelle, en lecture seule, rapide | Interroger un ou plusieurs agents et faire la synthèse |
   | Validation humaine, écriture, travail long ou ressource partagée (ex. job Jenkins unique) | Rediriger vers l'agent, avec un lien direct prérempli et un résumé du contexte (ex. tests Cucumber → scrum-master) |
   | Amélioration d'un agent | Rediriger vers dev-tool avec une demande prérédigée |
   | Demande ambiguë | Poser une seule question |

3. **Moindre privilège** : chaque agent ne reçoit que les secrets listés pour lui dans le registre (ex. odatio ne
   reçoit jamais `BITBUCKET_WRITE_TOKEN` ni `JENKINS_API_TOKEN`). Le jeton Jira est unique et personnel : odatio
   le reçoit et ne s'en sert qu'en lecture (garanti par son code, pas par le jeton).
4. **Dépôt public** : aucun secret, aucune donnée réelle de client, d'entrepôt ou d'infrastructure (URL internes,
   noms de clients, schémas de base) dans les commits, exemples et tests. `agents.toml`, `config/commun.toml` et
   `.env` ne sont jamais versionnés.
5. **Verrou Jenkins** : un seul job Cucumber à la fois. Le verrou appartient à scrum-master ; le manager l'affiche
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
- IHM Streamlit sur le port 8500, liens directs vers 8501, 8502 et 8503.

Lots : 0 cible dans dev-tool (fait) · 1 structure, registre, superviseur, onglet Agents (fait) · 2 configuration
commune et migration (fait) · 3 lecture du commun par odatio et scrum-master (fait) · 4 serveurs MCP, routeur, règle
de décision (fait) ·
5 liens de redirection préremplis (fait) · 6 journal, index par ticket, tableau de bord (fait) · 6 bis-a point du
matin (fait) · 6 bis-b relances (à faire, design validé le 20 novembre 2026) : règles évaluées à chaque
rafraîchissement, affichées dans Alertes avec un lien prérempli et « Ignorer » (table `relances_ignorees`, N jours,
clé règle + objet + état : la relance revient si l'objet change). Seuils dans `[relances]` de `commun.toml`
(onglet Configuration), jours ouvrés du lundi au vendredi : analyse à valider non publiée 3 j ouvrés, redirection
vers scrum-master sans suite 2 j ouvrés, patch NON joint (immédiat), patch non vérifié 1 j, dernier build Cucumber
en échec (jusqu'à un succès), génération Cucumber 6 h. Les deux alertes existantes (patch NON joint, génération)
deviennent des relances.

## 7. Questions ouvertes

- scrum-master a aussi un écouteur de merges (`webhook_server.py --poll`), qui n'est pas une IHM Streamlit :
  faut-il le lancer avec le manager ? Le registre accepte une commande libre, mais la santé suppose aujourd'hui
  `/_stcore/health` (prévoir un champ `sante` : streamlit, http ou processus seul).
- Serveur MCP de scrum-master : il ne doit pas être déclaré dans une configuration chargée par le CLI `claude` que
  scrum-master lance depuis sa racine (pas de `.mcp.json`) ; le manager le lance lui-même.
- Ports : odatio fixe 8502 dans son `.streamlit/config.toml`, scrum-master utilise le défaut 8501. Le superviseur
  passe toujours `--server.port` : le registre fait foi.
- Coûts : le journal note le coût de chaque question (lot 6, bloc Coûts du tableau de bord) ; un modèle léger pour
  le routage ? À trancher sur quelques semaines de journal.
- **Patchs livrés** : le registre de scrum-master ne couvre que les livraisons faites depuis
  agent-scrum-master#49 (pas d'historique). Si Jira répond autre chose que 200 à la relecture des pièces jointes, la
  livraison apparaît « NON jointe » au lieu de « non vérifiée » (`jira_tools.get_jira_attachment_names`, côté
  scrum-master).
- Tableau de bord : seul scrum-master est interrogé ; odatio `projets` n'y apporte rien d'utile aujourd'hui.
