# 第 9 版：完整驗收與兩個故障的根因

本版接續第 8 版候選結果，所有產品變更均在第 7 版的獨立工作副本完成。
原 checkout 與其未提交變更未動。只使用隔離合成帳號／資料，未呼叫 Oracle、
LDAP、SMTP、真實通知、正式資料或正式排程，未 push、merge、部署。

## 最終同一程式的結果

| 檢查 | 結果 | 證據（主包內文字記錄） |
| --- | --- | --- |
| Windows Python 3.10.22 完整 unittest | **877 通過、1 跳過、0 失敗／錯誤**；878 cases，55.708 秒 | `evidence/v9/windows-python310-v9-final.json` 及 `.txt` |
| MSI WSL Python 3.8.20 完整 unittest | **878 通過、0 跳過、0 失敗／錯誤**；67.399 秒 | `evidence/v9/wsl-python38-v9-final.json` 及 `.txt` |
| 原生 Chrome 154.0.8037.95 完整驗收 | **32 通過、0 失敗、0 未跑**；單次 suite，不重試 | `evidence/v9/browser/results.json` |
| HTTP 授權入口矩陣 | **263 / 263 通過**；12 PageSpecs、49 callbacks、17 HTTP routes | `entrypoint-coverage.json` |
| 原生 Plotly 靜態下載壓力對照 | **40 / 40** 長度及 SHA256 等於原檔；每次 3,578,466 bytes | `evidence/v9/browser-diagnostics/native-preflight-*/results.json` |
| 靜態 WSGI 相容層 | 9 個精確 bytes／header／狀態／close／exception tests 通過，已包含於完整 suite | `tests/test_static_transport.py` |

Windows 唯一 skip 是平台沒有 fork；Python 3.8 已執行該案例及全部 Node 契約。
兩個完整 Python run 都沒有 unraisable exception 或程序關閉 traceback。
263 個入口檢查屬 5 個 unittest cases 的子檢查，不重複加總。
瀏覽器 runtime/assets 的 55 個 hash 與最終來源一致；Python 3.8 copy 的 source
亦逐檔對 hash，交付時再次核對。Python 3.8 未替換 session/signature 函式。

瀏覽器本輪 `QA_HTTP10=0`、`QA_STATIC_BRIDGE=0`：直接載入本機 HTTP 靜態資產，
所有 dynamic callbacks 是原生瀏覽器 POST。Plotly 確認實際 **12 個 SVG bars**，
再操作刷新及真實下載 CSV；CRUD、取消／關閉、連點、版本衝突、上一頁／下一頁、
跨租戶改 ID、peer 禁寫、撤權、登出重播皆通過。撤權／重播仍保留真實 401。
本輪 pageErrors 0、非預期 console error 0、靜態傳輸失敗 0，`static-delivery.json`
為空。45 個 `ERR_ABORTED` 是導航取消，逐筆保留，沒有宣稱所有網路事件均成功。

## 故障一：大單塊傳送與立即關閉的時序

不載入任何產品程式的最小 Werkzeug 2.2.3 WSGI 伺服器，亦能重現大 JS 尾端
遺失：4 MB synthetic body 與原 Plotly，Python urllib、Node HTTP、Chrome 都
可能只收到部分 bytes。server iterable 已完成，connection_dropped 沒有事件。
HTTP/1.0 仍失敗，不能以改 protocol 解決。

同一 bytes 改為 16 KiB WSGI chunks 即完整；另一診斷對照只在 finish 前延後
100 ms，也達到 15/15 完整。這將問題定位在目前 Windows/Werkzeug stack 的
send／close 時序互動，**不是封包層或 Windows kernel 內部的最終定案**。
原始比較與所有失敗保留於 `evidence/v9/browser-diagnostics/`。

產品新增 `StaticTransport`，僅拆分本機 static GET/HEAD 的大 WSGI chunks，
包括相容的 WSGI write callable。它保持 bytes 順序、status、headers、
Content-Length、原始 iterable close 與例外；不 sleep、不 retry、不轉送、
不改依賴、路由、授權或 dynamic callback。其後原生完整 32/32 一次通過。

兩次預檢各自的 overly-strict network aggregate 因登入導航取消而 FAIL，原結果
仍保留；其中 40 次 static byte/hash 檢查皆完整。最終驗收使用既有完整情境，
保留導航取消記錄並新增圖表 render 斷言，沒有刪除或跳過先前失敗情境。

## 故障二：WSL 校時倒退造成 session 失效

兩次獨立 WSL boot 的原始 clock probe 共捕捉 5 次 realtime 回退，每約 32 秒
一次，幅度約 0.63–0.75 秒，與 NTP packet 更新一致。捕捉到 builtin time 函式、
真實 `SignatureExpired`（signed epoch 比 current epoch 大 1）及實際 Flask 401，
串起 **clock 回退 → cookie timestamp 在未來 → 原簽章驗證拒絕 → 空 session → 401**。
短暫暖機不足。尚未將底層歸因於 Hyper-V 或特定硬體；也未修改其設定。

最終 Linux 回歸使用受控且可恢復的測試窗口：確認沒有其他 app workload 後，
只暫停此 WSL guest 的 `systemd-timesyncd`，不改主機時鐘或持久設定；設置
180 秒自動恢復保護，並在 finally 恢復原 active/running/enabled 狀態。
ClockGuard 全程以系統 CLOCK_REALTIME／CLOCK_MONOTONIC 取樣，任何回退或監測
錯誤都使整輪無效（exit 86），不重試或吞掉失敗。最終 **backsteps 0、monitor
errors 0、878/878 通過**；服務前後狀態完全一致。

原始證據、clock guard、一次執行的 runner 及恢復記錄在 `evidence/v9/`。
沒有延長 cookie、接受未來 timestamp、mock session clock、改角色／租戶、
重試 401 或弱化斷言。現有 WSL 的永久校時設定未被更改；若用原本失穩的
clock 環境重跑，仍可能出現相同環境症狀。長期主機校時調整是另外的運維動作。

## 重現與交付

```text
python -B -m unittest discover -s tests -v
python -B tests/test_entrypoint_coverage.py --write-coverage docs/authorization/entrypoint-coverage.json
node tests/browser/acceptance.cjs
```

瀏覽器設定見 [tests/browser/README.md](../../tests/browser/README.md)；原生最終
測試不啟用 HTTP10／static bridge。Clock guard 與受控 runner 是本次診斷證據，
不是產品啟動流程，也不會被 app.py 或 unittest discover 自動執行。

主 ZIP 僅含完整程式及文字證據。大型 screenshots／binary exports 分到獨立、
每份低於 10 MB 的 `Dash_QA_Evidence_20261004_Part*.zip`，未刪除第 8 版已交付
的任何測試證據。將證據 ZIP 解壓到主 ZIP 相同父目錄，即可補全相同專案內的
evidence 路徑。每份檔案及 hash 見 `EVIDENCE_INDEX.json`；Library ID／版本／
整包 SHA256 另由交付清單提供。第 8 版失敗歷史見 [VALIDATION.md](VALIDATION.md)。

公司精確 patch runtimes 3.8.13／3.10.4、真實外部系統、正式部署、所有瀏覽器／OS
及真實併發壓力測試仍未驗證；本次不將 synthetic acceptance 當成公司整合認證。
