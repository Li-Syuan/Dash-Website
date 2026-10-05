## Current release handoff (2026-10-05)

The owner now authorizes the completed report, export/fault-drill fixes,
change-impact CLI and CI configuration through a dedicated validation branch,
full Python 3.8/3.10 regression and 50 native browser scenarios, then normal
fast-forward main publication and exact-SHA CI verification. This supersedes
the historical local-only holds below for this release only. See
[release use and migration](releases/2026-10-05.md). Do not deploy, force-push,
change product pins or activate the unapproved 15%/10% performance policy.

## Local change-impact CLI (2026-10-05)

Use [change-impact instructions](change-impact/README.md) to turn changed files
into source-evidenced page/API/callback/schedule candidates and test suggestions.
It is analysis-only, not a test runner or acceptance result. Unknown/core changes
fall back to full regression, and full regression remains mandatory before
publication. Current work authorizes no push, upload or deployment.

## Independent report handoff exercise (2026-10-05)

A second, distinct synthetic report is available through the same `app.py`:
[Corrective action aging](quality-actions/README.md). It remains default-off, with its own
configuration, authorization, query/export, tests and browser acceptance.
The linked handoff review records missing instructions before implementation.
This exercise authorizes local work only; historical publication permissions
below do not authorize publishing this or any future increment. Validation is
specific to the recorded runtime; MSI/Windows and company integrations remain
unverified. See the [final integrated validation](quality-actions/INTEGRATED_VALIDATION.md)
for this report plus fault-drill repair and the remaining browser blocker.

## Current publication handoff (2026-10-04)

The owner explicitly confirmed publishing the cumulative v11, v12, ETL/
performance follow-up and one-page acceptance report to
`Li-Syuan/Dash-Website` branch `main`, using a normal fast-forward push,
then checking that exact commit in the existing Python 3.8/3.10 CI.
Use the clean publication commit based on `e4a9681`; local restore
checkpoints containing raw evidence must not be published. Preserve the
original checkout and prior evidence. No force push or deployment.

See [publication scope and validation](acceptance/PUBLICATION.md). The 15% time / 10%
RSS policy remains unapproved and disabled; overall acceptance remains
INCOMPLETE. This authorization does not cover future rounds, dependency
changes, company integrations or system-clock changes.

The prior-round instructions and results below are historical snapshots.
Their publication holds are superseded only for this confirmed cumulative
release; their technical and safety constraints still apply.

---

# 系統 agent 接手與本機執行指南

## Current local report round

The user selected the one-page acceptance report. This round authorizes only
local changes, tests and review preparation. Read [REPORT.md](acceptance/REPORT.md)
and its validation record. Publication is pending the parent conversation's
explicit destination and authorization; do not push, upload or deploy.
15%/10% performance limits remain unapproved. Prior follow-up source/archives
were preserved before the report edits; do not remove historical failed runs.

## Completed v12 ETL/performance follow-up

The approved scope is ETL restart diagnosis/fixture repair and a configurable
performance gate, in a separate checkout on restore point
`50e6d185c616ef14d331289f894c7e52222fb3fd`. Read
[FOLLOWUP.md](acceptance/FOLLOWUP.md) for executable commands, required evidence,
provisional policy and exact limitations. Preserve completed v12 artifacts.
No push, merge, deployment, installation or system modification is authorized.

## v12: completed local acceptance tooling

The original v11 worktree remains intact. The isolated candidate uses local
restore checkpoint `1e7983962a19438d37d1d97f173ec0fe643249c9`. This round adds
fixed, executable acceptance profiles and fresh evidence verification only.
No push/merge/deploy is authorized. Read [the tool contract](acceptance/README.md)
for one-command usage, exact exit codes, evidence and environment limitations.
Historical v11 results below remain historical and must not be reused as v12 acceptance.


## 第 11 版本機工作範圍

基線為 main `e4a9681137f4762e4fcdcfee356351b7786a9420`（第 10 版，CI 37196698298）。
本輪尚未取得 push 授權；工作留在獨立 worktree，不部署、不呼叫公司系統。
完整結果見 [v11 驗證](v11/VALIDATION.md)。

- 大型 XLSX：先以獨立 v10 基線量測 100,000／200,000 筆，再同條件量測候選；
  預設匯出上限仍為 50,000，量測採伺服器端明確設定。見 [量測與限制](large-export/XLSX_EXPORT.md)。
- 部署前檢查／備份／隔離還原：[操作指引](deployment/OPERATIONS.md)。還原標記必須由
  維運核對後處理；不能宣稱任意 schema downgrade 安全，不能套用到正式資料做演練。
- 新報表可複製範本：[範本指引](report-template/README.md)。透過
  `REPORTING_ENABLE_REPORT_TEMPLATE=true` 在 demo 模式明確啟用；正式 provider 仍需公司提供。
- 權限 coverage 另以 `tests/test_entrypoint_coverage.py --report-template --write-coverage <path>`
  覆蓋 opt-in 頁面；預設頁面 coverage 另保留。HTTP 測試與真實 Chrome 證據分列。
- benchmark 獨佔機器測量時段；Python／瀏覽器最終回歸均在程式凍結後進行。
  WSL 發生 clock backstep 就判該次環境無效；不可調整 host/guest 時鐘或 cookie 來通過。
- Library 現有 helper 在 prepare 階段不支援；不反覆重試或替代外傳。保存 sanitized
  主 ZIP、每份低於 10 MB 的證據分包與 SHA256，由主對話負責附件交付。


先讀 [專案脈絡](PROJECT_CONTEXT.md)、[AGENTS.md](../AGENTS.md) 與
[既有 repository 指引](../.github/copilot-instructions.md)。這是完整可執行
專案的交接，不是另一個 AI 平台。延續目前已批准的復原、併發及效能工作；
新需求、公司系統連線、套件升級、推送及部署需依使用者當次授權處理。

## 啟動與測試

下列命令均從專案根目錄執行，`python` 必須是現有核准的環境。
不要用系統預設的新 Python 或歷史 requirements 自動升級公司環境。

```sh
python --version
python -B app.py
```

瀏覽 `http://127.0.0.1:8050/login`。公開合成帳號 `demo-admin`、
`demo-user-a`、`demo-user-b` 的密碼均為 `demo-only`，僅供隔離本機測試。
`app.py` 只監聽 loopback，無 debug/reloader。直接啟動會建立本機合成 state
並啟動明確管理的本機 synthetic worker；job 預設 disabled，mock 郵件不外送。
正式 provider 不可用 demo 帳密冒充。匯入 factory 不啟動 worker。

```sh
python -B -m unittest discover -s tests -v
python -B tests/test_entrypoint_coverage.py --write-coverage docs/authorization/entrypoint-coverage.json
python -B tests/probes/flask_composition.py . output/flask-composition
git diff --check
```

真實瀏覽器測試需現有 Node、Playwright module 與 Chrome，依
[瀏覽器操作說明](../tests/browser/README.md) 設定核准 Python/module 路徑：

```powershell
$env:QA_STATIC_BRIDGE = '0'
$env:QA_HTTP10 = '0'
node tests/browser/acceptance.cjs
```

使用本機正式 port 8050 的人不必停止自己的網站；harness 另開隔離 loopback
port，建立合成帳號/state，透過管道控制 fixture，結束後只清理自己建立的狀態。
測試結果在 `output/playwright/`，不要加入 Git。HTTP 401/403 測試不等於
瀏覽器點擊；瀏覽器結果要分開報告通過、失敗、未跑及預期導覽取消事件。

## 可重現的效能量測

```sh
python -B benchmarks/portal_latency.py --source-root . --output benchmarks/evidence/candidate.json --rows 5000 25000 --samples 15 --warmups 3 --label candidate
```

基線和候選需各自的獨立來源副本，用相同參數、核准環境與空閒機器順序執行。
`--source-root` 指向要量測的專案來源；不要在第一次改碼後才量基線。
每輪重建 deterministic 合成資料，不碰使用者資料庫。保留原始樣本、median、
p95、資料量、Python/套件版本、來源 hashes、query plan/profile 和驗收結果。
這是 callback HTTP 延遲，不是畫面 render 時間或企業尖峰吞吐量承諾。
量測時暫停本輪其他重負載測試；不要改系統時鐘、網路或安全設定。

## 架構邊界與修改位置

| 層 | 主要檔案 | 不可跨越的邊界 |
| --- | --- | --- |
| 建構/生命週期 | `app.py`、`application.py`、`launcher.py`、`lifecycle.py` | 單一入口；factory 無背景/外部副作用；worker 由 owner 啟停 |
| HTTP/UI | `web.py`、`registry.py`、`ui_pages/` | 既有 URL/IDs；明確 PageSpec/callback policy；不信任 browser claims |
| 現行身分 | `authorization.py`、`providers.py` | provider ID 精確匹配；撤權/換組織時重新核對，不升權、不快取過期身分 |
| `/maintenance` | `definition_policy/domain/repository.py`、`crud.py` | request-bound scope；共讀/owner-or-org-admin write；交易與 audit 原子性 |
| QSL/精靈 | `legacy_policy/crud.py`、`report_builder.py` | 保留 legacy 較窄政策、Query ID、one-use stage/token、版本與歷史 |
| ETL | `etl_dispatch.py`、`etl_adapters.py` | 可信任註冊函式、bounded snapshot、fencing、receipt、uncertain 不自動 replay |
| 報表/營運 | `governance.py`、`operations.py`、`job_monitor.py` | 租戶/動作授權、mock delivery、發布前核對 |
| 公司整合 | provider/adapter 契約 | 未提供的 RESTX、Oracle、LDAP、SMTP、bind keys 不猜測、不連線 |

不要以移除授權、放寬 request limit、共用跨租戶 cache 或更換依賴達成 benchmark
數字。讀取結果與匯出必須保持原排序、篩選、型別、CSV escaping、tenant 與
owner/admin 規則；效能修正仍要跑原完整回歸。

## 復原與驗收規則

- 中斷/程序終止：以新程序重開相同合成 state，查 receipt、snapshot、run
  狀態與 audit。已提交成果不得重複發布；未知結果保留待核對，不刪 claim。
- 資料庫 busy/locked：確認 timeout/rollback、無部分資料或成功 audit，釋鎖
  後可安全重新查詢/提交；不能把 busy 當作已成功。
- 多人同筆修改：真實獨立 connection/process 配合 barrier，同 expected version
  僅一位成功；其餘明確 conflict；資料與 audit 相符，跨租戶/非 owner 仍拒絕。
- 介面：維持 CRUD 彈窗、取消/關閉、連點、報表匯出及精靈上下頁。涉及這些
  路徑時使用真實瀏覽器回歸，不能僅靠 callback HTTP pass 宣稱 UI 通過。
- 結果：記錄精確 source、環境與命令；失敗/skip/unrun 不隐藏。WSL 曾有
  時鐘倒退導致 cookie 簽章過期；無效環境不得通过 mock clock/延長 cookie
  宣稱修復。永久時鐘設定未調整，後續也不得自行改動。

## 已知限制與交付

公司精確 Linux/Python patch、Flask-RESTX 實作、Oracle 交易、LDAP、真實 SMTP、
反向代理/多 worker、網路檔案系統、磁碟滿/損毀和企業負載仍須獨立驗收。
SQLite 中的 idempotency 不能證明外部郵件或資料庫 exactly-once。
歷史 ETL/寄信時刻只作待核對資訊，不寫回現有排程。

第 9、10 版 main checkpoint 均已完成；第 10 版正常推 main 與 GitHub CI
曾另獲批准。本第 11 版的變更仍留在獨立本機 worktree，交付完整專案、
測試/benchmark 證據、變更與遷移說明及變更清單；尚未批准 push 或部署。
既有批准不延伸到下一輪；不得 force push。
完整資料庫、憑證、私人路徑、套件/runtime、快取及大型截圖不納入程式包或 Git；
需要完整證據時另包，保留 hash 與來源鏈，不刪除舊失敗紀錄。

## 只讀時鐘驗證

已提供實際使用的只讀 guard，不修改時間服務、系統時鐘或 cookie 簽章。
以下命令會在 clock 倒退/監測錯誤時傳回 exit 86，即使 unittest 自己全過；
不能把該次環境判為有效，也不自動重試。

```sh
python -c "from pathlib import Path; Path('output').mkdir(exist_ok=True)"
python -B tests/probes/clock_guard.py --output output/clock-check.jsonl -- python -B -m unittest discover -s tests -v
```

## 2026-10-05 四情境故障演練接手（本機未發布）

閱讀 [故障演練／復原步驟](fault-drills/README.md) 及
[結果摘要](fault-drills/RESULTS.json)。入口是 `python -B tools/fault_drills.py`，
`--list` 列出固定 19 個測試方法；只使用臨時合成 DB 與自己建立的子程序。

QSL `query()` 新增交付前的現時權限／原 actor 檢查，修正最後匯出 refresh
期間撤權仍交付下載的已重現缺陷。整合時保留此檢查與新增 HTTP regression。
不要清空未知 ETL claim/receipt，也不要把本機去重當作外部 exactly-once。

Python 3.8.20／3.10.21 的完整回歸各 1125 項，0 failure/error、6 Windows-only
skip。真實 browser preflight 被 cloud executor 的 process socket 限制阻塞；
不得以改安全設定或 HTTP 測試代替。整合後重跑最終來源的可用完整回歸，
Windows／公司整合及真實 browser 仍需補驗。這一輪不授權 push、deploy、upload，
也未批准或啟用 15%／10% 效能門檻。
