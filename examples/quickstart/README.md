# 範例報表（純合成）

`synthetic-corrective-actions.csv` 是組織 A 的公開合成資料快照，
As of date 為 **2026-10-05**，未加 Search／Status／Priority 篩選。
36 列：Open 25、Closed 11、Open overdue 25。這不是公司案件或 SLA。
檔案為 UTF-8 CSV；在 Excel 請用「資料 → 從文字/CSV」選 UTF-8 匯入。

此檔由 `tools/quickstart_demo.py --as-of 2026-10-05` 透過現有 report service
產生。UI 匯出和這份範例一樣採當下控制項、全部篩選結果，不限目前分頁。
更換日期可能改變結案狀態；檔名不會自動標示日期，使用時另記日期與篩選。

完整流程見 [上手說明](../../docs/quickstart/README.md)。
