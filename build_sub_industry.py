"""半導體（及其他）產業鏈子產業分類表 —— 取代 build_valuation.py 裡寫死的
`SEMI_SUB_MAP`，改用證交所／櫃買「產業價值鏈資訊平台」(https://ic.tpex.org.tw/)
的官方節點分類做為來源，見 `collectors/industry_chain.py` 的節點/子鏈盤點說明。

背景：`SEMI_SUB_MAP` 原本是人工憑印象寫死的 {股號: 子產業} dict，2026-09-07
委員會評估時發現 147/190 檔落入「其他」（覆蓋率不足、且無來源可查證）。本腳本改成
「先抓官方節點 -> 節點名對齊固定 12 類 -> 寫入資料庫」，可重跑更新、有來源可查證。

    python build_sub_industry.py [--db-path PATH]
        抓半導體鏈（ic=D000）全部節點 + 被動元件鏈（ic=J000，TPEx 首頁盤點確認除
        半導體外唯一與此表相關的獨立產業鏈，光通訊/功率未見獨立成鏈，已內含在
        半導體鏈的節點裡）寫入 stock_sub_industry。純本地判斷 + 少量網路請求
        （每條鏈一次 GET，節流沿用 collectors/_http.py），冪等，重跑安全
        （先刪除 source 對應的舊列再整批寫入）。

12 類固定分類（NODE_TO_SUB，只對齊半導體鏈；被動元件鏈等其餘鏈的 sub 直接填節點原名，
未強制對齊 12 類，見下方 CHAIN_SPECS 的 align_sub 旗標）：
    IC設計 / 晶圓代工 / 封測 / 記憶體 / 矽智財與ASIC / 設備 / 材料與矽晶圓 /
    化合物半導體 / 功率與分離元件 / 光電與感測 / 通路 / 其他
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from collectors.industry_chain import fetch_chain

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stock_sub_industry (
    stock_id    TEXT NOT NULL,
    chain       TEXT NOT NULL,
    node        TEXT NOT NULL,
    sub         TEXT NOT NULL,
    source      TEXT NOT NULL,
    confidence  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (stock_id, node, source)
);
"""

VALID_SUBS = {
    "IC設計", "晶圓代工", "封測", "記憶體", "矽智財與ASIC", "設備",
    "材料與矽晶圓", "化合物半導體", "功率與分離元件", "光電與感測", "通路", "其他",
}

# 半導體鏈（ic=D000）節點/子鏈名稱 -> 固定 12 類對照。
# 每條註解＝對齊理由，來源節點盤點見 collectors/industry_chain.py 模組說明。
NODE_TO_SUB: dict[str, str] = {
    "IP設計/IC設計代工服務": "矽智財與ASIC",   # ARM/Cadence/Synopsys/創意/M31 等 IP 核心與 ASIC 代工設計服務
    "IC設計": "IC設計",                        # D100 父節點平面清單（子鏈 D110~D1F0 全部屬 IC設計細分類，見模組說明）
    "光罩": "其他",                            # 光罩（photomask）不屬於既有 12 類任一項，如實歸類其他
    "晶圓製造": "晶圓代工",                     # D310 子鏈：台積電/聯電/世界先進等純晶圓代工/製造
    "DRAM製造": "記憶體",                       # D320 子鏈：南亞科/華邦電/力積電等 DRAM IDM 製造
    "其他IC/二極體製造": "功率與分離元件",       # D330 子鏈：麗正/強茂/漢磊等功率二極體/分離元件製造為主
    "生產製程及檢測設備": "設備",               # D400/D600（頁面重複節點）
    "化學品": "材料與矽晶圓",                   # D500，半導體製程化學品原料
    "基板": "材料與矽晶圓",                     # D700，封裝基板材料
    "導線架": "材料與矽晶圓",                   # D800，封裝導線架材料
    "IC封裝測試": "封測",                       # D900
    "IC模組": "其他",                          # DA00，模組整合不屬於既有 12 類任一項
    "IC通路": "通路",                          # DB00
}

# CHAIN_SPECS：要抓的產業鏈清單。align_sub=True 才套用 NODE_TO_SUB 對齊 12 類，
# 否則 sub 直接填節點原名（誠實反映「未強制對齊」，供未來擴充參考，不影響
# build_valuation.py 目前只讀半導體鏈的用法）。
CHAIN_SPECS = [
    {"chain": "半導體", "ic_param": "D000", "source": "tpex_ic", "align_sub": True},
    {"chain": "被動元件", "ic_param": "J000", "source": "tpex_ic", "align_sub": False},
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def build(conn: sqlite3.Connection) -> dict:
    now = _now_iso()
    total_rows = 0
    unmapped_nodes: set[str] = set()

    for spec in CHAIN_SPECS:
        chain = spec["chain"]
        source = spec["source"]
        rows = fetch_chain(spec["ic_param"])

        conn.execute("DELETE FROM stock_sub_industry WHERE chain = ? AND source = ?", (chain, source))

        for r in rows:
            node = r["node_name"]
            if spec["align_sub"]:
                sub = NODE_TO_SUB.get(node)
                if sub is None:
                    unmapped_nodes.add(node)
                    sub = "其他"
            else:
                sub = node  # 未強制對齊 12 類的鏈，sub 直接是節點原名（不驗證 VALID_SUBS）

            conn.execute(
                """
                INSERT OR REPLACE INTO stock_sub_industry
                    (stock_id, chain, node, sub, source, confidence, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (r["stock_id"], chain, node, sub, source, "official_chain", now),
            )
            total_rows += 1

    conn.commit()
    return {"rows_written": total_rows, "unmapped_nodes": sorted(unmapped_nodes)}


def check_semiconductor_coverage(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """比對 build_valuation.py 的 semiconductor_universe()（stocks.industry_name LIKE
    '%半導體%'）跟 stock_sub_industry（chain='半導體'）——回傳「在 universe 但完全查
    不到任何半導體鏈節點列」的 (stock_id, name) 清單，只回報不自動補。"""
    rows = conn.execute(
        "SELECT stock_id, name FROM stocks WHERE industry_name LIKE '%半導體%'"
    ).fetchall()
    covered = {
        sid for (sid,) in conn.execute(
            "SELECT DISTINCT stock_id FROM stock_sub_industry WHERE chain = '半導體'"
        ).fetchall()
    }
    return [(sid, name) for sid, name in rows if sid not in covered]


def main() -> None:
    parser = argparse.ArgumentParser(description="產業鏈子產業分類表建置（取代 build_valuation.py 寫死的 SEMI_SUB_MAP）")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    conn = get_conn(args.db_path)
    try:
        result = build(conn)
        print(f"stock_sub_industry 寫入完成：{result['rows_written']} 列")
        if result["unmapped_nodes"]:
            print(f"警告：以下節點名稱不在 NODE_TO_SUB 對照表中，已暫歸「其他」："
                  f"{result['unmapped_nodes']}")

        total_universe = conn.execute(
            "SELECT COUNT(*) FROM stocks WHERE industry_name LIKE '%半導體%'"
        ).fetchone()[0]
        missing = check_semiconductor_coverage(conn)
        print(f"\n半導體 universe（stocks.industry_name LIKE '%半導體%'）共 {total_universe} 檔中，"
              f"{len(missing)} 檔在產業鏈平台完全查無節點：")
        for sid, name in missing:
            print(f"  {sid} {name}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
