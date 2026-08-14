// 自动连播计时常量
const MIN_PER_PAGE_MS = 2500;  // 每页保底停留时间(声音再短也不低于此)
const READ_CHARS_PER_SEC = 4.5; // 中文阅读速度估算(字/秒)
const READ_BUFFER_MS = 700;     // 阅读额外缓冲
const READ_MIN_MS = 1800;       // 阅读时间下限

const ComicApp = {
  manifest: null,
  scenes: [],
  currentIndex: 0,
  audio: null,
  isAutoPlay: false,
  isPaused: false,
  autoTimer: null,
  loadedAudios: {},
  sceneStartAt: 0,

  async init() {
    await this.loadManifest();
    this.bindEvents();
    this.showScene(0);
  },

  async loadManifest() {
    try {
      const resp = await fetch("manifest.json?" + Date.now());
      this.manifest = await resp.json();
      this.scenes = this.manifest.scenes;
      document.getElementById("total-pages").textContent = this.scenes.length;
    } catch (e) {
      document.getElementById("title-display").textContent =
        "Error: manifest.json not found. Run pipeline first.";
    }
    if (!this.scenes || !this.scenes.length) {
      document.getElementById("title-display").textContent =
        "No scenes in manifest.";
    }
  },

  bindEvents() {
    document.getElementById("btn-prev").onclick = () => this.prev();
    document.getElementById("btn-next").onclick = () => this.next();
    document.getElementById("btn-auto").onclick = () => this.toggleAutoPlay();
    document.getElementById("nav-left").onclick = () => this.prev();
    document.getElementById("nav-right").onclick = () => this.next();
    document.getElementById("comic-image").onclick = () => this.toggleAudio();

    document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.isContentEditable) return;
      if (e.key === "ArrowLeft") this.prev();
      else if (e.key === "ArrowRight") this.next();
      else if (e.key === " ") { e.preventDefault(); this.toggleAudio(); }
      else if (e.key === "a" || e.key === "A") this.toggleAutoPlay();
    });

    document.getElementById("btn-edit-close").onclick = () =>
      document.getElementById("edit-panel").classList.add("hidden");
    document.getElementById("btn-regen-img").onclick = () => this.regenerateImage();
    document.getElementById("btn-switch-workflow").onclick = () => this.switchWorkflow();
    this.loadWorkflows();
    document.getElementById("btn-regen-tts").onclick = () => this.regenerateTTS();
    document.getElementById("btn-save-text").onclick = () => this.saveTextEdit();

    document.getElementById("viewer").ondblclick = (e) => { if (e.target.id === "comic-image" || e.target.id === "viewer" || e.target.id === "narration-overlay") this.openEdit(); };
  },

  showScene(index) {
    if (index < 0 || index >= this.scenes.length) return;
    this.currentIndex = index;
    const scene = this.scenes[index];

    document.getElementById("current-page").textContent = index + 1;
    document.getElementById("title-display").textContent =
      `第${index + 1}幕: ${scene.title || ""}`;

    const img = document.getElementById("comic-image");
    img.src = scene.image + "?" + Date.now();
    img.alt = `scene ${index + 1}`;

    const narEl = document.getElementById("narration-overlay");
    if (scene.narration) {
      narEl.textContent = scene.narration;
      narEl.classList.remove("hidden");
    } else {
      narEl.classList.add("hidden");
    }

    const dd = document.getElementById("dialogue-display");
    const dt = document.getElementById("dialogue-text");
    const as = document.getElementById("audio-status");
    if (scene.dialogue && scene.dialogue.length > 0) {
      dd.classList.remove("hidden");
      dt.textContent = scene.dialogue
        .map((d) => `${d.speaker}: "${d.text_cn}"`)
        .join(" | ");
    } else {
      dd.classList.add("hidden");
    }

    if (scene.has_audio && scene.audio) {
      as.textContent = "🎵 点击图片播放声音";
    } else {
      as.textContent = "（本页无声）";
    }

    const pct = ((index + 1) / this.scenes.length) * 100;
    document.getElementById("progress-fill").style.width = pct + "%";

    this.preloadAudio(index);
  },

  preloadAudio(index) {
    for (let i = Math.max(0, index - 1); i <= Math.min(index + 2, this.scenes.length - 1); i++) {
      const s = this.scenes[i];
      if (s.has_audio && s.audio && !this.loadedAudios[i]) {
        const audio = new Audio(s.audio);
        audio.preload = "auto";
        this.loadedAudios[i] = audio;
      }
    }
  },

  estimateReadingMs(scene) {
    // 停留时间与文字相关：根据旁白+对话字数估算阅读耗时
    const text =
      (scene.narration || "") +
      " " +
      (scene.dialogue || []).map((d) => d.text_cn || "").join(" ");
    const chars = text.replace(/\s+/g, "").length;
    const readMs = (chars / READ_CHARS_PER_SEC) * 1000 + READ_BUFFER_MS;
    return Math.max(READ_MIN_MS, readMs);
  },

  playAudio(index) {
    this.stopAudio();
    const scene = this.scenes[index];
    this.sceneStartAt = Date.now();
    // 停留 = max(声音时长, 文字阅读, 保底)：有声音时由 onended + 待补延时实现，
    // 无声音时直接按 文字/保底 定时。
    if (!scene.has_audio || !scene.audio) {
      if (this.isAutoPlay && !this.isPaused) {
        const target = Math.max(this.estimateReadingMs(scene), MIN_PER_PAGE_MS);
        this.autoTimer = setTimeout(() => this.next(), target);
      }
      return;
    }

    let audio = this.loadedAudios[index];
    if (!audio) {
      audio = new Audio(scene.audio);
      this.loadedAudios[index] = audio;
    }
    this.audio = audio;

    audio.onended = () => {
      document.getElementById("audio-status").textContent = "🎵 点击播放";
      this.audio = null;
      if (this.isAutoPlay && !this.isPaused) {
        // 声音播完后，若阅读/保底时间还没到，继续等足再翻页
        const elapsed = Date.now() - this.sceneStartAt;
        const target = Math.max(this.estimateReadingMs(scene), MIN_PER_PAGE_MS);
        const remain = target - elapsed;
        this.autoTimer = setTimeout(
          () => this.next(),
          (remain > 0 ? remain : 0) + 800
        );
      }
    };
    // 修复"听不到前几个字"：先重置到 0 并等待 seek 完成，再 play() 且 await。
    // 直接 currentTime=0 后立即 play() 存在异步竞态，Chrome 会偶发从非零位置起播，跳过开头。
    const startPlay = () => {
      const tryPlay = () => {
        audio.currentTime = 0;
        audio.play().then(() => {
          document.getElementById("audio-status").textContent = "🔊 播放中...";
        }).catch(() => {
          document.getElementById("audio-status").textContent = "⚠️ 无法播放";
        });
      };
      if (audio.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA) {
        // 重置到开头并等 seek 完成
        const onSeeked = () => {
          audio.removeEventListener("seeked", onSeeked);
          tryPlay();
        };
        audio.addEventListener("seeked", onSeeked, { once: true });
        audio.currentTime = 0;
        if (audio.seeking === false && audio.currentTime === 0) {
          // 已在开头无需 seek，直接播
          setTimeout(() => {
            audio.removeEventListener("seeked", onSeeked);
            tryPlay();
          }, 0);
        }
      } else {
        audio.addEventListener("canplay", startPlay, { once: true });
      }
    };
    startPlay();
  },

  stopAudio() {
    if (this.audio) {
      this.audio.pause();
      this.audio.currentTime = 0;
      this.audio = null;
    }
    if (this.autoTimer) {
      clearTimeout(this.autoTimer);
      this.autoTimer = null;
    }
  },

  toggleAudio() {
    const scene = this.scenes[this.currentIndex];
    if (!scene.has_audio) return;
    if (this.audio && !this.audio.paused) {
      this.audio.pause();
      document.getElementById("audio-status").textContent = "⏸ 已暂停";
    } else {
      this.playAudio(this.currentIndex);
    }
  },

  prev() {
    if (this.currentIndex > 0) {
      this.stopAudio();
      this.showScene(this.currentIndex - 1);
      if (this.isAutoPlay && !this.isPaused) {
        setTimeout(() => this.playAudio(this.currentIndex), 300);
      }
    }
  },

  next() {
    if (this.currentIndex < this.scenes.length - 1) {
      this.stopAudio();
      this.showScene(this.currentIndex + 1);
      if (this.isAutoPlay && !this.isPaused) {
        setTimeout(() => this.playAudio(this.currentIndex), 300);
      }
    } else if (this.isAutoPlay) {
      this.isAutoPlay = false;
      this.isPaused = false;
      this.stopAudio();
      const btn = document.getElementById("btn-auto");
      btn.textContent = "▶自动";
      btn.classList.remove("active");
      document.getElementById("audio-status").textContent = "播放完毕";
    }
  },

  toggleAutoPlay() {
    const btn = document.getElementById("btn-auto");
    if (this.isAutoPlay) {
      this.isAutoPlay = false;
      this.isPaused = false;
      this.stopAudio();
      btn.textContent = "▶自动";
      btn.classList.remove("active");
      document.getElementById("audio-status").textContent = "";
    } else {
      this.isAutoPlay = true;
      this.isPaused = false;
      btn.textContent = "⏸暂停";
      btn.classList.add("active");
      setTimeout(() => this.playAudio(this.currentIndex), 500);
    }
  },

  async loadWorkflows() {
    try {
      const resp = await fetch("/api/list-workflows", {method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"});
      const data = await resp.json();
      const sel = document.getElementById("workflow-select");
      sel.innerHTML = "";
      for (const [id, info] of Object.entries(data.workflows || {})) {
        const opt = document.createElement("option");
        opt.value = id; opt.textContent = info.name;
        if (id === data.current) opt.selected = true;
        sel.appendChild(opt);
      }
    } catch(e) {}
  },

  async switchWorkflow() {
    const id = document.getElementById("workflow-select").value;
    try {
      const r = await fetch("/api/set-workflow", {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({workflowId: id}),
      });
      const d = await r.json();
      document.getElementById("edit-status").textContent = d.success ? "✅ 已切换: " + id : "❌ 失败";
    } catch(e) {
      document.getElementById("edit-status").textContent = "❌ 网络错误";
    }
  },

  openEdit() {
    const scene = this.scenes[this.currentIndex];
    document.getElementById("edit-img-prompt").value = scene.img_prompt || "";
    const tts = scene.dialogue ? scene.dialogue.map((d) => d.text_cn).join("\n") : "";
    document.getElementById("edit-tts-text").value = tts;
    document.getElementById("edit-status").textContent = "";
    document.getElementById("btn-regen-img").disabled = false;
    document.getElementById("btn-regen-tts").disabled = false;
    document.getElementById("btn-save-text").disabled = false;
    document.getElementById("edit-panel").classList.remove("hidden");
  },

  async regenerateImage() {
    const scene = this.scenes[this.currentIndex];
    const imgPrompt = document.getElementById("edit-img-prompt").value.trim();
    if (!imgPrompt) return;
    const btn = document.getElementById("btn-regen-img");
    btn.disabled = true;
    document.getElementById("edit-status").textContent = "正在重新生成图片...";
    try {
      const resp = await fetch("/api/regenerate-image", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sceneId: scene.id, imgPrompt }),
      });
      const result = await resp.json();
      if (result.success) {
        scene.image = result.imagePath;
        document.getElementById("comic-image").src = scene.image + "?" + Date.now();
        document.getElementById("edit-status").textContent = "✅ 图片已更新";
      } else {
        document.getElementById("edit-status").textContent = "❌ " + (result.error || "生成失败");
      }
    } catch (e) {
      document.getElementById("edit-status").textContent = "❌ 网络错误: " + e.message;
    }
    btn.disabled = false;
  },

  async regenerateTTS() {
    const scene = this.scenes[this.currentIndex];
    const ttsText = document.getElementById("edit-tts-text").value.trim();
    if (!ttsText) return;
    const btn = document.getElementById("btn-regen-tts");
    btn.disabled = true;
    document.getElementById("edit-status").textContent = "正在重新生成语音...";
    try {
      const resp = await fetch("/api/regenerate-tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sceneId: scene.id, ttsText }),
      });
      const result = await resp.json();
      if (result.success) {
        scene.audio = result.audioPath;
        document.getElementById("edit-status").textContent = "✅ 语音已更新";
      } else {
        document.getElementById("edit-status").textContent = "❌ " + (result.error || "生成失败");
      }
    } catch (e) {
      document.getElementById("edit-status").textContent = "❌ 网络错误: " + e.message;
    }
    btn.disabled = false;
  },

  async saveTextEdit() {
    const resp = await fetch("/api/save-edits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sceneId: this.scenes[this.currentIndex].id,
        imgPrompt: document.getElementById("edit-img-prompt").value.trim(),
        ttsTexts: document.getElementById("edit-tts-text").value.split("\n").filter((l) => l.trim()),
      }),
    });
    const result = await resp.json();
    document.getElementById("edit-status").textContent = result.success ? "✅ 已保存到切片文件" : "❌ 保存失败";
  },
};

document.addEventListener("DOMContentLoaded", () => ComicApp.init());
