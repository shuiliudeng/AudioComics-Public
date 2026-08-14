
import json, os, sys, shutil, subprocess, time, socket, wave, struct, random, re, threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from slicer.novel_slicer import SliceIO
from image_gen.comfyui_client import ComfyUIClient
from audio_gen.tts_client import TTSClient
from audio_gen.model_indexer import ModelIndexer

try:
    import numpy as np
    import librosa
    HAS_AUDIO_CHECK = True
except Exception:
    HAS_AUDIO_CHECK = False

_REF_F0_CACHE = {}


def _f0_med_file(path, sr=16000):
    try:
        y, _ = librosa.load(path, sr=sr, mono=True)
        f0, _, _ = librosa.pyin(y, fmin=librosa.note_to_hz('C2'),
                                fmax=librosa.note_to_hz('C6'), sr=sr)
        f0n = f0[~np.isnan(f0)]
        return float(np.median(f0n)) if len(f0n) else 0.0
    except Exception:
        return 0.0


def _seg_meds(y, sr):
    hop = 512
    win = sr // 4
    e = np.array([np.sqrt(np.mean(y[i:i+win]**2)) for i in range(0, len(y)-win, win)])
    th = max(e.mean()*0.12, 1e-4)
    voiced = e > th
    segs, cur = [], None
    step = win / sr
    for i, v in enumerate(voiced):
        t0, t1 = i*step, (i+1)*step
        if v:
            if cur is None:
                cur = [t0, t1]
            else:
                cur[1] = t1
        else:
            if cur:
                if cur[1]-cur[0] >= 0.3:
                    segs.append(cur)
                cur = None
    if cur and cur[1]-cur[0] >= 0.3:
        segs.append(cur)
    out = []
    for (t0, t1) in segs:
        a, b = int(t0*sr), max(int(t1*sr), int(t0*sr)+sr//2)
        f0, _, _ = librosa.pyin(y[a:b], fmin=librosa.note_to_hz('C2'),
                                fmax=librosa.note_to_hz('C6'), sr=sr)
        f0n = f0[~np.isnan(f0)]
        m = float(np.median(f0n)) if len(f0n) else 0
        out.append(m)
    return out


def verify_audio(path, ref_path, tol=0.30, need_ratio=0.85):
    """段级 F0 校验: 与参考音频中位 F0 偏差 <= tol 的段占比 >= need_ratio 才合格。
    合成音色错乱(采样率/参考失真)时段 F0 整体偏移, 校验会失败从而触发重试。"""
    if not HAS_AUDIO_CHECK or not ref_path or not os.path.exists(ref_path):
        return True
    try:
        ref_med = _REF_F0_CACHE.get(ref_path)
        if not ref_med:
            ref_med = _f0_med_file(ref_path)
            if ref_med <= 0:
                return True
            _REF_F0_CACHE[ref_path] = ref_med
        y, sr = librosa.load(path, sr=16000, mono=True)
        meds = _seg_meds(y, sr)
        if not meds:
            return False
        if len(y) / sr < 2.0:  # 短句(呻吟/语气词)放宽
            need_ratio = 0.6
        lo, hi = ref_med * (1 - tol), ref_med * (1 + tol)
        good = sum(1 for m in meds if m > 0 and lo <= m <= hi)
        return good / len(meds) >= need_ratio
    except Exception:
        return True

BASE = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE / "config"
FRONTEND_SRC = BASE / "frontend"


def load_paths():
    """本地路径配置（ComfyUI/GPT-SoVITS/ffmpeg 等），见 config/paths.json。"""
    try:
        with open(CONFIG_DIR / "paths.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


PATHS = load_paths()


def load_settings():
    with open(CONFIG_DIR / "settings.json", "r", encoding="utf-8") as f:
        return json.load(f)

def check_port(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        try:
            s.connect((host, port))
            return True
        except (socket.timeout, ConnectionRefusedError):
            return False

def get_wav_duration(fp):
    try:
        with wave.open(fp, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        pass
    # mp3: 解析帧数估算时长（320kbps CBR 常见；VBR 按帧采样数算）
    try:
        with open(fp, "rb") as f:
            data = f.read()
        frames = 0
        i = 0
        n = len(data)
        while i < n - 4:
            if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
                version = (data[i + 1] >> 3) & 0x03
                layer = (data[i + 1] >> 1) & 0x03
                if version != 1 and layer == 1:  # Layer III
                    bitrate_idx = (data[i + 2] >> 4) & 0x0F
                    srate_idx = (data[i + 2] >> 2) & 0x03
                    pad = (data[i + 2] >> 1) & 0x01
                    if bitrate_idx not in (0, 15) and srate_idx != 3:
                        sr_table = [44100, 48000, 32000]
                        sr = sr_table[srate_idx]
                        samples = 1152 if version == 3 else 576
                        br = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0][bitrate_idx] * 1000
                        flen = (samples // 8 * br // sr) + (1 if pad else 0)
                        frames += 1
                        i += max(flen, 4)
                        continue
            i += 1
        if frames > 0:
            return frames * 1152 / 44100.0
    except Exception:
        pass
    return None

def find_ref_audio(voice_base, character_name):
    char_dir = Path(voice_base) / character_name
    if not char_dir.exists():
        return None, ""
    candidates = []
    for root, dirs, files in os.walk(char_dir):
        for fn in files:
            if not fn.endswith(".wav"):
                continue
            fp = os.path.join(root, fn)
            lab_path = os.path.splitext(fp)[0] + ".lab"
            lab_text = ""
            if os.path.exists(lab_path):
                with open(lab_path, "r", encoding="utf-8") as f:
                    lab_text = f.read().strip()
            if lab_text and ("{" in lab_text or "#" in lab_text or "SEXPRO" in lab_text):
                continue
            dur = get_wav_duration(fp)
            if dur is not None and 2.5 < dur < 10 and len(lab_text) > 5:
                candidates.append((len(lab_text), os.path.getsize(fp), lab_text, fp, dur))
    if not candidates:
        for root, dirs, files in os.walk(char_dir):
            for fn in files:
                if not fn.endswith(".wav"):
                    continue
                dur = get_wav_duration(os.path.join(root, fn))
                if dur is not None and 2.5 < dur < 10:
                    candidates.append((0, 0, "", os.path.join(root, fn), dur))
    if not candidates:
        return None, ""
    # Boost reference_audios files (cleaner, single-emotion recordings)
    for i in range(len(candidates)):
        if "reference_audios" in candidates[i][3]:
            candidates[i] = (candidates[i][0] + 1000, candidates[i][1], candidates[i][2], candidates[i][3], candidates[i][4])
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][3], candidates[0][2]

def auto_map_characters(settings, voice_base):
    indexer = ModelIndexer(settings["tts"]["gpt_weights_dir"], settings["tts"]["sovits_weights_dir"])
    all_models = indexer.list_all_characters()
    cmap = settings.get("character_model_map", {})
    alias_map_local, name_to_id_local = {}, {}
    config_dir = Path(__file__).resolve().parent.parent / "config"
    chars_config_path = config_dir / "characters.json"
    if chars_config_path.exists():
        alias_map_local, name_to_id_local = load_characters_config(config_dir)
    for char_name, model_info in all_models.items():
        if char_name not in cmap:
            dirs_to_try = [char_name]
            if char_name in alias_map_local:
                char_id = alias_map_local[char_name]
                if chars_config_path.exists():
                    with open(chars_config_path, "r", encoding="utf-8") as f2:
                        cc = json.load(f2)
                    if char_id in cc:
                        dirs_to_try.append(cc[char_id].get("name", ""))
            ref_audio, ref_text = None, ""
            for vdir in dirs_to_try:
                ref_audio, ref_text = find_ref_audio(voice_base, vdir)
                if ref_audio:
                    break
            cmap[char_name] = {
                "gpt": model_info.get("gpt") or "",
                "sovits": model_info.get("sovits") or "",
                "ref_audio": ref_audio or "",
                "ref_text": ref_text or "",
            }
    tts_root = str(Path(settings["tts"]["gpt_weights_dir"]).parent).replace("\\", "/")
    for cname in list(cmap.keys()):
        entry = cmap[cname]
        gpt = (entry.get("gpt") or "").replace("\\", "/")
        sovits = (entry.get("sovits") or "").replace("\\", "/")
        ref_audio = (entry.get("ref_audio") or "").replace("\\", "/")
        if gpt and tts_root in gpt:
            entry["gpt"] = gpt.replace(tts_root + "/", "")
        if sovits and tts_root in sovits:
            entry["sovits"] = sovits.replace(tts_root + "/", "")
        if ref_audio and tts_root in ref_audio:
            entry["ref_audio"] = ref_audio.replace(tts_root + "/", "")
    return cmap

def start_tts_server(timeout=120):
    """按需启动 TTS 服务（窗口式）：仅当 9880 端口不通时才拉起。
    生图阶段不调用本函数，TTS 直到音频阶段 run_audio_gen 才被启动，
    避免 TTS 在生图阶段空转占用显存。
    启动方式：直接复用手动版 start_tts.bat，在新控制台窗口前台执行——
    自带窗口标题、chcp 65001(UTF-8 不乱码)、logtee(窗口/文件双写)与
    "关闭此窗口即停止服务"，与手动启动完全一致。"""
    if check_port("127.0.0.1", 9880):
        return True
    gptsovits_dir = PATHS.get("gptsovits", {}).get("root") or r"E:\AI\GPT-SoVITS-v2pro-20250604-nvidia50"
    log_dir = BASE / "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_path = str(log_dir / "tts.log")
    try:
        os.remove(log_path)
    except OSError:
        pass
    # 窗口式: 复用 start_tts.bat(title + chcp 65001 + logtee + 关窗即停)。
    # 可靠方式 = PowerShell Start-Process + 绝对路径新开窗口(实测稳定, 避开 cmd
    # 相对文件名找不到'不是内部或外部命令' 及 字面引号被当命令名 两个坑)。
    tts_bat = str(BASE / "start_tts.bat")
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command",
             'Start-Process -FilePath "{0}"'.format(tts_bat)],
        )
    except Exception as e:  # 兜底: 退回首版无窗后台启动
        print(f"[Pipeline] 窗口启动 TTS 失败({e})，回退 start /B")
        cmd = f'cd /d "{gptsovits_dir}" && start /B runtime\\\\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml > "{log_path}" 2>&1'
        subprocess.Popen(["cmd.exe", "/c", cmd], shell=False, cwd=gptsovits_dir)
    start_ts = time.time()
    while time.time() - start_ts < timeout:
        if check_port("127.0.0.1", 9880):
            time.sleep(2)
            return True
        time.sleep(3)
    return False

def load_characters_config(config_dir):
    char_path = config_dir / "characters.json"
    if not char_path.exists():
        return {}, {}
    with open(char_path, "r", encoding="utf-8") as f:
        chars = json.load(f)
    alias_map = {}
    name_to_id = {}
    for cid, cdata in chars.items():
        name = cdata.get("name", "")
        name_to_id[name] = cid
        alias_map[cid] = cid
        if name:
            alias_map[name] = cid
        for alias in cdata.get("aliases", []):
            alias_map[alias] = cid
    return alias_map, name_to_id

class Pipeline:
    def __init__(self, settings, tts_backend="longcat"):
        self.settings = settings
        self.tts_backend = tts_backend
        self.config_dir = CONFIG_DIR
        self.alias_map, self.name_to_id = load_characters_config(self.config_dir)
        self.comfy = ComfyUIClient(settings["comfyui"]["server_url"], settings["comfyui"])
        if tts_backend == "longcat":
            from audio_gen.longcat_client import LongCatClient
            self.tts = LongCatClient(settings["comfyui"]["server_url"], settings["tts"])
        else:
            self.tts = TTSClient(settings["tts"]["server_url"], settings["tts"])
        self.voice_base = str(Path(settings["tts"]["gpt_weights_dir"]).parent / "voice")
        self.char_map = auto_map_characters(settings, self.voice_base)

    def run_image_gen(self, scenes, output_subdir, start_from=1):
        print(f"[Pipeline] Generating {len(scenes)} images...")
        self.comfy.output_dir = Path(self.settings["comfyui"]["output_dir"])
        results = []
        for scene in scenes:
            if scene.id < start_from:
                continue
            out_path = self.comfy.output_dir / output_subdir / 'images' / f'scene_{scene.id:03d}.png'
            if out_path.exists():
                print(f"--- Scene {scene.id}: already exists, skip ---")
                results.append((scene.id, str(out_path)))
                continue
            print(f"--- Scene {scene.id}: {scene.title} ---")
            loras = getattr(scene, "lora", None) or []
            if loras:
                lora_tags = ", ".join(
                    "{}@{}".format(l.get("name"), l.get("weight", 0.8)) for l in loras
                )
                print(f"[Pipeline] Scene {scene.id}: injecting loras: {lora_tags}")
            path = self.comfy.generate(
                prompt_positive=scene.img_prompt_en,
                prompt_negative=scene.negative_prompt,
                width=scene.canvas.width,
                height=scene.canvas.height,
                scene_id=scene.id,
                output_subdir=output_subdir,
                loras=loras,
            )
            if path:
                results.append((scene.id, path))
            else:
                print(f"[Pipeline] Scene {scene.id}: image generation FAILED")
        return results

    def run_audio_gen(self, scenes, output_subdir, sort_by_speaker=True, verify=True):
        if self.tts_backend == "longcat":
            if not check_port("127.0.0.1", 8188):
                print("[Pipeline] WARNING: ComfyUI(8188) unavailable, LongCat TTS cannot run.")
        elif not check_port("127.0.0.1", 9880):
            if not start_tts_server():
                print("[Pipeline] WARNING: TTS server unavailable.")
        n_audio = sum(1 for s in scenes if s.has_audio)
        print(f"[Pipeline] Generating audio for {n_audio} scenes...")
        results = []
        jobs = []            # 待合成台词任务
        missing = []         # 无法解析模型/参考音频的行
        done_ids = set()
        # ---- Phase A: 收集全部台词行任务（保留断点续跑：已存在场景跳过）----
        ext = "mp3" if self.tts_backend == "longcat" else "wav"
        for scene in scenes:
            if not scene.has_audio or not scene.dialogue:
                continue
            first_speaker = scene.dialogue[0].speaker_id
            # 与 tts 后端保存命名保持一致：longcat={speaker}_{scene:03d}_{line:02d}.mp3, gptsovits={speaker}_{scene:03d}.wav
            if self.tts_backend == "longcat":
                sid0 = scene.id * 100 + 0
                out_path = self.tts.output_dir / output_subdir / 'audio' / f'{first_speaker}_{sid0 // 100:03d}_{sid0 % 100:02d}.{ext}'
                legacy_path = self.tts.output_dir / output_subdir / 'audio' / f'scene_{scene.id:03d}.{ext}'
                concat_path = self.tts.output_dir / output_subdir / 'audio' / f'{first_speaker}_{scene.id:03d}.{ext}'
                existing = concat_path if concat_path.exists() else (out_path if out_path.exists() else (legacy_path if legacy_path.exists() else None))
            else:
                out_path = self.tts.output_dir / output_subdir / 'audio' / f'{first_speaker}_{scene.id:03d}.{ext}'
                legacy_path = self.tts.output_dir / output_subdir / 'audio' / f'scene_{scene.id:03d}.{ext}'
                existing = out_path if out_path.exists() else (legacy_path if legacy_path.exists() else None)
            if existing is not None:
                # 完整性校验：场景音频总时长须 >= 各行最短估算之和，否则视为残缺场景重跑
                need = sum(max(0.16 * len(d.text_cn), 0.7) for d in scene.dialogue if d.text_cn)
                dur = self._audio_duration(str(existing))
                if dur is None or dur + 0.5 < need:
                    print(f"--- Scene {scene.id}: audio INCOMPLETE ({dur}s < need {need:.1f}s), resynthesize ---")
                else:
                    print(f"--- Scene {scene.id}: audio already exists, skip ---")
                    results.append((scene.id, str(existing)))
                    done_ids.add(scene.id)
                    continue
            print(f"--- Scene {scene.id}: {scene.title} ---")
            for line_idx, d in enumerate(scene.dialogue):
                speaker = d.speaker_id
                char_info = self._resolve_character(speaker, scene)
                if not char_info:
                    ref_audio, ref_text = self._resolve_audio(speaker)
                    if ref_audio:
                        char_info = {"ref_audio": ref_audio, "ref_text": ref_text, "gpt": None, "sovits": None}
                        for cname, cinfo in self.char_map.items():
                            for alias, cid in self.alias_map.items():
                                if alias == speaker or cid == speaker or cid == cname or alias == cname:
                                    if cinfo.get("gpt") and cinfo.get("sovits"):
                                        char_info["gpt"] = cinfo["gpt"]
                                        char_info["sovits"] = cinfo["sovits"]
                                        break
                            if char_info.get("gpt"):
                                break
                    else:
                        missing.append(f'  [{scene.id}] line {line_idx}: no model for "{speaker}"')
                        continue
                if not char_info.get("ref_audio"):
                    ra, rt = self._resolve_fallback_audio(speaker, scene)
                    if ra:
                        char_info["ref_audio"] = ra
                        char_info["ref_text"] = rt
                    else:
                        missing.append(f'  [{scene.id}] line {line_idx}: no ref audio for "{speaker}"')
                        continue
                jobs.append({"scene": scene, "line_idx": line_idx, "d": d,
                             "speaker": speaker, "char_info": char_info})
        # ---- Phase B: 按模型身份分组，每组只切换一次权重后批量合成 ----
        from collections import OrderedDict
        groups = OrderedDict()
        for job in jobs:
            if self.tts_backend == "longcat":
                key = (job["char_info"].get("ref_audio") or "",)
            else:
                key = (job["char_info"].get("gpt") or "", job["char_info"].get("sovits") or "")
            groups.setdefault(key, []).append(job)
        n_switches = 0
        for gjobs in groups.values():
            g = gjobs[0]["char_info"]
            gpt, sovits = g.get("gpt") or "", g.get("sovits") or ""
            tag = f'{gjobs[0]["speaker"]} group ({len(gjobs)} lines)'
            if gpt and not self.tts.ensure_gpt(gpt):
                print(f'[Pipeline] FATAL: GPT switch failed for {tag}, skipping lines')
                continue
            if sovits and not self.tts.ensure_sovits(sovits):
                print(f'[Pipeline] FATAL: SoVITS switch failed for {tag}, skipping lines')
                continue
            n_switches += 1
            for job in gjobs:
                scene = job["scene"]
                ref_audio_path = job["char_info"].get("ref_audio") or ""
                # 时长完整性校验：T2S 语义 token 偶发提前 EOS 导致尾部丢字，
                # 丢字音频时长明显偏短。按文本长度估算最短合理时长，过短则换 seed 重试。
                text_len = len(job["d"].text_cn)
                min_dur = max(0.16 * text_len, 0.7)  # ~0.16s/字(含标点停顿)，短句保底 0.7s
                path = None
                best_path, best_dur = None, -1.0
                ok = False
                for attempt in range(1, 6):
                    path = self.tts.synthesize(
                        text=job["d"].text_cn,
                        ref_audio_path=ref_audio_path,
                        ref_text=job["char_info"].get("ref_text") or "",
                        character_name=job["speaker"],
                        scene_id=scene.id * 100 + job["line_idx"],
                        output_subdir=output_subdir,
                        speaker=job["speaker"],
                    )
                    if not path:
                        break
                    dur = get_wav_duration(path) or 0.0
                    if dur > best_dur:
                        best_path, best_dur = path, dur
                    ok_len = dur >= min_dur
                    if self.tts_backend == "longcat":
                        ok_f0 = True
                    else:
                        ok_f0 = (not verify) or verify_audio(path, ref_audio_path)
                    if ok_len and ok_f0:
                        ok = True
                        break
                    if attempt < 5:
                        reason = []
                        if not ok_len:
                            reason.append(f"时长不足({dur:.1f}s<{min_dur:.1f}s)")
                        if not ok_f0:
                            reason.append("F0 校验未过")
                        print(f'  [{scene.id}] line {job["line_idx"]} {job["speaker"]}: {" / ".join(reason)}, 重试 {attempt}/5')
                        time.sleep(1.0)
                # 5 次都没通过校验时，退而取时长最长的版本（保住最完整输出）
                if not ok and best_path is not None:
                    path = best_path
                if path:
                    job["path"] = path
                    _safechars = []
                    for _c in job["d"].text_cn[:30]:
                        try:
                            _c.encode('gbk')
                            _safechars.append(_c)
                        except UnicodeEncodeError:
                            pass
                    print(f'  [{scene.id}] line {job["line_idx"]} {job["speaker"]}: {"".join(_safechars)}')
                else:
                    print(f'  [{scene.id}] line {job["line_idx"]} {job["speaker"]}: VERIFY FAILED, 已跳过')
        print(f'[Pipeline] Weight switches: {n_switches} (groups: {len(groups)})')
        # ---- Phase C: 按场景回装 concat（分句文件按行序合并后删除）----
        by_scene = {}
        for job in jobs:
            if job.get("path"):
                by_scene.setdefault(job["scene"].id, []).append(job)
        for scene in scenes:
            if scene.id in done_ids or scene.id not in by_scene:
                continue
            seg_files = [j["path"] for j in sorted(by_scene[scene.id], key=lambda j: j["line_idx"])]
            if not seg_files:
                continue
            first_speaker = scene.dialogue[0].speaker_id
            combined = os.path.join(os.path.dirname(seg_files[0]), f"{first_speaker}_{scene.id:03d}.wav")
            if self.tts_backend == "longcat":
                # LongCat 输出 mp3：用 ffmpeg concat 拼接（wave 模块不认 mp3）
                ffmpeg = PATHS.get("comfyui", {}).get("ffmpeg") or r"E:\AI\ComfyUI-aki-v3\.ext\Library\bin\ffmpeg.exe"
                combined = os.path.join(os.path.dirname(seg_files[0]), f"{first_speaker}_{scene.id:03d}.mp3")
                list_file = os.path.join(os.path.dirname(seg_files[0]), f"_concat_{scene.id}.txt")
                try:
                    with open(list_file, "w", encoding="utf-8") as lf:
                        for seg in seg_files:
                            lf.write(f"file '{os.path.abspath(seg).replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'\n")
                    cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", combined]
                    r = subprocess.run(cmd, capture_output=True, timeout=60)
                    if r.returncode != 0 or not os.path.exists(combined):
                        cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-acodec", "libmp3lame", "-q:a", "2", combined]
                        r = subprocess.run(cmd, capture_output=True, timeout=120)
                    if r.returncode == 0 and os.path.exists(combined):
                        results.append((scene.id, combined))
                        for seg in seg_files:
                            try: os.remove(seg)
                            except OSError: pass
                        try: os.remove(list_file)
                        except OSError: pass
                        continue
                    else:
                        raise RuntimeError(f"ffmpeg concat failed rc={r.returncode}")
                except Exception as e:
                    print(f'  [{scene.id}] longcat concat error: {e}')
                    results.append((scene.id, seg_files[0]))
                    continue
            try:
                # 注意: 不同角色权重的 TTS 输出采样率可能不同(32k/48k)，
                # 必须统一到首段采样率再拼接，否则混入的音段音高会错乱
                target_rate = None
                seg_frames = []
                for seg in seg_files:
                    with wave.open(seg, "rb") as w:
                        rate = w.getframerate()
                        if target_rate is None:
                            target_rate = rate
                        data = w.readframes(w.getnframes())
                    if rate != target_rate:
                        try:
                            import numpy as np
                            import librosa
                            y, _ = librosa.load(seg, sr=target_rate, mono=True)
                            data = (np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes()
                        except Exception:
                            pass
                    seg_frames.append(data)
                if target_rate:
                    with wave.open(combined, "wb") as out:
                        out.setnchannels(1)
                        out.setsampwidth(2)
                        out.setframerate(target_rate)
                        for f in seg_frames:
                            out.writeframes(f)
                    results.append((scene.id, combined))
                    for seg in seg_files:
                        if seg != combined:
                            try: os.remove(seg)
                            except: pass
            except Exception as e:
                print(f'  [{scene.id}] concat error: {e}')
                results.append((scene.id, seg_files[0]))
        if missing:
            print("[Pipeline] 以下台词行因缺模型/参考音频被跳过:")
            for m in missing:
                print(m)
        if self.tts_backend == "longcat" and hasattr(self.tts, "release"):
            try:
                last_ref = ""
                for job in jobs:
                    if job.get("char_info", {}).get("ref_audio"):
                        last_ref = job["char_info"]["ref_audio"]
                        break
                self.tts.release(ref_audio_path=last_ref or None)
            except Exception as e:
                print(f"[Pipeline] LongCat release error: {e}")
        return results

    def _audio_duration(self, path):
        """读取音频时长：wav 用 wave 模块，mp3 用 ffprobe；失败返回 None。"""
        try:
            import wave as _w
            with _w.open(path, "rb") as f:
                return f.getnframes() / f.getframerate()
        except Exception:
            pass
        try:
            ffprobe = PATHS.get("comfyui", {}).get("ffprobe") or r"E:\AI\ComfyUI-aki-v3\.ext\Library\bin\ffprobe.exe"
            r = subprocess.run(
                [ffprobe, "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", path],
                capture_output=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip():
                return float(r.stdout.decode("utf-8", "replace").strip())
        except Exception:
            pass
        return None

    def _resolve_character(self, speaker_id, scene):
        if speaker_id in self.char_map:
            return self.char_map[speaker_id]
        for alias, cid in self.alias_map.items():
            if speaker_id == alias or speaker_id == cid:
                if alias in self.char_map:
                    return self.char_map[alias]
                if cid in self.char_map:
                    return self.char_map[cid]
        for cid, cdata in self.char_map.items():
            if cid.lower() == speaker_id.lower():
                return cdata
        if speaker_id in self.name_to_id:
            canonical = self.name_to_id[speaker_id]
            if canonical in self.char_map:
                return self.char_map[canonical]
        return None

    def _resolve_fallback_audio(self, speaker_id, scene):
        for cname, cinfo in self.char_map.items():
            if cinfo.get("ref_audio"):
                return cinfo["ref_audio"], cinfo["ref_text"]
        return "", ""

    def _find_ref_audio_by_speaker(self, speaker_id):
        for cid, cdata in self.char_map.items():
            if cdata.get("ref_audio"):
                if cid.lower() == speaker_id.lower() or cid in speaker_id:
                    return cdata.get("ref_audio", ""), cdata.get("ref_text", "")
        return "", ""

    def _resolve_audio(self, speaker_id):
        resolved = self._resolve_character(speaker_id, None)
        if resolved and resolved.get("ref_audio"):
            return resolved["ref_audio"], resolved.get("ref_text", "")
        return self._find_ref_audio_by_speaker(speaker_id)

    def build_manifest(self, scenes, output_subdir, scenes_path=None):
        out_dir = Path(self.settings["comfyui"]["output_dir"]) / output_subdir
        os.makedirs(out_dir, exist_ok=True)
        # 从切片文件名推导真实粒度(不再硬编码 medium)
        gran = "medium"
        slice_name = os.path.basename(str(scenes_path)) if scenes_path else ""
        if slice_name:
            import re as _re
            m = _re.search(r"_sliced_(low|medium|high)\.json$", slice_name)
            if m:
                gran = m.group(1)
        manifest = {"project": output_subdir, "granularity": gran, "scenes": [],
                    "slice_path": slice_name or None}
        for scene in scenes:
            entry = {
                "id": scene.id, "title": scene.title,
                "narration": scene.narration_cn,
                "image": f"images/scene_{scene.id:03d}.png",
                "img_prompt": scene.img_prompt_en,
                "has_audio": scene.has_audio,
                "dialogue": [
                    {"speaker": d.speaker_id, "text_cn": d.text_cn,
                     "text_en": d.text_en_for_bubble, "emotion": d.emotion}
                    for d in scene.dialogue
                ],
            }
            if scene.has_audio and scene.dialogue:
                spk = scene.dialogue[0].speaker_id
                ext = "mp3" if self.tts_backend == "longcat" else "wav"
                new_p = out_dir / 'audio' / f"{spk}_{scene.id:03d}.{ext}"
                leg_p = out_dir / 'audio' / f"scene_{scene.id:03d}.{ext}"
                if new_p.exists():
                    entry["audio"] = f"audio/{spk}_{scene.id:03d}.{ext}"
                elif leg_p.exists():
                    entry["audio"] = f"audio/scene_{scene.id:03d}.{ext}"
                else:
                    entry["audio"] = f"audio/{spk}_{scene.id:03d}.{ext}"
            elif scene.has_audio:
                ext = "mp3" if self.tts_backend == "longcat" else "wav"
                entry["audio"] = f"audio/scene_{scene.id:03d}.{ext}"
            manifest["scenes"].append(entry)
        with open(out_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        return str(out_dir / "manifest.json")

    def deploy_frontend(self, output_subdir):
        out_dir = Path(self.settings["comfyui"]["output_dir"]) / output_subdir
        os.makedirs(out_dir, exist_ok=True)
        for fn in ["index.html", "style.css", "app.js"]:
            src = FRONTEND_SRC / fn
            if src.exists():
                shutil.copy2(str(src), str(out_dir / fn))

def print_vram_info():
    """打印显存状态与 TTS 容量评估（nvidia-smi 不可用时静默跳过）"""
    try:
        import subprocess
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        used, total = [int(x) for x in out.split(",")]
        free = total - used
        # 实测：ComfyUI 生图 ~12GB/16GB；TTS 服务 base ~2.3GB + 每角色权重对 ~1.1GB
        tts_pairs = max(0, int((free - 2500) // 1100))
        print(f"[VRAM] used {used}MiB / {total}MiB, free {free}MiB | TTS 可安全常驻角色对数: {tts_pairs}")
        return free
    except Exception:
        return None


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenes", default=str(BASE / "examples" / "sample_novel_sliced_medium.json"))
    parser.add_argument("--output", default="sample_novel")
    parser.add_argument("--skip-image", action="store_true")
    parser.add_argument("--workflow", default=None, help="workflow id (sdxl-basic, anima-txt2img-base, anima-aesthetic-lora)")
    parser.add_argument("--skip-audio", action="store_true")
    parser.add_argument("--start-from", type=int, default=1)
    parser.add_argument("--force", action="store_true", help="skip collision check, overwrite existing output")
    parser.add_argument("--resume", action="store_true", help="resume incomplete run instead of starting fresh")
    parser.add_argument("--parallel", action="store_true",
                        help="并行生图+生音（旧行为）。实测会抢 GPU/显存：生图 66s→170s/张，仅限小项目/显存富余时使用")
    parser.add_argument("--no-audio-sort", action="store_true",
                        help="音频不按角色分组排序（默认排序：每组角色只加载一次权重，切换次数 200+→~13）")
    parser.add_argument("--no-audio-verify", action="store_true",
                        help="关闭合成后的段级 F0 校验与自动重试（默认开启，约增加 5~10 耗时但坏音频会自动重合成）")
    parser.add_argument("--tts-backend", default="longcat", choices=["longcat", "gptsovits"],
                        help="声音后端: longcat=ComfyUI LongCat-AudioDIT 克隆TTS(默认), gptsovits=旧GPT-SoVITS")
    args = parser.parse_args()
    settings = load_settings()
    if args.workflow:
        settings.setdefault("comfyui", {})["workflow"] = args.workflow
    project = SliceIO.load(args.scenes)
    errors = SliceIO.validate(project)
    if errors:
        for e in errors:
            print(f"  - {e}")
        return

    # Output directory collision check
    output_root = Path(settings["comfyui"]["output_dir"])
    out_dir = output_root / args.output
    manifest_path = out_dir / "manifest.json"

    if out_dir.exists() and manifest_path.exists() and not args.force and not args.skip_image and not args.skip_audio:
        if args.resume:
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                existing_scenes = len(existing.get("scenes", []))
                total_scenes = len(project.scenes)
                if existing_scenes < total_scenes:
                    print(f"[Pipeline] Resuming incomplete run ({existing_scenes}/{total_scenes})")
                else:
                    print(f"[Pipeline] All scenes already complete, nothing to resume")
            except Exception as e:
                print(f"[Pipeline] Could not read manifest: {e}")
        else:
            i = 1
            while (output_root / f"{args.output}_v{i}").exists():
                i += 1
            new_name = f"{args.output}_v{i}"
            out_dir.rename(output_root / new_name)
            print(f"[Pipeline] Previous output moved to: {new_name}")

    pipeline = Pipeline(settings, tts_backend=args.tts_backend)

    # 单实例锁：防止同一项目被重复启动（重复提交 bug）
    # 1) 锁文件记录 PID；进程仍在则退出，进程已死则视为过期锁直接覆盖
    # 2) 额外全量扫描正在运行的 pipeline.py 进程，兜底旧代码进程（启动时没写锁）
    #    或锁文件被误删的场景。以 --output 参数为准判断是否为同一项目。
    my_pid = os.getpid()
    lock_path = out_dir / ".pipeline.lock"
    lock_active = False
    if lock_path.exists():
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                old_pid = int(f.read().strip())
            if old_pid == my_pid:
                lock_active = True
            else:
                try:
                    os.kill(old_pid, 0)
                    print(f"[Pipeline] 已有管道进程 (PID {old_pid}) 正在运行本项目 {args.output}，为避免重复提交自动退出。")
                    return
                except (OSError, ValueError):
                    print(f"[Pipeline] 发现过期锁文件，覆盖（原进程已退出）")
        except (ValueError, OSError) as e:
            print(f"[Pipeline] 锁文件损坏({e})，视为过期，覆盖")
    else:
        print(f"[Pipeline] 无锁文件，尝试扫描运行中的 pipeline 进程...")
        try:
            ps_script = (
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'pipeline\\.py' } | "
                "ForEach-Object { [PSCustomObject]@{ PID=$_.ProcessId; CMD=$_.CommandLine } } | "
                "ConvertTo-Json -Compress"
            )
            raw = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script],
                                 capture_output=True, timeout=20).stdout.decode("utf-8", "replace")
            procs = json.loads(raw) if raw.strip() else []
            if isinstance(procs, dict):
                procs = [procs]
            for proc in procs:
                pid = int(proc.get("PID", 0))
                if pid == my_pid:
                    continue
                cmdline = proc.get("CMD", "") or ""
                m_out = re.search(r"--output[=\s]+([^\s]+)", cmdline)
                if m_out and m_out.group(1).strip().strip('"') == args.output:
                    print(f"[Pipeline] 扫描到正在运行的管道进程 (PID {pid}) 运行同一项目 {args.output}，自动退出。")
                    return
        except Exception as e:
            print(f"[Pipeline] 扫描运行中管道进程失败({e})，仅靠锁文件")
    if not lock_active:
        try:
            with open(lock_path, "w", encoding="utf-8") as f:
                f.write(str(my_pid))
        except OSError as e:
            print(f"[Pipeline] 无法写入锁文件: {e}")

    free_vram = print_vram_info()

    # 默认串行：先生图，后生音。
    # 实测数据（2026-07）：并行时 TTS 与 ComfyUI 抢 GPU/显存，生图 66s→170s/张；
    # 串行两阶段各自独占 GPU，图片 ~66s/张 + 音频（按角色排序后）仅需 ~30-40 分钟。
    if args.parallel:
        if not args.skip_image and not args.skip_audio and free_vram is not None and free_vram < 4500:
            print("[WARNING] 显存余量 <4.5GB，并行模式可能导致 TTS 挤占 ComfyUI 显存（实测生图会显著变慢）。建议去掉 --parallel")
        threads = []
        if not args.skip_image:
            t_img = threading.Thread(
                target=pipeline.run_image_gen,
                args=(project.scenes, args.output, args.start_from),
                name="image-gen",
            )
            threads.append(t_img)
            t_img.start()
        if not args.skip_audio:
            t_aud = threading.Thread(
                target=pipeline.run_audio_gen,
                args=(project.scenes, args.output),
                kwargs={"sort_by_speaker": not args.no_audio_sort},
                name="audio-gen",
            )
            threads.append(t_aud)
            t_aud.start()
        for t in threads:
            t.join()
    else:
        if not args.skip_image:
            print("[Pipeline] 阶段一/二：生图（ComfyUI 独占 GPU）...")
            pipeline.run_image_gen(project.scenes, args.output, args.start_from)
        if not args.skip_audio:
            print("[Pipeline] 阶段二/二：生音（TTS 独占 GPU，按角色分组排序）...")
            pipeline.run_audio_gen(project.scenes, args.output, sort_by_speaker=not args.no_audio_sort,
                                   verify=not args.no_audio_verify)

    pipeline.build_manifest(project.scenes, args.output, scenes_path=args.scenes)
    pipeline.deploy_frontend(args.output)
    # 清理单实例锁
    try:
        os.remove(lock_path)
    except OSError:
        pass

if __name__ == "__main__":
    main()
