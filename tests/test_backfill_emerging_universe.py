from backfill_emerging_universe import (
    build_artifact,
    parse_current_master,
    parse_registrations,
    parse_revocations,
    parse_terminations,
)


def test_registration_date_is_inclusive_and_schema_driven():
    payload = {
        "tables": [{
            "fields": ["序號", "股票代號", "公司名稱", "登錄日期", "備註"],
            "data": [[1, "7610", "聯友金屬-新", "111/09/30", "轉一般板"]],
        }],
        "stat": "ok",
    }

    assert parse_registrations(payload) == [{
        "stock_id": "7610",
        "name": "聯友金屬-新",
        "start": "2022-09-30",
    }]


def test_termination_uses_effective_date_and_rejects_other_announcements():
    payload = {
        "stat": "ok",
        "tables": [{
            "fields": ["項次", "資料日期", "發文字號", "主旨", "詳細資料"],
            "data": [
                [1, "114/09/08", "證櫃審字第1號",
                 "公告自114年9月9日起終止與聯友金屬科技股份有限公司（股票代號：7610）簽訂之興櫃股票櫃檯買賣契約", "detail"],
                [2, "109/04/08", "證櫃審字第2號",
                 "證券商自109年4月23日起辭任鋒霖科技（證券代號：5294）之興櫃股票推薦證券商", "detail2"],
            ],
        }],
    }

    assert parse_terminations(payload) == [{
        "stock_id": "7610",
        "end": "2025-09-09",
        "announcement_date": "2025-09-08",
        "document_number": "證櫃審字第1號",
        "detail": "detail",
    }]


def test_current_master_uses_official_listing_date_even_when_future():
    rows = [{
        "Date": "1150908",
        "SecuritiesCompanyCode": "7947",
        "CompanyAbbreviation": "測試公司",
        "DateOfListing": "20260916",
    }]

    assert parse_current_master(rows)["7947"]["start"] == "2026-09-16"


def test_revoked_termination_does_not_close_the_interval():
    payload = {
        "stat": "ok",
        "tables": [{
            "fields": ["項次", "資料日期", "發文字號", "主旨", "詳細資料"],
            "data": [[1, "105/07/25", "證櫃審字第10501011671號",
                      "廢止本中心前於105年7月14日證櫃審字第10501010861號公告有關終止富圓采科技股份有限公司(股票代號：4969)普通股股票買賣", "detail"]],
        }],
    }
    revocations = parse_revocations(payload)
    terminations = [
        {"stock_id": "4969", "end": "2016-07-29", "announcement_date": "2016-07-14",
         "document_number": "證櫃審字第10501010861號", "detail": "revoked"},
        {"stock_id": "4969", "end": "2017-06-17", "announcement_date": "2017-06-02",
         "document_number": "證櫃審字第10600145811號", "detail": "effective"},
    ]

    artifact = build_artifact(
        {"records": []},
        [{"stock_id": "4969", "name": "富圓采", "start": "2010-09-24"}],
        terminations,
        {},
        [],
        revocations,
    )

    assert artifact["records"][0]["end"] == "2017-06-17"
    assert artifact["coverage"]["revoked_termination_event_count"] == 1
    assert artifact["coverage"]["unmatched_termination_event_count"] == 0


def test_build_preserves_listed_records_and_closes_transfer_at_same_boundary():
    listed_record = {
        "stock_id": "7610", "market": "TWSE", "start": "2025-09-09",
        "end": None, "source": "TWSE_CURRENT_MASTER", "extra": {"keep": True},
    }
    artifact = build_artifact(
        {"records": [listed_record]},
        [{"stock_id": "7610", "name": "聯友金屬-新", "start": "2022-09-30"}],
        [{"stock_id": "7610", "end": "2025-09-09", "announcement_date": "2025-09-08",
          "document_number": "證櫃審字第1號", "detail": "detail"}],
        {},
        [],
    )

    assert artifact["records"][0] == listed_record
    assert artifact["records"][1]["start"] == "2022-09-30"
    assert artifact["records"][1]["end"] == "2025-09-09"


def test_relisted_stock_keeps_gap_and_unknown_terminal_cycle_is_omitted():
    registrations = [
        {"stock_id": "7000", "name": "甲", "start": "2020-01-02"},
        {"stock_id": "7000", "name": "甲", "start": "2022-03-04"},
        {"stock_id": "7001", "name": "乙", "start": "2020-02-03"},
    ]
    terminations = [{
        "stock_id": "7000", "end": "2021-06-01", "announcement_date": "2021-05-17",
        "document_number": "證櫃審字第3號", "detail": "detail3",
    }]
    current = {"7000": {"start": "2022-03-04", "name": "甲"}}

    artifact = build_artifact({"records": []}, registrations, terminations, current, [])
    rows = artifact["records"]

    assert [(row["start"], row["end"]) for row in rows] == [
        ("2020-01-02", "2021-06-01"),
        ("2022-03-04", None),
    ]
    assert artifact["coverage"]["unknown_end_stock_ids"] == ["7001"]
