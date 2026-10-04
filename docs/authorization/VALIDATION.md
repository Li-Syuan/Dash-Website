# 第 8 版候選驗證歷史

**本頁保留第 8 版的原始通過／失敗與當時限制。第 9 版的根因、修正及原生完整
驗收結果見 [VALIDATION_V9.md](VALIDATION_V9.md)。以下未解事項已有後續證據，
勿將本歷史表當成本版最新結論。**

本次由 Library 第 7 版建立獨立副本，在 MSI 使用隔離合成帳號與資料驗證。
未更動原有未提交 checkout，未 push、merge 或部署；沒有呼叫 Oracle、LDAP、SMTP、
公司資料或真實通知。`app.py` 仍是唯一產品入口，既有釘選依賴未升級。

## 最終結果

| 驗證 | 結果 | 證據 |
| --- | --- | --- |
| Windows / Python 3.10.22 完整 unittest | **868 通過、0 失敗、0 錯誤、1 跳過**（共 869；54.905 秒） | `evidence/windows-python310-final.json` 及 `.txt` |
| MSI WSL / Python 3.8.20 完整 unittest | **869 通過、0 失敗、0 錯誤、0 跳過**（61.937 秒；只讀 session 診斷 wrapper） | `evidence/wsl-python38-final.json` 及 `.txt` |
| 入口 HTTP/transport 矩陣 | **263 / 263 通過**，5 unittest；12 PageSpecs、49 callbacks、17 routes | `entrypoint-coverage.json` |
| 真實 Chrome 154.0.8037.95 / Playwright 最後 full run | **28 通過、4 失敗**（靜態 Plotly 傳輸／等待問題，詳見下文） | `evidence/browser/results.json`、screenshots、DOM、server log |
| 同產品來源的前一 full run | **31 通過、1 失敗**（console 總檢查誤計預期拒絕；保留原始結果） | `evidence/browser-prior-complete/results.json` |
| 報表定向瀏覽器複驗 | **2 通過、0 失敗、30 未跑**；僅登入與表格 refresh/export，圖表仍有傳輸錯誤 | `evidence/browser-report-focused/results.json` |
| Python 3.8 語法、來源 hash、完整 ZIP integrity | 包裝時檢查，詳見 `MANIFEST.json` | 所有檔案相對路徑與 SHA256 |

Windows 唯一 skip 為 `test_fork_competing_leases`，理由是平台無 fork。
Windows 最終執行沒有 unraisable exception 或程序關閉 traceback。
Python 3.8 最終亦無 unraisable exception／關閉 traceback，fork 與 Node 測試均執行。
測試數量是 unittest cases；263 個授權檢查是其中 5 個 cases 的子檢查，不重複加總。

## 真實瀏覽器範圍

瀏覽器由新建 Chrome context 操作真實 DOM、表格、彈窗、按鈕、上傳與下載。
Fixture 在自動分配的 loopback port 匯入 `app.py`；未啟動 worker/scheduler。
涵蓋登入與錯誤密碼、CRUD、取消／關閉／重新開啟、連點與重播、版本衝突、
archive/restore、同組織共讀、peer 禁寫、跨租戶改 ID、撤權 Save、QSL Query 後
篡改 ID、匯入匯出、精靈上下步、瀏覽器上一頁／下一頁、行動版與登出後重播。
ETL/operations 的頁面及拒絕行為有瀏覽器驗證；背景競爭、publication、fencing
與 ETL action/retry/backfill 由 Python/process 測試驗證，不聲稱逐一以 GUI 執行。
`results.json` 將 browser-context request replay 明確標為 supplementary，HTTP
矩陣不列為瀏覽器測試。逐項狀態、耗時、未處理 JS 錯誤及預期拒絕回應另有記錄。

最後 full run 的失敗為 `22-report-refresh-export`、`23-etl-and-operations-render`、
`26-browser-history-navigation` 的 request-settle 等待，以及 `29-no-external-effects-or-page-errors`
偵測到 Plotly static GET 失敗。前一輪在相同產品/assets SHA256 下已完成上述
三個操作，但 console 總檢查把正確的 401／固定 Login required 拒絕文字判為錯誤。
這兩輪都保留真實結果，**不合併宣稱單輪 32/32 全過**。
定向重測改用 90 秒 fixture settle deadline，對齊 static reader 三次 20 秒重試
上限；其他操作與授權斷言不變。表格 refresh 與真實瀏覽器下載 CSV 成功（12 列、
含 revenue），但 Plotly 圖表仍有 `ERR_FAILED`／ChunkLoadError。定向結果的 2 pass
僅代表所述操作，未宣稱 chart 或 network 通過。其餘 30 個情境明列未跑。
因此原生靜態傳輸及最後 full run 的環境失敗仍屬未解限制。

**原生靜態傳輸未通過。** 此 MSI 的 Chrome/Node 接收部分大型本機 Dash JS 時出現
`ERR_CONNECTION_RESET`；CLI 套件取得亦曾遇 TLS 憑證驗證失敗，未停用 TLS 驗證。
使用既有 Playwright 套件，並明確啟用 fixture-only HTTP/1.0 和 static bridge：
PowerShell 從同一 owned loopback HTTP URL 取得相同原始 bytes，最多重试 3 次，
再交由 Chrome 正常載入。`static-delivery.json` 留存 URL path、大小、SHA256、
attempt count。僅 `/assets/` 與 component suites 使用此方法，dynamic callbacks
仍是原生瀏覽器 POST。這是執行環境的靜態傳輸限制，不代表原生靜態網路已通過。

撤權 Save 首次重現 pinned Dash 2.9.1 的 `body stream already read`。服務端已
正確回傳 401 且資料未變；相容 asset 修正後，仍保持 401，前端不再發生該未處理
例外。預期的 401 resource error 與固定 `Login required` 訊息會明確分類，
不將授權拒絕改成成功回應，也不忽略其他 page errors。

## 重現與歷史

```text
python -B -m unittest discover -s tests -v
python -B tests/test_entrypoint_coverage.py --write-coverage docs/authorization/entrypoint-coverage.json
node tests/browser/acceptance.cjs
```

瀏覽器先依 [tests/browser/README.md](../../tests/browser/README.md) 設定已核准的
Python、Playwright 和瀏覽器。MSI 最終使用 `QA_HTTP10=1`、`QA_STATIC_BRIDGE=1`、
`QA_STATIC_READER=powershell`；重現原生靜態傳輸檢查時關閉 bridge。

第 7 版未改動 source 在本機 Windows 的初次完整執行為 818 cases、1 failure、
73 errors、1 skip；主要為未關閉 SQLite 導致 Windows 目錄清理失敗，另有編碼
問題。新 lifecycle 與測試清理改善後，最終完整執行乾淨結束。
早期 Python 3.8 放在 WSL 掛載的 Windows 目錄時，曾有 2 failures、1 error、
23 skips（Node 不在該環境 PATH、subprocess/process 同步逾時及一個模擬回應失敗）。
後续改用 MSI 原生 Linux 暫存檔案系統，來源逐檔驗證 hash，加入 task-only Node
22.14.0（官方 archive SHA256 核對）；沒有放寬測試逾時或改產品依賴。
早期失敗保留在 `evidence/earlier-attempts/`，不與最終結果混算。
原生 Linux 儲存的第一次 869-case run 另有單一非預期 401，位置是 maintenance
pagination 的首次 HTTP 請求；Windows 同碼通過。加入只讀 session 診斷後，
產品與測試 source 未更動的完整重跑 869/869 通過，未記錄到 session 過期事件。
WSL clock 倒退是待證假說，**根因尚未確認**，不能將重跑通過當成已修復此間歇現象。
診斷 wrapper 呼叫原 session decision、只對既有空 session 補記過期資訊，不記
cookie/token、不調時鐘、不延長期限、不替換身分、不改測試斷言；原始程式列於
`evidence/session_diagnostic.py`。一次診斷 runner 因 multiprocessing main guard
遺漏而中止，修正 runner 後才完成最終重跑；該中止不是完整 suite 結果，其 metadata
亦保留。交付前逐檔驗證最終 Python 3.8 所用 source hash 與 ZIP source 相同。
`authorization-before.txt` 記錄新增回歸測試在修改前暴露的問題；`authorization-after.txt`
及 `governance-after.txt` 是對應修正後的 focused runs，最終完整 suite 才是驗收依據。

## 未執行

- 公司精確 patch runtimes Python 3.8.13 / 3.10.4；本次實際版本為上表所列。
- 真實 Oracle、LDAP、SMTP、正式通知、正式資料、正式排程與外部副作用。
- 生產部署、反向代理、多節點運維、所有 OS/browser 組合與真實瀏覽器壓力測試。
- 每個合法功能的所有排列；coverage 清單是入口與重要隔離情境的證據，不是形式證明。

遷移與已知 trusted-provider 邊界見 [CHANGES_AND_MIGRATION.md](CHANGES_AND_MIGRATION.md)。
