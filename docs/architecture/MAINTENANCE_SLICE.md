# CRUD 架構升級：報表定義完整切片

2026-10-04。這次完整落地 `/maintenance` 的查詢、選取、新增、修改、
封存與還原流程，作為後續頁面的遷移範本。`app.py` 仍是唯一啟動入口。
現有 QSL 獨立彈窗、差異確認、歷史還原、精靈、營運中心及 ETL 調度保留。

## 實際路徑與責任

1. `application.py` 的 HTTP 邊界與 `CallbackRegistry` 先檢查目前登入身分。
2. `ui_pages/maintenance.py` 保留 Dash 元件、事件解析、表單／提示與輸出形狀。
   資料操作統一取得 `Runtime.definition_request()`，不從 Store、表格列、
   URL 或 callback payload 接受使用者、角色、組織或稽核關聯 ID。
3. `definition_policy.py` 集中 `DEFINITION_ACCESS` 與不可變的
   `DefinitionPrincipal`。頁面、callback、service 及編輯權限提示使用同一規則。
4. `crud.py` 的 `ReportDefinitions` 負責輸入驗證、冪等新增與操作協調。
   `DefinitionRequest` 固定一次請求的身分及 request ID，提供不需傳入 user
   的 CRUD 方法；SQL 不在這一層。
5. `definition_domain.py` 放置安全錯誤型別、欄位與版本驗證。
6. `definition_repository.py` 專責 SQL，每次交易綁定一位 principal。
   `StateStore` 提供獨立連線、SQLite 交易、現有 schema 驗證與稽核寫入。

共同 service/repository 物件只保留 storage 設定，不保留「目前使用者」、
查詢結果、共享連線或跨請求可變資料。`DefinitionRequest` 與交易物件不可
存進全域變數、app extension、快取、session 或背景工作。

## 保留的權限與資料契約

- 只有登入的 `admin`／`user` 能使用此頁及 service。
- 同一組織的人可以共讀；只有本人或同組織管理員可以修改。
- 管理員不能越過組織邊界。同名使用者分屬不同組織也不會混用資料。
- ID、owner、org、版本與時間仍由伺服器管理。owner 仍為原先的 SHA-256
  使用者識別雜湊；没有重寫既有 ownership。
- 外組織 ID、不存在 ID 與格式不合法 ID 保持同樣的 NotFound 行為。
- 同組織非擁有者在版本／封存狀態檢查前被拒絕，不額外洩漏衝突資訊。
- 更新、封存、還原保留 optimistic version、封存狀態與版本上限檢查。
- 每筆寫入與不含欄位值的稽核事件在同一交易提交；audit 失敗全部回滾。
- 新增 request key 仍以組織與 owner 為範圍；相同初始 payload 的重送不會
  多建資料或多寫 audit。已修改或封存的舊 draft 不會被當成新資料重播。
- 同組織共讀是現有業務語意。「使用者隔離」指請求身分、owner 寫入權限、
  draft key、通知與稽核不串用，沒有改成每位使用者只能看到自己的資料。

Repository 在交易內執行同一份權限規則，再把 org、owner/admin、version
與 lifecycle 條件寫入 UPDATE 的 WHERE。測試會刻意略過 Python 權限檢查，
確認 SQL 的 owner、版本與封存條件仍能拒絕非預期寫入。這是縱深保護，
不是允許應用程式繞過 service；repository 只供可信任伺服器程式使用。

## 請求生命週期

`Runtime.definition_request()` 只接受該 Runtime 所屬 app 的活動 HTTP
請求。它複製 id/org/role 三個權威欄位、內部計算 actor hash，並固定當次
request ID。之後變動原始 dict 不會改變已綁定的操作或通知 audience。

每次方法呼叫都檢查原始 app、Request 物件以及 request-local 存活標記。
`teardown_request` 使標記失效，所以離開請求、其他使用者的新請求、其他
app、重複 correlation ID、重新進入或複製 Flask context 都不能復用該服務。
這不會把一個先前授權的身分保存成跨請求授權。下一個 HTTP 請求仍重新載入
identity provider 的角色與組織；撤權、轉組織或刪除身分會生效。

通知由同一個 bound scope 產生，與資料權限及 audit 使用相同的身分／request
ID。通知依舊只有固定訊息代碼，沒有表單、資料列或原始 backend 例外內容。

## 一項明確的事件行為調整

一次 maintenance mutation request 必須包含且只能包含一個已知寫入按鈕
的 `.n_clicks` 觸發。Save／Archive／Restore 同時出現時回傳 PreventUpdate
（HTTP 204），不再讓 changedPropIds 的排列順序決定實際操作。未知／pattern
觸發值不會授權寫入。正常單按鈕流程、New、重新載入與分頁行為維持。

## 遷移與回復

這是程式架構遷移，沒有新增套件、升級既有 pins、改路由或改資料庫 schema。
現行 schema 3 與既有 v1/v2 升級機制不變，`StateStore` 與 SQL 表結構未修改。
不需重新匯入資料、不需清除資料庫，也沒有 purge 功能。

1. 先停止現有程序，依既有運維程序備份 workspace SQLite 及 `.qa` 旁資料夾。
2. 更新整份原始碼，保留原有 storage 路徑、設定與安全保管的 session secret。
3. 在核准 runtime 執行 `python -B -m unittest discover -s tests -v`。
4. 使用既有 `python -B app.py` 啟動；分別以 owner、同組織 peer、管理員及
   其他組織身分驗證 maintenance 操作，再驗證 QSL 彈窗與 ETL 頁面。
5. 如需回復，停止新版並切回先前原始碼。此增量没有 schema downgrade；
   新版建立的相同 schema 資料仍與先前版本相容。不可盲目覆寫既有 instance。

原有 `ReportDefinitions(store).list/get/create/update/soft_delete/restore(user, …)`
與 `crud.py` 的錯誤型別、驗證 helper 匯入保留，既有 governance/operations
使用者不需改 import。HTTP callback 採用 request-bound 介面；可信任背景程式
可以使用原有 API 或短生命週期 `bind()`，但每個新工作必須自行重新解析身分。
未提供 `is_active` 的純 service bind 不具備 Flask 請求生命週期保護。

## 驗證與邊界

精確執行結果、測試數與 runtime 見 [VALIDATION.md](VALIDATION.md)。
新增案例使用真實 SQLite 交易與 Flask/Dash HTTP callback，包含重疊執行的
多使用者請求、不同組織同 ID、相同 draft key、權限撤銷、通知與 audit 關聯。
原有跨程序版本競爭、遷移／備份、QSL、modal、ETL 測試也列入總回歸。

這不是全站一次性改寫。QSL legacy policy、managed reports 與其他整合保留其
既有規則；後續頁面應逐一比對原業務矩陣，再採用此範本，不可直接把「管理員」
或「同組織」權限推廣到別的服務。沒有連上公司系統、真實 SMTP、Oracle 或
LDAP，沒有發佈到遠端。真實瀏覽器視覺／操作及指定 Windows／精確 patch
版本仍需另外驗收。
