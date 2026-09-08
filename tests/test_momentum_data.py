"""Unit tests for the read-only point-in-time cache adapter."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from momentum_data import MomentumDataError, audit_cache, load_cache
from momentum_engine import validate


START = "2020-01-01"
END = "2020-01-03"
STOCKS = ("1111", "2222")
PER_STOCK = (
    "TaiwanStockPrice", "TaiwanStockDividendResult", "TaiwanStockDividend",
    "TaiwanStockCapitalReductionReferencePrice",
)


def canonical(query):
    return json.dumps(query, sort_keys=True, separators=(",", ":"))


def ranged(dataset, sid=None, end=END):
    query = {"dataset": dataset, "start_date": START, "end_date": end}
    if sid is not None:
        query["data_id"] = sid
    return query


class MomentumDataTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = root / "raw.db"
        self.manifest = root / "manifest.json"
        self.evidence = root / "evidence.json"
        self.manifest.write_text(json.dumps({
            "start": START, "end": END, "candidate_ids": list(STOCKS),
            "source": "frozen candidate download list",
        }), encoding="utf-8")
        conn = sqlite3.connect(self.db)
        conn.execute("""CREATE TABLE responses (
            query TEXT PRIMARY KEY, retrieved_at TEXT NOT NULL,
            http_status INTEGER NOT NULL, status INTEGER NOT NULL,
            body TEXT NOT NULL, sha256 TEXT NOT NULL, row_count INTEGER NOT NULL)""")
        self._insert(conn, {"dataset": "TaiwanStockInfo"}, [
            {"stock_id": "1111", "date": "2099-12-31", "type": "twse"}
        ])
        dates = [{"date": value} for value in (START, "2020-01-02", END)]
        self._insert(conn, {"dataset": "TaiwanStockTradingDate"}, dates)
        for dataset in ("TaiwanStockSplitPrice", "TaiwanStockParValueChange",
                        "TaiwanStockDelisting"):
            self._insert(conn, ranged(dataset), [])
        for index in ("TAIEX", "TPEx"):
            self._insert(conn, ranged("TaiwanStockTotalReturnIndex", index), [
                {"stock_id": index, "date": date_row["date"], "price": 100 + offset}
                for offset, date_row in enumerate(dates)
            ])
        for stock_offset, sid in enumerate(STOCKS):
            prices = [{
                "stock_id": sid, "date": date_row["date"],
                "open": 10 + stock_offset + offset,
                "close": 10.5 + stock_offset + offset,
                "Trading_Volume": 1000 + offset,
            } for offset, date_row in enumerate(dates)]
            self._insert(conn, ranged("TaiwanStockPrice", sid), prices)
            for dataset in PER_STOCK[1:]:
                self._insert(conn, ranged(dataset, sid), [])
        conn.commit()
        conn.close()
        self._write_valid_evidence()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _insert(conn, query, data, *, http=200, status=200):
        body = json.dumps({"status": status, "data": data})
        conn.execute("INSERT OR REPLACE INTO responses VALUES (?,?,?,?,?,?,?)", (
            canonical(query), "2020-01-04T00:00:00+00:00", http, status,
            body, "fixture-sha", len(data),
        ))

    def _write_valid_evidence(self):
        self.evidence.write_text(json.dumps({
            "verified": {key: True for key in
                         ("universe", "events", "execution", "coverage")},
            "evidence": {
                "universe": "exchange security history review",
                "events": "registrar event reconciliation",
                "execution": "exchange halt and order-limit review",
                "coverage": "cache cutoff reconciliation",
            },
            "securities": [
                {"stock_id": sid, "start": "2019-01-01", "end": None,
                 "known_at": "2019-01-01", "source": "exchange listing bulletin"}
                for sid in STOCKS
            ],
            "events": [
                {"stock_id": "1111", "date": "2020-01-02", "known_at": "2020-01-01",
                 "kind": "split", "ratio": 2, "source": "issuer bulletin"}
            ],
            "execution": {
                "default": {"buyable": True, "sellable": True,
                            "source": "reviewed exchange status feed"},
                "overrides": {
                    "1111": {"2020-01-02": {
                        "buyable": False, "source": "exchange halt notice"
                    }}
                },
            },
        }), encoding="utf-8")

    def test_audit_counts_successful_empty_requests_as_complete(self):
        result = audit_cache(self.db, self.manifest)
        self.assertTrue(result["complete"], result["issues"])
        self.assertEqual(result["required_requests"], 15)
        self.assertEqual(result["successful_empty_responses"], 9)
        self.assertEqual(result["datasets"]["TaiwanStockDividend"]["empty"], 2)
        self.assertEqual(result["coverage"]["calendar_cutoff"], END)

    def test_audit_rejects_a_missing_candidate_dataset(self):
        conn = sqlite3.connect(self.db)
        conn.execute("DELETE FROM responses WHERE query=?", (
            canonical(ranged("TaiwanStockDividend", "2222")),
        ))
        conn.commit()
        conn.close()
        result = audit_cache(self.db, self.manifest)
        self.assertFalse(result["complete"])
        self.assertEqual(result["datasets"]["TaiwanStockDividend"]["missing"], 1)

    def test_audit_rejects_failed_response_even_when_body_is_empty(self):
        conn = sqlite3.connect(self.db)
        self._insert(conn, ranged("TaiwanStockDividendResult", "1111"), [],
                     http=429, status=429)
        conn.commit()
        conn.close()
        result = audit_cache(self.db, self.manifest)
        self.assertFalse(result["complete"])
        self.assertEqual(result["datasets"]["TaiwanStockDividendResult"]["failed"], 1)

    def test_shorter_cached_query_does_not_satisfy_manifest_cutoff(self):
        conn = sqlite3.connect(self.db)
        conn.execute("DELETE FROM responses WHERE query=?", (
            canonical(ranged("TaiwanStockPrice", "1111")),
        ))
        self._insert(conn, ranged("TaiwanStockPrice", "1111", end="2020-01-02"), [])
        conn.commit()
        conn.close()
        result = audit_cache(self.db, self.manifest)
        self.assertFalse(result["complete"])
        self.assertEqual(result["datasets"]["TaiwanStockPrice"]["missing"], 1)

    def test_audit_rejects_benchmark_body_that_stops_before_cutoff(self):
        conn = sqlite3.connect(self.db)
        self._insert(conn, ranged("TaiwanStockTotalReturnIndex", "TAIEX"), [
            {"stock_id": "TAIEX", "date": START, "price": 100},
            {"stock_id": "TAIEX", "date": "2020-01-02", "price": 101},
        ])
        conn.commit()
        conn.close()
        result = audit_cache(self.db, self.manifest)
        self.assertFalse(result["complete"])
        self.assertIn("TAIEX benchmark does not cover", " ".join(result["issues"]))

    def test_load_normalizes_prices_benchmarks_and_attested_execution(self):
        data = load_cache(self.db, self.manifest, self.evidence)
        point = data["prices"]["1111"]["2020-01-02"]
        self.assertEqual(point, {
            "open": 11, "close": 11.5, "volume": 1001,
            "buyable": False, "sellable": True,
        })
        self.assertEqual(data["calendar"], [START, "2020-01-02", END])
        self.assertEqual(data["benchmarks"]["TAIEX"][END], 102)
        self.assertEqual(data["events"], [{
            "stock_id": "1111", "date": "2020-01-02", "known_at": "2020-01-01",
            "kind": "split", "source": "issuer bulletin", "ratio": 2,
        }])
        self.assertNotIn("2099-12-31", {s["start"] for s in data["securities"]})
        validate(data)

    def test_load_never_converts_raw_dividend_rows_into_certified_events(self):
        conn = sqlite3.connect(self.db)
        self._insert(conn, ranged("TaiwanStockDividend", "2222"), [{
            "stock_id": "2222", "date": "2020-01-02",
            "CashEarningsDistribution": 99,
        }])
        conn.commit()
        conn.close()
        data = load_cache(self.db, self.manifest, self.evidence)
        self.assertEqual([event["stock_id"] for event in data["events"]], ["1111"])

    def test_load_rejects_missing_verification_source(self):
        value = json.loads(self.evidence.read_text(encoding="utf-8"))
        value["evidence"]["events"] = ""
        self.evidence.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(MomentumDataError, "events.*documentary source"):
            load_cache(self.db, self.manifest, self.evidence)

    def test_load_rejects_verification_that_is_not_explicitly_true(self):
        value = json.loads(self.evidence.read_text(encoding="utf-8"))
        value["verified"]["coverage"] = 1
        self.evidence.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(MomentumDataError, "coverage.*explicitly true"):
            load_cache(self.db, self.manifest, self.evidence)

    def test_load_rejects_ambiguous_same_stock_date_events(self):
        value = json.loads(self.evidence.read_text(encoding="utf-8"))
        value["events"].append({
            "stock_id": "1111", "date": "2020-01-02", "known_at": "2020-01-01",
            "kind": "distribution", "cash": 1, "pay_date": "2020-01-03",
            "source": "issuer bulletin",
        })
        self.evidence.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(MomentumDataError, "multiple events"):
            load_cache(self.db, self.manifest, self.evidence)

    def test_load_rejects_unattested_execution_default(self):
        value = json.loads(self.evidence.read_text(encoding="utf-8"))
        del value["execution"]["default"]["source"]
        self.evidence.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(MomentumDataError, "default.source"):
            load_cache(self.db, self.manifest, self.evidence)


if __name__ == "__main__":
    unittest.main()
