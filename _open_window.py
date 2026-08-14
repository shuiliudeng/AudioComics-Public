# -*- coding: utf-8 -*-
# 在独立新控制台窗口(弹窗)前台运行一个 .bat, 不阻塞调用方。
# 用法: python _open_window.py <bat 文件名 或 绝对路径>
# 可靠性说明: 之前用 cmd /k <相对bat名> 或给路径包字面引号, 都会报
#   '"xxx.bat" 不是内部或外部命令'(cmd 找不到/引号被当作命令名)。
# 故这里改为 PowerShell Start-Process + 绝对路径直接开 bat,
# 由 PowerShell 自行解析文件并新开窗口, 无视当前目录/引号问题(实测可靠)。
import sys, subprocess, os

arg = sys.argv[1] if len(sys.argv) > 1 else ""
if not arg:
    sys.exit("用法: python _open_window.py <bat 文件名或绝对路径>")

abs_bat = arg if os.path.isabs(arg) else os.path.join(os.getcwd(), arg)
abs_bat = os.path.abspath(abs_bat)
if not os.path.exists(abs_bat):
    sys.exit("bat 不存在: " + abs_bat)
if not abs_bat.lower().endswith(".bat"):
    sys.exit("仅支持 .bat 文件: " + abs_bat)

# PowerShell Start-Process 绝对路径 -> 新窗口运行该 bat
ps_cmd = 'Start-Process -FilePath "{0}"'.format(abs_bat)
subprocess.Popen(["powershell", "-NoProfile", "-Command", ps_cmd])
print("已弹窗启动(可靠):", abs_bat)
