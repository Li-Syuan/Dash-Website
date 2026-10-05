# 上手包驗證：2026-10-05

本頁記錄原 ZIP 完成本地交付時的證據，當時沒有新的 GitHub 推送或部署。
使用者後續已批准推送；目前發布流程與 exact-SHA CI 以 [PUBLICATION.md](PUBLICATION.md) 為準。
程式基線為 `7bb9b264482179a1b31ca32c9ed6737c9b8f51a0`；
最終本地 commit 與逐檔校驗碼由 ZIP 內 `BUNDLE_MANIFEST.json`、`SHA256SUMS.txt` 記錄。
[機器可讀證據](EVIDENCE.json) 將測試綁定到原始碼 SHA256，沒有原始 logs 或私有路徑。

## 新增及未改動

- 新增中文一頁入口、Windows 完整流程、公司接入清單與純合成 CSV 範例。
- 新增唯讀 `tools/quickstart_check.py`：先驗證九個既有 pins；檢查明確模式、
  state 路徑、品質報表旗標、未知設定、權限、marker、完整 sibling set 與 schema。
  不產生 DB、不啟動 app／worker、不連 provider；company profile 不能通過為 ready。
- 新增 `tools/quickstart_demo.py`：只建全新合成目的地，沿用既有備份／還原工具；
  對八個 stores 逐一比較還原前後 logical digest，檢查 receipt、fence、排程及 marker。
- `app.py`、產品模組、assets、QA/demo pins 及既有 browser workflow 共 70 個檔案，
  與上述基線逐 byte 相同。沒有改網站入口、路由、權限、DB schema 或公司整合。

## 本包實跑

清除歷史原始文字／DOM／log 證據後的全新副本，未帶入 `.git`、runtime、DB 或 cache，
使用已存在的核准 runtime 完成以下檢查；本輪沒有安裝套件。

| 檢查 | 實際環境與結果 |
| --- | --- |
| 完整 regression | Linux x86_64 / Python 3.8.20：1233 tests，1227 passed、6 Windows-only skipped、0 failure/error，95.717 秒 |
| 完整 regression | Linux x86_64 / Python 3.10.21：1233 tests，1227 passed、6 Windows-only skipped、0 failure/error，90.824 秒 |
| 時鐘監測 | 上述兩次 exit 0；clock guard valid，0 backstep；沒有調整系統時鐘 |
| 新工具覆蓋 | 33 個 preflight + 8 個 drill tests，已包含在每版 1233 項中，不重複加總 |
| 額外雙報表入口檢查 | Python 3.10.21，5 tests passed；其內 279 個 policy/route/callback 子檢查通過，使用 Flask test client |
| 乾淨副本 CLI | 兩版均通過 new-demo preflight（不建檔）、完整既有 set 唯讀檢查、產生 CSV、八-store backup/restore、marker 阻擋與拒絕覆寫 |
| 合成報表 | as-of 2026-10-05：36 列、25 open、11 closed、25 open overdue；日期更換與空結果另有回歸 |
| 回滾保護 | 還原 logical digests 全部符合；原 source 的較新 receipt 及 schema 999 保留；restore marker 保留且實際阻擋 factory 啟動 |
| 原始碼檢查 | `git diff --check` 通過；Python 3.8 相容語法及實際 runtime 測試通過 |

完整命令：`python -B tests/probes/clock_guard.py --output <local-clock-file> -- python -B -m unittest discover -s tests -v`。
兩個 `python` 都要替換為同一個核准 runtime 絕對路徑。原始 logs 留在驗證者本機，未混入交付包。
41 個新增 tests 包括錯誤參數不回顯 secret、拒絕公司模式、拒絕 symlink／WAL／不完整資料、
讀取前後檔案不變、還原拒絕啟動與失敗保留現場。

開發期間修正過三類測試／檢查問題：固定 as-of 的預期結案數、preflight 測試匹配及診斷邊界、
CLI 無效參數回顯。最終上述乾淨回歸在修正後執行；沒有把先前失敗重標為通過。
獨立安全複查另核對並修正了重開命令的 `--quiesced`，目前沒有遺留的已知操作安全阻擋。

## 舊 CI 與本包的界線

已讀回 [基線 CI 37261836522](https://github.com/Li-Syuan/Dash-Website/actions/runs/37261836522)，
其 `head_sha` 為上述 `7bb9b264`，狀態 completed/success。
該次 Python 3.8.18／3.10.21 各 1186 passed + 6 Windows-only skips，Chrome 為 50/50。
這是**基線**驗證；本輪新檢查沒有推上 GitHub，也沒有新的遠端 CI。

## 明確未驗證

- 本輪沒有 MSI／Windows 實測，PowerShell 命令僅做文件與靜態核對。Linux 跳過六項 Windows tests。
- 沒有重試先前受限的 browser／socket；沒有新的網路 listener、真實瀏覽器或畫面驗收。
  Flask test client 是程序內 HTTP transport 檢查，不是實際 TCP／瀏覽器渲染。
- Hosted CI 曾有中文字型缺字；本包沒有附字型或宣稱修復，須在使用者 OS／瀏覽器目視驗收。
- 精確公司 Linux kernel 3.10.0-1160／Python 3.8.13、Windows Python 3.10.4、完整 transitive matrix，
  真實 Oracle／LDAP／SMTP／Flask-RESTX／SQLALCHEMY_BINDS、外部 exactly-once 與正式部署均未驗收。
- 本輪沒有新效能數值或效能改善承諾；15% 時間／10% RSS 門檻仍未批准、未啟用。
- 新增 preflight 的權限檢查是保守 metadata／access check，不是寫入測試或 Windows ACL 證明；
  `--quiesced` 仍是操作者聲明，不能證明別的程序已停止。

請將 [公司接入清單](company-checklist.md) 與實機驗收當作正式接線前的獨立工作。
