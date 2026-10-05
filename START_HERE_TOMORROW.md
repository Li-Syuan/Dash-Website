# 明天先從這裡開始：QA Portal 上手卡

適用：2026-10-06 起的 Windows 本機試用。下載本次 Quickstart ZIP、核對交付 SHA256，
再解壓到**全新、本機磁碟上的資料夾**，保留舊版與公司專案。
本包提供合成資料主站、報表及離線檢查；公司 Oracle／LDAP／SMTP 接入仍待審核。

## 1．準備與設定

先由 IT 核准 Python、既有套件 pins 與安裝來源，再依
[Windows 完整步驟](docs/quickstart/README.md#setup) 建立獨立
`.venv`。使用 `requirements-qa-portal.txt`；本包不會自行安裝或升級套件。
PowerShell 直接呼叫 `.venv\Scripts\python.exe`，不用啟用虛擬環境或改 Execution Policy。

在解壓目錄開啟 PowerShell；以下只用於**第一次、全新 demo**：

```powershell
$Python = Join-Path $PWD '.venv\Scripts\python.exe'
$StateDir = Join-Path $PWD 'local-demo-state'
New-Item -ItemType Directory -Path $StateDir -ErrorAction Stop | Out-Null
$env:REPORTING_MODE = 'demo'
$env:REPORTING_STATE_PATH = Join-Path $StateDir 'workspace.sqlite'
$env:REPORTING_ENABLE_QUALITY_ACTIONS = '1'
$env:REPORTING_ENABLE_REPORT_TEMPLATE = '0'
$env:REPORTING_SESSION_COOKIE_SECURE = '0'
& $Python -B tools/quickstart_check.py --profile demo --new-demo
if ($LASTEXITCODE -ne 0) { throw '檢查未通過，請先讀取檢查結果。' }
& $Python -B app.py
```

若資料夾已存在，先停下核對；續用資料請看
[重開步驟](docs/quickstart/README.md#restart)。網站只有 `app.py` 一個入口。

## 2．登入、選日期、查詢、匯出

1. 用自己的瀏覽器開啟 `http://127.0.0.1:8050/login`。
2. 以 `demo-admin`／`demo-only` 登入。帳密公開，僅供本機合成測試。
3. 在 Quality 分類選 **Corrective action aging**，或開啟
   `http://127.0.0.1:8050/QA_portal/quality-actions`。
4. 先確認 **As of date**。預設固定為 **2026-10-05**，隔天也不會自動變更。
   設定 Status／Priority／Search，按 **Apply filters**。
5. 按 **Export filtered CSV**。下載包含目前控制項篩選的全部結果，並非只匯出本頁。
   檔名固定 `synthetic-corrective-actions.csv`；另記日期與篩選條件。

驗收時查看真實中文字是否完整；hosted CI 曾有 CJK 字型顯示問題。
本包未附字型，也未宣稱已修復。詳見[中文與匯出檢查](docs/quickstart/README.md#browser-check)。

可先開啟[已產生的純合成 CSV 範例](examples/quickstart/README.md)，核對 2026-10-05 的 36 列快照。

## 3．收工、備份、回退

- 回到啟動視窗按 Ctrl+C，確認 app 與所有寫入程序／worker 已停止。
- 依[備份與還原步驟](docs/quickstart/README.md#backup-restore)，將**八個必要資料庫及已建立的選用 stores**整組備份到新目錄。
- 還原也只能使用新目錄。看到 `RESTORE_REVIEW_REQUIRED.json` 或 `INCOMPLETE.json`
  必須停下檢查，不能為了啟動而直接刪除。
- 程式回退與資料回退要一起核對版本相容性；保留失敗版與最新資料，不能直接蓋回舊 DB。

## 4．今天能確認什麼？

可先執行[隔離自測](docs/quickstart/README.md#isolated-checks)，在全新暫存目錄
產生合成報表、備份與還原證據。其 Flask test-client 結果不等同真人瀏覽器或 Windows 驗收。
基線 `7bb9b264` 的 CI 與[本包的新檢查](docs/quickstart/VALIDATION.md)須分開看；
公司參數、正式主機與寄信尚未驗證，接入前完成[公司確認清單](docs/quickstart/company-checklist.md)。
