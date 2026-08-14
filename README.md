# Novel-to-Audio-Comic 小说转有声漫画管线

将小说自动转换为**有声漫画**的完整流水线：切片小说 → ComfyUI 生成漫画分镜图 → 语音合成（GPT-SoVITS / LongCat）→ 浏览器在线阅读。

## 功能特性

- **小说切片**：按剧情语义把小说切成漫画场景 JSON（每页含图片提示词 + 对话 + 可选音频），支持 low / medium / high 三档粒度
- **漫画生图**：基于 Anima 模型 + ComfyUI 的 SDXL 文生图工作流，支持角色 LoRA 注入、画师标签、系列标签
- **语音合成**：双后端可选
  - 默认 **GPT-SoVITS**（角色权重克隆，需 ckpt+pth 权重）
  - 可选 **LongCat-AudioDIT**（零样本克隆，只需 3-15s 参考音频；模型未安装时自动跳过并提示，不影响默认流程）
- **在线阅读**：浏览器分页阅读，自动播放配音，支持"停声后/无声页"间隔调节、单页配音重生成、提示词编辑

## 目录结构

```
├── slicer/               # 小说切片（novel-slicer skill + SliceIO）
├── image_gen/            # ComfyUI 生图客户端 + 工作流
│   └── workflows/        #   anima-sdxl-direct.json（默认生图）、longcat-tts-api.json（可选 TTS）
├── audio_gen/            # TTS 客户端：tts_client(GPT-SoVITS) / longcat_client(LongCat)
├── orchestrator/         # pipeline.py 主流程（切片→生图→配音→部署）
├── frontend/             # 在线阅读前端资源
├── config/
│   ├── settings.json     # 项目配置（工作流、角色映射 character_model_map 等）
│   ├── characters.json   # 角色表（外观、别名）
│   ├── paths.json        # ★ 本地路径统一配置（ComfyUI / GPT-SoVITS / ffmpeg）
│   └── paths.bat         # ★ 批处理脚本用的同一份路径配置
├── server.py             # Comic Server（在线阅读）
├── start_comfyui.bat     # 启动 ComfyUI（窗口式）
├── start_tts.bat         # 启动 GPT-SoVITS TTS（窗口式）
├── start_server.bat      # 启动 Comic Server + 自动打开浏览器
├── start_full.bat        # 一键启动全部服务
└── _open_pipeline.py     # 后台启动管线（唯一推荐入口，隐藏窗口防误关）
```

## 环境依赖

| 组件 | 说明 | 下载 |
|---|---|---|
| ComfyUI（秋叶整合包） | 生图 + LongCat TTS 运行环境 | [秋叶整合包 B站专栏](https://www.bilibili.com/opus/1159516886456598528) |
| Anima 基础模型 `anima-base-v1.0.safetensors` | 放 `ComfyUI/models/diffusion_models/` | 见 Anima 官方发布渠道（约 4GB） |
| GPT-SoVITS | 默认语音合成后端 | [GPT-SoVITS 使用文档（语雀）](https://www.yuque.com/baicaigongchang1145haoyuangong/ib3g1e/dkxgpiy9zb96hob4) |
| GPT-SoVITS 角色权重 | `GPT_weights_v2Pro/` + `SoVITS_weights_v2Pro/` + 参考音频 | [ModelScope: aihobbyist/GPT-SoVITS_Model_Collection](https://modelscope.cn/models/aihobbyist/GPT-SoVITS_Model_Collection)（原神/中文、星铁/中文 等按需下载，`{角色}_ZH.zip` 含权重+参考音频） |

### 可选组件（不下载也能正常跑默认流程）

| 组件 | 用途 | 下载 |
|---|---|---|
| LongCat-AudioDiT 3.5B | 零样本语音克隆（`--tts-backend longcat` 时使用），放 `ComfyUI/models/audiodit/` | [HuggingFace: drbaph/LongCat-AudioDiT-3.5B-bf16](https://huggingface.co/drbaph/LongCat-AudioDiT-3.5B-bf16) |
| Qwen3-ASR 1.7B | LongCat 参考音频自动转写，放 `ComfyUI/models/Qwen3-ASR/` | [HuggingFace: Qwen/Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)（LongCat 节点也可自动下载） |
| Anima 画风 LoRA | 提升出图质量 | `ComfyUI/models/loras/`，见 Anima 官方发布渠道 |

## 快速开始（手动启动）

### 1. 配置路径

编辑 `config/paths.json`（Python 代码读取）和 `config/paths.bat`（批处理读取），把 ComfyUI、GPT-SoVITS 路径改成你本机的实际路径。

### 2. 启动服务

```bat
start_comfyui.bat    :: ComfyUI (8188)，需等待模型加载
start_tts.bat        :: GPT-SoVITS TTS (9880)
start_server.bat     :: Comic Server (8012)，自动打开浏览器首页
```

或直接双击 `start_full.bat` 一键启动全部三个服务。

### 3. 切片小说

```
python _open_pipeline.py <切片.json> <项目名> anima-sdxl-direct --skip-audio   :: 只生图
python _open_pipeline.py <切片.json> <项目名> anima-sdxl-direct --skip-image   :: 只配音
python _open_pipeline.py <切片.json> <项目名> anima-sdxl-direct                :: 完整（图+音）
```

- 切片 JSON 由 `novel-slicer` skill 生成（见下方 Agent 用法）
- 管线后台运行、日志写 `logs/pipeline.log`，支持 `--resume` 断点续跑（幂等）
- 生成结果在 `output/<项目名>/`：`images/`、`audio/`、`manifest.json`
- 完成后浏览器打开 `http://127.0.0.1:8012/` 阅读

### 4. 可选：LongCat 语音后端

```
python _open_pipeline.py <切片.json> <项目名> anima-sdxl-direct --tts-backend longcat
```

需要先下载 LongCat-AudioDiT 与 Qwen3-ASR（见上表）并放置到对应目录；未安装时管线会给出提示并跳过，不影响默认流程。

## 使用 AI Agent 自动运行（推荐）

项目内置两个 skill，可被 Claude Code / opencode 等 Agent 自动加载：

| Skill | 触发词 | 作用 |
|---|---|---|
| `novel-slicer` | **切片、切分小说、小说分镜、novel slicer、scene slice** | 小说 → 分镜 JSON（每页图片提示词+对话） |
| `novel-to-comic` | **有声动漫、有声漫画、小说转漫画、novel to comic、audio comic、生成漫画** | 完整管线：切片 → 生图 → 配音 → 前端部署 |

Agent 工作流示例（对 Agent 说）：

```
"把 F:\novel.txt 做成有声漫画"        → 触发 novel-to-comic，全流程自动跑
"切片这本小说"                        → 触发 novel-slicer，只产出切片 JSON
"重新合成声音"                        → 走 pipeline --skip-image（GPT-SoVITS）
"用 longcat 合成"                     → 走 pipeline --skip-image --tts-backend longcat
```

Skill 会把 `config/paths.json` 作为路径唯一来源，无需在 prompt 里写路径。

## 管线流程

1. **切片**：小说按情节/对话/情绪切分为场景 JSON（粒度 low/medium/high）
2. **生图**：逐场景调用 ComfyUI（Anima 模型），幂等（已有图片跳过）
3. **配音**：按角色分组切换权重（减少加载），逐台词合成并拼接为场景音频；有 F0 校验与重试；resume 时按音频时长校验完整性，残缺场景自动重跑
4. **部署**：生成 `manifest.json`，前端可直接浏览

## 服务端口

| 服务 | 端口 |
|---|---|
| ComfyUI | 8188 |
| GPT-SoVITS TTS | 9880 |
| Comic Server | 8012 |

## 说明

- 所有服务均为**窗口式**：独立控制台窗口 + 日志文件双写，**关闭窗口即停止服务**
- 图片生成约 60-70s/张；管线后台运行不阻塞终端
- `output/`、`logs/`、`examples/` 均为运行产物，不入仓库（仅保留 `.gitkeep` 占位）
