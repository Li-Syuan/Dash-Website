# 四情境故障演練與復原

本輪基線為已發布 `45918b243af32cf8d4db966e82bfc6b634bf3771`。
所有演練僅使用臨時 SQLite、合成帳號／資料、自己建立的子程序；沒有
公司 Oracle／LDAP／SMTP、正式排程、系統時間或安全設定變更。

## 一鍵重跑

在 repository 根目錄，用已核准且裝有 `requirements-qa-portal.txt` 的
既有 Python 環境執行。命令不會安裝依賴或啟動網站／背景排程。

```sh
python -B tools/fault_drills.py --list
python -B tools/fault_drills.py
```

需要保留只讀時鐘檢查時，先建立新的輸出目錄；每次使用不同檔名，不覆蓋
舊失敗證據。clock guard 偵測環境無效會回傳 86，不自動重跑。

```sh
python -c "from pathlib import Path; Path('output/fault-drills').mkdir(parents=True, exist_ok=True)"
python -B tests/probes/clock_guard.py --output output/fault-drills/new-run-clock.jsonl -- python -B tools/fault_drills.py
python -B -m unittest discover -s tests -v
git diff --check
```

可用 `--scenario etl-interruption`、`duplicate-schedule`、`database-lock`、
`export-revocation` 選擇單一情境，重複參數可合併選擇。預設完整四情境，
重用 16 個既有測試方法，加 3 個有明確缺口的新方法，共 19 個；不把
subtest、SQL 斷言或內部检查另外加算。退出碼 0 只表示所選情境成功，
1 表示失敗／依賴無法載入，2 表示跳過／缺少預期執行或無效 CLI。
這個入口不是全專案 acceptance、效能 gate 或部署核准。

## 預期與實際

| 情境 | 注入與預期 | 本輪實際 |
| --- | --- | --- |
| ETL 執行中斷 | 終止自己建立的 spawn worker，涵蓋 claim、adapter、snapshot、publication 前後及部分 backfill；未提交資料回滾，未知結果不重跑，已提交收據只重讀 | 通過；原發布與上游快照保留，重啟後沒有不當 adapter replay，SQLite integrity/foreign keys 正常 |
| 重複排程 | 兩個不同 PID 在讀到同一到期 tuple 後、進入真實 claim 交易前同步釋放；只可有一條 adapter chain、一個 timer run／publication，fence 只增一次 | 通過；另一次 tick 及新程序重啟無額外執行。錯過時段合併、disabled 不執行的既有測試亦通過 |
| DB 鎖住 | 獨立 SQLite 連線持有 writer／reader lock，測首次 claim、短暫鎖、adapter 後及 publication commit；有限等待、安全錯誤、無部分成功 | 通過；沿用 5 秒 SQLite timeout，IPC／join 有限等待。首次未建 claim 可在解鎖後用同一請求執行；不確定的 adapter 後結果禁止自動重試 |
| 匯出途中撤權 | snapshot／首列前／序列化／ZIP save／最後 HTTP refresh 時撤銷當前權限；不得交付新下載，失敗不改來源及 audit，暫存檔清除 | 發現最後 refresh 缺少查詢後驗權；修正後 CSV、XLSX、template HTTP 回覆均無 `qa-download`、表格清空、顯示安全失敗訊息。其餘既有檢查通過 |

重複排程測試只把臨時 fixture 的 `next_run_at` 設為已到期，沒有調整 OS
時鐘或正式排程，也沒有啟動背景 worker。測試只在真實交易外加入觀察
barrier；claim／commit／rollback／fencing 沒有 mock。

## 缺陷與最小修正

`LegacyCrudService.query()` 原先只在進入查詢前驗權。QSL 匯出 callback
先準備下載，再查詢表格；若身分在這次 SELECT 期間撤銷，查詢仍回傳資料，
callback 便連同已準備的檔案一起回傳。

新增 HTTP regression 用 SQLite trace callback 在真實最後 SELECT 開始
時移除合成帳號；沒有替換查詢結果或 HTTP callback。修正前 2 個新 HTTP
測試方法中，archive 測試通過；final-refresh 方法的 CSV／XLSX／template
3 個 subtest 都重現下載洩出。原始失敗 log 保留在本輪本機 evidence。

修正僅在 `query()` 組完結果、交付前再次呼叫既有 `_authorize(user, 'read')`，
並核對與初始 actor 相同。拒絕沿用既有 `PermissionDenied('Operation denied.')`
及 callback 的清空／不下載流程。沒有增加角色權限、快取身分、改 callback ID、
改 `/QA_portal/`、改單一 `app.py`、變更依賴或資料 schema。

新增 archive HTTP 測試也在真實 `Workbook.save` 完成時撤權，核對下載未發出、
來源和 audit 沒變，以及 openpyxl 暫存檔 registry 復原。最後 refresh regression
同樣確認來源與 audit 未被此次失敗匯出變更。

## 復原步驟

1. 中斷：保留 DB、request receipt、snapshot、fencing 及未知結果。等待原 lease
   自然到期，再用同一 request 或明確 tick 記錄 `failed/interrupted`。單純 status
   讀取不會自動復原。不得刪除 claim／receipt 或還原舊 DB 來強迫重跑。
2. Backfill：用原 request／日期範圍重送，已 claim 的日期沿用結果，只執行尚未
   claim 的日期。失敗日期須先對帳；全新手動 request 代表新的明確意圖。
3. 重複排程：核對同一 job 的 due tuple、run、publication 與 fence。部署仍須有
   明確的單一 worker owner；本演練沒有替任何正式部署啟用或修改排程。
4. 鎖住：由連線 owner 釋放自己的鎖，依 durable run 判斷是否已 claim。未 claim
   的請求可明確重送；adapter 後／publication timeout 的不確定結果不可自動 retry。
   不強制終止其他人的程序，不刪 DB 或把 browser timeout 當成已回滾。
5. 撤權：此次匯出保持失敗，不重送已生成的 bytes。由授權管理流程核對目前權限，
   若之後合法恢復存取，以新請求重新查詢／匯出；不能沿用舊身分或舊 download。

## 驗證紀錄：2026-10-05

在 dot Linux 雲端電腦，獨立 checkout 執行；結果摘要見 [RESULTS.json](RESULTS.json)。

| 檢查 | Python 3.8.20 | Python 3.10.21 |
| --- | --- | --- |
| 四情境入口 | 19 通過，0 skip；30.778 秒 | 19 通過，0 skip；38.838 秒 |
| 完整 unittest discover | 1125 執行，1119 通過，6 skip；106.341 秒 | 1125 執行，1119 通過，6 skip；105.215 秒 |
| 只讀 clock guard | 有效，無 backstep／monitor error | 有效，無 backstep／monitor error |

6 項 skip 均為 Windows 原生功能：junction 路徑安全 3 項、Job Object
程序樹／指派 2 項、精確時鐘 backend 1 項。它們沒有在 Linux 被冒稱通過。
`git diff --check`、新增／修改 Python 檔的 3.8 grammar、無依賴 `--list` 與
CLI 無效情境拒絕檢查通過。初始 Python 3.12.14 的核心 10 項與 XLSX 13 項
只屬診斷，不取代已批准 3.8／3.10 環境的本輪執行結果。

真實瀏覽器：BLOCKED。既有 Chromium 的一般及獲准 process escalation
preflight 都在 browser 啟動時遇到 `process_singleton socket() Operation not permitted`。
兩次都未 render 頁面，沒有通過的 browser scenario；各自的 fixture server 已停止，
臨時狀態已清除。沒有調整安全設定或以 HTTP 測試冒充 browser 證據。測試用
可選 browser executable 小 patch 及兩次原始失敗結果保留在本機 evidence，
不把它當產品修正。完整 browser 回歸仍待允許的 browser executor。

本輪沒有效能量測或基於時長的門檻判定；上述回歸秒數不是 benchmark。
15% 時間／10% RSS policy 仍未批准、未啟用。

## 保證邊界

- 本輪證明所測單主機 SQLite 與 HTTP 發布邊界，沒有證明 NFS、跨主機、斷電、
  磁碟毀損、正式 Oracle transaction 或 SMTP exactly-once。外部副作用仍需另行
  批准的 outbox／idempotency／對帳契約。
- 撤權檢查不會撤回先前已合法交付的檔案，也不是 provider 與網路傳輸之間的
  分散式原子保證。真實 LDAP／SSO 的撤權傳播及快取必須另行驗收。
- 未測 Windows／MSI；未認證公司 Linux kernel 3.10.0-1160、Python 3.8.13／
  Windows Python 3.10.4 的精確組合。公司 RESTX／binds／Oracle／LDAP／SMTP
  整合沒有接線或資料存取。
- 只完成本機變更與驗證；沒有 push、deploy、upload 或啟用正式工作。
