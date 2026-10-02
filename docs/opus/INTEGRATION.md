# QA Portal：Opus 整合交接

這是可在本機運行的「離線功能實作與整合包」，不是正式環境已上線的宣告。
所有範例均使用 synthetic 資料、example.invalid 收件人、本機 SQLite；未接 Oracle、LDAP、SMTP，未啟動排程。

## 交付結構

- 既有 `app.py`：完整報表目錄、管理台、權限設定、排程設定、稽核、主題等既有 demo。
- 新增 `qa_portal_demo.py`：`/QA_portal/` 下的實作參考頁，連接新的安全 CRUD 與郵件模擬服務。
- `reporting_workspace/legacy_policy.py`：公司權限規則的獨立判斷器與 callback guard。
- `reporting_workspace/legacy_crud.py`：synthetic QSL 資料維護與安全匯入／匯出服務。
- `reporting_workspace/legacy_jobs.py`：來源、產報、寄信分段紀錄與 mock 執行服務。
- `tests/test_legacy_*.py`：新服務與實際 callback／HTTP 的測試。

兩個 demo 入口各自有用途，並非假裝已將公司 38 處頁面整套搬移。正式整合保留公司原有頁面與路徑，新服務逐頁導入。

## 啟動

使用隔離環境，避免更動公司的現有套件：

```sh
python3.8 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-qa-portal.txt
python -B qa_portal_demo.py
```

瀏覽器打開 `http://127.0.0.1:8051/QA_portal/`。公開 demo 身分不是正式登入系統；不要公開部署。
主功能目錄／管理台可另行執行 `python -B app.py`（主目錄預設 8050，參考頁預設 8051）。

測試：
```sh
python -B -m unittest discover -s tests -v
```

## 必須保留的權限契約

1. `check_user_org_or_id`／`denied` 的 True 是拒絕。
2. 無任何 entry 條件時：test/dev 例外先於 admin；只有 admin 並不能通過空條件。
3. 組織字首、個人 ID、角色採 OR。
4. CRUD 名單同時授予 entry；admin entry 不自動擁有 CRUD。
5. `dev` 和 `is_dev` 角色別名一致。身分由伺服端 provider／Flask-Login 提供。
6. 所有操作重新取伺服端 policy。不能信任 layout 的 `g.permission_context`、模組全域 crud_mode、dcc.Store、URL 或前端 disabled。
7. 未知操作拒絕；查詢與匯出也授權。登入與 row/tenant 邊界不能只靠頁面隱藏。

## 對現有原始碼的整合順序

### 第一階段：權限，不改業務規則

公司 `common/auth_layout.py` 保留 API 名稱、裝飾器、Forbidden UI、routes_pathname。
用 `legacy_policy.denied` 共用判斷，把 `current_user` 當身分參數。
針對每一個 block，在伺服端建立不可由 client 修改的 policy factory。
註冊 callback 時使用 guard，service 裡也檢查。既有 CRUD callback **不可留下一條未授權舊路徑**。
不要同時註冊新舊同 Output 的 callbacks。

### 第二階段：服務與資料適配

新 SQLite 服務用來說明交易／stage／回傳語意，**不是公司 Oracle model 的直接替換**。
Opus 必須把 schema map、SQLAlchemy unit-of-work、欄位 allowlist、session.user resolver 接到公司模組。
QSL 五欄業務鍵、Rev、Supplier_Level alias 及 dry-run rollback 保留；資料庫新增唯一鍵前先檢查既有重複資料並審核 migration。
日期、LOB、Oracle 名稱大小寫、事件 log、trigger、IPO_Interface CSV 必須用代表性去識別 fixture 驗證。

### 第三階段：使用者流程

以 FMEA 唯讀頁先試接，接著 QSL（主表與兩個字典表需各自的目標識別／policy），最後材料多表報表。
匯出使用伺服端重建同一篩選條件；畫面與匯出欄位／篩選契約應明確一致。
輸入錯誤不清空使用者表單；成功提交與刷新失敗分開呈現，避免使用者重送。

### 第四階段：排程與郵件

不替換現有 scheduler/file lock，不更動實際 job ID、觸發时间、Windows 寄信保護。
先只接設定與 dry-run；讓來源更新成功才產報。每段狀態分开儲存。
正式 SMTP adapter 必須回傳接受／部分拒收／不確定，接受不等於送達；不確定不得自動重寄。
收件者與正式開關須經 owner 審核。獨立確認 scheduler owner、Asia/Taipei、misfire、coalesce、重啟接管。

## 不要做

- 不整包覆蓋 company app.py、CRUD.py、auth_layout.py。
- 不升級 Python/Dash/DMC，不把曖昧依賴版本擅自選成較新。
- 不把 demo identity selector 留到正式環境。
- 不將 admin 一律等同所有資料寫入，不用 UI flag 當安全邊界。
- 不因為 callback 成功就宣稱 DB commit／mail delivery 成功。
- 不重試狀態不明的郵件，不宣稱 exactly-once SMTP。

## 版本矩陣

已知目標 Python 3.8、Dash 2.9.1、Flask 2.2.3、DMC 0.12.0、DBC 1.4.1。
歷史文件出現 SQLAlchemy 1.4.29/1.4.48、APScheduler 3.9.1/3.10.1；需由部署端 lock/env 釐清。
新服務不依賴這兩套件，不能把本機測試當成它們的相容性驗證。

## Opus 驗收清單

- [ ] 實際公司 Python/依賴 lock 已確認；沒有 blind upgrade。
- [ ] 路由 `/QA_portal/`、page order、既有 component ID/下游 CSV 不破壞。
- [ ] 匿名、reader、crud owner、dev、test、admin、外部門 matrix 都跑過。
- [ ] 直接 POST callback、權限撤銷後重送、兩人交錯操作均被正確處理。
- [ ] only_update/allow_upload 與欄位鎖在 server 生效。
- [ ] 偽造 Store、超量資料、跨人／跨目標／過期／重播 token 測試完成。
- [ ] 五欄唯一鍵、同批重複、併發更新、rollback、dry-run 都符合 QSL 規格。
- [ ] 保存成功但刷新失敗不會誤報 rollback，也不促使重複新增。
- [ ] 三個 QSL block 與材料多表匯出逐頁測試。
- [ ] source fail / generation fail / accepted / partial / uncertain 各狀態驗證。
- [ ] 首次 request、4 workers、持鎖者退出、重啟與時區正式環境測試。
- [ ] 真實瀏覽器測桌面／手機、重複點擊、取消、返回與表單保留。
- [ ] 正式身分／Oracle／SMTP 僅在明確授權後接入，先 backup 並備 rollback。

## 原 CRUD callback 遷移對照（避免遺漏）

| 原 callback | server 操作檢查 | 拒絕時輸出形狀 |
|---|---|---|
| read_data | read | 通知、no_update |
| download_submit | export/read | 通知、no_update |
| download_template | read + 啟用上傳（新示範採較嚴格 CRUD） | 通知、no_update |
| create_new_data | crud + 非 only_update | 通知、其餘欄位 no_update |
| update_query / delete_query | 各自寫入範圍；delete 非 only_update | 單一內容 |
| update_submit | crud + 欄位 allowlist | 單一通知，不額外包 tuple |
| delete_submit | crud + 非 only_update | 單一通知 |
| upload_store_data | crud + allow_upload；解析前檢查 | 通知、空 Store、空預覽、隱藏 |
| upload_submit | crud + allow_upload；再次驗證 | 通知、no_update |
| create_modal_body / update_modal_body | create / update | 單一內容 |

示範所有上傳統一用伺服端 staging，包含小檔案；不保留原 client-memory rows 寫入途徑。
示範多了一層 version 和 soft-delete，不應偷偷改公司既有使用流程；請在整合時明確映射、通知使用者。

## 修改預覽／報表精靈增量

新增完整流程與持久化，詳見 [REVISION_WIZARD_UPGRADE.md](REVISION_WIZARD_UPGRADE.md)。請注意新版啟動器會保存合成資料；歷史 API 與受限報表來源仍須正式 adapter 整合。
