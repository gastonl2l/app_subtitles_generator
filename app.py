import streamlit as st
import tempfile
from io import BytesIO
import subprocess
from openai import OpenAI
import shutil
import os
import re
import subprocess





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

# def 
def srt_time_to_seconds(t):
    t = t.replace(",", ".")
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)

# rozmiar wideo
def get_video_ratio(video_path):

    command = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        video_path
    ]

    result = subprocess.check_output(command).decode().strip()

    width, height = map(int, result.split("x"))

    return width, height


# def 2 line
def force_two_lines(text, max_chars=42):

    words = text.split()

    lines = []
    current_line = ""

    for w in words:

        test_line = current_line + " " + w

        if len(test_line.strip()) <= max_chars:
            current_line = test_line

        else:
            lines.append(current_line.strip())
            current_line = w

    if current_line:
        lines.append(current_line.strip())

    return "\n".join(lines)

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

    srt_path = "subs.srt"
    
    #rozmiar wideo
    width, height = get_video_ratio(video_path)

    ratio = width / height


    # SHORTS
    if ratio < 0.8:

        subtitle_style = (
            "Fontsize=13,"
            "Bold=1,"
            "BorderStyle=1,"
            "Shadow=1.5,"
            "BackColour=&H80000000,"
            "Alignment=2,"
            "MarginV=40,"
            "WrapStyle=0"
        )

        max_chars = 28

    
    # NORMAL VIDEO
    else:

        subtitle_style = (
            "Fontsize=20,"
            "Bold=1,"
            "BorderStyle=1,"
            "Shadow=1.5,"
            "BackColour=&H80000000,"
            "Alignment=2,"
            "MarginV=40,"
            "WrapStyle=0"
        )

        max_chars = 60


  

    blocks = srt_content.strip().split("\n\n")
    new_blocks = []

    for block in blocks:
        lines = block.split("\n")

        if len(lines) < 3:
            continue

        header = "\n".join(lines[:2])
        text = " ".join(lines[2:]).strip()

        text = force_two_lines(text, max_chars=max_chars)

        new_blocks.append(header + "\n" + text)

    final_srt = "\n\n".join(new_blocks)

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(final_srt)



    command = [
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-vf",
        f"subtitles=subs.srt:charenc=UTF-8:force_style='{subtitle_style}'",
        #"subtitles=subs.srt:charenc=UTF-8:force_style='Fontsize=13,Bold=1,BorderStyle=1,Shadow=1.5,BackColour=&H80000000,Alignment=2,MarginV=40,WrapStyle=0'",
        "-c:a",
        "copy",
        output_path
]

    subprocess.run(command, check=True)












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


        