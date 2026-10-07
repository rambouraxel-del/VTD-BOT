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

import os
import subprocess
import sys
import tarfile
import io

from api import ListingApi, health, make_handler, read_api_key
from sources import MockSource, S4mhSource, s4mh_row_to_listing

KEY = "k" * 40
FRONT = "https://app.example.test"
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
        handler = make_handler(make_api(), api_key=KEY, cors_origins=[FRONT])
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def call(self, method, path, body=None, key=KEY, headers=None, full=False):
        h = {"Content-Type": "application/json", **(headers or {})}
        if key:
            h["X-API-Key"] = key
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers=h)
        try:
            with urllib.request.urlopen(req) as res:
                raw, code, hdrs = res.read(), res.status, res.headers
        except urllib.error.HTTPError as e:
            raw, code, hdrs = e.read(), e.code, e.headers
        data = json.loads(raw) if raw else None
        return (code, data, hdrs) if full else (code, data)

    def test_api_key_required(self):
        self.assertEqual(self.call("GET", "/api/listings", key=None)[0], 401)
        self.assertEqual(self.call("GET", "/api/listings", key="mauvaise")[0], 401)
        self.assertEqual(self.call("PATCH", "/api/listings/mock-001", {"status": "matched"}, key=None)[0], 401)
        self.assertEqual(self.call("POST", "/api/reset", key=None)[0], 401)
        code, _ = self.call("GET", "/api/matches", key=None, headers={"Authorization": f"Bearer {KEY}"})
        self.assertEqual(code, 200)
        self.assertEqual(self.call("GET", "/api/health", key=None)[0], 200)  # public

    def test_cors_limited_to_frontend(self):
        _, _, h = self.call("GET", "/api/health", headers={"Origin": FRONT}, full=True)
        self.assertEqual(h.get("Access-Control-Allow-Origin"), FRONT)
        _, _, h = self.call("GET", "/api/health", headers={"Origin": "https://pirate.test"}, full=True)
        self.assertIsNone(h.get("Access-Control-Allow-Origin"))
        code, _, h = self.call("OPTIONS", "/api/listings", key=None, headers={"Origin": FRONT}, full=True)
        self.assertEqual((code, h.get("Access-Control-Allow-Origin")), (204, FRONT))

    def test_bad_body(self):
        self.assertEqual(self.call("PUT", "/api/criteria", [1, 2])[0], 400)

    def test_routes(self):
        code, h = self.call("GET", "/api/health")
        self.assertEqual((code, h["ok"], h["source"], h["store"]), (200, True, "mock", True))
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


class OpsTest(unittest.TestCase):
    def test_api_key_mandatory(self):
        old = os.environ.pop("API_KEY", None)
        try:
            with self.assertRaises(SystemExit):
                read_api_key()
            os.environ["API_KEY"] = "trop-courte"
            with self.assertRaises(SystemExit):
                read_api_key()
            os.environ["API_KEY"] = KEY
            self.assertEqual(read_api_key(), KEY)
        finally:
            os.environ.pop("API_KEY", None)
            if old is not None:
                os.environ["API_KEY"] = old

    def test_health_when_s4mh_missing(self):
        h = health(make_api(S4mhSource("/nulle/part/vinted.db")))
        self.assertFalse(h["ok"])
        self.assertFalse(h["data"]["db"])
        self.assertEqual(make_api(S4mhSource("/nulle/part/vinted.db")).listings(), [])

    def test_backup_archive(self):
        tmp = Path(tempfile.mkdtemp())
        Store(str(tmp / "vtd.db")).set_status("mock-001", "matched")
        env = {**os.environ, "VTD_DB": str(tmp / "vtd.db"), "S4MH_DB": str(tmp / "absente.db")}
        out = subprocess.run([sys.executable, "backup.py"], env=env, capture_output=True, check=True,
                             cwd=Path(__file__).parent).stdout
        with tarfile.open(fileobj=io.BytesIO(out)) as tar:
            self.assertEqual(tar.getnames(), ["data/vtd.db"])
            restored = tmp / "restored.db"
            restored.write_bytes(tar.extractfile("data/vtd.db").read())
        self.assertEqual(Store(str(restored)).statuses()["mock-001"][0], "matched")


if __name__ == "__main__":
    unittest.main()
