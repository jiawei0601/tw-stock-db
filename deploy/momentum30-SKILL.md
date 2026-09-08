---
name: momentum30
description: 使用者50萬元動能30%獨立帳戶的每週操作通知、實際成交回報及帳本核對。收到「動能30」或此帳戶成交訊息時使用。
---

# 動能30%帳戶

先讀 `/home/chang/tw-momentum30/STRATEGY.md`。固定引擎位於同目錄 `live_momentum.py`。週三16:00完整通知，平日收盤有退出或資料問題才通知，由Hermes固定腳本執行。

用戶僅授權通知，不授權券商下單。不要套用其他持股SOP、90天或MA分批。不要把候選當已買入。這個帳戶的實際成交唯一來源為使用者提供的成交資訊。

收到「動能30 成交ID 買/賣 代號 股數 均價 費稅合計 日期」：確認是實際成交、欄位完整，缺任何資料先補問；不要填預估成交價或猜費用。再使用固定命令（參數須經型別驗證，shell安全引號，日期ISO格式）：

```bash
cd /home/chang/tw-momentum30
/home/chang/.hermes/hermes-agent/venv/bin/python live_momentum.py fill --id '券商成交唯一ID' --side buy --stock-id '股票代號' --shares 整數 --price 實際均價 --fees 實際費稅總額 --date YYYY-MM-DD
```

`--side sell`為賣出。成交ID重複不可換ID繞過防重複。程序拒絕資金不足、超賣、重複加碼與未來成交。失敗要通知用戶，不直接改JSON繞過檢查。

可讀取 `live_data/account.json`、`pending.json`與`report-日期.json`核對。除權、配股、分割、股利與資金存提目前需取得券商確實資料後另做有備份的人工會計修正；不要猜比率，不要宣稱已完整處理。碰到資料警報先明示阻礙，不沿用舊買单。

使用者要臨時看候選，可跑 `live_momentum.py --preview`，必须說明非週三時只是預覽。來源失敗不以LLM猜選股或改門檻。
