# Strata Inkling-2-Mini 本機專案（RX 6700 XT 調校版）

專案位置：`g:\AI\Strata-inkling`  
引擎：**Strata v0.2.20 / `strata-inkling 0.1.66`**（Windows RDNA2 HIP 原生 `gfx1031` 套件，內建 TheRock ROCm `10.2.0a20260930`）  
模型：**`Inkling-2-Mini-SQ-10.strata`**（10.47 GB / 9.75 GiB，SHA-256 100% 校驗通過，100% 駐留 12 GB VRAM，無 RAM 溢位 / 無 SSD 串流）

## 日常操作（推薦直接用圖形控制台，免點 CMD）

- **`Inkling-2-Mini 控制台.lnk`**（🌟 **推薦首選**）：雙擊即以獨立桌面視窗開啟現代化圖形控制台（無黑框終端機），可一鍵切換模式、啟動/停止並釋放顯存、即時監控顯存衝突與 RAM、直接在介面內測速聊天、以及一鍵複製 OpenAI / Anthropic API 設定（也可右鍵釘選到工作列或複製到桌面）。
- 傳統腳本（備用）：`Start-Inkling.cmd`、`Start-Inkling-DSpark.cmd`、`Status-Inkling.cmd`、`Check-Inkling.cmd`、`Stop-Inkling.cmd` 仍完整保留並與控制台狀態互通。

## API 端點（預設埠 `8081`，避免與 `G:\AI\Strata` 的 `8080` 衝突）

- **網頁聊天室**：`http://127.0.0.1:8081`
- **OpenAI 相容 API**：`http://127.0.0.1:8081/v1`（模型名稱可填 `inkling-2-mini-sq-10`、`inkling-2-mini`、`inkling` 或 `strata`；API key 任意填如 `local`）
- **Anthropic Messages API**：`http://127.0.0.1:8081/v1/messages`

## RX 6700 XT（12 GB）實測與調校總結

實測平台：AMD Ryzen 5 3500X（6C/6T）+ RX 6700 XT 12 GB（驅動 `32.0.21045.29003`）+ Windows 11。

### 關鍵調校發現

1. **100% 全顯存駐留（`hybrid=0`）**：關閉 `--vision`（省下 0.64 GB）並使用壓縮 KV 快取後，`Inkling-2-Mini-SQ-10.strata` 完整塞入 RX 6700 XT 12 GB VRAM，完全不需經 PCIe/RAM 搬運專家權重。
2. **KV 快取改用 `--kv k8v8` 帶來最大加速（+73%）**：相比 `--kv fp16`（平均 14.4 tok/s），`--kv k8v8` 在減半 40 層 SWA-1024 與 8 層 Global KV 頻寬的同時，使用 RDNA2 原生 INT8（無 FP8 軟體轉換開銷），5.2K 長文本 Prefill 還比 `--kv fp8` 快 +14%（110.4 vs 96.9 tok/s）。
3. **為什麼預設關閉 DSpark（`--draft off`）反而更快？**
   透過 `STRATA_HIP_TREE_PROF=1` 實測剖析，RDNA2 沒有 WMMA 矩陣核心，驗證一棵 `M=6～8` 的 DSpark 草稿樹需要 `127–153 ms`（是單 token `38.5 ms` 的 3.3～4.0 倍成本）：
   - 在官方建議對話溫度 **`temperature=1.0`** 下，`--kv k8v8 --draft off` 在繁體中文、寫程式、數學與 5.2K 長文本全面領先（平均 **24.95 tok/s** vs DSpark **20.82 tok/s**；5.2K 長文本解碼更是 **22.7 vs 17.0 tok/s，+34%**），且載入更快（8.6 秒）、多省約 400 MiB VRAM 避免觸發 `max_prefill_chunk=256` 上限。
   - 若您需要大量跑**低溫（`temp <= 0.2`）英文數學/程式碼**（接受率 >55%），再改雙擊 `Start-Inkling-DSpark.cmd` 即可獲得 29.5–32.1 tok/s 爆發速度。

### 實測對照表（32K 上下文，100% VRAM）

| 組態設定 | 測試溫度 | 寫程式 | 數學推導 | 繁體中文 | 5.2K Prefill | 5.2K Decode | 平均 Decode |
|---|---:|---:|---:|---:|---:|---:|---:|
| **`--kv k8v8 --draft off`（預設）** | 1.0 | **24.8** | **26.9** | **25.4** | **110.4 p/s** | **22.7** | **24.95 tok/s** |
| `--kv k8v4 --draft off` | 1.0 | 24.4 | 25.5 | 23.9 | 110.0 p/s | 22.5 | 24.07 tok/s |
| `--kv fp8 --draft off` | 1.0 | 24.3 | 25.2 | 23.6 | 96.9 p/s | 22.5 | 23.90 tok/s |
| `--kv k8v8 --draft dspark` | 1.0 | 23.3 | 23.0 | 20.0 | 103.8 p/s | 17.0 | 20.82 tok/s |
| `--kv k8v8 --draft dspark`（低溫爆發） | 0.0 | 23.9 | **31.6** | 22.9 | — | — | 25.2 tok/s |
| `--kv fp16 --draft dspark`（未調校） | 0.0 | 14.6 | 18.0 | 13.6 | ~60 p/s | ~11.5 | 14.4 tok/s |

## 注意事項

- **在另一台電腦還原安裝**：本 GitHub 儲存庫不包含 10.47 GB 模型權重與編譯後的 ROCm/HIP 執行檔；`git clone` 後直接執行 `Setup-Inkling.cmd` 即可自動下載 Strata v0.2.20 引擎、`Inkling-2-Mini-SQ-10.strata`（自動核對 SHA-256）、建立 tokenizer 與圖形控制台捷徑。
- 啟動前請確認 `G:\AI\Strata`（Qwen）與 Ollama 沒有佔用 RX 6700 XT 顯存，才能確保 100% VRAM 全速運作。
- 官方建議日常對話設定 `temperature = 1.0`，並限制 `max_tokens` 以免模型陷入過長推理迴圈。
- 若未來需要圖片辨識，可從 Hugging Face 下載 `Inkling-2-Mini-Vision.strata`（0.64 GB）放進 `models\Inkling-2-Mini-Strata\` 並在設定檔 `args` 加上 `--vision <路徑>`（會駐留系統 RAM/CPU，不搶佔顯存）。
