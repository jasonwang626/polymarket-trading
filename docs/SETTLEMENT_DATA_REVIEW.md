# 結算資料取得與驗證狀態

查核日期：2026-09-08。

Polymarket US 官方提供 `GET /v1/markets/{slug}/settlement`，文件回應欄位為 slug 與 settlement，可透過固定公開 gateway 路徑查詢。[官方端點文件](https://docs.polymarket.us/api-reference/markets/get-market-settlement)

一般勝出／落敗合約以 $1／$0 結算，但部分市場有其他結算條款，需依合約 Settlement Description 判讀。結算流程在結果確定後開始。[官方結算說明](https://docs.polymarket.us/learn/markets/contract-settlement)

CF Benchmarks API 需要授權取得的帳號與 API key；歷史 values 另要求對應指數及 STREAM_HISTORICAL_VALUES 權限，最新歷史值可能延遲。此專案未取得 BRTI 歷史資料授權，沒有接入相關憑證或嘗試繞過權限。[API 存取說明](https://docs.cfbenchmarks.com/api/)、[歷史值文件](https://docs.cfbenchmarks.com/api/rest/historical-values/)

## 本次實作

- collect_settlement_evidence.py：限制 1–20 個不重複 BTC slug，只使用公開 GET、共用速率限制及有限重試。
- 依序取得市場描述與獨立 settlement 回應；任何來源失敗便停止該市場後續請求，401／403／451 停止整批，不切換路徑規避。
- 保存請求／接收時間、固定來源 URL、JSON payload 與 canonical JSON SHA-256；不覆寫既有資料夾。雜湊只能偵測內容變更，不能證明上傳文件來自官方。
- 驗證來源 URL、商品 slug、規則可解析性、時間順序、payload hash、結算值類型／範圍；特殊 0–1 間非二元值另行標記。
- 市場未明確 closed=true、active=false 時另記原因。這兩個欄位本身不證明結果已確認。
- 所有 evidence 保持 label=null、unverified、不可訓練；0/1 也只是 candidate_payout。

## 尚未通過的部分

在此執行環境直接 GET 已知 BTC 合約的 settlement 端點，8 秒內未取得回應（ReadTimeout）。未取得任何真實已結算 BTC 樣本。文件中的簡單回應未提供足以還原最早結果公布時間與特殊條款判讀的完整脈絡，因此本次不自動把回應提升為正式標籤。

下一步是用實際已結算合約核對這個端點、結算條款及結果生效／取得時間，並處理相互矛盾的結果紀錄；BRTI 逐秒路徑與觸價歷程則仍需合法資料存取。這些都是標籤驗收項目，不用行情最後價格或暫停狀態替代。

## 驗證

102 項測試通過；Ruff、Python 編譯、diff 格式檢查通過。新增測試涵蓋二元候選值不自動成為標籤、特殊結算、pending、開放市場、錯誤商品、錯誤來源、內容竄改、NaN／Infinity／布林等無效值、GET 限制與 403 後停止。

單元測試使用合成 HTTP 回應，不能視為實際端點連通性或已結算資料驗收。
