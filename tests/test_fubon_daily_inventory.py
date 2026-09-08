import json
import sys
from datetime import date
from types import ModuleType, SimpleNamespace

import pytest

import fubon_daily_inventory as daily


TODAY = date(2026, 9, 8)


def odd(qty=3):
    return SimpleNamespace(lastday_qty=qty, today_qty=qty, tradable_qty=qty,
                           buy_filled_qty=0, sell_filled_qty=0)


def inventory(symbol="2330", regular=1000, odd_row=None, inventory_date="2026/09/08"):
    return SimpleNamespace(date=inventory_date, stock_no=symbol, order_type="Stock",
                           lastday_qty=regular, today_qty=regular, tradable_qty=regular,
                           buy_filled_qty=0, sell_filled_qty=0,
                           odd=odd() if odd_row is None else odd_row)


class Reply:
    def __init__(self, data=None, is_success=True):
        self.data = data
        self.is_success = is_success


class Accounting:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def inventories(self, account):
        self.calls.append(account.account)
        reply = self.replies[account.account]
        if isinstance(reply, Exception):
            raise reply
        return reply


def account(kind, number):
    return SimpleNamespace(account_type=kind, account=number, branch_no="001", name="秘密姓名")


def test_normalize_adds_regular_and_odd_shares_and_accepts_both_date_forms():
    rows = [inventory(), inventory("0050", 10, odd(7), TODAY.isoformat())]
    result = daily.normalize_inventory(rows, TODAY)
    assert [row["total_shares"] for row in result] == [1003, 17]
    assert result[1]["odd"]["today_qty"] == 7


@pytest.mark.parametrize("row,error", [
    (inventory(odd_row=False), "missing_odd_inventory"),
    (inventory(regular=True), "invalid_inventory_quantity"),
    (inventory(regular=-1), "invalid_inventory_quantity"),
    (inventory(odd_row=odd(-1)), "invalid_inventory_quantity"),
    (inventory(inventory_date="2026/09/07"), "inventory_date_not_today"),
])
def test_normalize_rejects_missing_odd_bool_negative_and_old_date(row, error):
    # False is used only to make the factory preserve an explicit missing odd below.
    if row.odd is False:
        row.odd = None
    with pytest.raises(ValueError, match=error):
        daily.normalize_inventory([row], TODAY)


def test_query_isolates_stock_accounts_skips_futopt_and_throttles_each_stock():
    first, future, second = account("stock", "A1"), account("futopt", "F1"), account("stock", "A2")
    accounting = Accounting({"A1": Reply([inventory("2330")]), "A2": Reply([inventory("0050", 4, odd(1))])})
    pauses = []
    result = daily.query_inventory(SimpleNamespace(accounting=accounting), [first, future, second], TODAY, pauses.append)
    assert result[0]["holdings"][0]["stock_no"] == "2330"
    assert result[1]["holdings"][0]["stock_no"] == "0050"
    assert accounting.calls == ["A1", "A2"]
    assert pauses == [0.3, 0.3]
    assert "A1" not in str(result) and "秘密姓名" not in str(result)


def test_query_distinguishes_empty_success_from_rejected_none_and_no_stock():
    acc = account("stock", "A1")
    sdk = SimpleNamespace(accounting=Accounting({"A1": Reply([])}))
    assert daily.query_inventory(sdk, [acc], TODAY, lambda _: None)[0]["holdings"] == []
    for reply in (Reply(None), Reply([], is_success=False)):
        sdk = SimpleNamespace(accounting=Accounting({"A1": reply}))
        with pytest.raises(ValueError, match="inventory_query_failed"):
            daily.query_inventory(sdk, [acc], TODAY, lambda _: None)
    with pytest.raises(ValueError, match="no_stock_accounts"):
        daily.query_inventory(SimpleNamespace(), [account("futopt", "F1")], TODAY, lambda _: None)


def test_one_account_failure_prevents_partial_result():
    accounts = [account("stock", "A1"), account("stock", "A2")]
    sdk = SimpleNamespace(accounting=Accounting({"A1": Reply([inventory()]), "A2": Reply(None)}))
    with pytest.raises(ValueError, match="inventory_query_failed"):
        daily.query_inventory(sdk, accounts, TODAY, lambda _: None)


def test_format_context_has_only_monitoring_fields_and_omits_zero_holdings():
    snapshot = {"fetched_at": "2026-09-08T14:00:00+08:00", "inventory_date": "2026-09-08", "accounts": [
        {"account_label": "證券帳戶1", "holdings": [
            {"stock_no": "2330", "order_type": "Stock", "today_qty": 1000, "odd": {"today_qty": 3}, "total_shares": 1003,
             "account": "SECRET", "cost": 1},
            {"stock_no": "ZERO", "order_type": "Stock", "today_qty": 0, "odd": {"today_qty": 0}, "total_shares": 0},
        ]}
    ]}
    text = daily.format_context(snapshot)
    assert '"shares": 1003' in text and '"odd_shares": 3' in text
    assert "ZERO" not in text and "SECRET" not in text and '"cost"' not in text
    assert "50萬元動能30策略隔離" in text and "不是買賣指令" in text


class FakeSDK:
    next_login = None
    instances = []

    def __init__(self):
        self.logout_calls = 0
        self.shutdown_calls = 0
        self.login_args = None
        self.accounting = Accounting({"A1": Reply([inventory()])})
        type(self).instances.append(self)

    def apikey_login(self, *args):
        self.login_args = args
        reply = type(self).next_login
        if isinstance(reply, Exception):
            raise reply
        return reply

    def logout(self):
        self.logout_calls += 1

    def shutdown(self):
        self.shutdown_calls += 1


def install_fake_sdk(monkeypatch, login_reply):
    FakeSDK.instances.clear()
    FakeSDK.next_login = login_reply
    package = ModuleType("fubon_neo")
    module = ModuleType("fubon_neo.sdk")
    module.FubonSDK = FakeSDK
    monkeypatch.setitem(sys.modules, "fubon_neo", package)
    monkeypatch.setitem(sys.modules, "fubon_neo.sdk", module)


def write_private(tmp_path, credentials=None):
    private = tmp_path / "private"
    private.mkdir()
    (private / "credentials.json").write_text(json.dumps(credentials or {
        "personal_id": "PERSONAL-SECRET", "api_key": "API-SECRET", "cert_password": "CERT-SECRET"
    }), encoding="utf-8")
    (private / "certificate.p12").write_bytes(b"fake")


def test_run_success_uses_api_key_login_saves_latest_and_closes(monkeypatch, tmp_path, capsys):
    write_private(tmp_path)
    install_fake_sdk(monkeypatch, Reply([account("stock", "A1")]))
    assert daily.run(tmp_path) == 0
    sdk = FakeSDK.instances[0]
    assert sdk.login_args[0:2] == ("PERSONAL-SECRET", "API-SECRET")
    assert sdk.login_args[3] == "CERT-SECRET"
    assert sdk.logout_calls == sdk.shutdown_calls == 1
    latest = json.loads((tmp_path / "state" / "latest.json").read_text(encoding="utf-8"))
    assert latest["status"] == "complete" and latest["accounts"][0]["holdings"][0]["total_shares"] == 1003
    output = capsys.readouterr().out
    assert "PERSONAL-SECRET" not in output and "API-SECRET" not in output and "CERT-SECRET" not in output
    assert not hasattr(sdk, "place_order")


@pytest.mark.parametrize("login_reply,expected_stage", [
    (RuntimeError("API-SECRET leaked"), "login"),
    (Reply([], is_success=False), "login"),
])
def test_run_login_failure_closes_and_keeps_old_latest(monkeypatch, tmp_path, capsys, login_reply, expected_stage):
    write_private(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "latest.json").write_text('{"old": true}', encoding="utf-8")
    install_fake_sdk(monkeypatch, login_reply)
    assert daily.run(tmp_path) == 1
    sdk = FakeSDK.instances[0]
    assert sdk.logout_calls == sdk.shutdown_calls == 1
    assert json.loads((state / "latest.json").read_text(encoding="utf-8")) == {"old": True}
    failure = json.loads((state / "last-run.json").read_text(encoding="utf-8"))
    assert failure["status"] == "failed" and failure["stage"] == expected_stage
    output = capsys.readouterr().out
    assert "API-SECRET" not in output and "leaked" not in output


def test_run_query_failure_closes_and_does_not_overwrite_latest(monkeypatch, tmp_path, capsys):
    write_private(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "latest.json").write_text('{"old": true}', encoding="utf-8")
    install_fake_sdk(monkeypatch, Reply([account("stock", "A1")]))
    monkeypatch.setattr(daily, "query_inventory", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("CERT-SECRET")))
    assert daily.run(tmp_path) == 1
    sdk = FakeSDK.instances[0]
    assert sdk.logout_calls == sdk.shutdown_calls == 1
    assert json.loads((state / "latest.json").read_text(encoding="utf-8")) == {"old": True}
    assert json.loads((state / "last-run.json").read_text(encoding="utf-8"))["stage"] == "inventory"
    assert "CERT-SECRET" not in capsys.readouterr().out
