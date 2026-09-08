"""Read-only adapter for the FinMind point-in-time momentum cache.

``load_cache`` deliberately keeps downloaded data and human-reviewed evidence
separate.  The evidence JSON has this shape::

    {
      "verified": {"universe": true, "events": true,
                   "execution": true, "coverage": true},
      "evidence": {"universe": "...", "events": "...",
                   "execution": "...", "coverage": "..."},
      "securities": [...],
      "events": [...],
      "execution": {
        "default": {"buyable": true, "sellable": true, "source": "..."},
        "overrides": {
          "2330": {
            "2020-03-23": {"buyable": false, "source": "..."},
            "2020-03-24": {"buyable": true, "sellable": false,
                           "source": "..."}
          }
        }
      }
    }

The execution default is itself a reviewed factual attestation, not a value
inferred from the presence of a price row.  An override may replace either or
both booleans and inherits the other value from the attested default.  Every
default/override needs a non-empty documentary source.
"""
from __future__ import annotations

from datetime import date
import json
import math
from pathlib import Path
import sqlite3
from typing import Any


PER_STOCK_DATASETS = (
    "TaiwanStockPrice",
    "TaiwanStockDividendResult",
    "TaiwanStockDividend",
    "TaiwanStockCapitalReductionReferencePrice",
)
GLOBAL_QUERIES = (
    {"dataset": "TaiwanStockInfo"},
    {"dataset": "TaiwanStockTradingDate"},
)
RANGED_GLOBAL_DATASETS = (
    "TaiwanStockSplitPrice",
    "TaiwanStockParValueChange",
    "TaiwanStockDelisting",
)
BENCHMARK_IDS = ("TAIEX", "TPEx")
VERIFICATION_KEYS = ("universe", "events", "execution", "coverage")


class MomentumDataError(ValueError):
    """The cache or its independent evidence cannot support a formal run."""


def _canonical(query: dict[str, Any]) -> str:
    return json.dumps(query, sort_keys=True, separators=(",", ":"))


def _query(dataset: str, start: str, end: str, sid: str | None = None) -> dict[str, str]:
    result = {"dataset": dataset, "start_date": start, "end_date": end}
    if sid is not None:
        result["data_id"] = sid
    return result


def _iso_date(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise MomentumDataError(f"{label} must be an ISO date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise MomentumDataError(f"{label} must be YYYY-MM-DD: {value!r}") from None
    if parsed.isoformat() != value:
        raise MomentumDataError(f"{label} must be canonical YYYY-MM-DD: {value!r}")
    return value


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MomentumDataError(f"cannot read manifest: {exc}") from None
    if not isinstance(manifest, dict):
        raise MomentumDataError("manifest must be a JSON object")
    start = _iso_date(manifest.get("start"), "manifest.start")
    end = _iso_date(manifest.get("end"), "manifest.end")
    if start > end:
        raise MomentumDataError("manifest.start must not be after manifest.end")
    candidate_ids = manifest.get("candidate_ids")
    if (not isinstance(candidate_ids, list) or not candidate_ids or
            any(not isinstance(sid, str) or not sid.strip() or sid != sid.strip()
                for sid in candidate_ids)):
        raise MomentumDataError("manifest.candidate_ids must be a non-empty string list")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise MomentumDataError("manifest.candidate_ids contains duplicates")
    return {**manifest, "start": start, "end": end, "candidate_ids": candidate_ids}


def _expected_queries(manifest: dict[str, Any]) -> list[dict[str, str]]:
    start, end = manifest["start"], manifest["end"]
    expected = [dict(query) for query in GLOBAL_QUERIES]
    expected.extend(_query(dataset, start, end) for dataset in RANGED_GLOBAL_DATASETS)
    expected.extend(
        _query("TaiwanStockTotalReturnIndex", start, end, index)
        for index in BENCHMARK_IDS
    )
    expected.extend(
        _query(dataset, start, end, sid)
        for dataset in PER_STOCK_DATASETS
        for sid in manifest["candidate_ids"]
    )
    return expected


def _open_read_only(path: Path) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise MomentumDataError(f"cache database does not exist: {resolved}")
    try:
        return sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise MomentumDataError(f"cannot open cache read-only: {exc}") from None


def _response_row(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT http_status,status,body,row_count FROM responses WHERE query=?", (key,)
    ).fetchone()
    if row is None:
        return None
    return {"http_status": row[0], "status": row[1], "body": row[2], "row_count": row[3]}


def _check_schema(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(
            "SELECT query,http_status,status,body,row_count FROM responses LIMIT 0"
        )
    except sqlite3.Error as exc:
        raise MomentumDataError(f"invalid cache schema: {exc}") from None


def _payload_data(row: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        payload = json.loads(row["body"])
    except (TypeError, json.JSONDecodeError):
        return None, "response body is not valid JSON"
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None, "response body.data is not a list"
    if row["row_count"] != len(data):
        return None, "response row_count does not match body.data"
    if any(not isinstance(item, dict) for item in data):
        return None, "response body.data contains a non-object row"
    return data, None


def _dates(rows: list[dict[str, Any]], start: str, end: str) -> list[str]:
    result = []
    for row in rows:
        value = row.get("date")
        if isinstance(value, str):
            try:
                parsed = _iso_date(value, "response date")
            except MomentumDataError:
                continue
            if start <= parsed <= end:
                result.append(parsed)
    return sorted(set(result))


def audit_cache(db_path: Path, manifest_path: Path) -> dict[str, Any]:
    """Return a JSON-safe, read-only completeness audit of the raw cache.

    A successful empty response counts as a completed request.  Calendar,
    benchmark and aggregate price data must nevertheless span the manifest's
    first and last market dates.  Exact canonical queries are required, so a
    cached request with an earlier ``end_date`` cannot satisfy the cutoff.
    """
    manifest = _read_manifest(Path(manifest_path))
    expected = _expected_queries(manifest)
    conn = _open_read_only(Path(db_path))
    _check_schema(conn)
    stats: dict[str, dict[str, int]] = {}
    missing: list[dict[str, str]] = []
    failed: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    calendar: list[str] = []
    price_start: str | None = None
    price_cutoff: str | None = None
    benchmark_dates: dict[str, list[str]] = {}

    def bucket(dataset: str) -> dict[str, int]:
        return stats.setdefault(dataset, {
            "required": 0, "successful": 0, "empty": 0,
            "missing": 0, "failed": 0, "invalid": 0,
        })

    start, end = manifest["start"], manifest["end"]
    try:
        for query in expected:
            summary = bucket(query["dataset"])
            summary["required"] += 1
            key = _canonical(query)
            row = _response_row(conn, key)
            if row is None:
                summary["missing"] += 1
                missing.append(query)
                continue
            if row["http_status"] != 200 or row["status"] != 200:
                summary["failed"] += 1
                failed.append({"query": query, "http_status": row["http_status"],
                               "status": row["status"]})
                continue
            data, error = _payload_data(row)
            if error:
                summary["invalid"] += 1
                invalid.append({"query": query, "reason": error})
                continue
            assert data is not None
            summary["successful"] += 1
            summary["empty"] += not data
            observed_dates = _dates(data, start, end)
            if query["dataset"] == "TaiwanStockTradingDate":
                calendar = observed_dates
            elif query["dataset"] == "TaiwanStockPrice" and observed_dates:
                row_start, row_end = observed_dates[0], observed_dates[-1]
                price_start = row_start if price_start is None else min(price_start, row_start)
                price_cutoff = row_end if price_cutoff is None else max(price_cutoff, row_end)
            elif query["dataset"] == "TaiwanStockTotalReturnIndex":
                benchmark_dates[query["data_id"]] = observed_dates
    finally:
        conn.close()

    issues: list[str] = []
    if missing:
        issues.append(f"{len(missing)} required queries are missing")
    if failed:
        issues.append(f"{len(failed)} required queries failed")
    if invalid:
        issues.append(f"{len(invalid)} required responses are invalid")

    coverage: dict[str, Any] = {
        "requested_start": start,
        "requested_end": end,
        "calendar_start": calendar[0] if calendar else None,
        "calendar_cutoff": calendar[-1] if calendar else None,
        "prices_start": price_start,
        "prices_cutoff": price_cutoff,
        "benchmarks": {},
    }
    if not calendar:
        issues.append("trading calendar has no dates in the manifest range")
    else:
        if (price_start is None or price_cutoff is None or price_start > calendar[0] or
                price_cutoff < calendar[-1]):
            issues.append("aggregate stock prices do not cover the calendar range")

        for index in BENCHMARK_IDS:
            index_dates = benchmark_dates.get(index, [])
            entry = {
                "start": index_dates[0] if index_dates else None,
                "cutoff": index_dates[-1] if index_dates else None,
            }
            coverage["benchmarks"][index] = entry
            if (not index_dates or index_dates[0] > calendar[0] or
                    index_dates[-1] < calendar[-1]):
                issues.append(f"{index} benchmark does not cover the calendar range")

    return {
        "complete": not issues,
        "certification": "NOT_CERTIFIED",
        "manifest": {"start": start, "end": end,
                     "candidate_count": len(manifest["candidate_ids"])},
        "required_requests": len(expected),
        "successful_requests": sum(item["successful"] for item in stats.values()),
        "successful_empty_responses": sum(item["empty"] for item in stats.values()),
        "datasets": stats,
        "coverage": coverage,
        "missing": missing,
        "failed": failed,
        "invalid": invalid,
        "issues": issues,
    }


def _read_evidence(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MomentumDataError(f"cannot read evidence: {exc}") from None
    if not isinstance(value, dict):
        raise MomentumDataError("evidence file must contain a JSON object")
    return value


def _source(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MomentumDataError(f"{label} must be a non-empty documentary source")
    return value.strip()


def _number(value: Any, label: str, *, positive: bool = False,
            nonnegative: bool = False) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MomentumDataError(f"{label} must be a finite number")
    if positive and value <= 0:
        raise MomentumDataError(f"{label} must be positive")
    if nonnegative and value < 0:
        raise MomentumDataError(f"{label} must not be negative")
    return value


def _normalize_securities(value: Any, candidate_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise MomentumDataError("evidence.securities must be a non-empty list")
    result = []
    seen: set[tuple[str, str, str | None]] = set()
    for index, item in enumerate(value):
        label = f"evidence.securities[{index}]"
        if not isinstance(item, dict):
            raise MomentumDataError(f"{label} must be an object")
        sid = item.get("stock_id")
        if sid not in candidate_ids:
            raise MomentumDataError(f"{label}.stock_id is not in the frozen manifest")
        start = _iso_date(item.get("start"), f"{label}.start")
        end_value = item.get("end")
        end = None if end_value is None else _iso_date(end_value, f"{label}.end")
        if end is not None and start >= end:
            raise MomentumDataError(f"{label} must have start < end")
        key = (sid, start, end)
        if key in seen:
            raise MomentumDataError(f"duplicate security interval: {key}")
        seen.add(key)
        result.append({"stock_id": sid, "start": start, "end": end,
                       "known_at": _iso_date(item.get("known_at"), f"{label}.known_at"),
                       "source": _source(item.get("source"), f"{label}.source")})
    return sorted(result, key=lambda item: (item["stock_id"], item["start"], item["end"] or "9999"))


def _normalize_events(value: Any, candidate_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise MomentumDataError("evidence.events must be a list")
    result = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        label = f"evidence.events[{index}]"
        if not isinstance(item, dict):
            raise MomentumDataError(f"{label} must be an object")
        sid = item.get("stock_id")
        if sid not in candidate_ids:
            raise MomentumDataError(f"{label}.stock_id is not in the frozen manifest")
        event_date = _iso_date(item.get("date"), f"{label}.date")
        key = (sid, event_date)
        if key in seen:
            raise MomentumDataError(
                f"multiple events for {sid} on {event_date}; combine ratio/cash into one event"
            )
        seen.add(key)
        kind = item.get("kind")
        if kind not in ("split", "distribution", "delist"):
            raise MomentumDataError(f"{label}.kind is invalid")
        normalized: dict[str, Any] = {
            "stock_id": sid, "date": event_date,
            "known_at": _iso_date(item.get("known_at"), f"{label}.known_at"),
            "kind": kind,
            "source": _source(item.get("source"), f"{label}.source"),
        }
        if kind == "split":
            normalized["ratio"] = _number(item.get("ratio"), f"{label}.ratio", positive=True)
        elif kind == "distribution":
            if "ratio" not in item and "cash" not in item:
                raise MomentumDataError(f"{label} needs an explicit ratio or cash amount")
            normalized["ratio"] = _number(item.get("ratio", 1), f"{label}.ratio", positive=True)
            normalized["cash"] = _number(item.get("cash", 0), f"{label}.cash",
                                         nonnegative=True)
            if normalized['ratio'] != 1:
                normalized['shares_available_date'] = _iso_date(item.get('shares_available_date'), f'{label}.shares_available_date')
                if normalized['shares_available_date'] < event_date:
                    raise MomentumDataError(f'{label}.shares_available_date must not precede the event')
            if normalized["cash"] or item.get("pay_date") is not None:
                normalized["pay_date"] = _iso_date(item.get("pay_date"), f"{label}.pay_date")
                if normalized["pay_date"] < event_date:
                    raise MomentumDataError(f"{label}.pay_date must not precede the event")
        else:
            normalized["cash"] = _number(item.get("cash"), f"{label}.cash",
                                         nonnegative=True)
            if item.get("pay_date") is not None:
                normalized["pay_date"] = _iso_date(item["pay_date"], f"{label}.pay_date")
                if normalized["pay_date"] < event_date:
                    raise MomentumDataError(f"{label}.pay_date must not precede the event")
        result.append(normalized)
    return sorted(result, key=lambda item: (item["date"], item["stock_id"]))


def _price_number(row: dict[str, Any], names: tuple[str, ...], label: str,
                  *, positive: bool = False) -> int | float:
    for name in names:
        if name in row:
            return _number(row[name], label, positive=positive)
    raise MomentumDataError(f"{label} is missing")


def load_cache(db_path: Path, manifest_path: Path, evidence_path: Path) -> dict[str, Any]:
    """Normalize a complete raw cache using only explicit reviewed evidence."""
    audit = audit_cache(Path(db_path), Path(manifest_path))
    if not audit["complete"]:
        raise MomentumDataError("cache is incomplete: " + "; ".join(audit["issues"]))
    manifest = _read_manifest(Path(manifest_path))
    candidate_ids = set(manifest["candidate_ids"])
    evidence_file = _read_evidence(Path(evidence_path))

    verified = evidence_file.get("verified")
    sources = evidence_file.get("evidence")
    if not isinstance(verified, dict) or not isinstance(sources, dict):
        raise MomentumDataError("evidence requires verified and evidence objects")
    for key in VERIFICATION_KEYS:
        if verified.get(key) is not True:
            raise MomentumDataError(f"evidence.verified.{key} must be explicitly true")
        _source(sources.get(key), f"evidence.evidence.{key}")

    securities = _normalize_securities(evidence_file.get("securities"), candidate_ids)
    events = _normalize_events(evidence_file.get("events"), candidate_ids)

    execution = evidence_file.get("execution")
    if not isinstance(execution, dict):
        raise MomentumDataError("evidence.execution must be an object")
    default = execution.get("default")
    overrides = execution.get("overrides", {})
    if not isinstance(default, dict):
        raise MomentumDataError("evidence.execution.default must be an object")
    for field in ("buyable", "sellable"):
        if type(default.get(field)) is not bool:
            raise MomentumDataError(f"evidence.execution.default.{field} must be boolean")
    _source(default.get("source"), "evidence.execution.default.source")
    if not isinstance(overrides, dict):
        raise MomentumDataError("evidence.execution.overrides must be an object")

    conn = _open_read_only(Path(db_path))
    _check_schema(conn)
    start, end = manifest["start"], manifest["end"]
    calendar_row = _response_row(conn, _canonical({"dataset": "TaiwanStockTradingDate"}))
    assert calendar_row is not None
    calendar_data, _ = _payload_data(calendar_row)
    assert calendar_data is not None
    calendar = _dates(calendar_data, start, end)
    calendar_set = set(calendar)
    for event in events:
        if event["date"] not in calendar_set:
            raise MomentumDataError(
                f"event date is not in the independent trading calendar: "
                f"{event['stock_id']} {event['date']}"
            )

    prices: dict[str, dict[str, dict[str, Any]]] = {}
    try:
        for sid in manifest["candidate_ids"]:
            response = _response_row(conn, _canonical(_query("TaiwanStockPrice", start, end, sid)))
            assert response is not None
            raw, _ = _payload_data(response)
            assert raw is not None
            sid_prices: dict[str, dict[str, Any]] = {}
            for index, row in enumerate(raw):
                label = f"TaiwanStockPrice[{sid}][{index}]"
                row_sid = row.get("stock_id", sid)
                if row_sid != sid:
                    raise MomentumDataError(f"{label}.stock_id does not match its query")
                trade_date = _iso_date(row.get("date"), f"{label}.date")
                if not start <= trade_date <= end:
                    continue
                if trade_date not in calendar_set:
                    raise MomentumDataError(f"{label}.date is not in the trading calendar")
                if trade_date in sid_prices:
                    raise MomentumDataError(f"duplicate price for {sid} on {trade_date}")
                volume = _price_number(row, ("Trading_Volume", "volume"), f"{label}.volume")
                if volume < 0:
                    raise MomentumDataError(f"{label}.volume must not be negative")
                sid_prices[trade_date] = {
                    "open": _price_number(row, ("open",), f"{label}.open"),
                    "close": _price_number(row, ("close",), f"{label}.close"),
                    "volume": volume,
                    "buyable": default["buyable"],
                    "sellable": default["sellable"],
                }
                if sid_prices[trade_date]["open"] < 0 or sid_prices[trade_date]["close"] < 0:
                    raise MomentumDataError(f"{label}.open/close must not be negative")
            prices[sid] = dict(sorted(sid_prices.items()))

        benchmark_raw: dict[str, list[dict[str, Any]]] = {}
        for index in BENCHMARK_IDS:
            response = _response_row(conn, _canonical(
                _query("TaiwanStockTotalReturnIndex", start, end, index)
            ))
            assert response is not None
            raw, _ = _payload_data(response)
            assert raw is not None
            benchmark_raw[index] = raw
    finally:
        conn.close()

    for sid, by_date in overrides.items():
        if sid not in candidate_ids or not isinstance(by_date, dict):
            raise MomentumDataError("execution override stock/date mapping is invalid")
        for trade_date, override in by_date.items():
            _iso_date(trade_date, f"execution.overrides[{sid}] date")
            if trade_date not in prices[sid]:
                raise MomentumDataError(f"execution override has no price row: {sid} {trade_date}")
            if not isinstance(override, dict):
                raise MomentumDataError(f"execution override must be an object: {sid} {trade_date}")
            _source(override.get("source"), f"execution override source: {sid} {trade_date}")
            changed = False
            for field in ("buyable", "sellable"):
                if field in override:
                    if type(override[field]) is not bool:
                        raise MomentumDataError(f"execution override {field} must be boolean")
                    prices[sid][trade_date][field] = override[field]
                    changed = True
            if not changed:
                raise MomentumDataError(f"execution override changes no flags: {sid} {trade_date}")

    benchmarks: dict[str, dict[str, int | float]] = {}
    for index in BENCHMARK_IDS:
        raw = benchmark_raw[index]
        values: dict[str, int | float] = {}
        for row_number, row in enumerate(raw):
            label = f"TaiwanStockTotalReturnIndex[{index}][{row_number}]"
            if row.get("stock_id", index) != index:
                raise MomentumDataError(f"{label}.stock_id does not match its query")
            index_date = _iso_date(row.get("date"), f"{label}.date")
            if start <= index_date <= end:
                if index_date in values:
                    raise MomentumDataError(f"duplicate {index} benchmark on {index_date}")
                values[index_date] = _price_number(row, ("price", "close"),
                                                   f"{label}.price", positive=True)
        benchmarks[index] = dict(sorted(values.items()))

    return {
        "calendar": calendar,
        "prices": prices,
        "securities": securities,
        "events": events,
        "benchmarks": benchmarks,
        "verified": {key: True for key in VERIFICATION_KEYS},
        "evidence": {key: sources[key].strip() for key in VERIFICATION_KEYS},
        "synthetic": False,
    }
