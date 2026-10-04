> Historical v11 record. See [current publication scope](../acceptance/PUBLICATION.md).
> Full raw evidence and local checkpoint inventories are preserved separately.
> Only the sanitized browser result JSON needed by regression tests is retained here.

# v11 入口 coverage

本次由實際 factory 的 PageSpec、callback registry 與 Flask URL map 產生清單。
JSON 同時列出入口、policy、actor、預期／實際結果；以下為 HTTP 檢查，不能
替代 Chrome 點擊證據。原有頁面、API、匯出和背景入口的歷史盤點仍保存在
`docs/authorization/`，新範本及本次服務邊界補充如下。

| 設定 | 頁面 | callback | 否定情境 | 結果 |
| --- | ---: | ---: | ---: | --- |
| 預設，範本關閉 | 12 | 49 | 263 | 全數通過 |
| 明確啟用合成範本 | 13 | 51 | 271 | 全數通過 |

證據：[預設 matrix](entrypoint-default.json)、[範本 matrix](entrypoint-template.json)。
每組由相同五個測試方法展開情境；它們不是額外 534 個 unittest 方法。
涵蓋匿名／不同組織與角色的 page、callback、API/export 拒絕、未註冊 output、
禁止把 clientside callback 當 server callback 呼叫，以及撤銷 session。

| 本輪入口／邊界 | 控制與驗證 |
| --- | --- |
| `/QA_portal/report-template` | 明確 opt-in PageSpec；登入後只取 canonical identity 所屬組織；匿名重新導向、guest 拒絕 |
| `report_template.query` | 登記於 callback registry；query 欄位／型別／bounds 嚴格驗證；repository 前後重新核對 identity；整組拒絕外組織 row |
| `report_template.export` | 重新查詢 server rows、套用相同 contract；all-matching CSV bounded；公式安全文字；不採信 DOM／browser actor claims |
| QSL CSV/XLSX/template download | 服務入口查最新 provider identity；XLSX 分批及發佈前重新授權；callback 最終讀取發現撤權即丟棄下載 |
| 直接 integrated QSL service | 拒絕未知 ID、外組織、provider ID 不符、改角色或偽造 `is_admin`／布林屬性；同組織 reader 仍可讀取與匯出 |
| `/healthz` | 既有公開存活檢查，disposed 狀態失敗；不回傳身份、資料或例外細節 |
| `/readyz` | 既有公開健康端點；唯讀驗證持有連線及 sibling schema，鎖等待受限；失敗僅 sanitized 503，不啟動 worker/provider |
| `tools/deployment_check.py` | 本機離線 CLI，不是新增 HTTP 入口；preflight 不呼叫 provider；備份要求明確 quiesced，還原新目錄及 marker 阻擋啟動 |
| 既有 ETL／job／notification 背景入口 | 未新增 queue、worker 或外部副作用；現有權限、receipt、fencing、uncertain 狀態與復原測試納入完整回歸 |

`tests/test_report_template.py` 另有 19 個 service／HTTP 測試，包含相同 ID 的
跨組織資料、provider mid-fetch 撤權、非法列、query 偽造、CSV 全量篩選及
公式安全。`tests/test_v11_integration.py` 另驗證 factory 隔離、strict flag、
直接服務 actor 與 XLSX ZIP 完成時撤權；`test_unified_portal.py` 以真實 HTTP
重現完成匯出後的帳號／組織／角色撤權，CSV、XLSX、範本皆不能發布。

Chrome 的 40 個情境及完整 Python 結果見 [VALIDATION.md](VALIDATION.md)。
公司 Oracle／LDAP／SMTP、正式租戶或正式通知均未執行。
