"""Build auditable TWSE/TPEx/emerging-market eligibility intervals from TPEx data.

The registration date is inclusive.  The date stated in a TPEx termination
announcement as the date from which trading is terminated is exclusive.
Unknown terminal dates fail closed; price availability is never membership
evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


DEFAULT_LISTED = Path("data/momentum_pit/listed_universe/intervals.json")
DEFAULT_OUTPUT_DIR = Path("data/momentum_pit/emerging_universe")
FIRST_YEAR = 2002
USER_AGENT = "tw-stock-db emerging-universe backfill/1.0"
REGISTRATION_URL = (
    "https://www.tpex.org.tw/www/zh-tw/company/latestEmerge"
    "?date={year}&response=json&paging-table=0&paging-size=10000&paging-offset=0"
)
CURRENT_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_R"
TERMINATION_URL = (
    "https://www.tpex.org.tw/www/zh-tw/bulletin/announcement"
    "?startDate={year}%2F01%2F01&endDate={year}%2F12%2F31"
    "&cate=4&txtKeyword=%E8%88%88%E6%AB%83&receiver=9"
    "&response=json&paging-table=0&paging-size=10000&paging-offset=0"
)
REVOCATION_URL = (
    "https://www.tpex.org.tw/www/zh-tw/bulletin/announcement"
    "?startDate={year}%2F01%2F01&endDate={year}%2F12%2F31"
    "&cate=4&txtKeyword=%E5%BB%A2%E6%AD%A2&receiver=9"
    "&response=json&paging-table=0&paging-size=10000&paging-offset=0"
)


def parse_official_date(value: Any) -> str:
    text = str(value).strip()
    parts = text.replace("年", "/").replace("月", "/").replace("日", "").split("/")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"invalid official date: {value!r}")
    year, month, day = map(int, parts)
    if year < 1912:
        year += 1911
    return date(year, month, day).isoformat()


def parse_registrations(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, dict) or payload.get("stat") != "ok":
        raise ValueError("registration payload is not an ok TPEx response")
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        raise ValueError("registration payload has no table")
    table = tables[0]
    fields = table.get("fields", [])
    required = ("股票代號", "公司名稱", "登錄日期")
    if not all(field in fields for field in required):
        raise ValueError("registration table has an unexpected schema")
    indexes = {field: fields.index(field) for field in required}
    return [
        {
            "stock_id": str(row[indexes["股票代號"]]).strip(),
            "name": str(row[indexes["公司名稱"]]).strip(),
            "start": parse_official_date(row[indexes["登錄日期"]]),
        }
        for row in table.get("data", [])
    ]


def parse_current_master(payload: Any) -> dict[str, dict[str, str]]:
    if not isinstance(payload, list):
        raise ValueError("current emerging master must be a list")
    result: dict[str, dict[str, str]] = {}
    for row in payload:
        sid = str(row.get("SecuritiesCompanyCode", "")).strip()
        start = row.get("DateOfListing")
        if not sid or not start:
            continue
        result[sid] = {
            "start": _parse_compact_date(start),
            "name": str(row.get("CompanyAbbreviation", "")).strip(),
        }
    return result


def _parse_compact_date(value: Any) -> str:
    text = str(value).strip()
    if "/" in text or "年" in text:
        return parse_official_date(text)
    if not text.isdigit() or len(text) not in (7, 8):
        raise ValueError(f"invalid official date: {value!r}")
    if len(text) == 7:
        return parse_official_date(f"{text[:3]}/{text[3:5]}/{text[5:]}")
    return parse_official_date(f"{text[:4]}/{text[4:6]}/{text[6:]}")


_EFFECTIVE_DATE_RE = re.compile(r"(?P<year>\d{2,3})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日起")
_STOCK_CODE_RE = re.compile(r"(?:股票|證券)代號\s*[：:]\s*(?P<code>[0-9A-Z]{4,8})")


def parse_terminations(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, dict) or payload.get("stat") != "ok":
        raise ValueError("termination payload is not an ok TPEx response")
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        raise ValueError("termination payload has no table")
    table = tables[0]
    fields = table.get("fields", [])
    required = ("資料日期", "發文字號", "主旨", "詳細資料")
    if not all(field in fields for field in required):
        # A valid empty response uses the same table envelope without fields.
        if not table.get("data"):
            return []
        raise ValueError("termination table has an unexpected schema")
    indexes = {field: fields.index(field) for field in required}
    result: list[dict[str, str]] = []
    for row in table.get("data", []):
        subject = str(row[indexes["主旨"]]).strip()
        if "終止" not in subject or "興櫃股票" not in subject:
            continue
        effective = _EFFECTIVE_DATE_RE.search(subject)
        code = _STOCK_CODE_RE.search(subject)
        if not effective or not code:
            raise ValueError(f"unparseable TPEx termination announcement: {subject}")
        end = parse_official_date(
            f"{effective.group('year')}/{effective.group('month')}/{effective.group('day')}"
        )
        result.append({
            "stock_id": code.group("code"),
            "end": end,
            "announcement_date": parse_official_date(row[indexes["資料日期"]]),
            "document_number": str(row[indexes["發文字號"]]).strip(),
            "detail": str(row[indexes["詳細資料"]]).strip(),
        })
    return result


_REVOKED_DOCUMENT_RE = re.compile(r"(?P<document>證櫃審字第\d+號)")


def parse_revocations(payload: Any) -> list[dict[str, str]]:
    """Return official revocations of earlier emerging termination notices."""
    if not isinstance(payload, dict) or payload.get("stat") != "ok":
        raise ValueError("revocation payload is not an ok TPEx response")
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        raise ValueError("revocation payload has no table")
    table = tables[0]
    fields = table.get("fields", [])
    required = ("資料日期", "發文字號", "主旨", "詳細資料")
    if not all(field in fields for field in required):
        if not table.get("data"):
            return []
        raise ValueError("revocation table has an unexpected schema")
    indexes = {field: fields.index(field) for field in required}
    result: list[dict[str, str]] = []
    for row in table.get("data", []):
        subject = str(row[indexes["主旨"]]).strip()
        if "廢止" not in subject or "終止" not in subject:
            continue
        code = _STOCK_CODE_RE.search(subject)
        prior_documents = _REVOKED_DOCUMENT_RE.findall(subject)
        if not code or not prior_documents:
            raise ValueError(f"unparseable TPEx termination revocation: {subject}")
        result.append({
            "stock_id": code.group("code"),
            "revoked_document_number": prior_documents[-1],
            "announcement_date": parse_official_date(row[indexes["資料日期"]]),
            "document_number": str(row[indexes["發文字號"]]).strip(),
            "detail": str(row[indexes["詳細資料"]]).strip(),
        })
    return result


def build_artifact(
    listed_payload: dict[str, Any],
    registrations: list[dict[str, str]],
    terminations: list[dict[str, str]],
    current: dict[str, dict[str, str]],
    sources: list[dict[str, Any]],
    revocations: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    listed_records = listed_payload.get("records")
    if not isinstance(listed_records, list):
        raise ValueError("listed universe has no records list")

    revocations = revocations or []
    revoked_documents = {
        row["revoked_document_number"] for row in revocations
    }
    effective_terminations = [
        row for row in terminations if row["document_number"] not in revoked_documents
    ]

    # Current master supplies an independently verified start for current names,
    # including a scheduled/current row absent from the annual archive.
    events: dict[tuple[str, str], dict[str, str]] = {}
    for row in registrations:
        events[(row["stock_id"], row["start"])] = dict(row)
    registration_keys = set(events)
    for sid, row in current.items():
        events.setdefault((sid, row["start"]), {
            "stock_id": sid, "name": row.get("name", ""), "start": row["start"]
        })

    starts_by_sid: dict[str, list[dict[str, str]]] = {}
    for row in events.values():
        starts_by_sid.setdefault(row["stock_id"], []).append(row)
    ends_by_sid: dict[str, list[dict[str, str]]] = {}
    for row in effective_terminations:
        ends_by_sid.setdefault(row["stock_id"], []).append(row)
    listed_starts: dict[str, list[str]] = {}
    for row in listed_records:
        listed_starts.setdefault(str(row["stock_id"]), []).append(str(row["start"]))

    emerging_records: list[dict[str, Any]] = []
    unknown_end_ids: set[str] = set()
    unknown_end_cycles: list[dict[str, str | None]] = []
    contradictions: list[dict[str, str]] = []
    consumed_end_events: set[tuple[str, str, str]] = set()
    for sid in sorted(starts_by_sid):
        cycles = sorted(starts_by_sid[sid], key=lambda row: row["start"])
        for index, cycle in enumerate(cycles):
            start = cycle["start"]
            next_start = cycles[index + 1]["start"] if index + 1 < len(cycles) else None
            listed_overlap = [
                listed_start for listed_start in listed_starts.get(sid, [])
                if listed_start <= start
            ]
            if listed_overlap:
                contradictions.append({"stock_id": sid, "start": start, "reason": "listed_overlap"})
                continue

            possible_ends: list[tuple[str, str, dict[str, str] | None]] = []
            for end_event in ends_by_sid.get(sid, []):
                end = end_event["end"]
                if end > start and (next_start is None or end <= next_start):
                    possible_ends.append((end, "termination", end_event))
            for listed_start in listed_starts.get(sid, []):
                if listed_start > start and (next_start is None or listed_start <= next_start):
                    possible_ends.append((listed_start, "listed_start", None))
            possible_ends.sort(key=lambda item: item[0])

            end: str | None
            end_kind: str | None
            end_event: dict[str, str] | None
            if possible_ends:
                end, end_kind, end_event = possible_ends[0]
            elif sid in current and current[sid]["start"] == start and index == len(cycles) - 1:
                end, end_kind, end_event = None, "current_master", None
            else:
                unknown_end_ids.add(sid)
                unknown_end_cycles.append({
                    "stock_id": sid,
                    "start": start,
                    "next_registration_start": next_start,
                })
                continue

            start_source = (
                "TPEX_EMERGING_REGISTRATION_HISTORY"
                if (sid, start) in registration_keys else "TPEX_EMERGING_CURRENT_MASTER"
            )
            evidence: list[dict[str, str]] = [{
                "source": start_source,
                "field": "登錄日期" if (sid, start) in registration_keys else "DateOfListing",
            }]
            if end_kind == "termination" and end_event is not None:
                evidence.append({
                    "source": "TPEX_EMERGING_TERMINATION_ANNOUNCEMENTS",
                    "field": "主旨中的終止生效日",
                    "document_number": end_event["document_number"],
                    "announcement_date": end_event["announcement_date"],
                    "detail": end_event["detail"],
                })
                consumed_end_events.add((sid, end_event["end"], end_event["document_number"]))
            elif end_kind == "listed_start":
                evidence.append({"source": "LISTED_UNIVERSE", "field": "start"})
            else:
                evidence.append({"source": "TPEX_EMERGING_CURRENT_MASTER", "field": "active_snapshot"})
            source = "+".join(dict.fromkeys(item["source"] for item in evidence))
            emerging_records.append({
                "stock_id": sid,
                "market": "EMERGING",
                "start": start,
                "end": end,
                "source": source,
                "evidence": evidence,
            })

    emerging_records.sort(key=lambda row: (row["stock_id"], row["start"]))
    relevant = sorted({
        row["stock_id"] for row in emerging_records
        if row["end"] is None or row["end"] > "2018-12-01"
    })
    unknown_relevant = sorted({
        row["stock_id"] for row in unknown_end_cycles
        if row["start"] >= "2018-12-01"
    })
    unmatched_terminations = [
        row for row in effective_terminations
        if (row["stock_id"], row["end"], row["document_number"])
        not in consumed_end_events
    ]
    all_sources = list(listed_payload.get("sources", [])) + sources
    return {
        "universe_kind": "TWSE_TPEX_EMERGING_EFFECTIVE_INTERVALS",
        "records": list(listed_records) + emerging_records,
        "coverage": {
            "listed_record_count": len(listed_records),
            "registration_event_count": len(registration_keys),
            "registration_stock_count": len({sid for sid, _ in registration_keys}),
            "current_master_count": len(current),
            "termination_event_count": len(terminations),
            "revoked_termination_event_count": len(terminations) - len(effective_terminations),
            "termination_revocation_count": len(revocations),
            "emerging_interval_count": len(emerging_records),
            "active_emerging_interval_count": sum(row["end"] is None for row in emerging_records),
            "open_ended_emerging_interval_count": sum(row["end"] is None for row in emerging_records),
            "current_snapshot_verified_count": len(current),
            "terminated_emerging_interval_count": sum(row["end"] is not None for row in emerging_records),
            "unknown_end_cycle_count": len(unknown_end_cycles),
            "unknown_end_stock_ids": sorted(unknown_end_ids),
            "unknown_end_cycles": unknown_end_cycles,
            "unknown_end_relevant_stock_ids": unknown_relevant,
            "current_only_start_count": len(set(events) - registration_keys),
            "unmatched_termination_event_count": len(unmatched_terminations),
            "unmatched_termination_events": unmatched_terminations,
            "contradiction_count": len(contradictions),
            "contradictions": contradictions,
            "relevant_after_2018_12_01_count": len(relevant),
            "relevant_after_2018_12_01_stock_ids": relevant,
        },
        "sources": all_sources,
        "limitations": [
            "TPEx annual registration history is published from 2002-01-01; no earlier emerging interval is inferred.",
            "The registration archive includes scheduled future starts; only the current master can prove an open-ended active interval.",
            "Termination announcement dates are not interval ends; the stated effective termination date is the exclusive end.",
            "Registration cycles without a termination, a later listed-market start, or current-master active evidence are omitted.",
            "The current master is a retrieval-time snapshot, so historical intervals remain conservative rather than full point-in-time certification.",
            "No first/last price date or third-party current-status date is used as market-membership evidence.",
        ],
    }


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _raw_meta_path(raw_path: Path) -> Path:
    return raw_path.with_suffix(".meta.json")


def _load_cached(raw_path: Path) -> tuple[Any, dict[str, Any]]:
    meta_path = _raw_meta_path(raw_path)
    if not raw_path.exists() or not meta_path.exists():
        raise FileNotFoundError(f"missing offline source cache: {raw_path}")
    body = raw_path.read_bytes()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("sha256") != _sha256(body):
        raise RuntimeError(f"source hash mismatch: {raw_path}")
    return json.loads(body.decode("utf-8")), meta


def _fetch_json(session: requests.Session, url: str, raw_path: Path) -> tuple[Any, dict[str, Any]]:
    response = session.get(url, timeout=60)
    response.raise_for_status()
    body = response.content
    payload = json.loads(body.decode("utf-8"))
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(body)
    meta = {
        "url": url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "sha256": _sha256(body),
        "bytes": len(body),
        "raw_path": raw_path.as_posix(),
    }
    _raw_meta_path(raw_path).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload, meta


def run(
    listed_path: Path = DEFAULT_LISTED,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    offline: bool = False,
    end_year: int | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    end_year = end_year or date.today().year
    raw_dir = output_dir / "raw"
    client = session or requests.Session()
    client.headers.update({"User-Agent": USER_AGENT})
    emerging_sources: list[dict[str, Any]] = []

    def acquire(url: str, filename: str) -> Any:
        raw_path = raw_dir / filename
        payload, meta = _load_cached(raw_path) if offline else _fetch_json(client, url, raw_path)
        emerging_sources.append(meta)
        return payload

    registrations: list[dict[str, str]] = []
    terminations: list[dict[str, str]] = []
    revocations: list[dict[str, str]] = []
    for year in range(FIRST_YEAR, end_year + 1):
        registrations.extend(parse_registrations(acquire(
            REGISTRATION_URL.format(year=year), f"registrations_{year}.json"
        )))
        terminations.extend(parse_terminations(acquire(
            TERMINATION_URL.format(year=year), f"terminations_{year}.json"
        )))
        revocations.extend(parse_revocations(acquire(
            REVOCATION_URL.format(year=year), f"termination_revocations_{year}.json"
        )))
    current = parse_current_master(acquire(CURRENT_URL, "current_master.json"))
    listed_payload = json.loads(listed_path.read_text(encoding="utf-8"))
    artifact = build_artifact(
        listed_payload, registrations, terminations, current, emerging_sources, revocations
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "intervals.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description="建立含興櫃的官方歷史市場資格區間")
    parser.add_argument("--listed", type=Path, default=DEFAULT_LISTED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--end-year", type=int)
    args = parser.parse_args()
    artifact = run(args.listed, args.output_dir, offline=args.offline, end_year=args.end_year)
    coverage = artifact["coverage"]
    print(
        f"完成：興櫃區間 {coverage['emerging_interval_count']}，"
        f"目前有效 {coverage['active_emerging_interval_count']}，"
        f"未知終止而排除 {coverage['unknown_end_cycle_count']} 個週期"
    )
    print(
        f"2018-12-01 後相關股票 {coverage['relevant_after_2018_12_01_count']} 檔"
    )


if __name__ == "__main__":
    main()
