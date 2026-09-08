"""唯讀查詢在成功、拒絕、例外時均結束券商連線。"""
import sys
from datetime import date
from types import SimpleNamespace

import pytest


def test_explicit_external_classification_is_snapshot_scoped():
    from fubon_readonly_report import render_report
    html = render_report(dict(status='complete', classification='external_all_user_confirmed', accounts=[]))
    assert '使用者已確認' in html
    assert '未來新成交仍須核對歸屬' in html
    assert '所有庫存與成交均為「待分類」' not in html


def setup_gui(monkeypatch, tmp_path, login_ok=True):
    import fubon_readonly_gui as gui
    calls = []
    class FakeSDK:
        def login(self, *args):
            calls.append('login')
            return SimpleNamespace(is_success=login_ok, data=[])
        def logout(self):
            calls.append('logout')
        def shutdown(self):
            calls.append('shutdown')
    monkeypatch.setitem(sys.modules, 'fubon_neo.sdk', SimpleNamespace(FubonSDK=FakeSDK))
    monkeypatch.setattr(gui, 'verify_pfx', lambda *args: dict(not_before='2020-01-01T00:00:00+00:00', not_after='2099-01-01T00:00:00+00:00'))
    monkeypatch.setattr(gui, 'collect_snapshot', lambda *args: dict(status='complete', accounts=[]))
    from fubon_readonly import save_snapshot
    monkeypatch.setattr(gui, 'save_snapshot', lambda snapshot: save_snapshot(snapshot, tmp_path))
    return gui, calls


def test_readonly_success_logs_out_and_saves_no_secrets(monkeypatch, tmp_path):
    gui, calls = setup_gui(monkeypatch, tmp_path)
    result = gui.run_readonly('private-id', 'private-password', 'private-path', 'cert-secret', date(2026, 9, 1), date(2026, 9, 8))
    assert calls == ['login', 'logout', 'shutdown']
    assert result['logout_completed'] is True
    for file in tmp_path.iterdir():
        content = file.read_text(encoding='utf-8')
        assert 'private-' not in content
        assert 'cert-secret' not in content


def test_rejected_login_still_closes_session(monkeypatch, tmp_path):
    gui, calls = setup_gui(monkeypatch, tmp_path, login_ok=False)
    with pytest.raises(ValueError, match='broker_login_rejected'):
        gui.run_readonly('u', 'p', 'c', 'cp', date(2026, 9, 1), date(2026, 9, 8))
    assert calls == ['login', 'logout', 'shutdown']
    assert not list(tmp_path.iterdir())


def test_query_exception_still_closes_session(monkeypatch, tmp_path):
    gui, calls = setup_gui(monkeypatch, tmp_path)
    def fail(*args):
        raise RuntimeError('query failed')
    monkeypatch.setattr(gui, 'collect_snapshot', fail)
    with pytest.raises(RuntimeError):
        gui.run_readonly('u', 'p', 'c', 'cp', date(2026, 9, 1), date(2026, 9, 8))
    assert calls == ['login', 'logout', 'shutdown']
    assert not list(tmp_path.iterdir())
