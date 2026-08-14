# -*- coding: utf-8 -*-
"""LongCat-AudioDIT-TTS 语音克隆客户端（v2 声音后端）。

通过 ComfyUI API 提交 YZ金鱼-LongCat-AudioDIT-TTS 工作流完成零样本语音克隆：
  参考音频(3-15s) -> Qwen3-ASR 自动转写文本 -> LongCat AudioDiT 克隆合成 -> mp3。
与 v1 TTSClient 保持同名接口（synthesize / ensure_gpt / ensure_sovits / check_server），
方便 pipeline 无痛切换后端。
"""
import os
import json
import uuid
import time
import urllib.request
import urllib.parse
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WORKFLOW_TEMPLATE = BASE / "image_gen" / "workflows" / "longcat-tts-api.json"


def _load_paths():
    """从 config/paths.json 读取本地路径配置；缺失时回退到环境变量/默认值。"""
    try:
        with open(BASE / "config" / "paths.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


PATHS = _load_paths()
_comfy = PATHS.get("comfyui", {})
_gptsovits = PATHS.get("gptsovits", {})
COMFY_INPUT_DIR = Path(_comfy.get("input_dir", r"E:\AI\ComfyUI-aki-v3\ComfyUI\input"))
COMFY_OUTPUT_DIR = Path(_comfy.get("output_dir", r"E:\AI\ComfyUI-aki-v3\ComfyUI\output"))
VOICE_BASE = Path(_gptsovits.get("voice_dir", r"E:\AI\GPT-SoVITS-v2pro-20250604-nvidia50\voice"))
AUDIODIT_DIR = Path(_comfy.get("models_dir", r"E:\AI\ComfyUI-aki-v3\ComfyUI\models")) / "audiodit"
QWEN_ASR_DIR = Path(_comfy.get("models_dir", r"E:\AI\ComfyUI-aki-v3\ComfyUI\models")) / "Qwen3-ASR"


class LongCatClient:
    def __init__(self, server_url='http://127.0.0.1:8188', settings=None):
        self.server_url = server_url.rstrip('/')
        self.settings = settings or {}
        self.output_dir = Path(self.settings.get("output_dir", "output"))
        # 参考音频目录（优先项目 voice 目录，兼容 settings 的 ref_audio 相对路径）
        self.voice_base = VOICE_BASE
        # 可选后端：模型缺失时给出明确提示，不阻塞默认(gptsovits)流程
        self.models_ok = self.check_models()

    def check_models(self):
        """LongCat 为可选后端：两个模型（LongCat-AudioDiT + Qwen3-ASR）缺任一即不可用，
        返回 False 并给出安装指引；默认 gptsovits 后端不依赖它们。"""
        audiodit = list(AUDIODIT_DIR.rglob("model.safetensors"))
        qwen = any(QWEN_ASR_DIR.rglob("*"))
        if audiodit and qwen:
            return True
        print("[LongCat] 可选语音后端模型未安装：需要 LongCat-AudioDiT 与 Qwen3-ASR")
        print("[LongCat]   下载链接见 README「模型下载」；或改用默认后端 --tts-backend gptsovits")
        return False

    # ---- 兼容 v1 接口 ----
    def ensure_gpt(self, weights_path, retries=3, pause=2.0):
        return True

    def ensure_sovits(self, weights_path, retries=3, pause=2.0):
        return True

    def check_server(self):
        try:
            urllib.request.urlopen(f'{self.server_url}/system_stats', timeout=5)
            return True
        except Exception:
            return False

    # ---- 工作流提交 ----
    def _prepare_ref_audio(self, ref_audio_path):
        """把参考音频复制到 ComfyUI input 目录，返回文件名。"""
        ref_audio_path = str(ref_audio_path)
        # 兼容三种路径：绝对路径 / 相对 voice_base 的 voice/xxx/... / 仅角色名
        p = Path(ref_audio_path)
        if p.exists():
            pass
        else:
            alt = self.voice_base / ref_audio_path
            if not alt.exists() and ref_audio_path.startswith("voice/"):
                # 去掉重复的 voice 前缀
                alt = self.voice_base / ref_audio_path[len("voice/"):]
            if not alt.exists() and "/" not in ref_audio_path and "\\" not in ref_audio_path:
                # 裸角色名：按 角色/ref.wav 找
                import glob
                cands = list(Path(self.voice_base).glob(f"{ref_audio_path}/*.wav"))
                if cands:
                    alt = cands[0]
            if alt.exists():
                p = alt
            else:
                raise FileNotFoundError(f"ref audio not found: {ref_audio_path}")
        name = f"longcat_ref_{uuid.uuid4().hex[:8]}{p.suffix}"
        dest = COMFY_INPUT_DIR / name
        import shutil
        shutil.copy2(p, dest)
        return name

    def _submit(self, text, ref_audio_name, prefix):
        with open(WORKFLOW_TEMPLATE, 'r', encoding='utf-8') as f:
            wf = json.load(f)
        wf["9"]["inputs"]["prompt"] = text
        wf["10"]["inputs"]["audio"] = ref_audio_name
        wf["11"]["inputs"]["filename_prefix"] = prefix
        data = json.dumps({"prompt": wf}).encode('utf-8')
        req = urllib.request.Request(f'{self.server_url}/prompt', data=data,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def _wait_result(self, prompt_id, timeout=600):
        """轮询 /history 直到完成，返回输出文件列表。"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                with urllib.request.urlopen(f'{self.server_url}/history/{prompt_id}', timeout=30) as resp:
                    hist = json.loads(resp.read().decode('utf-8'))
            except Exception:
                time.sleep(2)
                continue
            if prompt_id in hist and hist[prompt_id].get("status", {}).get("completed"):
                outputs = hist[prompt_id].get("outputs", {})
                files = []
                for node_out in outputs.values():
                    for img in node_out.get("audio", []):
                        files.append(img.get("filename"))
                return files
            if prompt_id in hist and hist[prompt_id].get("status", {}).get("status_str") == "error":
                msgs = hist[prompt_id].get("status", {}).get("messages", [])
                print(f"[LongCat] prompt {prompt_id} ERROR: {msgs}")
                return None
            time.sleep(3)
        print(f"[LongCat] prompt {prompt_id} timeout")
        return None

    def _download_output(self, filename, save_path):
        url = f'{self.server_url}/view?filename={urllib.parse.quote(filename)}&type=output&subfolder='
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, 'wb') as f:
            f.write(data)
        return str(save_path)

    # ---- 主入口 ----
    def synthesize(self, text, ref_audio_path, ref_text='', character_name='',
                   scene_id=1, output_subdir='', speaker=''):
        """克隆合成一句台词。返回 mp3 路径。"""
        if not text.strip():
            return None
        if not self.models_ok:
            return None
        ref_name = self._prepare_ref_audio(ref_audio_path)
        prefix = f'longcat_{scene_id:03d}_{uuid.uuid4().hex[:6]}'
        try:
            result = self._submit(text, ref_name, prefix)
        except Exception as e:
            print(f'[LongCat] submit failed: {e}')
            return None
        if not result or 'prompt_id' not in result:
            print(f'[LongCat] no prompt_id: {result}')
            return None
        prompt_id = result['prompt_id']
        print(f'[LongCat] Scene {scene_id}: submitted ({prompt_id}) text="{text[:20]}..."')
        files = self._wait_result(prompt_id)
        if not files:
            return None
        out_dir = self.output_dir / output_subdir / 'audio'
        os.makedirs(out_dir, exist_ok=True)
        if speaker:
            scene_no = scene_id // 100
            line_no = scene_id % 100
            save_path = out_dir / f'{speaker}_{scene_no:03d}_{line_no:02d}.mp3'
        else:
            save_path = out_dir / f'scene_{scene_id:03d}.mp3'
        try:
            saved = self._download_output(files[0], save_path)
            print(f'[LongCat] Scene {scene_id}: saved to {saved}')
            return saved
        except Exception as e:
            print(f'[LongCat] download failed: {e}')
            return None

    def release(self, ref_audio_path=None, timeout=300):
        """提交一条 keep_model_loaded=false 的收尾请求，触发 LongCat 节点卸载模型释放显存。

        LongCat 节点在 finally 里按 keep_model_loaded 决定是否 unload_model()；
        VBAR 环境下 CPU offload 会被跳过（日志 "skipping manual CPU offload"），
        只能靠这条假提示词走 finally 的完整卸载路径。
        卸载后再提交一条空任务驱逐 ComfyUI 节点输出缓存，连 Qwen3-ASR(~4GB) 一并释放。
        """
        if not self.models_ok:
            return False
        try:
            with open(WORKFLOW_TEMPLATE, 'r', encoding='utf-8') as f:
                wf = json.load(f)
            for nid, node in wf.items():
                inputs = node.get("inputs", {})
                if "keep_model_loaded" in inputs:
                    inputs["keep_model_loaded"] = False
            wf["9"]["inputs"]["prompt"] = "。"
            if ref_audio_path:
                ref_name = self._prepare_ref_audio(ref_audio_path)
            else:
                cands = sorted(Path(self.voice_base).glob("*/*.wav"))
                if not cands:
                    print('[LongCat] release: no ref audio found, skip')
                    return False
                ref_name = self._prepare_ref_audio(str(cands[0]))
            wf["10"]["inputs"]["audio"] = ref_name
            wf["11"]["inputs"]["filename_prefix"] = f'longcat_release_{uuid.uuid4().hex[:6]}'
            data = json.dumps({"prompt": wf}).encode('utf-8')
            req = urllib.request.Request(f'{self.server_url}/prompt', data=data,
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode('utf-8'))
            if 'prompt_id' not in result:
                print(f'[LongCat] release submit failed: {result}')
                return False
            self._wait_result(result['prompt_id'], timeout=timeout)
            print('[LongCat] release prompt done: model unloaded, VRAM freed')
            self._evict_output_cache()
            return True
        except Exception as e:
            print(f'[LongCat] release failed: {e}')
            return False

    def _evict_output_cache(self):
        """提交一条轻量任务驱逐 ComfyUI 节点输出缓存，释放 Qwen3-ASR 等驻留模型。

        新 prompt 一旦开始执行，ComfyUI 就会丢弃上一轮的节点输出缓存，
        被缓存引用的 ASR 模型随之被回收，soft_empty_cache 清空显存。
        （VAEDecode 因通道数不匹配会报错，但驱逐动作已发生，无需成功。）
        """
        try:
            wf = {
                "1": {"class_type": "EmptyLatentImage", "inputs": {"width": 64, "height": 64, "batch_size": 1}},
                "2": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
                "3": {"class_type": "VAEDecode", "inputs": {"samples": ["1", 0], "vae": ["2", 0]}},
                "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "longcat_evict"}},
            }
            data = json.dumps({"prompt": wf}).encode('utf-8')
            req = urllib.request.Request(f'{self.server_url}/prompt', data=data,
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode('utf-8'))
            if 'prompt_id' in result:
                self._wait_result(result['prompt_id'], timeout=120)
            print('[LongCat] output cache evicted, ASR VRAM released')
        except Exception as e:
            print(f'[LongCat] evict failed: {e}')
