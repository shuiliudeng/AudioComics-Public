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
├── .claude/skills/       # Agent skill（Claude Code 项目位置）
├── .opencode/skills/     # Agent skill（opencode 项目位置）
├── .agents/skills/       # Agent skill（Codex 项目位置）
├── data/danbooru_artists.txt  # 完整画师标签列表（88124 名，来自 danbooru）
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
| Anima 基础模型 `anima-base-v1.0.safetensors` | 2B 参数动漫文生图模型（CircleStone Labs × Comfy Org），放 `ComfyUI/models/diffusion_models/` | [HuggingFace: circlestone-labs/Anima](https://huggingface.co/circlestone-labs/Anima/tree/main/split_files/diffusion_models) |
| Anima CLIP `qwen_3_06b_base.safetensors` | 文本编码器，放 `ComfyUI/models/text_encoders/` | [同仓库 split_files/text_encoders](https://huggingface.co/circlestone-labs/Anima/tree/main/split_files/text_encoders) |
| Anima VAE `qwen_image_vae.safetensors` | 放 `ComfyUI/models/vae/` | [同仓库 split_files/vae](https://huggingface.co/circlestone-labs/Anima/tree/main/split_files/vae) |
| Anima LoRA ×2 | `anima-highres-aesthetic-boost`（高清美学增强）+ `anima-turbo-lora-v0.2`（Turbo 加速：8-12 步+CFG1 快速出图），放 `ComfyUI/models/loras/` | [HuggingFace: circlestone-labs/Anima-Official-LoRAs](https://huggingface.co/circlestone-labs/Anima-Official-LoRAs)（展示图见 [CivitAI](https://civitai.com/user/circlestone_labs/models)） |
| GPT-SoVITS | 默认语音合成后端 | [GPT-SoVITS 使用文档（语雀）](https://www.yuque.com/baicaigongchang1145haoyuangong/ib3g1e/dkxgpiy9zb96hob4) |
| GPT-SoVITS 角色权重 | `GPT_weights_v2Pro/` + `SoVITS_weights_v2Pro/` + 参考音频 | [ModelScope: aihobbyist/GPT-SoVITS_Model_Collection](https://modelscope.cn/models/aihobbyist/GPT-SoVITS_Model_Collection)（原神/中文、星铁/中文 等按需下载，`{角色}_ZH.zip` 含权重+参考音频） |

> ⚠️ **GPT-SoVITS 下载注意**：语雀文档提供两个版本——**N 卡 50 系版本**（RTX 50 系列显卡专用）和**非 50 系版本**，请按你的显卡选择对应版本，装错版本会导致 CUDA/显存相关错误。

### 可选组件（不下载也能正常跑默认流程）

| 组件 | 用途 | 下载 |
|---|---|---|
| LongCat-AudioDiT 3.5B | 零样本语音克隆（`--tts-backend longcat` 时使用），放 `ComfyUI/models/audiodit/` | [HuggingFace: drbaph/LongCat-AudioDiT-3.5B-bf16](https://huggingface.co/drbaph/LongCat-AudioDiT-3.5B-bf16) |
| Qwen3-ASR 1.7B | LongCat 参考音频自动转写，放 `ComfyUI/models/Qwen3-ASR/` | [HuggingFace: Qwen/Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)（LongCat 节点也可自动下载） |
| Anima 画风 LoRA | 提升出图质量（可选扩展，见上方 LoRA 链接） | `ComfyUI/models/loras/`，见 [Anima-Official-LoRAs](https://huggingface.co/circlestone-labs/Anima-Official-LoRAs) |

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

项目内置两个 skill，可被主流 Agent 自动加载：

| Skill | 触发词 | 作用 |
|---|---|---|
| `novel-slicer` | **切片、切分小说、小说分镜、novel slicer、scene slice** | 小说 → 分镜 JSON（每页图片提示词+对话） |
| `novel-to-comic` | **有声动漫、有声漫画、小说转漫画、novel to comic、audio comic、生成漫画** | 完整管线：切片 → 生图 → 配音 → 前端部署 |

### Skill 安装位置（按你的 Agent 选择）

仓库内置了**三份标准位置**（内容相同），每个 Agent 都认自己的原生目录，装好即自动加载：

| Agent | 位置 | 说明 |
|---|---|---|
| **opencode** | `.opencode/skills/<name>/SKILL.md` | 项目内自动加载，无需配置 |
| **Claude Code** | `.claude/skills/<name>/SKILL.md` | 项目内自动加载 |
| **Codex (OpenAI)** | `.agents/skills/<name>/SKILL.md` | 项目内自动加载 |
| 任意 Agent（全局） | `~/.claude/skills/`、`~/.agents/skills/` | 把对应目录复制到用户级即可全局生效 |

> 说明：`.agents/skills/` 是 Codex 的原生项目技能目录；opencode 只认用户级 `~/.agents/skills/`（项目级请用 `.opencode/skills/`）；Claude Code 只认 `.claude/skills/`。没有单一目录能被所有 Agent 的项目级加载，故仓库同时提供三份。

Agent 工作流示例（对 Agent 说）：

```
"把 F:\novel.txt 做成有声漫画"        → 触发 novel-to-comic，全流程自动跑
"切片这本小说"                        → 触发 novel-slicer，只产出切片 JSON
"重新合成声音"                        → 走 pipeline --skip-image（GPT-SoVITS）
"用 longcat 合成"                     → 走 pipeline --skip-image --tts-backend longcat
```

Skill 会把 `config/paths.json` 作为路径唯一来源，无需在 prompt 里写路径。

## 画师标签

- **默认画师**：`@rosumerii`（切片 skill 与管线的默认配置）
- **完整画师列表**：`data/danbooru_artists.txt`（88,124 名，格式 `画师名 | 作品数`，生成日期见文件头）——画师标签全部来自 [danbooru](https://danbooru.donmai.us/)（Danbooru 标签体系），提示词中使用格式为 `@画师名`（如 `@dairi`）
- **换画师**：直接告诉 Agent 即可（如"用 @dairi 画"），切片时会自动覆盖默认的 `@rosumerii`；也可在切片 JSON 的 `img_prompt_en` 中手动替换

## 管线流程

1. **切片**：小说按情节/对话/情绪切分为场景 JSON（粒度 low/medium/high）
2. **生图**：逐场景调用 ComfyUI（Anima 模型），幂等（已有图片跳过）
3. **配音**：按角色分组切换权重（减少加载），逐台词合成并拼接为场景音频；有 F0 校验与重试；resume 时按音频时长校验完整性，残缺场景自动重跑
4. **部署**：生成 `manifest.json`，前端可直接浏览

## 在线阅读前端使用

启动 `start_server.bat` 后浏览器打开 `http://127.0.0.1:8012/`。

### 首页（项目库）

- 项目卡片列表（显示图片/音频徽标、场景数），点击卡片进入阅读页
- 顶部服务状态指示灯：ComfyUI / TTS 是否在线
- `+ New`：新建项目页（上传切片 JSON）

### 阅读与播放

| 操作 | 方式 |
|---|---|
| 上一页 / 下一页 | 点击画面左右区域、`←` / `→` 方向键、底部按钮 |
| 自动播放 | 点击 `Auto`（或按 `a`），再点变 `Pause` |
| 播放/暂停配音 | 点击画面（空格键亦可） |
| 翻页间隔 | 控制栏「停声后」(音频播完再停几秒) 与「无声页」(无音频页停留几秒) 两个输入框，随时调整，浏览器记忆 |

### 编辑与重新生成（核心功能）

**双击画面**打开右侧编辑面板，可对当前场景做四件事：

1. **改图**：修改 `Image Prompt` 文本框 → 点 **`Regen Image`** → 调 ComfyUI 重新生成该场景图片（生成中显示进度，完成后自动替换画面）
2. **改配音**：修改 `Dialogue (TTS)` 文本框（一行一句，格式 `角色名: 台词` 或纯台词）→ 点 **`Regen TTS`** → 调 TTS 重新合成该场景音频
3. **换工作流**：`Workflow` 下拉选择（需 ComfyUI 在线）→ **`Switch`** 切换
4. **保存**：**`Save`** 把当前编辑结果写回 `manifest.json`（持久化，刷新不丢）

编辑面板右下角 `Close` 关闭。

> 提示：`Regen Image` / `Regen TTS` 直接调服务重跑，适合"某页图不满意重出、某句台词口型重配"；批量全量重跑仍建议走管线（`--skip-image` / `--skip-audio` + `--resume`）。

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
