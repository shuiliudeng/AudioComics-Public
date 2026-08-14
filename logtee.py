# logtee.py — 日志双写包装器：窗口显示 + 文件保留
# 用法: python logtee.py [--cwd <dir>] <logfile> -- <command> [args...]
# 特点:
#   1. 目标进程前台运行 -> 关闭窗口 = 关闭服务(释放显存)
#   2. 输出同时打印到控制台(窗口可见)和追加写入文件
#   3. 用 Python open() 打开日志(Windows 共享写) -> 服务自身写同一文件不再冲突
#   4. --cwd 指定目标进程的工作目录（重要：api_v2.py / configs 依赖 cwd=GPT-SoVITS 根目录）
import sys, os, subprocess

def _set_utf8_console():
    """Windows 控制台设为 UTF-8(65001)：保证窗口里中英文都不乱码。
    (与 start_*.bat 里的 chcp 65001 一致；这里的设置对所有调用 logtee 的窗口兜底)"""
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleOutputCP(65001)
            k.SetConsoleCP(65001)
        except Exception:
            pass

def _smart_decode(b):
    """智能解码子进程字节流：UTF-8 优先，失败回退 GBK。
    ComfyUI(tqdm 进度条)输出 UTF-8，GPT-SoVITS 输出 GBK，
    单一编码会有一方窗口乱码(鈻堚枅=█ 的 UTF-8 被 GBK 误读)。"""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("gbk", errors="replace")

def main():
    _set_utf8_console()
    cwd = None
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "--cwd":
        cwd = args[1]
        args = args[2:]
    if len(args) < 3 or args[1] != "--":
        print("用法: python logtee.py [--cwd <dir>] <logfile> -- <command> [args...]")
        sys.exit(2)
    logfile = args[0]
    cmd = args[2:]

    os.makedirs(os.path.dirname(os.path.abspath(logfile)), exist_ok=True)
    f = open(logfile, "a", encoding="utf-8", errors="replace")
    # 强制子进程 Python 以 UTF-8 输出，避免 GBK/UTF-8 混流乱码（tqdm █、中文告警等）
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             bufsize=0, cwd=cwd, env=env)
    except Exception as e:
        print(f"[logtee] 启动失败: {e}")
        f.write(f"[logtee] 启动失败: {e}\n")
        f.flush()
        sys.exit(1)

    def read_loop(stream):
        buf = b""
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = _smart_decode(line)
                try:
                    print(text, end="\n", flush=True)
                except Exception:
                    pass
                f.write(text + "\n")
                f.flush()
        if buf:
            text = _smart_decode(buf)
            try:
                print(text, end="\n", flush=True)
            except Exception:
                pass
            f.write(text + "\n")
            f.flush()
        stream.close()

    import threading
    t = threading.Thread(target=read_loop, args=(p.stdout,), daemon=True)
    t.start()
    try:
        code = p.wait()
    except KeyboardInterrupt:
        # 关窗/Ctrl+C：终止子进程树
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True)
        except Exception:
            pass
        p.wait()
        code = 1
    t.join(timeout=5)
    f.close()
    sys.exit(code or 0)

if __name__ == "__main__":
    main()
