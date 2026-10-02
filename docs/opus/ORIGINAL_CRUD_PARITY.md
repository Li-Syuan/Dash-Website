# 原版維護操作流程對照

2026-10-02 更新。比較基準為使用者提供的 CRUD_ModalBlock / CRUD_callback_initial；本版恢復 QSL 維護的操作流程，不宣稱已移植公司全部泛用 CRUD 元件與資料庫。

啟動仍只用 `python -B app.py`，登入後進入 `/QA_portal/maintenance`。所有測試使用合成 SQLite 資料，沒有公司資料或外部服務。

| 原版使用方式 | 本版主站 | 驗證 |
|---|---|---|
| Create → 彈窗表單 → Submit to add | 獨立新增彈窗，與修改欄位分離 | HTTP 成功寫入、重複拒絕、成功刷新並關窗 |
| Update → Table ID → Query → 載入欄位 → Submit to update | 獨立修改彈窗，Query 後顯示欄位、版本自動取得 | HTTP 直接提交成功；不必先按差異預覽 |
| Delete → Table ID → Query → 查看該筆資料 → Submit to delete | 獨立刪除彈窗，最後 Submit 是明確刪除動作 | HTTP 查詢展示、取消不寫入、ID 更換拒絕、成功刪除 |
| Read / Download data | 刷新與 XLSX 匯出，另保留 CSV | 現有 HTTP 匯出測試；匯出明確使用目前查詢條件 |
| Download template / Upload CSV | CSV 模板；獨立 CSV/XLSX 上傳、預覽、Submit upload 彈窗 | 現有及新增 HTTP/SQLite 原子匯入測試 |
| Close / Cancel | 每個彈窗均可取消，重開從乾淨狀態開始 | 清除欄位、ID、版本、查詢憑證，撤銷修改預覽與上傳 token |
| 新版歷史、差異、精靈 | 差異預覽在 Update 彈窗內選用；歷史／還原、報表精靈仍在同頁 | 既有服務與主站 HTTP 回歸保留 |

## 有意修正，不保留原版錯誤

- 原版 Submit 無論成功／失敗都會關窗。本版只有成功提交才關；錯誤訊息顯示在原彈窗，欄位保留。
- 以目前登入者在 callback 與服務層重新授權，不信任按鈕可見性或共享 crud_mode。
- Query 產生伺服器簽章的使用者／動作／ID／版本憑證，30 分鐘到期；更改 ID 必須重查，舊版本不能覆蓋新版本。前端不可手改版本。
- 修改差異預覽不是強制新增步驟。若選擇預覽確認，確認僅提交該份伺服器保存的預覽，與畫面後續編輯分開。
- 匯出使用查詢條件，避免原版畫面篩選但下載全表的落差。
- 刪除保留歷史可還原；不是永久刪除。
- backdrop/ESC 不會直接關窗，以確保 Close / Cancel 完整清理暫存；這是有意的交互差異。
- 替換上傳檔案立即撤銷上一份暫存；新檔失敗不會誤送舊檔。整批失敗顯示回滾，不關窗、不宣稱成功。

## 驗證範圍與交接限制

`tests/test_original_modal_workflow.py` 使用實際主站 Flask 登入、Dash HTTP callback dispatch 與 SQLite。包括真實 SQLite trigger 拒絕寫入時的回滾、read-only、跨身分、偽造查詢 token、錯 ID、舊版本、關閉／重開、無效替換上傳及整批回滾。

測試不是實際瀏覽器點擊或視覺驗收。尚未驗證 Windows、桌面／手機渲染、focus trap、檔案選擇器、鍵盤操作和公司 Oracle／LDAP／SMTP。CSS 依 pinned DBC 1.4.1 Modal 外層 class contract 配置本地樣式，不依賴 CDN；仍需瀏覽器驗收。

本頁為 QSL 專用欄位，並未宣稱重現公司原版三組任意 ORM 維護表、所有 model_config 控件／日期／LOB／事件行為；公司整合時仍須接正式 adapter 並逐項驗證。保留 Python 3.8、Dash 2.9.1、DMC 0.12.0、DBC 1.4.1。
