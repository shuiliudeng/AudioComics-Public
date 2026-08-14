#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified Comic Server - FastAPI"""
import json, os, sys, shutil, wave, time, socket, uuid, re
import urllib.request, urllib.parse, subprocess, threading
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse
import uvicorn
sys.path.insert(0, str(Path(__file__).resolve().parent))
BASE = Path(__file__).resolve().parent
import logging
logger = logging.getLogger("comic")
logger.setLevel(logging.INFO)
log_dir = Path(__file__).resolve().parent / "logs"
log_dir.mkdir(exist_ok=True)
fh = logging.FileHandler(str(log_dir / "comic_server.log"), mode="a", encoding="utf-8")
fh.setFormatter(logging.Formatter("{asctime} {message}", style="{", datefmt="%H:%M:%S"))
logger.addHandler(fh)
for hn in ["uvicorn", "uvicorn.error", "uvicorn.access"]:
    logging.getLogger(hn).addHandler(fh)
def plog(*args):
    logger.info(" ".join(str(a) for a in args))
print = plog

EXAMPLES_DIR = BASE / "examples"
OUTPUT_DIR = BASE / "output"
CONFIG_DIR = BASE / "config"
WORKFLOWS_DIR = BASE / "image_gen" / "workflows"
FRONTEND_DIR = BASE / "frontend"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
PORT = 8012
_settings = None
def load_settings():
    global _settings
    if _settings is None:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            _settings = json.load(f)
    return _settings
def save_settings():
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(_settings, f, ensure_ascii=False, indent=2)
def _build_char_name_map(char_map):
    """桥接 character_model_map(英文cid) 与 characters.json(cid/name/aliases)。
    返回 (alias_pool, display_names): alias_pool[k] = 角色 k 所有可称呼名(英文cid/中文名/别名),
    display_names[k] = 中文显示名。characters.json 缺失时仅剩 key 本身。"""
    alias_pool = {k: {k} for k in char_map}
    display_names = {k: k for k in char_map}
    try:
        with open(CONFIG_DIR / "characters.json", "r", encoding="utf-8") as f:
            chars = json.load(f)
    except Exception:
        return alias_pool, display_names
    links = []
    for cid, cdata in chars.items():
        names = {cid}
        if cdata.get("name"): names.add(cdata["name"])
        for a in cdata.get("aliases", []):
            if a: names.add(str(a))
        links.append((names, cdata.get("name") or cid))
    for k in char_map:
        kl = k.lower()
        for names, disp in links:
            if any(str(n).lower() == kl for n in names):
                alias_pool[k].update(names)
                display_names[k] = disp
                break
    return alias_pool, display_names
def check_port(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        try: s.connect((host, port)); return True
        except: return False
def get_service_status():
    return {"comfyui": check_port("127.0.0.1", 8188), "tts": check_port("127.0.0.1", 9880), "server": True}
def list_projects():
    projects = []
    if not OUTPUT_DIR.exists(): return projects
    for d in sorted(OUTPUT_DIR.iterdir()):
        if not d.is_dir(): continue
        mf = d / "manifest.json"
        if mf.exists():
            try:
                with open(mf, "r", encoding="utf-8") as f:
                    m = json.load(f)
                projects.append({"name": d.name, "scenes": len(m.get("scenes", [])), "has_images": (d / "images").exists(), "has_audio": (d / "audio").exists()})
            except: pass
    return projects
@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"[server] http://127.0.0.1:{PORT}")
    yield
app = FastAPI(title="Comic Server", lifespan=lifespan)
@app.get("/api/status")
async def api_status():
    return get_service_status()
@app.get("/api/projects")
async def api_projects():
    return {"projects": list_projects()}
@app.get("/api/workflows")
async def api_workflows():
    wfs = {}
    cur = load_settings().get("comfyui", {}).get("workflow", "")
    if WORKFLOWS_DIR.exists():
        for fn in sorted(os.listdir(WORKFLOWS_DIR)):
            if fn.endswith(".json") and fn not in ("manifest.json",):
                wfs[fn.replace(".json", "")] = {"name": fn.replace(".json", "")}
    return {"workflows": wfs, "current": cur}
@app.post("/api/set-workflow")
async def api_set_workflow(data: dict):
    wid = data.get("workflowId", "")
    if not wid: raise HTTPException(400, "missing workflowId")
    s = load_settings()
    s["comfyui"]["workflow"] = wid
    save_settings()
    return {"success": True}
def auto_map_characters(settings):
    voice_base = str(Path(settings["tts"]["gpt_weights_dir"]).parent / "voice")
    from audio_gen.model_indexer import ModelIndexer
    indexer = ModelIndexer(settings["tts"]["gpt_weights_dir"], settings["tts"]["sovits_weights_dir"])
    all_models = indexer.list_all_characters()
    cmap = settings.get("character_model_map", {})
    alias_map_local, name_to_id_local = load_characters_config()
    for char_name, model_info in all_models.items():
        canonical = alias_map_local.get(char_name, char_name) if char_name in alias_map_local else char_name
        gpt = model_info.get("gpt") or ""
        sovits = model_info.get("sovits") or ""
        if canonical in cmap:
            ng = os.path.basename(gpt) if gpt else ""
            og = os.path.basename(cmap[canonical].get("gpt", "")) if cmap[canonical].get("gpt") else ""
            if ng and ng != og:
                cmap[canonical]["gpt"] = gpt
            ns = os.path.basename(sovits) if sovits else ""
            osv = os.path.basename(cmap[canonical].get("sovits", "")) if cmap[canonical].get("sovits") else ""
            if ns and ns != osv:
                cmap[canonical]["sovits"] = sovits
            if not cmap[canonical].get("ref_audio"):
                for vdir in [canonical, char_name]:
                    ra, rt = find_ref_audio(voice_base, vdir)
                    if ra:
                        cmap[canonical]["ref_audio"] = ra
                        cmap[canonical]["ref_text"] = rt
                        break
        else:
            dirs_to_try = [char_name]
            if char_name in alias_map_local:
                cid = alias_map_local[char_name]
                with open(CONFIG_DIR / "characters.json", "r", encoding="utf-8") as f2:
                    cc = json.load(f2)
                if cid in cc:
                    dirs_to_try.append(cc[cid].get("name", ""))
            ra, rt = None, ""
            for vdir in dirs_to_try:
                ra, rt = find_ref_audio(voice_base, vdir)
                if ra: break
            cmap[canonical] = {
                "gpt": gpt, "sovits": sovits,
                "ref_audio": ra or "", "ref_text": rt or "",
            }
    tts_root = str(Path(settings["tts"]["gpt_weights_dir"]).parent).replace("\\", "/")
    for cname in list(cmap.keys()):
        entry = cmap[cname]
        for k in ("gpt", "sovits", "ref_audio"):
            v = entry.get(k, "").replace("\\", "/")
            if v and tts_root in v:
                entry[k] = v.replace(tts_root + "/", "")
    settings["character_model_map"] = cmap
    return cmap

def resolve_sliced_path(manifest):
    # 根治: manifest 若记录了实际切片文件(slice_path), 直接用它——读哪存哪, 不再靠名字猜。
    sp = manifest.get("slice_path") or ""
    if sp:
        p = Path(sp)
        if p.exists():
            return p
        p2 = EXAMPLES_DIR / Path(sp).name
        if p2.exists():
            return p2
    # 兼容旧项目: 按命名约定 low/medium/high 三档都找; 再兜底任意 _sliced_*.json
    project = manifest.get("project", "")
    if not project:
        return None
    for g in ("low", "medium", "high"):
        p = EXAMPLES_DIR / f"{project}_sliced_{g}.json"
        if p.exists():
            return p
    for p in [EXAMPLES_DIR / f"{project}.json", EXAMPLES_DIR / f"{project}_sliced.json"]:
        if p.exists():
            return p
    import glob as _glob
    matches = sorted(_glob.glob(str(EXAMPLES_DIR / f"{project}_sliced_*.json")))
    if matches:
        return Path(matches[0])
    return None
def load_sliced_data(manifest):
    sp = resolve_sliced_path(manifest)
    if sp and sp.exists():
        with open(sp, "r", encoding="utf-8") as f: return json.load(f)
    return None
def save_sliced_data(manifest, data):
    sp = resolve_sliced_path(manifest)
    if sp:
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    return False
@app.post("/api/save-edits")
async def api_save_edits(data: dict):
    scene_id = data.get("sceneId")
    img_prompt = data.get("imgPrompt", "")
    tts_texts = data.get("ttsTexts", [])
    project_name = data.get("projectName", "")
    if not project_name: raise HTTPException(400, "missing projectName")
    mf_path = OUTPUT_DIR / project_name / "manifest.json"
    if not mf_path.exists(): raise HTTPException(404, "project not found")
    with open(mf_path, "r", encoding="utf-8") as f: manifest = json.load(f)
    sliced_data = load_sliced_data(manifest)
    if not sliced_data: raise HTTPException(400, "sliced data not found")
    for scene in sliced_data.get("scenes", []):
        if scene.get("id") == scene_id:
            if img_prompt: scene["img_prompt_en"] = img_prompt
            if tts_texts:
                dlg = scene.get("dialogue", [])
                for i, t in enumerate(tts_texts):
                    if i < len(dlg):
                        m = re.match(r"^[^:：]{1,12}[：:]\s*(.+)$", t)
                        dlg[i]["text_cn"] = m.group(1) if m else t
            break
    ok = save_sliced_data(manifest, sliced_data)
    if ok and mf_path.exists():
        with open(mf_path, "r", encoding="utf-8") as f: manifest = json.load(f)
        for scene in sliced_data.get("scenes", []):
            if scene.get("id") == scene_id:
                ms = next((s for s in manifest.get("scenes", []) if s.get("id") == scene_id), None)
                if ms:
                    if img_prompt: ms["img_prompt"] = img_prompt
                    if tts_texts:
                        dlg = ms.get("dialogue", [])
                        for i, t in enumerate(tts_texts):
                            if i < len(dlg):
                                m = re.match(r"^[^:：]{1,12}[：:]\s*(.+)$", t)
                                dlg[i]["text_cn"] = m.group(1) if m else t
        with open(mf_path, "w", encoding="utf-8") as f: json.dump(manifest, f, ensure_ascii=False, indent=2)
    return {"success": ok}
@app.post("/api/regenerate-image")
def api_regen_image(data: dict):
    status = get_service_status()
    if not status["comfyui"]: raise HTTPException(503, "ComfyUI not running")
    scene_id = data.get("sceneId")
    prompt = data.get("imgPrompt", "")
    project_name = data.get("projectName", "")
    if not scene_id or not prompt or not project_name: raise HTTPException(400, "missing params")
    settings = load_settings()
    from image_gen.comfyui_client import ComfyUIClient
    c = ComfyUIClient(settings["comfyui"]["server_url"], settings["comfyui"])
    c.output_dir = Path(settings["comfyui"]["output_dir"])
    path = c.generate(prompt_positive=prompt, prompt_negative="", width=1152, height=1536, scene_id=scene_id, output_subdir=project_name, force=True)
    if path: return {"success": True, "imagePath": f"images/scene_{scene_id:03d}.png"}
    raise HTTPException(500, "ComfyUI generation failed")
@app.post("/api/regenerate-tts")
def api_regen_tts(data: dict):
    status = get_service_status()
    if not status["tts"]: raise HTTPException(503, "TTS not running")
    scene_id = data.get("sceneId")
    tts_text = data.get("ttsText", "")
    project_name = data.get("projectName", "")
    if not scene_id or not tts_text or not project_name: raise HTTPException(400, "missing params")
    settings = load_settings()
    mf_path = OUTPUT_DIR / project_name / "manifest.json"
    if not mf_path.exists(): raise HTTPException(404, "project not found")
    with open(mf_path, "r", encoding="utf-8") as f: manifest = json.load(f)
    sliced_data = load_sliced_data(manifest)
    scene_slice = next((s for s in sliced_data.get("scenes", []) if s.get("id") == scene_id), None) if sliced_data else None
    dialogue = scene_slice.get("dialogue", []) if scene_slice else []
    lines = [l.strip() for l in tts_text.split("\n") if l.strip()]
    out_dir = OUTPUT_DIR / project_name
    audio_segs = []
    char_map = settings.get("character_model_map", {})
    # 角色别名表: characters.json 的 cid/中文名/别名 全部桥接到 char_map 的 key(英文cid)
    alias_pool, _ = _build_char_name_map(char_map)

    def resolve_char(speaker):
        if not speaker: return None
        s = str(speaker).strip()
        if not s: return None
        sl = s.lower()
        # 1. 精确匹配(忽略大小写): char_map key 或角色的 cid/中文名/别名
        for k, v in char_map.items():
            if k.lower() == sl: return v
        for k, v in char_map.items():
            for n in alias_pool.get(k, ()):
                if str(n).lower() == sl: return v
        # 2. 子串模糊匹配(兼容旧数据)
        for k, v in char_map.items():
            if k.lower() in sl or sl in k.lower(): return v
        return None

    def strip_label(t):
        """去掉行首 '角色名:' 前缀（若存在）"""
        m = re.match(r"^([^:：]{1,12})[：:]\s*(.+)$", t)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return "", t

    speakers = []
    for i, line in enumerate(lines):
        spk_label, line = strip_label(line)
        if spk_label:
            speaker = spk_label
        else:
            speaker = dialogue[i].get("speaker_id", "") if i < len(dialogue) else (dialogue[0].get("speaker_id", "") if dialogue else "")
        speakers.append(speaker)
        ci = resolve_char(speaker)
        if ci is None:
            raise HTTPException(400, f"unknown speaker '{speaker}' (scene {scene_id} line {i+1}): 检查切片 speaker 或 characters.json 别名")
        ra = ci.get("ref_audio", "") or ""
        rt = ci.get("ref_text", "") or ""
        if not ra:
            vb = str(Path(settings["tts"]["gpt_weights_dir"]).parent / "voice")
            for r, d, files in os.walk(vb):
                for fn in sorted(files):
                    if fn.endswith(".wav"):
                        lp = os.path.splitext(os.path.join(r, fn))[0] + ".lab"
                        lt = ""
                        if os.path.exists(lp):
                            with open(lp, "r", encoding="utf-8") as f:
                                lt = f.read().strip()
                        if lt:
                            ra = os.path.join(r, fn)
                            rt = lt
                        break
                if ra: break
        gp, sp = (ci or {}).get("gpt", ""), (ci or {}).get("sovits", "")
        if gp:
            try: urllib.request.urlopen(f"http://127.0.0.1:9880/set_gpt_weights?weights_path={urllib.parse.quote(gp)}", timeout=10)
            except: pass
        if sp:
            try: urllib.request.urlopen(f"http://127.0.0.1:9880/set_sovits_weights?weights_path={urllib.parse.quote(sp)}", timeout=10)
            except: pass
        params = {"text": line, "text_lang": "zh", "ref_audio_path": ra, "prompt_text": rt, "prompt_lang": "zh", "how_to_cut": "cut5", "top_k": "15", "top_p": "1.0", "temperature": "1.0", "speed": "0.9", "pause_second": "0.3", "sample_steps": "16"}
        try:
            url = "http://127.0.0.1:9880/tts?" + urllib.parse.urlencode(params)
            resp = urllib.request.urlopen(url, timeout=60)
            seg = str(out_dir / "audio" / f"{speaker}_{scene_id:03d}_{i}.wav")
            os.makedirs(os.path.dirname(seg), exist_ok=True)
            with open(seg, "wb") as f: f.write(resp.read())
            audio_segs.append(seg)
        except Exception as e:
            print(f"[tts] scene {scene_id} line {i}: {e}")
    first_speaker = next((s for s in speakers if s), "unknown")
    combined = str(out_dir / "audio" / f"{first_speaker}_{scene_id:03d}.wav")
    if audio_segs:
        try:
            frames, params_wav = [], None
            for s in audio_segs:
                with wave.open(s, "rb") as w:
                    if params_wav is None: params_wav = w.getparams()
                    frames.append(w.readframes(w.getnframes()))
            if params_wav:
                with wave.open(combined, "wb") as out:
                    out.setparams(params_wav)
                    for fbytes in frames: out.writeframes(fbytes)
            for s in audio_segs:
                try: os.remove(s)
                except: pass
        except:
            if audio_segs: shutil.copy2(audio_segs[0], combined)
    else:
        raise HTTPException(500, "TTS generation failed")
    # 持久化 manifest 中的 audio 路径
    new_audio_path = f"audio/{first_speaker}_{scene_id:03d}.wav"
    try:
        for sc in manifest.get("scenes", []):
            if sc.get("id") == scene_id:
                sc["audio"] = new_audio_path
                break
        with open(mf_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return {"success": True, "audioPath": new_audio_path}
@app.post("/api/submit-job")
async def api_submit_job(file: UploadFile = File(...), project_name: str = Form(...), workflow_id: str = Form("anima-sdxl-direct")):
    os.makedirs(EXAMPLES_DIR, exist_ok=True)
    # 保留上传文件原名(如 xxx_sliced_high.json), 粒度由 pipeline 从文件名推导, manifest 记录实际路径
    orig = Path(file.filename or "").name
    if not orig.lower().endswith(".json"):
        orig = f"{project_name}_sliced_medium.json"
    slice_path = EXAMPLES_DIR / orig
    content = await file.read()
    with open(slice_path, "wb") as f: f.write(content)
    try: data = json.loads(content)
    except: raise HTTPException(400, "invalid JSON")
    if not data.get("scenes"): raise HTTPException(400, "no scenes")
    if not data.get("project"):
        data["project"] = project_name
        with open(slice_path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[job] {project_name} ({len(data['scenes'])} scenes)")
    status = get_service_status()
    if not status["comfyui"]: return {"success": False, "warning": "ComfyUI offline"}
    if not status["tts"]: return {"success": False, "warning": "TTS offline"}
    def run_pipeline():
        try:
            from orchestrator.pipeline import main as pipeline_main
            sys.argv = ["pipeline", "--scenes", str(slice_path), "--output", project_name, "--workflow", workflow_id]
            try: pipeline_main()
            except SystemExit: pass
        except Exception as e: print(f"[job] Error: {e}")
    threading.Thread(target=run_pipeline, daemon=True).start()
    return {"success": True, "message": "Job submitted", "project_name": project_name}
# --- Frontend Routes ---
@app.get("/")
async def index():
    projects = list_projects()
    cards = ""
    for p in projects:
        badges = "&#x1f5bc;" if p["has_images"] else ""
        if p["has_audio"]: badges += " &#x1f50a;"
        cards += '<a href="/project/{name}" class="card"><div class="icon">{badges}</div><div class="name">{name}</div><div class="meta">{n} scenes</div></a>'.format(name=p["name"], badges=badges, n=p["scenes"])
    if not cards:
        cards = '<div class="empty">No projects yet.</div>'
    parts = []
    parts.append('<!DOCTYPE html><html lang=zh-CN><head><meta charset=UTF-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Comic Library</title><style>')
    parts.append('*{margin:0;padding:0;box-sizing:border-box}')
    parts.append('body{font-family:-apple-system,Microsoft YaHei,sans-serif;background:#1a1a2e;color:#eee}')
    parts.append('.header{background:linear-gradient(135deg,#16213e,#0f3460);padding:20px 32px}')
    parts.append('.header h1{font-size:20px}')
    parts.append('.status{display:flex;gap:16px;margin-top:8px;font-size:13px;color:#8899aa}')
    parts.append('.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}')
    parts.append('.dot.on{background:#4ade80}.dot.off{background:#666}')
    parts.append('.actions{padding:16px 32px}')
    parts.append('.actions button{padding:8px 16px;border:none;border-radius:6px;cursor:pointer;font-size:14px}')
    parts.append('.btn-new{background:#4f46e5;color:#fff}')
    parts.append('.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;padding:0 32px 32px}')
    parts.append('.card{background:#16213e;border-radius:8px;padding:16px;text-decoration:none;color:#eee;border:1px solid #2a2a4a}')
    parts.append('.card:hover{transform:translateY(-2px)}')
    parts.append('.icon{font-size:28px;margin-bottom:8px}')
    parts.append('.name{font-size:14px;font-weight:500}')
    parts.append('.meta{font-size:12px;color:#8899aa;margin-top:4px}')
    parts.append('.empty{padding:40px;text-align:center;color:#667}')
    parts.append('</style></head><body>')
    parts.append('<div class=header><h1>Comic Library</h1>')
    parts.append('<div class=status><span><span class="dot" id=dc></span>ComfyUI</span><span><span class="dot" id=dt></span>TTS</span></div></div>')
    parts.append('<div class=actions><button class=btn-new onclick="location=\'/create\'">+ New</button></div>')
    parts.append('<div class=grid>' + cards + '</div>')
    parts.append('<script>async function ck(){try{const r=await fetch(\'/api/status\');const s=await r.json();document.getElementById(\'dc\').className=\'dot \'+(s.comfyui?\'on\':\'off\');document.getElementById(\'dt\').className=\'dot \'+(s.tts?\'on\':\'off\')}catch(e){}}ck();setInterval(ck,10000)</script>')
    parts.append('</body></html>')
    return HTMLResponse("".join(parts))
@app.get("/create")
async def create_page():
    parts = []
    parts.append('<!DOCTYPE html><html lang=zh-CN><head><meta charset=UTF-8><meta name=viewport content="width=device-width,initial-scale=1"><title>New Project</title><style>')
    parts.append('*{margin:0;padding:0;box-sizing:border-box}')
    parts.append('body{font-family:-apple-system,Microsoft YaHei,sans-serif;background:#1a1a2e;color:#eee;padding:32px;max-width:700px;margin:0 auto}')
    parts.append('h1{font-size:20px;margin-bottom:8px}.back{color:#818cf8;text-decoration:none;font-size:14px}')
    parts.append('label{display:block;font-size:13px;color:#8899aa;margin:16px 0 4px}')
    parts.append('input[type=text],select{width:100%;padding:10px;border-radius:6px;border:1px solid #333;background:#0d1b2a;color:#eee;font-size:14px}')
    parts.append('.drop{border:2px dashed #444;border-radius:10px;padding:30px;text-align:center;cursor:pointer}')
    parts.append('.drop:hover{border-color:#818cf8}.drop p{font-size:14px;color:#8899aa}')
    parts.append('.btn{width:100%;padding:14px;background:#4f46e5;color:#fff;border:none;border-radius:8px;font-size:16px;cursor:pointer;margin-top:20px}')
    parts.append('.btn:disabled{background:#444}')
    parts.append('#st{margin-top:16px;padding:12px;border-radius:8px;font-size:14px}')
    parts.append('</style></head><body>')
    parts.append('<a href=/ class=back>&larr; Back</a><h1>New Project</h1>')
    parts.append('<p style="font-size:13px;color:#8899aa;margin-bottom:20px">Upload sliced JSON to generate a comic.</p>')
    parts.append('<label>Project Name</label><input type=text id=pn placeholder="project_name">')
    parts.append('<label>Sliced JSON</label><div class=drop id=drop onclick="document.getElementById(\'fi\').click()"><p>Click or drag JSON here</p><p id=fn style="font-size:12px;color:#666;margin-top:8px"></p></div>')
    parts.append('<input type=file id=fi accept=.json style=display:none>')
    parts.append('<label>Workflow</label><select id=wf></select>')
    parts.append('<button class=btn id=btn onclick=submit()>Submit</button><div id=st></div>')
    parts.append('<script>let sf=null;document.getElementById(\'fi\').onchange=e=>{const f=e.target.files[0];if(f&&f.name.endsWith(\'.json\')){sf=f;document.getElementById(\'fn\').textContent=\'OK \'+f.name}};document.getElementById(\'drop\').ondragover=e=>e.preventDefault();document.getElementById(\'drop\').ondrop=e=>{e.preventDefault();const f=e.dataTransfer.files[0];if(f&&f.name.endsWith(\'.json\')){sf=f;document.getElementById(\'fn\').textContent=\'OK \'+f.name}};async function loadWf(){const r=await fetch(\'/api/workflows\');const d=await r.json();const s=document.getElementById(\'wf\');s.innerHTML=\'\';for(const[k,v]of Object.entries(d.workflows||{})){const o=document.createElement(\'option\');o.value=k;o.textContent=v.name;if(k===d.current)o.selected=true;s.appendChild(o)}}loadWf();async function submit(){const st=document.getElementById(\'st\');const btn=document.getElementById(\'btn\');btn.disabled=true;st.textContent=\'Submitting...\';const pn=document.getElementById(\'pn\').value.trim();if(!pn||!sf){st.textContent=\'Error\';btn.disabled=false;return}const fd=new FormData();fd.append(\'file\',sf);fd.append(\'project_name\',pn);fd.append(\'workflow_id\',document.getElementById(\'wf\').value);try{const r=await fetch(\'/api/submit-job\',{method:\'POST\',body:fd});const d=await r.json();if(d.success){st.innerHTML=\'OK! <a href=/project/\'+pn+\'>View</a>\'}else{st.innerHTML=\'Warning: \'+(d.warning||\'\')}}catch(e){st.textContent=\'Error: \'+e.message};btn.disabled=false}</script>')
    parts.append('</body></html>')
    return HTMLResponse("".join(parts))
@app.get("/project/{project_name}")
async def project_viewer(project_name: str):
    project_dir = OUTPUT_DIR / project_name
    if not project_dir.exists():
        raise HTTPException(404, "project not found")
    mf_path = project_dir / "manifest.json"
    if not mf_path.exists():
        raise HTTPException(404, "manifest not found")
    with open(mf_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    scenes = manifest.get("scenes", [])
    status = get_service_status()
    scenes_json = json.dumps(scenes, ensure_ascii=False)
    status_json = json.dumps(status)
    spk_names = {}
    try:
        settings = load_settings()
        alias_pool, display_names = _build_char_name_map(settings.get("character_model_map", {}))
        for k, disp in display_names.items():
            spk_names[k] = disp
            for n in alias_pool.get(k, ()):
                spk_names[n] = disp
    except Exception:
        pass
    sn_json = json.dumps(spk_names, ensure_ascii=False)
    pn_json = json.dumps(project_name)
    html = []
    html.append('<!DOCTYPE html><html lang=zh-CN><head><meta charset=UTF-8><meta name=viewport content="width=device-width,initial-scale=1"><title>' + project_name + '</title><style>')
    html.append('*{margin:0;padding:0;box-sizing:border-box}')
    html.append('body{font-family:-apple-system,Microsoft YaHei,sans-serif;background:#1a1a2e;color:#eee}')
    html.append('.bar{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;background:#16213e}')
    html.append('.bar h2{font-size:16px}.back{color:#818cf8;text-decoration:none;font-size:13px}')
    html.append('#viewer{position:relative;width:100%;max-width:900px;margin:0 auto;aspect-ratio:3/4;max-height:85vh;background:#111;overflow:hidden}')
    html.append('#comic-image{width:100%;height:100%;object-fit:contain;cursor:pointer}')
    html.append('#narration{position:absolute;bottom:0;left:0;right:0;padding:16px;background:linear-gradient(transparent,rgba(0,0,0,.8));font-size:14px;line-height:1.6;transition:opacity .8s;cursor:pointer}')
    html.append('#narration.hidden{display:none}')
    html.append('#narration.faded{opacity:0.25}')
    html.append('.nav{position:absolute;top:0;bottom:0;width:15%;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:36px;color:rgba(255,255,255,.3);user-select:none}')
    html.append('.nav:hover{background:rgba(255,255,255,.08);color:rgba(255,255,255,.7)}')
    html.append('#nl{left:0}#nr{right:0}')
    html.append('#controls{display:flex;align-items:center;gap:12px;padding:10px 16px;flex-wrap:wrap;max-width:900px;margin:0 auto}')
    html.append('#counter{font-size:14px;color:#8899aa;min-width:50px}#title{flex:1;font-size:14px;color:#ccc}')
    html.append('#btns{display:flex;gap:6px}#btns button{padding:6px 12px;border:1px solid #444;background:#222;color:#eee;border-radius:6px;cursor:pointer;font-size:13px}')
    html.append('#btns button:hover{background:#333}#btns button.on{background:#4f46e5;border-color:#4f46e5}')
    html.append('#ep{position:fixed;top:0;right:0;bottom:0;width:380px;max-width:100vw;background:#16213e;padding:20px;overflow-y:auto;z-index:100;border-left:1px solid #2a2a4a}')
    html.append('#ep.hidden{display:none}#ep h3{font-size:15px;margin-bottom:12px}')
    html.append('#ep label{display:block;font-size:12px;color:#8899aa;margin:8px 0 4px}')
    html.append('#ep textarea,#ep select{width:100%;padding:8px;border-radius:6px;border:1px solid #333;background:#0d1b2a;color:#eee;font-size:13px}')
    html.append('#ep button{padding:8px 14px;border:none;border-radius:6px;cursor:pointer;font-size:13px}')
    html.append('#rgi,#rgt{background:#2563eb;color:#fff}#sv{background:#16a34a;color:#fff}#ec{background:#444;color:#eee}')
    html.append('#eb{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}#es{font-size:13px;margin-top:8px}')
    html.append('#pb{height:3px;background:#333;max-width:900px;margin:0 auto 4px}#pf{height:3px;width:0;background:#4f46e5}')
    html.append('@media(max-width:600px){#ep{width:100vw}#viewer{aspect-ratio:2/3}}')
    html.append('</style></head><body>')
    html.append('<div class=bar><h2>' + project_name + '</h2><a href=/ class=back>&larr; Back</a></div>')
    html.append('<div id=pb><div id=pf></div></div>')
    html.append('<div id=viewer><img id=comic-image src="" alt=""><div id=narration class=hidden></div><div id=nl class=nav>&lsaquo;</div><div id=nr class=nav>&rsaquo;</div></div>')
    html.append('<div id=controls><div id=counter><span id=cp>1</span>/<span id=tp>' + str(len(scenes)) + '</span></div><div id=title></div><div id=btns><button id=bp>&#x25c0;</button><button id=ba>&#x25b6; Auto</button><button id=bn>&#x25b6;</button></div><div id=gapc style="display:flex;align-items:center;gap:6px;font-size:12px;color:#8899aa">停声后<input id=gapA type=number min=1 max=30 step=1 value=3 style="width:44px;background:#222;color:#eee;border:1px solid #444;border-radius:4px;padding:4px">s 无声页<input id=gapS type=number min=1 max=60 step=1 value=8 style="width:44px;background:#222;color:#eee;border:1px solid #444;border-radius:4px;padding:4px">s</div><div id=as></div></div>')
    html.append('<div id=ep class=hidden>')
    html.append('<h3>Edit</h3>')
    html.append('<label>Image Prompt</label><textarea id=eip rows=3></textarea>')
    html.append('<label>Dialogue (TTS)</label><textarea id=ett rows=2></textarea>')
    html.append('<label>Workflow</label><select id=wfs></select>')
    html.append('<button id=bsw onclick=switchWf() style="background:#444;color:#eee;border:none;padding:6px 12px;border-radius:4px;cursor:pointer">Switch</button>')
    html.append('<div id=eb><button id=rgi onclick=regenImg()>Regen Image</button><button id=rgt onclick=regenTts()>Regen TTS</button><button id=sv onclick=saveText()>Save</button><button id=ec onclick="document.getElementById(\'ep\').classList.add(\'hidden\')">Close</button></div>')
    html.append('<div id=es></div></div>')
    html.append('<script>')
    html.append('var scenes=' + scenes_json + ';')
    html.append('var st=' + status_json + ';')
    html.append('var SN=' + sn_json + ';')
    html.append('var PN=' + pn_json + ';'); html.append('var BASE="/project/"+PN+"/";')
    # JavaScript functions
    html.append('var ci=0,au=null,ap=false,pa=false,ti=null,ld={},GAP_A=parseInt(localStorage.getItem("comicGapA")||"3000",10)||3000,GAP_S=parseInt(localStorage.getItem("comicGapS")||"8000",10)||8000;')
    html.append('function ub(){document.getElementById("rgi").style.display=st.comfyui?"":"none";document.getElementById("rgt").style.display=st.tts?"":"none";if(!st.comfyui){document.getElementById("wfs").style.display="none";document.getElementById("bsw").style.display="none"}}')
    html.append('function show(i){if(i<0||i>=scenes.length)return;ci=i;var s=scenes[i];document.getElementById("cp").textContent=i+1;document.getElementById("title").textContent="Scene "+(i+1)+": "+(s.title||"");document.getElementById("comic-image").src=BASE+s.image+"?"+Date.now();var ne=document.getElementById("narration");if(s.narration){ne.textContent=s.narration;ne.classList.remove("hidden","faded");clearTimeout(window._nt);var fd=Math.max(2000,Math.min(8000,s.narration.length*200));window._nt=setTimeout(function(){ne.classList.add(\"faded\")},fd)}else ne.classList.add("hidden");document.getElementById("pf").style.width=((i+1)/scenes.length*100)+"%";if(!document.getElementById("ep").classList.contains("hidden")){document.getElementById("eip").value=s.img_prompt||"";document.getElementById("ett").value=s.dialogue?s.dialogue.map(function(d){return (SN[d.speaker]?SN[d.speaker]+": ":"")+d.text_cn}).join("\\n"):""}}')
    html.append('function play(i){var s=scenes[i];if(!s.has_audio||!s.audio){if(ap&&!pa){ti=setTimeout(next,GAP_S)}return}stop();var a=ld[i];if(!a){a=new Audio(BASE+s.audio+"?"+Date.now());ld[i]=a}a.currentTime=0;au=a;a.onended=function(){au=null;if(ap&&!pa){ti=setTimeout(next,GAP_A)}};a.onerror=function(){au=null;if(ap&&!pa){ti=setTimeout(next,GAP_A)}};if(a.readyState>=3){a.play()}else{a.addEventListener("canplay",function(){a.play()},{once:true})}}')
    html.append('function stop(){if(au){au.pause();au.currentTime=0;au=null}if(ti){clearTimeout(ti);ti=null}}')
    html.append('function toggle(){var s=scenes[ci];if(!s.has_audio)return;if(au&&!au.paused){au.pause()}else{play(ci)}}')
    html.append('function prev(){if(ci>0){stop();show(ci-1);if(ap&&!pa)setTimeout(function(){play(ci)},300)}}')
    html.append('function next(){if(ci<scenes.length-1){stop();show(ci+1);if(ap&&!pa)setTimeout(function(){play(ci)},300)}else if(ap){ap=false;pa=false;stop();document.getElementById("ba").textContent="Auto";document.getElementById("ba").classList.remove("on")}}')
    html.append('function ta(){var b=document.getElementById("ba");if(ap){ap=false;pa=false;stop();b.textContent="Auto";b.classList.remove("on")}else{ap=true;pa=false;b.textContent="Pause";b.classList.add("on");setTimeout(function(){play(ci)},500)}}')
    html.append('async function lw(){try{var r=await fetch("/api/workflows");var d=await r.json();var s=document.getElementById("wfs");s.innerHTML="";for(const[k,v]of Object.entries(d.workflows||{})){var o=document.createElement("option");o.value=k;o.textContent=v.name;if(k===d.current)o.selected=true;s.appendChild(o)}}catch(e){}}')
    html.append('async function sw(){var id=document.getElementById("wfs").value;try{var r=await fetch("/api/set-workflow",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({workflowId:id})});var d=await r.json();document.getElementById("es").textContent=d.success?"OK":"Fail"}catch(e){document.getElementById("es").textContent="Error"}}')
    html.append('function oe(){var s=scenes[ci];document.getElementById("eip").value=s.img_prompt||"";document.getElementById("ett").value=s.dialogue?s.dialogue.map(function(d){return (SN[d.speaker]?SN[d.speaker]+": ":"")+d.text_cn}).join("\\n"):"";document.getElementById("ep").classList.remove("hidden")}')
    html.append('async function ri(){var s=scenes[ci];var p=document.getElementById("eip").value.trim();if(!p)return;document.getElementById("es").textContent="Generating...";try{var r=await fetch("/api/regenerate-image",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({sceneId:s.id,imgPrompt:p,projectName:PN})});var d=await r.json();if(d.success){s.image=d.imagePath;document.getElementById("comic-image").src=BASE+s.image+"?"+Date.now();document.getElementById("es").textContent="OK"}else{document.getElementById("es").textContent="Fail: "+(d.error||"")}}catch(e){document.getElementById("es").textContent="Error: "+e.message}}')
    html.append('async function rt(){var s=scenes[ci];var t=document.getElementById("ett").value.trim();if(!t)return;document.getElementById("es").textContent="Generating...";try{var r=await fetch("/api/regenerate-tts",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({sceneId:s.id,ttsText:t,projectName:PN})});var d=await r.json();if(d.success){s.audio=d.audioPath;delete ld[ci];document.getElementById("es").textContent="OK"}else{document.getElementById("es").textContent="Fail: "+(d.error||"")}}catch(e){document.getElementById("es").textContent="Error: "+e.message}}')
    html.append('async function sv(){try{var r=await fetch("/api/save-edits",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({sceneId:scenes[ci].id,imgPrompt:document.getElementById("eip").value.trim(),ttsTexts:document.getElementById("ett").value.split("\\n").filter(function(l){return l.trim()}),projectName:PN})});var d=await r.json();if(d.success){var _s=scenes[ci];_s.img_prompt=document.getElementById("eip").value.trim();var _tl=document.getElementById("ett").value.split("\\n").filter(function(l){return l.trim()});if(_s.dialogue){for(var _di=0;_di<_tl.length&&_di<_s.dialogue.length;_di++){_s.dialogue[_di].text_cn=_tl[_di]}}};document.getElementById("es").textContent=d.success?"Saved":"Fail"}catch(e){document.getElementById("es").textContent="Error: "+e.message}}')
    html.append('document.addEventListener("DOMContentLoaded",function(){ub();show(0);lw();document.getElementById("bp").onclick=prev;document.getElementById("bn").onclick=next;document.getElementById("ba").onclick=ta;document.getElementById("gapA").value=Math.round(GAP_A/1000);document.getElementById("gapS").value=Math.round(GAP_S/1000);document.getElementById("gapA").oninput=function(){var v=parseInt(this.value||"3",10);if(isNaN(v)||v<1)v=1;if(v>30)v=30;this.value=v;GAP_A=v*1000;localStorage.setItem("comicGapA",GAP_A)};document.getElementById("gapS").oninput=function(){var v=parseInt(this.value||"8",10);if(isNaN(v)||v<1)v=1;if(v>60)v=60;this.value=v;GAP_S=v*1000;localStorage.setItem("comicGapS",GAP_S)};document.getElementById("nl").onclick=prev;document.getElementById("nr").onclick=next;document.getElementById("comic-image").onclick=toggle;document.getElementById("narration").onclick=function(){var n=document.getElementById("narration");clearTimeout(window._nt);n.classList.toggle("faded")};document.getElementById("viewer").ondblclick=oe;document.getElementById("rgi").onclick=ri;document.getElementById("rgt").onclick=rt;document.getElementById("sv").onclick=sv;document.getElementById("bsw").onclick=sw;document.onkeydown=function(e){if(e.target.tagName==="INPUT"||e.target.tagName==="TEXTAREA")return;if(e.key==="ArrowLeft")prev();else if(e.key==="ArrowRight")next();else if(e.key===" "&&!e.repeat){e.preventDefault();toggle()}else if(e.key==="a")ta()}})')
    html.append('</script></body></html>')
    return HTMLResponse("".join(html))
@app.get("/project/{project_name}/images/{filename}")
async def project_image(project_name: str, filename: str):
    path = OUTPUT_DIR / project_name / "images" / filename
    if not path.exists(): raise HTTPException(404)
    return FileResponse(str(path))
@app.get("/project/{project_name}/audio/{filename}")
async def project_audio(project_name: str, filename: str):
    path = OUTPUT_DIR / project_name / "audio" / filename
    if not path.exists(): raise HTTPException(404)
    return FileResponse(str(path))
if __name__ == "__main__":
    port = int(os.environ.get("COMIC_PORT", PORT))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info", log_config=None)
