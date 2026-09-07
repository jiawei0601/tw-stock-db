"""證交所／櫃買「產業價值鏈資訊平台」(https://ic.tpex.org.tw/) —— 產業鏈節點對應公司清單。

`introduce.php?ic=<chain_code>` 回傳整條產業鏈的完整靜態 HTML（含所有節點的公司清單，
不需要額外 AJAX），節點公司清單藏在 `<div id="companyList_<node_id>" title="<節點名>">`
裡的 `<a class="company-text-over" href="company_basic.php?stk_code=XXXX" title="公司名">`；
部分節點（目前觀察到 D100 IC設計、D300 IC/晶圓製造）另外細分「子鏈」，子鏈公司清單在
`<table id="sc_company_<subchain_id>">`（同樣的 `<a>` 結構）。**子鏈清單跟父節點的
`companyList_` 平面清單是同一批公司的不同粒度呈現，不要兩者都收，否則會重複計數**——
本模組固定只收「葉節點」：有子鏈的節點用子鏈，沒有子鏈的節點用父節點自己的
`companyList_`。

半導體鏈 (`ic=D000`) 節點與子鏈盤點（2026-09-07 實測）：
    DC00 IP設計/IC設計代工服務（無子鏈）
    D100 IC設計（有子鏈 D110~D1F0，全部屬於「IC設計」的細分類型，如 LED驅動IC／
          記憶體IC／電源管理IC 等，不需要逐一細分，直接用父節點 D100 的平面清單即可，
          因為所有子鏈語意上都同屬 IC設計）
    D200 光罩（無子鏈）
    D300 IC/晶圓製造（**必須用子鏈**，父節點平面清單把晶圓代工/記憶體/化合物半導體/
          功率元件全部混在一起，無法對齊 12 類；子鏈：
          D310 晶圓製造、D320 DRAM製造、D330 其他IC/二極體製造）
    D400 生產製程及檢測設備（無子鏈）
    D500 化學品（無子鏈）
    D600 生產製程及檢測設備（跟 D400 同名重複節點，TPEx 原始頁面如此，一併收，
          `INSERT OR REPLACE` 不會重複）
    D700 基板（無子鏈）
    D800 導線架（無子鏈）
    D900 IC封裝測試（無子鏈）
    DA00 IC模組（無子鏈）
    DB00 IC通路（無子鏈）

一檔股票可能同時出現在多個節點（例如 2408 南亞科同時在 D310 晶圓製造與 D320 DRAM
製造），這是真實的產業鏈重疊，呼叫端（build_sub_industry.py）允許一檔多列，不去重。
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from collectors._http import get as http_get

BASE_URL = "https://ic.tpex.org.tw/introduce.php"

# 已知有子鏈、且子鏈粒度比父節點更細（值得用子鏈取代父節點平面清單）的節點。
# key = 父節點 id, value = {子鏈id: 子鏈名稱}（子鏈名稱從 sc_link_<id> 的文字擷取，
# 這裡先寫死做為防禦性備援；主要仍以動態解析 HTML 為準，見 _parse_subchains）。
SUBCHAIN_PARENTS = {"D300"}  # D100 的子鏈全部同義（IC設計），刻意不展開


def _extract_companies(container) -> list[tuple[str, str]]:
    """從一個含 <a class="company-text-over" href="...stk_code=XXXX"> 的容器擷取
    (stock_id, name) 清單，只保留本國股票（href 帶 stk_code 參數的），排除外國供應商
    的 URL 連結（href 是 http(s):// 開頭的官網連結，不是台股代號）。"""
    out = []
    for a in container.find_all("a", class_="company-text-over"):
        href = a.get("href", "")
        if "stk_code=" not in href:
            continue
        sid = href.split("stk_code=")[-1]
        if not sid.isdigit():
            continue
        name = a.get("title") or a.get_text(strip=True)
        out.append((sid, name))
    return out


def fetch_chain(chain_code: str, *, ic_param: str | None = None) -> list[dict]:
    """抓一整條產業鏈的節點/子鏈 -> 公司清單。

    回傳 list of {"node_id": str, "node_name": str, "stock_id": str, "name": str}。
    純資料擷取，不判斷 sub 對齊（sub 對齊邏輯在 build_sub_industry.py）。
    """
    resp = http_get(
        "tpex_ic",
        BASE_URL,
        params={"ic": ic_param or chain_code},
        throttle_bucket="tpex_ic",
        min_interval=1.7,
    )
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    rows: list[dict] = []
    seen_node_ids: set[str] = set()

    for div in soup.find_all("div", id=lambda x: x and x.startswith("companyList_")):
        node_id = div.get("id").replace("companyList_", "")
        if node_id in seen_node_ids:
            continue  # 同一個 id 在頁面上出現兩次（menu 一次、內容一次），只收第一次
        seen_node_ids.add(node_id)
        node_name = div.get("title") or node_id

        if node_id in SUBCHAIN_PARENTS:
            subchain_tables = div.find_all("table", id=lambda x: x and x.startswith("sc_company_"))
            if subchain_tables:
                for table in subchain_tables:
                    sub_id = table.get("id").replace("sc_company_", "")
                    sub_name = _subchain_name(soup, sub_id) or node_name
                    for sid, name in _extract_companies(table):
                        rows.append({"node_id": sub_id, "node_name": sub_name, "stock_id": sid, "name": name})
                continue  # 有子鏈就不再收父節點自己的平面清單，避免重複

        for sid, name in _extract_companies(div):
            rows.append({"node_id": node_id, "node_name": node_name, "stock_id": sid, "name": name})

    return rows


def _subchain_name(soup: BeautifulSoup, sub_id: str) -> str | None:
    """從 `<div id="sc_link_<sub_id>">...文字...(N家)</div>` 擷取子鏈名稱（去掉家數）。"""
    link = soup.find("div", id=f"sc_link_{sub_id}")
    if link is None:
        return None
    text = link.get_text(strip=True)
    # 格式類似 "▶ 晶圓製造(26家)"，去掉開頭符號跟結尾家數
    text = text.lstrip("▸►► ").strip()
    idx = text.rfind("(")
    if idx > 0:
        text = text[:idx]
    return text.strip() or None
