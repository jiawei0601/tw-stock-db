"""refresh_daily.py 的測試：只測邏輯（dry-run 輸出、log 輪替、單步失敗不中斷、
第 12 步 publish 的無變更跳過/commit+push/push 失敗不中止），用 mock 取代
subprocess.run，**不真的執行任何 build/export 腳本，也不真的碰 git**。"""
from __future__ import annotations

import itertools
from datetime import datetime
from types import SimpleNamespace

import pytest

import refresh_daily


EXPECTED_STEPS = [
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
    "publish",
]


def test_dry_run_lists_all_twelve_steps_in_order(capsys):
    rc = refresh_daily.main(["--dry-run"])
    out = capsys.readouterr().out

    assert rc == 0
    assert EXPECTED_STEPS == refresh_daily.STEPS
    assert len(refresh_daily.STEPS) == 12
    assert refresh_daily.STEPS[-1] == "publish"

    # 輸出裡的出現順序須與 STEPS 順序一致
    positions = [out.index(step) for step in EXPECTED_STEPS]
    assert positions == sorted(positions)
    for step in EXPECTED_STEPS:
        assert step in out


def test_dry_run_does_not_touch_subprocess(monkeypatch):
    """--dry-run 不應呼叫 subprocess.run（不執行任何步驟，含 publish）。"""
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
# 單步失敗不中斷、結尾彙總失敗（mock subprocess，不真的跑更新鏈/碰 git）


def _fake_run_factory(fail_scripts, git_behavior=None):
    """回傳一個假的 subprocess.run，同時處理兩種呼叫形態：

    - build/export 腳本（cmd = [sys.executable, script_path]）：script 名在
      fail_scripts 就回傳非 0。
    - git 呼叫（cmd[0] == "git"，來自 publish 步驟）：預設全部回傳 0（`diff
      --cached --quiet` 回 0 = 無變更，publish 會安靜跳過、不影響其他測試的
      「全部成功」假設），可透過 git_behavior={"diff": 1, ...} 覆寫個別子命令。
    """
    git_behavior = git_behavior or {}

    def fake_run(cmd, cwd=None, capture_output=None, text=None):
        if cmd[0] == "git":
            sub = cmd[1]
            rc = git_behavior.get(sub, 0)
            return SimpleNamespace(
                returncode=rc, stdout="", stderr="模擬 git 失敗" if rc != 0 else ""
            )
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
        if cmd[0] == "git":
            label = "publish"
        else:
            label = str(cmd[1]).rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        attempted.append(label)
        return real_fake_run(cmd, **kwargs)

    monkeypatch.setattr(refresh_daily.subprocess, "run", tracking_run)

    notified = []
    monkeypatch.setattr(
        refresh_daily, "send_failure_notification", lambda failed: notified.append(failed)
    )

    rc = refresh_daily.main([])

    # 全部 12 步都被嘗試過，即使 build_taiex.py 失敗（publish 的多次 git 呼叫
    # 摺疊成一個 "publish" 記號，順序與 STEPS 一致）
    collapsed = [k for k, _ in itertools.groupby(attempted)]
    assert collapsed == refresh_daily.STEPS
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


# ---------------------------------------------------------------------------
# publish（第 12 步）—— 只 add 白名單、無變更跳過、有變更 commit+push、push 失敗不中止


def _fake_git_run_factory(diff_rc=0, commit_rc=0, push_rc=0, add_rc=0):
    """比 `_fake_run_factory` 更細緻的 git-only 假 subprocess.run，記錄每次呼叫的
    完整 cmd，供斷言呼叫順序與參數（白名單、commit message、push 目標）。"""
    calls: list[list[str]] = []

    def fake_run(cmd, cwd=None, capture_output=None, text=None):
        calls.append(list(cmd))
        if cmd[0] != "git":
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")
        sub = cmd[1]
        rc = {"add": add_rc, "diff": diff_rc, "commit": commit_rc, "push": push_rc}.get(sub, 0)
        return SimpleNamespace(returncode=rc, stdout="", stderr="模擬 git 失敗" if rc != 0 else "")

    fake_run.calls = calls
    return fake_run


def test_publish_step_skips_when_no_changes(monkeypatch, isolated_log):
    fake = _fake_git_run_factory(diff_rc=0)  # `git diff --cached --quiet` 回 0 = 無差異
    monkeypatch.setattr(refresh_daily.subprocess, "run", fake)

    success, elapsed = refresh_daily.run_publish_step()

    assert success is True
    assert elapsed >= 0
    subcommands = [c[1] for c in fake.calls if c[0] == "git"]
    # 只 add + diff，無變更就不呼叫 commit/push
    assert subcommands == ["add", "diff"]
    # 只 add 白名單路徑，絕不 -A
    add_call = fake.calls[0]
    assert add_call == ["git", "add", "dashboard.html", "analysis/*.html"]
    assert "-A" not in add_call
    log_text = isolated_log.read_text(encoding="utf-8")
    assert "無變更，跳過發布" in log_text
    assert "status=OK" in log_text


def test_publish_step_commits_and_pushes_when_changed(monkeypatch, isolated_log):
    fake = _fake_git_run_factory(diff_rc=1)  # 有差異
    monkeypatch.setattr(refresh_daily.subprocess, "run", fake)

    success, elapsed = refresh_daily.run_publish_step()

    assert success is True
    assert elapsed >= 0
    subcommands = [c[1] for c in fake.calls if c[0] == "git"]
    assert subcommands == ["add", "diff", "commit", "push"]

    commit_call = next(c for c in fake.calls if c[0] == "git" and c[1] == "commit")
    assert "每日自動更新" in commit_call[-1]
    assert datetime.now().strftime("%Y-%m-%d") in commit_call[-1]

    push_call = next(c for c in fake.calls if c[0] == "git" and c[1] == "push")
    assert push_call == ["git", "push", "origin", "master"]

    log_text = isolated_log.read_text(encoding="utf-8")
    assert "status=OK" in log_text


def test_publish_step_push_failure_does_not_raise_and_marks_failed(monkeypatch, isolated_log):
    fake = _fake_git_run_factory(diff_rc=1, push_rc=1)  # 有變更、commit 成功、push 失敗
    monkeypatch.setattr(refresh_daily.subprocess, "run", fake)

    success, elapsed = refresh_daily.run_publish_step()  # 不應拋例外

    assert success is False
    assert elapsed >= 0
    subcommands = [c[1] for c in fake.calls if c[0] == "git"]
    assert subcommands == ["add", "diff", "commit", "push"]  # commit 仍有先跑過
    log_text = isolated_log.read_text(encoding="utf-8")
    assert "git push 失敗" in log_text
    assert "status=FAIL" in log_text


def test_publish_step_push_failure_via_main_marks_step_failed_but_exits(monkeypatch, isolated_log):
    """透過 main() 整體驗證：publish push 失敗會被計入 failed_steps、觸發通知、
    exit code 反映失敗，但不會讓其他步驟（本身是最後一步）之外的流程崩潰。"""
    fake = _fake_run_factory(fail_scripts=set(), git_behavior={"diff": 1, "push": 1})
    monkeypatch.setattr(refresh_daily.subprocess, "run", fake)

    notified = []
    monkeypatch.setattr(
        refresh_daily, "send_failure_notification", lambda failed: notified.append(failed)
    )

    rc = refresh_daily.main([])

    assert rc == 1
    assert notified == [["publish"]]


def test_no_publish_flag_skips_publish_step_entirely(monkeypatch, isolated_log):
    monkeypatch.setattr(
        refresh_daily.subprocess, "run", _fake_run_factory(fail_scripts=set())
    )

    def _fail_if_called(failed):
        raise AssertionError("--no-publish 且其餘步驟皆成功時不應呼叫通知")

    monkeypatch.setattr(refresh_daily, "send_failure_notification", _fail_if_called)

    rc = refresh_daily.main(["--no-publish"])

    assert rc == 0
    log_text = isolated_log.read_text(encoding="utf-8")
    assert "SKIP publish" in log_text
    # 真的被跳過，不是「執行後成功」——不應留下 publish 的 START/END 紀錄
    assert "START publish" not in log_text
    assert "END publish" not in log_text
