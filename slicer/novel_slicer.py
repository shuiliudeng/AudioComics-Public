import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class DialogueLine:
    speaker_id: str
    text_cn: str
    text_en_for_bubble: str
    emotion: str = "neutral"


@dataclass
class AppearanceEntry:
    scene_start: int
    description: str


@dataclass
class CharacterDef:
    name: str
    voice_model: str = ""
    appearance_log: list = field(default_factory=list)


@dataclass
class Canvas:
    width: int = 1152
    height: int = 1536


@dataclass
class Scene:
    id: int
    title: str = ""
    narration_cn: str = ""
    characters_present: list = field(default_factory=list)
    characters_appearance: dict = field(default_factory=dict)
    img_prompt_en: str = ""
    negative_prompt: str = ""
    canvas: Canvas = field(default_factory=Canvas)
    dialogue: list = field(default_factory=list)
    has_audio: bool = True
    lora: list = field(default_factory=list)


@dataclass
class SlicedProject:
    project: str
    granularity: str = "medium"
    characters: dict = field(default_factory=dict)
    scenes: list = field(default_factory=list)


class SliceIO:
    """读写切片 JSON 的工具类"""

    @staticmethod
    def dump(project: SlicedProject, path: str):
        """将 SlicedProject 写入 JSON 文件"""
        d = asdict(project)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        print(f"[SliceIO] 写入 {path}")

    @staticmethod
    def load(path: str) -> SlicedProject:
        """从 JSON 文件读取 SlicedProject"""
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        scenes = []
        for sd in d.get("scenes", []):
            canvas_data = sd.get("canvas", {})
            canvas = Canvas(
                width=canvas_data.get("width", 1152),
                height=canvas_data.get("height", 1536),
            )
            dialogue = [
                DialogueLine(**dl) for dl in sd.get("dialogue", [])
            ]
            scene = Scene(
                id=sd["id"],
                title=sd.get("title", ""),
                narration_cn=sd.get("narration_cn", ""),
                characters_present=sd.get("characters_present", []),
                characters_appearance=sd.get("characters_appearance", {}),
                img_prompt_en=sd.get("img_prompt_en", ""),
                negative_prompt=sd.get("negative_prompt", ""),
                canvas=canvas,
                dialogue=dialogue,
                has_audio=sd.get("has_audio", len(dialogue) > 0),
                lora=sd.get("lora", []),
            )
            scenes.append(scene)
        proj = SlicedProject(
            project=d.get("project", ""),
            granularity=d.get("granularity", "medium"),
            characters=d.get("characters", {}),
            scenes=scenes,
        )
        return proj

    @staticmethod
    def validate(project: SlicedProject) -> list:
        """返回校验错误列表，为空则通过"""
        errors = []
        if not project.project:
            errors.append("project name is empty")
        if project.granularity not in ("low", "medium", "high"):
            errors.append(f"invalid granularity: {project.granularity}")
        if not project.scenes:
            errors.append("no scenes")
        char_ids = set(project.characters.keys())
        for i, s in enumerate(project.scenes):
            if not s.img_prompt_en or len(s.img_prompt_en.split()) < 20:
                errors.append(
                    f"scene[{s.id}]: img_prompt_en too short "
                    f"(got {len(s.img_prompt_en.split())} words, need >=20)"
                )
            for dl in s.dialogue:
                if dl.speaker_id not in char_ids:
                    errors.append(
                        f"scene[{s.id}]: unknown speaker_id"
                        f" '{dl.speaker_id}'"
                    )
            if s.has_audio and not s.dialogue:
                errors.append(
                    f"scene[{s.id}]: has_audio=true but no dialogue"
                )
        return errors
