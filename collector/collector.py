"""Collector VTD : recherches Vinted -> filtres -> envoi au VPS en mode « snapshot ».

À chaque cycle COMPLET (les 4 recherches réussies), le collector renvoie toutes
les annonces retenues pendant ce cycle (POST /api/ingest/listings, par lots), puis
valide la collecte (POST /api/ingest/snapshot avec la liste des IDs). Le VPS ne
garde alors dans le Scanner que ces annonces, plus toutes les annonces matched.
Cycle incomplet (erreur, refus Vinted, arrêt) : rien n'est envoyé ni nettoyé.

    python collector.py                 # mode continu (usage normal, lancé par Docker)
    python collector.py --once          # un seul passage puis arrêt (tests)
    python collector.py --once --dry-run          # lit Vinted, affiche, n'envoie rien
    python collector.py --once --fixture fixtures/catalog_sample.txt
                                        # n'interroge PAS Vinted : page d'exemple locale

Configuration : variables d'environnement (.env) + searches.json.
Codes de sortie (--once) : 0 OK, 2 blocage Vinted, 3 erreur d'envoi au VPS, 4 config,
5 cycle incomplet (une recherche en erreur : rien envoyé ni nettoyé).
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
import uuid
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
    """Collecte en attente d'envoi + IDs de la dernière collecte validée (/data/state.json).

    `pending` = une collecte complète pas encore validée par le VPS (VPS
    injoignable…) : elle est renvoyée en entier au passage suivant, sauf si une
    collecte plus récente la remplace.
    """

    def __init__(self, path: Path, read_only: bool = False) -> None:
        self.path = path
        self.read_only = read_only  # dry-run : rien n'est mémorisé
        data = json.loads(path.read_text("utf-8")) if path.is_file() else {}
        pending = data.get("pending")
        self.pending: dict[str, Any] | None = pending if isinstance(pending, dict) else None
        self.last_ids: list[str] = data.get("last_ids", [])

    def save(self) -> None:
        if self.read_only:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"pending": self.pending, "last_ids": self.last_ids}), "utf-8")
        tmp.replace(self.path)  # écriture atomique
        (self.path.parent / "heartbeat").write_text(datetime.now(timezone.utc).isoformat(), "utf-8")


# --------------------------------------------------------------- envoi au VPS
class IngestError(Exception):
    pass


def post(cfg: Config, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(f"{cfg.api_url}{path}", data=json.dumps(payload).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Ingest-Key": cfg.ingest_key,
                                          "User-Agent": "vtd-collector"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as err:
        hint = {401: " (INGEST_KEY différente de celle du VPS ?)",
                404: " (VPS pas à jour : git pull + docker compose up -d --build)",
                503: " (INGEST_KEY absente sur le VPS)"}.get(err.code, "")
        raise IngestError(f"VPS : HTTP {err.code}{hint}") from err
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise IngestError(f"VPS injoignable : {err}") from err


def flush(cfg: Config, state: State) -> None:
    """Envoie la collecte en attente : tous les lots, PUIS la validation.

    Les lots ne suppriment rien côté VPS ; seul le dernier appel (snapshot)
    nettoie, une fois que tout est arrivé. En cas d'échec à n'importe quelle
    étape, la collecte reste en attente et sera renvoyée en entier.
    """
    snap = state.pending
    if not snap:
        return
    listings = snap["listings"]
    if cfg.dry_run:
        for l in listings:
            log(f"  [dry-run] {l['search']} | {l['price']}€ | {l['brand']} | {l['size']} | "
                f"{l['condition']} | {l['title']} | {l['vintedUrl']}")
        log(f"Dry-run : {len(listings)} annonce(s) retenue(s), rien envoyé ni mémorisé")
        state.pending = None
        return
    inserted = 0
    for start in range(0, len(listings), BATCH):
        r = post(cfg, "/api/ingest/listings",
                 {"snapshotId": snap["id"], "listings": listings[start:start + BATCH]})
        inserted += int(r.get("inserted") or 0)
    ids = [l["id"] for l in listings]
    r = post(cfg, "/api/ingest/snapshot", {"snapshotId": snap["id"], "ids": ids})
    state.last_ids = ids
    state.pending = None
    state.save()
    log(f"Collecte validée par le VPS : {r.get('kept')} annonce(s) dans le Scanner "
        f"(dont {inserted} nouvelle(s)), {r.get('removed')} ancienne(s) retirée(s), "
        f"{r.get('preservedMatched')} match(s) conservé(s) hors collecte")


# --------------------------------------------------------------------- boucle
def collect(cfg: Config, client: VintedClient, searches: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Lit les recherches. Renvoie toutes les annonces retenues, ou None si le
    cycle est incomplet (une recherche en erreur, arrêt demandé).
    Lève VintedBlocked au premier refus."""
    retained: dict[str, dict[str, Any]] = {}
    for n, search in enumerate(searches):
        if _stop:
            log("Cycle interrompu : rien n'est envoyé.")
            return None
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
            log(f"[{search['name']}] erreur : {err} — cycle incomplet, rien n'est envoyé ni nettoyé")
            return None
        kept = [normalize(i, search) for i in items if keep(i, search)]
        for l in kept:
            retained.setdefault(l["id"], l)  # une annonce trouvée par 2 recherches : 1 seule fois
        log(f"[{search['name']}] {len(items)} annonces lues, {len(kept)} retenue(s)")
    return list(retained.values())


def run_cycle(cfg: Config, client: VintedClient, state: State, searches: list[dict[str, Any]]) -> bool:
    """Un passage complet : collecte puis envoi. Une collecte en attente (VPS
    injoignable au passage précédent) est remplacée par la nouvelle si celle-ci
    est complète, sinon renvoyée telle quelle."""
    listings = collect(cfg, client, searches)
    if listings is not None:
        new = len(set(l["id"] for l in listings) - set(state.last_ids))
        log(f"Cycle complet : {len(listings)} annonce(s) retenue(s), dont {new} jamais envoyée(s)")
        state.pending = {"id": uuid.uuid4().hex,
                         "created_at": datetime.now(timezone.utc).isoformat(),
                         "listings": listings}
        state.save()
    flush(cfg, state)
    return listings is not None


def sleep(seconds: float) -> None:
    end = time.time() + seconds
    while not _stop and time.time() < end:
        time.sleep(min(5, end - time.time()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collector Vinted -> VTD-BOT")
    parser.add_argument("--once", action="store_true", help="un seul passage puis arrêt")
    parser.add_argument("--dry-run", action="store_true", help="n'envoie rien au VPS, affiche les annonces")
    parser.add_argument("--fixture", default="", help="page d'exemple locale au lieu de Vinted (tests)")
    parser.add_argument("--reset-state", action="store_true", help="efface l'état local (collecte en attente)")
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
            complete = run_cycle(cfg, client, state, searches)
            backoff = cfg.backoff
            if cfg.once:
                return 0 if complete else 5
            sleep(cfg.interval + random.uniform(0, 30))
        except VintedBlocked as err:
            state.save()
            try:
                flush(cfg, state)  # collecte complète précédente encore en attente
            except IngestError:
                pass
            if cfg.once:
                log(f"⛔ Vinted refuse l'accès : {err}. Arrêt (aucun contournement tenté).")
                return 2
            log(f"⛔ Vinted refuse l'accès : {err}. Pause de {backoff // 60} min avant de réessayer.")
            sleep(backoff)
            backoff = min(backoff * 2, cfg.max_backoff)
        except IngestError as err:
            state.save()
            n = len(state.pending["listings"]) if state.pending else 0
            log(f"⚠️ {err} — collecte de {n} annonce(s) gardée(s) pour le prochain envoi, "
                "aucun nettoyage effectué")
            if cfg.once:
                return 3
            sleep(cfg.interval)
    state.save()
    log("Collector arrêté.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
