# 本輪變更、遷移與回退

本輪基於已推送且 CI 成功的第 9 版
`a35898344c752e6c41fab0afdfd81d7d6ebdb617`，僅在獨立本機 worktree 續作。
本輪已另獲使用者批准正常推 main 與 GitHub CI 驗證；不 force push、不部署。
本文件說明已完成的變更，CI 結果須以本輪實際 commit/run 為準。

## 產品程式

`reporting_workspace/legacy_crud.py` 增加一個 SQLite partial index：

```sql
CREATE INDEX IF NOT EXISTS legacy_qsl_active_order
ON legacy_qsl_records(target, id) WHERE deleted=0;
```

它供既有 target、active-row、ID 排序查詢使用，去掉暫存排序。沒有新增
schema 欄位、修改 row/version/audit、改商業唯一鍵、改 filter/order/limit、
放寬授權、改 callback/UI 或更新依賴。owner/admin、跨租戶拒絕、CSV escaping
與 XLSX literal-string 契約不變。[量測與代價](PERFORMANCE.md) 均有原始樣本。

既有 store 在原本 QSL 初始化時透過 IF NOT EXISTS 建立索引；可重入。
建立索引需要 SQLite schema write lock 及額外儲存空間，依既有停止服務、
備份完整 state 與 sibling stores 的程序升級，不在正式運作中盲目替換檔案。
不需要清空資料或重建記錄。回到舊程式時舊版可忽略新增索引，不必 DROP。
這僅是本機 SQLite adapter 的增量，不是公司 Oracle migration。

25,000-row fixture 的 DB 增加 696,320 bytes（3.63%）；一次性批次匯入 setup
增加約 5.77%，不是重複量測的寫入吞吐量。頁面查詢與 CSV 有實測改善，
文字子字串 filter 略慢，XLSX 的主要 XML 序列化成本沒有改善。

## 可執行測試與量測

- 新增 10 項 ETL 真實程序中斷與 SQLite 鎖定測試；保留已提交 publication、
  snapshot/receipt/fence 與未知狀態，不自動 replay。原 ETL runtime 已滿足
  這些案例，沒有為了產生程式差異而改寫復原政策。
- 新增 8 項 maintenance 多程序一致性測試：同版本競爭、生命週期、
  owner/admin/tenant 邊界、lock timeout、audit constraint rollback、
  原子可見性、中止後重開，以及等待期間撤權。
- 新增 5 項 QSL 索引/內容契約測試，包含舊 store 初始化和再次開啟、排序、
  分頁、目標/刪除邊界、字面 filter、CSV/XLSX、匯出上限與即時 policy 撤權。
- `benchmarks/portal_latency.py` 與 `compare_latency.py` 可重現相同條件的
  query/export/Dash HTTP callback 前後數據；不安裝依賴或啟動外部服務。

## 交接文件

新增根目錄 `AGENTS.md`、`docs/PROJECT_CONTEXT.md`、`docs/AGENT_HANDOFF.md`，
保留並引用既有 `.github/copilot-instructions.md`。只收錄使用者提供的 Dash
資訊，將目標版本/實測版本、歷史排程/實際配置、公司技術/缺少的實作分開。
README 提供接手入口，`.gitignore` 排除本機 browser/probe output 與 pytest cache。
第 9 版 checkpoint/CI、完整回歸、真實瀏覽器、benchmark 各自保留證據，
不能把 HTTP 測試或舊版本 CI 冒充本輪瀏覽器或未推送程式的 CI。
