"""FinMind 歷史動能研究資料下載；原始快取可續傳，下載完成不代表回測已通過驗收。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
API = "https://api.finmindtrade.com/api/v4/data"
DATASETS = ("TaiwanStockPrice", "TaiwanStockDividendResult",
            "TaiwanStockDividend", "TaiwanStockCapitalReductionReferencePrice")


def canonical(query):
    return json.dumps(query, sort_keys=True, separators=(",", ":"))


def token_from_env():
    token = os.environ.get("FINMIND_TOKEN", "")
    if not token and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "FINMIND_TOKEN":
                token = value.strip().strip('"').strip("'")
                break
    return token


def candidate_ids(info, delist):
    # 這只是下載候選，不宣稱更新日期就是歷史上市日。
    ids = {str(r["stock_id"]) for r in info if r.get("type") in ("twse", "tpex")}
    ids.update(str(r["stock_id"]) for r in delist)
    return sorted(s for s in ids if len(s) == 4 and s.isascii()
                  and s.isdigit() and not s.startswith("0"))


def init_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS responses (
            query TEXT PRIMARY KEY, retrieved_at TEXT NOT NULL,
            http_status INTEGER NOT NULL, status INTEGER NOT NULL,
            body TEXT NOT NULL, sha256 TEXT NOT NULL, row_count INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (started REAL NOT NULL);
    """)
    return conn


class FetchStopped(RuntimeError):
    pass


class Client:
    def __init__(self, conn, token, max_requests):
        self.conn, self.token = conn, token
        self.limit, self.used = max_requests, 0
        # 保留同一帳戶其他專案的餘裕；402/403/429 一律停，不繞過限流。
        self.interval = 7.0 if token else 13.0

    def cached(self, query):
        row = self.conn.execute("SELECT body FROM responses WHERE query=? AND status=200",
                                (canonical(query),)).fetchone()
        return json.loads(row[0])["data"] if row else None

    def fetch(self, query):
        rows = self.cached(query)
        if rows is not None:
            return rows
        if self.used >= self.limit:
            raise FetchStopped("request_budget_exhausted; rerun to resume")
        last = self.conn.execute("SELECT MAX(started) FROM attempts").fetchone()[0]
        if last:
            time.sleep(max(0, last + self.interval - time.time()))
        self.conn.execute("INSERT INTO attempts VALUES (?)", (time.time(),))
        self.conn.commit()
        self.used += 1
        headers = {"User-Agent": "tw-stock-db historical-research"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(API + "?" + urllib.parse.urlencode(query), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                http_status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            http_status, raw = exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # 不列印 request/header 或可能含憑證的例外內容。
            raise FetchStopped("network_error: " + type(exc).__name__) from None
        try:
            body = raw.decode("utf-8-sig")
            payload = json.loads(body)
            status = int(payload.get("status", http_status))
        except (ValueError, UnicodeError, AttributeError):
            raise FetchStopped("invalid_json_response") from None
        if status == 200 and (http_status != 200 or not isinstance(payload.get("data"), list)):
            status = -1
        row_count = len(payload["data"]) if isinstance(payload.get("data"), list) else 0
        self.conn.execute("INSERT OR REPLACE INTO responses VALUES (?,?,?,?,?,?,?)", (
            canonical(query), datetime.now(timezone.utc).isoformat(), http_status,
            status, body, hashlib.sha256(raw).hexdigest(), row_count))
        self.conn.commit()
        print(json.dumps({"dataset": query["dataset"], "stock_id": query.get("data_id"),
                          "status": status, "rows": row_count,
                          "requests_this_run": self.used}), flush=True)
        if status != 200:
            raise FetchStopped(f"api_status_{status}; cached failure is NOT complete")
        return payload["data"]


def query_for(dataset, start, end, sid=None):
    q = {"dataset": dataset, "start_date": start, "end_date": end}
    if sid is not None:
        q["data_id"] = sid
    return q


def write_status(conn, directory, manifest, state):
    done = dict(conn.execute("SELECT query,row_count FROM responses WHERE status=200"))
    counts = {dataset: 0 for dataset in DATASETS}
    empty = {dataset: 0 for dataset in DATASETS}
    if manifest:
        for dataset in DATASETS:
            for sid in manifest["candidate_ids"]:
                key = canonical(query_for(dataset, manifest["start"], manifest["end"], sid))
                if key in done:
                    counts[dataset] += 1
                    empty[dataset] += done[key] == 0
    result = {"updated_at": datetime.now(timezone.utc).isoformat(), "state": state,
              "certification": "NOT_CERTIFIED", "candidate_count": len(manifest["candidate_ids"]) if manifest else 0,
              "successful_requests": counts, "empty_responses": empty,
              "note": "Download completeness is not PIT universe, execution or cashflow certification."}
    temp = directory / "status.tmp"
    temp.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temp.replace(directory / "status.json")
    return result


def run(args):
    directory = args.out.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # 作業系統鎖隨程序退出釋放，避免多個 writer 共用額度／manifest。
    import msvcrt
    with (directory / "worker.lock").open("a+b") as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit("Another momentum download worker is active") from None
        conn = init_db(directory / "finmind_raw.db")
        client = Client(conn, token_from_env(), args.max_requests)
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
        state = "starting"
        try:
            if manifest and (manifest["start"], manifest["end"]) != (args.start, args.end):
                raise FetchStopped("manifest date mismatch: use a different --out directory")
            if not manifest:
                info = client.fetch({"dataset": "TaiwanStockInfo"})
                delist = client.fetch(query_for("TaiwanStockDelisting", args.start, args.end))
                ids = candidate_ids(info, delist)
                if not ids:
                    raise FetchStopped("empty_candidate_universe")
                manifest = {"start": args.start, "end": args.end,
                            "candidate_ids": ids, "created_at": datetime.now(timezone.utc).isoformat(),
                            "universe_certified": False, "source": "FinMind Info union Delisting"}
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            for dataset in ("TaiwanStockSplitPrice", "TaiwanStockParValueChange"):
                client.fetch(query_for(dataset, args.start, args.end))
            client.fetch({"dataset": "TaiwanStockTradingDate"})
            for index in ("TAIEX", "TPEx"):
                client.fetch(query_for("TaiwanStockTotalReturnIndex", args.start, args.end, index))
            # 先驗證每一必要端點，避免行情抓幾小時後才發現事件端點沒有權限。
            for dataset in DATASETS:
                client.fetch(query_for(dataset, args.start, args.end, manifest["candidate_ids"][0]))
            # 先抓全候選行情，避免先把今日熱門股票抓完形成偏誤子池。
            for dataset in DATASETS:
                for i, sid in enumerate(manifest["candidate_ids"]):
                    if (directory / "STOP").exists():
                        raise FetchStopped("user_stop_file")
                    client.fetch(query_for(dataset, args.start, args.end, sid))
                    if i % 25 == 0:
                        write_status(conn, directory, manifest, "downloading:" + dataset)
            state = "download_complete_pending_validation"
        except FetchStopped as exc:
            state = str(exc)
        finally:
            result = write_status(conn, directory, manifest, state)
            conn.close()
            print(json.dumps(result), flush=True)
    return 0 if state == "download_complete_pending_validation" else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2018-12-01")
    parser.add_argument("--end", default="2026-09-07")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "momentum_pit")
    parser.add_argument("--max-requests", type=int, default=10000)
    raise SystemExit(run(parser.parse_args()))
