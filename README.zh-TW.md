# hololive-Dreams 日常

以 MaaFramework、MFAAvalonia 與獨立執行的 Holodori-Auto 模組建立的 Windows 日常程式。Live 僅使用遊戲內建 Auto；資源消耗可設定，不自動補充資源。

目前仍在開發。原始碼包含任務設定介面、錄影畫面辨識、Auto 檢查、結果紀錄、導航基礎與子程序管理；完整日常執行及可攜打包尚未驗收。預設只勾選連接遊戲。

料理、單人 Live 與多人 Live 的局數分別由使用者填寫。釣魚使用參考錄影中的第二個釣點；每日限次項目可只驗證互動入口，不消耗遊玩次數。

目前已在實際遊戲中驗證第二釣點「菈米的冰天雪堡」的地圖選擇、移動、有限距離接近及釣魚設定入口；驗證停在「開始」按鈕前，尚未驗收釣魚遊玩。地圖卡片須確認地點名稱後才移動，並處理玩家標記遮住釣點圖示的情況。

## 開發

使用 Python 3.13 x64 與 .NET 10，安裝 `requirements-dev.txt` 後執行：

```powershell
python tools/build_resources.py
python tools/validate_resources.py
python -m pytest -q
```

複製專案時須包含子模組，`dependencies.lock.json` 記錄鎖定的上游提交。上游模組另需相應執行環境與模型，並與 Maa Agent 的環境分開。組件聲明見 `THIRD_PARTY_NOTICES.md` 及 `upstream/holodori/LICENSES.md`。
