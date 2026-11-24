# Strata Inkling-2-Mini 本機專案（一鍵安裝 + GUI 控制台 + Open Agent Gateway）

針對 **Windows（AMD RX 6700 XT 12 GB RDNA2 `gfx1031` / RX 7000–9000 / NVIDIA CUDA）** 與 **macOS Apple Silicon（MacBook Pro M5 24 GB · Metal 4 MPP）** 雙平台深度實測調校的 **Thinking Machines Inkling-2-Mini（26.4B MoE）** 完整本機部署方案，內建：
- **一鍵自動安裝與下載腳本**（Windows `Install.cmd` / macOS `./install-mac.sh`：自動偵測硬體、選擇最佳 `SQ-10`/`SQ-15`/`SQ-20` 量化包、SHA-256 校驗＋32 MiB 分塊自動修復）
- **無黑框現代化圖形控制台**（Windows `Inkling-2-Mini 控制台.lnk` / macOS `Inkling-2-Mini 控制台.app`）
- **低延遲開放 Agent 閘道**（單一埠口預設 `11435`，同時相容 **OpenAI / Ollama / LM Studio / Anthropic** 四大協定，內建 Keep-Alive 連線池、`TCP_NODELAY`、JIT 自動喚醒載入與閒置釋放記憶體）

---

## 一鍵安裝與下載（Windows + macOS Apple Silicon M1–M5 雙平台）

### Windows（AMD RX 6000/7000/9000 HIP / NVIDIA CUDA）
```bat
git clone https://github.com/ckijficqstrvy/Strata-inkling.git
cd Strata-inkling
Install.cmd
```

`Install.cmd`（呼叫 `scripts\Setup-Inkling.ps1`）會自動完成：
1. **自動偵測顯卡**：自動匹配 `hip-rdna2`（RX 6000）、`hip-rdna3`（RX 7000）、`hip-rx9070`（RX 9000 / R9700）、`hip-strix`（Strix Halo）或 `cuda-cuda12`（NVIDIA），並選擇最適合的 `SQ-10` / `SQ-15` / `SQ-20` 量化包與最佳調校參數。
2. **續傳下載＋自動分塊修復校驗**：自動下載 Strata v0.2.20（`strata-inkling 0.1.66` + `strata-control` 內建 Python 3.12）與 10.47 GB 模型權重，透過 `scripts\Verify-Download.py` 驗證全檔 SHA-256；若傳輸發生單點位元錯誤，自動只針對損毀的 32 MiB 區塊進行 Range 二次共識修復，免整份重抓。
3. **自動完成配置**：建置 o200k tokenizer、執行 GPU `--selfcheck`、產生調校設定檔、圖示與無黑框捷徑 **`Inkling-2-Mini 控制台.lnk`** 並自動開啟圖形介面。

> 進階參數範例（PowerShell）：
> - 加裝看圖 Vision 模組：`.\scripts\Setup-Inkling.ps1 -WithVision -LaunchGui`
> - 指定下載 SQ-15（16 GB+ 顯卡）：`.\scripts\Setup-Inkling.ps1 -ModelPack SQ-15 -LaunchGui`
> - 預先開啟區網連線：`.\scripts\Setup-Inkling.ps1 -LanAccess -LaunchGui`

### macOS Apple Silicon（MacBook Pro M5 24 GB · Metal 4 MPP 原生加速）
```bash
git clone https://github.com/ckijficqstrvy/Strata-inkling.git
cd Strata-inkling
./install-mac.sh
```
- **自動匹配最佳量化包**：24 GB 統一記憶體（如 MacBook Pro M5）自動下載 **`SQ-15`（14.35 GB，與原版 top-1 一致率 96.68%）**；32 GB+ 自動下載無損 **`SQ-20`（19.35 GB）**；16 GB 自動下載 **`SQ-10`（10.47 GB）**。
- **獨立 Metal 4 MPP 引擎**：自動下載 `strata-inkling-0.1.66-macos-arm64-metal.tar.gz`（內嵌 Metal 4 `mpp::tensor_ops::matmul2d` M5 神經加速器核心，免裝驅動，需 macOS 15+）與 `strata-control` macOS arm64 版，並自動移除 macOS Gatekeeper 隔離屬性。
- **依晶片頻寬自動調校**：基礎版 M1–M5（≤24 GB）預設採用 **`--kv fp8 --draft off --chunk 1024 --max-context 32768`**（搭配 `STRATA_METAL_SPIN=1`、`STRATA_METAL_KEEPALIVE_WINDOW_S=600` 與 `caffeinate` 防休眠），繁中與 8K 長文比原版開啟 DSpark 快 **2.5–2.8 倍**；`Max`/`Ultra` 高頻寬機型則預設開啟 DSpark。
- **免終端機啟動**：安裝完成會自動產生附原生 `.icns` Retina 圖示的 **`Inkling-2-Mini 控制台.app`**（可直接拖進 Dock 雙擊開啟），與 Windows 共用同一套 GUI 與低延遲開放 Agent 閘道。
- 進階參數：`./install-mac.sh --pack SQ-15 --vision --lan`（可加裝 0.64 GB 看圖 Vision 模組、改裝更輕快的 `--pack SQ-10`，或預先開放區網連線）。

---

## 日常操作（免開終端機）

- **🌟 圖形控制台（推薦）**：
  - Windows：雙擊 **`Inkling-2-Mini 控制台.lnk`**
  - macOS：雙擊 **`Inkling-2-Mini 控制台.app`**（或執行 `./start-mac.command`）
  - 集中操作模型啟停、切換速度模式與 `--chunk` 分塊、開關開放 Agent 接口、即時串流測速、監控記憶體壓力與查看日誌。
- **獨立無頭 Agent 閘道（選用）**：Windows 執行 `Start-Gateway.cmd`、macOS 執行 `./start-gateway-mac.command`，可單獨在背景啟動開放 API 閘道（收到外部 Agent 請求自動喚醒載入模型）。

---

## 開放 Agent API 閘道（讓其他 Agent 像接 LM Studio / Ollama 一樣使用）

開放閘道預設監聽 **`http://127.0.0.1:11435`**（刻意避開 Ollama `11434` 與 LM Studio `1234`），可在 GUI「🌐 開放 Agent 接口」分頁一鍵切換至區網／Tailscale（`0.0.0.0`）、更改埠口、設定 API Key、開啟 **JIT 自動喚醒** 與 **閒置自動釋放顯存／記憶體**。

| 協定 / 用戶端 | Base URL | 支援端點 | 推薦填寫 Model 名稱 |
|---|---|---|---|
| **OpenAI / LM Studio**（Cline、Roo Code、Cursor、Continue、Cherry Studio、Open WebUI、LangChain、CrewAI、AutoGen、n8n、Dify） | `http://127.0.0.1:11435/v1` | `/v1/models`, `/v1/chat/completions`, `/v1/completions`, `/v1/responses`, `/api/v0/models` | `inkling-2-mini-sq-15` *(Mac)* / `inkling-2-mini-sq-10` *(Win 12GB)* |
| **Ollama 相容**（任何支援 Ollama Provider 的 Agent、Open WebUI、Continue、`ChatOllama`） | `http://127.0.0.1:11435` | `/`, `/api/version`, `/api/tags`, `/api/ps`, `/api/show`, `/api/chat`, `/api/generate` | `inkling-2-mini:latest` |
| **Anthropic**（Claude Code、Anthropic SDK） | `http://127.0.0.1:11435` | `/v1/messages` | `inkling-2-mini-sq-15` *(Mac)* / `inkling-2-mini-sq-10` *(Win 12GB)* |
| **Strata 內建網頁聊天室** | `http://127.0.0.1:8081` | 瀏覽器直接開聊 | — |

### Agent 串接範例

**Python (OpenAI SDK / LangChain / CrewAI)**
```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:11435/v1", api_key="local")
resp = client.chat.completions.create(
    model="inkling-2-mini-sq-15",  # Windows 12 GB 可填 inkling-2-mini-sq-10，或統一填 inkling-2-mini
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
```bash
# macOS / Linux (zsh/bash)
export ANTHROPIC_BASE_URL="http://127.0.0.1:11435"
export ANTHROPIC_API_KEY="local"
```
```powershell
# Windows (PowerShell)
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:11435"
$env:ANTHROPIC_API_KEY  = "local"
```

---

## 雙平台實測效能總覽（Windows RX 6700 XT vs macOS Apple M5）

兩台實測主機分別採用各自最匹配的量化包（Windows 12 GB VRAM 採 `SQ-10`；macOS 24 GB 統一記憶體採更高品質的 `SQ-15`），調校後橫向對比如下：

| 指標項目 | Windows · AMD RX 6700 XT (12 GB VRAM) | macOS · Apple M5 (24 GB 統一記憶體) | 跨平台亮點對比 |
|---|---|---|---|
| **運算後端** | HIP RDNA2 (`gfx1031`, 無 WMMA) | **Metal 4 MPP 神經加速器 (`mpp=1`)** | M5 具備硬體矩陣單元加速 Prefill |
| **模型量化包 / 品質** | `SQ-10`（10.47 GB · Top-1 `92.35%`） | **`SQ-15`（14.35 GB · Top-1 `96.68%`）** | M5 品質高一檔（多 37% 權重體積） |
| **冷啟動載入時間** | 8.6 s（PCIe 拷貝進 VRAM） | **0.92 s**（mmap 零拷貝，DSpark 1.53 s） | **M5 載入快 9.3×** |
| **最佳全能預設參數** | `--kv k8v8 --draft off`（32K） | `--kv fp8 --draft off --chunk 1024`（32K） | 兩者日常均以純解碼最穩最快 |
| **繁體中文日常 (`t=1.0`)** | **25.4 tok/s** | **25.1–26.2 tok/s** | 幾乎持平（M5 跑更重 SQ-15） |
| **長文 Prefill 讀取速度** | 110.4 p/s *(5.2K prompt)* | **437.2–474.2 p/s** *(7.9K prompt)* | **M5 長文閱讀快 4.0×～4.3×** |
| **長文 Decode 出字速度** | 22.7 tok/s *(5.2K)* | **23.1–25.2 tok/s** *(7.9K)* | M5 長上下文續寫略勝 |
| **低溫 DSpark 爆發上限** | 31.6 tok/s *(數學 `t=0.0`)* | **39.9 tok/s** *(英文寫程式 `t=0.2`)* | **M5 寫程式爆發快 +26%** |
| **全場景平均 Decode** | **24.95 tok/s** | **24.4–25.1 tok/s** | 雙平台日常均穩居 ~25 tok/s 級距 |

---

### 1. Windows：RX 6700 XT (12 GB) 實測調校數據（`SQ-10` · 32K 上下文 · 100% VRAM）

| 組態設定 | 溫度 | 載入 | 寫程式 | 數學推導 | 繁體中文 | 5.2K Prefill | 5.2K Decode | 平均 Decode |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 🏆 **`--kv k8v8 --draft off`（預設極速全能）** | 1.0 | **8.6s** | **24.8** | **26.9** | **25.4** | **110.4 p/s** | **22.7** | **24.95 tok/s** |
| `--kv k8v4 --draft off` | 1.0 | 8.5s | 24.4 | 25.5 | 23.9 | 110.0 p/s | 22.5 | 24.07 tok/s |
| `--kv fp8 --draft off` | 1.0 | 8.5s | 24.3 | 25.2 | 23.6 | 96.9 p/s | 22.5 | 23.90 tok/s |
| `--kv k8v8 --draft dspark`（低溫爆發） | 0.0 | 10.0s | 23.9 | **31.6** | 22.9 | — | — | 25.2 tok/s |
| `--kv fp16 --draft dspark`（未調校） | 0.0 | 11.0s | 14.6 | 18.0 | 13.6 | ~60 p/s | ~11.5 | 14.4 tok/s |

- **`--kv k8v8` 較 `fp16` 快 +73%**：原生 INT8 避開 RDNA2 軟體 FP8 轉換開銷。
- **預設關閉 DSpark**：RDNA2 無 WMMA 矩陣核心，驗證草稿樹（`M=6..8`）需 127–153 ms（單 token 的 3.3–4.0 倍），因此日常高溫與繁中／長文關閉 DSpark 反而更快更穩；專跑低溫英文數學／程式時再於控制台切換至「低溫爆發模式」。

---

### 2. macOS：Apple M5 (24 GB) 完整三輪實測調校數據（`SQ-15` · Metal 4 MPP `mpp=1`）

測試環境：MacBook Pro Apple M5（10 核 CPU、24 GB 統一記憶體、Metal 預設 `recommendedMaxWorkingSetSize = 17.76 GiB`、`maxBufferLength = 13.32 GiB`），模型完全常駐熱身後連續量測 5 大真實任務（繁中 `t=1.0`、Python 寫程式 `t=0.2` / `t=1.0`、英文數學 `t=0.0`、7,935-token 8K 長文摘要 `t=0.2`）：

| 組態設定（`SQ-15` · 13.36 GiB） | 載入 | 繁體中文 (`t=1.0`) | 寫程式 (`t=0.2`) | 寫程式 (`t=1.0`) | 數學 (`t=0.0`) | 8K Prefill (7,935 tok) | 8K Decode | 五項平均 Decode | 總顯存配置 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 🏆 **`--draft off --chunk 1024`（32K · 推薦預設）** | **0.92s** | **25.1** | 25.1 | **25.0** | 24.2 | **437.2 p/s** | 22.5 | **24.4 tok/s** | **15.55 GiB** |
| 🏆 **`--draft off --chunk 1024`（16K · 省記憶體）** | **0.92s** | **23.5–26.2** | 24.3–26.0 | **24.9–25.5** | 24.0 | **443.0 p/s** | **23.1** | **24.0–24.6 tok/s** | **15.04 GiB** |
| `--draft off --chunk 512`（32K · 預設分塊） | 0.62s | 23.4 | 24.7 | 24.6 | 24.1 | 408.8 p/s | 22.9 | 23.9 tok/s | 15.21 GiB |
| `--draft off --chunk 512`（32K · `fastmath`） | 0.93s | 25.0 | 26.0 | 24.9 | 24.4 | 438.7 p/s | **25.2** | **25.1 tok/s** | 15.21 GiB |
| 🚀 **`--draft dspark --chunk 1024`（16K · 低溫爆發）** | 1.53s | 9.3 *(4%)* | **39.9** *(66%)* | 25.4 *(37%)* | **27.9** *(70%)* | **474.2 p/s** | 11.0 *(10%)* | 22.7 tok/s | 15.18 GiB |
| `--draft dspark --chunk 1024`（32K） | 1.53s | 8.9 *(4%)* | 37.9 *(66%)* | 24.2 *(37%)* | 26.0 *(57%)* | 438.5 p/s | 10.4 *(10%)* | 21.5 tok/s | 15.74 GiB |
| `--draft dspark --chunk 512`（32K · `k8v8`） | 1.53s | 8.8 *(4%)* | 38.1 *(66%)* | 24.2 *(37%)* | 26.2 *(61%)* | 407.7 p/s | 9.8 *(9%)* | 21.4 tok/s | 15.40 GiB |
| `--draft dspark --chunk 256`（32K） | 1.53s | 8.9 *(4%)* | 38.0 *(66%)* | 24.1 *(37%)* | 25.6 *(61%)* | 337.2 p/s | 10.3 *(10%)* | 21.4 tok/s | 15.23 GiB |
| `--draft dspark --chunk 1024`（16K · `SPIN=0`） | 1.53s | 8.6 | 34.1 | 24.8 | 24.9 | 433.8 p/s | 9.9 | 20.5 tok/s | 15.18 GiB |
| 原版 Mac 預設（`--draft dspark --chunk 512`, 32K） | 2.44s | **8.9** *(4%)* | 35.6 *(61%)* | 26.2 *(41%)* | 26.3 *(57%)* | 411.3 p/s | **10.1** *(10%)* | 21.4 tok/s | 15.40 GiB |

> *(括號內百分比為實測 DSpark 草稿接受率；`--chunk 2048/4096` 實測顯存配置膨脹至 `16.42–17.79 GiB` 頂到 Metal 工作集上限，底層 `--bench` Decode 反而由 `29.5` 掉至 `23.8–25.6 tok/s`，故上限鎖定為 `1024`)*

- **為何基礎版 M5 日常應預設 `--draft off`**：Inkling-2-Mini 每層 128 專家（Top-8），DSpark 驗證 8 個草稿 token 時會讀入 4–6 倍不同專家權重；繁體中文與長文摘要的接受率僅 **4–10%**，關閉 DSpark 讓繁中從 **8.9 → 25.1–26.2 tok/s（+181%～+194%）**、8K 長文解碼從 **10.1 → 25.2 tok/s（+150%）**，冷啟動更縮短至 **0.92 秒**。
- **低溫英文寫程式／數學切換 DSpark 爆發**：當 `temperature ≤ 0.2` 跑英文程式碼或數學時，DSpark 接受率飆升至 **61–70%**，可將出字速度推上 **38.1–39.9 tok/s**。
- **Metal 引擎參數 A/B 實證**：
  - **`--chunk 1024`**：較預設 `512` 僅多佔 `0.34 GiB`，8K Prefill 穩定提升 **+7%～+15%**（最高 **474.2 p/s**）。
  - **`STRATA_METAL_SPIN=1`（預設）**：較 `SPIN=0` 快 **+7%～+17%**（DSpark 寫程式 `39.9 vs 34.1 tok/s`）。
  - **Safe Math（預設）優於 `STRATA_METAL_MATH=fast`**：M5 解碼受限於統一記憶體頻寬且 MPP 已走硬體矩陣單元，實測同溫下 Safe Math 吞吐更穩（`24.4 vs 24.1`）且維持 100% 精準數值。
  - **`STRATA_METAL_KEEPALIVE_WINDOW_S=600` + `caffeinate`**：延長 GPU 熱駐留至 10 分鐘（系統記憶體壓力升高時自動暫停），並防止 macOS 待機休眠。

---

### 3. Open Agent Gateway 最佳化前後延遲實測（跨平台受惠）

重構 `gateway/gateway.py` 與 `gui/app.py`（HTTP/1.1 Keep-Alive 連線池、全端 `TCP_NODELAY`、2 秒快速健康快取、背景非同步衝突掃描、零重複 `json.loads` SSE 串流轉發）後之實測對比：

| 指標 | 優化前（原版） | 優化後（本機實測） | 改善幅度 |
|---|---:|---:|---:|
| Gateway `/health` P95 延遲 | **1,911.51 ms** | **0.32 ms** | **快 5,973×（-99.98%）** |
| Gateway `/health` 中位數 | 0.55 ms | **0.21 ms** | **-62%** |
| Gateway `/v1/models` 中位數 | 1.07 ms | **0.19 ms** | **-82%（5.6× 快）** |
| Gateway `/api/tags` 中位數 | 0.58 ms | **0.18 ms** | **-69%（3.2× 快）** |
| 串流對話 Gateway 額外 TTFT | **+38.0 ms** | **+6.0 ms** | **-84%** |
| 端對端經 Gateway 實測（5 任務平均） | — | **24.2–24.8 tok/s** *(8K Prefill 442.5 p/s)* | 與直連 `:8081` 幾乎零損耗 |

---

### 4. 24 GB Apple Silicon 記憶體管理與進階榨乾建議

- **關閉高耗記憶體背景 App**：`SQ-15` 連同 32K KV 快取需約 **15.5 GiB** 統一記憶體。執行前建議先關閉 **Google Chrome、Claude、ChatGPT、Hermes、GenOffice、Docker Desktop** 等高耗 RAM 軟體，避免觸發 macOS Swap 換頁掉速；GUI 控制台在 Swap ≥ 2.5 GB 或記憶體使用率 ≥ 85% 時亦會自動列出最吃 RAM 的前 4 個 App 提醒關閉。
- **64K 長上下文進階上限**：macOS 24 GB 機型預設 Metal 工作集上限為 `17.76 GiB`，若要跑 64K 上下文，可暫時執行 `sudo sysctl iogpu.wired_limit_mb=19456`（重開機自動還原）。
- **極致速度／多工選項（`SQ-10`）**：若希望邊開 Chrome 邊跑或追求更高純解碼速度，可執行 `./install-mac.sh --pack SQ-10 --no-launch`（10.47 GB，比 `SQ-15` 少佔 4 GB 記憶體、頻寬負載再降 27%）。
