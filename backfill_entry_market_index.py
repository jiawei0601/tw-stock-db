"""回補進場濾網使用的官方 TAIEX 價格指數。

只寫入 ``data/momentum_pit/entry_market_index``，不修改任何既有資料庫。
TWSE FMTQIK 每次查詢一個月；原始回應逐月保存，重跑時可直接續用。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from collectors._http import get
from models import CollectorError


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = ROOT / "data" / "momentum_pit" / "entry_market_index"
DEFAULT_FINMIND_DB = ROOT / "data" / "momentum_pit" / "finmind_raw.db"
DEFAULT_TW_STOCKS_DB = ROOT / "data" / "tw_stocks.db"
DEFAULT_START = "2018-12-01"
DEFAULT_END = "2026-09-07"
SIGNAL_START = "2020-01-02"
MA_WINDOW = 60
SOURCE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
SOURCE = f"TWSE FMTQIK {SOURCE_URL}"


def _parse_iso_date(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a YYYY-MM-DD string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD: {value!r}") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must be canonical YYYY-MM-DD: {value!r}")
    return value


def _positive_finite_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return number


def load_market_prices(path: Path) -> dict[str, float]:
    """Strictly load a ``TAIEX_PRICE`` artifact without network access."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read market price JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("market price JSON root must be an object")
    if payload.get("index_kind") != "TAIEX_PRICE":
        raise ValueError('index_kind must be "TAIEX_PRICE"')
    if not isinstance(payload.get("source"), str) or not payload["source"].strip():
        raise ValueError("source must be a non-empty string")
    start = _parse_iso_date(payload.get("start"), "start")
    end = _parse_iso_date(payload.get("end"), "end")
    if start > end:
        raise ValueError("start must not be after end")
    if not isinstance(payload.get("fetched_at"), str) or not payload["fetched_at"].strip():
        raise ValueError("fetched_at must be a non-empty string")
    if not isinstance(payload.get("coverage"), dict):
        raise ValueError("coverage must be an object")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("records must be a list")

    result: dict[str, float] = {}
    previous: str | None = None
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            raise ValueError(f"records[{index}] must be an object")
        trade_date = _parse_iso_date(row.get("date"), f"records[{index}].date")
        if not start <= trade_date <= end:
            raise ValueError(f"records[{index}].date is outside start/end")
        if trade_date in result:
            raise ValueError(f"duplicate market price date: {trade_date}")
        if previous is not None and trade_date <= previous:
            raise ValueError("records must be sorted by ascending unique date")
        result[trade_date] = _positive_finite_float(
            row.get("close"), f"records[{index}].close"
        )
        previous = trade_date
    return result


def _month_range(start: date, end: date) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        result.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def _roc_to_iso(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"TWSE date is not a string: {value!r}")
    parts = value.strip().split("/")
    if len(parts) != 3:
        raise ValueError(f"unexpected TWSE date: {value!r}")
    try:
        result = f"{int(parts[0]) + 1911:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    except ValueError as exc:
        raise ValueError(f"unexpected TWSE date: {value!r}") from exc
    return _parse_iso_date(result, "TWSE date")


def _twse_number(value: Any, label: str) -> float:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} is not numeric: {value!r}")
    try:
        number = float(str(value).replace(",", "").strip())
    except ValueError as exc:
        raise ValueError(f"{label} is not numeric: {value!r}") from exc
    return _positive_finite_float(number, label)


def parse_fmtqik(payload: Any) -> list[dict[str, Any]]:
    """Parse one official FMTQIK response and reject ambiguous structures."""
    if not isinstance(payload, dict):
        raise ValueError("TWSE response root must be an object")
    if payload.get("stat") != "OK":
        raise ValueError(f"TWSE response stat is not OK: {payload.get('stat')!r}")
    fields = payload.get("fields")
    rows = payload.get("data")
    if not isinstance(fields, list) or not isinstance(rows, list):
        raise ValueError("TWSE response requires fields and data lists")
    try:
        date_index = fields.index("日期")
        close_index = fields.index("發行量加權股價指數")
    except ValueError as exc:
        raise ValueError(f"TWSE response has unexpected fields: {fields!r}") from exc
    needed = max(date_index, close_index)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) <= needed:
            raise ValueError(f"TWSE data row {index} is shorter than fields")
        trade_date = _roc_to_iso(row[date_index])
        if trade_date in seen:
            raise ValueError(f"duplicate date in TWSE response: {trade_date}")
        seen.add(trade_date)
        result.append({"date": trade_date, "close": _twse_number(row[close_index], "close")})
    return sorted(result, key=lambda row: row["date"])


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _load_or_fetch_month(year: int, month: int, raw_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    label = f"{year:04d}-{month:02d}"
    raw_path = raw_dir / f"{label}.json"
    reused = False
    if raw_path.exists():
        raw_bytes = raw_path.read_bytes()
        try:
            payload = json.loads(raw_bytes.decode("utf-8-sig"))
            rows = parse_fmtqik(payload)
            reused = True
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raw_bytes = b""
    else:
        raw_bytes = b""
    if not raw_bytes:
        response = get(
            "entry_market_index",
            SOURCE_URL,
            params={"date": f"{year:04d}{month:02d}01", "response": "json"},
            throttle_bucket="entry_market_index",
        )
        raw_bytes = response.content
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError(f"{label} TWSE response is not JSON") from exc
        rows = parse_fmtqik(payload)
        _atomic_write(raw_path, raw_bytes)
    return rows, {
        "month": label,
        "file": f"raw/{raw_path.name}",
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "bytes": len(raw_bytes),
        "records": len(rows),
        "cache_reused": reused,
    }


def _open_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ValueError(f"required database does not exist: {path}")
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def _finmind_calendar_evidence(path: Path, start: str, end: str) -> dict[str, Any]:
    """重建 dynamic_momentum.load_data 使用的有效日曆及排除證據。

    TaiwanStockTotalReturnIndex 只參與原程式既有的「該日是否有市場觀測」判斷，
    絕不作為本模組的指數價格。
    """
    conn = _open_read_only(path)
    try:
        canonical = json.dumps(
            {"dataset": "TaiwanStockTradingDate"}, sort_keys=True, separators=(",", ":")
        )
        calendar_row = conn.execute(
            "SELECT http_status, status, body FROM responses WHERE query=?", (canonical,)
        ).fetchone()
        if calendar_row is None or calendar_row[0] != 200 or calendar_row[1] != 200:
            raise ValueError("FinMind TaiwanStockTradingDate response is missing or unsuccessful")
        try:
            payload = json.loads(calendar_row[2])
        except json.JSONDecodeError as exc:
            raise ValueError("FinMind TaiwanStockTradingDate body is invalid JSON") from exc
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("FinMind TaiwanStockTradingDate data is not a list")
        reference: set[str] = set()
        for index, item in enumerate(rows):
            if not isinstance(item, dict):
                raise ValueError(f"FinMind calendar row {index} is not an object")
            value = _parse_iso_date(item.get("date"), f"FinMind calendar row {index}.date")
            if start <= value <= end:
                reference.add(value)

        observed_prices: set[str] = set()
        total_return_dates: set[str] = set()
        response_rows = conn.execute(
            "SELECT query, status, body FROM responses "
            "WHERE query LIKE '%TaiwanStockPrice%' "
            "OR query LIKE '%TaiwanStockTotalReturnIndex%'"
        )
        for raw_query, status, body in response_rows:
            if status != 200:
                continue
            try:
                query = json.loads(raw_query)
                response_payload = json.loads(body)
            except json.JSONDecodeError as exc:
                raise ValueError("FinMind price/calendar evidence contains invalid JSON") from exc
            data = response_payload.get("data") if isinstance(response_payload, dict) else None
            if not isinstance(data, list):
                raise ValueError("FinMind price/calendar evidence data is not a list")
            dataset = query.get("dataset") if isinstance(query, dict) else None
            if dataset == "TaiwanStockPrice":
                for item in data:
                    day = item.get("date") if isinstance(item, dict) else None
                    volume = item.get("Trading_Volume", 0) if isinstance(item, dict) else 0
                    if isinstance(day, str) and start <= day <= end and isinstance(volume, (int, float)) and volume > 0:
                        observed_prices.add(day)
            elif dataset == "TaiwanStockTotalReturnIndex" and query.get("data_id") == "TAIEX":
                for item in data:
                    day = item.get("date") if isinstance(item, dict) else None
                    if isinstance(day, str) and start <= day <= end:
                        total_return_dates.add(day)
    finally:
        conn.close()

    excluded = sorted(reference - observed_prices - total_return_dates)
    effective = sorted(reference - set(excluded))
    return {
        "reference": sorted(reference),
        "observed_positive_volume": sorted(observed_prices),
        "total_return_observed": sorted(total_return_dates),
        "excluded": excluded,
        "effective": effective,
    }


def _crosscheck_taiex(path: Path, prices: dict[str, float]) -> dict[str, Any]:
    conn = _open_read_only(path)
    try:
        rows = conn.execute("SELECT date, close FROM taiex_daily ORDER BY date").fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"cannot read taiex_daily from {path}: {exc}") from exc
    finally:
        conn.close()
    overlap = [(day, float(close)) for day, close in rows if day in prices]
    mismatches = [
        {"date": day, "official": prices[day], "sqlite": close, "absolute_difference": abs(prices[day] - close)}
        for day, close in overlap
        if not math.isclose(prices[day], close, rel_tol=0.0, abs_tol=1e-9)
    ]
    return {
        "database": str(path),
        "overlap_records": len(overlap),
        "exact_value_matches": len(overlap) - len(mismatches),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def _coverage(
    prices: dict[str, float], calendar_evidence: dict[str, Any], start: str, end: str,
    raw_manifest: list[dict[str, Any]], crosscheck: dict[str, Any],
) -> dict[str, Any]:
    official_dates = sorted(prices)
    official_set = set(official_dates)
    reference = calendar_evidence["reference"]
    effective = calendar_evidence["effective"]
    reference_set = set(reference)
    effective_set = set(effective)
    reference_missing = sorted(reference_set - official_set)
    effective_missing = sorted(effective_set - official_set)
    extra = sorted(official_set - effective_set)
    positions = {day: index for index, day in enumerate(official_dates)}
    signal_days = [day for day in effective if SIGNAL_START <= day <= end]
    insufficient = [
        day for day in signal_days
        if day not in positions or positions[day] + 1 < MA_WINDOW
    ]
    digest = hashlib.sha256()
    for item in raw_manifest:
        digest.update(item["month"].encode("ascii"))
        digest.update(item["sha256"].encode("ascii"))
    return {
        "requested_start": start,
        "requested_end": end,
        "first_official_date": official_dates[0] if official_dates else None,
        "last_official_date": official_dates[-1] if official_dates else None,
        "official_record_count": len(official_dates),
        "finmind_reference_calendar_first": reference[0] if reference else None,
        "finmind_reference_calendar_last": reference[-1] if reference else None,
        "finmind_reference_calendar_count": len(reference),
        "finmind_reference_dates_missing_official_price_count": len(reference_missing),
        "finmind_reference_dates_missing_official_price": reference_missing,
        "effective_calendar_rule": (
            "dynamic_momentum.load_data: remove a reference date only when no "
            "TaiwanStockPrice row has Trading_Volume>0 and TAIEX total-return has no row; "
            "total-return values are not used as prices"
        ),
        "effective_calendar_excluded_dates": calendar_evidence["excluded"],
        "effective_calendar_first": effective[0] if effective else None,
        "effective_calendar_last": effective[-1] if effective else None,
        "effective_calendar_count": len(effective),
        "effective_dates_missing_official_price_count": len(effective_missing),
        "effective_dates_missing_official_price": effective_missing,
        "official_dates_missing_effective_calendar_count": len(extra),
        "official_dates_missing_effective_calendar": extra,
        "signal_start": SIGNAL_START,
        "ma_window_market_days": MA_WINDOW,
        "signal_dates_checked": len(signal_days),
        "signal_dates_without_60_market_days_count": len(insufficient),
        "signal_dates_without_60_market_days": insufficient,
        "raw_month_count": len(raw_manifest),
        "raw_source_fingerprint_sha256": digest.hexdigest(),
        "sqlite_crosscheck": crosscheck,
    }


def backfill(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    finmind_db: Path = DEFAULT_FINMIND_DB,
    tw_stocks_db: Path = DEFAULT_TW_STOCKS_DB,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
) -> dict[str, Any]:
    _parse_iso_date(start, "start")
    _parse_iso_date(end, "end")
    if start > end:
        raise ValueError("start must not be after end")
    raw_dir = output_dir / "raw"
    all_rows: dict[str, float] = {}
    raw_manifest: list[dict[str, Any]] = []
    for year, month in _month_range(date.fromisoformat(start), date.fromisoformat(end)):
        rows, source_row = _load_or_fetch_month(year, month, raw_dir)
        raw_manifest.append(source_row)
        for row in rows:
            trade_date = row["date"]
            if not start <= trade_date <= end:
                continue
            if trade_date in all_rows:
                raise ValueError(f"duplicate official date across monthly responses: {trade_date}")
            all_rows[trade_date] = row["close"]

    calendar_evidence = _finmind_calendar_evidence(finmind_db, start, end)
    crosscheck = _crosscheck_taiex(tw_stocks_db, all_rows)
    coverage = _coverage(all_rows, calendar_evidence, start, end, raw_manifest, crosscheck)
    fetched_at = datetime.now(timezone.utc).isoformat()
    artifact = {
        "index_kind": "TAIEX_PRICE",
        "source": SOURCE,
        "start": start,
        "end": end,
        "fetched_at": fetched_at,
        "records": [
            {"date": day, "close": all_rows[day]} for day in sorted(all_rows)
        ],
        "coverage": coverage,
    }
    manifest = {
        "source": SOURCE,
        "requested_start": start,
        "requested_end": end,
        "generated_at": fetched_at,
        "files": raw_manifest,
        "aggregate_sha256": coverage["raw_source_fingerprint_sha256"],
    }
    _atomic_write(
        output_dir / "raw_manifest.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    destination = output_dir / "market_prices.json"
    _atomic_write(
        destination,
        (json.dumps(artifact, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    loaded = load_market_prices(destination)
    if loaded != all_rows:
        raise ValueError("written market price artifact failed round-trip validation")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--finmind-db", type=Path, default=DEFAULT_FINMIND_DB)
    parser.add_argument("--tw-stocks-db", type=Path, default=DEFAULT_TW_STOCKS_DB)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    args = parser.parse_args()
    try:
        artifact = backfill(
            args.output_dir, args.finmind_db, args.tw_stocks_db, args.start, args.end
        )
    except (CollectorError, ValueError, OSError, sqlite3.Error) as exc:
        raise SystemExit(f"entry market index backfill failed: {exc}") from exc
    coverage = artifact["coverage"]
    print(
        f"TAIEX_PRICE 完成：{coverage['official_record_count']} 筆，"
        f"{coverage['first_official_date']}..{coverage['last_official_date']}"
    )
    print(
        f"FinMind 參考日曆缺官方價格 "
        f"{coverage['finmind_reference_dates_missing_official_price_count']} 日；"
        f"有效日曆缺官方價格 {coverage['effective_dates_missing_official_price_count']} 日；"
        f"MA60 暖機不足 {coverage['signal_dates_without_60_market_days_count']} 日"
    )
    check = coverage["sqlite_crosscheck"]
    print(
        f"本機 taiex_daily 重疊 {check['overlap_records']} 筆，"
        f"值差異 {check['mismatch_count']} 筆"
    )


if __name__ == "__main__":
    main()
