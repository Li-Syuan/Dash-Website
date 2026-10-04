> Historical v11 record. See [current publication scope](../acceptance/PUBLICATION.md).
> Full raw evidence and local checkpoint inventories are preserved separately.
> Only the sanitized browser result JSON needed by regression tests is retained here.

# 第 11 版變更與遷移

基線：`e4a9681137f4762e4fcdcfee356351b7786a9420`（第 10 版 main）。
本輪為獨立 worktree 中尚未提交的候選；沒有 push、merge 或部署。

## 變更

1. QSL XLSX 匯出改用 1,024 筆批次的暫存快照，再逐筆送入既有 openpyxl
   write-only serializer。SQLite snapshot 完成後釋放鎖，保留當時一致的行資料。
   欄位、排序、literal text、篩選、權限與同步 bytes 回傳介面不變。
   預設 50,000 筆上限不變，100,000／200,000 僅為明確設定的合成量測。
2. 整合 QSL service 每次授權均重新查詢正式入口所使用的 identity provider，
   拒絕已不存在、改組織、改角色、偽造屬性或 provider 回傳不同 ID 的 actor。
   同組織讀者仍可共讀與匯出；既有管理員／本人寫入規則維持。
   匯出後若同一 callback 的最終重讀偵測撤權，丟棄已產生的下載。
3. 新增離線設定檢查、liveness/readiness、SQLite 集合備份及新路徑還原工具。
   readiness 同時檢查實際持有連線、檔案/schema、資源與 worker 狀態。
   正式還原前必須停機並核對整組資料；還原和未完成集合保留阻擋啟動標記。
4. 新增可複製、可執行的 quality inspection 報表範本：明確 PageSpec、query
   contract、repository、service、頁面、CSV 匯出與 HTTP／瀏覽器驗證。
   預設關閉，只能於 demo 模式透過受控 flag 啟用；沒有任意 SQL 或模組載入。
5. 保留整體專案脈絡、AGENTS、維護交接、既有報表／ETL／CRUD 彈窗；所有
   操作從既有 `app.py` 進入。沒有新增套件或改動既有 dependency/CI pins。

## 設定與資料遷移

沒有新增資料表、改欄位或自動 schema migration。升級前依
[部署操作](../deployment/OPERATIONS.md) 使用離線完整集合備份並驗證 checksums。
本輪工具不構成正式部署；不要直接以範例命令操作公司的資料路徑。

新選用設定 `REPORTING_ENABLE_REPORT_TEMPLATE` 嚴格接受 `1/0/true/false`
（布林文字大小寫皆可）；未設定等於 false，空白或任意字串遭拒絕。
`REPORTING_ENABLE_REPORT_TEMPLATE=1 python -B app.py` 的 POSIX 寫法，或
PowerShell 設定 `$env:REPORTING_ENABLE_REPORT_TEMPLATE = '1'` 後執行
`python -B app.py`。頁面路徑為 `/QA_portal/report-template`。
正式模式啟用此合成範本會失敗，實際公司 adapter 需另行整合與驗證。

大檔匯出需要本機暫存空間；快照、worksheet XML、ZIP 檔可能同時存在，
最後 bytes／Dash base64 仍使用記憶體。確切量測與取捨見
[XLSX 指引](../large-export/XLSX_EXPORT.md)。沒有新增背景隊列或共享快取。

備份預設只支援已知 bundled store 組合，需明確宣告已停止所有寫入者。
SQLite WAL 模式會遭拒絕。還原只允許新的目錄，不能覆寫既有檔案。
啟動前需依文件核對 `RESTORE_REVIEW_REQUIRED.json`；`INCOMPLETE.json` 也會
阻擋啟動。不要將移除標記當成已完成審核，或自動啟動還原的 schedules。

rollback 使用已驗證、相容版本的完整 before-image 和對應程式／設定；
不宣稱任意 schema downgrade、跨版本重播或外部副作用安全。
新工具保留 uncertain claims／fences，不能自動清除後重播。

## 授權入口與驗證

預設入口 matrix 及 opt-in 範本 matrix 分別保存。除 page/callback/API/export
否定測試外，範本服務拒絕跨 tenant rows、改 ID／URL／query、過期權限與
client table 偽造；CSV 重新查詢伺服器資料，不信任 DOM。

完整測試分類與來源雜湊見 [VALIDATION.md](VALIDATION.md)。實際 Chrome 測試
獨立列示，HTTP 回歸不能代表瀏覽器。公司 Oracle/LDAP/SMTP、正式資料、
外部通知、正式部署及未提供的 RESTX 實作均未執行。
