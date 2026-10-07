"""Tests du pont API (aucun accès réseau externe).  python -m unittest -v"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from api import ListingApi, make_handler
from sources import MockSource, S4mhSource, s4mh_row_to_listing
from store import Store

FRONT_KEYS = {"id", "title", "brand", "size", "condition", "price", "resalePrice",
              "profit", "roi", "score", "imageUrl", "vintedUrl", "status", "tags"}


def make_api(source=None) -> ListingApi:
    tmp = tempfile.mkdtemp()
    return ListingApi(source or MockSource(), Store(str(Path(tmp) / "vtd.db")))


class ApiLogicTest(unittest.TestCase):
    def test_feed_matches_and_statuses(self):
        api = make_api()
        feed = api.listings("new")
        self.assertEqual(len(feed), 12)
        self.assertEqual(set(feed[0]), FRONT_KEYS)
        self.assertEqual(feed, sorted(feed, key=lambda l: -l["score"]))

        api.set_status("mock-001", "matched")
        api.set_status("mock-002", "ignored")
        api.set_status("mock-003", "matched")
        self.assertEqual(len(api.listings("new")), 9)
        self.assertEqual([l["id"] for l in api.matches()], ["mock-003", "mock-001"])
        self.assertEqual(api.get("mock-002")["status"], "ignored")

        api.set_status("mock-001", "sold")
        self.assertIn("mock-001", [l["id"] for l in api.matches()])
        api.set_status("mock-003", "new")
        self.assertEqual(len(api.listings("new")), 10)
        api.store.reset()
        self.assertEqual(len(api.listings("new")), 12)

    def test_unknown_listing_and_bad_status(self):
        api = make_api()
        self.assertIsNone(api.set_status("nope", "matched"))
        with self.assertRaises(ValueError):
            api.store.set_status("mock-001", "bidon")

    def test_criteria_filter_and_persistence(self):
        api = make_api()
        feed = api.listings("new", {"keywords": "", "brands": ["Nike"], "minScore": 0})
        self.assertEqual([l["brand"] for l in feed], ["Nike"])
        self.assertTrue(all(l["score"] >= 85 for l in api.listings("new", {"minScore": 85})))
        saved = api.store.save_criteria({"minScore": 70, "junk": 1})
        self.assertNotIn("junk", saved)
        self.assertEqual(api.store.criteria()["minScore"], 70)


class S4mhMappingTest(unittest.TestCase):
    ROW = {
        "vinted_id": "4242", "title": "Veste Carhartt Detroit", "brand": "Carhartt",
        "size": "L", "condition": "Très bon état", "price": 40.0, "resale_price": 95.0,
        "profit": 38.456, "roi_percent": 81.27, "score": 87.6, "risk": 10.0,
        "photo_url": "https://images.example/1.jpg", "url": "https://www.vinted.fr/items/4242",
        "defect_level": "possible", "defect_words": "tache, trou",
        "seller_rating": 4.86, "seller_reviews": 120,
        "published_at": None, "accepted": 1,
    }

    def test_row_mapping(self):
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        row = {**self.ROW, "published_at": (now - timedelta(minutes=4)).isoformat()}
        l = s4mh_row_to_listing(row, now)
        self.assertEqual(l["id"], "4242")
        self.assertEqual((l["profit"], l["roi"], l["score"]), (38.46, 81, 88))
        self.assertEqual(l["vintedUrl"], row["url"])
        self.assertEqual(l["tags"], ["⚠️ Défaut possible : tache", "⚡ Publiée il y a 4 min",
                                     "⭐ Vendeur 4.9 (120 avis)"])

    def test_reads_s4mh_sqlite_read_only(self):
        db = Path(tempfile.mkdtemp()) / "vinted.db"
        cols = list(self.ROW) + ["first_seen_at"]
        with sqlite3.connect(db) as conn:
            conn.execute(f"CREATE TABLE seen_listings ({', '.join(cols)})")
            for i, accepted in ((1, 1), (2, 0)):
                values = {**self.ROW, "vinted_id": str(i), "accepted": accepted,
                          "first_seen_at": f"2026-01-0{i}"}
                conn.execute(f"INSERT INTO seen_listings VALUES ({','.join('?' * len(cols))})",
                             [values[c] for c in cols])
        api = make_api(S4mhSource(str(db)))
        self.assertEqual([l["id"] for l in api.listings("new")], ["1"])  # accepted seulement
        api.set_status("1", "matched")
        self.assertEqual(api.matches()[0]["status"], "matched")
        with self.assertRaises(sqlite3.OperationalError):  # base s4mh jamais modifiée
            S4mhSource(str(db))._connect().execute("DELETE FROM seen_listings")


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(make_api(), "*"))
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as res:
                raw = res.read()
                return res.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def test_routes(self):
        self.assertEqual(self.call("GET", "/api/health"), (200, {"ok": True, "source": "mock"}))
        code, feed = self.call("GET", "/api/listings?status=new&brands=Nike,Seiko&minScore=80")
        self.assertEqual((code, sorted(l["brand"] for l in feed)), (200, ["Nike", "Seiko"]))
        code, l = self.call("PATCH", "/api/listings/mock-002", {"status": "matched"})
        self.assertEqual((code, l["status"]), (200, "matched"))
        self.assertEqual(self.call("GET", "/api/matches")[1][0]["id"], "mock-002")
        self.assertEqual(self.call("GET", "/api/listings/mock-002")[1]["status"], "matched")
        self.assertEqual(self.call("PATCH", "/api/listings/mock-002", {"status": "x"})[0], 400)
        self.assertEqual(self.call("GET", "/api/listings/inconnue")[0], 404)
        self.assertEqual(self.call("PUT", "/api/criteria", {"minScore": 60})[1]["minScore"], 60)
        self.assertEqual(self.call("GET", "/api/criteria")[1]["minScore"], 60)
        self.assertIn("Nike", self.call("GET", "/api/brands")[1])
        self.assertEqual(self.call("POST", "/api/reset")[0], 204)
        self.assertEqual(len(self.call("GET", "/api/listings")[1]), 12)


if __name__ == "__main__":
    unittest.main()
