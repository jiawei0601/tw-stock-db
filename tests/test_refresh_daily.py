"""refresh_daily.py 的測試：只測邏輯（dry-run 輸出、log 輪替、單步失敗不中斷），
用 mock 取代 subprocess.run，**不真的執行任何 build/export 腳本**。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import refresh_daily


def test_dry_run_lists_all_eleven_steps_in_order(capsys):
    rc = refresh_daily.main(["--dry-run"])
    out = capsys.readouterr().out

    assert rc == 0
    expected = [
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
    assert expected == refresh_daily.STEPS
    assert len(refresh_daily.STEPS) == 11

    # 輸出裡的出現順序須與 STEPS 順序一致
    positions = [out.index(step) for step in expected]
    assert positions == sorted(positions)
    for step in expected:
        assert step in out


def test_dry_run_does_not_touch_subprocess(monkeypatch):
    """--dry-run 不應呼叫 subprocess.run（不執行任何步驟）。"""
    called = []
    monkeypatch.setattr(
        refresh_daily.subprocess, "run", lambda *a, **k: called.append((a, k))
    )
    rc = refresh_daily.main(["--dry-run"])
    assert rc == 0
    assert called == []


# ---------------------------------------------------------------------------
# log 輪替


def test_rotate_log_noop_when_under_limit(tmp_path):
    log_path = tmp_path / "refresh.log"
    log_path.write_text("hello\nworld\n", encoding="utf-8")
    before = log_path.read_bytes()

    refresh_daily.rotate_log(log_path, max_bytes=1000)

    assert log_path.read_bytes() == before


def test_rotate_log_noop_when_missing(tmp_path):
    log_path = tmp_path / "does_not_exist.log"
    refresh_daily.rotate_log(log_path, max_bytes=1000)  # 不應丟例外
    assert not log_path.exists()


def test_rotate_log_truncates_keeping_second_half(tmp_path):
    log_path = tmp_path / "refresh.log"
    lines = [f"line-{i:04d}\n" for i in range(1000)]
    content = "".join(lines)
    log_path.write_bytes(content.encode("utf-8"))
    original_size = log_path.stat().st_size
    max_bytes = original_size // 4  # 遠低於原始大小，強制觸發輪替

    refresh_daily.rotate_log(log_path, max_bytes=max_bytes)

    result = log_path.read_bytes()
    assert len(result) < original_size
    # 保留的是「後半」內容：最後一行仍在，最早的幾行已被砍掉
    assert b"line-0999\n" in result
    assert b"line-0000\n" not in result
    # 截斷點對齊到換行後（不從行中間切斷）
    text = result.decode("utf-8")
    assert text == "" or text.startswith("line-")


def test_rotate_log_idempotent_when_no_newline_found(tmp_path):
    """單行超大、找不到換行時，保底直接砍到 max_bytes 大小以內。"""
    log_path = tmp_path / "refresh.log"
    log_path.write_bytes(b"x" * 10_000)

    refresh_daily.rotate_log(log_path, max_bytes=1000)

    assert log_path.stat().st_size <= 1000


# ---------------------------------------------------------------------------
# 單步失敗不中斷、結尾彙總失敗（mock subprocess，不真的跑更新鏈）


def _fake_run_factory(fail_scripts):
    """回傳一個假的 subprocess.run：cmd 裡的腳本名在 fail_scripts 就回傳非 0。"""

    def fake_run(cmd, cwd=None, capture_output=None, text=None):
        script_name = str(cmd[1]).rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        if script_name in fail_scripts:
            return SimpleNamespace(returncode=1, stdout="部分輸出", stderr="模擬失敗")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    return fake_run


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    log_path = tmp_path / "refresh.log"
    monkeypatch.setattr(refresh_daily, "LOG_PATH", log_path)
    return log_path


def test_single_step_failure_does_not_stop_subsequent_steps(monkeypatch, isolated_log):
    attempted = []
    fail_scripts = {"build_taiex.py"}

    real_fake_run = _fake_run_factory(fail_scripts)

    def tracking_run(cmd, **kwargs):
        script_name = str(cmd[1]).rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        attempted.append(script_name)
        return real_fake_run(cmd, **kwargs)

    monkeypatch.setattr(refresh_daily.subprocess, "run", tracking_run)

    notified = []
    monkeypatch.setattr(
        refresh_daily, "send_failure_notification", lambda failed: notified.append(failed)
    )

    rc = refresh_daily.main([])

    # 全部 11 步都被嘗試過，即使 build_taiex.py 失敗
    assert attempted == refresh_daily.STEPS
    # exit code 反映有失敗
    assert rc == 1
    # 結尾彙總失敗並通知，且只包含真正失敗的那一步
    assert notified == [["build_taiex.py"]]
    assert isolated_log.exists()
    log_text = isolated_log.read_text(encoding="utf-8")
    assert "build_taiex.py" in log_text
    assert "FAIL" in log_text
    assert "OK" in log_text


def test_all_success_no_notification_and_exit_zero(monkeypatch, isolated_log):
    monkeypatch.setattr(
        refresh_daily.subprocess, "run", _fake_run_factory(fail_scripts=set())
    )

    def _fail_if_called(failed):
        raise AssertionError("全部成功時不應呼叫 send_failure_notification")

    monkeypatch.setattr(refresh_daily, "send_failure_notification", _fail_if_called)

    rc = refresh_daily.main([])

    assert rc == 0
    log_text = isolated_log.read_text(encoding="utf-8")
    assert "FAIL" not in log_text
    assert log_text.count("status=OK") == len(refresh_daily.STEPS)


def test_multiple_step_failures_all_reported(monkeypatch, isolated_log):
    fail_scripts = {"build_daily_prices.py", "build_group_flow.py"}
    monkeypatch.setattr(refresh_daily.subprocess, "run", _fake_run_factory(fail_scripts))

    notified = []
    monkeypatch.setattr(
        refresh_daily, "send_failure_notification", lambda failed: notified.append(failed)
    )

    rc = refresh_daily.main([])

    assert rc == 1
    assert notified == [["build_daily_prices.py", "build_group_flow.py"]]


def test_run_step_exception_treated_as_failure(monkeypatch, isolated_log):
    """subprocess.run 本身拋例外（例如找不到 python）也要視為該步失敗，不中止整條鏈。"""

    def boom(cmd, **kwargs):
        raise OSError("模擬找不到執行檔")

    monkeypatch.setattr(refresh_daily.subprocess, "run", boom)

    success, elapsed = refresh_daily.run_step("build_taiex.py")

    assert success is False
    assert elapsed >= 0
    log_text = isolated_log.read_text(encoding="utf-8")
    assert "EXCEPTION" in log_text


def test_send_failure_notification_swallow_import_error(monkeypatch, capsys):
    """notify 模組載入失敗（或缺 token）不可讓呼叫端拋例外。"""
    # 指向一個不存在的目錄，import notify 必失敗
    monkeypatch.setattr(refresh_daily, "TELEGRAM_NOTIFY_DIR", refresh_daily.REPO_ROOT / "no_such_dir_xyz")
    refresh_daily.send_failure_notification(["build_taiex.py"])  # 不應丟例外
