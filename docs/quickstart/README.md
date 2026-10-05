# QA Portal：Windows 優先上手步驟

[一頁入口](../../START_HERE_TOMORROW.md) ·
[公司接入清單](company-checklist.md) ·
[報表語意](../quality-actions/README.md) ·
[完整備份契約](../deployment/OPERATIONS.md)

## 發布狀態

原 ZIP 完成本地交付後，使用者已要求推送。請以 [PUBLICATION.md](PUBLICATION.md)
及所用 SHA 的 GitHub CI 為準；以下「未推送」是 ZIP 建立當時的歷史敘述。

## 使用範圍

本次 Quickstart 包以 `main` 的 `7bb9b264` 為程式基線，增加操作文件及離線工具。
請使用本次交付 ZIP 中的版本，不把舊文件中的歷史批准當成下一次發布批准。
網站仍從 `app.py` 啟動；其餘 `tools/` 命令為檢查或維護工具。
包內保留執行與測試所需的原始碼、測試、操作文件、範例及 JSON fixtures。
歷史原始 `.txt`／`.log`、DOM 擷取與一次性診斷 scripts 不隨包交付，省略清單見 manifest；
舊文件若連到這些歷史證據，請查
[GitHub 基線](https://github.com/Li-Syuan/Dash-Website/tree/7bb9b264482179a1b31ca32c9ed6737c9b8f51a0)。

- **本機 demo：** 使用公開合成帳號、合成報表及本機 SQLite；可以照本頁操作。
- **公司接入：** 尚缺私有 provider、Oracle／LDAP／SMTP、RESTX 與 binds 契約。
  先完成[清單](company-checklist.md)，不要把 demo 改名當成正式環境。
- 本輪沒有部署、公司連線、MSI 系統修改或新依賴。以下安裝命令只是操作說明，
  由使用者在 IT 核准後自行明確執行。

<a id="setup"></a>
## 1. 準備獨立資料夾與 Python

1. 下載本次 Quickstart ZIP，核對交付的 SHA256，再解壓到全新本機資料夾。
   不覆蓋舊版，不放到 OneDrive 同步目錄、UNC／網路磁碟或共用 NAS。
   保留 ZIP、校驗碼、原有程式與環境；ZIP 不含 `.venv`、資料庫或公司帳密。
2. 確認目錄內有 `app.py`、`requirements-qa-portal.txt` 與 `tools/quickstart_check.py`。
   以下以 `C:\QA-Portal\quickstart-20261005` 示範，請換成自己的解壓目錄。
3. 由 IT 確認可用的 Python 與安裝來源。歷史 Windows 目標為 Python 3.10.4；
   Linux 目標為 kernel 3.10.0-1160／Python 3.8.13。其他 patch 的通過結果
   不等於這些精確目標已驗證。Quickstart preflight 目前只接受 Python 3.8 或 3.10；
   本頁不要求自行下載、升級或降版系統 Python。

下載後先在 PowerShell 算出 ZIP 的 SHA256。把路徑改成實際下載位置，
**手動逐字比對交付訊息提供的 SHA256**；不符就停止，不要解壓執行：

```powershell
Get-FileHash -Algorithm SHA256 'C:\Downloads\Dash_Quickstart_20261005.zip'
```

```powershell
Set-Location 'C:\QA-Portal\quickstart-20261005'
# 若已安裝 Windows Python launcher，可先用 py -0p 列出現有 Python。
# 改成 IT 核准的現有 Python 絕對路徑：
$BasePython = 'C:\PATH-APPROVED-BY-IT\python.exe'
& $BasePython --version
if ($LASTEXITCODE -ne 0) { throw '請核對 Python 路徑。' }
if (Test-Path '.venv') { throw '.venv 已存在，請先核對，勿覆蓋。' }
& $BasePython -m venv .venv
if ($LASTEXITCODE -ne 0) { throw '建立虛擬環境失敗。' }
$Python = Join-Path $PWD '.venv\Scripts\python.exe'
& $Python --version
```

只有在使用者決定安裝、IT 已核准既有 pins 與來源後，才執行：

```powershell
& $Python -m pip install -r requirements-qa-portal.txt
if ($LASTEXITCODE -ne 0) { throw '依賴安裝未完成，請保留錯誤供 IT 檢查。' }
& $Python -m pip check
if ($LASTEXITCODE -ne 0) { throw '依賴相容性檢查未通過。' }
```

這份 requirements 包含已核准的 Dash 2.9.1、Flask 2.2.3、Werkzeug 2.2.3、
Flask-Login 0.6.2、DBC 1.4.1、DMC 0.12.0、Plotly 5.13.1、setuptools 57.5.0
與 openpyxl 3.1.5；它不是完整 transitive lock 或安裝成功保證。
無網路／公司套件鏡像未核准時，請由 IT 提供相同 pins 的核准來源，不改版本來湊過。
不要使用歷史 `requirements.txt` 升級公司環境，也不要另裝 Flask-RESTX、Oracle 或 LDAP 套件。
全程使用 `$Python` 的絕對路徑，不需要 `Activate.ps1`、管理員權限或調整 Execution Policy。

## 2. 設定全新 demo，先檢查再啟動

在同一 PowerShell 視窗、同一解壓目錄執行。先確認沒有 app 使用此目錄，
且此視窗未載入公司 `REPORTING_*`／secret 設定；不確定時先交 IT 核對。
`local-demo-state` 只能是這次新的本機資料目錄，不能指向既有公司資料。

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
if ($LASTEXITCODE -ne 0) { throw 'Preflight 未通過，先處理列出的原因。' }
& $Python -B app.py
```

- 環境變數只作用於此 PowerShell 及它啟動的子程序；請勿寫入系統全域設定。
  若此視窗已載入公司 `REPORTING_*` 設定，先交 IT 核對，別把公司 secrets 帶入 demo。
- `--new-demo` 表示「尚未建立、準備首次啟動的 state」。父目錄須由你明確建立；
  工具只讀檢查，不幫你建資料庫、修復資料或啟動網站。已有資料不能靠這個旗標忽略。
- Windows 目錄須由 owner／IT 檢查 ACL，確保只有核准人員能存取；工具的基本權限檢查
  不等於完整 Windows ACL 驗收。Linux 的新 state 父目錄須為 owner 私有（例如 `mkdir -m 700`）。
- `REPORTING_STATE_PATH` 必須是絕對本機檔案路徑。設定錯誤時停止處理，勿刪除既有 DB。
- 新報表的開關為 `REPORTING_ENABLE_QUALITY_ACTIONS=1`。另一個 inspection 範本需要時
  才將 `REPORTING_ENABLE_REPORT_TEMPLATE` 設為 `1`，並在停止後重新啟動同一 `app.py`。
- `app.py` 綁定 `127.0.0.1:8050`，無 debug/reloader。正常啟動會建立本機合成 state，
  並啟動由 launcher 管理的合成 worker；新資料的 job schedules 預設停用。
  舊資料可能保留已啟用排程，重開前要檢查。不要對外開放公開 demo 帳號。

<a id="browser-check"></a>
## 3. 瀏覽器操作與目視驗收

用**自己的 Windows 瀏覽器**開啟 `http://127.0.0.1:8050/login`：

1. 輸入 `demo-admin`／`demo-only`，按登入按鈕。
2. 在目錄的 Quality 分類選 **Corrective action aging**，或直接開啟
   `http://127.0.0.1:8050/QA_portal/quality-actions`。
3. **先選 As of date。** 預設是固定的 **2026-10-05**，不是今天；
   要看 2026-10-06 快照就明確選該日。日期會影響開案、結案與逾期判定。
4. 選擇 Status（Open／Closed）、Priority（Low／Medium／High），
   Search 可找 case ID 或 finding；再按 **Apply filters**。
5. 檢查 open／closed／open overdue 數字；它們涵蓋全部符合結果。
   **Previous／Next** 只切分頁。逾期採日曆日，到期日當天不算逾期，並非公司 SLA。
6. 按 **Export filtered CSV**。CSV 依當下控制項重新取得 server 資料，包含全部符合列，
   不限目前分頁，也不採用瀏覽器表格修改。建議先 Apply 再匯出，確保畫面與條件一致。
7. 下載檔名固定為 `synthetic-corrective-actions.csv`，不含快照時間。
   記錄 As of date、篩選條件與來源版本。Excel 若出現亂碼，使用「資料 → 從文字/CSV」
   並選 UTF-8；核對日期、中文、列數及標題。零筆結果可正常匯出僅標題的 CSV。
8. 登出後確認受保護內容不能查詢。可另用 `demo-user-a` 查看組織 A，
   `demo-user-b` 查看組織 B；此新報表每個角色都是唯讀，無跨組織管理員特權。

**中文顯示是實機驗收項目。** Hosted CI 的截圖曾有 CJK 字型不足問題。
請實際看中文標題／導覽與匯出的中文字，確認沒有方框、空白或缺字；瀏覽器與 OS
使用可用的 fallback 字型。本包沒有附字型、沒有自動安裝字型，也沒有宣稱 screenshot
字型問題已修好。缺字時記下瀏覽器／OS 與畫面，交 IT 確認核准的字型環境。

另可在瀏覽器查看 `http://127.0.0.1:8050/healthz` 與 `/readyz`：
前者為程序存活，後者為本機 stores／resource／worker readiness。
HTTP 200 不能證明公司 Oracle、LDAP、SMTP 或正式部署健康。

<a id="restart"></a>
## 4. 停止與下次重開

回啟動 PowerShell 按 Ctrl+C，等待程序退出。備份前要確認沒有其他視窗、服務、排程
或 worker 寫入同一組 state；關閉瀏覽器不等於停止 server。
若停止報錯或仍有寫入者，先保留現場查明，不宣告已停機、不執行備份。

下次在相同解壓目錄開啟 PowerShell，重設這些**程序層級**變數，續用原本完整資料：

```powershell
$Python = Join-Path $PWD '.venv\Scripts\python.exe'
$env:REPORTING_MODE = 'demo'
$env:REPORTING_STATE_PATH = Join-Path $PWD 'local-demo-state\workspace.sqlite'
$env:REPORTING_ENABLE_QUALITY_ACTIONS = '1'
$env:REPORTING_ENABLE_REPORT_TEMPLATE = '0'
$env:REPORTING_SESSION_COOKIE_SECURE = '0'
& $Python -B tools/quickstart_check.py --profile demo --quiesced
if ($LASTEXITCODE -ne 0) { throw '原資料檢查未通過，請勿建立替代 DB 掩蓋問題。' }
& $Python -B app.py
```

續用時沒有 `--new-demo`；檢查的是完整現有資料組。`--quiesced` 表示你已確認所有寫入者
停止，工具不能替你證明停機。重啟通常要重新登入。
既有資料的唯讀檢查需要 SQLite 3.22 以上；若收到 `sqlite_immutable_support_required`，
交 IT 核對 Python 所帶的 SQLite，不切回 `--new-demo` 或自行替換 DLL 來繞過。
若要全新練習，另選新的資料目錄，保留原資料；勿刪原 DB 來重置。

<a id="backup-restore"></a>
## 5. 完整備份與隔離還原

以下只用於本機合成 demo，並且**所有請求、寫入程序、worker 與長期連線均已停止**。
`--quiesced` 是操作者聲明，工具不會替你停止其他程序。

主檔不是整份資料。demo 至少有八個 stores：主 `workspace.sqlite`；
`.qa/` 的 `qsl.sqlite`、`jobs.sqlite`、`report_builder.sqlite`；
`.operations/` 的 `operations.sqlite`、`job_monitor.sqlite`、
`job_monitor.sqlite.leases.sqlite`；以及同層的 `workspace.sqlite.etl.sqlite`。
若建立了 operations 的 `fixture-sqlite-a/b/c.sqlite`，也必須保存。
請使用整組工具，不直接複製正在使用的 DB／WAL 檔。

在已停止的 app 所用 PowerShell，確認 `$env:REPORTING_STATE_PATH` 正確：

```powershell
# 此標籤是備份說明，不是工具認證過的 Git commit。
$Release = 'quickstart-20261005-local'
$Suffix = [guid]::NewGuid().ToString('N')
$Backup = Join-Path $PWD ('backup-' + $Suffix)
& $Python -B tools/deployment_check.py backup --state $env:REPORTING_STATE_PATH --destination $Backup --release $Release --profile demo --quiesced
if ($LASTEXITCODE -ne 0) { throw '備份失敗：保留現場，先查原因。' }

# 不要預先建立此目錄；restore 只接受全新目的地。
$Restore = Join-Path $PWD ('restore-review-' + $Suffix)
& $Python -B tools/deployment_check.py restore --backup $Backup --destination $Restore --expected-release $Release
if ($LASTEXITCODE -ne 0) { throw '還原失敗：保留不完整目錄供檢查。' }
```

保存備份的實際位置、程式來源版本、環境與任何本機修改。備份內有完整資料，
只留在受保護的本機位置，不放進 Git、交付 ZIP 或公開附件。

成功還原會留下 `RESTORE_REVIEW_REQUIRED.json` 並阻擋 app 啟動；失敗／部分完成
可能留下 `INCOMPLETE.json`。**不要直接移除 marker，不要改 `REPORTING_STATE_PATH`
啟動此還原組，也不要覆寫失敗目的地重試。** 先離線核對未知／進行中的工作、
外部結果、lease／fencing、保留的排程與程式／schema 相容性，再由 owner 明確批准切換。
本 quickstart 沒有自動啟用還原資料的步驟。

### 回到舊版時

1. 升級前保留舊程式、舊 `.venv` 與停機後完整備份；新版本使用另一資料夾。
2. 新版出問題先停止它，保留其資料與未確定工作，不能把舊 DB 直接蓋上去。
3. 用與備份 storage-source fingerprint 相容的工具，將升級前備份還原到新目錄。
4. 分別確認**程式版本**與**資料格式**相容，再離線對帳、取得切換批准。
   換回 Python 檔案不會自動降版資料；本工具無 `--force`、原地覆寫或任意 schema downgrade。

完整限制（WAL、未知 sibling、checksum、fingerprint、外部效果）以
[OPERATIONS.md](../deployment/OPERATIONS.md) 為準。

<a id="isolated-checks"></a>
## 6. 可選：隔離自測與回歸

已有核准依賴時，可先用全新暫存目的地驗證練習流程；不要預先建立該目錄：

```powershell
$DrillDir = Join-Path $env:TEMP ('dash-quickstart-' + [guid]::NewGuid().ToString('N'))
& $Python -B tools/quickstart_demo.py --destination $DrillDir --as-of '2026-10-05'
if ($LASTEXITCODE -ne 0) { throw '隔離自測未通過，保留輸出以便檢查。' }
```

這個工具只在新目的地建立自己的合成 state、範例 CSV、備份、還原與檢查證據；
不採用你的 app state、不啟動 HTTP server／worker，也不連公司系統。
登入／路由／健康檢查使用 Flask test client，報表匯出走服務層，**不是真實瀏覽器操作**。
為驗證回退保護，工具會刻意把 `source` 主資料庫標成未來 schema 999；
`restored` 保留還原 review marker。**兩個目錄都不能拿來啟動 app**。
完成後只讀 `evidence.json` 與範例 CSV，勿把演練資料當正式 demo state。
自測成功不等於 Windows、完整回歸、公司接入或效能驗收通過。

如需完整 Python 回歸，在核准環境執行：

```powershell
& $Python -B -m unittest discover -s tests -v
```

記錄 Python patch、OS、source、passed／failed／skipped／unrun。真實瀏覽器的 50 情境
有獨立[操作與 gate 說明](../../tests/browser/README.md)；不要以 test-client 或手動幾步取代。
未安裝的工具交 IT 核准，本 quickstart 不自動補裝。

包內另附 [已產生的純合成 CSV 範例](../../examples/quickstart/README.md)，可先檢視內容與欄位。

## 7. 快速排查與證據界線

`quickstart_check.py` 的 stdout 是 JSON。exit 0／`status: passed` 只表示
`local-demo-preflight-only` 範圍通過；exit 2／`status: blocked` 要先處理固定診斷碼。
若使用 `--config-json`，該 JSON 完全取代檢查所讀的程序環境，只接受已知
`REPORTING_*` 字串設定；它不會把設定套用到之後的 `app.py`。
一般初次使用請照上面的同視窗環境變數步驟，避免檢查與啟動讀到兩份不同設定。
`--profile company` 會阻擋並列出尚待正式整合的界線，不是公司 ready 訊號。

| 狀況 | 安全下一步 |
| --- | --- |
| 找不到 Python／模組 | 核對 `$Python` 是否來自此包的 `.venv`，核對核准 pins 與 `pip check`；不改公司 Python |
| Preflight 非零 | 看安全錯誤碼；核對模式、完整路徑、品質報表旗標、新建／既有模式及 marker；不直接啟動繞過 |
| 8050 被占用 | 找出是否為自己已啟動的 demo，先正常關閉；不殺未知程序、不改網路設定 |
| 找不到報表／404 | 確認在啟動 app 的同一視窗設了 `REPORTING_ENABLE_QUALITY_ACTIONS=1`；停下重開後重新登入 |
| 查不到預期資料 | 確認日期與篩選；預設日期固定 2026-10-05，報表資料為合成，不代表真實案件 |
| 匯出遭拒或登入失效 | 重新確認身分與權限；不放寬授權、不改 cookie 壽命或系統時鐘來消除錯誤 |
| readiness 503／SQLite locked | 確認完整 stores、restore marker 與其他寫入者；保留資料與錯誤，不刪 claim／DB |

基線 `7bb9b264` 的 [GitHub CI](https://github.com/Li-Syuan/Dash-Website/actions/runs/37261836522)
已記錄 Python 3.8.18／3.10.21 各 1186 passed、6 Windows-only skipped，
及真實 Chrome 50/50。這是**原基線**證據，不是新增 quickstart 工具的測試結果。
本包新檢查的實際環境／結果以[同包驗證紀錄](VALIDATION.md)為準；沒有新增 Windows 實機驗證。
公司精確 OS／Python patch、Oracle／LDAP／SMTP／Flask-RESTX／SQLALCHEMY_BINDS、
外部 exactly-once、正式部署仍待驗收；15% 時間／10% RSS 門檻未批准且未啟用。
