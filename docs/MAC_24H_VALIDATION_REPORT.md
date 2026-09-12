# Mac 24 小時唯讀 Campaign 驗證報告

測試日期：2026-09-10 至 2026-09-11（America/Los_Angeles）  
UTC 觀測期間：2026-09-11 01:16:28 至 2026-09-12 01:16:28  
Campaign ID：`8df0dda32273b72fee7ebfadf516a8f5`  
Session ID：`07452075-8e0f-4771-b78a-042d1b35bba3`  
上傳封存 SHA-256：`6ece709950f4a9ad9c3726a0fc8340ca3a0ecdbd1f937a728a38a59c8850611b`

## 結論

24 小時正式唯讀 campaign 驗收通過。單一 24 小時分卷完成 8,636／8,636 輪，沒有整輪失敗或單一市場失敗；SQLite integrity check、外鍵、event hash chain 與 session artifact SHA-256 全部通過。

本次 `provenance_status=clean_git`，執行來源為已推送的 Git commit `58be9402e5f1fa0d46a60e54caa1549f404b75b5`，工作樹沒有未提交變更。因此本資料可作為後續連續 30 天的可重現**資料品質／市場結構研究**基準。

這不是勝率模型、策略獲利、模擬成交或實盤驗收。程式沒有讀取 Polymarket 帳號、錢包或私鑰，沒有送出訂單，也沒有呼叫 GPT／LLM。

## Campaign 與封存完整性

| 項目 | 結果 |
|---|---:|
| 指定時長 | 86,400 秒 |
| 實際 session 時長 | 86,431.711 秒 |
| 分卷／attempt | 1／day-001-attempt-001 |
| Campaign outcome／healthy | completed／true |
| Session outcome／healthy | completed／true |
| Event chain | 3 個事件；sequence、previous hash、head 全部一致 |
| session manifest 成品 SHA-256 | 5／5 全部一致 |
| SQLite schema | v3 |
| SQLite integrity／外鍵錯誤 | ok／0 |
| 資料庫／日誌大小 | 839 MB／146 MB |

Campaign 使用 40 GiB 起始空間門檻及 5 GiB 保留門檻；本次正常完成，沒有觸發低磁碟暫停。

## 運作與連通性

| 指標 | 結果 |
|---|---:|
| 成功輪數 | 8,636 |
| 失敗輪數 | 0 |
| 單一市場失敗 | 0 |
| 結果／決策 | 82,460 |
| 結構化日誌行數 | 303,582 |
| 已完成 HTTP 回應 | 97,588，全部 HTTP 200 |
| WARNING | 22；全部為可恢復的 Coinbase timeout |
| ERROR／CRITICAL | 0／0 |
| 市場搜尋 | 288 次；10 個市場 watchlist 275 次，13 次暫時為空 |

Coinbase BTC 發生 14 次、ETH 發生 8 次 timeout；全部由 Kraken 備援補回。外部價格合計 17,272 筆：BTC Coinbase 8,622／Kraken 14，ETH Coinbase 8,628／Kraken 8。每一成功輪都有可用的 BTC 與 ETH 外部參考價格。

## 已保存資料與市場品質

| 項目 | 筆數 |
|---|---:|
| 市場／metadata／特徵 | 16／82,460／82,460 |
| 市場快照 | 80,028 |
| 原生＋合成委託簿快照 | 160,056 |
| 外部價格 | 17,272 |
| WATCH | 1,731（2.10%） |
| NO_TRADE | 80,729（97.90%） |
| 原生 OPEN／HALTED 委託簿 | 79,616／412 |
| 含資料問題的特徵 | 61,817（74.97%） |
| 委託簿過期品質旗標 | 55,366（67.14%） |
| 暫停等待、未重新讀取 | 2,432 |

各穩定市場的觀測間隔中位數約 10 秒，符合預期輪詢。部分市場的最大間隔達 3,912–4,814 秒，原因是當時動態 discovery watchlist 暫時移除或未提供該市場；並非 scanner 停止。獨立日誌證實整個 campaign 共完成 8,636 輪。

儲存的決策原因可重疊，主要是委託簿時間過期、缺少雙邊報價／交叉、深度不足與 spread 超限。這些品質門檻正確地阻擋了多數 NO_TRADE；不得為了提高 WATCH 比例而直接放寬門檻。下一步應先在 30 天資料上量化各合約／時段的資料新鮮度與深度可用率，再決定是否調整市場選擇或資料處理政策。

## 驗收決策與下一步

- 24 小時公開 API 連通、持續運作、備援、封存與資料庫完整性：**通過**。
- 可重現來源版本：**通過**（clean Git commit）。
- 行情品質可直接支持勝率建模或交易：**未通過／尚未評估**；NO_TRADE 多數由安全資料門檻產生。
- BRTI 結算標籤、逐筆成交、fair probability、費用／滑價、paper／live execution：**尚未完成且不啟用**。

下一個操作步驟是在同一乾淨 commit 以 `polymarket-campaign --days 30 --session-hours 24 --max-markets 10` 收集 30 個獨立每日分卷。完成後再先做資料品質與標籤可得性評估，不直接啟用交易。
