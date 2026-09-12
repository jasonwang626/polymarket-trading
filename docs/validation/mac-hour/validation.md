# 唯讀掃描資料驗證報告

觀測期間：2026-09-08T05:29:21.419564+00:00 至 2026-09-08T06:29:13.754241+00:00

完整性：ok；外鍵錯誤：0
特徵筆數：3600；分類：{'NO_TRADE': 2924, 'WATCH': 676}

| 市場 | 觀測筆數 | WATCH | NO_TRADE | 最長觀測間隔（秒） |
|---|---:|---:|---:|---:|
| When will Bitcoin cross $100k again? — Before October 2026 | 360 | 0 | 360 | 11.515 |
| When will Bitcoin cross $100k again? — Before November 2026 | 360 | 0 | 360 | 11.921 |
| When will Bitcoin cross $100k again? — Before December 2026 | 360 | 0 | 360 | 11.723 |
| When will Bitcoin cross $100k again? — Before January 2027 | 360 | 0 | 360 | 12.152 |
| When will Bitcoin hit $150k? — Before October 2026 | 360 | 0 | 360 | 13.841 |
| When will Bitcoin hit $150k? — Before November 2026 | 360 | 0 | 360 | 12.747 |
| When will Bitcoin hit $150k? — Before December 2026 | 360 | 0 | 360 | 12.640 |
| When will Bitcoin hit $150k? — Before January 2027 | 360 | 339 | 21 | 12.691 |
| Will Bitcoin be above ___ in 2026? — $200,000 | 360 | 186 | 174 | 15.332 |
| Will Bitcoin be above ___ in 2026? — $250,000 | 360 | 151 | 209 | 12.869 |

原生委託簿狀態：{"MARKET_STATE_OPEN": 3600}

有效外部價格筆數：{"BTC-USD/coinbase": 178, "ETH-USD/coinbase": 277, "ETH-USD/kraken": 77, "BTC-USD/kraken": 161}

限制：

- 資料庫中的有效外部價格不包含被拒絕的來源回應；拒絕原因需搭配日誌。
- 觀測間隔不是網路延遲；資料庫不能證明預期輪數或 HTTP 成功率。
- 品質旗標可能重疊；暫停等待的特徵不是新行情快照。
- 這是已保存資料的品質稽核，不是策略重播、成交率或損益回測。
