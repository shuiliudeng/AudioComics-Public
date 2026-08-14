import json, os, time, uuid, re
import urllib.request, urllib.parse
from pathlib import Path

WORKFLOWS_DIR = Path(__file__).resolve().parent / "workflows"

class ComfyUIClient:
    def __init__(self, server_url='http://127.0.0.1:8188', settings=None):
        self.server_url = server_url.rstrip('/')
        self.settings = settings or {}
        self.output_dir = Path(self.settings.get('output_dir', 'v1/output'))
        self._manifest = None
        self._current_id = self.settings.get("workflow", "anima-sdxl-direct")

    @property
    def manifest(self):
        if self._manifest is None:
            self.reload_manifest()
        return self._manifest

    def reload_manifest(self):
        path = WORKFLOWS_DIR / "manifest.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                self._manifest = json.load(f)
        else:
            self._manifest = {"current": "anima-sdxl-direct", "workflows": {}}

    def list_workflows(self):
        return {k: {"name": v["name"], "description": v.get("description","")}
                for k, v in self.manifest["workflows"].items()}

    def set_workflow(self, workflow_id):
        if workflow_id not in self.manifest["workflows"]:
            raise ValueError(f"Unknown workflow: {workflow_id}")
        self._current_id = workflow_id
        self.manifest["current"] = workflow_id
        man_path = WORKFLOWS_DIR / "manifest.json"
        with open(man_path, "w", encoding="utf-8") as f:
            json.dump(self._manifest, f, ensure_ascii=False, indent=2)

    @property
    def current_workflow_info(self):
        return self.manifest["workflows"].get(self._current_id, {})

    def _load_template(self):
        info = self.current_workflow_info
        wf_path = WORKFLOWS_DIR / info.get("file", "sdxl-basic.json")
        with open(wf_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _inject_prompt(self, wf, prompt_pos, prompt_neg, width, height, seed=None, prefix='comic'):
        s = json.dumps(wf)
        s = s.replace('__PROMPT_POSITIVE__', json.dumps(prompt_pos)[1:-1])
        s = s.replace('__PROMPT_NEGATIVE__', json.dumps(prompt_neg)[1:-1])
        s = s.replace('__WIDTH__', str(width))
        s = s.replace('__HEIGHT__', str(height))
        s = s.replace('__SEED__', str(seed if seed is not None else uuid.uuid4().int & ((1<<32)-1)))
        s = s.replace('__STEPS__', str(self.settings.get('steps', 30)))
        s = s.replace('__CFG__', str(self.settings.get('cfg', 7)))
        s = s.replace('__SAMPLER__', str(self.settings.get('sampler_name', 'euler')))
        s = s.replace('__SCHEDULER__', str(self.settings.get('scheduler', 'normal')))
        model_name = self.current_workflow_info.get("model") or self.settings.get("model_name", "waiIllustriousSDXL_v170.safetensors")
        s = s.replace('__MODEL_NAME__', model_name)
        s = s.replace('__PREFIX__', prefix)
        # 动态系列/画师标签：从切片提示词自动提取（换游戏/换画师自动跟随，无需改工作流）
        m_series = re.search(r"\([^()]*?:\s*1\.\d+\)", prompt_pos)
        m_artist = re.search(r"@[\w.-]+", prompt_pos)
        s = s.replace('__SERIES_TAG__', m_series.group(0) if m_series else "")
        s = s.replace('__ARTIST_TAG__', m_artist.group(0) if m_artist else "")
        return json.loads(s)

    def _api_post(self, endpoint, data):
        url = f'{self.server_url}{endpoint}'
        req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            print(f'[ComfyUI] POST {endpoint} failed: {e}')
            return None

    def _api_get(self, endpoint):
        url = f'{self.server_url}{endpoint}'
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            print(f'[ComfyUI] GET {endpoint} failed: {e}')
            return None

    def _inject_loras(self, wf, loras):
        """动态注入角色 LoRA 链（0..n 个），插在 KSampler 的 model 链末端
        （即最后一个 LoraLoaderModelOnly 之后、ModelSampling/KSampler 之前）。
        无 lora 时原样返回，不新建工作流文件。"""
        if not loras:
            return wf
        wf = json.loads(json.dumps(wf))
        # 找到 KSampler 及其 model 输入节点 M（如 ModelSamplingAuraFlow）
        ks_id = None
        m_id = None
        for nid, node in wf.items():
            if node.get("class_type") == "KSampler":
                ks_id = nid
                ref = node["inputs"].get("model")
                m_id = ref[0] if ref else None
                break
        if ks_id is None or m_id is None or m_id not in wf:
            print("[ComfyUI] WARN: no KSampler model chain, cannot inject loras")
            return wf
        # M 的 model 输入指向链末端 L（最后一个 LoraLoaderModelOnly 或 UNETLoader）
        l_id = m_id
        ref = wf[m_id]["inputs"].get("model")
        if ref and ref[0] in wf:
            l_id = ref[0]
        # 逐级插入：第一个 lora 接在 l_id 后，后续串联，M.model 指向链末端
        max_id = max(int(k) for k in wf.keys())
        prev_out = l_id
        for i, lora in enumerate(loras):
            max_id += 1
            nid = str(max_id)
            wf[nid] = {
                "inputs": {
                    "lora_name": lora.get("name", ""),
                    "strength_model": float(lora.get("weight", 0.8)),
                    "model": [prev_out, 0],
                },
                "class_type": "LoraLoaderModelOnly",
                "_meta": {"title": f"LoRA注入{i + 1}"},
            }
            prev_out = nid
        wf[m_id]["inputs"]["model"] = [prev_out, 0]
        return wf

    def generate(self, prompt_positive, prompt_negative, width=1152, height=1536,
                 scene_id=1, seed=None, output_subdir='', force=False, loras=None):
        out_dir = self.output_dir / output_subdir / 'images'
        save_path = out_dir / f'scene_{scene_id:03d}.png'
        if not force and save_path.exists():
            print(f'[ComfyUI] Scene {scene_id}: already exists, skip')
            return str(save_path)

        workflow = self._load_template()
        scene_prefix = f'comic_scene_{scene_id:03d}'
        workflow = self._inject_prompt(workflow, prompt_positive, prompt_negative,
                                       width, height, seed, scene_prefix)
        workflow = self._inject_loras(workflow, loras or [])

        result = self._api_post('/prompt', {'prompt': workflow})
        if not result or 'prompt_id' not in result:
            print(f'[ComfyUI] Scene {scene_id}: failed to submit')
            return None

        prompt_id = result['prompt_id']
        print(f'[ComfyUI] Scene {scene_id}: submitted (prompt_id={prompt_id}) [{self._current_id}]')

        while True:
            history = self._api_get(f'/history/{prompt_id}')
            if history and prompt_id in history:
                outputs = history[prompt_id].get('outputs', {})
                for node_id, node_out in outputs.items():
                    for img_data in node_out.get('images', []):
                        filename = img_data.get('filename', '')
                        if filename:
                            out_dir = self.output_dir / output_subdir / 'images'
                            os.makedirs(out_dir, exist_ok=True)
                            save_path = out_dir / f'scene_{scene_id:03d}.png'
                            img_url = f'{self.server_url}/view?filename={urllib.parse.quote(filename)}&type=output'
                            try:
                                with urllib.request.urlopen(img_url, timeout=60) as img_resp:
                                    with open(save_path, 'wb') as sf:
                                        sf.write(img_resp.read())
                                print(f'[ComfyUI] Scene {scene_id}: saved to {save_path}')
                                return str(save_path)
                            except Exception as e:
                                print(f'[ComfyUI] Scene {scene_id}: download failed: {e}')
                                return None
                time.sleep(3)
            else:
                time.sleep(2)

    def check_server(self):
        result = self._api_get('/queue')
        return result is not None
