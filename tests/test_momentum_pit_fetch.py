"""下載器資料完整性測試，不連外、不依賴正式 DB。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fetch_momentum_pit import Client, FetchStopped, candidate_ids, init_db, write_status


class Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self.payload).encode()


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.conn = init_db(":memory:")
        self.client = Client(self.conn, "", 20)
        self.client.interval = 0
        self.query = {"dataset": "TaiwanStockPrice", "data_id": "2456"}

    def tearDown(self):
        self.conn.close()

    def test_delisted_missing_from_current_info_is_kept(self):
        info = [{"stock_id": "2330", "type": "twse"},
                {"stock_id": "2330", "type": "tpex"},
                {"stock_id": "0050", "type": "twse"}]
        self.assertEqual(candidate_ids(info, [{"stock_id": "2456"}]), ["2330", "2456"])

    def test_failed_response_not_cached_as_empty_success(self):
        with patch("urllib.request.urlopen", return_value=Response({"status": 400, "msg": "denied"})):
            with self.assertRaises(FetchStopped):
                self.client.fetch(self.query)
        self.assertIsNone(self.client.cached(self.query))
        self.assertEqual(self.conn.execute("SELECT status FROM responses").fetchone()[0], 400)

    def test_successful_empty_is_retained_without_refetch(self):
        with patch("urllib.request.urlopen", return_value=Response({"status": 200, "data": []})) as request:
            self.assertEqual(self.client.fetch(self.query), [])
            self.assertEqual(self.client.fetch(self.query), [])
            self.assertEqual(request.call_count, 1)

    def test_missing_data_is_not_success(self):
        with patch("urllib.request.urlopen", return_value=Response({"status": 200})):
            with self.assertRaises(FetchStopped):
                self.client.fetch(self.query)
        self.assertIsNone(self.client.cached(self.query))

    def test_budget_stops_before_request(self):
        self.client.limit = 0
        with patch("urllib.request.urlopen") as request:
            with self.assertRaises(FetchStopped):
                self.client.fetch(self.query)
            request.assert_not_called()

    def test_partial_coverage_never_certified(self):
        with tempfile.TemporaryDirectory() as directory:
            result = write_status(self.conn, Path(directory),
                                  {"candidate_ids": ["2456"], "start": "2018-12-01", "end": "2026-09-07"}, "stopped")
        self.assertEqual(result["certification"], "NOT_CERTIFIED")
        self.assertEqual(result["successful_requests"]["TaiwanStockPrice"], 0)


if __name__ == "__main__":
    unittest.main()
