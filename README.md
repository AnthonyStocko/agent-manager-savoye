# agent-manager-savoye

Le chef d'orchestre de la suite d'agents Savoye : un double-clic lance tous les agents, les surveille et réunit
leur état dans une seule IHM. À venir : un point d'entrée unique pour les questions, la configuration partagée et
un tableau de bord. Rôle, règles et décisions : voir `CLAUDE.md`.

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

## Tests

```powershell
venv\Scripts\python.exe -m unittest discover -s tests
```
