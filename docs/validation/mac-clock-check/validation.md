# 唯讀掃描資料驗證報告

觀測期間：2026-09-08T06:37:25.511401+00:00 至 2026-09-08T06:42:16.845772+00:00

完整性：ok；外鍵錯誤：0
特徵筆數：300；分類：{'NO_TRADE': 300}

| 市場 | 觀測筆數 | WATCH | NO_TRADE | 最長觀測間隔（秒） |
|---|---:|---:|---:|---:|
| When will Bitcoin cross $100k again? — Before October 2026 | 30 | 0 | 30 | 10.131 |
| When will Bitcoin cross $100k again? — Before November 2026 | 30 | 0 | 30 | 10.331 |
| When will Bitcoin cross $100k again? — Before December 2026 | 30 | 0 | 30 | 10.155 |
| When will Bitcoin cross $100k again? — Before January 2027 | 30 | 0 | 30 | 10.156 |
| When will Bitcoin hit $150k? — Before October 2026 | 30 | 0 | 30 | 10.193 |
| When will Bitcoin hit $150k? — Before November 2026 | 30 | 0 | 30 | 10.114 |
| When will Bitcoin hit $150k? — Before December 2026 | 30 | 0 | 30 | 10.110 |
| When will Bitcoin hit $150k? — Before January 2027 | 30 | 0 | 30 | 10.133 |
| Will Bitcoin be above ___ in 2026? — $200,000 | 30 | 0 | 30 | 10.134 |
| How high will Bitcoin get this year? — Above $99,999.99 | 30 | 0 | 30 | 10.119 |

原生委託簿狀態：{"MARKET_STATE_HALTED": 299, "MARKET_STATE_OPEN": 1}

有效外部價格筆數：{"BTC-USD/coinbase": 30, "ETH-USD/coinbase": 30}

限制：

- 資料庫中的有效外部價格不包含被拒絕的來源回應；拒絕原因需搭配日誌。
- 觀測間隔不是網路延遲；資料庫不能證明預期輪數或 HTTP 成功率。
- 品質旗標可能重疊；暫停等待的特徵不是新行情快照。
- 這是已保存資料的品質稽核，不是策略重播、成交率或損益回測。
