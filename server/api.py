"""Pont API entre le front VTD Deals et s4mh/vinted-bot.

Python standard uniquement (aucune dépendance). Lancement :

    API_KEY=... python server/api.py                       # mode mock (défaut)
    API_KEY=... VTD_SOURCE=s4mh S4MH_DB=../vinted-bot/data/vinted.db python server/api.py
    (S4MH_ACCEPTED_ONLY=0 pour voir aussi les annonces rejetées par s4mh)

Ce serveur ne contacte JAMAIS Vinted : il lit soit des mocks, soit la base
SQLite déjà remplie par s4mh, et stocke les statuts dans sa propre base.

Sources (VTD_SOURCE) : collector (défaut, annonces envoyées par le collector
du PC), mock (12 annonces fictives), s4mh (désactivé, conservé pour retour arrière).

Sécurité : toutes les routes (sauf /api/health et l'ingestion) exigent l'en-tête
`X-API-Key: <API_KEY>` (ou `Authorization: Bearer <API_KEY>`). La clé vient
uniquement de la variable d'environnement API_KEY. CORS limité à CORS_ORIGINS.

Routes (JSON) :
    GET   /api/health               public : état du pont, de la base et de s4mh
    GET   /api/listings?status=new&keywords=&brands=a,b&maxPrice=&minProfit=&minRoi=&minScore=
    GET   /api/listings/{id}
    PATCH /api/listings/{id}        {"status": "new|matched|ignored|sold"}
    GET   /api/matches
    GET   /api/brands
    GET   /api/criteria
    PUT   /api/criteria             {SearchCriteria}
    POST  /api/reset                remet tous les statuts à "new"
    POST  /api/ingest/listings      {"listings": [...]}  — réservé au collector,
                                    en-tête `X-Ingest-Key: <INGEST_KEY>` (clé distincte)
"""

from __future__ import annotations

import hmac
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from urllib.parse import urlsplit

from scoring import evaluate
from sources import CollectorSource, ListingSource, MockSource, S4mhSource
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
#  Ingestion (collector -> pont)
# --------------------------------------------------------------------------
MAX_INGEST_ITEMS = 200


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _https_url(value: Any, *, vinted_item: bool = False) -> str:
    url = _text(value, 600)
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        return ""
    if vinted_item and ("vinted." not in parts.netloc or "/items/" not in parts.path):
        return ""
    return url


def normalize_ingested(item: Any) -> dict[str, Any] | None:
    """Valide une annonce du format commun (collector) ; None si inutilisable."""
    if not isinstance(item, dict):
        return None
    listing_id = _text(item.get("id"), 64)
    title = _text(item.get("title"), 300)
    url = _https_url(item.get("vintedUrl"), vinted_item=True)
    try:
        price = round(float(item.get("price")), 2)
    except (TypeError, ValueError):
        return None
    if not (listing_id.isdigit() and title and url and 0 < price < 100_000):
        return None
    try:
        resale_hint = float(item.get("resalePrice") or 0) or None
    except (TypeError, ValueError):
        resale_hint = None
    condition = _text(item.get("condition"), 60)
    return {
        "id": listing_id,
        "title": title,
        "brand": _text(item.get("brand"), 120),
        "size": _text(item.get("size"), 60),
        "condition": condition,
        "price": price,
        "imageUrl": _https_url(item.get("imageUrl")),
        "vintedUrl": url,
        "search": _text(item.get("search"), 80),
        **evaluate(price, resale_hint, condition),
    }


def ingest(store: Store, payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("listings")
    if not isinstance(items, list):
        raise ValueError("champ `listings` (liste) attendu")
    if len(items) > MAX_INGEST_ITEMS:
        raise ValueError(f"maximum {MAX_INGEST_ITEMS} annonces par envoi")
    valid: dict[str, dict[str, Any]] = {}
    for raw in items:
        l = normalize_ingested(raw)
        if l:
            valid[l["id"]] = l  # doublons dans le même envoi : on garde le dernier
    result = store.upsert_listings(list(valid.values()))
    return {"received": len(items), "rejected": len(items) - len(valid), **result}


# --------------------------------------------------------------------------
#  HTTP
# --------------------------------------------------------------------------
MAX_BODY = 16 * 1024  # requêtes de l'app : quelques centaines d'octets
MAX_INGEST_BODY = 1024 * 1024  # envoi du collector : jusqu'à 200 annonces
MIN_KEY_LENGTH = 32


def health(api: ListingApi) -> dict[str, Any]:
    store_ok = api.store.ping()
    source = api.source.health()
    return {"ok": store_ok and source.get("db", False), "source": api.source.name,
            "store": store_ok, "data": source}


def make_handler(api: ListingApi, *, api_key: str, cors_origins: list[str], ingest_key: str = ""):
    class Handler(BaseHTTPRequestHandler):
        server_version = "vtd-api"
        sys_version = ""

        def _send(self, code: int, body: Any = None) -> None:
            data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            origin = self.headers.get("Origin")
            if origin and origin in cors_origins:  # CORS limité à notre frontend
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, PUT, PATCH, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")
                self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            given = self.headers.get("X-API-Key") or ""
            auth = self.headers.get("Authorization") or ""
            if not given and auth.lower().startswith("bearer "):
                given = auth[7:].strip()
            return bool(given) and hmac.compare_digest(given.encode(), api_key.encode())

        def _ingest_authorized(self) -> bool:
            given = self.headers.get("X-Ingest-Key") or ""
            return bool(ingest_key and given) and hmac.compare_digest(given.encode(), ingest_key.encode())

        def _json(self, limit: int = MAX_BODY) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                raise ValueError("requête trop volumineuse")
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("objet JSON attendu")
            return data

        def _dispatch(self, handler) -> None:
            path = urlparse(self.path).path.rstrip("/")
            if path == "/api/health" and self.command == "GET":
                h = health(api)  # public : aucune donnée d'annonce
                return self._send(200 if h["ok"] else 503, h)
            if path == "/api/ingest/listings":
                # Route du collector : SA clé uniquement (la clé de l'app ne suffit pas).
                if not ingest_key:
                    return self._send(503, {"error": "ingestion désactivée (INGEST_KEY absente)"})
                if self.command != "POST":
                    return self._send(405, {"error": "POST attendu"})
                if not self._ingest_authorized():
                    return self._send(401, {"error": "clé d'ingestion manquante ou invalide"})
                try:
                    return self._send(200, ingest(api.store, self._json(MAX_INGEST_BODY)))
                except (ValueError, json.JSONDecodeError) as exc:
                    return self._send(400, {"error": str(exc)})
            if not self._authorized():
                return self._send(401, {"error": "clé API manquante ou invalide"})
            try:
                handler()
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, {"error": str(exc)})

        def do_OPTIONS(self) -> None:  # pré-requête CORS : pas de clé
            self._send(204)

        def do_GET(self) -> None:
            self._dispatch(self._get)

        def do_PATCH(self) -> None:
            self._dispatch(self._patch)

        def do_PUT(self) -> None:
            self._dispatch(self._put)

        def do_POST(self) -> None:
            self._dispatch(self._post)

        def _get(self) -> None:
            url = urlparse(self.path)
            q = parse_qs(url.query)
            path = url.path.rstrip("/")
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

        def _patch(self) -> None:
            path = urlparse(self.path).path.rstrip("/")
            if m := re.fullmatch(r"/api/listings/([^/]+)", path):
                status = self._json().get("status")
                if status not in STATUSES:
                    return self._send(400, {"error": "statut invalide"})
                l = api.set_status(unquote(m.group(1)), status)
                return self._send(200, l) if l else self._send(404, {"error": "annonce introuvable"})
            self._send(404, {"error": "route inconnue"})

        def _put(self) -> None:
            if urlparse(self.path).path.rstrip("/") == "/api/criteria":
                return self._send(200, api.store.save_criteria(self._json()))
            self._send(404, {"error": "route inconnue"})

        def _post(self) -> None:
            if urlparse(self.path).path.rstrip("/") == "/api/reset":
                api.store.reset()
                return self._send(204)
            self._send(404, {"error": "route inconnue"})

        def log_message(self, fmt: str, *args: Any) -> None:  # jamais la clé dans les logs
            print(f"[api] {self.command} {urlparse(self.path).path} -> {args[1] if len(args) > 1 else ''}",
                  flush=True)

    return Handler


def build_api() -> ListingApi:
    source_name = os.getenv("VTD_SOURCE", "mock")
    store_path = Path(os.getenv("VTD_DB", HERE / "data" / "vtd.db"))
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(str(store_path))
    if source_name == "collector":
        return ListingApi(CollectorSource(store), store)
    if source_name == "s4mh":
        db = os.getenv("S4MH_DB", "../vinted-bot/data/vinted.db")
        accepted_only = os.getenv("S4MH_ACCEPTED_ONLY", "1") != "0"
        source: ListingSource = S4mhSource(db, accepted_only=accepted_only)
    else:
        source = MockSource()
    return ListingApi(source, store)


def read_api_key() -> str:
    key = os.getenv("API_KEY", "").strip()
    if len(key) < MIN_KEY_LENGTH:
        raise SystemExit(
            f"API_KEY absente ou trop courte (minimum {MIN_KEY_LENGTH} caractères). "
            "Générez-en une : python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    return key


def read_ingest_key(api_key: str) -> str:
    """Clé du collector : facultative (sinon ingestion désactivée), mais si elle
    est définie, elle doit être longue et différente de la clé de l'app."""
    key = os.getenv("INGEST_KEY", "").strip()
    if not key:
        return ""
    if len(key) < MIN_KEY_LENGTH:
        raise SystemExit(f"INGEST_KEY trop courte (minimum {MIN_KEY_LENGTH} caractères).")
    if hmac.compare_digest(key.encode(), api_key.encode()):
        raise SystemExit("INGEST_KEY doit être différente de API_KEY.")
    return key


def main() -> None:
    api_key = read_api_key()
    ingest_key = read_ingest_key(api_key)
    api = build_api()
    origins = [o.strip().rstrip("/") for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
    host, port = os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "8787"))
    server = ThreadingHTTPServer((host, port), make_handler(api, api_key=api_key, cors_origins=origins, ingest_key=ingest_key))
    print(f"Pont API VTD ({api.source.name}) sur http://{host}:{port}/api/health "
          f"— CORS : {', '.join(origins) or 'même origine uniquement'} "
          f"— ingestion : {'activée' if ingest_key else 'désactivée'}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
