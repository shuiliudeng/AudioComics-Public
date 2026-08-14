---
name: novel-slicer
description: |
  将小说文本按情节切分为结构化 JSON，每片对应漫画的一页（图片提示词 + 对话 + 可选音频）。
  触发词：切片、切分小说、小说分镜、novel slicer、scene slice。
---

# Novel Slicer — 小说切片

将小说按剧情语义切分为漫画场景 JSON。你（Claude）扮演 DeepSeek API 的角色完成切片。

## 输入

| 参数 | 说明 |
|------|------|
| novel_path | 小说文本文件路径 |
| characters_path | 角色配置 JSON 路径（可选） |
| granularity | low / medium / high（**默认 high**，未指定一律按 high） |
| output_path | 切分结果 JSON 输出路径 |

## 粒度定义

以中文字符数为参考，实际按剧情语义分割：

| 级别 | 场景数公式 | 场景划分逻辑 |
|------|-----------|-------------|
| low | max(8, 总中文字符数/500) | 按剧情节拍：引入 → 冲突 → 转折 → 高潮 → 结局 |
| medium | max(20, 总中文字符数/250) | 按场景切换 + 对话回合：换地点、换情绪、重要对话 |
| high | max(30, 总中文字符数/150) | 每次明显动作变化、每次完整对话交换、每次情绪转变 |

**公式只估算下限参考量，不设上限**：长篇小说（数万字/百万字）按比例自然切分，每 ~250 字（medium）一个场景，有多少切多少。宁可多切，不可因截断丢失情节/对话。若按比例算出的场景数庞大，按剧情节拍适当合并纯过渡片段，但**任何有对话或关键情节的片段不得省略**。

**重要**：先通读全文理解剧情节奏，再算目标场景数 N。找出所有自然断点，选 N±2 个最合适的。不得按字数硬切。

## 角色命名规则（核心）

img_prompt_en 中**每个角色都必须以「角色全名 (作品名)」格式出现**（作品名＝角色出自的作品，游戏/动画/漫画均可，如 genshin impact）。

**正确写法**：
```
chasca (genshin impact:1.2), aether (genshin impact:1.1), standing close together, moonlight
```

角色名后不加默认外貌描述。只需要 `chasca (genshin impact)`，不写 hair color, skin color, 服装特征。知名角色只写 角色名+作品名，生图模型认识这些角色。

**场景特定描述来源**：
- 角色的默认外貌（发色、肤色、体型、经典服装等）→ 小说没写就**一律不写**，不要画蛇添足
- 角色的服装/状态变化（换装、特定服装、脱衣等）→ 小说明确写了才按原文提取，不自己臆想
- 场景的动作、环境、光线 → 从**小说原文提取**

### 绝对禁止
- 不得写 "a dark-skinned woman" 代替 chasca (genshin impact)
- 不得写 "a young blonde man" 代替 aether (genshin impact)
- 不得写任何角色默认外貌特征（发色、肤色、身高体型等），除非小说原文明确写了
- 不得自己编造小说里没有的角色外貌细节

## Step 1 — 构建角色表

如果提供了 characters_path，读取并扩展它；否则从小说中自行提取角色：

1. 扫描文本中所有出现的人物名称及代称
2. 推断每个角色所属的作品（游戏 / 动画 / 漫画等，如 genshin impact）
3. 记录每个角色在全篇中的服装/状态变化
4. 输出到最终 JSON 的 characters 字段

角色 ID 使用英文短标识（如 chasca、aether）。

## Step 2 — 分析剧情结构

通读小说全文，识别：

- 场景切换：地点变化、时间跳跃、人物进出场 → 必须作为切片边界
- 情绪转折：角情绪的重要变化 → 优先作为边界
- 对话回合：一次完整说话交换 → medium/high 时作为边界
- 关键动作：有明显视觉表现的行动 → high 时作为边界

## Step 3 — 划分场景

按粒度级别选择切片边界。同场景多种动作/对话变化时，尽量拆分以丰富画面。

**切分口径：**
- **剧情/过渡章节**：按对话回合 + 场景切换切分，过渡段可合并。
- 先通读全文理解剧情节奏，再找出所有自然断点，选最合适的场景边界。**不得按字数硬切**，不得为凑固定数量硬拆或硬并。

**约束：**
- 每片必须有明确的视觉呈现主体
- 每片之间的剧情要连贯
- 过渡场景在 low 时可省略，high 时应保留

### Step 3.5 — 忠实保留原文（对话 / 剧情）

1. dialogue 中 text_cn 必须逐字逐句从原文复制（忠实）
2. 严禁删减或改写任何剧情细节、情节走向（忠实）
3. img_prompt_en 按原文描述场景，不臆想不改写

## Step 4 — 为每片生成内容

### 4a. 角色当前外观

追踪每个出场角色的外观状态变化。如有更新则记录。外部特征（发色、肤色等）不记录，只记录服装/状态变化。

### 4b. 图片提示词（img_prompt_en）

格式：
```
(Genshin Impact:1.2), masterpiece, best quality, [角色全名 (作品名:权重)], [其他角色全名 (作品名:权重)], [小说原文中的场景服装/动作/环境], [光线/氛围], speech bubble saying "[英文对话]" (需要时), (@rosumerii:1.0)
```

规则：
- 角色默认外貌一律不写，只写「角色名+作品名」（如 nilou (genshin impact)、thoma (genshin impact)）。生图模型认识这些角色
- 场景的服装/地点/光线：小说明确写了才写，不臆想不改写
- ⚠️ **服装描述必须点名归属角色**：多角色场景中，服装必须写成「谁的服装」（如 `nilou's moon-white gauze dress`、`chiori's black stockings`、`kamisato ayaka in her white kimono`），**禁止**不带主语的裸写（如 `wearing a white dress`）——否则多角色同框时会把 A 的衣服张冠李戴到 B 身上（与下方动作归属规则同理，先点名角色，再写他的服装）
- 场景的动作/环境：按原文正常描述即可
- ⚠️ **动作/行为句里指代角色必须用其规范名**（kamisato ayaka / thoma / aether），
  **禁止**用 generic 的 woman / man / young lady / girl / female / blonde traveler 代替——
  多角色 + 明确动作场景用 generic 词会混淆身份，导致动作绑错人、人物错乱。
  规范名先出现在开头 tag，再在动作句里点名，以绑定 动作→角色。

### 4c. 对话文本（dialogue）

| 字段 | 说明 |
|------|------|
| speaker_id | 对应 characters 中的 ID |
| text_cn | 原始中文对话文本，逐字原文 |
| text_en_for_bubble | 英文翻译 |
| emotion | neutral/happy/sad/shy/excited/determined |

规则：
- 旁白/叙述不放入 dialogue
- 连续对话按自然回合分割成多段

## Step 5 — 输出 JSON

```json
{
  "project": "",
  "granularity": "high",
  "characters": {},
  "scenes": []
}
```

**characters**:
- id, name, voice_model, appearance_log

**scenes[]**:
- id, title, narration_cn, characters_present, characters_appearance
- img_prompt_en (>= 30 词)
- negative_prompt, canvas (default {"width": 1152, "height": 1536})
- dialogue [], has_audio
- lora（可选）：`[{"name": "xxx.safetensors", "weight": 0.8}]`——该场景要注入的角色 LoRA 列表，无则省略（默认不注入）

**lora 字段规则**：
- **结构化配置，不是解析提示词**：切片时按"角色→LoRA 映射"查表填写，pipeline 机械注入，代码不做语义判断
- 有专用 LoRA 的角色（尤其模型不认识的新角色/冷门角色）：查证 LoRA 存在并按其**触发词**写入 img_prompt_en（如 `lynae`、`lynaev4`），同时填 lora 字段
- 无专用 LoRA / 场景不需要：不填该字段，生图走原工作流，零影响
- 同一场景可挂多个 LoRA（按权重叠加），lora 文件名必须与 `ComfyUI/models/loras/` 下的实际文件一致，否则该场景报错（仅该场景失败，不影响其他场景）

**角色可识别性兜底（重要）**：生图模型只认识训练数据内的角色（Anima 训练数据截止 2025-09）。
- 实装晚于截止期的角色（如鸣潮琳奈 2025-12）模型不认识 → 必须①查官方外观（发色/发型/瞳色/标志性服装）写入 img_prompt_en；②优先查专用 LoRA 并填写 lora 字段 + 触发词
- 冷门/易混淆角色（如鸣潮男漂泊者 vs 女漂泊者）→ 明确性别 + 外观特征，避免模型默认画成热度高的版本

**dialogue[]**:
- speaker_id, text_cn, text_en_for_bubble, emotion

## 输出文件

将最终 JSON 写入 output_path。UTF-8 编码，JSON 格式合法。

**保存前检查**：如果 output_path 已存在同名文件，自动生成新文件名：
- 格式：`{原名}_v1.json`、`{原名}_v2.json`，递增直到不冲突
- 保存后将**实际路径**告知调用方（如 novel-to-comic skill 需要知道实际路径来运行 pipeline）
- 禁止直接覆盖已有文件

## 默认配置

- 粒度：**默认 high**（未指定 granularity 一律按 high 执行）
- 画师标签：`@rosumerii`
- 作品系列默认权重（当前主要支持 Genshin Impact）：`(Genshin Impact:1.2)`
- canvas 默认：`{"width": 1152, "height": 1536}`
- 服务端口：ComfyUI 8188, TTS 9880, Comic Server 8012

## 注意事项（切片必读）

1. 过渡/纯氛围场景可合并，但任何含对话或关键情节的片段不得省略
2. **多角色同框时，服装与动作都必须点名归属角色**：`nilou's dress`、`chiori licking aether's neck`，禁止无主语的服装描述（张冠李戴）或 generic 角色指代（见 4b）
