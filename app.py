import streamlit as st
import tempfile
from io import BytesIO
import subprocess
from openai import OpenAI
import shutil
import os
import re






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


# def start speech
def detect_speech_start(audio_path):
    command = [
        "ffmpeg",
        "-i", audio_path,
        "-af", "silencedetect=noise=-30dB:d=0.5",
        "-f", "null",
        "-"
    ]

    result = subprocess.run(command, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    logs = result.stderr

    silence_ends = re.findall(r"silence_end: ([0-9.]+)", logs)

    if silence_ends:
        return float(silence_ends[0])  # pierwszy moment mowy

    return 0.0

# time syncro
def shift_time(time_str, offset):
    h, m, s = time_str.replace(",", ".").split(":")
    total = int(h)*3600 + int(m)*60 + float(s)

    total += offset

    h = int(total // 3600)
    m = int((total % 3600) // 60)
    s = total % 60

    return f"{h:02}:{m:02}:{s:06.3f}".replace(".", ",")


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

    if "x" not in result:
        return 9, 16  # fallback dla Shorts (bez crasha)

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

# def
def transcribe_audio(audio_path):
    openai_client = get_openai_client()
    with open(audio_path, "rb") as audio_file:
        transcript = openai_client.audio.transcriptions.create(
            file=audio_file,
            model=AUDIO_TRANSCRIBE_MODEL,
            response_format="srt",
        )
    return transcript

# --- 3. FUNKCJE PRZETWARZANIA WIDEO I AUDIO ---
def add_subtitles_to_video(video_path, srt_content, output_path):
    offset = st.session_state.get("speech_offset", 0.0)

    width, height = get_video_ratio(video_path)
    ratio = width / height

    srt_path = os.path.join(tempfile.gettempdir(), "subs.srt")

    # SHORTS
    if ratio < 0.8:
        subtitle_style = (
            "Fontsize=13,"
            "Bold=1,"
            "BorderStyle=1,"
            "Shadow=1,"
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
            "Shadow=1,"
            "BackColour=&H80000000,"
            "Alignment=2,"
            "MarginV=40,"
            "WrapStyle=0"
        )
        max_chars = 60

    blocks = srt_content.strip().split("\n\n")
    new_blocks = []

    # Licznik bloków – poprawny format SRT wymaga unikalnego numeru nad czasem
    for block_idx, block in enumerate(blocks, start=1):
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if len(lines) < 2:
            continue

        # Szukamy linii, która faktycznie zawiera separator czasu " --> "
        time_line_idx = -1
        for idx, line in enumerate(lines):
            if " --> " in line:
                time_line_idx = idx
                break

        if time_line_idx == -1:
            continue

        try:
            start, end = lines[time_line_idx].split(" --> ")
        except ValueError:
            continue

        start = shift_time(start, offset)
        end = shift_time(end, offset)

        header = f"{start} --> {end}"
        
        # Wszystkie linie poniżej znacznika czasu to tekst napisów
        text_lines = lines[time_line_idx + 1:]
        text = " ".join(text_lines).strip()
        text = force_two_lines(text, max_chars=max_chars)

        # Składamy poprawny blok SRT: Indeks, Czas, Tekst
        new_blocks.append(f"{block_idx}\n{header}\n{text}")

    final_srt = "\n\n".join(new_blocks)

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(final_srt)

    # Na Linuxie (Debian) nie uciekamy dwukropków, dbamy jedynie o właściwe ułożenie filtrów
    safe_srt_path = srt_path.replace("\\", "/").replace(":", "\\:")

    vf_filter = (
        f"subtitles='{safe_srt_path}':"
        f"charenc=UTF-8:"
        f"force_style='{subtitle_style}'"
    )

    command = [
        "ffmpeg",
        "-loglevel", "debug",
        "-y",
        "-i", video_path,
        "-vf", vf_filter,
        "-c:a", "copy",
        output_path
    ]

    result = subprocess.run(command, capture_output=True, text=True)
    #debug
    st.code(vf_filter)
    st.text(result.stderr)
    
    
    if result.returncode != 0:
        raise Exception(f"FFMPEG Error:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
    












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

        st.session_state["speech_offset"] = detect_speech_start(audio_path)



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