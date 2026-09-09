"""以官方 bulk 資料建立 TWSE／TPEx 歷史有效資格區間。

未知起日一律不產生區間。尤其 TPEx 終止上櫃清單只有終止日，不能把終止日
以前的行情反推成完整上櫃期間。所有下載原文都保存在輸出目錄，並在 artifact
記錄 SHA-256，讓同一份輸出可以離線重建與稽核。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests


DEFAULT_MANIFEST = Path("data/momentum_pit/manifest.json")
DEFAULT_OUTPUT_DIR = Path("data/momentum_pit/listed_universe")
USER_AGENT = "tw-stock-db listed-universe backfill/1.0"

SOURCES = {
    "TWSE_CURRENT_MASTER": {
        "url": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
        "method": "GET",
        "raw": "raw/twse_current_master.json",
    },
    "TPEX_CURRENT_MASTER": {
        "url": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
        "method": "GET",
        "raw": "raw/tpex_current_master.json",
    },
    "TWSE_LISTING_HISTORY": {
        "url": "https://openapi.twse.com.tw/v1/company/newlisting",
        "method": "GET",
        "raw": "raw/twse_listing_history.json",
    },
    "TWSE_DELISTING_HISTORY": {
        "url": "https://openapi.twse.com.tw/v1/company/suspendListingCsvAndHtml",
        "method": "GET",
        "raw": "raw/twse_delisting_history.json",
    },
    "TPEX_DELISTING_HISTORY": {
        "url": "https://www.tpex.org.tw/www/zh-tw/company/deListed",
        "method": "POST",
        "form": {
            "date": "ALL",
            "response": "json",
            "paging-table": "0",
            "paging-size": "10000",
            "paging-offset": "0",
        },
        "raw": "raw/tpex_delisting_history.json",
    },
}


def parse_official_date(value: Any) -> str:
    """解析官方常見西元／民國日期，回傳嚴格 YYYY-MM-DD。"""
    if value is None:
        raise ValueError("missing official date")
    text = str(value).strip()
    if not text:
        raise ValueError("missing official date")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 7:  # YYYMMDD (ROC)
        year, month, day = int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:])
    elif len(digits) == 8:
        raw_year = int(digits[:4])
        if raw_year < 1912:  # 0YYYMMDD is not a supported official representation
            raise ValueError(f"invalid official date: {value!r}")
        year, month, day = raw_year, int(digits[4:6]), int(digits[6:])
    else:
        parts = [part for part in text.replace("年", "/").replace("月", "/")
                 .replace("日", "").replace(".", "/").replace("-", "/").split("/") if part]
        if len(parts) != 3:
            raise ValueError(f"invalid official date: {value!r}")
        raw_year, month, day = map(int, parts)
        year = raw_year + 1911 if raw_year < 1912 else raw_year
    return date(year, month, day).isoformat()


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _read_json_bytes(body: bytes, source_name: str) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{source_name} did not return UTF-8 JSON") from exc


def fetch_sources(output_dir: Path, *, offline: bool = False,
                  session: requests.Session | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """下載或重讀快取，回傳 parsed payload 與可重現來源 metadata。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    client = session or requests.Session()
    client.headers.update({"User-Agent": USER_AGENT})
    payloads: dict[str, Any] = {}
    metadata: list[dict[str, Any]] = []
    for name, spec in SOURCES.items():
        raw_path = output_dir / spec["raw"]
        meta_path = raw_path.with_suffix(raw_path.suffix + ".meta.json")
        if offline:
            if not raw_path.exists():
                raise FileNotFoundError(f"offline raw cache missing: {raw_path}")
            if not meta_path.exists():
                raise FileNotFoundError(f"offline raw metadata missing: {meta_path}")
            body = raw_path.read_bytes()
            item = json.loads(meta_path.read_text(encoding="utf-8"))
            if item.get("sha256") != _sha256(body) or item.get("bytes") != len(body):
                raise RuntimeError(f"offline raw cache hash mismatch: {raw_path}")
        else:
            kwargs: dict[str, Any] = {"timeout": 60}
            if spec["method"] == "POST":
                kwargs["data"] = spec["form"]
            response = client.request(spec["method"], spec["url"], **kwargs)
            response.raise_for_status()
            body = response.content
            # 先驗證，避免以錯誤頁覆蓋最後一份可用 raw。
            _read_json_bytes(body, name)
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(body)
            item = {
                "id": name,
                "url": spec["url"],
                "method": spec["method"],
                "raw_path": raw_path.relative_to(output_dir).as_posix(),
                "sha256": _sha256(body),
                "bytes": len(body),
                "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            if "form" in spec:
                item["request_form"] = spec["form"]
            meta_path.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        payloads[name] = _read_json_bytes(body, name)
        metadata.append(item)
    return payloads, metadata


def parse_twse_current(payload: Any) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, list):
        raise ValueError("TWSE current master must be a list")
    for item in payload:
        sid = str(item.get("公司代號", "")).strip()
        start = item.get("上市日期")
        if sid and start:
            short_name = str(item.get("公司簡稱", "")).strip()
            rows[sid] = {
                "start": parse_official_date(start),
                "security_type": "TDR" if short_name.endswith("-DR") else "ORDINARY",
                "raw": item,
            }
    return rows


def parse_tpex_current(payload: Any) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, list):
        raise ValueError("TPEx current master must be a list")
    for item in payload:
        sid = str(item.get("SecuritiesCompanyCode", "")).strip()
        start = item.get("DateOfListing")
        if sid and start:
            rows[sid] = {"start": parse_official_date(start), "raw": item}
    return rows


def parse_twse_listing_history(payload: Any) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, list):
        raise ValueError("TWSE listing history must be a list")
    for item in payload:
        sid = str(item.get("Code", "")).strip()
        # OpenAPI schema: ApprovedListingDate = 股票上市買賣日期；ListingDate 是契約核准日。
        start = item.get("ApprovedListingDate")
        if sid and start:
            rows[sid] = {
                "start": parse_official_date(start),
                "note": str(item.get("Note", "")).strip(),
                "raw": item,
            }
    return rows


def parse_twse_delisting_history(payload: Any) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, list):
        raise ValueError("TWSE delisting history must be a list")
    for item in payload:
        sid = str(item.get("Code", "")).strip()
        end = item.get("DelistingDate")
        if sid and end:
            rows[sid] = {"end": parse_official_date(end), "raw": item}
    return rows


def parse_tpex_delisting_history(payload: Any) -> dict[str, dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ValueError("TPEx delisting history must contain data list")
    rows: dict[str, dict[str, Any]] = {}
    for item in data:
        if not isinstance(item, list) or len(item) < 4:
            continue
        sid = str(item[0]).strip()
        if sid and item[2]:
            rows[sid] = {
                "end": parse_official_date(item[2]),
                "reason": str(item[3]).strip(),
                "raw": item,
            }
    return rows


def build_artifact(candidate_ids: list[str], payloads: dict[str, Any],
                   source_metadata: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = {str(sid).strip() for sid in candidate_ids if str(sid).strip()}
    twse_current = parse_twse_current(payloads["TWSE_CURRENT_MASTER"])
    tpex_current = parse_tpex_current(payloads["TPEX_CURRENT_MASTER"])
    twse_listings = parse_twse_listing_history(payloads["TWSE_LISTING_HISTORY"])
    twse_delistings = parse_twse_delisting_history(payloads["TWSE_DELISTING_HISTORY"])
    tpex_delistings = parse_tpex_delisting_history(payloads["TPEX_DELISTING_HISTORY"])

    records: list[dict[str, Any]] = []
    current_ids: set[str] = set()
    for market, rows, source in (
        ("TWSE", twse_current, "TWSE_CURRENT_MASTER"),
        ("TPEX", tpex_current, "TPEX_CURRENT_MASTER"),
    ):
        for sid in sorted(candidates & rows.keys()):
            current_ids.add(sid)
            record: dict[str, Any] = {
                "stock_id": sid,
                "market": market,
                "start": rows[sid]["start"],
                "end": None,
                "source": source,
                "evidence": [{"source": source, "field": "listing_date"}],
            }
            if market == "TWSE" and rows[sid].get("security_type") == "TDR":
                record["security_type"] = "TDR"
            # 櫃轉市只在「官方 TWSE 備註 + 官方 TPEx 終止日 + 日期銜接」三者一致時標記。
            listing = twse_listings.get(sid)
            prior = tpex_delistings.get(sid)
            if (market == "TWSE" and listing and prior and "櫃轉市" in listing["note"]
                    and listing["start"] == record["start"] == prior["end"]):
                record["transition_from"] = "TPEX"
                record["prior_market_end"] = prior["end"]
                record["transition_evidence"] = ["TWSE_LISTING_HISTORY", "TPEX_DELISTING_HISTORY"]
            records.append(record)

    # 非現存股票只有「官方 TWSE 掛牌日 + 官方 TWSE 終止日」兩端都存在才產生區間。
    historical_twse_ids = (candidates - current_ids) & twse_listings.keys() & twse_delistings.keys()
    for sid in sorted(historical_twse_ids):
        start, end = twse_listings[sid]["start"], twse_delistings[sid]["end"]
        if end <= start:
            continue
        records.append({
            "stock_id": sid,
            "market": "TWSE",
            "start": start,
            "end": end,
            "source": "TWSE_LISTING_HISTORY+TWSE_DELISTING_HISTORY",
            "evidence": [
                {"source": "TWSE_LISTING_HISTORY", "field": "ApprovedListingDate"},
                {"source": "TWSE_DELISTING_HISTORY", "field": "DelistingDate"},
            ],
        })

    records.sort(key=lambda row: (row["stock_id"], row["start"], row["market"]))
    evidenced_ids = {row["stock_id"] for row in records}
    unknown_ids = sorted(candidates - evidenced_ids)
    terminal_candidates = (candidates - current_ids) & (twse_delistings.keys() | tpex_delistings.keys())
    transfer_records = [row for row in records if row.get("transition_from") == "TPEX"]
    tdr_records = [row for row in records if row.get("security_type") == "TDR"]

    return {
        "universe_kind": "TWSE_TPEX_EFFECTIVE_INTERVALS",
        "records": records,
        "coverage": {
            "candidate_count": len(candidates),
            "evidenced_candidate_count": len(evidenced_ids),
            "unknown_candidate_count": len(unknown_ids),
            "unknown_candidate_ids": unknown_ids,
            "record_count": len(records),
            "current_twse_count": len(candidates & twse_current.keys()),
            "current_tpex_count": len(candidates & tpex_current.keys()),
            "historical_twse_complete_interval_count": len(historical_twse_ids),
            "official_terminal_candidate_count": len(terminal_candidates),
            "official_terminal_evidenced_count": len(terminal_candidates & evidenced_ids),
            "verified_tpex_to_twse_transition_count": len(transfer_records),
            "verified_tpex_to_twse_stock_ids": [row["stock_id"] for row in transfer_records],
            "confirmed_transfer_with_unknown_prior_start_count": len(transfer_records),
            "confirmed_transfer_with_unknown_prior_start_stock_ids": [
                row["stock_id"] for row in transfer_records
            ],
            "prior_tpex_complete_interval_count": 0,
            "twse_tdr_count": len(tdr_records),
            "twse_tdr_stock_ids": [row["stock_id"] for row in tdr_records],
        },
        "sources": source_metadata,
        "limitations": [
            "TWSE listing-history bulk endpoint starts at 2001; older historical listing starts remain unknown.",
            "TPEx delisting bulk endpoint supplies the exclusive end date but not the original OTC listing date; no prior TPEx interval is emitted without that start evidence.",
            "Verified TPEx-to-TWSE transitions retain the official TPEx end and TWSE start on the TWSE record, but the earlier TPEx period remains excluded when its start is unknown.",
            "Unknown candidates are rejected rather than backfilled from FinMind TaiwanStockInfo.date or first available price.",
            "Coverage is conservative and can bias historical tests toward surviving/current issuers; this artifact does not claim complete PIT membership.",
            "The requested scope is market eligibility, so four officially listed TWSE TDRs in the manifest remain included and are explicitly classified; ETFs and warrants are absent from the company master endpoints.",
        ],
    }


def run(manifest_path: Path = DEFAULT_MANIFEST, output_dir: Path = DEFAULT_OUTPUT_DIR,
        *, offline: bool = False,
        session: requests.Session | None = None) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidates = manifest.get("candidate_ids")
    if not isinstance(candidates, list):
        raise ValueError("manifest candidate_ids must be a list")
    payloads, metadata = fetch_sources(output_dir, offline=offline, session=session)
    artifact = build_artifact(candidates, payloads, metadata)
    output_path = output_dir / "intervals.json"
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description="回補具官方證據的 TWSE／TPEx 歷史資格區間")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--offline", action="store_true", help="只用 output-dir 既有 raw 快取重建")
    args = parser.parse_args()
    artifact = run(args.manifest, args.output_dir, offline=args.offline)
    coverage = artifact["coverage"]
    print(
        f"完成：{coverage['evidenced_candidate_count']}/{coverage['candidate_count']} 檔有官方區間，"
        f"未知 {coverage['unknown_candidate_count']} 檔，records={coverage['record_count']}"
    )
    print(
        f"終止資格候選涵蓋 {coverage['official_terminal_evidenced_count']}/"
        f"{coverage['official_terminal_candidate_count']}；"
        f"核實櫃轉市 {coverage['verified_tpex_to_twse_transition_count']} 檔"
    )


if __name__ == "__main__":
    main()
