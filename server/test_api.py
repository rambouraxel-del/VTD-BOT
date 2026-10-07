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

from api import ListingApi, commit_snapshot, health, ingest, make_handler, normalize_ingested, read_api_key, read_ingest_key
from sources import CollectorSource, MockSource, S4mhSource, s4mh_row_to_listing

KEY = "k" * 40
INGEST = "i" * 40
FRONT = "https://app.example.test"
from store import SnapshotIncomplete, Store

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


def raw_item(i: int, **over):
    return {"id": str(1000 + i), "title": f"Veste test {i}", "brand": "The North Face", "size": "M",
            "condition": "Très bon état", "price": 30, "resalePrice": 75,
            "imageUrl": "https://images.vinted.net/x.jpg",
            "vintedUrl": f"https://www.vinted.fr/items/{1000 + i}-veste", "search": "TNF", **over}


def make_collector_api() -> ListingApi:
    store = Store(str(Path(tempfile.mkdtemp()) / "vtd.db"))
    return ListingApi(CollectorSource(store), store)


class IngestTest(unittest.TestCase):
    def test_normalize_and_reject(self):
        l = normalize_ingested(raw_item(1))
        self.assertEqual((l["id"], l["price"], l["resalePrice"]), ("1001", 30.0, 75.0))
        self.assertGreater(l["profit"], 0)
        self.assertTrue(0 <= l["score"] <= 100)
        for bad in (raw_item(1, id="abc"), raw_item(1, price="x"), raw_item(1, title=""),
                    raw_item(1, vintedUrl="http://www.vinted.fr/items/1"),
                    raw_item(1, vintedUrl="https://evil.test/items/1"), "pas un objet"):
            self.assertIsNone(normalize_ingested(bad))
        self.assertEqual(normalize_ingested(raw_item(1, imageUrl="javascript:alert(1)"))["imageUrl"], "")

    def test_dedupe_and_keep_statuses(self):
        api = make_collector_api()
        r = ingest(api.store, {"listings": [raw_item(1), raw_item(2), raw_item(2), raw_item(3, id="x")]})
        self.assertEqual(r, {"received": 4, "rejected": 2, "inserted": 2, "updated": 0})
        api.set_status("1001", "matched")
        api.set_status("1002", "ignored")
        # Le collector renvoie les mêmes annonces (prix modifié) : pas de doublon, statuts intacts.
        r = ingest(api.store, {"listings": [raw_item(1, price=25), raw_item(2), raw_item(4)]})
        self.assertEqual((r["inserted"], r["updated"]), (1, 2))
        self.assertEqual(api.get("1001")["status"], "matched")
        self.assertEqual(api.get("1001")["price"], 25.0)
        self.assertEqual(api.get("1002")["status"], "ignored")
        self.assertEqual([l["id"] for l in api.listings("new")], ["1004"])
        self.assertEqual(len(api.store.ingested()), 3)
        self.assertEqual(set(api.listings("all")[0]), FRONT_KEYS)
        self.assertIn("🔎 TNF", api.get("1004")["tags"])

    def test_bad_payload(self):
        api = make_collector_api()
        with self.assertRaises(ValueError):
            ingest(api.store, {"listings": "x"})
        with self.assertRaises(ValueError):
            ingest(api.store, {"listings": [raw_item(i) for i in range(201)]})

    def test_ingest_key_rules(self):
        os.environ["INGEST_KEY"] = KEY
        try:
            with self.assertRaises(SystemExit):
                read_ingest_key(KEY)  # identique à API_KEY : refusé
            os.environ["INGEST_KEY"] = "court"
            with self.assertRaises(SystemExit):
                read_ingest_key(KEY)
            os.environ["INGEST_KEY"] = INGEST
            self.assertEqual(read_ingest_key(KEY), INGEST)
        finally:
            os.environ.pop("INGEST_KEY", None)
        self.assertEqual(read_ingest_key(KEY), "")


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.api = make_collector_api()
        ingest(self.api.store, {"listings": [raw_item(i) for i in range(1, 6)]})  # 1001..1005
        self.api.set_status("1001", "matched")
        self.api.set_status("1002", "sold")
        self.api.set_status("1003", "ignored")
        self.api.set_status("1004", "ignored")

    def test_replace_keeps_matched_and_statuses(self):
        ingest(self.api.store, {"listings": [raw_item(4), raw_item(6)]})
        r = commit_snapshot(self.api.store, {"ids": ["1004", "1006"]})
        self.assertEqual(r, {"kept": 2, "removed": 2, "preservedMatched": 2})
        self.assertEqual(sorted(l["id"] for l in self.api.listings("all")), ["1001", "1002", "1004", "1006"])
        self.assertEqual([l["id"] for l in self.api.listings("new")], ["1006"])
        self.assertEqual(self.api.get("1004")["status"], "ignored")
        self.assertIsNone(self.api.get("1003"))
        self.assertNotIn("1003", self.api.store.statuses())   # statut ignoré nettoyé aussi
        self.assertEqual(sorted(l["id"] for l in self.api.matches()), ["1001", "1002"])

    def test_batches_alone_never_delete(self):
        ingest(self.api.store, {"listings": [raw_item(9)]})
        self.assertEqual(len(self.api.store.ingested()), 6)

    def test_incomplete_snapshot_deletes_nothing(self):
        with self.assertRaises(SnapshotIncomplete):
            commit_snapshot(self.api.store, {"ids": ["1005", "1999"]})  # 1999 jamais reçue
        self.assertEqual(len(self.api.store.ingested()), 5)
        self.assertEqual(self.api.store.statuses()["1003"][0], "ignored")

    def test_invalid_payload(self):
        for bad in ({}, {"ids": "1001"}, {"ids": ["abc"]}, {"ids": ["1"] * 10_001}):
            with self.assertRaises(ValueError):
                commit_snapshot(self.api.store, bad)
        self.assertEqual(len(self.api.store.ingested()), 5)

    def test_empty_snapshot_keeps_only_matches(self):
        commit_snapshot(self.api.store, {"ids": []})
        self.assertEqual(sorted(l["id"] for l in self.api.listings("all")), ["1001", "1002"])
        self.assertEqual(self.api.source.health()["last_snapshot"]["count"], 0)


class IngestHttpTest(unittest.TestCase):
    def setUp(self):
        handler = make_handler(make_collector_api(), api_key=KEY, cors_origins=[], ingest_key=INGEST)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def post(self, body, headers):
        req = urllib.request.Request(self.base + "/api/ingest/listings", method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def get(self, path, key):
        req = urllib.request.Request(self.base + path, headers={"X-API-Key": key})
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read())

    def test_ingest_route(self):
        body = {"listings": [raw_item(1), raw_item(2)]}
        self.assertEqual(self.post(body, {})[0], 401)
        self.assertEqual(self.post(body, {"X-API-Key": KEY})[0], 401)        # clé de l'app refusée
        self.assertEqual(self.post(body, {"X-Ingest-Key": KEY})[0], 401)
        code, r = self.post(body, {"X-Ingest-Key": INGEST})
        self.assertEqual((code, r["inserted"]), (200, 2))
        self.assertEqual(len(self.get("/api/listings", KEY)), 2)            # visible dans l'app
        with self.assertRaises(urllib.error.HTTPError):                      # la clé d'ingestion
            self.get("/api/listings", INGEST)                                # n'ouvre pas l'app
        h = json.loads(urllib.request.urlopen(self.base + "/api/health").read())
        self.assertEqual((h["source"], h["data"]["listings"]), ("collector", 2))

    def test_snapshot_route(self):
        self.post({"listings": [raw_item(1), raw_item(2)]}, {"X-Ingest-Key": INGEST})
        url = "/api/ingest/snapshot"
        def snap(body, headers):
            req = urllib.request.Request(self.base + url, method="POST", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", **headers})
            try:
                with urllib.request.urlopen(req) as res:
                    return res.status, json.loads(res.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"null")
        self.assertEqual(snap({"ids": ["1001"]}, {"X-API-Key": KEY})[0], 401)  # clé de l'app refusée
        self.assertEqual(snap({"ids": ["1001", "1999"]}, {"X-Ingest-Key": INGEST})[0], 409)
        self.assertEqual(len(self.get("/api/listings", KEY)), 2)               # rien supprimé
        self.assertEqual(snap({"ids": "x"}, {"X-Ingest-Key": INGEST})[0], 400)
        code, r = snap({"ids": ["1001"]}, {"X-Ingest-Key": INGEST})
        self.assertEqual((code, r["kept"], r["removed"]), (200, 1, 1))
        self.assertEqual([l["id"] for l in self.get("/api/listings", KEY)], ["1001"])

    def test_ingest_disabled_without_key(self):
        self.server.shutdown()
        self.server.server_close()
        handler = make_handler(make_collector_api(), api_key=KEY, cors_origins=[])
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.assertEqual(self.post({"listings": []}, {"X-Ingest-Key": INGEST})[0], 503)


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
