# 本輪獲准推送後的 GitHub CI 驗收

使用者已明確批准本輪 main 推送及 GitHub CI 驗證。此驗收規格在建立
本輪 commit 前完成；第 9 版成功的 run 只能證明第 9 版。推送後必須
核對本輪實際遠端 commit、CI run 與兩個 job 的終態，結果另附交付紀錄。

## 已提供的可執行配置

既有 [reporting-tests.yml](../../.github/workflows/reporting-tests.yml) 保持原樣：

- `push` 到 `main` 與 `pull_request` 觸發。
- `ubuntu-latest`、Python `3.8` / `3.10` matrix、`fail-fast: false`。
- checkout/setup-python 固定既有 action commit，token 僅 `contents: read`。
- 安裝既有 `requirements-qa-portal.txt`，未新增或升級依賴。
- 兩個工作都執行以下完整命令，會自動發現新增的 23 個案例：

```sh
python -B -m unittest discover -s tests -v
git diff --check
```

這份配置隨完整專案交付，可直接由 GitHub 在獲准推送後執行；不需公司
secret、Oracle、LDAP、SMTP、外部 API 或正式資料。新增 recovery/concurrency
案例使用獨立合成 state、受控子程序、SQLite 和現有依賴。

## 驗收準則

1. 保存新 commit SHA、run URL、兩個 job 的實際 Python patch 版本與結論。
   不把 matrix 的 `3.8` 說成固定 `3.8.13`，不把 Ubuntu 當成公司目標 kernel。
2. 目前候選的預期是每個 job `Ran 901 tests`，Linux 有 fork，無平台 skip。
   檢查 Node 案例確實執行；若有新 skip/failure/error，逐項記錄，不只看綠色狀態。
3. 保存完整 job logs；查看有無 unraisable exception、shutdown traceback、
   身分簽章/時間問題。不可為取得通過而靜默重跑、修改測試或降級判準。
4. 兩個 matrix 工作成功後，可補上本輪正常 Linux 環境的 Python 3.8/3.10
   回歸證據；保留這次 MSI WSL 的 exit 86，不能改寫為當時通過。
5. CI 不執行真實瀏覽器或效能量測；本輪 MSI Chrome 32/32 和 benchmark
   有自己的來源 hashes。CI 結果不能替代這些證據。

## 只讀時間監測

既有 CI 目前直接執行 unittest，沒有接入額外 clock gate。交付的診斷工具
可在需要監測的核准環境使用以下命令；它不修正時鐘、不改服務、不重試：

```sh
python -c "from pathlib import Path; Path('output').mkdir(exist_ok=True)"
python -B tests/probes/clock_guard.py --output output/clock-check.jsonl -- python -B -m unittest discover -s tests -v
```

偵測到時鐘倒退或監測錯誤時，guard 回傳 86，即使 unittest 自己成功。
本輪不再在已知不穩定的 WSL 重跑。這次 GitHub CI 已有使用者的明確
推送批准；此批准不包含部署、新範圍或之後的優化回合。
