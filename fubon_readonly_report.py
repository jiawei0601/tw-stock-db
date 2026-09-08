"""將富邦唯讀快照轉成可離線檢視的繁中 HTML。"""
from html import escape


INVENTORY_COLUMNS = (
    ("date", "日期"), ("stock_no", "股票代號"), ("order_type", "類型"),
    ("lastday_qty", "昨日庫存"), ("today_qty", "今日庫存"),
    ("tradable_qty", "可交易"), ("buy_filled_qty", "買進成交"),
    ("sell_filled_qty", "賣出成交"),
)
ODD_COLUMNS = (
    ("stock_no", "股票代號"), ("lastday_qty", "昨日庫存"),
    ("today_qty", "今日庫存"), ("tradable_qty", "可交易"),
    ("buy_filled_qty", "買進成交"), ("sell_filled_qty", "賣出成交"),
)
FILL_COLUMNS = (
    ("date", "日期"), ("filled_time", "時間"), ("stock_no", "股票代號"),
    ("buy_sell", "買賣"), ("order_type", "類型"), ("order_no", "委託序號"),
    ("filled_no", "成交序號"), ("filled_qty", "成交量"),
    ("filled_price", "成交價"), ("filled_avg_price", "平均成交價"),
    ("fees", "費稅"), ("assignment", "歸屬"),
)


def _text(value):
    if value is None:
        return "未知"
    if value is False:
        return "否"
    if value is True:
        return "是"
    return str(value)


def _cell(value):
    return escape(_text(value), quote=True)


def _table(columns, rows, empty_message="無資料"):
    head = "".join(f"<th>{escape(label)}</th>" for _, label in columns)
    if rows:
        body = "".join(
            "<tr>" + "".join(f"<td>{_cell(row.get(key))}</td>" for key, _ in columns) + "</tr>"
            for row in rows
        )
    else:
        body = f'<tr><td class="empty" colspan="{len(columns)}">{escape(empty_message)}</td></tr>'
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def render_report(snapshot: dict) -> str:
    """產生不需網路資源、只供人工對帳的 standalone HTML。"""
    status = snapshot.get("status")
    status_labels = {
        "complete": "查詢完成",
        "partial": "部分完成（不可視為成功）",
        "no_stock_accounts": "找不到證券帳戶",
    }
    status_label = status_labels.get(status, _text(status))
    status_class = "ok" if status == "complete" else "warning"

    account_sections = []
    for account in snapshot.get("accounts") or []:
        inventories = account.get("inventories") or []
        odd_rows = []
        for inventory in inventories:
            odd = inventory.get("odd")
            odd_rows.append({"stock_no": inventory.get("stock_no"), **(odd or {})})
        account_status = account.get("status")
        account_label = "查詢完成" if account_status == "complete" else "部分完成"
        account_sections.append(
            '<section class="account">'
            f'<h2>帳戶 {_cell(account.get("account_ref"))}</h2>'
            f'<p class="badge {"ok" if account_status == "complete" else "warning"}">{escape(account_label)}</p>'
            '<h3>整股庫存</h3>' + _table(INVENTORY_COLUMNS, inventories) +
            '<h3>零股庫存</h3>' + _table(ODD_COLUMNS, odd_rows, "無零股資料；缺少零股欄位表示未知") +
            '<h3>成交紀錄</h3>' + _table(FILL_COLUMNS, account.get("fills") or []) +
            '</section>'
        )
    if not account_sections:
        account_sections.append('<section class="account"><p class="empty">沒有可顯示的證券帳戶。</p></section>')

    errors = snapshot.get("errors") or []
    error_rows = _table(
        (("account_ref", "帳戶"), ("query", "查詢"), ("error_type", "錯誤類型")),
        errors,
        "無查詢錯誤",
    )
    imported = "是" if snapshot.get("strategy_imported") is True else "否"
    return f'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>富邦券商唯讀對帳報告</title>
<style>
:root{{--ink:#17202a;--muted:#59636e;--line:#d9dee3;--paper:#fff;--bg:#f4f6f8;--ok:#176b3a;--warn:#9a4d00}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Noto Sans TC",sans-serif}}
main{{max-width:1180px;margin:auto;padding:28px 18px}}h1{{margin:0 0 8px}}h2{{margin:0}}h3{{margin:24px 0 8px}}
.summary,.account{{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:20px;margin:16px 0}}
.meta{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:8px 20px}}.meta b{{display:block;color:var(--muted);font-size:13px}}
.badge{{display:inline-block;margin:8px 0;padding:3px 9px;border-radius:999px;font-weight:700}}.ok{{color:var(--ok);background:#e9f7ef}}.warning{{color:var(--warn);background:#fff1df}}
.notice{{border-left:4px solid var(--warn);padding:10px 14px;background:#fff8ed;margin-top:16px}}.scroll{{overflow:auto}}
table{{border-collapse:collapse;width:100%;white-space:nowrap}}th,td{{padding:8px 10px;border:1px solid var(--line);text-align:left}}th{{background:#eef1f4}}.empty{{color:var(--muted);text-align:center}}
</style></head><body><main>
<h1>富邦券商唯讀對帳報告</h1><p>本機快照，僅供人工核對。</p>
<section class="summary"><div class="meta">
<div><b>擷取時間（UTC）</b>{_cell(snapshot.get("fetched_at"))}</div>
<div><b>查詢範圍</b>{_cell(snapshot.get("start"))} ～ {_cell(snapshot.get("end"))}</div>
<div><b>完整性狀態</b><span class="badge {status_class}">{escape(status_label)}</span></div>
<div><b>已匯入策略帳本</b>{imported}</div>
</div><div class="notice">所有庫存與成交均為「待分類」。本報告不代表任何項目屬於策略持股；成交費用與交易稅未知，且缺少的庫存欄位應視為未知，不能當作 0。</div></section>
{''.join(account_sections)}
<section class="account"><h2>查詢錯誤</h2>{error_rows}</section>
</main></body></html>'''
