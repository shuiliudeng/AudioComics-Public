# Gradio API 版 TTS 客户端
import os, json, time
from pathlib import Path
from gradio_client import Client

class TTSGradioClient:
    def __init__(self, server_url="http://127.0.0.1:9880", output_dir="v1/output"):
        self.server_url = server_url.rstrip("/")
        self.client = None
        self.output_dir = Path(output_dir)
        self.current_gpt = None
        self.current_sovits = None

    def _ensure_client(self):
        if self.client is None:
            self.client = Client(self.server_url, verbose=False)

    def check_server(self):
        try:
            self._ensure_client()
            return True
        except:
            return False

    def synthesize(self, text, ref_audio_path="", prompt_text="",
                   character_name="", scene_id=1, output_subdir=""):
        self._ensure_client()
        # get_tts_wav 参数顺序
        ref = ref_audio_path or None
        prompt_t = prompt_text or None
        ref_free = not bool(ref_audio_path)
        try:
            result = self.client.predict(
                ref,           # ref_wav_path
                prompt_t,      # prompt_text
                "中文",        # prompt_language
                text,          # text
                "中文",        # text_language
                "按中文标点符号切",  # how_to_cut
                15,            # top_k
                1.0,           # top_p
                1.0,           # temperature
                ref_free,      # ref_free
                1.0,           # speed
                False,         # if_freeze
                None,          # inp_refs
                32,            # sample_steps
                False,         # if_sr
                0.3,           # pause_second
                api_name="/predict"
            )
        except Exception as e:
            print(f"[TTS] Gradio predict failed: {e}")
            return None
        # result is (sample_rate, audio_array) tuple for type="numpy"
        # or a file path for type="filepath"
        out_dir = self.output_dir / output_subdir / "audio"
        os.makedirs(out_dir, exist_ok=True)
        save_path = out_dir / f"scene_{scene_id:03d}.wav"

        if isinstance(result, tuple) and len(result) == 2:
            sr, audio_arr = result
            import soundfile as sf
            sf.write(str(save_path), audio_arr, sr, format="WAV", subtype="PCM_16")
            print(f"[TTS] Scene {scene_id}: saved {save_path} ({len(audio_arr)} samples)")
            return str(save_path)
        elif isinstance(result, str):
            # result is a file path
            import shutil
            shutil.copy(result, save_path)
            print(f"[TTS] Scene {scene_id}: copied {save_path}")
            return str(save_path)
        else:
            print(f"[TTS] Scene {scene_id}: unexpected result type {type(result)}")
            return None

    def switch_gpt(self, weights_path):
        pass  # Gradio API 不支持运行时切换模型

    def switch_sovits(self, weights_path):
        pass
