# 本輪完整驗證（本機候選 v10）

本輪基於第 9 版 commit `a35898344c752e6c41fab0afdfd81d7d6ebdb617`。
產品差異只有 QSL 的 additive partial index；ETL、maintenance transaction、
身分/授權、UI/harness、依賴 pins 均保持既有行為。新增 23 個 unittest cases、
可執行 benchmark、只讀 clock guard 和完整專案交接。
以下是本機驗證的原始紀錄。使用者已另行批准本輪推 main 並以 GitHub CI
驗證；CI 的實際 commit/run 結果另附，不以第 9 版 CI 代替，也不部署。

## 結果矩陣

| 項目 | 實際結果 | 證據 |
| --- | --- | --- |
| Windows Python 3.10.22 完整回歸 | **900 PASS、1 SKIP、0 FAIL/ERROR**；901 cases，99.330 秒 | `evidence/windows-python310-final.json` / `.txt` |
| WSL Python 3.8.20 完整 unittest | **901 cases 顯示 OK**，101.297 秒；但下面 clock gate 失敗，**不可列為合格的乾淨回歸** | `evidence/wsl-python38-final.json` / `.txt` |
| WSL 只讀 clock gate | **環境無效，exit 86**；3 次 realtime 倒退，monitor errors 0；不重試 | `evidence/wsl-python38-final-clock.jsonl` |
| 原生 Chrome 154.0.8037.95 | **32 PASS、0 FAIL、0 UNRUN**；單次 full suite，不重跑 | `evidence/browser/summary.json` / `results.json` |
| 新增 ETL 故障復原 | **10/10 PASS**，29.510 秒；已包含於901 cases | [RECOVERY.md](RECOVERY.md) |
| 新增多程序一致性 | **8/8 PASS**，7.457 秒；已包含於901 cases | [CONCURRENCY.md](CONCURRENCY.md) |
| 新增 QSL 索引/輸出契約 | **5/5 PASS**；已包含於901 cases | [PERFORMANCE.md](PERFORMANCE.md) |
| 效能前後比較 | 條件/fixture/harness hashes 相符，14 scenarios，保留所有樣本與退步項目 | `benchmarks/evidence/` |
| 實際公司 RESTX | **未跑**；實作與版本不在提供的 source，只有先前16項共用Flask檢查 | [RESTX 範圍](../authorization/FLASK_RESTX_COMPATIBILITY.md) |
| 目標企業 Linux/Python精確patch、Oracle/LDAP/SMTP、正式部署 | **未跑** | [專案脈絡](../PROJECT_CONTEXT.md) |

Windows 唯一 skip 是平台沒有 fork；WSL 執行了該案例與 Node 測試。
兩個完整 unittest run 都無 unraisable exception 或 shutdown traceback。
程式/測試來源在 run 前後 hash 一致；32項瀏覽器的55個 runtime/assets hashes
與凍結來源一致。901 是 unittest cases；其內的263授權子檢查、新增23 cases
不能重複加總。兩個 full unittest 都只執行一次。

## WSL 環境限制

本輪觀察到與第9版診斷相同類型的時鐘倒退，約0.64–0.75秒。此次沒有改
systemd-timesyncd、host/guest clock、網路、安全設定或 cookie/signer；
時間同步服務前後都是 active/running/enabled。Guard 採用獨立系統時鐘，
任何倒退永久將該次 run 判為無效，即使 unittest 自己回傳0。

因此本輪 **Python3.8程式案例結果是OK，但完整驗收gate未通過**。保留所有
原始結果，不暫停服務、不用mock時鐘、不重試來產生較好紀錄。後續應在
正常穩定的核准Linux環境重跑相同命令；永久WSL/host校時維運不屬本輪。

## 真實瀏覽器範圍

沿用凍結的 native harness，`QA_STATIC_BRIDGE=0`、`QA_HTTP10=0`。涵蓋登入、
owner/peer/跨租戶、CRUD彈窗取消/關閉/再開啟、連點、版本衝突、撤權Save、
QSL CRUD/upload/history、精靈上/下一頁、browser back/forward、行動版、
ETL/operations畫面、Plotly12個SVG長條及真實CSV/XLSX下載。
report.csv含12列、qsl.csv含15列，XLSX ZIP完整。

pageErrors0、unexpected console0、其他requestfailed0。45個ERR_ABORTED
導航取消完整保留，未更改分類規則；有3次真實401與5個預期授權console訊息。
fixture已停止，合成state已移除，沒有啟動正式排程或外部服務。
完整截圖與下載物另存證據分包；原始DOM與結果保留，不以HTTP replay替代UI。

## 效能與程序復原

量測在其他重負載測試暫停時完成，5,000與25,000筆、3 warmups + 15 samples。
25,000筆查詢median5.810→2.097ms，Dash查詢callback10.022→4.461ms；CSV
222.514→187.483ms。文字filter略慢、DB增加3.63%、單次import setup增加5.77%，
XLSX約2.54秒沒有實質改善。這些是合成service/HTTP數據，不是browser latency
或企業負載SLA。完整median/p95、原始樣本及成本見 [PERFORMANCE.md](PERFORMANCE.md)。

ETL及maintenance既有交易邏輯通過新增故障案例，沒有為製造差異而改寫安全
復原政策。unknown/interrupted不自動replay；同版本多writer只有一個成功，
資料與成功audit一起提交或回滾。外部SMTP/Oracle exactly-once仍未認證。

## 重現與下一步

啟動、完整/指定回歸、browser、benchmark及只讀clock guard命令見
[AGENT_HANDOFF.md](../AGENT_HANDOFF.md)。修改與遷移見
[CHANGES_AND_MIGRATION.md](CHANGES_AND_MIGRATION.md)。

剩餘限制：穩定Linux環境的合格gate、缺少公司RESTX和其他私有adapter、精確
目標OS/patch、斷電/磁碟損毀或滿載/NFS/多主機及實際企業負載。
本輪推 main 與 GitHub CI 驗證已獲明確批准；保留以上 WSL 無效紀錄，
不再調整時間服務或在同一不穩定環境重跑。本輪批准不延伸至下一輪或部署。
