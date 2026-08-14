import os
import re
from pathlib import Path


class ModelIndexer:
    """Scan GPT-SoVITS weight directories and build a character->model map."""

    GPT_PATTERN = re.compile(r'^(.+)-e(\d+)$')
    SOVITS_PATTERN = re.compile(r'^(.+)_e(\d+)_s\d+$')

    def __init__(self, gpt_dir, sovits_dir):
        self.gpt_dir = Path(gpt_dir)
        self.sovits_dir = Path(sovits_dir)

    def scan_gpt_models(self):
        """Return {char_name: {epoch: filepath}} for GPT checkpoint files."""
        models = {}
        if not self.gpt_dir.exists():
            print(f'[ModelIndexer] GPT dir not found: {self.gpt_dir}')
            return models
        for f in self.gpt_dir.iterdir():
            if not f.suffix == '.ckpt':
                continue
            m = self.GPT_PATTERN.match(f.stem)
            if m:
                name, epoch = m.group(1), int(m.group(2))
                models.setdefault(name, {})[epoch] = str(f)
        return models

    def scan_sovits_models(self):
        """Return {char_name: {epoch: filepath}} for SoVITS model files."""
        models = {}
        if not self.sovits_dir.exists():
            print(f'[ModelIndexer] SoVITS dir not found: {self.sovits_dir}')
            return models
        for f in self.sovits_dir.iterdir():
            if not f.suffix == '.pth':
                continue
            m = self.SOVITS_PATTERN.match(f.stem)
            if m:
                name, epoch = m.group(1), int(m.group(2))
                models.setdefault(name, {})[epoch] = str(f)
        return models

    def best_model_for(self, char_name, models_dict):
        """Given {epoch: path}, return the path with the highest epoch."""
        if char_name not in models_dict:
            return None
        epochs = models_dict[char_name]
        best_epoch = max(epochs.keys())
        return epochs[best_epoch]

    def build_map(self, char_name):
        """For a given character name, find best GPT and SoVITS models."""
        gpt_models = self.scan_gpt_models()
        sovits_models = self.scan_sovits_models()
        gpt_path = self.best_model_for(char_name, gpt_models)
        sovits_path = self.best_model_for(char_name, sovits_models)
        return {'gpt': gpt_path, 'sovits': sovits_path}

    def list_all_characters(self):
        """Return all character names found in GPT weights."""
        gpt_models = self.scan_gpt_models()
        sovits_models = self.scan_sovits_models()
        all_chars = set(gpt_models.keys()) | set(sovits_models.keys())
        result = {}
        for c in sorted(all_chars):
            result[c] = {
                'gpt': self.best_model_for(c, gpt_models),
                'sovits': self.best_model_for(c, sovits_models),
            }
        return result
