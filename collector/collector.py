"""Collector VTD : recherches Vinted -> filtres -> envoi au VPS (POST /api/ingest/listings).

    python collector.py                 # mode continu (usage normal, lancé par Docker)
    python collector.py --once          # un seul passage puis arrêt (tests)
    python collector.py --once --dry-run          # lit Vinted, affiche, n'envoie rien
    python collector.py --once --fixture fixtures/catalog_sample.txt
                                        # n'interroge PAS Vinted : page d'exemple locale

Configuration : variables d'environnement (.env) + searches.json.
Codes de sortie (--once) : 0 OK, 2 blocage Vinted, 3 erreur d'envoi au VPS, 4 config.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from vinted import VintedBlocked, VintedClient, VintedError, parse_catalog

HERE = Path(__file__).resolve().parent
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
MIN_INTERVAL = 60          # jamais plus d'un passage par minute
BATCH = 50                 # annonces par envoi au VPS
MAX_SENT_IDS = 20_000      # mémoire des IDs déjà envoyés
MAX_PENDING = 1_000        # annonces en attente si le VPS est injoignable
_stop = False


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {msg}", flush=True)


# ---------------------------------------------------------------- configuration
class Config:
    def __init__(self, args: argparse.Namespace) -> None:
        self.api_url = os.getenv("VTD_API_URL", "").strip().rstrip("/")
        self.ingest_key = os.getenv("INGEST_KEY", "").strip()
        self.interval = max(MIN_INTERVAL, int(os.getenv("INTERVAL_SECONDS", "180")))
        self.search_gap = (5, 15)  # pause entre deux recherches (secondes)
        self.backoff = max(5, int(os.getenv("BACKOFF_MINUTES", "15"))) * 60
        self.max_backoff = max(self.backoff, int(os.getenv("MAX_BACKOFF_MINUTES", "120")) * 60)
        self.user_agent = os.getenv("USER_AGENT", "").strip() or DEFAULT_UA
        self.accept_language = os.getenv("ACCEPT_LANGUAGE", "fr-FR,fr;q=0.9")
        self.searches_file = Path(os.getenv("SEARCHES_FILE", HERE / "searches.json"))
        self.state_file = Path(os.getenv("STATE_FILE", HERE / "data" / "state.json"))
        self.once = args.once
        self.dry_run = args.dry_run or os.getenv("DRY_RUN", "") == "1"
        self.fixture = args.fixture or os.getenv("FIXTURE_FILE", "")
        if self.fixture:
            self.search_gap = (0, 0)

    def check(self) -> list[str]:
        errors = []
        if not self.dry_run:
            parts = urlsplit(self.api_url)
            local = parts.hostname in ("localhost", "127.0.0.1", "api", "host.docker.internal")
            if parts.scheme != "https" and not (parts.scheme == "http" and local):
                errors.append("VTD_API_URL doit être en https:// (ex. https://ton-domaine.fr)")
            if len(self.ingest_key) < 32:
                errors.append("INGEST_KEY absente ou trop courte (32 caractères minimum)")
        if not self.searches_file.is_file():
            errors.append(f"fichier de recherches introuvable : {self.searches_file}")
        return errors


def load_searches(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text("utf-8"))
    searches = [s for s in data["searches"] if s.get("enabled", True)]
    defaults = data.get("defaults", {})
    return [{**defaults, **s} for s in searches]


# ---------------------------------------------------------------------- filtres
def _fold(text: Any) -> str:
    """minuscules sans accents : « Stüssy » -> « stussy »."""
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return t.lower()


def size_matches(size: str | None, wanted: list[str]) -> bool:
    if not wanted:
        return True
    if not size:
        return False
    tokens = {t.strip().upper() for t in size.replace("/", " ").split()}
    return any(w.upper() in tokens for w in wanted)


def keep(item: dict[str, Any], search: dict[str, Any]) -> bool:
    """Critères de la recherche, appliqués aussi localement (l'URL ne suffit pas toujours)."""
    if item["price"] is None or not item["url"] or not item["title"]:
        return False
    if search.get("max_price") is not None and item["price"] > float(search["max_price"]):
        return False
    if not size_matches(item.get("size"), search.get("sizes", [])):
        return False
    brands = [_fold(b) for b in search.get("brands", [])]
    haystack = _fold(f"{item.get('brand')} {item.get('title')}")
    if brands and not any(b in haystack for b in brands):
        return False
    keywords = [_fold(k) for k in search.get("keywords", [])]
    if keywords and not any(k in _fold(item["title"]) for k in keywords):
        return False
    return True


def normalize(item: dict[str, Any], search: dict[str, Any]) -> dict[str, Any]:
    """Format commun envoyé au VPS (voir docs/COLLECTOR.md)."""
    return {
        "id": item["id"],
        "title": item["title"],
        "brand": item.get("brand") or "",
        "size": item.get("size") or "",
        "condition": item.get("condition") or "",
        "price": item["price"],
        "currency": item.get("currency") or "EUR",
        "imageUrl": item.get("photo") or "",
        "vintedUrl": item["url"],
        "search": search["name"],
        "resalePrice": search.get("resale_price"),
        "detectedAt": datetime.now(timezone.utc).isoformat(),
    }


# ------------------------------------------------------------------------ état
class State:
    """IDs déjà envoyés + annonces en attente, persistés dans /data/state.json."""

    def __init__(self, path: Path, read_only: bool = False) -> None:
        self.path = path
        self.read_only = read_only  # dry-run : rien n'est mémorisé
        data = json.loads(path.read_text("utf-8")) if path.is_file() else {}
        self.sent: dict[str, str] = data.get("sent", {})
        self.pending: list[dict[str, Any]] = data.get("pending", [])

    def is_known(self, item_id: str) -> bool:
        return item_id in self.sent or any(p["id"] == item_id for p in self.pending)

    def mark_sent(self, ids: list[str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for i in ids:
            self.sent[i] = now
        if len(self.sent) > MAX_SENT_IDS:  # on oublie les plus anciens
            self.sent = dict(sorted(self.sent.items(), key=lambda kv: kv[1])[-MAX_SENT_IDS:])

    def save(self) -> None:
        if self.read_only:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"sent": self.sent, "pending": self.pending[-MAX_PENDING:]}), "utf-8")
        tmp.replace(self.path)  # écriture atomique
        (self.path.parent / "heartbeat").write_text(datetime.now(timezone.utc).isoformat(), "utf-8")


# --------------------------------------------------------------- envoi au VPS
class IngestError(Exception):
    pass


def send(cfg: Config, listings: list[dict[str, Any]]) -> dict[str, Any]:
    body = json.dumps({"source": "collector", "listings": listings}).encode()
    req = urllib.request.Request(f"{cfg.api_url}/api/ingest/listings", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Ingest-Key": cfg.ingest_key,
                                          "User-Agent": "vtd-collector"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as err:
        hint = " (INGEST_KEY différente de celle du VPS ?)" if err.code == 401 else ""
        raise IngestError(f"VPS : HTTP {err.code}{hint}") from err
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise IngestError(f"VPS injoignable : {err}") from err


def flush(cfg: Config, state: State) -> None:
    """Envoie les annonces en attente ; elles ne sont marquées envoyées qu'après un 200."""
    while state.pending:
        batch = state.pending[:BATCH]
        if cfg.dry_run:
            for l in batch:
                log(f"  [dry-run] {l['search']} | {l['price']}€ | {l['brand']} | {l['size']} | "
                    f"{l['condition']} | {l['title']} | {l['vintedUrl']}")
            r = {"inserted": len(batch), "updated": 0, "rejected": 0}
        else:
            r = send(cfg, batch)
        state.mark_sent([l["id"] for l in batch])
        state.pending = state.pending[len(batch):]
        state.save()
        if cfg.dry_run:
            log(f"Dry-run : {len(batch)} annonce(s) affichée(s), rien envoyé ni mémorisé")
        else:
            log(f"Envoyé au VPS : {len(batch)} annonce(s) — nouvelles {r.get('inserted')}, "
                f"mises à jour {r.get('updated')}, rejetées {r.get('rejected')}")


# --------------------------------------------------------------------- boucle
def run_cycle(cfg: Config, client: VintedClient, state: State, searches: list[dict[str, Any]]) -> None:
    """Un passage sur toutes les recherches. Lève VintedBlocked au premier blocage."""
    for n, search in enumerate(searches):
        if _stop:
            return
        if n:
            time.sleep(random.uniform(*cfg.search_gap))
        try:
            if cfg.fixture:
                items = parse_catalog(Path(cfg.fixture).read_text("utf-8"), "www.vinted.fr")
            else:
                items = client.fetch_catalog(search["url"])
        except VintedBlocked:
            raise
        except VintedError as err:
            log(f"[{search['name']}] erreur : {err} — recherche ignorée pour ce passage")
            continue
        fresh = [normalize(i, search) for i in items if keep(i, search) and not state.is_known(i["id"])]
        state.pending.extend(fresh)
        log(f"[{search['name']}] {len(items)} annonces lues, {len(fresh)} nouvelle(s) retenue(s)")
    state.save()
    flush(cfg, state)


def sleep(seconds: float) -> None:
    end = time.time() + seconds
    while not _stop and time.time() < end:
        time.sleep(min(5, end - time.time()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collector Vinted -> VTD-BOT")
    parser.add_argument("--once", action="store_true", help="un seul passage puis arrêt")
    parser.add_argument("--dry-run", action="store_true", help="n'envoie rien au VPS, affiche les annonces")
    parser.add_argument("--fixture", default="", help="page d'exemple locale au lieu de Vinted (tests)")
    parser.add_argument("--reset-state", action="store_true", help="oublie les IDs déjà envoyés")
    args = parser.parse_args(argv)
    cfg = Config(args)

    errors = cfg.check()
    if errors:
        for e in errors:
            log(f"❌ {e}")
        return 4
    searches = load_searches(cfg.searches_file)
    if args.reset_state and cfg.state_file.exists():
        cfg.state_file.unlink()
    state = State(cfg.state_file, read_only=cfg.dry_run)
    client = VintedClient(cfg.user_agent, cfg.accept_language)

    def stop(*_: Any) -> None:
        global _stop
        _stop = True
        log("Arrêt demandé, sauvegarde de l'état…")
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    mode = "one-shot" if cfg.once else f"continu (toutes les {cfg.interval} s)"
    source = f"fixture {cfg.fixture}" if cfg.fixture else "Vinted"
    target = "aucun envoi (dry-run)" if cfg.dry_run else cfg.api_url
    log(f"Collector démarré — {len(searches)} recherche(s), mode {mode}, source {source}, VPS : {target}")

    backoff = cfg.backoff
    while not _stop:
        try:
            run_cycle(cfg, client, state, searches)
            backoff = cfg.backoff
            if cfg.once:
                return 0
            sleep(cfg.interval + random.uniform(0, 30))
        except VintedBlocked as err:
            state.save()
            if cfg.once:
                log(f"⛔ Vinted refuse l'accès : {err}. Arrêt (aucun contournement tenté).")
                return 2
            log(f"⛔ Vinted refuse l'accès : {err}. Pause de {backoff // 60} min avant de réessayer.")
            sleep(backoff)
            backoff = min(backoff * 2, cfg.max_backoff)
        except IngestError as err:
            state.save()
            log(f"⚠️ {err} — {len(state.pending)} annonce(s) gardée(s) pour le prochain envoi")
            if cfg.once:
                return 3
            sleep(cfg.interval)
    state.save()
    log("Collector arrêté.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
