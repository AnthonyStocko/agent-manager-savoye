"""Faux agent pour les tests du superviseur : sert /_stcore/health sur le port donné.

Usage : faux_agent.py <port> [normal | meurt | muet]
- normal : répond « ok » ;
- meurt  : s'arrête aussitôt avec le code 3 ;
- muet   : tourne sans rien servir.
"""
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

port, mode = int(sys.argv[1]), (sys.argv[2] if len(sys.argv) > 2 else "normal")
if mode == "meurt":
    sys.exit(3)
if mode == "muet":
    while True:
        time.sleep(1)


class Sante(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        corps = b"ok" if self.path == "/_stcore/health" else b"?"
        self.send_response(200 if corps == b"ok" else 404)
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", port), Sante).serve_forever()
