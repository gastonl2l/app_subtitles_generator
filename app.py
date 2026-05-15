import streamlit as st
import tempfile
from io import BytesIO
import subprocess
from openai import OpenAI
import shutil
import os
import re
from moviepy import VideoFileClip, TextClip, CompositeVideoClip


# API_KEY pobierany z sekretów Streamlit Cloud
@st.cache_resource
def get_openai_client():
    return OpenAI(api_key=st.secrets["OPENAI_API_KEY"])


# Model
AUDIO_TRANSCRIBE_MODEL = "whisper-1"


# Funkcja transkrypcji dźwięków na napisy
def transcribe_audio(audio_path):
    openai_client = get_openai_client()

    with open(audio_path, "rb") as audio_file:
        transcript = openai_client.audio.transcriptions.create(
            file=audio_file,
            model=AUDIO_TRANSCRIBE_MODEL,
            response_format="srt",
        )

    return transcript


# Inicjalizacja stanów sesji
if "note_audio_text" not in st.session_state:
    st.session_state["note_audio_text"] = ""

if "last_uploaded_file" not in st.session_state:
    st.session_state["last_uploaded_file"] = None

if "audio_ready" not in st.session_state:
    st.session_state["audio_ready"] = False

if "video_rendered" not in st.session_state:
    st.session_state["video_rendered"] = False


# Funkcja dodawania napisów do wideo przy użyciu moviepy
def add_subtitles_to_video(video_path, srt_content, output_path):

    # 1. Funkcja pomocnicza: konwersja czasu SRT (00:00:05,123) na sekundy dla moviepy
    def srt_time_to_seconds(srt_time_str):
        srt_time_str = srt_time_str.replace(",", ".")
        match = re.match(r"(\d+):(\d+):(\d+\.\d+|\d+)", srt_time_str)
        if match:
            h, m, s = map(float, match.groups())
            return h * 3600 + m * 60 + s
        return 0.0

    # 2. Ładowanie bazowego pliku wideo
    video = VideoFileClip(video_path)
    
    # 3. Parsowanie bloków napisów SRT z pamięci podręcznej
    blocks = srt_content.strip().split("\n\n")
    subtitle_clips = []

    for block in blocks:
        lines = block.split("\n")
        if len(lines) >= 3:
            time_line = lines[1]
            text_content = " ".join(lines[2:])

            # Wyciąganie czasów wyświetlania napisu
            times = re.findall(r"\d{2}:\d{2}:\d{2}[,\.]\d{3}", time_line)
            if len(times) == 2:
                start_sec = srt_time_to_seconds(times[0])
                end_sec = srt_time_to_seconds(times[1])
                duration = end_sec - start_sec

                if duration > 0:
                    # ZACHOWANO TWÓJ STYL WYŚWIETLANIA NAPISÓW
                    txt_clip = (
                        TextClip(
                            text=text_content,      
                            font_size=28,                       # Zwiększono rozmiar, aby napisy były czytelne
                            color='white', 
                            font='Montserrat-Bold.ttf',         # Nowoczesna, gruba czcionka bezszeryfowa
                            size=(int(video.w * 0.75), None),   # Szerokość 75% ekranu wymusza podział na max 2-3 linie bez ucinania słów
                            shadow_color='black',               # Kolor cienia pod napisami
                            shadow_radius=5,                    # Rozmycie cienia (tworzy efekt miękkiego blasku z obrazka)
                            text_align='center',                # Centrowanie tekstu w poziomie
                            method='caption'                    # Bezpieczne zawijanie całych słów do nowej linii
                        )
                        .with_start(start_sec)       
                        .with_duration(duration)    
                        .with_position(('center', video.h - 260)) # Pozycja wyżej, dopasowana do układu wieloliniowego
                    )
                    subtitle_clips.append(txt_clip)

    # 4. Łączenie oryginalnego wideo z wygenerowanymi nakładkami tekstowymi
    final_video = CompositeVideoClip([video] + subtitle_clips)
    
    # 5. Zapisanie pliku końcowego
    final_video.write_videofile(
        output_path, 
        codec="libx264", 
        audio_codec="aac",
        temp_audiofile="temp-audio.m4a", 
        remove_temp=True
    )
    
    # Zamykanie obiektów w celu zwolnienia pamięci RAM i odblokowania plików
    video.close()
    final_video.close()


# Title
st.title("Generator napisów App")

uploaded_file = st.file_uploader(
    "Dodaj wideo",
    type=["mp4", "mov", "avi", "mkv"]
)

if uploaded_file is not None:

    # nowe video → wyczyść stary stan aplikacji
    if uploaded_file.name != st.session_state["last_uploaded_file"]:
        st.session_state["note_audio_text"] = ""
        st.session_state["last_uploaded_file"] = uploaded_file.name
        st.session_state["audio_ready"] = False
        st.session_state["video_rendered"] = False

        if os.path.exists("audio.mp3"):
            os.remove("audio.mp3")
        if os.path.exists("video_with_subtitles.mp4"):
            os.remove("video_with_subtitles.mp4")

    # zapis video do pliku tymczasowego
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_video:
        file_bytes = uploaded_file.getvalue()
        tmp_video.write(file_bytes)
        video_path = tmp_video.name

    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        st.video(video_path)

    # output audio
    audio_path = "audio.mp3"

    # ffmpeg command do ekstrakcji audio
    command = [
        "ffmpeg",
        "-i", video_path,
        "-q:a", "0",
        "-map", "a",
        audio_path,
        "-y"
    ]

    # progress bar dla ekstrakcji audio
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

    # Przycisk generowania napisów przez OpenAI Whisper
    if st.button("Generuj napisy"):
        with st.spinner("Transcribing audio..."):
            st.session_state["note_audio_text"] = transcribe_audio(audio_path)
        st.success("Napisy wygenerowane!")

    # Zakładki interfejsu
    tab1, tab2 = st.tabs(["Napisy", "Wideo z napisami"])

    with tab1:
        # wyświetl napisy i pobierz napisy SRT
        if st.session_state["note_audio_text"]:
            st.text_area(
                "napisy SRT",
                value=st.session_state["note_audio_text"],
                height=400
            )

            st.download_button(
                "Pobierz napisy",
                st.session_state["note_audio_text"],
                file_name="subtitles.srt",
                mime="text/plain"
            )

    with tab2:
        if "video_rendered" not in st.session_state:
            st.session_state["video_rendered"] = False

        if st.button("Generuj wideo z napisami"):
            if not st.session_state["note_audio_text"]:
                st.error("Najpierw wygeneruj napisy w pierwszej zakładce!")
            else:
                output_video_path = "video_with_subtitles.mp4"

                # NOWOŚĆ: Profesjonalny pasek stanu ładowania st.status
                with st.status("Rozpoczynanie renderowania...", expanded=True) as status:
                    try:
                        status.update(label="Trwa przetwarzanie wideo i nakładanie napisów... (To może chwilę potrwać)", state="running")
                        
                        add_subtitles_to_video(video_path, st.session_state["note_audio_text"], output_video_path)
                        
                        st.session_state["video_rendered"] = True
                        status.update(label="Wideo zostało pomyślnie wygenerowane!", state="complete", expanded=False)
                        st.toast("Video ready!", icon="🎬")
                    except Exception as e:
                        status.update(label="Wystąpił błąd podczas generowania wideo", state="error")
                        st.error(f"Wystąpił błąd: {e}")

        # Wyświetlanie gotowego pliku i przycisku pobierania
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
