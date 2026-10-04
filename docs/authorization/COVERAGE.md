# 授權入口 coverage

`entrypoint-coverage.json` 由真實 app factory 的 registries 與 Flask url_map 產生：
**12 PageSpecs、49 callbacks（46 server / 3 clientside）、17 HTTP routes**。
`tests/test_entrypoint_coverage.py` 執行 **263 個拒絕檢查**。
這是 HTTP/transport 矩陣，與真實瀏覽器證據分開，亦不是程式碼行 coverage。

| 入口 | Policy 與 service 邊界 | 主要證據 |
| --- | --- | --- |
| `/login`, `/logout` | public；按鈕登入、精確 ID、清 session、跨站 logout 拒絕 | application、authorization、unified_portal |
| `/` | authenticated；依 PageSpec/managed grants 過濾 catalog | catalog、page_integration |
| `/page1` | admin；無商業資料的權限 fixture | entrypoint matrix |
| `/page2` | admin/user AND org A | entrypoint matrix |
| `/page3` | admin；原始 report rows/export 前後 current identity | authorization |
| `/admin` | admin；tenant/action/current identity；交易前後查核 | governance、admin_ui、authorization |
| `/reports` | admin/user；report ID+org+view/export/maintain；disabled/archived/grant revocation | governance、admin_ui |
| `/maintenance` | admin/user；request scope；org read、owner/same-org admin write；version/replay/audit | definition、service_isolation、browser |
| `/QA_portal/maintenance` | demo-only admin/user AND org A；主 session resolver；read/export vs CRUD；precise token owner | legacy、original_modal、unified_portal、browser |
| `/QA_portal/operations` | demo-only admin/user AND org A；tenant source/catalog/quality/usage、table registry policy | operations、registry_identity、browser |
| `/QA_portal/etl` | demo-only org A readers；manage admin；job/action/current identity | ETL acceptance/engine/UI、service_isolation、browser |
| `/api/reports/export.csv`, `/demo-api/report.csv` | session+admin；server provider；read前後身份 | authorization、API matrix |
| `/api/managed-reports/<id>/export.csv` | tenant ID+view/export+current grants、server rows、CSV safeguards | governance、API matrix |
| `/QA_portal/source/<report>/<version>/<record>` | org A policy+tenant report/version/record；opaque not-found | operations、API matrix |
| `/QA_portal/feature-todo` | org A policy；固定文件 | API matrix |
| QSL CSV/XLSX/template callbacks | registered org A policy+service export/upload guards | unified_portal、legacy、browser downloads |
| ETL diagnostic ZIP callback | current org A reader+bounded run ID+固定 allowlist | job_monitor、operations_ui |
| `/_dash-update-component` | exact registered server output；origin/body bounds；unknown/replaced/client-only deny | 263 checks、registry、application |
| layout/dependencies/shell catch-all | public shell/registration metadata；先查 policy 才載入 protected data | page matrix；shell HTTP 200 不代表授權 |
| healthz/readyz | deliberate public limited status；無 report/user rows | application |
| assets/component suites/favicon/reload hash/Flask static | framework static/infrastructure；不是 private data entry | route inventory；browser傳輸限制另記 |

Public callbacks 限 routing/login、資訊/sidebar 呈現與 client-only theme/preferences。
Registry 要求 page callback 至少與 page 一樣嚴格；clientside callback 不可走 server HTTP。

| 背景／直接入口 | 保護與證據 |
| --- | --- |
| Runtime simulation | org-hashed lease/job+tenant count；legacy claim fail closed；A/B/A、restart/revoke tests |
| Managed schedule mock | tenant report/schedule/version/grants；claim before capture、fresh check、truthful completion、no automatic replay |
| LegacyJobAdapter | bounded exact actor、server authorizer、claim/uncertain preservation；malformed/error tests |
| JobMonitor | current role/org、lease token/expiry/fence、publication transaction重驗；撤權回滾、process races |
| ETL run/timer/retry/backfill | registered trusted adapters、job permissions、worker/publish重驗、bounded dates、dedup/provenance |
| ReportDefinitions direct/bound | Runtime注入provider；scope expiry、operation/transaction重驗、owner/org/version predicates |
| MaintenanceRegistry | current actor+server policy before I/O and read release；adapter writes retain tenant/version/soft-delete |

原有完整 suites 繼續驗證修改 ID、跨租戶、重播與並行。最終 UI passed/failed/unrun
與 static-delivery 限制另記於 `VALIDATION.md`，不把 HTTP matrix 當 browser 通過，
也不宣稱所有合法功能的每種排列都已窮盡。
