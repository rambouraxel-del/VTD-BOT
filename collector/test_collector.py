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


def page(*ids: int, size: str = "M", price: float = 30) -> str:
    """Page Vinted fictive (même format que la vraie) contenant des vestes TNF."""
    items = [{"productItem": {
        "id": i, "title": f"Veste The North Face {i}", "url": f"/items/{i}-veste",
        "price": {"amount": str(price), "currencyCode": "EUR"},
        "photos": [{"url": f"https://images.example.invalid/{i}.jpg"}],
        "itemBox": {"firstLine": "The North Face", "secondLine": f"{size} · Très bon état"}}} for i in ids]
    payload = '5:{"items":{"items":' + json.dumps(items) + '}}'
    return "<html><script>self.__next_f.push([1," + json.dumps(payload) + "])</script></html>"


class EndToEndTest(unittest.TestCase):
    """collector -> vrai pont API (server/api.py) en mémoire. Aucun accès à Vinted."""

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
        self.fixture = self.tmp / "page.txt"
        os.environ.update(VTD_API_URL=f"http://127.0.0.1:{self.server.server_port}",
                          INGEST_KEY="i" * 40, STATE_FILE=str(self.tmp / "state.json"))
        self._post, self._batch = collector.post, collector.BATCH

    def tearDown(self):
        collector.post, collector.BATCH = self._post, self._batch
        self.server.shutdown()
        self.server.server_close()
        for k in ("VTD_API_URL", "INGEST_KEY", "STATE_FILE"):
            os.environ.pop(k, None)

    def run_once(self, text: str | None = None, *extra):
        self.fixture.write_text(FIXTURE if text is None else text, "utf-8")
        return collector.main(["--once", "--fixture", str(self.fixture), *extra])

    def ids(self, status="all"):
        return sorted(l["id"] for l in self.api.listings(status))

    def state(self):
        return json.loads((self.tmp / "state.json").read_text())

    def test_first_cycle_sends_everything(self):
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(len(self.api.listings("new")), 5)
        self.assertEqual((self.state()["pending"], len(self.state()["last_ids"])), (None, 5))

    def test_every_cycle_resends_all_retained(self):
        self.run_once(page(1, 2))
        calls = []
        def spy(cfg, path, payload):
            calls.append((path, len(payload.get("listings", payload.get("ids", [])))))
            return self._post(cfg, path, payload)
        collector.post = spy
        self.assertEqual(self.run_once(page(1, 2)), 0)  # rien de nouveau : tout est renvoyé quand même
        self.assertEqual(calls, [("/api/ingest/listings", 2), ("/api/ingest/snapshot", 2)])

    def test_snapshot_replaces_scanner_but_keeps_matched(self):
        self.run_once(page(1, 2, 3, 4))
        self.api.set_status("1", "matched")   # match absent du prochain cycle
        self.api.set_status("2", "ignored")   # ignoré absent du prochain cycle
        self.api.set_status("3", "ignored")   # ignoré toujours présent
        self.api.set_status("4", "matched")   # match toujours présent
        self.assertEqual(self.run_once(page(3, 4, 5)), 0)
        self.assertEqual(self.ids("new"), ["5"])                       # Scanner = collecte actuelle
        self.assertEqual(self.ids(), ["1", "3", "4", "5"])             # 2 supprimée
        self.assertEqual(sorted(l["id"] for l in self.api.matches()), ["1", "4"])
        self.assertEqual(self.api.get("3")["status"], "ignored")       # statut préservé
        self.assertIsNone(self.api.get("2"))

    def test_empty_complete_cycle_empties_scanner_only(self):
        self.run_once(page(1, 2))
        self.api.set_status("1", "matched")
        self.assertEqual(self.run_once(page(7, size="XL")), 0)        # rien de retenu
        self.assertEqual(self.ids(), ["1"])

    def test_failed_search_means_no_send_no_cleanup(self):
        self.run_once(page(1, 2))
        self.assertEqual(self.run_once("<html>page inconnue</html>"), 5)
        self.assertEqual(self.ids("new"), ["1", "2"])

    def test_blocked_means_no_cleanup(self):
        self.run_once(page(1, 2))
        self.assertEqual(self.run_once("<title>Just a moment...</title>"), 2)
        self.assertEqual(self.ids("new"), ["1", "2"])

    def test_no_cleanup_between_batches_and_retry(self):
        self.run_once(page(1, 2))
        collector.BATCH = 2
        def failing_snapshot(cfg, path, payload):
            if path.endswith("/snapshot"):
                raise collector.IngestError("VPS injoignable : test")
            return self._post(cfg, path, payload)
        collector.post = failing_snapshot
        self.assertEqual(self.run_once(page(3, 4, 5, 6, 7)), 3)
        # les lots sont arrivés, mais rien n'a été nettoyé : 1 et 2 sont toujours là
        self.assertEqual(self.ids("new"), ["1", "2", "3", "4", "5", "6", "7"])
        self.assertEqual(len(self.state()["pending"]["listings"]), 5)
        # VPS revenu, Vinted en erreur ce coup-ci : la collecte complète en attente est validée
        collector.post = self._post
        self.assertEqual(self.run_once("<html>page inconnue</html>"), 5)
        self.assertEqual(self.ids("new"), ["3", "4", "5", "6", "7"])
        self.assertIsNone(self.state()["pending"])

    def test_vps_down_then_newer_cycle_replaces_pending(self):
        os.environ["VTD_API_URL"] = "http://127.0.0.1:9"  # personne n'écoute
        self.assertEqual(self.run_once(page(1, 2)), 3)
        self.assertEqual(len(self.state()["pending"]["listings"]), 2)
        os.environ["VTD_API_URL"] = f"http://127.0.0.1:{self.server.server_port}"
        self.assertEqual(self.run_once(page(2, 3)), 0)
        self.assertEqual(self.ids("new"), ["2", "3"])

    def test_wrong_key(self):
        os.environ["INGEST_KEY"] = "x" * 40
        self.assertEqual(self.run_once(), 3)

    def test_dry_run_remembers_nothing(self):
        self.assertEqual(self.run_once(None, "--dry-run"), 0)
        self.assertFalse((self.tmp / "state.json").exists())
        self.assertEqual(self.api.store.ingested(), [])

    def test_old_state_format_is_ignored(self):
        (self.tmp / "state.json").write_text(json.dumps({"sent": {"1": "x"}, "pending": [{"id": "1"}]}))
        self.assertEqual(self.run_once(page(1)), 0)
        self.assertEqual(self.ids("new"), ["1"])

    def test_config_errors(self):
        os.environ["VTD_API_URL"] = "http://mon-vps.example"
        self.assertEqual(self.run_once(), 4)  # HTTPS obligatoire hors réseau local


if __name__ == "__main__":
    unittest.main()
