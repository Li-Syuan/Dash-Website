# QA Portal 完整離線整合包

先讀 `docs/opus/INTEGRATION.md`，再依 `docs/opus/VALIDATION.md` 查看實際驗證范围。
本包保留既有 demo 功能，加上公司 legacy 權限語意、安全 QSL CRUD/匯入匯出及 mock 郵件狀態流程。
提供給 Opus 整合；**尚未接入公司正式系統、尚未部署，沒有真實寄信或正式授權異動**。

## 新增功能

新版加入 QSL 修改差異確認、可追溯歷史還原、四步報表建立精靈。操作與保存／升級方式請讀 `docs/opus/REVISION_WIZARD_UPGRADE.md`。這些新功能在下方 **8051 的 QA Portal 參考入口**；8050 主目錄沒有被替換。

## 執行入口

- `python -B app.py` → `http://127.0.0.1:8050/login`：完整報表目錄／管理台、權限、維護、排程設定、稽核。既有公開 demo 帳號見 README。
- `python -B qa_portal_demo.py` → `http://127.0.0.1:8051/QA_portal/`：公司規則相容參考頁。僅 synthetic 身分與資料，預設將合成資料與報表定義保存在本機 `instance/qa_portal_demo/`，重啟後保留。

先在隔離 Python3.8 虛擬環境安裝 `requirements-qa-portal.txt`。不要使用歷史 `requirements.txt` 升級公司環境。
不要直接執行歷史 `test_admin.py`（它是舊資料庫範例，不是測試入口）。

## 自動測試

`python -B -m unittest discover -s tests -v`

完整目錄與新相容參考頁是兩個 runnable 入口。這是刻意保留 baseline、提供逐步整合邊界，並非原公司所有報表的已遷移版本。

## 隱私與發佈

本 ZIP 不含 .git、實際 SQLite、credentials、真實公司資料或原聊天內容。
只有合成測試 fixture。未對 Git remote push，未 publish/deploy。

`examples/qa_portal/qsl_new_rows.csv` 和 `.xlsx` 可直接用於參考頁匯入，兩筆為同供應商不同城市的合成資料，驗證五欄唯一鍵；同一批再次上傳應被拒絕為重複資料。
