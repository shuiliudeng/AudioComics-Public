# -*- coding: utf-8 -*-
# 可靠的后台管线启动器（解决 cmd start / CREATE_NEW_CONSOLE 分离失败导致调用方卡死/不启动的问题）。
# 原理与 _open_window.py 相同：生成临时 bat -> PowerShell Start-Process 新开独立窗口运行，
# 进程不继承调用方控制台句柄，AI 助手等工具的 bash 调用立即返回，管线在后台继续跑。
# 用法: python _open_pipeline.py <scenes_json> <output> [workflow] [extra args...]
import sys, os, subprocess, json

if len(sys.argv) < 3:
    sys.exit("用法: python _open_pipeline.py <scenes_json> <output> [workflow] [extra args...]")

scenes = os.path.abspath(sys.argv[1])
out = sys.argv[2]
wf = sys.argv[3] if len(sys.argv) > 3 else "anima-sdxl-direct"
extra = sys.argv[4:]

# 本地路径统一从 config/paths.json 读取（ComfyUI / GPT-SoVITS 等）
HERE = os.path.dirname(os.path.abspath(__file__))
try:
    with open(os.path.join(HERE, "config", "paths.json"), "r", encoding="utf-8") as f:
        PATHS = json.load(f)
except Exception:
    PATHS = {}
BASE = HERE  # 项目根目录（本文件所在目录）
PY = PATHS.get("gptsovits", {}).get("python") or r"E:\AI\GPT-SoVITS-v2pro-20250604-nvidia50\runtime\python.exe"
LOG = os.path.join(HERE, "logs", "pipeline.log")
os.makedirs(os.path.dirname(LOG), exist_ok=True)
try:
    os.remove(LOG)
except OSError:
    pass

# 生成临时 bat（UTF-8 编码，配合 bat 内 chcp 65001 保持一致）
bat_path = os.path.join(os.path.dirname(LOG), "_pipeline_run.bat")
extra_str = " " + " ".join(extra) if extra else ""
bat_lines = [
    "@echo off",
    "chcp 65001 >nul",
    'cd /d "{0}"'.format(BASE),
    '"{py}" -u orchestrator\pipeline.py --scenes "{scenes}" --output "{out}" --workflow "{wf}"{extra} < NUL > "{log}" 2>&1'.format(
        py=PY, scenes=scenes, out=out, wf=wf, extra=extra_str, log=LOG),
]
with open(bat_path, "w", encoding="utf-8") as f:
    f.write("\r\n".join(bat_lines) + "\r\n")

# 与 _open_window.py 相同的可靠启动方式：PowerShell Start-Process 绝对路径开 bat
# -WindowStyle Hidden：窗口完全隐藏，既不会弹可见 cmd 窗口，也不会被误关杀进程
ps_cmd = 'Start-Process -FilePath "{0}" -WindowStyle Hidden'.format(bat_path)
subprocess.Popen(["powershell", "-NoProfile", "-Command", ps_cmd],
                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
print("pipeline launched (new console via bat). log:", LOG)
