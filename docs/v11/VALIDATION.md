> Historical v11 record. See [current publication scope](../acceptance/PUBLICATION.md).
> Full raw evidence and local checkpoint inventories are preserved separately.
> Only the sanitized browser result JSON needed by regression tests is retained here.

# 第 11 版驗證與交付

第 10 版 main 基線 `e4a9681137f4762e4fcdcfee356351b7786a9420` 的獨立本機候選。
沒有 v11 commit、push、merge 或部署；原始 D 槽 checkout 未覆寫。
基線 GitHub CI 37196698298 的兩個 901-case 通過結果屬於 v10，不能充作 v11 CI。

## 最終結果

| 驗證 | 實際結果 | 分類 |
| --- | --- | --- |
| Windows Python 3.10.22 完整回歸 | 961 通過、1 平台 skip、0 失敗；108.421 s | 通過；fork-only 案例在 Windows 未執行 |
| MSI WSL Python 3.8.20 完整回歸 | 961 通過、1 失敗、0 skip；共 962 項，程序 exit 1 | 環境無效，不算合格 Python 3.8 gate |
| WSL 唯讀 ClockGuard | 3 次 backstep；gate exit 86；111.621 s | 保留原始紀錄；沒有重跑、改時鐘／time service／cookie |
| 原生 Chrome 154.0.8037.95 | 40 通過、0 失敗、0 未跑；Python 3.10.22、Node v24.21.0 | 真實 UI；static bridge 關閉、原生 HTTP |
| 預設入口 HTTP matrix | 12 頁、49 callbacks、263／263 情境 | 通過；另列於 UI 證據之外 |
| opt-in 範本 HTTP matrix | 13 頁、51 callbacks、271／271 情境 | 通過；另列於 UI 證據之外 |

完整 suite 為 962 個 unittest 方法，較基線增加 61：大型 XLSX 13、
部署 21、報表範本 19、factory／長匯出權限 7、HTTP 發布撤權 1。
61 個新案例在 Windows 與 WSL log 皆為通過；WSL 整體仍有既有案例失敗且環境 gate 無效。
targeted reruns 與 matrix 子情境不重複加總。Windows／WSL 的 unraisable
例外計數為 0／0；結束後 traceback 為 0／0。

WSL 唯一失敗為既有
`test_kill_before_snapshot_commit_keeps_no_partial_snapshot`：預期租約到期後
`error_code='interrupted'`，實際為 `None`。測試與 ETL 實作逐位元與 v10 相同；
「計算一次剩餘時間後 sleep」可能受同次觀察到的 wall-clock backstep 影響，
但未記錄該斷言的即時 deadline，不能斷言唯一原因。此案例仍列失敗，沒有
修改 clock、測試等待條件或重跑。詳見 [WSL 失敗分析](WSL_FAILURE_ANALYSIS.md)。

全部測試使用隔離合成帳號／資料與自有程序。Chrome 的 fixture 已停止，state
已移除；renderer page errors 0，沒有外部 origin 呼叫。主程式、測試和 harness
以 SHA256 與最終交付核對；見 `evidence/final-integrity.json`。文件／證據收集
不改動凍結程式。Python 3.8 AST 相容檢查、原 checkout hash 和 diff check
的實際結果亦保存在該檔，並非用 AST 取代實際 Python 3.8 執行。

原始結果位於 `evidence/regression/` 及 `evidence/browser/`。瀏覽器下載與
screenshots 放在 companion evidence ZIP，依 EVIDENCE_INDEX 解壓重建。
所有既有版本的證據與失敗紀錄皆保留；新文字證據只清除私人主目錄與
隔離暫存路徑，對應原始／sanitized hashes 記錄在 final-integrity。

第一輪 v11 Chrome 為 **35 通過／5 失敗／0 未跑**：新測試錯將 Quality
catalog 卡片入口當成頂部 nav link，導致入口案例及四個依賴案例失敗。
依註冊契約修正 harness 為點擊實際卡片 href，保留標題及功能斷言後，
才重跑完整 40 項。產品程式未因這次 harness 修正而改動。原始失敗 run、
screenshots、結果及修正前 harness 保存於 `evidence/browser-initial/` 和
`evidence/regression/browser-initial-harness/`，不覆寫或重標為成功。

## 大型 XLSX 的實測取捨

同一機器／已核准套件，基線與候選各一次 warmup、三次 timed samples，
各大小使用獨立 export 程序；seed／驗證另開程序。RSS 是 Windows 原生程序
峰值 working set，不是 tracemalloc，也不含瀏覽器／Dash base64 的額外配置。

| 筆數 | 耗時中位數：前 → 後 | 峰值 RSS 中位數：前 → 後 | RSS 降幅 |
| ---: | ---: | ---: | ---: |
| 100,000 | 11.018 → 10.829 s | 150.047 → 43.551 MiB | 71.0% |
| 200,000 | 21.904 → 23.679 s | 262.719 → 47.703 MiB | 81.8% |

20 萬筆耗時增加 8.1%，不能宣稱整體加速；10 萬筆小幅差異只作描述，三個樣本
不構成 SLA。暫存檔觀測峰值 10 萬筆 50.386 → 67.330 MiB，20 萬筆
101.936 → 135.951 MiB，約增加 33%。這是用暫存 I/O 換取較低記憶體。
phase-based disk probe 不等於 OS-wide disk high-water counter。

共 16 份 warmup／timed 工作簿均逐列核對數量、ID 順序、數字／字串型別、
前導零、Unicode、公式／錯誤字串與日期樣式文字；內容 fixture hashes 相符。
QSL 原 schema 沒有 native date/datetime 欄位，不擅自轉換 Excel 日期型別。
大型資料透過既有可信 constructor 明確提高 cap；預設 50,000 筆上限不變，
超限拒絕另有證據。四次 disk probes 皆確認 owned handles 關閉及 openpyxl
temporary registry 恢復；沒有重跑或排除較慢樣本。
完整方法、p95、commit memory 與原始樣本見 [XLSX 指引](../large-export/XLSX_EXPORT.md)。

## 審查發現與回歸

1. 完成 CSV／XLSX／範本匯出後，最終 query 偵測帳號移除／組織變更／角色
   變更，原 callback 仍發布 download。先以真實 HTTP 重現 9／9 子情境失敗，
   再單獨處理 PermissionDenied，清空資料且不送下載。修正後 unified 21 項通過，
   並包含於最終完整回歸。原始 failure log 為 `publication-before-fix.txt`。
2. 關閉 QSL／ReportBuilder 的實際持久連線，舊 readiness 仍回傳 200；兩項
   回歸先失敗。加入受既有鎖保護的 SELECT 1、50 ms 取得鎖上限，正確回傳
   sanitized 503；四項聚焦檢查與最終回歸通過。詳細初始／修正結果在
   [部署驗證](../deployment/VALIDATION.md)。
3. 新 fresh-policy 邊界使原撤權測試的「再用已撤權 actor 查資料」後置檢查失敗。
   已保留拒絕斷言，再以獨立同組織 reader 核對資料未改動；未放寬 service。
   部署初始 worker 屬性差異亦保留在部署驗證紀錄，未重標為最初即通過。

## 交付與尚未驗證範圍

- [變更與遷移](CHANGES_AND_MIGRATION.md)、[入口 coverage](ENTRYPOINT_COVERAGE.md)、
  [部署操作／rollback](../deployment/OPERATIONS.md)、[可複製範本](../report-template/README.md)。
- 主 ZIP 是完整 source/tests/config/docs；每份證據 ZIP 小於 10 MB，保留全部
  歷史與本輪 browser 二進位。EVIDENCE_INDEX／MANIFEST／PENDING_PUSH 與外部
  `delivery.json` 提供檔名、長度、SHA256、確切未提交變更及重建方法。
- Library identity `libfile_f6e3a5e35ac481918aad5a9c86971127` 最後核對為 version 8。
  官方 batch helper 已知於 prepare 前回報 `Library prepare_uploads is not available`。
  本輪依指示不反覆嘗試或改走 direct／外部傳輸；沒有新 Library version。
- 公司 Oracle／LDAP／SMTP、實際 RESTX 程式、正式資料／通知、反向代理、多節點、
  正式 cutover、任意 schema downgrade、精確公司 OS／Python patch 均未跑。
  新還原流程只對隔離合成資料完成 before-image drill，不代表正式部署認證。
- v11 GitHub CI 未跑，因為沒有 push 授權；若 WSL clock gate 無效，必須明列此
  Python 3.8 環境限制，不能引用 v10 CI 或 unittest 的 OK 混充本輪有效 gate。
