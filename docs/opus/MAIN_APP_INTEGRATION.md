> 維護表已恢復獨立 Create / Update / Delete / Upload 彈窗，Update/Delete 先 Query ID。見[原版操作對照](ORIGINAL_CRUD_PARITY.md)。

# 主站 app.py 整合與公司接入指南

## 目前交付範圍

本包只保留一個可執行入口：`python -B app.py`。報表目錄、管理台、權限／排程設定、稽核及 QSL 維護／報表精靈，均由同一個 Flask + Dash 主站提供。

本次完成的是**本包內的主站整合**。本包的 `app.py` 並非公司原始 Portal 的同一份程式；公司登入、Oracle、正式寄信及排程仍未接入。刪除多餘啟動器不會把合成 adapter 變成正式資料源，也不代表正式部署已完成。

## 唯一啟動方式

先在相容且隔離的 Python／conda 環境安裝 `requirements-qa-portal.txt`，不要升級公司現有環境：

```powershell
python -B app.py
```

開啟 `http://127.0.0.1:8050/login`，登入後由主導覽或報表目錄進入 `http://127.0.0.1:8050/QA_portal/maintenance`。Windows 的 `START_QA_ENHANCEMENTS.cmd` 執行相同命令。

`app.py` 直接呼叫 `reporting_workspace.application.create_app()`，不再經過 demo launcher wrapper。僅在直接執行時設定持久化預設及 15 MiB request-envelope 上限，以容納 QSL 上傳的 base64／JSON 包裝；已明確設定的 `REPORTING_MAX_CONTENT_LENGTH` 不會被覆蓋。一般 factory 預設仍為 1 MiB，匯入 `app.py` 不會執行這兩項 launcher 設定。

公開本機測試帳號密碼皆為 `demo-only`：

| 帳號 | 主站身分 | QSL 功能 |
| --- | --- | --- |
| `demo-admin` | admin，組織 A | 查詢／匯出、CRUD、匯入、歷史還原、保存報表定義、mock 郵件 |
| `demo-user-a` | user，組織 A | 查詢／匯出及報表預覽；不允許 CRUD、匯入、歷史與保存定義 |
| `demo-user-b` | user，組織 B | 拒絕頁面與資料 callbacks |

這些只是公開測試帳號。主頁與 QSL 使用同一個 Flask-Login session，沒有第二個身分選單、登入服務或獨立 8051 入口。預設只綁定 loopback，不能公開作為公司登入站。

## 已接上的程式位置

### 頁面、導覽與目錄

`reporting_workspace/web.py` 的 `create_dash_app` 建立 app-scoped `PageRegistry` 與 `CallbackRegistry`。只有 `runtime.is_demo` 時才加上 QSL 的 `SPEC`；production factory 不自動註冊這個合成頁。

`reporting_workspace/ui_pages/qa_maintenance.py`：

- `POLICY`：主站入場條件是已登入 **AND** role 為 admin/user **AND** org 為 A。
- `SPEC`：明確宣告 `page_id='qa-maintenance'`、路徑 `/QA_portal/maintenance`、導覽名稱及目錄 metadata；它是原目錄中的一頁。
- `layout(runtime)`：重用 QSL 表單與功能 panels，使用主站返回目錄與登出連結。
- `register_callbacks(callbacks, runtime)`：取得當前主站 Flask server，拒絕非 demo runtime，再建立合成服務與註冊受保護 callbacks。
- 內部 `user_resolver()`：從 `runtime.identity()` 讀取主站伺服端身分，先檢查 `POLICY`，再映射為 legacy 服務所需的 user 介面。無效身分一律回傳未認證使用者。
- `_RegisteredCallbacks`：先收集重用 UI callbacks；`flush()` 將共用 Output 的群組合併為單一 dispatch，再綁定主站 page registrar。每組附帶 `qa-maintenance.action-*` 識別與同一入場 policy，避免重複 Output 或直接掛到無授權的 `app.callback`。

這個測試 resolver 將 org A 映射為合成 `ORG_QA01`，並將主站 admin 映射為合成服務的 `is_dev`。這只是本機測試橋接，**不得直接套用為公司 admin 或組織的授權規則**。

### 共用功能與身分注入

`reporting_workspace/legacy_demo_ui.py` 保留為主站共用 UI 與隔離測試 factory，沒有網站啟動入口。其 `_register_services_and_callbacks(..., user_resolver=..., policy=...)` 接受主站提供的 resolver 與 server policy；它將 resolver 傳給 `_register_crud`、`register_enhancements` 與 mock job 授權。查詢、修改、匯入、版本、精靈及 mock mail 都重新解析伺服端使用者，不信任 `dcc.Store`、URL、表單 role 或另一套 cookie。

- `legacy_crud.py`：QSL 查詢／匯出、CRUD、暫存匯入、修改預覽、歷史及還原的服務層。
- `qa_enhancements_ui.py`：歷史 panel、四步精靈與 callbacks；共用 Output 的流程須先合併再註冊，不放寬 registry 的重複 Output 防護。
- `report_builder.py`：`ReportBuilderService`，固定資料源／欄位 schema 的驗證、預覽、保存與載入。
- `legacy_jobs.py`：設定、來源處理、產報與 mock 寄信狀態；沒有真實 SMTP 或背景排程。

服務層仍保留 legacy policy 的 entry／read／crud 判斷。主站 transport guard 保護 callback HTTP dispatch，registry 再檢查頁面與 callback policy，服務最後檢查操作與資料範圍；導覽可見性不是安全邊界。

`CallbackRegistry.for_page()` 限制 callback 所屬頁面；callback policy 不得弱於頁面 policy。`freeze()` 檢查是否存在繞過 registry 的註冊，`policy_for()` 對未知或被替換的 server callback 拒絕授權。不要為了接舊 callback 關閉這些 fail-closed 檢查。

## 本機保存與升級

`app.py` 的直接啟動預設使用本包目錄下的 `instance/workspace.sqlite`。QSL 服務使用 `runtime.settings.state_path + '.qa'`，因此預設是同層的 `instance/workspace.sqlite.qa/`：

| 儲存位置 | 內容 |
| --- | --- |
| `instance/workspace.sqlite` | 原主站維護、治理、稽核與本機執行狀態 |
| `instance/workspace.sqlite.qa/qsl.sqlite` | 合成 QSL、稽核、歷史、一次性預覽／匯入 token |
| `instance/workspace.sqlite.qa/report_builder.sqlite` | 使用者＋組織範圍的報表定義 |
| `instance/workspace.sqlite.qa/jobs.sqlite` | mock 設定與執行紀錄 |

可先設定 `REPORTING_STATE_PATH` 為絕對本機 SQLite 檔名；QSL 目錄會跟隨該完整檔名加 `.qa`，不接受從前端指定。沒有 state path 的 factory 測試使用臨時 QSL 目錄。預設 session key 每次啟動產生，重啟後需重新登入；持久資料不因此重設。

升級或回退前，停服務，備份主站 SQLite 與整個 `.qa` 目錄。不要只備份程式，也不要直接把舊 8051 使用的 `instance/qa_portal_demo/` 指到新主站：舊、新 actor／organization 不同，報表所有權與 token 必須經審核後遷移，沒有自動轉換或自動回退。不要把真實公司資料、Oracle 路徑或共用網路磁碟用於此合成本機 adapter。

## Opus 如何接回公司的原 app.py

### 1. 先取得並對照真正的 Portal 邊界

在公司專案的獨立分支操作，先備份。需取得實際主入口／app factory、登入與 user loader、`common/auth_layout.py`、CRUD callback／模型、路由註冊、現行依賴 lock、部署 URL prefix，以及 scheduler/file-lock 設定。以原始碼確認每個接點，不用本包檔名猜測公司實作。

保留公司既有 Flask server、Dash app、Flask-Login／SSO 與使用者 session。將公司 `current_user` 或既有可信 provider 注入 resolver，不能建立第二個 LoginManager 或用公開 demo 帳號取代原登入。`id`、orgcode、dev/test/admin 與 CRUD owner 必須逐一對照。

### 2. 保留原權限與路徑

維持公司既有 route prefix、page order、component IDs、Forbidden 行為及 callback Output 形狀。新 `/QA_portal/maintenance` 是本包的掛載位置，不授權任意改動公司路徑。

以 [INTEGRATION.md](INTEGRATION.md) 的 legacy 規則建立每個 block 的 server policy：True 表示拒絕、entry 條件 OR、CRUD 名單同時授予 entry、admin entry 不自動擁有 CRUD、dev/test 例外順序不變。QSL 主表與兩個字典表須有各自的 target／policy。

所有舊 CRUD／export／upload callbacks 都要接上同等或更嚴格的 transport 與 service 檢查，不能只保護新按鈕、留下舊 POST 寫入路徑，也不能同時註冊相同 Output 的新舊 callbacks。公司若不採用本包 registry，必須實作並驗收等價的 fail-closed guard。

### 3. 替換服務，不只複製 UI

目前主站傳入固定 synthetic policy；`_register_services_and_callbacks` 仍建立 SQLite、合成來源與 mock job。已有 `user_resolver`／policy 注入不等於已完成資料層依賴注入。正式接入前，應將公司的 policy factory、交易／repository、核准報表來源與 job adapter 明確注入，再由受審核的公司頁面註冊。不要移除 `runtime.is_demo` 檢查後直接暴露合成服務。

把 `LegacyCrudService` 的行為映射到公司 Oracle／SQLAlchemy unit-of-work：

- 欄位 allowlist、鎖定欄位、only_update、allow_upload 與 row／tenant 範圍
- QSL 五欄業務唯一鍵、Rev／Supplier_Level alias、現存重複資料處理
- 樂觀版本檢查，資料寫入＋稽核＋版本 snapshot＋token 消耗同一交易
- 暫存 token 的 actor／target 綁定、到期／取消／重播拒絕與整批 rollback
- 日期、LOB、名稱大小寫、trigger／事件 log、下游 CSV 契約

Oracle transaction、schema migration、鎖與錯誤轉換不能用通過 SQLite 測試代替。版本 snapshot 含資料內容，需補公司批准的留存、最小權限、備份與容量策略。

### 4. 分階段驗收與切換

1. 確認公司 Python／依賴版本與備份／rollback。不得以本包歷史 `requirements.txt` 升級正式環境。
2. 接原登入與唯讀來源，先驗收 FMEA／目錄路由及匿名、reader、owner、dev、test、admin、外部門權限矩陣。
3. 接 QSL 查詢／匯出，再接主表及字典表 CRUD、匯入、差異與還原。測試直接 HTTP POST、偽造 Store、權限撤銷、跨人／跨表 token、併發衝突、取消與重複提交。
4. 接精靈核准來源，驗收欄位／篩選／分組、候選資料上限、定義所有權及版本衝突。沒有核准前不提供任意 SQL 或跨使用者分享。
5. 郵件／排程先保持 dry-run。經另外授權後接正式 adapter；確認 scheduler owner、job ID、時區、misfire、coalesce、檔案鎖與多 worker 行為。accepted 不等於送達，uncertain 不自動重寄。
6. 在實際公司 Windows／代理部署環境做瀏覽器驗收：登入／登出、返回／重整、取消／連點、表單保留、下載及桌面／手機。通過後才依核准計畫切換。

## 清理範圍

已移除舊 `demo_app.py`／`demo_server.py` wrappers、`qa_portal_demo.py`、`my_crud_app.py`、未使用的全域 `pages/`、`app_server.py`／`app_config.py`／`auth.py`、`test_admin.py` 與過期 bytecode，共 42 個檔案。精確清單、外部清理備份及復原界線見 [REMOVED_FILES.md](REMOVED_FILES.md)。沒有刪除或遷移應用資料。

`demo_services.py`、`legacy_demo_ui.py` 與相關服務仍由主站或隔離測試使用；檔名保留不表示另有一個可執行網站。`wsgi.py` 只是供既有 WSGI host 使用的 factory-import adapter。啟動說明、快捷檔與功能導覽均以 `app.py` 為唯一入口。此清單僅針對本包，不表示公司同名原檔案可刪除。

## 驗證紀錄

本文件描述整合位置與待驗收範圍，不宣稱已執行新的測試。執行命令為 `python -B -m unittest discover -s tests -v`；以根目錄 [VALIDATION.md](../../VALIDATION.md) 和 [Opus 驗證紀錄](VALIDATION.md) 的實際結果為準。公司私有系統、正式資料、LDAP／Oracle／SMTP、Windows 與部署瀏覽器結果須獨立記錄。
