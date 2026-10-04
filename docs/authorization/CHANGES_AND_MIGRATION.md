# 全站授權與資料隔離 — 2026-10-04

## 基線

接續 Library 第 7 版 `QA_Portal_Opus_Integration_20261002.zip`，542,305 bytes，
SHA256 `5a44966e072f4ae176bd68967c2323191eb29994e045bb3cb6ac337517fd4318`。
已在 MSI 目的地取得 bytes、版本身份並驗證 hash。Windows Python 不支援 Library
helper 的 xattr API，因此使用同一 MSI 的 WSL 原版 helper 在持久工作目錄完成；
沒有自製下载 URL 或略過身份 metadata。

原 checkout 有未提交變更。134 個基線檔案中，115 相同、9 不同、10 缺少，另有不同
大小/hash 的舊同名 ZIP。開發使用獨立第 7 版副本，未覆蓋原 checkout。
相對路徑與兩側 digest 見 `baseline-comparison.json`，供後續人工合併判讀。

## 修改

1. `authorization.lookup_identity` 精確綁定 provider 回傳 ID；`current_identity`
   比對目前 id/role/org，不正規化、升權或快取。HTTP loader、原始報表、模擬、managed
   reports、maintenance facade、table registry 使用相同邊界。錯 ID 測試模擬已配置
   provider 違反契約，並非聲稱一般瀏覽器能任意操控 provider。
2. Maintenance/managed transactions 在取得交易後及提交前重驗，資料與 audit 同時
   回滾。報表與 table adapter 在釋出 rows 前重驗。請求中撤權時不回傳過期 record
   更新或通知，也不因錯誤通知再次讀取失效 scope 而變成 500。
3. QSL/report builder 保留精確 actor，`writer` 與 ` writer ` 不再共用 token。
   拒絕控制字元、surrogate、缺少身份及非布林 authenticated claims，保留既有 read/CRUD
   分權、preview/stage token、版本與重播保護。
4. Monitor publication 在交易內重驗；Operations quality gate 後及固定 mock capture
   前再驗證。ETL job/action/org/user allowlist、fencing、backfill、retry provenance
   保留，新增撤權後重播拒絕測試。管理模擬的 lease/job key 及顯示計數改為組織獨立。
5. `lifecycle.dispose_app(server)` 供 app owner 在停止接收請求後停止 worker、關閉
   長連線，再清理 app 自有暫存目錄；它不是 request teardown。Factory 失敗亦清理資源，
   保留原始錯誤。修復 QSL constructor 失敗時 connection leak、測試清理及 ETL 範例連線。

`/maintenance` 維持同組織共讀、owner 或同組織 admin 可寫；跨組織 admin 無特權。
QSL demo 仍是 org A admin/user 入口、user 只讀、admin 經原 demo adapter 取得 CRUD。

真實瀏覽器另外重現 pinned Dash 2.9.1 對 callback 400/401 重複讀取 response
text 的例外。`assets/00_dash_error_response.js` 僅對同源 POST
`/_dash-update-component` 的這兩種回應共用一次 text Promise；保留 HTTP
status、headers、body 與原 Response，不重試操作。其餘 URL、method、status
保持原行為。原生 Response 邊界測試與真實撤權 Save 均驗證此相容處理。

app.py、既有 URL/登入 IDs、CRUD 彈窗、ETL、報表、套件釘選及 Python 3.8 相容性保留。
沒有 Superset、自助報表、金融策略或公司系統整合。

## 遷移與使用契約

第 9 版補上 `StaticTransport`，在原 WSGI 邊界將本機靜態 GET/HEAD 大回應拆成
16 KiB 原始 bytes，修復已重現的大單塊送出／立即 close 尾端遺失；不改 status、
headers、內容、授權或依賴。原生瀏覽器完整 32/32、兩平台完整回歸與 WSL 401
環境根因見 [VALIDATION_V9.md](VALIDATION_V9.md)。不需新增產品配置或資料遷移。

- **不需 schema migration。** 主 schema 3 及 QSL/ETL schema 不變；沒有自動資料
  改寫、purge、push、merge 或部署。
- 升級前另行停妥服務，備份主 `REPORTING_STATE_PATH`、`<state-path>.qa/`、
  `<state-path>.operations/`、`<state-path>.etl.sqlite` 及既有 lease/sibling stores。
  依既有運維文件在停止狀態備份，勿只複製忙碌中的單一 DB。
- **舊未分租戶 claim 不自動重播。** 若存在舊 `demo-mail / fixture-1` claim，
  不論 running/failed/succeeded，新模擬均安全停止並保留舊紀錄。它沒有足夠組織資訊，
  不猜租戶、不自動搬移。由 operator 人工核對，不可刪 uncertain claim 來強制重試。
  此限制只涉及固定合成模擬，不寄真實郵件。
- Provider-backed service 拒絕舊 snapshot；角色/組織改變後先重新取得身份。不要傳
  browser claims。`ReportDefinitions(store, identities=provider)` 使用 authoritative
  provider；省略 identities 的相容 API 僅供已認證的可信 server/offline caller，
  它不是 authentication API。Bound request 不得跨請求或存入全域 cache。
- 舊 trim 行為下含空白的 legacy actor/token 不再互通。重新建立 token，不改寫 owner
  強制恢復。持久資料若有身份歧義須人工核對，本增量不推斷擁有者。
- `dispose_app` 必須在停止接受新請求後使用。失敗會回報，不隱藏關閉錯誤、不刪仍被
  使用的目錄；正式持久 state 不由 helper 刪除。

## 實測邊界

帳號、資料、匯出、通知均為隔離合成 fixture；沒有 Oracle、LDAP、SMTP、真實通知、
公司資料或正式排程。可信 Python adapter 並非 sandbox，外部副作用仍需另行審核的
outbox/idempotency/fencing 契約。外部 identity provider 與 SQLite 沒有分散式原子交易；
本增量在可控制的讀取、交易及 publication 邊界重驗。已發生的 mock outcome 不因稍後
撤權被偽裝成未發生，也不自動重播。

見 [COVERAGE.md](COVERAGE.md)、[VALIDATION.md](VALIDATION.md) 的逐項證據與限制。
