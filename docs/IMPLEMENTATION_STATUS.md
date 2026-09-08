# Polymarket US 實作狀態

更新：2026-09-08。規格：[SPEC.md](SPEC.md)；開發順序：[PLAN.md](PLAN.md)。

Phase 0 基礎環境已完成。Sprint 1 已從國際版調整為美國版中長天期 BTC 唯讀掃描器。

| 元件 | 已實作 |
|---|---|
| 市場搜尋 | 美國版公開 GET、分頁、狀態／觀察天期過濾、規則解析 |
| 市場識別 | 平台＋slug、單一 instrument、合成 NO 顯示 |
| 合約 | touch above／below、terminal above／below／range；分開截止與結算日期 |
| 委託簿 | 原始深度、價差、來源及接收時間、資料品質阻擋 |
| 外部現貨 | Coinbase ticker；Kraken 帶來源時間的 Recent Trades 備援 |
| 特徵 | 有效 mid、深度、動能、現貨距離與品質旗標；缺失成交量為 null |
| 歷史 | 顯示買價讀取、逐日 CLI、快取、去重及獨立資料表 |
| 儲存 | 非破壞性 schema v2 升級、每次連線外鍵、metadata 歷程 |
| 運作 | 共用節流、有限重試、逐輪錯誤復原、可指定輪數與資料庫 |
| 驗證 | 55 項測試通過；離線端到端、美國版三輪即時觀察、單合約 31 天資料收集 |

目前 US CLI 只輸出 WATCH／NO_TRADE，不產生方向性交易訊號；`fair_probability=null`。

尚未完成：此工作環境的外部現貨連通性、完整交易時段的穩定性、BRTI 判定歷史、逐筆成交流、勝率模型、風控執行、paper／shadow／live 與 LLM。長時間執行能力不能視為已通過長時間驗收。

原國際版解析器僅保留歷史回歸相容性。實際結果、限制與重現方式見 [US_VALIDATION_REPORT.md](US_VALIDATION_REPORT.md)。
