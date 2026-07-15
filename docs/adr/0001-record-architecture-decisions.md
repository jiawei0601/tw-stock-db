# 1. 記錄架構決策（ADR）

狀態：已採納

## 脈絡
本專案由多個 AI agent（Claude Code、Antigravity）輪流開發，架構決策必須 agent-neutral、留痕，避免不同 agent 各做各的。

## 決策
重要架構決策以 ADR 形式記錄於 docs/adr/，依序編號；每則含：脈絡、決策、後果。

## 後果
任何 agent 接手前先讀 ADR，不重新糾結已定案的選擇。
