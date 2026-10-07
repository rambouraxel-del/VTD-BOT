"""Tests du collector — aucun accès à Vinted.  python -m unittest -v"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import collector
from vinted import VintedBlocked, VintedError, catalog_query, parse_catalog

HERE = Path(__file__).resolve().parent
FIXTURE = (HERE / "fixtures" / "catalog_sample.txt").read_text("utf-8")
SEARCHES = collector.load_searches(HERE / "searches.json")


class ParseTest(unittest.TestCase):
    def test_html_flight_payload(self):
        items = parse_catalog(FIXTURE, "www.vinted.fr")
        self.assertEqual(len(items), 9)  # 10 annonces dont 1 sponsorisée
        first = items[0]
        self.assertEqual(first, {
            "id": "9000000001", "url": "https://www.vinted.fr/items/9000000001-annonce-fictive",
            "title": "Doudoune The North Face Nuptse 700 noire", "price": 45.0, "currency": "EUR",
            "brand": "The North Face", "size": "M", "condition": "Très bon état",
            "photo": "https://images.example.invalid/9000000001.jpg"})
        self.assertIsNone(items[-1]["size"])  # « Bon état » seul : pas de taille

    def test_raw_rsc_body(self):
        rsc = '5:{"items":{"items":[{"productItem":{"id":1,"title":"T","url":"/items/1-t",' \
              '"price":{"amount":"$undefined"},"itemBox":{"secondLine":"Neuf"}}}]}}'
        item = parse_catalog(rsc, "www.vinted.fr")[0]
        self.assertEqual((item["id"], item["price"], item["size"]), ("1", None, None))

    def test_challenge_and_unknown_page(self):
        with self.assertRaises(VintedBlocked):
            parse_catalog('<html><iframe src="https://geo.captcha-delivery.com/x"></iframe></html>', "h")
        with self.assertRaises(VintedBlocked):
            parse_catalog("<title>Just a moment...</title>", "h")
        with self.assertRaises(VintedError):
            parse_catalog("<html>autre chose</html>", "h")

    def test_catalog_query_forces_newest(self):
        self.assertEqual(catalog_query("search_text=nike&order=price_low_to_high&page=3&price_to=50"),
                         "search_text=nike&price_to=50&order=newest_first")


class FilterTest(unittest.TestCase):
    def test_sizes(self):
        self.assertTrue(collector.size_matches("M", ["M", "L"]))
        self.assertTrue(collector.size_matches("L / 40 / 12", ["M", "L"]))
        self.assertFalse(collector.size_matches("XL", ["M", "L"]))
        self.assertFalse(collector.size_matches(None, ["M", "L"]))

    def test_searches_config(self):
        self.assertEqual([s["name"] for s in SEARCHES], [
            "The North Face vestes/doudounes", "Nike vestes/doudounes vintage",
            "Carhartt vestes", "Stüssy streetwear"])
        for s in SEARCHES:
            self.assertEqual((s["sizes"], s["max_price"]), (["M", "L"], 50))
            self.assertIn("price_to=50", s["url"])

    def test_keep(self):
        items = parse_catalog(FIXTURE, "www.vinted.fr")
        kept = {s["name"]: [i["id"][-2:] for i in items if collector.keep(i, s)] for s in SEARCHES}
        self.assertEqual(kept, {"The North Face vestes/doudounes": ["01", "02"],
                                "Nike vestes/doudounes vintage": ["04"],
                                "Carhartt vestes": ["06"],
                                "Stüssy streetwear": ["08"]})


class EndToEndTest(unittest.TestCase):
    """collector (fixture) -> vrai pont API (server/api.py) en mémoire."""

    def setUp(self):
        sys.path.insert(0, str(HERE.parent / "server"))
        from api import ListingApi, make_handler
        from sources import CollectorSource
        from store import Store
        self.tmp = Path(tempfile.mkdtemp())
        store = Store(str(self.tmp / "vtd.db"))
        self.api = ListingApi(CollectorSource(store), store)
        handler = make_handler(self.api, api_key="a" * 40, cors_origins=[], ingest_key="i" * 40)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        os.environ.update(VTD_API_URL=f"http://127.0.0.1:{self.server.server_port}",
                          INGEST_KEY="i" * 40, STATE_FILE=str(self.tmp / "state.json"))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for k in ("VTD_API_URL", "INGEST_KEY", "STATE_FILE"):
            os.environ.pop(k, None)

    def run_once(self, *extra):
        return collector.main(["--once", "--fixture", str(HERE / "fixtures" / "catalog_sample.txt"), *extra])

    def test_send_dedupe_and_statuses(self):
        self.assertEqual(self.run_once(), 0)
        feed = self.api.listings("new")
        self.assertEqual(len(feed), 5)
        state = json.loads((self.tmp / "state.json").read_text())
        self.assertEqual((len(state["sent"]), state["pending"]), (5, []))
        self.api.set_status("9000000001", "matched")
        self.api.set_status("9000000004", "ignored")
        # 2e passage : rien de nouveau, rien de renvoyé, statuts intacts
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(len(self.api.store.ingested()), 5)
        self.assertEqual(self.api.get("9000000001")["status"], "matched")
        self.assertEqual(self.api.get("9000000004")["status"], "ignored")
        # même après --reset-state, le VPS ne crée pas de doublon et garde les statuts
        self.assertEqual(self.run_once("--reset-state"), 0)
        self.assertEqual(len(self.api.store.ingested()), 5)
        self.assertEqual(self.api.get("9000000001")["status"], "matched")

    def test_vps_down_keeps_pending(self):
        os.environ["VTD_API_URL"] = "http://127.0.0.1:9"  # personne n'écoute
        self.assertEqual(self.run_once(), 3)
        state = json.loads((self.tmp / "state.json").read_text())
        self.assertEqual((len(state["pending"]), state["sent"]), (5, {}))

    def test_wrong_key(self):
        os.environ["INGEST_KEY"] = "x" * 40
        self.assertEqual(self.run_once(), 3)

    def test_dry_run_remembers_nothing(self):
        self.assertEqual(self.run_once("--dry-run"), 0)
        self.assertFalse((self.tmp / "state.json").exists())
        self.assertEqual(self.api.store.ingested(), [])

    def test_config_errors(self):
        os.environ["VTD_API_URL"] = "http://mon-vps.example"
        self.assertEqual(self.run_once(), 4)  # HTTPS obligatoire hors réseau local


if __name__ == "__main__":
    unittest.main()
