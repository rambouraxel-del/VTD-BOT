"""Sources d'annonces du pont API.

Chaque source renvoie des annonces au format du front (type `Listing` de
src/types.ts), SANS le statut : le statut (new/matched/ignored/sold) est géré
par le pont dans sa propre base (store.py).

- MockSource : annonces fictives (mock_listings.json). Défaut.
- CollectorSource : annonces envoyées par le collector du PC Windows
  (POST /api/ingest/listings), stockées dans la base du pont. Source réelle.
- S4mhSource : lit, en lecture seule, la base SQLite de s4mh/vinted-bot
  (table `seen_listings`). Désactivée par défaut (s4mh ne fonctionne plus),
  conservée pour revenir en arrière. Aucune requête vers Vinted.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

HERE = Path(__file__).resolve().parent


class ListingSource(Protocol):
    name: str

    def all(self) -> list[dict[str, Any]]: ...

    def get(self, listing_id: str) -> dict[str, Any] | None: ...

    def health(self) -> dict[str, Any]: ...


class MockSource:
    name = "mock"

    def __init__(self, path: Path = HERE / "mock_listings.json") -> None:
        self._listings: list[dict[str, Any]] = json.loads(path.read_text("utf-8"))

    def all(self) -> list[dict[str, Any]]:
        return [dict(l) for l in self._listings]

    def health(self) -> dict[str, Any]:
        return {"db": True, "listings": len(self._listings)}

    def get(self, listing_id: str) -> dict[str, Any] | None:
        return next((dict(l) for l in self._listings if l["id"] == listing_id), None)


class CollectorSource:
    """Annonces reçues du collector, lues dans la base du pont (store.py)."""

    name = "collector"

    def __init__(self, store: Any) -> None:
        self.store = store

    def all(self) -> list[dict[str, Any]]:
        return [collector_view(l) for l in self.store.ingested()]

    def get(self, listing_id: str) -> dict[str, Any] | None:
        l = self.store.ingested_one(listing_id)
        return collector_view(l) if l else None

    def health(self) -> dict[str, Any]:
        return {"db": self.store.ping(), **self.store.ingest_stats()}


FRONT_FIELDS = ("id", "title", "brand", "size", "condition", "price", "resalePrice",
                "profit", "roi", "score", "imageUrl", "vintedUrl")


def collector_view(stored: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Annonce stockée -> format du front (tags calculés à la lecture)."""
    out = {k: stored.get(k, "") for k in FRONT_FIELDS}
    tags: list[str] = []
    age = _age_minutes(stored.get("_first_seen_at"), now)
    if age is not None and age < 60:
        tags.append(f"⚡ Détectée il y a {max(1, age)} min")
    if str(stored.get("condition", "")).lower().startswith("neuf avec"):
        tags.append("🏷️ Neuf avec étiquette")
    if stored.get("search"):
        tags.append(f"🔎 {stored['search']}")
    out["tags"] = tags[:3]
    return out


class S4mhSource:
    """Lecture seule de la base s4mh (`data/vinted.db` par défaut).

    Seules les annonces `accepted = 1` (retenues par l'analyse s4mh) sont
    exposées : ce sont les « opportunités » que s4mh enverrait sur Discord.
    """

    name = "s4mh"

    def __init__(self, db_path: str, *, accepted_only: bool = True, limit: int = 500) -> None:
        self.db_path = db_path
        self.accepted_only = accepted_only
        self.limit = limit

    def exists(self) -> bool:
        return Path(self.db_path).is_file()

    def _connect(self) -> sqlite3.Connection:
        # mode=ro : on ne modifie jamais la base de s4mh.
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def all(self) -> list[dict[str, Any]]:
        if not self.exists():  # s4mh pas encore démarré : aucune annonce
            return []
        where = "WHERE accepted = 1" if self.accepted_only else ""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT * FROM seen_listings {where} ORDER BY first_seen_at DESC LIMIT ?",
                (self.limit,),
            ).fetchall()
        return [s4mh_row_to_listing(dict(r)) for r in rows]

    def get(self, listing_id: str) -> dict[str, Any] | None:
        if not self.exists():
            return None
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM seen_listings WHERE vinted_id = ?", (listing_id,)
            ).fetchone()
        return s4mh_row_to_listing(dict(row)) if row else None

    def health(self) -> dict[str, Any]:
        """Base lisible ? Dernière activité de s4mh (dernier cycle de recherche)."""
        if not self.exists():
            return {"db": False, "error": "base s4mh absente (s4mh pas encore lancé ?)"}
        try:
            with closing(self._connect()) as conn:
                listings = conn.execute("SELECT COUNT(*) FROM seen_listings").fetchone()[0]
                last = conn.execute("SELECT MAX(started_at) FROM cycle_stats").fetchone()[0]
            return {"db": True, "listings": listings, "last_cycle_at": last}
        except sqlite3.Error as exc:
            return {"db": False, "error": str(exc)}


# --------------------------------------------------------------------------
#  Correspondance s4mh (table seen_listings) -> Listing du front
# --------------------------------------------------------------------------
def s4mh_row_to_listing(row: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    price = float(row.get("price") or 0)
    return {
        "id": str(row["vinted_id"]),
        "title": row.get("title") or "",
        "brand": row.get("brand") or "",
        "size": row.get("size") or "",
        "condition": row.get("condition") or "",
        "price": round(price, 2),
        "resalePrice": round(float(row.get("resale_price") or 0), 2),
        # s4mh calcule déjà une marge NETTE (frais acheteur, port, frais de revente).
        "profit": round(float(row.get("profit") or 0), 2),
        # ROI s4mh = profit / coût total (prix + frais + port), en %.
        "roi": round(float(row.get("roi_percent") or 0)),
        "score": max(0, min(100, round(float(row.get("score") or 0)))),
        "imageUrl": row.get("photo_url") or "",
        "vintedUrl": row.get("url") or "",
        "tags": build_tags(row, now)[:3],
    }


def build_tags(row: dict[str, Any], now: datetime | None = None) -> list[str]:
    """Alertes courtes calculées à partir des colonnes s4mh."""
    tags: list[str] = []
    level = row.get("defect_level") or "none"
    words = (row.get("defect_words") or "").strip()
    if level in ("confirmed", "possible"):
        tags.append(f"⚠️ Défaut {'confirmé' if level == 'confirmed' else 'possible'}"
                    + (f" : {words.split(',')[0]}" if words else ""))
    age = _age_minutes(row.get("published_at"), now)
    if age is not None and age < 60:
        tags.append(f"⚡ Publiée il y a {max(1, age)} min")
    rating, reviews = row.get("seller_rating"), int(row.get("seller_reviews") or 0)
    if rating is not None and reviews >= 3:
        tags.append(f"⭐ Vendeur {float(rating):.1f} ({reviews} avis)")
    elif reviews < 3:
        tags.append("🆕 Vendeur récent")
    if float(row.get("risk") or 0) >= 50:
        tags.append("⚠️ Risque élevé")
    return tags


def _age_minutes(value: Any, now: datetime | None) -> int | None:
    if not value:
        return None
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return int((now - dt).total_seconds() // 60)
