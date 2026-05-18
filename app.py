import streamlit as st
import tempfile
import subprocess
import os
import re

from openai import OpenAI
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip
from PIL import Image, ImageDraw, ImageFont


# =========================
# OPENAI SETUP
# =========================

if not st.session_state.get("openai_api_key"):
    if "OPENAI_API_KEY" in st.secrets:
        st.session_state["openai_api_key"] = st.secrets["OPENAI_API_KEY"]
    else:
        st.info("Dodaj klucz OpenAI")
        key = st.text_input("API KEY", type="password")
        if key:
            st.session_state["openai_api_key"] = key
            st.rerun()

if not st.session_state.get("openai_api_key"):
    st.stop()


@st.cache_resource
def get_openai_client():
    return OpenAI(api_key=st.session_state["openai_api_key"])


# =========================
# TRANSCRIBE
# =========================

def transcribe_audio(audio_path):
    client = get_openai_client()
    with open(audio_path, "rb") as f:
        return client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="srt"
        )


# =========================
# SRT PARSER
# =========================

def srt_time_to_seconds(t):
    t = t.replace(",", ".")
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


# =========================
# SUBTITLE IMAGE (PIL)
# =========================

def create_subtitle_image(text, video_width):

    font = ImageFont.truetype("arialbd.ttf", 42)
    max_width = int(video_width * 0.85)

    words = text.split()

    line1, line2 = "", ""

    for w in words:
        test = (line1 + " " + w).strip()
        if font.getlength(test) < max_width:
            line1 = test
        else:
            line2 += " " + w

    line1, line2 = line1.strip(), line2.strip()
    final_text = line1 + "\n" + line2

    dummy = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy)

    bbox = draw.multiline_textbbox((0, 0), final_text, font=font, stroke_width=4)

    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    padding = 50

    img = Image.new("RGBA", (w + padding * 2, h + padding * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.multiline_text(
        (padding, padding),
        final_text,
        font=font,
        fill="white",
        stroke_fill="black",
        stroke_width=4,
        align="center"
    )

    return img


# =========================
# MAIN RENDER FUNCTION
# =========================

def add_subtitles_to_video(video_path, srt_content, output_path):

    video = VideoFileClip(video_path)
    subtitle_clips = []

    blocks = srt_content.strip().split("\n\n")

    for block in blocks:
        lines = block.split("\n")

        if len(lines) < 3:
            continue

        time_line = lines[1]
        text = " ".join(lines[2:]).strip()

        times = re.findall(r"\d{2}:\d{2}:\d{2}[,\.]\d{3}", time_line)

        if len(times) != 2:
            continue

        start = srt_time_to_seconds(times[0])
        end = srt_time_to_seconds(times[1])
        duration = end - start

        if duration <= 0:
            continue

        img = create_subtitle_image(text, video.w)

        tmp_path = f"tmp_{start}.png"
        img.save(tmp_path)

        clip = (
            ImageClip(tmp_path)
            .set_start(start)
            .set_duration(duration)
            .set_position(("center", int(video.h * 0.72)))
        )

        subtitle_clips.append(clip)

    final = CompositeVideoClip([video] + subtitle_clips)
    final.write_videofile(output_path, fps=video.fps)


# =========================
# STREAMLIT UI
# =========================

st.title("🎬 Subtitle Generator")

uploaded_file = st.file_uploader("Upload video", type=["mp4", "mov", "avi"])

if uploaded_file:

    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
        tmp.write(uploaded_file.getvalue())
        video_path = tmp.name

    st.video(video_path)

    audio_path = "audio.mp3"

    if st.button("Extract audio"):
        subprocess.run([
            "ffmpeg", "-i", video_path,
            "-q:a", "0", "-map", "a",
            audio_path, "-y"
        ])
        st.success("Audio extracted")

    if st.button("Generate subtitles"):
        srt = transcribe_audio(audio_path)
        st.session_state["srt"] = srt
        st.success("Subtitles ready")

    if "srt" in st.session_state:
        st.text_area("SRT", st.session_state["srt"], height=300)

        if st.button("Render video with subtitles"):
            output = "output.mp4"

            add_subtitles_to_video(
                video_path,
                st.session_state["srt"],
                output
            )

            st.video(output)

            with open(output, "rb") as f:
                st.download_button(
                    "Download video",
                    f,
                    file_name="video_with_subs.mp4"
                )