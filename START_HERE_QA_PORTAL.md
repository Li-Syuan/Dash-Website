> 新增功能與 DONE 證據：[功能清單](FEATURE_TODO.md)；[營運中心交接](docs/opus/OPERATIONS_HANDOFF.md)。

> 維護表已恢復獨立 Create / Update / Delete / Upload 彈窗，Update/Delete 先 Query ID。見[原版操作對照](docs/opus/ORIGINAL_CRUD_PARITY.md)。

# QA Portal：從主站登入開始

**現在只要啟動 `app.py`。** 本包的報表目錄、管理台與 QSL 維護／報表精靈已整合在同一個本機主站，共用一次登入。
這是合成資料的離線整合版；**尚未接入公司正式系統、尚未部署，沒有真實寄信或正式授權異動**。

## 1. 啟動主站

在新解壓的資料夾中，啟用相容的隔離 conda／Python 3.8 環境。若該環境尚未安裝依賴：

```powershell
python -m pip install -r requirements-qa-portal.txt
python -B app.py
```

Windows 也可在啟用環境後執行 `START_QA_ENHANCEMENTS.cmd`，它同樣啟動主站 `app.py`，不會自動安裝或升級套件。

1. 開啟 `http://127.0.0.1:8050/login`。
2. 使用 `demo-admin`／`demo-only` 登入。
3. 點上方「QSL 維護 / 精靈」，或報表目錄中的「QSL 維護與報表精靈」。
4. 進入 `http://127.0.0.1:8050/QA_portal/maintenance`；先按「重新查詢」。

不要使用歷史 `requirements.txt` 升級公司環境。舊資料庫範例 `test_admin.py` 已移除，測試請用下方 unittest 命令。預設只綁定 loopback；不要公開部署公開測試帳號。

## 2. 帳號與功能

以下三個公開測試帳號的密碼都是 `demo-only`，僅供本機合成資料使用：

- `demo-admin`：組織 A，可查詢／匯出、CRUD、匯入、版本還原、保存報表定義及操作 mock 郵件。
- `demo-user-a`：組織 A，唯讀查詢／匯出及報表預覽；不能修改、匯入、讀取歷史或保存定義。
- `demo-user-b`：組織 B，拒絕進入此 QSL 頁與其資料 callbacks。

本頁使用主站 Flask-Login session，沒有第二次選身分或另一套登入。這個 demo 的管理員映射不等於公司正式 admin 擁有 CRUD 權限。

同一頁包含既有 QSL 新增／修改／刪除、CSV／XLSX 匯入匯出，以及新增的修改差異確認、歷史還原、四步報表精靈和 mock 郵件流程。舊報表目錄、管理台、權限、排程設定、稽核與主題仍保留。

- 修改：Update 彈窗 → Table ID → Query → 修改欄位 → Submit to update。差異預覽另可選用。
- 還原：載入歷史 → 選版本 → 預覽差異 → 確認還原為新版本。
- 匯入：上傳 CSV／XLSX → 檢查暫存摘要 → 提交；整批重新驗證，有錯誤時回滾。
- 精靈：選來源 → 選欄位 → 設篩選／分組 → 預覽後保存。

`examples/qa_portal/qsl_new_rows.csv` 和 `.xlsx` 可直接匯入。兩筆為同供應商不同城市的合成資料，用來檢查五欄唯一鍵；同一批再次上傳應被拒絕為重複資料。

## 3. ETL 調度中心

同一登入開啟 `/QA_portal/etl`（導覽「ETL 調度中心」）。

- 管理員選擇已註冊的合成 job，查看步驟依賴，再手動執行或啟用間隔排程。
- 每次執行會列出步驟狀態、耗時、筆數及安全錯誤代碼。失敗時保留最近成功發布。
- 安全可重試的失敗批次可建立重試，保留已成功上游步驟的來源鏈；不會把中斷／結果不確定的批次默默重播。
- 日期補跑先確認區間，最多 31 個日期。未來日期與反向區間會拒絕。
- `demo-user-a` 只能查看；組織 B 無法讀取 A 的工作。服務層及 HTTP callback 都重新檢查身分與權限。

原營運中心的固定範例、診斷 ZIP 和 mock 通知仍在 `/QA_portal/operations`。
詳見 [ETL 整合指南](docs/etl/INTEGRATION.md)。這版沒有接上公司 ETL 或 SMTP。

## 4. 保存位置

主站預設保存至本包的 `instance/workspace.sqlite`；QSL 相關資料放在它的同層目錄 `instance/workspace.sqlite.qa/`：

- `qsl.sqlite`：合成 QSL、稽核、歷史與暫存 token
- `report_builder.sqlite`：報表定義
- `jobs.sqlite`：mock 排程／郵件設定與紀錄

ETL 調度中心另存於 `instance/workspace.sqlite.etl.sqlite`；營運中心在 `instance/workspace.sqlite.operations/`。兩者同樣需要備份。

若指定 `REPORTING_STATE_PATH`，ETL 檔名為該完整路徑加 `.etl.sqlite`；QSL 目錄就是該完整檔案路徑加上 `.qa`。重啟保留資料，預設重啟後須重新登入。升級前停服務並備份主站資料庫、整個 `.qa`／`.operations` 目錄及 `.etl.sqlite` 檔案；不要使用公司真實資料或網路磁碟。沒有設定持久化路徑的 factory 測試只使用臨時 QSL 目錄。

## 5. 交給 Opus 整合公司原始碼

先讀 [主站整合與公司接入指南](docs/opus/MAIN_APP_INTEGRATION.md)。本包的 `app.py` 與公司原始 `app.py` 是不同來源，**不要直接覆蓋公司專案**。
公司登入、原權限、callback、路徑、Oracle adapter 與分階段驗收仍需按實際原始碼接入。production factory 不會自動載入這個合成 QSL 頁。

[既有公司規則與 adapter 交接](docs/opus/INTEGRATION.md) 保留原始整合約束；[修改／歷史／精靈操作說明](docs/opus/REVISION_WIZARD_UPGRADE.md) 提供詳細流程。詳細服務規則與操作仍適用；啟動、登入與保存位置以本文件為準。

## 單一入口與舊資料

不需要啟動其他 Python 網站或切換第二套身分。本次清理移除多餘啟動器，所有使用者功能從 `app.py` 進入。移除清單與復原說明見 [REMOVED_FILES.md](docs/opus/REMOVED_FILES.md)。舊獨立版本的 `instance/qa_portal_demo/` 不會自動遷移到主站；如需保留舊資料，先備份並由 Opus 審核身分與報表所有權映射。

## 驗證與發佈界線

測試入口：`python -B -m unittest discover -s tests -v`。實際執行結果與未驗證項目看 [VALIDATION.md](VALIDATION.md) 和 [Opus 驗證紀錄](docs/opus/VALIDATION.md)；本啟動指南本身不代表測試通過或正式驗收。

交付 ZIP 不含 `.git`、實際 SQLite、credentials、真實公司資料或原聊天內容，只有合成 fixture。未對 Git remote push，未 publish/deploy。
