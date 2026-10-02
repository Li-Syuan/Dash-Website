# 營運中心交接（新增增量）

## 一個啟動入口

沿用 `python -B app.py`。新功能位於 `/QA_portal/operations`；原 QSL 彈窗留在 `/QA_portal/maintenance`。共同 Flask-Login 身分，沒有第二次登入或獨立網站。

功能狀態唯一索引：[FEATURE_TODO.md](../../FEATURE_TODO.md)。狀態與測試證據請從這裡看，避免分散閱讀。

## 操作順序

1. 用合成帳號 `demo-admin` 登入。
2. 進「營運中心 / TODO」。
3. 改動影響：輸入登錄的來源 ID 或報表 ID，選擇欄位後預覽；結果依已登錄的依賴關係，不宣稱自動掃描整個公司系統。
4. 版本差異：比較 `monthly-performance` 第 1、2 版，查新增／刪除／異動紀錄，再輸入來源 ID 查原始列。
5. 需求去重：輸入關鍵字，依目錄說明／標籤／欄位給出相似度與重用方向。沒有傳給外部模型。
6. 品質攔截：選故障快照後測試 mock 寄送，應被拒絕；選正常快照才允許。真 SMTP 尚未接線。
7. 統計：只顯示實際事件數，不虛構節省時間。
8. 多表目錄：每頁 20 個定義，共 100 個合成定義。SQLite 可用；Oracle 僅契約且明確未接線。

## 原系統整合

- 維護表由可信任的 Python 設定註冊，沿用 `model / init_db / bind / model_config` 概念。
- `bind` 必須與 ORM model 的 `__bind_key__` 一致，不能讀 SQLite 卻寫到另一個 Oracle bind。
- 註冊表只存資料結構／adapter；每個 request 重算權限，不把使用者、資料列或 CRUD 權限放到模組全域。
- 公司 adapter 由整合者注入；不接受瀏覽器傳入連線字串、SQL、Python 模組名稱或角色。
- 100 個合成定義證明註冊／查詢分頁與隔離；不是 Oracle 負载認證。
- 原版 QSL 五欄唯一鍵、欄位鎖定、彈窗及安全還原照舊。泛型維護模組並不會自動取代全部原維護頁，應按每張表的業務鍵／欄位規則註冊。

## 持久化與限制

新營運資料使用獨立 SQLite 檔，與既有 workspace／QSL 資料分開。指定 `REPORTING_STATE_PATH` 時，營運目錄為該路徑加 `.operations`。備份需在應用停止時涵蓋原 store、`.qa`、`.operations`。

僅 demo 模式掛入合成營運頁。公司環境需要明確 adapter 與非合成資料目錄，不能將 demo 身分映射搬去當公司授權。

## 驗收

- 自動測試：`python -B -m unittest discover -s tests -q`
- UI/HTTP 測試走實際 Dash callback 與 Flask session，不只呼叫 service。
- Oracle、公司帳號、LDAP、SMTP、實體 Windows 瀏覽器操作需另外驗收；未驗收不標 DONE。
- 不自動推送 GitHub、不自動部署、不永久刪除使用者資料。

## 最小接線驗收（Opus）

1. 在隔離分支中依現有 `SQLALCHEMY_BINDS` 建立可信任 adapter，不更動 bind key。
2. 註冊一張 SQLite 表與一張 Oracle 測試表，核對讀寫同一 bind。
3. 以 read-only、CRUD、錯組織、未登入四種身分測試實際 HTTP callback。
4. 先用少量合成資料驗證 Query→Update/Delete 彈窗、欄位鎖定、版本衝突與回滾。
5. 把報表／匯出／API／寄信的依賴元資料登錄；刻意移除欄位或加入重複鍵，確認 mock 寄送被阻擋。
6. 公司真寄送必須在正式 adapter 中再接同一品質 gate；單純建立營運頁不會自動保護舊 SMTP 呼叫。
7. 完成上述才擴大到其餘維護表；避免一次替換全部舊頁面。

### 泛型 adapter 的明確界線

保留 model/init_db/bind/model_config 註冊介面不等於舊 model 原封不動即可替換。此增量的泛型 adapter 需要單一整數主鍵及 org/version/deleted 伺服器欄位；舊模型若缺少這些欄位，必須由公司 adapter 映射，不能擅自改 schema。Oracle CAS 更新不自動觸發原 ORM mapper update 事件，因此寫入需要可信任、同一交易的 audit_hook；沒有 hook 就拒絕寫入。LOB、複合主鍵、公司 trigger 與原審計事件仍需專用 adapter 測試。原 QSL 既有模組並未被取代。

## 自動 ETL 範例與監控

`python -B app.py` 是唯一啟動入口，同時啟動本機排程輪詢器；範例預設停用，由營運中心管理者啟用（預設每 300 秒，允許 60–86400 秒）。僅匯入 app／create_app 不啟動排程。程式離開會呼叫 stop；故障恢復與多程序爭用由持久化 lease 防重複處理。

範例固定執行 source_snapshot → clean_validate → atomic_publish，寫入合成 SQLite 結果。监控含 run ID、每一步狀態/筆數/耗時/錯誤代碼、下一次執行、最近成功與資料距今時間。上游失败標示下游 skipped，新批次失敗保留上次成功結果。這是新範例的失敗政策，沒有改動公司原本「先繼續再彙整錯誤」的 pipeline。

此頁的「立即執行」只處理固定安全範例。沒有任意程式碼或 SQL 排程，也不呼叫外部服務。公司正式 ETL 要經可信任 adapter 註冊與部署驗收，不可只用啟用按鈕當作已整合。

### 同頁診斷匯出

從 Job 執行紀錄複製 run_id，按「下載去識別診斷 ZIP」。套件只有 diagnostic.json 與 README.txt，限定所選批次的允許欄位（步驟、狀態、錯誤代碼、筆數、時間、設定版本、固定報表參照）。不匯出環境變數、憑證、session/lease token、原始資料列或完整 traceback。不存在或無權限的批次不產生檔案。

### Job owner 失敗通知

同頁設定合成 owner 信箱（只接受 example.invalid）與 mock 通知開關。每個 run 去重，連續失敗合併，通知結果獨立保存；寄送模擬失敗不重新跑 ETL。通知只含固定 job 名稱、失敗步驟、時間、安全錯誤代碼與監控入口，不附資料列、憑證、環境或原始 traceback。這不是真 SMTP，不宣稱收件人收到；接正式郵件需額外 provider 與公司驗收。

### 排程儲存部署界線

只支援同主機本機 SQLite rollback-journal；不宣稱 NFS、叢集或 WAL 相容。lease sidecar 與主 job DB 都要備份。錯過多個 tick 合併處理，重啟中斷的批次標記失敗而不自動重播。實際公司 ETL 仍需專用可信任 adapter；不能把固定安全範例當作任意公司作業執行器。
