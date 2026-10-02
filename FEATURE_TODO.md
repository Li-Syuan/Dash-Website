# QA Portal 功能進度與驗收清單

更新：2026-10-03（台北）。此清單區分「程式／自動測試完成」與「公司環境驗收」。不把合成資料測試稱為正式上線。

狀態：TODO＝未開始；IN PROGRESS＝實作中；DONE＝指定範圍已有測試證據；NEEDS ACCEPTANCE＝需部署／使用者驗收。

| 功能 | 狀態 | 操作入口／實作 | 完成證據 | 尚未包含 |
|---|---|---|---|---|
| 原版 QSL Create/Update/Delete/Upload 彈窗 | DONE（既有交付） | app.py → /QA_portal/maintenance | tests/test_original_modal_workflow.py、test_unified_portal.py；本次完整回歸包含 | 公司 Oracle／瀏覽器驗收 |
| 改動影響預覽 | DONE（核心／HTTP） | /QA_portal/operations；operations.py | tests/test_operations.py（38項）、test_operations_ui.py | 自動解析全部舊報表；依已登錄關聯 |
| 報表版本差異與來源追蹤 | DONE（核心／HTTP） | /QA_portal/operations | tests/test_operations.py（38項）、test_operations_ui.py | 公司歷史快照匯入 |
| 相似需求／報表去重 | DONE（核心／HTTP） | /QA_portal/operations | tests/test_operations.py（38項）、test_operations_ui.py | 語意模型；先用可解釋規則 |
| 資料品質告警與寄送攔截 | DONE（核心／HTTP） | /QA_portal/operations | tests/test_operations.py（38項）、test_operations_ui.py | 真 SMTP；先測 mock 寄送 |
| 使用情況／自助成果統計 | DONE（核心／HTTP） | /QA_portal/operations | tests/test_operations.py（38項）、test_operations_ui.py | 不推估省下工時 |
| 泛型維護表／多 bind 註冊 | DONE（SQLite／契約） | maintenance_registry.py | test_maintenance_registry.py（25項）：100 定義、75 SQLite 表實測、Oracle fake-session 契約 | 真 Oracle 連線、100 表負載驗收 |
| 統一 app.py／共同登入整合 | DONE（程式／HTTP） | app.py、導覽、README | test_operations_ui.py、test_application.py、test_page_integration.py | 正式公司登入 |
| 自動範例 ETL / Job 監控 | DONE（真時間觸發） | app.py → 營運中心 | test_job_monitor.py（36項）；app.py 真60秒timer三步成功 | 不接公司 ETL、SMTP；預設停用 |
| 所選 Job 診斷 ZIP | DONE（mock診斷） | 營運中心監控同頁 | test_job_monitor.py＋test_operations_ui.py：白名單、敏感內容排除、ZIP下載、跨組拒絕 | 不含 raw traceback 或敏感資料 |
| Job owner 失敗通知 | DONE（mock通知） | 營運中心監控同頁 | test_job_monitor.py：逐run/跨程序去重、失敗合併、寄送失敗不重跑ETL | 不是真 SMTP，未宣稱送達 |
| 公司整合與使用者驗收 | NEEDS ACCEPTANCE | docs/opus | 待公司測試環境與 adapter | 不自動連線、部署、推送 GitHub |

## 開啟方式
使用既有 Python 環境執行 `python -B app.py`，登入後由導覽進入功能。營運中心已加入同一 app，無第二個網站啟動入口。

## 如何看 DONE
每項 DONE 都只適用於列出的檔案與測試範圍；最後交付會記錄命令、Python 版本、通過數及未測部分。未經測試不得只因為程式寫完就標 DONE。

## 本次完成證據

- 真 app.py 啟動、loopback healthz 正常、60 秒定时觸發三步 ETL 成功，4 筆合成資料；關閉程序後不留下 worker。
- 核心專項：operations 38、maintenance registry 25、job monitor 36。
- 完整回歸最終數量見 VALIDATION.md；Windows 與公司 Oracle/LDAP/SMTP/瀏覽器仍為 NEEDS ACCEPTANCE。
- 泛型目錄與 service CRUD 已做；原QSL彈窗保持原樣。未聲稱100張公司維護頁已自動完成替換。
