# 本輪變更與推送驗收清單

基底 main：`a35898344c752e6c41fab0afdfd81d7d6ebdb617`。
工作分支：`codex/recovery-concurrency-performance`。
本清單在建立本輪 commit 前產生。使用者已另行明確批准本輪正常推 main，
並以 GitHub CI 完成 Python 3.8/3.10 驗證；不 force push、不部署或擴大範圍。
`PENDING_PUSH` 檔名保留以便對照已交付的候選包，代表提交前的逐檔清單。

| 區域 | 待推內容 |
| --- | --- |
| 產品程式 | `reporting_workspace/legacy_crud.py` 兩行 additive partial index；唯一產品程式差異 |
| 23 個新案例 | `tests/test_etl_recovery_faults.py` 10 個；`tests/test_concurrency_consistency.py` 8 個；`tests/test_legacy_performance_contracts.py` 5 個 |
| 重現與量測 | `benchmarks/portal_latency.py`、`compare_latency.py`；三份完整前後量測 JSON；`tests/probes/clock_guard.py` |
| 專案接手 | `AGENTS.md`、`docs/PROJECT_CONTEXT.md`、`docs/AGENT_HANDOFF.md`、README 入口、`.gitignore`；`.gitattributes` 保留原始證據 bytes |
| 變更與證據 | `docs/optimization/` 的 recovery/concurrency/performance、遷移、結果矩陣、CI 驗收、第9版checkpoint、本輪完整文字證據與此清單 |

逐檔相對路徑、狀態、bytes 與 SHA256 見 [PENDING_PUSH.json](PENDING_PUSH.json)。
該 JSON 不列自己的 hash，避免自我雜湊循環；交付包的總 manifest 會包含它。
不將 `output/`、資料庫、instance、環境、credentials、runtime、二進位截圖
或下載物加入 Git。完整截圖／XLSX 另有證據 ZIP，文字證據已去除私人路徑。
證據專用 attributes 保留 CRLF、CSV BOM 與 DOM 排版空白，避免 Git 改變
已記錄的 SHA256；產品程式、測試與文件的 whitespace 檢查仍照常執行。

## 已批准的推送與驗收步驟

1. 保留本輪範圍、效能得失、Windows/Chrome 結果，以及 WSL exit 86。
2. 本輪 main 推送已批准；檢查遠端是否前進，若有則審閱新差異，
   不覆蓋使用者變更或 force push。
3. 使用既有 [CI 配置與驗收準則](CI_ACCEPTANCE.md)；完整兩個 Python matrix
   工作都成功後，才補上正常 Linux 環境的回歸結果。
4. 保留原 WSL 無效紀錄；不要為改善結果再跑同一不穩定環境或調整時間服務。

現存 requirements、CI workflow、UI/harness、ETL 與 maintenance 產品交易邏輯
均未修改。CI 預期每個 Linux job 執行901案例；本清單建立時 CI 尚未執行，推送後結果另附。
