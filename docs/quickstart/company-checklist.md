# 公司接入前：請 IT／系統 owner 確認

這份清單用來收齊正式整合的決策與契約。現有程式是合成資料整合基礎；
啟動 demo、preflight 或 CI 通過，都不會建立公司系統的相容性證明。
公司帳密、token、連線字串與資料只留在核准的私有管道，勿填進公開文件、Git 或 ZIP。

## 明天先做的三個決定

- [ ] 確認先試用本機合成 demo，還是開始正式整合評估；分開環境與驗收紀錄。
- [ ] 指定應用程式、身分、資料庫、排程／寄信及備份各自的負責人。
- [ ] 確認核准 Python、依賴清單、安裝來源、隔離目錄，以及允許的測試資料。

## 正式參數與契約：目前不能猜的內容

| 項目 | 請由授權來源提供／確認 | 尚未提供時 |
| --- | --- | --- |
| Flask／Dash／RESTX | 公司原始 `app.py`、Api／Namespace／model、前綴、callback IDs、回應與 docs 授權契約、RESTX 確切版本 | 保留本包單入口與已知接口，不直接覆蓋公司主站 |
| SQLALCHEMY_BINDS／Oracle | 正式 bind keys、模型／schema、型別、時區、查詢及交易邊界、trigger、錯誤與撤權行為、測試資料來源 | 不新增猜測的 bind／SQL／DSN，也不宣稱 SQLite 即相容 |
| LDAP／SSO／權限 | provider、使用者 ID、role／org claims、session、撤權、登入失敗行為、資料層權限 | 公開 demo 帳號僅留在本機；不把 demo admin 映射成正式權限 |
| 報表定義 | 核准來源欄位、單位、日期基準、SLA／工作日、匯出欄位、筆數上限與敏感欄遮罩 | 新帳齡報表只代表合成日曆日語意；固定日期與假資料須明示 |
| SMTP／寄信 | 核准伺服器與傳輸方式、寄件人、範本、收件人、outbox、投遞狀態、未知結果及重播規則 | 不送真實信件；mock 成功不等於投遞成功 |
| ETL／排程 | job IDs、callable adapter、時區、trigger、misfire／coalesce／retry、單一 scheduler owner | 不套用歷史時刻，不啟用真實 job 或外部副作用 |
| 狀態／鎖 | 單主機／多 worker 架構、實際檔案系統、鎖與 lease／fencing 消費端、備份保留政策 | 不把本機 SQLite 視為 NFS 或分散式鎖 |
| 對外服務 | TLS、proxy／URL prefix、cookie、CSRF、rate limit、secret 配置與監控 owner | 本機 `127.0.0.1:8050` demo 不對外公開 |

Production 設定需要由可信程式注入實際非 demo identity/report provider。
JSON／環境變數不能憑空建出 Oracle 或 LDAP adapter；不能從設定字串載入任意程式。
`REPORTING_MODE=production` 還需要合規 secret、絕對本機 state 路徑與 secure cookie，
且會拒絕兩個合成報表開關。不要為了讓範例可用而移除 production guard。
原離線 `deployment_check.py preflight` 不具備 provider 實例時會拒絕 production；
這是預期保護，不是已完成公司連線檢測。

## 切換前的最低驗收

- [ ] 用目標 Windows／Linux 與**精確 Python patch**跑核准 pins；CI 的其他 patch 不替代。
- [ ] 逐角色／組織驗證登入、撤權、查詢、匯出、CRUD、稽核與跨租戶拒絕。
- [ ] 實機瀏覽器驗證中文、鍵盤／focus、登入中斷、返回／前進、取消／關閉、連點、分頁及匯出。
- [ ] 驗證 Oracle 交易、LDAP／provider 中斷、DB lock、多程序、未知結果與恢復；保留失敗證據。
- [ ] 核對唯一 scheduler owner、關閉順序及外部 effects 的 idempotency／outbox。
      本機 receipt／SQLite fence 不能證明外部系統 exactly-once。
- [ ] 完整停機備份、以新目的地還原、marker 審核、schema／storage fingerprint 相容性與回退演練。
      公司 binds 的資料不在 demo 八-store 備份內，須另訂完整覆蓋。
- [ ] 確認告警、資料／audit 保留、事件處理人、切換窗口與明確部署批准。

詳細依據：[公司整合](../INTEGRATION_CHECKLIST.md)、
[主站整合](../opus/MAIN_APP_INTEGRATION.md)、
[Adapter 契約](../opus/ADAPTER_CONTRACTS.md)、
[RESTX 核對](../authorization/FLASK_RESTX_COMPATIBILITY.md)、
[備份與回退](../deployment/OPERATIONS.md)。
