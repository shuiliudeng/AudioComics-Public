import json
import os
import time
import urllib.request
import urllib.parse
from pathlib import Path


class TTSClient:
    """GPT-SoVITS v2 API client."""

    def __init__(self, server_url='http://127.0.0.1:9880', settings=None):
        self.server_url = server_url.rstrip('/')
        self.settings = settings or {}
        self.current_gpt = None
        self.current_sovits = None
        self.output_dir = Path(self.settings.get('output_dir', 'v1/output'))

    def _api_get(self, endpoint):
        url = f'{self.server_url}{endpoint}'
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return resp.read().decode('utf-8')
        except Exception as e:
            print(f'[TTS] GET {endpoint} failed: {e}')
            return None

    def _api_post_audio(self, endpoint, data):
        """POST JSON, expect audio/wav response."""
        url = f'{self.server_url}{endpoint}'
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='ignore')[:200]
            print(f'[TTS] POST {endpoint} failed: {e.code} - {body}')
            return None
        except Exception as e:
            print(f'[TTS] POST {endpoint} failed: {e}')
            return None

    def switch_gpt(self, weights_path):
        if weights_path == self.current_gpt:
            return True
        path_enc = urllib.parse.quote(weights_path)
        result = self._api_get(f'/set_gpt_weights?weights_path={path_enc}')
        if result and 'success' in result.lower():
            self.current_gpt = weights_path
            print(f'[TTS] Switched GPT: {Path(weights_path).name}')
            return True
        print(f'[TTS] Failed to switch GPT: {weights_path}')
        return False

    def ensure_gpt(self, weights_path, retries=3, pause=2.0):
        import time
        for attempt in range(1, retries + 1):
            if self.switch_gpt(weights_path):
                return True
            if attempt < retries:
                print(f'[TTS] GPT switch attempt {attempt}/{retries} failed, retrying in {pause}s...')
                time.sleep(pause)
        return False

    def switch_sovits(self, weights_path):
        if weights_path == self.current_sovits:
            return True
        path_enc = urllib.parse.quote(weights_path)
        result = self._api_get(f'/set_sovits_weights?weights_path={path_enc}')
        if result and 'success' in result.lower():
            self.current_sovits = weights_path
            print(f'[TTS] Switched SoVITS: {Path(weights_path).name}')
            return True
        print(f'[TTS] Failed to switch SoVITS: {weights_path}')
        return False

    def ensure_sovits(self, weights_path, retries=3, pause=2.0):
        import time
        for attempt in range(1, retries + 1):
            if self.switch_sovits(weights_path):
                return True
            if attempt < retries:
                print(f'[TTS] SoVITS switch attempt {attempt}/{retries} failed, retrying in {pause}s...')
                time.sleep(pause)
        return False

    @staticmethod
    def _clean_text(text):
        """去掉无法被 GBK 编码的字符（如 ♡ U+2661 等装饰符），
        GPT-SoVITS 服务端按 GBK 处理文本，遇非 GBK 字符会 400 失败。"""
        if not text:
            return text or ''
        out = []
        for ch in text:
            try:
                ch.encode('gbk')
                out.append(ch)
            except UnicodeEncodeError:
                continue  # 丢弃非 GBK 字符
        return ''.join(out)

    def synthesize(self, text, ref_audio_path, ref_text='', character_name='',
                   scene_id=1, output_subdir='', speaker=''):
        """Synthesize speech and save as wav. Returns path to audio file.
        文件命名：{speaker}_{场景号:03d}_{行号:02d}.wav（speaker 缺省时退回 scene_{id:03d}.wav）"""
        text = self._clean_text(text)
        ref_text = self._clean_text(ref_text)
        data = {
            'text': text,
            'text_lang': 'zh',
            'ref_audio_path': ref_audio_path,
            'prompt_text': ref_text,
            'prompt_lang': 'zh',
            'text_split_method': 'cut5',
            'batch_size': 1,
            'media_type': 'wav',
            'streaming_mode': False,
            'top_k': 15,
            'top_p': 1,
            'temperature': 1,
            'speed_factor': 0.9,
            'sample_steps': 16,
            'fragment_interval': 0.3,
        }
        audio_data = self._api_post_audio('/tts', data)
        if audio_data is None:
            return None

        out_dir = self.output_dir / output_subdir / 'audio'
        os.makedirs(out_dir, exist_ok=True)
        if speaker:
            scene_no = scene_id // 100
            line_no = scene_id % 100
            save_path = out_dir / f'{speaker}_{scene_no:03d}_{line_no:02d}.wav'
        else:
            save_path = out_dir / f'scene_{scene_id:03d}.wav'
        with open(save_path, 'wb') as f:
            f.write(audio_data)
        print(f'[TTS] Scene {scene_id}: saved {len(audio_data)} bytes to {save_path}')
        return str(save_path)

    def check_server(self):
        result = self._api_get('/tts?text=test&text_lang=zh')
        return result is not None
