import hashlib
import json

import pytest

from backfill_listed_universe import (
    build_artifact,
    fetch_sources,
    parse_official_date,
    parse_tpex_delisting_history,
)
from listed_universe import ListedUniverse


def _payloads():
    return {
        "TWSE_CURRENT_MASTER": [
            {"公司代號": "7610", "上市日期": "20250909"},
            {"公司代號": "5236", "上市日期": "20260716"},
            {"公司代號": "9103", "公司簡稱": "美德醫療-DR", "上市日期": "20021213"},
        ],
        "TPEX_CURRENT_MASTER": [
            {"SecuritiesCompanyCode": "1240", "DateOfListing": "20180808"},
        ],
        "TWSE_LISTING_HISTORY": [
            {"Code": "5236", "ApprovedListingDate": "1150716", "Note": "櫃轉市"},
            {"Code": "2456", "ApprovedListingDate": "0900926", "Note": ""},
        ],
        "TWSE_DELISTING_HISTORY": [
            {"Code": "2456", "DelistingDate": "110/01/05"},
        ],
        "TPEX_DELISTING_HISTORY": {
            "data": [["5236", "凌陽創新", "115-07-16", "轉上市"]]
        },
    }


def test_parse_official_date_supports_roc_and_gregorian():
    assert parse_official_date("115/09/09") == "2026-09-09"
    assert parse_official_date("1140909") == "2025-09-09"
    assert parse_official_date("2025-09-09") == "2025-09-09"
    with pytest.raises(ValueError):
        parse_official_date("")


def test_tpex_delisting_date_is_preserved_as_exclusive_end():
    rows = parse_tpex_delisting_history(_payloads()["TPEX_DELISTING_HISTORY"])
    assert rows["5236"]["end"] == "2026-07-16"


def test_builds_boundaries_transfer_and_rejects_unknown():
    artifact = build_artifact(
        ["7610", "5236", "1240", "2456", "9103", "9999"], _payloads(), []
    )
    universe = ListedUniverse(artifact["records"])

    assert not universe.eligible("7610", "2025-09-08")
    assert universe.eligible("7610", "2025-09-09")
    assert universe.eligible("2456", "2021-01-04")
    assert not universe.eligible("2456", "2021-01-05")
    assert not universe.eligible("9999", "2025-01-01")

    transfer = next(row for row in artifact["records"] if row["stock_id"] == "5236")
    assert transfer["transition_from"] == "TPEX"
    assert transfer["prior_market_end"] == transfer["start"] == "2026-07-16"
    assert artifact["coverage"]["unknown_candidate_ids"] == ["9999"]
    assert artifact["coverage"]["prior_tpex_complete_interval_count"] == 0
    assert artifact["coverage"]["confirmed_transfer_with_unknown_prior_start_stock_ids"] == ["5236"]
    tdr = next(row for row in artifact["records"] if row["stock_id"] == "9103")
    assert tdr["security_type"] == "TDR"
    assert artifact["coverage"]["twse_tdr_stock_ids"] == ["9103"]


class _Response:
    def __init__(self, body):
        self.content = body

    def raise_for_status(self):
        return None


class _Session:
    def __init__(self, bodies):
        self.headers = {}
        self._bodies = iter(bodies)

    def request(self, method, url, **kwargs):
        return _Response(next(self._bodies))


def test_raw_cache_hash_allows_offline_reproduction(tmp_path):
    bodies = [json.dumps(value, ensure_ascii=False).encode() for value in _payloads().values()]
    live_payloads, live_meta = fetch_sources(tmp_path, session=_Session(bodies))
    offline_payloads, offline_meta = fetch_sources(tmp_path, offline=True)

    assert offline_payloads == live_payloads
    assert [item["sha256"] for item in offline_meta] == [
        hashlib.sha256(body).hexdigest() for body in bodies
    ]
    for item in live_meta:
        assert (tmp_path / item["raw_path"]).exists()
