"""Stockage propre au pont : statuts des annonces et critères.

s4mh n'a pas de notion de statut new/matched/ignored/sold : on le garde ici,
dans une petite base SQLite séparée (on ne touche jamais à la base s4mh).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

STATUSES = ("new", "matched", "ignored", "sold")

DEFAULT_CRITERIA: dict[str, Any] = {
    "keywords": "",
    "brands": [],
    "maxPrice": 1000,
    "minProfit": 0,
    "minRoi": 0,
    "minScore": 0,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS listing_status (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            -- Annonces envoyées par le collector (une ligne par ID Vinted).
            CREATE TABLE IF NOT EXISTS listings (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                search TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            """
        )

    def statuses(self) -> dict[str, tuple[str, str]]:
        with self._lock:
            rows = self._conn.execute("SELECT id, status, updated_at FROM listing_status")
            return {r[0]: (r[1], r[2]) for r in rows}

    def set_status(self, listing_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"statut invalide : {status}")
        with self._lock, self._conn:
            if status == "new":
                self._conn.execute("DELETE FROM listing_status WHERE id = ?", (listing_id,))
            else:
                self._conn.execute(
                    "INSERT INTO listing_status (id, status, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
                    (listing_id, status, datetime.now(timezone.utc).isoformat()),
                )

    def ping(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1 FROM listing_status LIMIT 1")
            return True
        except sqlite3.Error:
            return False

    # --- annonces ingérées (collector) ------------------------------------
    def upsert_listings(self, listings: list[dict[str, Any]]) -> dict[str, int]:
        """Ajoute les nouvelles annonces, met à jour les connues (prix, titre…).

        Ne touche JAMAIS à la table des statuts : un match ou une annonce
        ignorée le reste, même si le collector la renvoie.
        """
        now = _now()
        inserted = updated = 0
        with self._lock, self._conn:
            for l in listings:
                data = json.dumps({k: v for k, v in l.items() if k != "status"}, ensure_ascii=False)
                cur = self._conn.execute(
                    "INSERT INTO listings (id, data, search, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                    (l["id"], data, l.get("search", ""), now, now),
                )
                if cur.rowcount:
                    inserted += 1
                else:
                    self._conn.execute(
                        "UPDATE listings SET data = ?, last_seen_at = ? WHERE id = ?",
                        (data, now, l["id"]),
                    )
                    updated += 1
        return {"inserted": inserted, "updated": updated}

    def ingested(self, limit: int = 2000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data, first_seen_at FROM listings ORDER BY first_seen_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{**json.loads(d), "_first_seen_at": seen} for d, seen in rows]

    def ingested_one(self, listing_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data, first_seen_at FROM listings WHERE id = ?", (listing_id,)
            ).fetchone()
        return {**json.loads(row[0]), "_first_seen_at": row[1]} if row else None

    def ingest_stats(self) -> dict[str, Any]:
        with self._lock:
            count, last = self._conn.execute(
                "SELECT COUNT(*), MAX(last_seen_at) FROM listings"
            ).fetchone()
        return {"listings": count, "last_ingest_at": last}

    def reset(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM listing_status")

    def criteria(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = 'criteria'").fetchone()
        return {**DEFAULT_CRITERIA, **(json.loads(row[0]) if row else {})}

    def save_criteria(self, criteria: dict[str, Any]) -> dict[str, Any]:
        merged = {k: criteria.get(k, v) for k, v in DEFAULT_CRITERIA.items()}
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO kv (key, value) VALUES ('criteria', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps(merged),),
            )
        return merged
