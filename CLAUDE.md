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
2. **Point d'entrée unique** (lot 4) : l'utilisateur pose ses questions au manager, qui applique la règle de
   décision (§ 5).
3. **Tableau de bord** (lot 6) : tickets en cours, analyses à valider, jobs Jenkins, patchs livrés, alertes.
4. **Configuration partagée** : accès et référentiel des applications Odatio, communs à tous les agents (lot 2 ;
   les agents la liront en priorité au lot 3).

Le manager **ne fait aucune écriture métier** : ni Jira, ni push, ni Jenkins. Ces gestes restent dans les agents
spécialisés, avec leurs propres validations. Il ne les contourne jamais.

## 2. Stack, structure et commandes

- Python 3.12 ; `python` n'est pas dans le PATH de PowerShell : utiliser `py -3.12` ou le venv du dépôt.
- Streamlit (IHM, port 8500). Claude Agent SDK (lot 4), SQLite (lot 6), `tomllib` et `.env`.
- Installation : `py -3.12 -m venv venv` puis `venv\Scripts\python.exe -m pip install -r requirements.txt`.
- Lancement : `lancer.bat` (= `venv\Scripts\python.exe -m manager.superviseur`).
- Tests : `venv\Scripts\python.exe -m unittest discover -s tests` (bibliothèque standard seulement). Aucun linter.

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
tests/                   tests unitaires ; tests/faux_agent.py simule un agent Streamlit
```

Prévu dans les lots suivants : `manager/consignes.py`, `manager/routeur.py`, `manager/liens.py`,
`manager/journal.py`, `tests/evals/`.

### Configuration commune

- `config/commun.toml` (sans secret) : `[acces]` (variables non secrètes : `JIRA_DOMAIN`, `BITBUCKET_URL`,
  `JENKINS_URL`…) et `[[applications]]` (nom, projet Jira, sources, dépôt Cucumber, job Jenkins, application web,
  base, logs dont SSH). `config/.env` : les secrets. Les deux sont écrits par l'onglet Configuration, jamais
  versionnés ; liste des accès dans `configuration.ACCES`.
- Noms de variables : **ceux que les agents utilisent déjà** (`JIRA_API_TOKEN`, `BITBUCKET_HTTP_TOKEN`…). Secrets
  d'une application : `DB_PASSWORD_<NOM>`, `APP_PASSWORD_<NOM>`, `SSH_PASSWORD_<NOM>` (convention d'agent-odatio).
- Au lancement d'un agent, le superviseur ajoute à son environnement : les accès non secrets, les secrets que le
  registre lui autorise (`secrets`, motifs acceptés) et `SAVOYE_CONFIG_DIR`. Relu à chaque lancement : après une
  modification, redémarrer l'agent.
- Tant qu'un agent n'est pas adapté (lot 3), son propre `.env` l'emporte : odatio ne lit l'environnement que
  pour les secrets absents de son `.env`, et scrum-master recharge son `.env` avec `override=True`.
- L'import (`migration.py`) complète sans écraser et ne modifie jamais les fichiers des agents.

### Superviseur

- Processus séparé, car Streamlit réexécute son script à chaque clic et peut ouvrir plusieurs sessions : lancer
  des processus depuis l'IHM créerait des doublons et des orphelins.
- Santé : `GET http://127.0.0.1:<port>/_stcore/health` doit répondre « ok ». États : démarrage (90 s de grâce),
  en ligne, injoignable (le processus tourne mais ne répond pas : pas de relance automatique), arrêté, en échec
  (plus de 3 relances en 10 min), lancé hors superviseur (le port répondait déjà : rien n'est lancé),
  installation incomplète (dossier ou venv absent).
- Journal de chaque agent : `.tmp/logs/<nom>.log` (sa console y est redirigée).
- Canal de commande : HTTP sur 127.0.0.1:8499, en-tête `X-Jeton` obligatoire ; port et jeton dans
  `.tmp/superviseur.json`, recréé à chaque lancement et supprimé à l'arrêt.
- Le superviseur lance la commande de l'agent dans son dossier avec son propre venv. Il ne modifie jamais le
  dépôt, la branche ni les fichiers d'un agent.

## 3. Prompts, outils et évals

- Prompt système : `manager/consignes.py` (lot 4).
- Outils : les serveurs MCP **en lecture seule** des agents, déclarés dans le registre (champ `mcp`, lot 4).
- Évals : `tests/evals/`, une question type et la décision attendue (répondre / interroger / rediriger).

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
commune et migration (fait) · 3 lecture du commun par odatio et scrum-master · 4 serveurs MCP, routeur, règle de décision ·
5 liens de redirection préremplis (`?ticket=…&onglet=…`, lus par les agents) · 6 journal, index par ticket,
tableau de bord, puis initiatives (point du matin, relances).

## 7. Questions ouvertes

- scrum-master a aussi un écouteur de merges (`webhook_server.py --poll`), qui n'est pas une IHM Streamlit :
  faut-il le lancer avec le manager ? Le registre accepte une commande libre, mais la santé suppose aujourd'hui
  `/_stcore/health` (prévoir un champ `sante` : streamlit, http ou processus seul).
- Serveur MCP de scrum-master (lot 4) : il ne doit pas être déclaré dans une configuration chargée par le CLI
  `claude` que scrum-master lance depuis sa racine, sinon chaque appel de production le recevrait.
- Ports : odatio fixe 8502 dans son `.streamlit/config.toml`, scrum-master utilise le défaut 8501. Le superviseur
  passe toujours `--server.port` : le registre fait foi.
- Coûts : le journal (lot 6) notera le coût de chaque question routée ; un modèle léger pour le routage ?
