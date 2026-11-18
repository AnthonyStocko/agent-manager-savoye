"""Client du superviseur, utilisé par l'IHM : lit le port et le jeton dans ``.tmp/superviseur.json``."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from manager import superviseur as sup


class PiloteErreur(Exception):
    pass


class Pilote:
    def __init__(self, fichier: Path = sup.FICHIER_ETAT, delai: float = 30.0):
        self.fichier = fichier
        self.delai = delai          # un arrêt peut prendre une dizaine de secondes

    def _appel(self, methode: str, chemin: str):
        try:
            infos = json.loads(self.fichier.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise PiloteErreur("superviseur introuvable : lance le manager avec lancer.bat") from e
        requete = urllib.request.Request(f"http://127.0.0.1:{infos['port']}{chemin}", method=methode,
                                         headers={"X-Jeton": infos["jeton"]}, data=b"" if methode == "POST" else None)
        try:
            with urllib.request.urlopen(requete, timeout=self.delai) as reponse:
                return json.loads(reponse.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                message = json.loads(e.read().decode("utf-8")).get("erreur", str(e))
            except ValueError:
                message = str(e)
            raise PiloteErreur(message) from e
        except OSError as e:
            raise PiloteErreur(f"superviseur injoignable ({e}) : relance le manager avec lancer.bat") from e

    def etat(self) -> list[dict]:
        return self._appel("GET", "/etat")["agents"]

    def ordre(self, nom: str, action: str) -> dict:
        if action not in sup.ACTIONS:
            raise PiloteErreur(f"action inconnue : {action}")
        return self._appel("POST", f"/agents/{nom}/{action}")
