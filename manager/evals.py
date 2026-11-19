"""Évaluation de la règle de décision de l'assistant sur des questions types (tests/evals/decisions.toml).

    venv\\Scripts\\python.exe -m manager.evals            # simulation : les agents ne sont pas interrogés
    venv\\Scripts\\python.exe -m manager.evals --reel     # les agents répondent vraiment (plus long, plus cher)

Chaque cas coûte un appel au modèle. Code de sortie 1 si une décision diffère de l'attendu.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tomllib

from manager import registre as reg
from manager import routeur

FICHIER = reg.RACINE / "tests" / "evals" / "decisions.toml"
DECISIONS = ("repondre", "interroger", "rediriger")


def charger_cas() -> list:
    cas = tomllib.loads(FICHIER.read_text(encoding="utf-8")).get("cas", [])
    for c in cas:
        if c.get("decision") not in DECISIONS:
            raise ValueError(f"décision invalide pour « {c.get('question')} » : {c.get('decision')}")
    return cas


async def evaluer(reel: bool = False) -> int:
    echecs, cout = 0, 0.0
    for c in charger_cas():
        r = routeur.Routeur(simulation=not reel)
        reponse = await r.demander(c["question"])
        obtenu = routeur.decision(reponse)
        attendu = (c["decision"], c.get("agent", ""))
        ok = obtenu == attendu if attendu[1] or attendu[0] != "repondre" else obtenu[0] == "repondre"
        echecs += not ok
        cout += reponse.cout_usd or 0
        print(f"{'OK ' if ok else 'KO '} {c['question']}\n    attendu {attendu[0]} {attendu[1]} · obtenu "
              f"{obtenu[0]} {obtenu[1]}" + ("" if ok else f"\n    réponse : {reponse.texte[:300]}"), flush=True)
    print(f"\n{echecs} écart(s) · coût {cout:.3f} $")
    return 1 if echecs else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reel", action="store_true", help="interroger vraiment les agents")
    return asyncio.run(evaluer(parser.parse_args().reel))


if __name__ == "__main__":
    sys.exit(main())
