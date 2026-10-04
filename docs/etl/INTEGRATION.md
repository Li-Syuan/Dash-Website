# ETL 調度中心：既有 ETL 整合指南

此增量以既有 `job_monitor.py` 的明確 worker 生命週期、當前身分檢查與 durable fencing 原則擴展為多 pipeline。原單一範例監控、診斷 ZIP、mock 通知與 QSL 彈窗仍保留。

新入口為 `app.py` → `/QA_portal/etl`，使用原 Flask-Login 與 app-scoped PageSpec／callback 權限。範例僅使用合成 SQLite；沒有公司 Oracle、LDAP、SMTP、連線字串或憑證。

## 整合前先分類現有步驟

1. 盤點每個 ETL 的來源、依賴、日期分區、欄位與品質規則。
2. 把唯讀／純轉換步驟與外部寫入、寄信、檔案覆寫分開。
3. 確認一個 business date 在來源端的時區與截止時間；不要假設固定間隔等於每日午夜排程。
4. 為每一個 callable 指定穩定名稱與版本；程式或語意改變必須升版。既有成功步驟的輸出不可用新邏輯重新解釋。
5. 由伺服器註冊核准 adapter。使用者只能選已註冊的 job，不可提供 Python、SQL、module path、連線資料或 URL。

## 安全邊界

這版只保證引擎管理的本機 SQLite 發布與 claim／fence 交易。外部系統的寫入必須另設 idempotency key、交易 outbox、下游 fencing 與模糊結果對帳。不能把寄信或既有 Oracle commit 直接放進可重試 callable，然後宣稱 exactly-once。

排程由 `app.py` 明確啟動；import／factory 不啟動 worker。正式 WSGI 整合時需指定單一受監督 worker owner，不可在每一個 Gunicorn worker import 時自動啟動。

本機 SQLite 必須位於同主機可信任的本機磁碟；不支援 NFS／網路同步資料夾／多主機叢集。本版沒有 Celery、Redis 或分散式工作佇列。

## 公司環境驗收關卡

- 實際公司身分與 tenant/action 權限映射；撤權後 callback、排程與發布皆拒絕
- 已核准來源 adapter、型別／NULL／編碼／日期截止點及資料量上限
- 失敗、上游依賴、品質 gate、安全重試與外部不確定結果對帳
- 排程 owner、重啟、停止、併發、lease 期限、處理時間上限與 backlog 政策
- 本機磁碟 ACL、容量／保留期限、整體停機備份與還原演練
- 公司指定 Python patch、Windows、proxy prefix、完整依賴與瀏覽器操作驗收

完成上述前，此包為離線可執行整合基礎，不是公司 production migration 或正式上線證明。

## 儲存位置與 worker

主站有 `REPORTING_STATE_PATH` 時，dispatcher 使用該完整檔名加 `.etl.sqlite`，例如 `instance/workspace.sqlite.etl.sqlite`。Factory 未提供持久化路徑時，只建立測試專用臨時 store。`server.extensions['etl_dispatch']` 為注入後的 app-owned service；不是 module 全域租戶狀態。

## 受信任註冊 API

程式碼位於 `reporting_workspace/etl_adapters.py`，範例位於 `examples/etl_registry.py`。`SnapshotAdapter` 接受名稱、明確版本、callable 與 `retry_safe`。`StepSpec` 指定依賴、筆數上下限、必要欄位、唯一鍵、非負數欄位及 `count_matches`；後者必須指向直接上游。`JobSpec` 指定組織、可讀／可管理角色及可選的明確使用者名單。

- Pipeline 1–20 步、registry 最多 50 個 job；重複名稱、未知依賴與循環在註冊時拒絕。
- 發布節點必須涵蓋所有分支，防止未通過檢查的分支被忽略。
- Adapter 只收到 `job_id`、`run_id`、`business_date`，以及每個直接上游輸出的獨立副本。
- 回傳 `list[dict]`，欄位值限可驗證的 JSON scalar；引擎自己計算 row count。每步最多 1000 列、256 KiB，不信任 adapter 自報筆數。
- 若來源查詢需截斷，用「上限加一」偵測溢出並讓 gate 失敗；不可只 LIMIT N 後誤稱資料完整。
- Job 的明確角色規則是範例設定；公司「管理台 admin 不等於 CRUD」規則需獨立對照，不可默默套用。

範例 callable 的形式：

```python
def normalize(context, upstream):
    return [dict(row) for row in upstream['source']]
```

範例 module 不會在 import 時建立檔案、連線或啟動執行緒。它的 `create_fixture(...)` 是需明確呼叫的測試 setup，只接受不存在的檔案，避免覆蓋原資料。`build_registry(...)` 固定使用唯讀 SQLite、固定 SQL、綁定日期參數；不是任意 SQL 執行入口。

## 先用合成 fixture 驗證接線

在已安裝專案既有依賴的隔離環境，可由測試或受審核的整合程式執行：

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from examples.etl_registry import create_fixture, build_registry
from reporting_workspace.etl_dispatch import ETLDispatch
from reporting_workspace.providers import DemoIdentityProvider

with TemporaryDirectory() as directory:
    root = Path(directory)
    source = create_fixture(root / 'source.sqlite', '2026-10-01')
    identities = DemoIdentityProvider()
    engine = ETLDispatch(root / 'dispatch.sqlite', identities,
                         registry=build_registry(source))
    user = identities.get_user('demo-admin')
    result = engine.run_now(user, 'approved-orders-fixture',
                            business_date='2026-10-01',
                            request_id='integration-example-1')
    assert result['status'] == 'succeeded'
    assert [step['row_count'] for step in result['steps']] == [2, 2, 1]
    engine.stop()
```

這段不是新的網站入口，只是受審核整合程式的 service 使用範例。正式網站仍只從 `app.py` 啟動。測試 `test_etl_integration_example.py` 會核對來源檔案未被修改，空日期分區或超過上限不能發布，source rows／檔案路徑不會流入公開結果。

## 導入現有公司 ETL 的建議順序

1. 保留公司現有 ETL 的原呼叫路徑，先選一個只讀來源、一個純轉換與一個本機結果提案，建立明確註冊。
2. 由公司 adapter 處理既有 `init_db`／bind／型別；不要把 browser 設定當資料庫或 module 選擇權。資料讀取也需最小權限與業務日期界線。
3. 用核准 fixture 與新舊結果比對，再加上缺欄、重複鍵、筆數落差、NULL、負值、錯誤及撤權案例。
4. 先驗證手動執行，再測一天補跑；最後才啟用短期測試排程。排程開啟由公司授權的操作者明確執行。
5. 外部發布／Oracle commit／檔案覆寫／SMTP 另行設計 outbox 或下游 idempotent sink；本版 `SnapshotAdapter` 不承諾承接這些副作用。
6. 不要改掉原 pipeline「繼續執行再彙整錯誤」等公司業務政策，除非使用者另有明確授權。此中心的新 synthetic pipeline 採失敗依賴阻擋與安全發布政策。

## 版本與資料復原

Registry 的 adapter 版本、DAG、品質規則與權限構成持久化相容性簽章。改變這些設定時不會默默覆寫既有排程／重試來源；引擎會拒絕不相容的 store。請停機、備份所有相關檔案，在獨立位置設計並驗收遷移，不要刪除舊 store 來假裝升級成功。

舊 monitor 與新的 dispatcher 為不同檔案，各自保存原有紀錄。升級不需要搬走原 QSL 資料。備份與還原應涵蓋主 workspace、`.qa`、`.operations` 及新 dispatcher 的 `<REPORTING_STATE_PATH>.etl.sqlite` 檔案，並在所有 writers 停止後執行。還原舊 claim／fence 可能復活曾執行過的批次；必須先對帳再重啟。

Callable 需合作式及有界完成。租約到期會阻擋晚到的本機發布，但不會強制終止 Python 函式，也不能撤銷它自行做出的外部副作用。若公司步驟可能阻塞／長於租約，需先設計超時、進程隔離與受監督 worker；不要只把 TTL 任意加大後當作 production 保證。

## 調度、重試與補跑語意

- UI 排程為固定間隔（60–86400 秒，預設 300 秒），日期與呈現時區為 Asia/Taipei。不是 cron、每個午夜執行或假日行事曆；名稱中的 daily 指業務資料日期。
- 錯過多個 interval tick 會合併；下一次啟動不逐一重播所有錯過時間。日期補跑是另外明確操作。
- 同一 job 同時只允許一個持有有效 lease 的執行。現有 worker 逐 job 處理，沒有平行 DAG worker pool；步驟按拓撲順序執行。遇第一個失敗會結束該批次並把尚未執行步驟標記 skipped。
- 只在全部 gate 成功後，於 fenced 交易中發布指定日期的本機結果；失敗保留原成功結果。最近發布可能來自歷史補跑日期，不等於最新業務日期。
- UI 的「建立新請求」明確開啟新手動執行意圖；未變更表單的重複點擊沿用原 request ID。後端 request receipt 綁定 actor、job、action 與內容；換內容重用同 key 會衝突。
- 重試會新增一個 run，以 parent/root ID 連結，最多 3 次嘗試（含原批次）。成功步驟從已驗證的不可變 snapshot 沿用，失敗與 skipped 步驟才再執行。若需要刷新成功的來源步驟，應是明確的新手動 run，不是假裝原失敗的安全續跑。
- Retry 只允許白名單的 adapter／品質／snapshot 錯誤，且待執行步驟全部宣告 retry_safe。已中斷、lease 失效、撤權、設定更改或 provenance 失效，不會自動重播。
- 重試要求相同 registry 與排程設定版本；每一個失敗 parent 只能有一個後續 child。已建立的 child 會重用，不能對同一 parent 分叉重試以繞過限制。
- 補跑範圍含首尾，最多 31 日，拒絕未來／反向／非標準日期。每 job／日期的補跑 claim 持久化去重，重疊區間重用已有補跑；手動與 timer 執行有各自的明確 key。失敗補跑另用 retry，不會因再次送出區間就自動重播。
- UI 紀錄顯示最近 100 個 run；這是顯示上限，不是資料庫保留上限。既有 receipts、snapshots 與來源鏈必須保留才能保持去重與安全重試；正式長期運行前需設計容量監測及保留／封存政策，不可直接清空 claim。

補跑不是整個日期範圍的單一交易：先前已成功或失敗的日期會逐筆保存。若途中撤權、儲存失敗或另一個執行占用 job，整個呼叫可能中止，但已完成日期不會回滾。先檢查各日紀錄，再以同一 request／區間恢復；既有日期不會因恢復而重複執行。背景 worker 狀態是目前 app instance 的執行緒狀態，不是分散式健康檢查。
