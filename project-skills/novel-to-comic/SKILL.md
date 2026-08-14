---
name: novel-to-comic
description: |
  小说到有声动漫完整管线：切片小说 → ComfyUI 生图 → GPT-SoVITS 生声音 → 前端展示。
  触发词：有声动漫、有声漫画、小说转漫画、novel to comic、audio comic、生成漫画。
---

# Novel-to-Audio-Comic 完整管线

当用户提供小说文件时，自动完成从小说到有声漫画的完整流程。

## 版本选择（2026-08-13 起：默认 v1，v2 仅兜底）

| 小说路径 / 用户意图 | 声音后端 | 项目目录 |
|---|---|---|
| 路径含 `Audio comics\v1`，或**无 v1/v2 标记**（默认） | **GPT-SoVITS（9880 独立服务）** | `D:\Code\Audio comics\v1` |
| 路径含 `Audio comics\v2` | LongCat-AudioDIT（ComfyUI 8188，零样本克隆） | `D:\Code\Audio comics\v2` |
| 用户明确说"用新版/用 v2" | LongCat-AudioDIT | `D:\Code\Audio comics\v2` |

规则：
- **切片、生图、前端两版完全一致**，只有"合成声音"一步分流
- 判断顺序：用户明示 > 路径标记 > **默认 v1（GPT-SoVITS）**
- **v2 兜底条件**：v1 缺该角色 GPT-SoVITS 权重，且 ModelScope（`aihobbyist/GPT-SoVITS_Model_Collection` 原神/中文）也下不到时，才走 v2 LongCat
- v1 命令示例：`python _open_pipeline.py <切片.json> <项目名> anima-sdxl-direct --tts-backend gptsovits`
- v2 命令示例：`python _open_pipeline.py <切片.json> <项目名> anima-sdxl-direct --tts-backend longcat`
- v1 输出 wav、v2 输出 mp3；v2 只需角色参考音频（3-15s 干净语音），v1 需要 GPT-SoVITS 权重（ckpt+pth）
- v1 缺角色时先按下方「角色语音模型下载」补权重，补不到才考虑 v2
- v2 comic server 端口为 8013（v1 为 8012），两端口可同时开启互不干扰

## v2 参考音频获取（默认流程，缺音色时执行）

1. **本地查找**：`E:\AI\GPT-SoVITS-v2pro-20250604-nvidia50\voice\{角色名}\` 目录
   - 有 .wav（2.5-10s、带 .lab 文本优先）→ 直接用，settings.json 的 `character_model_map` 加映射
2. **没有则网上下载**，按优先级：
   - ModelScope 数据集（如 `aihobbyist/WutheringWaves_Dataset`、各游戏语音合集，命令行直下免登录）
   - B站中配语音合集（yt-dlp 下载视频音频 + ffmpeg 裁 3-15s 干净段，避免战斗/音效段）
3. **下载落位**：一律放到 `E:\AI\GPT-SoVITS-v2pro-20250604-nvidia50\voice\{角色名}\`（与本地查找同一目录），
   命名 `{角色}_ref.wav` 或保留原文件名，settings.json 加映射
4. 角色映射示例：`"lynae": {"gpt": "", "sovits": "", "ref_audio": "voice/琳奈/linnei_ref.wav", "ref_text": ""}`
5. 下载的临时文件（未裁剪的完整音频等）放 `D:\Code\AITMP\tmp\`，不落 C 盘

## 前置条件（v1，默认）

项目路径：`D:\Code\Audio comics\v1`

结构：
- 切片 skill：`novel-slicer`（本项目 skill）
- 管道脚本：`orchestrator\pipeline.py`
- 后台启动脚本（唯一合法入口）：`_open_pipeline.py`
- 手动完整启动器（仅人肉交互用）：`start_full.bat`
- ComfyUI 客户端：`image_gen\comfyui_client.py`
- TTS 客户端：`audio_gen\tts_client.py`（GPT-SoVITS，默认）/ `longcat_client.py`（LongCat，兜底）
- 生图工作流：`image_gen\workflows\anima-sdxl-direct.json`
- 切片输出目录：`examples\`
- 最终输出目录：`output\{project_name}\`
- 日志目录：`logs\`

服务端口：
- ComfyUI: 8188（生图共用）
- TTS: 9880（v1 GPT-SoVITS）
- Comic Server: 8012（v1）/ 8013（v2，可同时开）

工作流 KSampler 参数：
- steps=30, cfg=5, sampler_name=er_sde, scheduler=simple

Python 路径：`E:\AI\GPT-SoVITS-v2pro-20250604-nvidia50\runtime\python.exe`

## 流程

### Step 1 — 启动服务

先检查端口连通性。不通的依次**弹窗**启动：
- **ComfyUI(8188) 和 Comic Server(8012) 用窗口式启动**（调用 `start_comfyui.bat` / `start_server.bat`，
  各开一个独立控制台窗口，logtee 窗口实时显示日志 + 写日志文件，**关窗即停**）。
- **TTS(9880) 不要在这里启动**——TTS 由 pipeline 在音频阶段按需启动
  （`run_audio_gen` 检测 9880 不通时自动 `start_tts_server()`，同样弹独立窗口启动）。
  提前拉 TTS 会在生图阶段（pipeline 未到音频阶段）空转占用 ~2.3GB+ 显存，属于浪费。

```powershell
# 清理旧进程（仅 8188 和 8012；9880 留空让 pipeline 按需启动）
foreach ($port in 8188,8012) {
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
}

# ComfyUI（如 8188 不通）：弹独立控制台窗口（复用 start_comfyui.bat: 标题+UTF8日志+关窗即停）
cd "D:/Code/Audio comics/v1"; python _open_window.py start_comfyui.bat

# Comic Server（如 8012 不通）：弹独立控制台窗口
cd "D:/Code/Audio comics/v1"; python _open_window.py start_server.bat

# ⚠️ TTS(9880)：不在此启动。pipeline 音频阶段会弹窗按需拉起。
```

等待端口就绪后再继续——ComfyUI 需要 30-60s 加载模型。已通的跳过。

> **提示**：`start_full.bat` 保留为手动完整启动器（三个窗口），供交互式手动使用（会连 TTS 一起拉起）。
> 自动化跑管道时，TTS 由 pipeline 在音频阶段弹窗按需启动，无需手动。

> **⚠️ 硬性规定（2026-08-08 起，防工具卡死）**：后台启动管道**只准用 `_open_pipeline.py`**。
> 禁止 `Start-Process`、`start /B`、`nohup`、PowerShell `&` 直接调 pipeline.py——这些方式会让后台子进程
> 共享调用方控制台句柄，opencode 等工具的 bash 调用会一直等到管道退出（几十小时）才返回，表现为"卡死"。
> `_open_pipeline.py` 内部用 `start "" /MIN ... < NUL > log` 开独立新控制台断掉句柄继承，调用方 ~0.1s 秒回。
> 平台是 Windows/PowerShell，不存在 nohup；Linux 写法一律无效。

### Step 2 — 切片小说

加载 `novel-slicer` skill，按照规则对小说进行切片。

1. 读取小说文件，理解角色构成、剧情结构、场景切换点
2. 按 slicer 规则生成 JSON 切片文件。slicer 会自动检查同名冲突（如已有文件则生成 `_v1`、`_v2`），**用实际保存的路径**继续下一步

JSON 字段约束：
- img_prompt_en 中角色只写名+游戏名，不写默认外貌（小说没写的发色/肤色/体型/经典服装不要画蛇添足）
- 场景特定描述（换装/服装/动作/环境）小说明确写了才写，不臆想
- 包含 `@rosumerii` 画师标签
- **动作/行为句必须用角色规范名点名主宾**（谁对谁做什么），禁止 generic 的 woman/man/young lady 等
- canvas 为 {"width": 1152, "height": 1536}
- dialogue 为空时 has_audio 为 false
- 默认画师：`@rosumerii`，默认游戏系列：`(Genshin Impact:1.2)`

### Step 3 — 运行管道

使用 Step 2 的实际输出路径运行，调用 `_open_pipeline.py`（CREATE_NEW_CONSOLE 全新控制台启动，输出进日志，调用方 ~1s 返回）：

```powershell
python "D:\Code\Audio comics\v1\_open_pipeline.py" {实际切片路径} {project_name} anima-sdxl-direct
```

（在 `D:\Code\Audio comics\v1` 下执行，或用 GPT-SoVITS 自带 Python 的绝对路径调用）

注意：
- `--scenes` 参数必须使用 Step 2 slicer 返回的实际文件路径（可能带 `_v1`/`_v2` 后缀），不要用模板路径
- pipeline 是长期任务，后台运行并重定向日志

**⚠️ 硬性规定（2026-08-09 修订，防工具卡死）**：后台启动管道**只准用 `_open_pipeline.py`**（或 `_open_window.py` 系新窗口启动器）。
- **禁止** `& "D:\Code\Audio comics\v1\_open_pipeline.py" ...`——其内部 `cmd start` 在 opencode 管道捕获环境下分离失效，子进程继承调用方 stdout 管道句柄，工具会一直等到管线退出（1-2 小时）才返回，表现为"卡死"
- **禁止** `Start-Process` + `-RedirectStandard*`、`start /B`、nohup、PowerShell `&` 直接调 pipeline.py——同理会共享句柄
- 启动后**只做三件事**：查进程在不在（`Get-CimInstance Win32_Process -Filter "Name='python.exe'"`）→ 查 `logs\pipeline.log` 有无输出 → 返回；**不在同一命令里等待**
- 等进度用**轮询**：短命令数 `output\{project}\images\` 文件数 / 看日志尾巴，每轮命令短超时（15-30s）；命令若超时被杀，先查进程是否还在跑，在则继续轮询（管线幂等，勿重复启动）
- 平台是 Windows/PowerShell，不存在 nohup；Linux 写法一律无效

**⚠️ 生成顺序（重要，2026-08 起默认串行）**：

pipeline 默认**串行两阶段**：先生图（ComfyUI 独占 GPU）→ 后生音（TTS 独占 GPU）。
实测：并行双线程时 TTS 与 ComfyUI 抢 GPU/显存，生图从 66s/张 恶化到 170s/张；
串行时图片 ~66s/张，音频因**按角色分组排序**（权重只切换一次，切换次数 200+→~13）仅需 30-40 分钟。

可选参数：
- `--parallel`：恢复旧的并行双线程（仅限小项目/显存富余时使用，启动时会按显存余量警告）
- `--no-audio-sort`：关闭音频按角色排序
- `--skip-audio` / `--skip-image`：只跑单个阶段（配合 `--resume` 可断点续跑，幂等跳过已完成场景）

**显存规格参考**（启动时 pipeline 会打印实测值）：
- ComfyUI 生图占用 ~12GB / 16.3GB（anima-base-v1.0 + Qwen3-0.6B CLIP 工作流）
- TTS 服务 base ~2.3GB + 每角色权重对 ~1.1GB，服务只驻留当前 1 对，切换即换载
- 并行模式安全条件：显存余量 ≥ 4.5GB（TTS base + 1 对 ≈ 3.4GB）

管道会依次：
1. 为每个场景调用 ComfyUI 生成图片（幂等：已有则跳过）
2. 为每个场景调用 TTS 生成音频（幂等：已有则跳过）
3. 构建 manifest.json
4. 部署前端

### Step 4 — 打开浏览器

在浏览器中打开 `http://127.0.0.1:8012/` 查看结果。

## 日志查看

所有日志在 `logs/` 目录：
- `comfyui.log` — ComfyUI 输出和错误
- `tts.log` — TTS 服务输出和错误
- `comic_server.log` — Comic Server 输出
- `pipeline.log` — 管道运行日志

排查问题时先看对应日志。

## 小说路径参考

| 小说 | 路径 |
|------|------|
| 流风尽欢 | D:\Code\Model\test\小说\流风尽欢.txt |
| 妈妈的味道 | D:\Code\Model\test\小说\妈妈的味道.md |

可以按用户提供的新路径处理。

## 角色语音模型下载（缺模型时用）

> 模型合集：ModelScope 仓库 `aihobbyist/GPT-SoVITS_Model_Collection`（3918 个模型，含原神/星铁/绝区零/崩坏三/蔚蓝档案/鸣潮/明日方舟/妮姬，各游戏下分 中文/日语/英语/韩语 子目录）。
> 网页入口：`https://pan.baicai1145.com/baicai1145/GPT-SoVITS模型/{游戏}/{语种}/`（浏览器抓取用）。
> **只下中文模型**（zip 命名 `角色_ZH.zip`，约 200MB，含 ckpt + pth + reference_audios 参考音频）。

### 主方式：ModelScope API 直接下载（首选，无需浏览器）

```python
# 1) 查文件信息（Path/Size/Sha256）
GET https://modelscope.cn/api/v1/models/aihobbyist/GPT-SoVITS_Model_Collection/repo/files?Revision=master&Root=原神/中文&Recursive=true

# 2) 直接下载（repo 接口返回文件二进制，无签名过期问题）
GET https://modelscope.cn/api/v1/models/aihobbyist/GPT-SoVITS_Model_Collection/repo?Revision=master&FilePath=原神/中文/{角色}_ZH.zip
```

下载后校验 SHA256（与 `repo/files` 接口返回的 Sha256 一致），再解压安装。

### 备方式：浏览器页面抓取直链

打开 `https://pan.baicai1145.com/baicai1145/GPT-SoVITS模型/{游戏}/{语种}/{角色}_ZH.zip` 详情页，点"下载"按钮，从浏览器下载事件抓取真实 URL（ModelScope CDN，带 `auth_key` 有时效，须立即下载）。

### 解压与安装（⚠️ 中文文件名编码坑）

zip 内文件名是 **GBK 编码**，Python `zipfile` 默认按 cp437 误读导致乱码（`妮露_ZH` 变 `─▌┬╢_ZH`），**必须修复后再解压**：

```python
with zipfile.ZipFile(zip_path) as z:
    for info in z.infolist():
        raw = info.filename.encode("cp437", errors="replace")
        name = raw.decode("gbk", errors="replace")   # 修复中文名
        # 按修复后的 name 落盘
```

安装到三个位置（与克洛琳德/千织一致）：

| 内容 | 目标路径 |
|------|---------|
| `{角色}_ZH-e10.ckpt`（GPT 权重） | `E:\AI\GPT-SoVITS-v2pro-20250604-nvidia50\GPT_weights_v2Pro\` |
| `{角色}_ZH_e10_s{数字}_l32.pth`（SoVITS 权重） | `E:\AI\GPT-SoVITS-v2pro-20250604-nvidia50\SoVITS_weights_v2Pro\` |
| `reference_audios\中文\emotions\*.wav`（参考音频） | `E:\AI\GPT-SoVITS-v2pro-20250604-nvidia50\voice\{角色}\` |

参考音频需补写同名 `.lab` 文本（内容 = wav 文件名去掉【默认】前缀的台词）。安装后可用 `ModelIndexer` 验证：`audio_gen/model_indexer.py` 的 `list_all_characters()` 应包含该角色。

## 注意事项

1. ComfyUI / Comic Server / TTS 均为**弹窗式**服务：独立控制台窗口 + logtee 双写日志，**关闭窗口即停止服务**（释放显存）
2. 管道使用的 Python 是 GPT-SoVITS 自带的：`E:\AI\GPT-SoVITS-v2pro-20250604-nvidia50\runtime\python.exe`
3. 图片生成较慢（约 70秒/张），需要等待
4. Pipeline 自带幂等检查，中途断掉重跑会自动续传
5. Pipeline 自带输出重名检查，同名项目自动改名 `_v1`/`_v2`

## 显存驻留与释放（2026-08-12 实测经验）

**背景**：16GB 显存（RTX 5060 Ti）。管线串行两阶段（先图后音）时模型按需加载，但 **ComfyUI 进程是常驻服务，模型加载后不会自动卸载**。

### 各模型驻留情况

| 模型 | 何时加载 | 何时释放 | 说明 |
|------|---------|---------|------|
| **anima-base-v1.0 生图模型**（~12GB） | 生图阶段首个任务提交时 | **生音阶段被 LongCat 自动替换** | ComfyUI 管理显存：生音阶段 LongCat 加载时会把生图模型逐出（实测生音时显存 12.4GB→14.4GB，未叠加到 24GB），**生图阶段结束后不主动卸载，但不会与 LongCat 共存** |
| **LongCat-AudioDiT 3.5B**（~7GB）+ **Qwen3-ASR 1.7B**（~4GB） | 生音阶段首次合成时 | **不释放，驻留**（keep_model_loaded=true） | 日志可见 `[LongCatAudioDiT] VBAR active，skipping manual CPU offload`。管线结束后仍占 **~14GB**（实测 14.1GB），是显存残留的大头 |
| TTS(GPT-SoVITS 9880) 权重对 | 按角色切换 | 切换即换载 | 只驻留当前 1 对，无残留问题 |

### 关键结论：连续跑多个项目前必须重启 ComfyUI

**管线结束后，ComfyUI 内驻留的模型不会随管线退出而释放**（管线只是 HTTP 客户端，ComfyUI 是独立常驻进程）。
实测：琴团长项目完成后显存仍占 **14.1GB/16.3GB**——残留的是 **LongCat 语音模型**（生图模型已被替换掉，不会叠加）。
此时直接启动下一个项目：
- 生图阶段需加载 anima-base-v1.0（12GB），而 LongCat 还占 14GB → **16GB 显存装不下，ComfyUI 会因显存不足逐出 LongCat 或直接 OOM 失败**
- 极端情况下（配合 PCIe 链路问题）曾触发整机掉电保护

⚠️ **判别要点（用户疑问澄清）**：
- 生图模型（anima-base-v1.0）**不是残留问题**：它只在生图阶段占显存，生音阶段被 LongCat 替换，不会两套模型叠加共存。生图失败/中断时重启 ComfyUI 即可。
- **LongCat 才是残留大户**：`keep_model_loaded=true` 是 v2 工作流写死的（`longcat-tts-api.json`），代码里没有 unload 逻辑（`longcat_client.py` 无 release/offload 调用），只能靠**重启 ComfyUI 进程**释放。
- 若只是单项目断点续跑（`--resume`），ComfyUI 已驻留模型反而省去重复加载，**不要**重启；只有**换新项目**才需要重启释放。

### 正确流程（项目间切换时）

```powershell
# ① 管线完全结束后，重启 ComfyUI 释放全部驻留模型
Get-NetTCPConnection -LocalPort 8188 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
Start-Sleep -Seconds 5
nvidia-smi --query-gpu=memory.used --format=csv,noheader   # 应回落到 ~2GB（系统占用）

# ② 重新拉起 ComfyUI（Comic Server 8012 无需重启）
python _open_window.py start_comfyui.bat
```

### 验证方法（确认模型已释放）

```powershell
# 显存应回落至 ~2GB 基线
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader

# 确认无残留 LongCat 进程（正常应无输出）
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "longcat|AudioDiT|audiodit" }

# 确认管线进程已退出
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "pipeline.py" }
```

### 判别要点（用户疑问澄清）

- **生图模型不用手动管**：生图阶段本身就要独占 ~12GB，串行流程下每次只加载一套，重启 ComfyUI 后按需重新加载即可，无叠加风险。
- **LongCat 才是残留大户**：`keep_model_loaded=true` 是 v2 工作流写死的（`longcat-tts-api.json`），代码里没有 unload 逻辑（`longcat_client.py` 无 release/offload 调用），只能靠**重启 ComfyUI 进程**释放。
- 若只是单项目断点续跑（`--resume`），ComfyUI 已驻留模型反而省去重复加载，**不要**重启；只有**换新项目**才需要重启释放。

