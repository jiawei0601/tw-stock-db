"""每日刷新腳本 —— 依序嚴格串行執行完整更新鏈（SQLite 單寫入者，不可併發跑）。

執行順序（依賴順序，見 AGENTS.md build/run 段落）：
    build_institutional_summary.py -> build_daily_prices.py -> build_taiex.py
    -> build_revenue_history.py -> build_fundamentals.py -> build_sector_flow.py
    -> build_sector_flow_weekly.py -> build_sector_flow_value.py -> build_group_flow.py
    -> export_sector_flow_animation.py -> export_dashboard.py

每一步用 `subprocess.run([sys.executable, script], cwd=repo根)` 執行，開始/結束/耗時/
成功失敗記錄到 `data/refresh.log`（append；超過 5MB 會砍掉前半只保留後半）。

**單步失敗不中止**：記錄後繼續跑後續步驟（增量抓取失敗明天會自動補上，彙總/匯出用現有
資料跑完即可）。全部跑完後若有任何失敗，透過 `C:\\CLAUDE\\tools\\telegram\\notify.py`
的 `send()` 發一則失敗摘要通知（比照 tw-momentum-scanner 的 notifier/telegram.py 用法：
sys.path 加入該目錄後 import）；Telegram 發送失敗一律吞掉、不可讓 refresh 當掉。全部成功
時安靜結束、不發通知。

`--dry-run`：只列印步驟清單，不執行、不寫 log。

Exit code：全部成功 = 0；有任何步驟失敗 = 1（供排程系統判斷失敗狀態）。
排程註冊本身不在本腳本範圍內，見 HANDOFF.md 第十二輪紀錄（建議週一至五 18:30）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
LOG_PATH = REPO_ROOT / "data" / "refresh.log"
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5MB
TELEGRAM_NOTIFY_DIR = Path(r"C:\CLAUDE\tools\telegram")

STEPS = [
    "build_institutional_summary.py",
    "build_daily_prices.py",
    "build_taiex.py",
    "build_revenue_history.py",
    "build_fundamentals.py",
    "build_sector_flow.py",
    "build_sector_flow_weekly.py",
    "build_sector_flow_value.py",
    "build_group_flow.py",
    "export_sector_flow_animation.py",
    "export_dashboard.py",
]


def rotate_log(path: Path, max_bytes: int = LOG_MAX_BYTES) -> None:
    """log 檔超過 max_bytes 時，砍掉前半只保留後半（避免無限增長）。

    不存在或未超過門檻時不動作。截斷點盡量對齊到換行後（避免從行中間切斷），
    若找不到換行（例如單行超大）則保底直接留最後 max_bytes 位元組。
    """
    if not path.exists():
        return
    data = path.read_bytes()
    if len(data) <= max_bytes:
        return
    cut = len(data) // 2
    nl = data.find(b"\n", cut)
    if nl == -1:
        cut = max(0, len(data) - max_bytes)
    else:
        cut = nl + 1
    path.write_bytes(data[cut:])


def _log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(msg.rstrip("\n") + "\n")


def run_step(script: str, repo_root: Path = REPO_ROOT) -> tuple[bool, float]:
    """執行單一步驟（子行程），記錄開始/結束/耗時/成功失敗到 log。回傳 (成功與否, 耗時秒數)。"""
    script_path = repo_root / script
    start = time.monotonic()
    _log(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] START {script}")

    result = None
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        success = result.returncode == 0
    except Exception as e:  # noqa: BLE001 - 單步任何例外都不可中止整條刷新鏈
        success = False
        _log(f"  EXCEPTION: {e}")

    elapsed = time.monotonic() - start
    status = "OK" if success else "FAIL"
    _log(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] END {script} status={status} elapsed={elapsed:.1f}s")

    if result is not None and not success:
        tail_out = (result.stdout or "").strip().splitlines()[-20:]
        tail_err = (result.stderr or "").strip().splitlines()[-20:]
        if tail_out:
            _log("  stdout(tail): " + " | ".join(tail_out))
        if tail_err:
            _log("  stderr(tail): " + " | ".join(tail_err))

    return success, elapsed


def send_failure_notification(failed_steps: list[str]) -> None:
    """失敗摘要透過 Telegram 通知。任何例外一律吞掉，通知失敗不可讓 refresh 當掉。"""
    try:
        sys.path.insert(0, str(TELEGRAM_NOTIFY_DIR))
        import notify  # type: ignore

        cfg = notify.load_env()
        token = cfg.get("TELEGRAM_BOT_TOKEN")
        chat_id = cfg.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            print("[refresh_daily] 警告：找不到 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID，略過推播")
            return
        text = (
            f"[tw-stock-db] 每日刷新完成，{len(failed_steps)} / {len(STEPS)} 步驟失敗：\n"
            + "\n".join(f"- {s}" for s in failed_steps)
            + "\n詳見 data/refresh.log"
        )
        notify.send(token, chat_id, text)
    except Exception as e:  # noqa: BLE001 - 通知失敗不可擋 refresh
        print(f"[refresh_daily] 警告：Telegram 通知失敗，不擋 refresh：{e}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="tw-stock-db 每日刷新腳本（依序執行完整更新鏈）")
    parser.add_argument("--dry-run", action="store_true", help="只列印步驟清單，不執行")
    args = parser.parse_args(argv)

    if args.dry_run:
        print(f"每日刷新腳本步驟（依序執行，共 {len(STEPS)} 步）：")
        for i, step in enumerate(STEPS, 1):
            print(f"  {i}. {step}")
        return 0

    rotate_log(LOG_PATH)
    _log(f"===== refresh_daily 開始 {datetime.now():%Y-%m-%d %H:%M:%S} =====")

    failed_steps: list[str] = []
    for step in STEPS:
        success, _elapsed = run_step(step)
        if not success:
            failed_steps.append(step)

    _log(
        f"===== refresh_daily 結束 {datetime.now():%Y-%m-%d %H:%M:%S}，"
        f"共 {len(STEPS)} 步，失敗 {len(failed_steps)} 步 ====="
    )

    if failed_steps:
        send_failure_notification(failed_steps)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
