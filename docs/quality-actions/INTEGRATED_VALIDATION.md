# 最終整合驗證：報表接手與四情境故障演練

2026-10-05，dot Linux 雲端獨立 checkout，僅合成帳號／資料／SQLite。

## 版本與整合

- 公開 main 基線：`45918b243af32cf8d4db966e82bfc6b634bf3771`
- 獨立新報表 commit：`e01cfc4ebf070dc2d297838fd1f99327b9dd6b17`
- 獨立故障修復 commit：`7e1f39b1893e9829b89959f6a977f2af11757977`
- 故障修復在報表分支上的 cherry-pick：`ba8a2b766d6e0b07053f5e5ac37020b7249b516e`
- 整合 branch：`agent/handoff-integrated`。無合併衝突，保留原兩個獨立成果。

以下驗證全部針對 `ba8a2b7` 的整合程式。其後 commit 只新增本驗證紀錄、
manifest 及文件導引，沒有再改 runtime 或測試。這是 local checkpoint，沒有
push、upload、遠端merge、deploy 或正式整合。

## 最終實際結果

| 檢查 | Python 3.8.20 | Python 3.10.21 |
| --- | --- | --- |
| 完整 unittest discover | 1162 項：1156 通過、6 skip、0 failure/error；101.510 秒 | 1162 項：1156 通過、6 skip、0 failure/error；97.189 秒 |
| 四情境故障入口 | 19 通過、0 skip；28.580 秒 | 19 通過、0 skip；28.061 秒 |
| 故障入口只讀 clock guard | infrastructure_valid=true、exit=0 | infrastructure_valid=true、exit=0 |

雙 report opt-in 的入口授權矩陣：Python 3.8.20，5 個測試方法／279 個
內部檢查全通過，0 failure/error；包含 quality_actions 路由與 query/export。
37 個新報表測試已包含在完整回歸數字內；19 個故障方法也重用完整回歸中的
測試，不另加總成更多獨立測試。這些回歸耗時不作效能benchmark或門檻。

6 個 skip 全為現有 Windows-only 測試：3 個 junction path 邊界、2 個
Job Object 指派／子程序樹、1 個 native precise-clock backend。
`git diff --check`、新增/修改 Python 3.8 grammar、browser JavaScript syntax
檢查通過。完整套件版本見本目錄 runtime JSON；所有明訂 pins 保持不變。

## 已交付的功能與修正

- [矯正措施帳齡報表](README.md)：新 `/QA_portal/quality-actions`，獨立
  opt-in，日期快照／優先級／狀態／文字篩選、分頁、全篩選結果KPI與安全CSV。
  仍以單一 `app.py` 啟動，不增加正式權限或公司adapter。
- [最初接手缺漏](HANDOFF_REVIEW.md)：事前記錄開關接線、browser/入口coverage
  延伸、歷史授權文字與合成／公司整合步驟的缺口，再補最小必要文件。
- [四情境故障演練](../fault-drills/README.md)：ETL中斷、不同process重複排程、
  DB lock、匯出中撤權；新入口 `python -B tools/fault_drills.py`。
- QSL 最後查詢刷新階段新增交付前驗權，修掉在最後 SELECT 期間撤權仍可能
  回傳已準備下載的缺口。CSV／XLSX／template均有實際HTTP regression。
  新帳齡報表也有lazy fetch和CSV末格序列化期間撤權測試，均拒絕發布。

## 瀏覽器與剩餘界線

真瀏覽器 **BLOCKED / 未通過**。已新增10個新報表互動場景，與原有40個
場景合計50；預設及核准escalated Chromium啟動均報
`process_singleton socket() Operation not permitted`。受支援 managed browser
對隔離loopback也回 `net::ERR_BLOCKED_BY_CLIENT`，不再重試或繞過限制。
兩次原始失敗保留，50場景全部未跑。最終helper靜態預期已核對，仍未在真實
瀏覽器執行；整合後也沒有宣稱新的瀏覽器pass。HTTP與service測試不取代它。

MSI／Windows、公司精確Python/OS版本、Oracle／LDAP／SMTP／Flask-RESTX／
SQLALCHEMY_BINDS、公司SLA及production部署均未驗證。15%時間／10%RSS policy
仍未批准／未啟用。不能把這些Linux合成測試稱為全專案或公司整合完成。

## 重現與保留證據

在已核准且裝有既有pins的環境，以該環境Python執行：

```sh
python -B -m unittest discover -s tests -v
python -B tools/fault_drills.py
python -B tests/test_entrypoint_coverage.py --report-template --quality-actions --write-coverage output/integration/entrypoints.json
git diff --check
```

真瀏覽器待允許的環境，以 `QA_REPORT_TEMPLATE=1 QA_QUALITY_ACTIONS=1` 執行
`node tests/browser/acceptance.cjs`；現有Chrome可用channel，現有Chromium可用
`QA_BROWSER_EXECUTABLE`。不安裝新版、不改OS／安全／網路設定。

`output/integration/` 保留兩版完整log、兩版故障log與clock JSONL及入口矩陣；
`output/handoff/` 保留report-only結果；`output/playwright/quality-actions/`
保留兩次browser阻礙和fixture/static核對。全部ignored，不包含在commit。
[INTEGRATED_EVIDENCE.json](INTEGRATED_EVIDENCE.json) 保存摘要、來源與log hashes。
原report-only及fault-only的紀錄維持各自scope，不以整合結果改寫舊結果。
