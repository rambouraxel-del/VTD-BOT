"""Pont API entre le front VTD Deals et s4mh/vinted-bot.

Python standard uniquement (aucune dépendance). Lancement :

    python server/api.py                                   # mode mock (défaut)
    VTD_SOURCE=s4mh S4MH_DB=../vinted-bot/data/vinted.db python server/api.py
    (S4MH_ACCEPTED_ONLY=0 pour voir aussi les annonces rejetées par s4mh)

Ce serveur ne contacte JAMAIS Vinted : il lit soit des mocks, soit la base
SQLite déjà remplie par s4mh, et stocke les statuts dans sa propre base.

Routes (JSON) :
    GET   /api/health
    GET   /api/listings?status=new&keywords=&brands=a,b&maxPrice=&minProfit=&minRoi=&minScore=
    GET   /api/listings/{id}
    PATCH /api/listings/{id}        {"status": "new|matched|ignored|sold"}
    GET   /api/matches
    GET   /api/brands
    GET   /api/criteria
    PUT   /api/criteria             {SearchCriteria}
    POST  /api/reset                remet tous les statuts à "new"
"""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from sources import ListingSource, MockSource, S4mhSource
from store import STATUSES, Store

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------
#  Logique métier (indépendante du HTTP, testée dans test_api.py)
# --------------------------------------------------------------------------
class ListingApi:
    def __init__(self, source: ListingSource, store: Store) -> None:
        self.source = source
        self.store = store

    def _with_status(self, listings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        statuses = self.store.statuses()
        for l in listings:
            status, updated = statuses.get(l["id"], ("new", ""))
            l["status"] = status
            l["_updated"] = updated
        return listings

    @staticmethod
    def _clean(l: dict[str, Any]) -> dict[str, Any]:
        l.pop("_updated", None)
        return l

    def listings(self, status: str = "new", criteria: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        items = self._with_status(self.source.all())
        if status != "all":
            items = [l for l in items if l["status"] == status]
        if criteria:
            items = [l for l in items if matches_criteria(l, criteria)]
        items.sort(key=lambda l: l["score"], reverse=True)
        return [self._clean(l) for l in items]

    def matches(self) -> list[dict[str, Any]]:
        items = [l for l in self._with_status(self.source.all()) if l["status"] in ("matched", "sold")]
        items.sort(key=lambda l: l["_updated"], reverse=True)  # plus récent en premier
        return [self._clean(l) for l in items]

    def get(self, listing_id: str) -> dict[str, Any] | None:
        l = self.source.get(listing_id)
        return self._clean(self._with_status([l])[0]) if l else None

    def set_status(self, listing_id: str, status: str) -> dict[str, Any] | None:
        if self.source.get(listing_id) is None:
            return None
        self.store.set_status(listing_id, status)
        return self.get(listing_id)

    def brands(self) -> list[str]:
        return sorted({l["brand"] for l in self.source.all() if l.get("brand")})


def matches_criteria(l: dict[str, Any], c: dict[str, Any]) -> bool:
    """Même règle que matchesCriteria() côté front (src/services/ListingService.ts)."""
    kw = str(c.get("keywords") or "").strip().lower()
    if kw and kw not in f"{l['title']} {l['brand']}".lower():
        return False
    brands = c.get("brands") or []
    if brands and l["brand"] not in brands:
        return False
    return (
        l["price"] <= float(c.get("maxPrice", 1e9))
        and l["profit"] >= float(c.get("minProfit", 0))
        and l["roi"] >= float(c.get("minRoi", 0))
        and l["score"] >= float(c.get("minScore", 0))
    )


def criteria_from_query(q: dict[str, list[str]]) -> dict[str, Any] | None:
    keys = ("keywords", "brands", "maxPrice", "minProfit", "minRoi", "minScore")
    if not any(k in q for k in keys):
        return None
    c: dict[str, Any] = {"keywords": q.get("keywords", [""])[0]}
    c["brands"] = [b for b in q.get("brands", [""])[0].split(",") if b]
    for k in keys[2:]:
        if k in q:
            c[k] = float(q[k][0])
    return c


# --------------------------------------------------------------------------
#  HTTP
# --------------------------------------------------------------------------
def make_handler(api: ListingApi, cors_origin: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: Any = None) -> None:
            data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", cors_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, PUT, PATCH, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def do_OPTIONS(self) -> None:
            self._send(204)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            q = parse_qs(url.query)
            path = url.path.rstrip("/")
            if path == "/api/health":
                return self._send(200, {"ok": True, "source": api.source.name})
            if path == "/api/listings":
                status = q.get("status", ["new"])[0]
                if status != "all" and status not in STATUSES:
                    return self._send(400, {"error": "statut invalide"})
                return self._send(200, api.listings(status, criteria_from_query(q)))
            if m := re.fullmatch(r"/api/listings/([^/]+)", path):
                l = api.get(unquote(m.group(1)))
                return self._send(200, l) if l else self._send(404, {"error": "annonce introuvable"})
            if path == "/api/matches":
                return self._send(200, api.matches())
            if path == "/api/brands":
                return self._send(200, api.brands())
            if path == "/api/criteria":
                return self._send(200, api.store.criteria())
            self._send(404, {"error": "route inconnue"})

        def do_PATCH(self) -> None:
            path = urlparse(self.path).path.rstrip("/")
            if m := re.fullmatch(r"/api/listings/([^/]+)", path):
                status = self._json().get("status")
                if status not in STATUSES:
                    return self._send(400, {"error": "statut invalide"})
                l = api.set_status(unquote(m.group(1)), status)
                return self._send(200, l) if l else self._send(404, {"error": "annonce introuvable"})
            self._send(404, {"error": "route inconnue"})

        def do_PUT(self) -> None:
            if urlparse(self.path).path.rstrip("/") == "/api/criteria":
                return self._send(200, api.store.save_criteria(self._json()))
            self._send(404, {"error": "route inconnue"})

        def do_POST(self) -> None:
            if urlparse(self.path).path.rstrip("/") == "/api/reset":
                api.store.reset()
                return self._send(204)
            self._send(404, {"error": "route inconnue"})

        def log_message(self, fmt: str, *args: Any) -> None:  # logs plus discrets
            print(f"[api] {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    return Handler


def build_api() -> ListingApi:
    source_name = os.getenv("VTD_SOURCE", "mock")
    if source_name == "s4mh":
        db = os.getenv("S4MH_DB", "../vinted-bot/data/vinted.db")
        if not Path(db).exists():
            raise SystemExit(f"Base s4mh introuvable : {db} (variable S4MH_DB)")
        accepted_only = os.getenv("S4MH_ACCEPTED_ONLY", "1") != "0"
        source: ListingSource = S4mhSource(db, accepted_only=accepted_only)
    else:
        source = MockSource()
    store_path = Path(os.getenv("VTD_DB", HERE / "data" / "vtd.db"))
    store_path.parent.mkdir(parents=True, exist_ok=True)
    return ListingApi(source, Store(str(store_path)))


def main() -> None:
    api = build_api()
    host, port = os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "8787"))
    server = ThreadingHTTPServer((host, port), make_handler(api, os.getenv("CORS_ORIGIN", "*")))
    print(f"Pont API VTD ({api.source.name}) sur http://{host}:{port}/api/health")
    server.serve_forever()


if __name__ == "__main__":
    main()
