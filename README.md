# agent-manager-savoye

Le chef d'orchestre de la suite d'agents Savoye : un double-clic lance tous les agents, les surveille et réunit
leur état dans une seule IHM, et tient la configuration commune (accès, applications Odatio). À venir : un point
d'entrée unique pour les questions et un tableau de bord. Rôle, règles et décisions : voir `CLAUDE.md`.

| Agent | Port | Lancement |
|---|---|---|
| Manager | 8500 | `lancer.bat` |
| Scrum Master (`agent-scrum-master`) | 8501 | avec le manager |
| Agent fonctionnel Odatio (`agent-odatio`) | 8502 | avec le manager |
| Outillage de développement (`agent-dev-tool-savoye`) | 8503 | à la demande, depuis l'onglet Agents |

## Installation (Windows)

Clone le manager **à côté** des autres agents (dossiers frères, chacun avec son venv déjà installé) :

```powershell
cd C:\
git clone https://github.com/AnthonyStocko/agent-manager-savoye.git
cd agent-manager-savoye
py -3.12 -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
```

Si tes agents ne sont pas dans des dossiers frères, ou pour changer un port ou un mode de lancement, copie
`agents.example.toml` en `agents.toml` (non versionné) et adapte-le. Sans `agents.toml`, le modèle est utilisé.

## Utilisation

Double-clique sur `lancer.bat`. La fenêtre noire est celle du **superviseur** : elle démarre les agents « auto »,
puis ouvre le manager sur http://localhost:8500. Laisse-la ouverte ; la fermer arrête tous les agents.

Onglet **Agents** : l'état de chaque agent (🟢 en ligne, 🟡 démarrage, 🔵 lancé hors superviseur, ⚪ arrêté,
🟠 injoignable, 🔴 en échec ou installation incomplète), son lien, les boutons Démarrer / Arrêter / Redémarrer et
la fin de son journal. Un agent qui s'arrête tout seul est relancé (3 fois au plus en 10 minutes). Un agent déjà
lancé par son propre `lancer.bat` est reconnu et laissé tel quel.

Journaux : `.tmp/logs/<agent>.log`.

## Assistant

Onglet **Assistant** : pose ta question au manager. Selon la règle de décision, il :

- **répond seul** à une question d'état général (agents en ligne, applications configurées) ;
- **interroge** les agents en lecture seule pour une question rapide : état d'une analyse ou d'un ticket, génération
  Cucumber ou build Jenkins en cours (Scrum Master), question fonctionnelle sur une application (Odatio, quelques
  minutes ; la conversation se poursuit ensuite dans Odatio) ;
- **te redirige** vers le bon agent, avec un lien et un résumé, pour tout ce qui demande une validation, une
  écriture, un travail long ou le job Jenkins (cadrage, chiffrage, patch, tests Cucumber, fichier d'interface…), et
  vers l'outillage de développement, avec une demande prête à coller, pour faire évoluer un agent.

Il ne modifie jamais rien lui-même. Il utilise le compte Claude de la configuration commune.

## Configuration commune

Onglet **Configuration** :

- **Accès** : Jira, Bitbucket, Jenkins, groundcover et compte Claude, une seule fois pour tous les agents ;
- **Applications** : le référentiel des applications Odatio (sources, base, logs, application web, projet Jira,
  job Jenkins), avec leurs mots de passe ;
- **Import** : reprend une fois les `.env` des agents et les projets d'agent-odatio. Rien n'est écrit avant ta
  validation, les fichiers des agents ne sont pas modifiés, ce qui existe déjà est gardé ;
- **Registre des agents** : ports, modes et secrets transmis à chacun.

Fichiers : `config/commun.toml` (sans secret) et `config/.env` (secrets), jamais versionnés. Au lancement, chaque
agent reçoit les accès communs et **seulement les secrets que le registre lui autorise** ; après une
modification, redémarre-le depuis l'onglet Agents.

## Tests

```powershell
venv\Scripts\python.exe -m unittest discover -s tests
```
