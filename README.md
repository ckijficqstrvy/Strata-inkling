# Strata Inkling-2-Mini 本機專案（一鍵安裝 + GUI 控制台 + Open Agent Gateway）

針對 **AMD RX 6700 XT (12 GB, RDNA2 `gfx1031`)** 與 Windows 顯卡最佳化的 **Thinking Machines Inkling-2-Mini（SQ-10）** 完整部署方案，內建：
- **一鍵自動安裝與下載腳本**（`Install.cmd`：自動偵測顯卡、下載引擎與模型、SHA-256 校驗＋32 MiB 分塊自動修復）
- **無黑框現代化圖形控制台**（`Inkling-2-Mini 控制台.lnk`）
- **LM Studio / Ollama 風格開放 Agent 閘道**（單一埠口 `11435` 同時相容 **OpenAI / Ollama / LM Studio / Anthropic** 四大協定，支援外部 Agent 自動喚醒載入與閒置釋放顯存）

---

## 一鍵安裝與下載（Windows + macOS Apple Silicon M1–M5 雙平台）

### Windows（AMD RX 6000/7000/9000 HIP / NVIDIA CUDA）
```bat
git clone https://github.com/ckijficqstrvy/Strata-inkling.git
cd Strata-inkling
Install.cmd
```

### macOS Apple Silicon（MacBook Pro M5 24 GB · Metal 原生加速）
```bash
git clone https://github.com/ckijficqstrvy/Strata-inkling.git
cd Strata-inkling
./install-mac.sh
```
- **自動匹配最佳量化包**：24 GB 統一記憶體（如 MacBook Pro M5）自動下載 **`SQ-15`（14.35 GB，與原版 top-1 一致率 96.68%）**；32 GB+ 自動下載無損 **`SQ-20`（19.35 GB）**；16 GB 自動下載 **`SQ-10`（10.47 GB）**。
- **獨立 Metal 引擎**：自動下載 `strata-inkling-0.1.66-macos-arm64-metal.tar.gz`（內嵌 Metal kernel，免裝驅動，需 macOS 15+）與 `strata-control` macOS arm64 版，並自動移除 macOS Gatekeeper 隔離屬性。
- **預設開啟 DSpark 加速**：Apple Silicon Metal 支援高效矩陣運算，預設採用 `--kv fp8 --draft dspark --max-context 32768`。
- **免終端機啟動**：安裝完成會自動產生附圖示的原生 **`Inkling-2-Mini 控制台.app`**（可直接拖進 Dock 雙擊開啟），與 Windows 共用同一套 GUI 與開放 Agent 閘道（`:11435`）。
- 進階參數：`./install-mac.sh --pack SQ-15 --vision --lan`（可加裝 0.64 GB 看圖 Vision 模組或預先開放區網連線）。

---

`Install.cmd`（呼叫 `scripts\Setup-Inkling.ps1`）會自動完成：
1. **自動偵測顯卡**：自動匹配 `hip-rdna2`（RX 6000）、`hip-rdna3`（RX 7000）、`hip-rx9070`（RX 9000 / R9700）、`hip-strix`（Strix Halo）或 `cuda-cuda12`（NVIDIA），並選擇最適合的 `SQ-10` / `SQ-15` / `SQ-20` 量化包與最佳調校參數。
2. **續傳下載＋自動分塊修復校驗**：自動下載 Strata v0.2.20（`strata-inkling 0.1.66` + `strata-control` 內建 Python 3.12）與 10.47 GB 模型權重，透過 `scripts\Verify-Download.py` 驗證全檔 SHA-256；若傳輸發生單點位元錯誤，自動只針對損毀的 32 MiB 區塊進行 Range 二次共識修復，免整份重抓。
3. **自動完成配置**：建置 o200k tokenizer、執行 GPU `--selfcheck`、產生調校設定檔、圖示與無黑框捷徑 **`Inkling-2-Mini 控制台.lnk`** 並自動開啟圖形介面。

> 進階參數範例（PowerShell）：
> - 加裝看圖 Vision 模組：`.\scripts\Setup-Inkling.ps1 -WithVision -LaunchGui`
> - 指定下載 SQ-15（16 GB+ 顯卡）：`.\scripts\Setup-Inkling.ps1 -ModelPack SQ-15 -LaunchGui`
> - 預先開啟區網連線：`.\scripts\Setup-Inkling.ps1 -LanAccess -LaunchGui`

---

## 日常操作（免點 CMD）

- **🌟 `Inkling-2-Mini 控制台.lnk`**（推薦）：雙擊開啟獨立桌面視窗，集中操作模型啟停、切換速度模式、開關開放 Agent 接口、即時串流測速與查看日誌。
- **獨立無頭 Agent 閘道（選用）**：`Start-Gateway.cmd` 可單獨在背景啟動開放 API 閘道（收到外部 Agent 請求自動喚醒載入模型）。

---

## 開放 Agent API 閘道（讓其他 Agent 像接 LM Studio / Ollama 一樣使用）

開放閘道預設監聽 **`http://127.0.0.1:11435`**（刻意避開 Ollama `11434` 與 LM Studio `1234`），可在 GUI「🌐 開放 Agent 接口」分頁一鍵切換至區網／Tailscale（`0.0.0.0`）、設定 API Key、開啟 **JIT 自動喚醒** 與 **閒置自動釋放顯存**。

| 協定 / 用戶端 | Base URL | 支援端點 | 推薦填寫 Model 名稱 |
|---|---|---|---|
| **OpenAI / LM Studio**（Cline、Roo Code、Cursor、Continue、Cherry Studio、Open WebUI、LangChain、CrewAI、AutoGen、n8n、Dify） | `http://127.0.0.1:11435/v1` | `/v1/models`, `/v1/chat/completions`, `/v1/completions`, `/v1/responses`, `/api/v0/models` | `inkling-2-mini-sq-10` |
| **Ollama 相容**（任何支援 Ollama Provider 的 Agent、Open WebUI、Continue、`ChatOllama`） | `http://127.0.0.1:11435` | `/`, `/api/version`, `/api/tags`, `/api/ps`, `/api/show`, `/api/chat`, `/api/generate` | `inkling-2-mini:latest` |
| **Anthropic**（Claude Code、Anthropic SDK） | `http://127.0.0.1:11435` | `/v1/messages` | `inkling-2-mini-sq-10` |
| **Strata 內建網頁聊天室** | `http://127.0.0.1:8081` | 瀏覽器直接開聊 | — |

### Agent 串接範例

**Python (OpenAI SDK / LangChain / CrewAI)**
```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:11435/v1", api_key="local")
resp = client.chat.completions.create(
    model="inkling-2-mini-sq-10",
    messages=[{"role": "user", "content": "Hello!"}],
    temperature=1.0,
    max_tokens=1024,
)
print(resp.choices[0].message.content)
```

**Ollama API（`curl` / `ChatOllama`）**
```bash
curl http://127.0.0.1:11435/api/chat -d '{
  "model": "inkling-2-mini:latest",
  "messages": [{"role": "user", "content": "請用繁體中文打招呼"}],
  "stream": false
}'
```

**Claude Code（Anthropic API）**
```powershell
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:11435"
$env:ANTHROPIC_API_KEY  = "local"
```

---

## RX 6700 XT (12 GB) 實測調校數據（32K 上下文 · 100% VRAM）

| 組態設定 | 溫度 | 載入 | 寫程式 | 數學推導 | 繁體中文 | 5.2K Prefill | 5.2K Decode | 平均 Decode |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 🏆 **`--kv k8v8 --draft off`（預設極速全能）** | 1.0 | **8.6s** | **24.8** | **26.9** | **25.4** | **110.4 p/s** | **22.7** | **24.95 tok/s** |
| `--kv k8v4 --draft off` | 1.0 | 8.5s | 24.4 | 25.5 | 23.9 | 110.0 p/s | 22.5 | 24.07 tok/s |
| `--kv fp8 --draft off` | 1.0 | 8.5s | 24.3 | 25.2 | 23.6 | 96.9 p/s | 22.5 | 23.90 tok/s |
| `--kv k8v8 --draft dspark`（低溫爆發） | 0.0 | 10.0s | 23.9 | **31.6** | 22.9 | — | — | 25.2 tok/s |
| `--kv fp16 --draft dspark`（未調校） | 0.0 | 11.0s | 14.6 | 18.0 | 13.6 | ~60 p/s | ~11.5 | 14.4 tok/s |

- **`--kv k8v8` 較 `fp16` 快 +73%**：原生 INT8 避開 RDNA2 軟體 FP8 轉換開銷。
- **預設關閉 DSpark**：RDNA2 無 WMMA 矩陣核心，驗證草稿樹（`M=6..8`）需 127–153 ms（單 token 的 3.3–4.0 倍），因此日常高溫與繁中／長文關閉 DSpark 反而更快更穩；專跑低溫英文數學／程式時再於控制台切換至「低溫爆發模式」。
