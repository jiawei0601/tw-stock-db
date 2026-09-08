from __future__ import annotations

import csv
import json

import backtest_momentum as report


def _sample_result() -> dict:
    return {
        "certification": {"status": "SYNTHETIC_ONLY", "certified": False},
        "signals": [
            {
                "signal_date": "2020-01-31",
                "stock_id": "SYN1",
                "momentum": 0.12,
                "rank": 1,
                "weight": 1.0,
                "strategy": "momentum",
            }
        ],
        "trades": [{"date": "2020-02-03", "stock_id": "SYN1", "side": "buy"}],
        "cohorts": [
            {
                "signal_date": "2020-01-31",
                "entry_date": "2020-02-03",
                "exit_date": "2020-08-03",
                "horizon": "6m",
                "strategy": "momentum",
                "return_price": 0.1,
                "return_total": 0.11,
                "return_net": 0.104,
                "status": "matured",
                "n_stocks": 1,
            }
        ],
        "equity": [
            {"date": "2020-02-03", "horizon": "6m", "strategy": "momentum", "nav": 1.0, "drawdown": 0.0},
            {"date": "2020-02-04", "horizon": "6m", "strategy": "momentum", "nav": 1.01, "drawdown": 0.0},
        ],
        "summary": {
            "momentum_6m": {
                "n": 1,
                "mean": 0.104,
                "median": 0.104,
                "positive_rate": 1.0,
                "CAGR": 0.2,
                "MDD": 0.1,
            }
        },
        "issues": [{"code": "DEMO", "message": "合成資料僅供展示"}],
    }


def test_demo_data_is_deterministic_complete_and_explicitly_synthetic():
    first = report.build_demo_data()
    second = report.build_demo_data()

    assert first == second
    assert first["synthetic"] is True
    assert first["calendar"][0] == "2018-12-03"
    assert first["calendar"][-1] == "2023-02-28"
    assert len(first["prices"]) == 4
    assert all(first["verified"].values())
    assert all(first["evidence"][key] for key in report.EVIDENCE_KEYS)
    assert all(set(rows) == set(first["calendar"]) for rows in first["prices"].values())


def test_report_bundle_labels_every_portable_synthetic_artifact(tmp_path):
    paths = report.write_report_bundle(_sample_result(), tmp_path, synthetic=True)

    payload = json.loads(paths["result"].read_text(encoding="utf-8"))
    assert payload["synthetic"] is True
    assert "非真實市場績效" in payload["dataset_label"]

    assert paths["signals"].name == "synthetic_signals.csv"
    for section in report.LIST_SECTIONS:
        assert paths[section].name.startswith("synthetic_")
        with paths[section].open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if rows:
            assert all(row["synthetic"] == "True" for row in rows)

    markdown = paths["report"].read_text(encoding="utf-8")
    assert "合成展示資料" in markdown
    assert "非真實市場績效" in markdown
    assert "synthetic_signals.csv" in markdown

    for key in ("equity_svg", "drawdown_svg"):
        assert paths[key].name.startswith("synthetic_")
        svg = paths[key].read_text(encoding="utf-8")
        assert svg.startswith("<svg")
        assert "合成展示資料" in svg
        assert "momentum / 6m" in svg


def test_generic_csv_uses_union_of_columns(tmp_path):
    target = tmp_path / "rows.csv"
    report.write_listdict_csv(target, [{"a": 1}, {"b": 2}], synthetic=False)
    with target.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert reader.fieldnames == ["synthetic", "a", "b"]
    assert rows == [
        {"synthetic": "False", "a": "1", "b": ""},
        {"synthetic": "False", "a": "", "b": "2"},
    ]


def test_equity_chart_preserves_benchmark_horizon_zero():
    series = report._equity_series(
        [{"date": "2020-01-02", "strategy": "TAIEX_close_reference", "horizon": 0, "nav": 1.0}],
        "nav",
    )
    assert "TAIEX_close_reference / 0" in series


def test_markdown_summary_is_compact_and_uses_percentages():
    result = _sample_result()
    result["summary"].update(
        {
            "momentum_6_entry_year_2020": {
                "n": 1,
                "mean": 0.104,
                "median": 0.104,
                "worst_cohort": 0.104,
            },
            "momentum_common_entries": {
                "n": 1,
                "entries": ["2020-02-03"],
                "mean_6m": 0.104,
                "mean_12m": 0.2,
            },
            "momentum_6_vs_universe": {
                "n": 1,
                "mean_excess": 0.03,
                "beat_rate": 1.0,
                "excess_ci95": [0.01, 0.05],
            },
            "future_metric": {"large_array": list(range(20))},
        }
    )
    markdown = report.render_markdown_report(
        result,
        synthetic=False,
        csv_names={section: f"{section}.csv" for section in report.LIST_SECTIONS},
    )

    assert "10.40%" in markdown
    assert "### 依進場年度" in markdown
    assert "### 6／12 個月共同成熟月份" in markdown
    assert "### 相對全母體等權" in markdown
    assert "2020-02-03" not in markdown
    assert "large_array" not in markdown
    assert "result.json" in markdown
    assert "實際支付日前" in markdown


def test_real_chart_label_does_not_claim_certification():
    svg = report._svg_chart({}, title="測試", y_label="淨值", synthetic=False)
    assert "研究資料（認證狀態見報告）" in svg
    assert "已核對資料回測" not in svg


def test_validation_error_refuses_result_files(tmp_path, monkeypatch, capsys):
    def engine(_data, **_kwargs):
        raise ValueError("coverage 尚未通過")

    monkeypatch.setattr(report, "_load_engine", lambda: engine)
    code = report.main(["--demo", "--out", str(tmp_path)])

    assert code == 2
    assert not (tmp_path / "result.json").exists()
    assert not (tmp_path / "report.md").exists()
    assert "拒絕產生回測結果" in capsys.readouterr().err


def test_demo_runs_default_cost_sensitivity_without_changing_strategy(tmp_path, monkeypatch):
    calls = []

    def engine(_data, **kwargs):
        calls.append(kwargs)
        return _sample_result()

    monkeypatch.setattr(report, "_load_engine", lambda: engine)
    assert report.main(["--demo", "--out", str(tmp_path)]) == 0

    assert [(call["buy_cost"], call["sell_cost"]) for call in calls] == [
        (0.003, 0.003),
        (0.0015, 0.0015),
        (0.003, 0.003),
        (0.006, 0.006),
    ]
    payload = json.loads((tmp_path / "synthetic_cost_sensitivity.json").read_text(encoding="utf-8"))
    assert payload["synthetic"] is True
    assert "不是策略參數搜尋" in payload["note"]


def test_audit_never_loads_engine_or_computes_returns(tmp_path, monkeypatch):
    raw_db = tmp_path / "cache.sqlite"
    manifest = tmp_path / "manifest.json"
    raw_db.touch()
    manifest.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        report,
        "_load_data_functions",
        lambda: (lambda db, mf: {"db": db.name, "manifest": mf.name, "complete": False}, lambda *_: None),
    )
    monkeypatch.setattr(report, "_load_engine", lambda: (_ for _ in ()).throw(AssertionError("engine must not load")))

    code = report.main(
        ["--raw-db", str(raw_db), "--manifest", str(manifest), "--audit", "--out", str(tmp_path / "out")]
    )

    assert code == 0
    payload = json.loads((tmp_path / "out" / "audit.json").read_text(encoding="utf-8"))
    assert payload["complete"] is False
    assert payload["audit_only"] is True
    assert payload["returns_computed"] is False
