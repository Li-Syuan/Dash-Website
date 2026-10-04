# 系統 agent 接手與本機執行指南

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

第 9 版 main checkpoint 已完成；本輪變更留在獨立本機 worktree，交付完整專案、
測試/benchmark 證據、變更與遷移說明及變更清單。本輪已另獲批准正常推 main
並完成 GitHub CI 驗證；不得 force push、部署或擴大到下一輪。
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
