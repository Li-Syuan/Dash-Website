# 上手包 GitHub 發布（2026-10-05）

原本的本地交付已完成後，使用者明確要求將新增內容推送到
`Li-Syuan/Dash-Website` 的 `main`。此次只發布必要的 source、tests、
中文操作文件與純合成範例，不上傳 ZIP、資料庫、runtime、credentials 或原始 logs。

## 候選與驗證順序

- GitHub 起點：`7bb9b264482179a1b31ca32c9ed6737c9b8f51a0`。
- 原 ZIP 的本地來源：`931eaa404762656906143c55072cd08140ceb3a5`。
  本發布候選只在該成果上補正發布狀態文字；工具、產品程式及既有 pins 不變。
- 先建立 `validation/dash-handoff-quickstart-20261005-0946` 分支，
  等候該確切 commit 的 Python 3.8／3.10 regression 和原 50 項 native browser gate。
- 三個 jobs 全部成功後，重讀 `main`，只允許正常 fast-forward；不 force push。
- `main` 移動後再次核對 commit SHA，等待 main 自己的 CI 成功才宣告發布完成。

本檔不提前聲稱 CI 已通過。請查看
[專案 Actions](https://github.com/Li-Syuan/Dash-Website/actions)，
確認所用 commit SHA、branch、全部 jobs 與 conclusion；其他 SHA 的成功不算本次結果。
[原本地驗證](VALIDATION.md) 每版 1233 tests（1227 passed、6 Windows-only skips），
與新遠端 CI 各自保留來源／runtime，不能互相替代。

## 不變的邊界

沒有部署、MSI／Windows 實機操作或公司 Oracle／LDAP／SMTP 接線；
中文字型仍須在目標 OS／瀏覽器目視驗收。沒有新依賴、產品 pin 升級、
任意 schema downgrade、外部 exactly-once 宣稱或 15% 時間／10% RSS 門檻啟用。
本地受限的 browser／socket 不重試；瀏覽器發布 gate 由原有 GitHub workflow 執行。
