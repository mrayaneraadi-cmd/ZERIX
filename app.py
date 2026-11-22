import os
import re
import subprocess
import tempfile
import json
from pathlib import Path

import streamlit as st
import imageio_ffmpeg
from faster_whisper import WhisperModel

# ----------------------------- PAGE SETUP -----------------------------
st.set_page_config(
    page_title="ZERIX | Gaming Shorts Studio",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
    html, body, [class*="css"] { font-family: Inter, sans-serif; }
    .stApp {
        background: radial-gradient(circle at 8% 0%, rgba(124,58,237,.18), transparent 28%),
                    radial-gradient(circle at 95% 8%, rgba(14,165,233,.10), transparent 25%), #090914;
        color: #f5f3ff;
    }
    [data-testid="stSidebar"] { background: linear-gradient(180deg, #111025 0%, #0b0b18 100%); border-right: 1px solid rgba(167,139,250,.18); }
    .hero { padding: 26px 28px; border: 1px solid rgba(167,139,250,.25); border-radius: 22px;
            background: linear-gradient(120deg, rgba(91,33,182,.28), rgba(15,23,42,.78));
            box-shadow: 0 18px 55px rgba(0,0,0,.22); margin-bottom: 22px; }
    .brand { font-size: 13px; letter-spacing: 4px; font-weight: 900; color: #c4b5fd; margin-bottom: 8px; }
    .hero h1 { font-size: clamp(32px, 5vw, 54px); line-height: 1.05; margin: 0; font-weight: 900; }
    .hero p { color: #c4c2d6; margin: 12px 0 0; font-size: 15px; }
    .pill { display:inline-block; padding:6px 11px; border-radius:999px; background:rgba(139,92,246,.16);
            border:1px solid rgba(167,139,250,.28); color:#ddd6fe; font-size:12px; font-weight:700; margin: 0 5px 5px 0; }
    .section-title { font-size:19px; font-weight:800; margin: 12px 0 8px; }
    .muted { color:#aaa8c2; font-size:13px; }
    div[data-testid="stMetric"] { background: rgba(22,20,43,.72); border:1px solid rgba(167,139,250,.18); padding: 14px 16px; border-radius: 16px; }
    div.stButton > button, div.stDownloadButton > button { border-radius: 12px; border: 1px solid rgba(196,181,253,.35);
        background: linear-gradient(100deg,#7c3aed,#5b21b6); color:white; font-weight:800; min-height:46px; }
    div.stButton > button:hover, div.stDownloadButton > button:hover { border-color:#c4b5fd; color:white; filter:brightness(1.12); }
    .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] { background:#151429 !important; }
    hr { border-color: rgba(167,139,250,.16); }
    </style>
    """,
    unsafe_allow_html=True,
)

# ----------------------------- HELPERS -----------------------------
@st.cache_resource
def load_whisper(model_name: str):
    return WhisperModel(model_name, device="cpu", compute_type="int8")


def run_command(command, timeout=None):
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        detail = result.stderr[-3500:] if result.stderr else "Unknown FFmpeg error"
        raise RuntimeError(detail)
    return result


def get_duration(video_path: str, ffmpeg: str) -> float:
    result = subprocess.run(
        [ffmpeg, "-i", video_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        raise RuntimeError("Ma9drtch n9ra durée dyal video. Jarrab fichier MP4 akhor.")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def find_best_scene(video_path: str, ffmpeg: str) -> float:
    result = subprocess.run(
        [ffmpeg, "-i", video_path, "-vf", "select='gt(scene,0.20)',metadata=print", "-an", "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    matches = re.findall(r"pts_time:([0-9.]+).*?lavfi\.scene_score=([0-9.]+)", result.stderr or "", flags=re.S)
    if not matches:
        return 0.0
    best_time, _score = max(matches, key=lambda item: float(item[1]))
    return float(best_time)


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def clean_ass_text(value: str) -> str:
    return str(value).replace("\\", "").replace("{", "").replace("}", "").replace("\n", " ").strip()


def srt_stamp(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def make_srt(segment_data, edited_lines=None) -> str:
    blocks = []
    for index, item in enumerate(segment_data, start=1):
        text = edited_lines[index - 1] if edited_lines is not None and index - 1 < len(edited_lines) else item["text"]
        if not text.strip():
            continue
        blocks.append(f"{index}\n{srt_stamp(item['start'])} --> {srt_stamp(item['end'])}\n{text.strip()}")
    return "\n\n".join(blocks)


def make_ass(segment_data, edited_lines, font_size, active_color, normal_color, position, word_mode):
    alignment = {"Bottom": 2, "Middle": 5, "Top": 8}[position]
    margin_v = 58 if position == "Bottom" else 0
    colors = {
        "White": "&H00FFFFFF", "Yellow": "&H0000D7FF", "Purple": "&H00E5A0FF",
        "Cyan": "&H00FFFF55", "Green": "&H0000FF80",
    }
    primary = colors.get(active_color, "&H0000D7FF")
    secondary = colors.get(normal_color, "&H00FFFFFF")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},{primary},{secondary},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,{alignment},42,42,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogues = []
    for index, item in enumerate(segment_data):
        edited = edited_lines[index] if index < len(edited_lines) else item["text"]
        if not edited.strip():
            continue
        original_same = edited.strip() == item["text"].strip()
        words = item.get("words", [])
        text_line = clean_ass_text(edited)
        if word_mode and original_same and words:
            pieces = []
            for word in words:
                token = clean_ass_text(word.get("word", ""))
                if not token:
                    continue
                duration = max(1, int(round(max(0.06, word["end"] - word["start"]) * 100)))
                pieces.append(r"{\k" + str(duration) + "}" + token)
            if pieces:
                text_line = " ".join(pieces)
        if text_line:
            dialogues.append(
                f"Dialogue: 0,{ass_time(item['start'])},{ass_time(max(item['end'], item['start'] + 0.12))},Default,,0,0,0,,{text_line}"
            )
    return header + "\n".join(dialogues) + "\n"


def ffmpeg_subtitle_path(path: str) -> str:
    return str(Path(path).resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def render_subtitles(clean_video_path, output_path, ass_path, ffmpeg):
    filter_path = ffmpeg_subtitle_path(ass_path)
    run_command([
        ffmpeg, "-y", "-i", clean_video_path, "-vf", f"subtitles=filename='{filter_path}'",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "copy",
        "-movflags", "+faststart", output_path,
    ], timeout=1800)

# ----------------------------- SIDEBAR -----------------------------
with st.sidebar:
    st.markdown("## ⚡ ZERIX")
    st.caption("GAMING SHORTS STUDIO")
    st.markdown("---")
    st.markdown("### 🧠 AI engine")
    model_choice = st.selectbox("Whisper model", ["tiny", "base"], index=0,
        help="Tiny أسرع على PC ديالك. Base يقدر يعطي transcription أحسن ولكن أبطأ.")
    st.markdown("### 🎨 Subtitle design")
    subtitle_mode = st.selectbox("Subtitle animation", ["Word-by-word highlight", "Classic sentence subtitles"], index=0)
    active_color = st.selectbox("Active word color", ["Yellow", "Purple", "Cyan", "Green"], index=0)
    normal_color = st.selectbox("Other words color", ["White", "Yellow", "Purple"], index=0)
    font_size = st.slider("Subtitle size", 20, 48, 32, 2)
    position = st.selectbox("Subtitle position", ["Bottom", "Middle", "Top"], index=0)
    st.markdown("---")
    st.caption("ZERIX runs locally on your PC. Processing speed depends on your computer.")

# ----------------------------- MAIN UI -----------------------------
st.markdown(
    """
    <div class="hero">
      <div class="brand">ZERIX / CREATOR TOOLS</div>
      <h1>Turn gameplay into<br><span style="color:#c4b5fd">viral-style Shorts.</span></h1>
      <p>Upload your gameplay. ZERIX cuts a vertical clip, creates AI subtitles and lets you edit before export.</p>
      <div style="margin-top:16px">
        <span class="pill">⚡ 9:16 Vertical</span><span class="pill">🎙️ AI Captions</span>
        <span class="pill">🎯 Scene Detection</span><span class="pill">🛠️ Subtitle Editor</span><span class="pill">📥 MP4 Export</span>
      </div>
    </div>
    """, unsafe_allow_html=True,
)

metric1, metric2, metric3 = st.columns(3)
metric1.metric("VIDEO FORMAT", "9:16", "Shorts / Reels / TikTok")
metric2.metric("AI SUBTITLES", "Whisper", "Editable captions")
metric3.metric("EXPORT", "MP4", "Preview before download")

left, right = st.columns([1.12, 0.88], gap="large")
with left:
    st.markdown('<div class="section-title">🎮 01 — Upload your gameplay</div>', unsafe_allow_html=True)
    uploaded_file = st.file_uploader("Choose a video", type=["mp4", "mov", "mkv"], help="Best result: gameplay with clear audio.")
    if uploaded_file:
        st.video(uploaded_file)
        st.success(f"Video ready: {uploaded_file.name}")
with right:
    st.markdown('<div class="section-title">✂️ 02 — Short settings</div>', unsafe_allow_html=True)
    duration_choice = st.selectbox("Short duration", [15, 30, 45, 60], index=1)
    edit_mode = st.radio("Choose clip source", ["Auto Highlights", "Start of video"],
        help="Auto Highlights selects a strong visual scene change. It does not understand kills or wins semantically.")
    language_choice = st.selectbox("Spoken language", ["Auto Detect", "English", "French", "Arabic"], index=0)
    burn_subtitles = st.toggle("Burn subtitles into the video", value=True)
    st.caption("Tip: ila kan audio wad7, Whisper y9der ykhrrej subtitles a7san.")

st.markdown("---")
st.markdown('<div class="section-title">🚀 03 — Create your Short</div>', unsafe_allow_html=True)

if st.button("⚡ GENERATE MY ZERIX SHORT", type="primary", use_container_width=True, disabled=(uploaded_file is None)):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    language_map = {"Auto Detect": None, "English": "en", "French": "fr", "Arabic": "ar"}
    try:
        progress = st.progress(0, text="Preparing your video...")
        status = st.empty()
        work_dir = tempfile.mkdtemp(prefix="zerix_")
        source_path = os.path.join(work_dir, uploaded_file.name)
        with open(source_path, "wb") as file:
            file.write(uploaded_file.getbuffer())
        total_duration = get_duration(source_path, ffmpeg)
        clip_duration = min(float(duration_choice), total_duration)
        if edit_mode == "Auto Highlights":
            status.info("🔎 Searching for a strong scene change...")
            progress.progress(10, text="Analyzing scenes...")
            scene_time = find_best_scene(source_path, ffmpeg)
            start_time = max(0.0, scene_time - clip_duration * 0.35)
            start_time = min(start_time, max(0.0, total_duration - clip_duration))
        else:
            start_time = 0.0

        short_path = os.path.join(work_dir, "zerix_short.mp4")
        status.info("✂️ Creating vertical 9:16 video...")
        progress.progress(25, text="Cropping and rendering video...")
        run_command([
            ffmpeg, "-y", "-ss", f"{start_time:.3f}", "-i", source_path, "-t", f"{clip_duration:.3f}",
            "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
            "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", short_path,
        ], timeout=1800)

        status.info("🎙️ AI is listening and creating subtitles...")
        progress.progress(42, text="Loading Whisper model...")
        model = load_whisper(model_choice)
        segments_iter, info = model.transcribe(
            short_path, language=language_map[language_choice], vad_filter=True,
            beam_size=5 if model_choice == "base" else 3,
            condition_on_previous_text=False,
            word_timestamps=(subtitle_mode == "Word-by-word highlight"),
        )
        segments = list(segments_iter)
        if not segments:
            raise RuntimeError("Whisper ma l9ach speech واضح فهاد الفيديو. T2ekked belli l'audio kayn.")

        segment_data = []
        for segment in segments:
            word_data = []
            for word in (getattr(segment, "words", None) or []):
                if word.start is not None and word.end is not None and clean_ass_text(word.word):
                    word_data.append({"start": float(word.start), "end": float(word.end), "word": str(word.word)})
            segment_data.append({"start": float(segment.start), "end": float(segment.end), "text": segment.text.strip(), "words": word_data})

        srt_text = make_srt(segment_data)
        srt_path = os.path.join(work_dir, "zerix_subtitles.srt")
        with open(srt_path, "w", encoding="utf-8-sig") as file:
            file.write(srt_text)

        ass_path = os.path.join(work_dir, "zerix_subtitles.ass")
        initial_lines = [item["text"] for item in segment_data]
        final_path = short_path
        if burn_subtitles:
            ass_text = make_ass(segment_data, initial_lines, font_size, active_color, normal_color, position,
                                subtitle_mode == "Word-by-word highlight")
            with open(ass_path, "w", encoding="utf-8") as file:
                file.write(ass_text)
            final_path = os.path.join(work_dir, "ZERIX_FINAL.mp4")
            status.info("✨ Styling subtitles and rendering final video...")
            progress.progress(72, text="Burning subtitles...")
            render_subtitles(short_path, final_path, ass_path, ffmpeg)

        st.session_state["zerix_result"] = {
            "work_dir": work_dir, "short_path": short_path, "final_path": final_path,
            "ass_path": ass_path, "srt_path": srt_path, "segments": segment_data,
            "initial_lines": initial_lines, "language": getattr(info, "language", "unknown"),
            "duration": clip_duration, "subtitle_mode": subtitle_mode, "burned": burn_subtitles,
            "font_size": font_size, "active_color": active_color, "normal_color": normal_color, "position": position,
        }
        progress.progress(100, text="Your ZERIX Short is ready!")
        status.success("🔥 ZERIX finished successfully!")
    except Exception as error:
        st.error("Ma t9drnach nkemlo generation. Hadi hiya l'erreur:")
        st.code(str(error))
        st.info("Ila kan l'erreur dyal FFmpeg, sft liya screenshot dyalha bach nصلحوها.")

# ----------------------------- PREVIEW + SUBTITLE EDITOR -----------------------------
result = st.session_state.get("zerix_result")
if result:
    st.markdown("---")
    st.markdown('<div class="section-title">🎬 04 — Live Preview</div>', unsafe_allow_html=True)
    info1, info2, info3 = st.columns(3)
    info1.metric("Duration", f"{result['duration']:.0f}s")
    info2.metric("Detected language", str(result["language"]).upper())
    info3.metric("Subtitle mode", "Word highlight" if "Word-by-word" in result["subtitle_mode"] else "Classic")

    preview_left, preview_right = st.columns(2, gap="large")
    with preview_left:
        st.markdown("**Clean preview — without subtitles**")
        st.video(result["short_path"])
    with preview_right:
        st.markdown("**Current final preview**")
        current_preview = result["final_path"] if result["burned"] else result["short_path"]
        st.video(current_preview)

    st.markdown("---")
    st.markdown('<div class="section-title">📝 05 — Subtitle Editor</div>', unsafe_allow_html=True)
    st.caption("كل سطر كيمثل جملة من الترجمة. بدّل النصوص وصحّح الأخطاء، وخلي كل جملة فالسطر ديالها.")
    editor_key = f"zerix_editor_{result['work_dir']}"
    if editor_key not in st.session_state:
        st.session_state[editor_key] = "\n".join(result["initial_lines"])
    edited_text = st.text_area("Edit your subtitles", key=editor_key, height=240,
                               help="Matbdelch ترتيب السطور باش يبقى التوقيت مضبوط.")
    edited_lines = edited_text.splitlines()
    if len(edited_lines) != len(result["segments"]):
        st.warning(f"خاص يكون عندك {len(result['segments'])} سطر بالضبط، كل سطر مقابل لجملة واحدة. دابا عندك {len(edited_lines)}.")
    else:
        if st.button("✨ APPLY CHANGES & UPDATE PREVIEW", use_container_width=True):
            try:
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
                ass_text = make_ass(result["segments"], edited_lines, result["font_size"], result["active_color"],
                                    result["normal_color"], result["position"],
                                    "Word-by-word" in result["subtitle_mode"])
                with open(result["ass_path"], "w", encoding="utf-8") as file:
                    file.write(ass_text)
                edited_srt = make_srt(result["segments"], edited_lines)
                with open(result["srt_path"], "w", encoding="utf-8-sig") as file:
                    file.write(edited_srt)
                edited_final = os.path.join(result["work_dir"], "ZERIX_EDITED_FINAL.mp4")
                render_subtitles(result["short_path"], edited_final, result["ass_path"], ffmpeg)
                result["final_path"] = edited_final
                result["burned"] = True
                result["edited_lines"] = edited_lines
                st.session_state["zerix_result"] = result
                st.success("Subtitles updated! Preview and downloads are ready.")
                st.rerun()
            except Exception as error:
                st.error("Ma9drnach nطبقو التعديلات:")
                st.code(str(error))

    st.markdown("---")
    st.markdown('<div class="section-title">📥 06 — Export your Short</div>', unsafe_allow_html=True)
    final_download = result["final_path"] if result["burned"] else result["short_path"]
    with open(final_download, "rb") as file:
        final_bytes = file.read()
    with open(result["short_path"], "rb") as file:
        clean_bytes = file.read()
    with open(result["srt_path"], "rb") as file:
        srt_bytes = file.read()
    d1, d2, d3 = st.columns(3)
    d1.download_button("⬇️ Download FINAL MP4", data=final_bytes, file_name="ZERIX_FINAL.mp4", mime="video/mp4", use_container_width=True)
    d2.download_button("⬇️ Download CLEAN MP4", data=clean_bytes, file_name="ZERIX_CLEAN.mp4", mime="video/mp4", use_container_width=True)
    d3.download_button("⬇️ Download subtitles SRT", data=srt_bytes, file_name="ZERIX_SUBTITLES.srt", mime="text/plain", use_container_width=True)
    st.caption("Auto Highlights كيعتمد على تغيّر المشهد فالصورة، ماشي على فهم AI للأحداث بحال kill أو victory.")
else:
    st.markdown('<div class="muted">Your finished video, live preview and subtitle editor will appear here after generation.</div>', unsafe_allow_html=True)

st.markdown("---")
st.markdown('<div style="text-align:center;color:#77758f;font-size:12px;padding:8px 0">ZERIX STUDIO • Built for creators • Local processing on your PC</div>', unsafe_allow_html=True)
