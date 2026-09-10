# 30 天唯讀資料收集操作手冊

## 安全邊界

`polymarket-campaign` 只呼叫公開 GET 行情與外部現貨來源，不讀取 Polymarket 帳號、不接受私鑰、不連錢包、不建立委託，也不呼叫 GPT／LLM。它的輸出是研究資料，不是已驗證的交易策略或獲利證明。

## 先做 24 小時驗收

在全新或乾淨的 repository checkout 執行：

```bash
git status --short
git rev-parse HEAD
df -h .
caffeinate -dimsu uv run --frozen polymarket-campaign \
  captures/mac-24h-01 \
  --days 1 \
  --session-hours 24 \
  --max-markets 10
```

第一個命令必須沒有輸出。工具也會自行阻擋 dirty／未知 Git 來源。預設要求啟動時至少 40 GiB 可用空間；依 8 小時實測推估，10 個市場的 30 天未壓縮資料約需 35 GiB，40 GiB 是最低操作門檻而非保證值。

Mac 應接上電源、保持穩定網路並預留散熱；`caffeinate` 只在命令執行期間防止系統睡眠，不會關閉或重啟電腦。

完成後須確認 `campaign.json` 的 `healthy=true`、`completed_sessions=1`，且子目錄 `session.json` 的 `provenance_status=clean_git`。若未通過，保留整個目錄，不要手動編輯檔案。

## 開始 30 天 campaign

```bash
caffeinate -dimsu uv run --frozen polymarket-campaign \
  captures/mac-30d-01 \
  --days 30 \
  --session-hours 24 \
  --max-markets 10
```

每一天寫入 `day-NNN-attempt-NNN/`。成功完成 30 卷後才建立最終 `campaign.json`。`campaign-start.json` 保存固定參數、設定雜湊與 Git commit；`events.jsonl` 以 sequence、previous hash 與 event hash 串接所有嘗試。

## 中斷與續跑

Ctrl-C、網路／程式錯誤或重新開機後，使用完全相同參數並加 `--resume`：

```bash
caffeinate -dimsu uv run --frozen polymarket-campaign \
  captures/mac-30d-01 \
  --days 30 \
  --session-hours 24 \
  --max-markets 10 \
  --resume
```

取消或失敗的分卷保留原狀；續跑建立新的 attempt 目錄，不會覆寫舊資料。若 commit、設定或命令參數不同，工具拒絕續跑。低於 5 GiB 可用空間時結果為 `paused_low_disk`；釋放同一磁碟的空間後，再以相同命令續跑。

不要修改 JSONL、manifest 或 session 內容，也不要把兩個 campaign 目錄合併。若雜湊鏈檢查失敗，先完整備份該目錄，再調查儲存裝置或不完整寫入原因。
