import subprocess
import numpy as np
import whisper
from collections import deque
from threading import Thread, Lock
from flask import Flask, request, render_template_string, jsonify
import pika
import time
import os
import json

app = Flask(__name__)
 
# Shared data structures with thread safety
live_transcriptions = deque(maxlen=10)
keyword_results = deque(maxlen=10)
lock = Lock()
connection = None
channel = None
transcription_thread = None
KEYWORD = ""
 
def transcribe_audio(model, audio_np):
    """
    Transcribes audio data using Whisper.
    """
    try:
        result = model.transcribe(audio_np, fp16=False)
        return result['text'], result['segments']
    except Exception as e:
        print(f"Error during transcription: {e}")
        return "", []
 
def capture_and_transcribe_stream(ip_stream_url):
    
    """
    Captures audio from an IPTV stream and transcribes it while checking for a keyword.
    """
    print("Loading Whisper model...")
    try:
        model = whisper.load_model("base")
    except Exception as e:
        print(f"Error loading Whisper model: {e}")
        return
 
    # Start ffmpeg process
    ffmpeg_command = [
        "ffmpeg",
        "-i", ip_stream_url,
        "-f", "s16le",
        "-ac", "1",
        "-ar", "16000",
        "-acodec", "pcm_s16le",
        "-"
    ]

    process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10**7)
    
    print("Processing IPTV stream...")
    audio_buffer = b""
    chunk_size = 16000 * 2 * 5  # 5 seconds of audio for faster detection
    pre_keyword_buffer = deque(maxlen=16000 * 2 * 60)  # 1 minute buffer
    
    def save_clip(url):
        # Save a 1-minute clip (30 seconds before and after detection)
        timestamp = int(time.time())
        
        # Ensure the 'clips' directory exists
        os.makedirs("clips", exist_ok=True)

        # Define the clip filename with the folder path
        clip_filename = os.path.join(
            "clips",
            f"{url.replace('://', '_').replace('/', '_').replace('?', '_').replace('=', '_')}_{timestamp}.mp4"
        )
        
        ffmpeg_save_command = [
            "ffmpeg",
            "-i", url,
            "-ss", "00:00:30",  # Adjust this based on the stream timing
            "-t", "00:01:00",  # 1 minute duration
            "-c:v", "libx264",
            "-c:a", "aac",
            clip_filename
        ]
        
        try:
            print("Saving video clip...")
            ffmpeg_process = subprocess.run(ffmpeg_save_command, check=True)
            print(f"Video clip saved successfully as '{clip_filename}'")

            # Validate the video using ffprobe
            ffprobe_command = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                clip_filename
            ]
            
            try:
                print("Validating saved video clip...")
                ffprobe_output = subprocess.check_output(ffprobe_command, stderr=subprocess.DEVNULL).decode().strip()
                if not ffprobe_output or float(ffprobe_output) <= 0:
                    raise ValueError("Video is corrupted or has no duration.")

                # Optional: Append notification to keyword results if valid
                with lock:
                    keyword_results.append(f"Keyword detected, video clip saved: {clip_filename}")
                print(f"Video clip '{clip_filename}' is valid.")

            except Exception as validation_error:
                print(f"Validation failed for '{clip_filename}': {validation_error}")
                if os.path.exists(clip_filename):
                    os.remove(clip_filename)
                    print(f"Corrupted video file '{clip_filename}' deleted.")

        except subprocess.CalledProcessError as e:
            print(f"Error saving video clip: {e}")
            if os.path.exists(clip_filename):
                os.remove(clip_filename)
                print(f"Incomplete video file '{clip_filename}' deleted.")

        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            if os.path.exists(clip_filename):
                os.remove(clip_filename)
                print(f"Incomplete video file '{clip_filename}' deleted.")

    def transcribe_and_check():
      nonlocal audio_buffer
      while True:
        if audio_buffer:
            audio_np = np.frombuffer(audio_buffer, np.int16).astype(np.float32) / 32768.0
            if audio_np.size > 0:
                text, segments = transcribe_audio(model, audio_np)
                with lock:
                    live_transcriptions.append(ip_stream_url + ": " + text)
                print("Live Transcription:", text)
                with lock:
                    keyword = KEYWORD 
                if keyword.lower() in text.lower():
                    print("Keyword detected: " + keyword)
                    saving_clip_thread = Thread(target=save_clip , args=(ip_stream_url,) , daemon=True)
                    saving_clip_thread.start()

            audio_buffer = b""
    
    global transcription_thread
    if transcription_thread is not None and transcription_thread.is_alive():
        transcription_thread.join()
    transcription_thread = Thread(target=transcribe_and_check, daemon=True)
    transcription_thread.start()
 
    try:
        while True:
            audio_chunk = process.stdout.read(chunk_size)
            if not audio_chunk:
                break
 
            audio_buffer += audio_chunk
            pre_keyword_buffer.extend(audio_chunk)
 
    except KeyboardInterrupt:
        print("Stopping transcription...")
    finally:
        process.terminate()
        process.wait()
        transcription_thread.join()

# Main worker setup
def start_consumer(keyword):
    global connection
    global channel
    global KEYWORD
 
    # Update keyword
    with lock:
        KEYWORD = keyword
 
    # Properly close existing connection
    if connection is not None:
        try:
            if connection.is_open:
                connection.close()
        except Exception as e:
            print(f"Error closing connection: {e}")
 
    # Properly stop existing channel
    if channel is not None:
        try:
            if channel.is_open:
                channel.stop_consuming()
        except Exception as e:
            print(f"Error stopping channel: {e}")
 
    # Establish a new connection and channel
    try:
        connection = pika.BlockingConnection(pika.ConnectionParameters('rabbitmq'))
        channel = connection.channel()
 
        # Declare the queue
        channel.queue_declare(queue='transcription_tasks', durable=True)
 
        # Define the callback function
        def callback(ch, method, properties, body):
            print(f"Raw message received: {body}")
            try:
                message = body.decode('utf-8')
                json_data = json.loads(message)
                print(f"Received IPTV Object: {json_data}")
                capture_and_transcribe_stream(json_data['live_url'])
 
                # Acknowledge message processing
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception as e:
                print(f"Error in callback processing: {e}")
 
        # Start consuming messages
        channel.basic_qos(prefetch_count=1)  # Fair dispatch
        channel.basic_consume(queue='transcription_tasks', on_message_callback=callback)
        print("Waiting for transcription tasks...")
        channel.start_consuming()
 
    except Exception as e:
        print(f"Error in start_consumer: {e}")
    
    
    
@app.route('/', methods=['GET', 'POST'])
def index():
    keyword = ""
    global KEYWORD
    if request.method == 'POST':
        keyword = request.form['keyword']
        with lock:
            KEYWORD = keyword
        print(f"Keyword set to: {keyword}")
        start_consumer(keyword)

    return render_template_string('''
 <div id="chat-container">
    <form id="chat-form" method="post">
        <input type="text" id="keyword" name="keyword" placeholder="Type your keyword here..." required>
        <button type="submit">Search</button>
    </form>
</div>
<div id="transcription-result">
    <h3>Live Transcription</h3>
    <pre id="live-transcription"></pre>
    <h3>Keyword Results (30 seconds before and after)</h3>
    <pre id="keyword-results"></pre>
    <h3>Current Keyword</h3>
    <pre id="current-keyword">{{ keyword }}</pre>
</div>
 
<style>
    body {
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        background-color: #1e1e1e;
        color: #dcdcdc;
        margin: 0;
        padding: 0;
        line-height: 1.6;
    }
 
    #chat-container {
        display: flex;
        justify-content: center;
        align-items: center;
        width: 100%;
        max-width: 700px;
        margin: 30px auto;
        padding: 20px;
        border-radius: 10px;
        background-color: #2c2c2c;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
    }
 
    #chat-form {
        display: flex;
        width: 100%;
    }
 
    #chat-form input {
        flex: 1;
        padding: 10px 15px;
        border: 1px solid #444;
        border-radius: 10px 0 0 10px;
        outline: none;
        font-size: 16px;
        background-color: #3c3c3c;
        color: #dcdcdc;
        transition: border-color 0.3s;
    }
 
    #chat-form input:focus {
        border-color: #1e90ff;
    }
 
    #chat-form button {
        padding: 10px 20px;
        border: 1px solid #444;
        border-radius: 0 10px 10px 0;
        background-color: #1e90ff;
        color: white;
        cursor: pointer;
        font-size: 16px;
        font-weight: 600;
        transition: background-color 0.3s, transform 0.2s;
    }
 
    #chat-form button:hover {
        background-color: #1c86e0;
        transform: translateY(-2px);
    }
 
    #transcription-result {
        margin-top: 30px;
        width: 100%;
        padding: 20px;
        border-radius: 10px;
        border: 1px solid #444;
        background-color: #2c2c2c;
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.2);
    }
 
    h3 {
        margin-bottom: 15px;
        color: #dcdcdc;
        font-weight: 500;
    }
 
    pre {
        white-space: pre-wrap;
        word-wrap: break-word;
        color: #dcdcdc;
        font-family: 'Courier New', Courier, monospace;
        font-size: 14px;
        margin: 0;
    }
</style>
 
<script>
    function fetchLiveTranscription() {
        fetch('/live_transcription').then(response => response.json()).then(data => {
            document.getElementById('live-transcription').innerHTML = data.transcriptions.join('<br>');
        });
    }
 
    function fetchKeywordResults() {
        fetch('/keyword_results').then(response => response.json()).then(data => {
            document.getElementById('keyword-results').innerHTML = data.results.join('<br>');
        });
    }
 
    setInterval(fetchLiveTranscription, 1000);
    setInterval(fetchKeywordResults, 1000);
</script>
    ''', keyword=keyword)
 
@app.route('/live_transcription')
def get_live_transcription():
    with lock:
        return jsonify({"transcriptions": list(live_transcriptions)})
 
@app.route('/keyword_results')
def keyword_results_view():
    with lock:
        return jsonify({"results": list(keyword_results)})
    
 
if __name__ == "__main__":
    app.run(debug=False, threaded=True, host="0.0.0.0", port=5000)