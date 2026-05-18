import streamlit as st
import tempfile
from io import BytesIO
import subprocess
from openai import OpenAI
import shutil
import os
import re
from moviepy import VideoFileClip, TextClip, CompositeVideoClip

# --- 1. WERYFIKACJA I INTERFEJS KLUCZA API OPENAI ---
# Sprawdzenie, czy klucz jest już zapisany w stanie sesji
if not st.session_state.get("openai_api_key"):
    # Najpierw szukamy klucza w bezpiecznych sekretach (lokalnie lub w Streamlit Cloud)
    if "OPENAI_API_KEY" in st.secrets:
        st.session_state["openai_api_key"] = st.secrets["OPENAI_API_KEY"]
    else:
        # Jeśli klucza nie ma w sekretach, prosimy użytkownika o wpisanie go ręcznie
        st.info("Dodaj swój klucz API OpenAI, aby móc korzystać z tej aplikacji")
        user_key = st.text_input("Klucz API", type="password")
        if user_key:
            st.session_state["openai_api_key"] = user_key
            st.rerun()

# Blokada aplikacji, dopóki klucz nie zostanie dostarczony
if not st.session_state.get("openai_api_key"):
    st.stop()


# --- 2. KONFIGURACJA KLIENTA I MODELU ---
@st.cache_resource
def get_openai_client():
    # Używamy klucza zweryfikowanego i zapisanego w st.session_state
    return OpenAI(api_key=st.session_state["openai_api_key"])

AUDIO_TRANSCRIBE_MODEL = "whisper-1"


# --- 3. FUNKCJE PRZETWARZANIA WIDEO I AUDIO ---
def transcribe_audio(audio_path):
    openai_client = get_openai_client()
    with open(audio_path, "rb") as audio_file:
        transcript = openai_client.audio.transcriptions.create(
            file=audio_file,
            model=AUDIO_TRANSCRIBE_MODEL,
            response_format="srt",
        )
    return transcript


def add_subtitles_to_video(video_path, srt_content, output_path):
    def srt_time_to_seconds(srt_time_str):
        srt_time_str = srt_time_str.replace(",", ".")
        match = re.match(r"(\d+):(\d+):(\d+\.\d+|\d+)", srt_time_str)
        if match:
            h, m, s = map(float, match.groups())
            return h * 3600 + m * 60 + s
        return 0.0

    video = VideoFileClip(video_path)
    blocks = srt_content.strip().split("\n\n")
    subtitle_clips = []

    # Pozycja napisu w pionie (ok. 240 pikseli od dołu, idealna dla 2 linii)
    text_position_y = video.h - 240

    for block in blocks:
        lines = block.split("\n")
        if len(lines) >= 3:
            time_line = lines[1]
            text_content = " ".join(lines[2:]).strip()

            times = re.findall(r"\d{2}:\d{2}:\d{2}[,\.]\d{3}", time_line)
            if len(times) == 2:
                start_sec = srt_time_to_seconds(times[0])
                end_sec = srt_time_to_seconds(times[1])
                duration = end_sec - start_sec

                if duration > 0:
                    # 1. WYMUSZENIE DOKŁADNIE 2 LINII TEKSTU (Dzielenie zdania równo na pół)
                    words = text_content.split()

                    if len(words) > 1:
                        half = len(text_content) // 2

                        current_len = 0
                        split_index = 0

                        for i, word in enumerate(words):
                            current_len += len(word) + 1
                            if current_len >= half:
                                split_index = i + 1
                                break

                        line1 = " ".join(words[:split_index])
                        line2 = " ".join(words[split_index:])

                        formatted_text = f"{line1}\n{line2}"
                    else:
                        formatted_text = text_content

                    # 2. DODANIE SPACJI OCHRONNYCH DO KAŻDEJ Z LINII (zapobiega ścinaniu krawędzi liter)
                    safe_lines = [f" {line.strip()} " for line in formatted_text.split("\n")]
                    final_text = "\n".join(safe_lines)

                    # 3. BARDZO SZEROKI KONTENER (98% ekranu) - blokuje przypadkowe przeskoki do 3 linii przy grubej czcionce
                    container_size = (int(video.w * 1.2), None)

                    # 4. JEDEN KLIP: Pogrubiona czcionka (Bold) z mocnym konturem
                    txt_clip = (
                        TextClip(
                            text=final_text,      
                            font_size=42,           
                            color='white',
                            font='Arial Black.ttf',      # ZMIANA: Pogrubiona wersja czcionki Arial
                            size=container_size,
                            text_align='center', 
                            stroke_color='black',   # Czarny, wyraźny kontur
                            stroke_width=5,       # ZMIANA: Zwiększona grubość obrysu dla lepszego kontrastu
                            method='caption' 
                        )
                        .with_start(start_sec)       
                        .with_duration(duration)    
                        .with_position(('center', text_position_y))
                    )
                    
                    subtitle_clips.append(txt_clip)

    final_video = CompositeVideoClip([video] + subtitle_clips)
    final_video.write_videofile(
        output_path, 
        codec="libx264", 
        audio_codec="aac",
        temp_audiofile="temp-audio.m4a", 
        remove_temp=True
    )
    video.close()
    final_video.close()








# --- 4. INICJALIZACJA STANÓW SESJI INTERFEJSU ---
if "note_audio_text" not in st.session_state:
    st.session_state["note_audio_text"] = ""
if "last_uploaded_file" not in st.session_state:
    st.session_state["last_uploaded_file"] = None
if "audio_ready" not in st.session_state:
    st.session_state["audio_ready"] = False
if "video_rendered" not in st.session_state:
    st.session_state["video_rendered"] = False


# --- 5. INTERFEJS UŻYTKOWNIKA ---
st.title("Generator napisów App")

uploaded_file = st.file_uploader(
    "Dodaj wideo",
    type=["mp4", "mov", "avi", "mkv"]
)

if uploaded_file is not None:
    if uploaded_file.name != st.session_state["last_uploaded_file"]:
        st.session_state["note_audio_text"] = ""
        st.session_state["last_uploaded_file"] = uploaded_file.name
        st.session_state["audio_ready"] = False
        st.session_state["video_rendered"] = False

        if os.path.exists("audio.mp3"):
            os.remove("audio.mp3")
        if os.path.exists("video_with_subtitles.mp4"):
            os.remove("video_with_subtitles.mp4")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_video:
        file_bytes = uploaded_file.getvalue()
        tmp_video.write(file_bytes)
        video_path = tmp_video.name

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.video(video_path)

    audio_path = "audio.mp3"
    command = [
        "ffmpeg", "-i", video_path, "-q:a", "0", "-map", "a", audio_path, "-y"
    ]

    if not st.session_state["audio_ready"]:
        progress_bar = st.progress(0)
        process = subprocess.Popen(command)
        for percent in range(100):
            import time
            time.sleep(0.05)
            progress_bar.progress(percent + 1)
        process.wait()
        st.session_state["audio_ready"] = True
        st.toast("Audio extracted!")

    st.audio(audio_path)

    if st.button("Generuj napisy"):
        with st.spinner("Transcribing audio..."):
            st.session_state["note_audio_text"] = transcribe_audio(audio_path)
        st.success("Napisy wygenerowane!")

    tab1, tab2 = st.tabs(["Napisy", "Wideo z napisami"])

    with tab1:
        if st.session_state["note_audio_text"]:
            st.text_area("napisy SRT", value=st.session_state["note_audio_text"], height=400)
            st.download_button(
                "Pobierz napisy",
                st.session_state["note_audio_text"],
                file_name="subtitles.srt",
                mime="text/plain"
            )

    with tab2:
        if st.button("Generuj wideo z napisami"):
            if not st.session_state["note_audio_text"]:
                st.error("Najpierw wygeneruj napisy w pierwszej zakładce!")
            else:
                output_video_path = "video_with_subtitles.mp4"
                with st.status("Rozpoczynanie renderowania...", expanded=True) as status:
                    try:
                        status.update(label="Trwa nakładanie napisów...", state="running")
                        add_subtitles_to_video(video_path, st.session_state["note_audio_text"], output_video_path)
                        st.session_state["video_rendered"] = True
                        status.update(label="Wideo wygenerowane pomyślnie!", state="complete", expanded=False)
                        st.toast("Video ready!", icon="🎬")
                    except Exception as e:
                        status.update(label="Wystąpił błąd podczas generowania", state="error")
                        st.error(f"Wystąpił błąd: {e}")

        if st.session_state["video_rendered"] and os.path.exists("video_with_subtitles.mp4"):
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                st.video("video_with_subtitles.mp4")
            with open("video_with_subtitles.mp4", "rb") as file:
                st.download_button(
                    "Pobierz wideo",
                    file,
                    file_name="video_with_subtitles.mp4",
                    mime="video/mp4"
                )
