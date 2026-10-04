# 專案脈絡與使用者已提供資訊

本文件只整理 Dash QA Portal 相關需求、環境、既有功能、限制與決策。
不包含私人生活、其他專案、帳密、token、公司連線字串或私人記憶原文。
資訊來源是本次使用者指示及既有 README、公司整合與 adapter 文件；
公司原始碼未提供的部分明確列為待核對，不能由合成範例推定。

## 已確認的目標與既有系統

| 項目 | 使用者提供的要求 | 本 repository 的實際界線 |
| --- | --- | --- |
| 用途 | 企業內部 QA Portal | 可執行的 Flask + Dash 整合基礎與離線合成 adapter |
| 入口與路徑 | `app.py` 單入口；保留 `/QA_portal/` 前綴 | QSL、operations、ETL 使用此路徑；其他既有頁面如 `/maintenance`、`/admin` 不擅自改址 |
| 網頁技術 | Flask、Dash、Flask-RESTX | Flask/Dash 在原始碼中；公司 RESTX Api/Namespace/model 未提供 |
| 資料層 | 既有 `SQLALCHEMY_BINDS` 與 Oracle 整合需保留 | 公司 bind key、engine、model、交易/trigger 契約待私有來源核對；不以 SQLite 取代後宣稱相容 |
| 身分 | 既有權限與 LDAP 等整合 | provider 接口及 synthetic identity；實際公司登入/角色/撤權行為未連線驗證 |
| 既有能力 | 權限、文件鎖、排程、CRUD/SQLite、報表頁、寄信 | 有合成/本機功能與測試；公司鎖、正式排程/寄信實作未獲驗收 |
| 報表管理 | 數十張報表由管理員維護，包含權限和寄信排程 | 保留 governed report metadata、目錄、權限與 mock schedule；不是任意 SQL 或自助 BI |
| 導覽 | 側欄有外部系統入口 | 保留需求；實際連結、SSO、可見性及正式 URL 待核對，不猜測或自動呼叫 |
| CRUD | 維持原有 Create/Update/Delete/Upload 彈窗 | 保留查詢 ID、取消/關閉、連點防護、版本衝突、歷史與報表精靈 |
| 資料語意 | 同組織共讀；本人或同組織管理員可寫 | 不擴大成跨組織 admin 權限；legacy QSL 保持自己的較窄 policy |

## 環境、版本與驗證狀態

- 目標 Linux kernel：`3.10.0-1160`；目標 Python：`3.8.13`。
  此精確 kernel/完整企業依賴組合未驗證，不猜測 Linux 發行版。
- 歷史 Windows Python：`3.10.4`，此精確 patch 未驗證。
- MSI 第 9 版實測：Windows Python `3.10.22`、WSL Python `3.8.20`；
  原生 Chrome `154.0.8037.95`。WSL 不是目標企業 Linux。
- 第 9 版 GitHub CI 實際使用 Python `3.8.18`、`3.10.21`，兩個工作成功。
  CI 的 `3.8`／`3.10` matrix 不能被解讀成固定 patch 或目標 OS 認證。
- 已有最小依賴 pins：Dash `2.9.1`、Flask `2.2.3`、Werkzeug `2.2.3`、
  Flask-Login `0.6.2`、dash-bootstrap-components `1.4.1`、Plotly `5.13.1`、
  setuptools `57.5.0`、dash-mantine-components `0.12.0`；QA XLSX adapter
  使用 openpyxl `3.1.5`。以現有 requirements 檔案為準，不擅自升級。
- SQLAlchemy 與 APScheduler 的既有文件列出不同已提供版本組合，尚未
  確認完整企業矩陣。Flask-RESTX 確切版本也待提供，沒有自行加入 pin。
- 歷史 `requirements.txt` 含另一組版本；不得將它當成本輪升級批准。

## 歷史設定與方向

| 資訊/決策 | 狀態與使用方式 |
| --- | --- |
| ETL 時刻 `05:30 / 09:00 / 13:00 / 17:00` | 歷史資訊；須與公司實際排程、時區、misfire/coalescing 設定核對。不得覆盖當前設定 |
| 寄信時刻 `10:00` | 歷史資訊；須核對實際配置、收件人與投遞契約。不得因此啟用 SMTP |
| Superset | 已取消，不引入 |
| 自助報表/自助 BI | 已取消；既有受控報表精靈與管理員維護功能繼續保留 |
| ETL、架構分層、權限/租戶隔離、真實瀏覽器驗證 | 已批准並納入第 9 版 checkpoint |
| 金融策略、外部 AI API、新 AI-agent 平台 | 本專案範圍外 |
| 持續優化及交給系統 agent | 指本 repository 的程式、測試、量測、說明與可執行命令；不是另外搭建 agent 服務 |

## 版本與本輪批准邊界

1. 第 7 版 Library 基線：`QA_Portal_Opus_Integration_20261002.zip`，
   542305 bytes，SHA256
   `5a44966e072f4ae176bd68967c2323191eb29994e045bb3cb6ac337517fd4318`。
   先在 MSI 核對 bytes，因原 checkout 有差異而使用獨立副本。
2. 第 9 版已獲使用者明確批准直接推 main；commit
   `a35898344c752e6c41fab0afdfd81d7d6ebdb617`，
   [CI 37194191362](https://github.com/Li-Syuan/Dash-Website/actions/runs/37194191362)
   已完成且成功。沒有 force push 或部署。
3. 第 9 版本機完整 ZIP 與四份證據各低於 10 MB；Library 批次回存在開始
   寫入前因功能不可用受阻。不可把 Git 推送說成 Library 已更新第 9 版。
4. 本輪另行批准故障復原、多人併發、量測效能與實測瓶頸改善，以及完整
   專案交接。先完成本機成果與待推清單後，使用者已明確批准本輪正常推
   main，並以 GitHub CI 完成 Python 3.8/3.10 驗證。不批准 force push、部署
   或擴大範圍；保留 WSL 環境無效紀錄，不再調整時間服務或反覆重跑。
5. 保護使用者原 checkout；使用獨立 worktree。所有帳號、資料、郵件都
   為隔離合成 fixture；不接公司 Oracle/LDAP/SMTP，不送通知、不改正式
   資料、不啟用外部副作用、不部署。

## 尚待公司資訊

實際 RESTX 版本/Api/Namespace/model 與前綴、Swagger/docs 授權、正式
response contract；SQLALCHEMY_BINDS keys 與 model/交易；LDAP/SSO claims；
正式排程時區及 owner；鎖定/多 worker 架構；核准報表來源、寄信模板及
收件人；側欄外部系統；備份留存和實際系統 agent 執行方式。
這些缺漏不阻止離線本輪工作，但不能以猜測填入正式設定。

相關契約：[公司整合檢查](INTEGRATION_CHECKLIST.md)、
[主站整合](opus/MAIN_APP_INTEGRATION.md)、[Adapter 契約](opus/ADAPTER_CONTRACTS.md)、
[RESTX 核對結果](authorization/FLASK_RESTX_COMPATIBILITY.md)。
