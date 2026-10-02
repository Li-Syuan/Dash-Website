# QSL 修改預覽、歷史版本與報表精靈

本次新增功能已整合進本包唯一主入口 `app.py`；不是公司原始系統已接入／已部署。仍使用合成資料，沒有連接 Oracle、LDAP、SMTP。主目錄原有功能保留，公司路由契約需在實際接入時逐項核對。

## 從哪裡開始

在獨立解壓的版本資料夾內，使用既有相容 conda／Python 環境：

```powershell
python -B app.py
```

在同一台電腦瀏覽 `http://127.0.0.1:8050/login`，以 `demo-admin`／`demo-only` 登入，從主導覽或目錄進入 `/QA_portal/maintenance`。同一頁包含 QSL CRUD、匯入匯出、修改預覽、歷史、精靈與 mock 郵件。

- `demo-admin`：組織 A，可維護及保存定義。
- `demo-user-a`：組織 A，可查詢、匯出及預覽報表，不能修改、保存定義或讀取歷史。
- `demo-user-b`：組織 B，拒絕此頁與其資料 callbacks。三者密碼皆為 `demo-only`。
- 共用主站 Flask-Login session，不另開 8051，也不再切換獨立模擬身分。
- 不公開部署公開測試帳號；只綁定 loopback。
- 沒有因主入口整合新增套件或升級公司環境；隔離環境安裝依賴仍參考 `requirements-qa-portal.txt`。

公司 admin entry 不自動取得 CRUD 的原規則不變；上方為本包測試身分的明確映射。完整接點與公司原始碼接入順序見 [MAIN_APP_INTEGRATION.md](MAIN_APP_INTEGRATION.md)。

## 操作流程

### 1. 修改差異確認

1. 輸入 ID，按「查詢 ID 並載入表單」，取得目前值和版本。
2. 修改欄位，按「預覽修改差異」。此時資料尚未寫入；畫面列出修改前後。
3. 確認後按「確認套用已預覽修改」。這次套用的是已預覽內容；在預覽之後又改表單，須重新預覽。
4. 「取消預覽」在伺服器撤銷該 token。重複確認不會重複寫入；取消／過期／其他使用者的 token 無法提交。
5. 同一筆資料被他人更新時，舊預覽被拒絕。重新載入最新資料後再比較，不會強行覆蓋。

### 2. 歷史與還原

1. 歷史區輸入 ID，按「載入歷史版本」。每版包含版本、操作人、UTC 時間、變更欄位與還原來源版本。
2. 選舊版並按「預覽還原差異」。核對後再按「確認還原為新版本」。
3. 還原新增下一版，不修改或刪除歷史。已刪除資料也可依合法歷史狀態恢復；唯一鍵衝突及欄位鎖仍阻擋操作。
4. 還原後重新查詢主表，並重新載入表單版本。

新建、修改、軟刪除、復原、批次匯入都在同一交易記錄版本快照。歷史為此參考服務的資料，不能當成公司原有所有異動的歷史。

### 匯入差異預覽

CSV／XLSX 暫存後會顯示全批新增、更新、失敗數，以及前 10 列的修改差異與衝突碼。預覽以 rollback-only 交易跑同一套驗證，資料、歷史與稽核不會寫入。再次提交仍會重新檢查目前資料；任何一列失敗時，示範 UI 的整批 atomic 模式不寫入任何列。預覽的新 ID 不會預先保留。

### 3. 四步報表精靈

1. 選核准資料來源：目前只有 synthetic QSL。
2. 選欄位與順序。
3. 設定一個可選的文字篩選（字面包含／完全相等）、至多三欄分組計數，以及 1–500 列顯示上限。
4. 預覽實際授權來源資料，再保存具名定義。修改設定後須重新預覽才能保存。

「列出我的已存報表」→選取→「載入所選定義」，可修改再預覽；同名更新須持有最新版本。改名稱會另存新定義。報表定義隸屬伺服器身分 ID＋組織，admin 也不能跨使用者讀取。

- 分組時輸出欄位為所選分組欄位＋count，不會偷偷混入其他欄位。
- 保存的是設定，並非凍結資料快照；重新預覽會讀取目前授權資料。
- 服務支援最多 8 個 AND 篩選；目前精簡 UI 只編輯一個。由其他工具建立多篩選定義時，UI 拒絕載入而不會默默截斷。
- 原始候選資料上限 5,000 筆，超出時要求縮小篩選，不提供不完整的分組總數。
- 不接受 SQL、Python、任意資料表／adapter／檔案路徑、前端角色或授權設定。
- 目前未提供精靈定義分享、排程寄送、SQL 編輯或正式資料源；這些需另定權限與整合規格。

## 保存與升級

主入口預設將主站狀態保存到本包目錄下的 `instance/workspace.sqlite`，QSL 相關資料保存到同層 `instance/workspace.sqlite.qa/`：

- `qsl.sqlite`：合成資料、稽核、歷史、一次性預覽與匯入 token
- `report_builder.sqlite`：報表定義與 schema version
- `jobs.sqlite`：原有合成排程／寄信設定與紀錄

也可以在啟動前指定絕對本機 state 檔名；QSL 目錄為完整檔名加上 `.qa`：

```powershell
$env:REPORTING_STATE_PATH = "D:\Dash_website\qa_data\workspace.sqlite"
python -B app.py
```

先建立 `qa_data` 目錄；不要填入公司正式資料路徑。此例 QSL 目錄為 `D:\Dash_website\qa_data\workspace.sqlite.qa\`。

不要指定公司 Oracle、共用 NFS／網路磁碟或真實資料路徑。這是同一主機 SQLite 示範。重新啟動後資料／歷史／定義保留；預設主站登入工作階段會失效，需要重新登入。

早期啟動器使用臨時資料夾，因此舊程式退出後的臨時資料未必仍存在，不能承諾自動救回。其後獨立 8051 版本的 `instance/qa_portal_demo/` 也不會自動搬入主站：需先備份，核對 actor／組織與報表所有權後再遷移。對既有此服務的 SQLite，新增歷史表與 preview 表，不重設原本的資料／稽核／stage；只有現存當下版本能成為 baseline，不能捏造更早的完整內容。正式採用前先停服務、備份主站資料庫與整個 `.qa` 目錄再升級。

本 ZIP **不含** instance 資料或資料庫。若自行解壓，放到 `D:\Dash_website\QA_Portal_Revision_Wizard_20261002\` 等新的版本資料夾；不要把 ZIP 直接覆蓋原專案。指定同一資料目錄前先做備份。公司 adapter 與 repo 合併由 Opus 按整合指南處理。

## Opus 整合邊界

- `legacy_crud.py`：preview_update / confirm_update / discard_preview / history / preview_restore / confirm_restore，以及交易式快照。
- `report_builder.py`：ReportBuilderService，固定 schema 的 validate / preview / save / list_definitions / load。
- `qa_enhancements_ui.py`：新兩組 panel 與實際 Dash callbacks。
- `legacy_demo_ui.py`：主站共用 UI／隔離測試 factory，注入 server 身分與 policy，將舊直接更新改成預覽＋確認；合成資料來源仍需正式 adapter 替換，沒有獨立啟動入口。
- `ui_pages/qa_maintenance.py`：主站頁面、policy、可信身分 resolver 與 callback registry 接點。
- `app.py`：唯一入口，主站狀態及 QSL `.qa` 目錄的啟動設定。

把公司模型接入時，必須讓授權、樂觀鎖、業務唯一鍵、修改＋稽核＋版本快照＋token 消耗在同一交易內；不要僅複製 UI。資料庫歷史 snapshot 含資料內容，正式環境要補企業級保留期限、最小權限、備份與容量策略。

## 驗證界線

詳見 `VALIDATION.md` 的本次增量結果。測試使用隔離 SQLite 和 Flask/Dash 真實 HTTP callback dispatch。未做新畫面的瀏覽器視覺驗證，未執行公司 Oracle／LDAP／SMTP、正式排程或 Windows 公司環境測試。程式與服務測試通過不等於正式整合驗收。
