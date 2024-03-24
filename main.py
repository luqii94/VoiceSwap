import os

import json
import shutil
import time
import ffmpeg
import requests
import subprocess

from flask import Flask, request, send_file, render_template
from threading import Thread
from requests_toolbelt.multipart.encoder import MultipartEncoder
from spleeter.separator import Separator

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

app = Flask(__name__, static_url_path='/static')



#Cleanup files after a little bit of delay
def async_cleanup_files(delay, files_to_remove, dirs_to_remove=None):
  def delayed_cleanup():
    time.sleep(delay)  # Wait for a specified delay (in seconds)
    for file_path in files_to_remove:
        if os.path.exists(file_path):
            os.remove(file_path)
    if dirs_to_remove:
        for dir_path in dirs_to_remove:
            if os.path.exists(dir_path):
                shutil.rmtree(dir_path)
  Thread(target=delayed_cleanup).start()



#Upload Video Function
@app.route('/', methods=['GET', 'POST'])
def upload_video():
  voice_options = {
    'Rachel Voice, Female, Corporate': 'EXAVITQu4vr4xnSDxMaL',
    'Seraphina, Female, Teenage': 'AZnzlk1XvdvUeBnXmlld',
    'Antony, Male, Rigid': 'ErXwobaYiN019PkySvjV',
    'Joseph, Male, Commercials': 'UuDE0Ki4TEAyPHmuPW9p',
    'Jeff, Male, Storytelling': 'nWyi64I3m632IQdDOTzM',
    'Samad, Male, Hindi': 'rW2lcIFbB5AVdzWcOG9n'
  }

  if request.method == 'POST':
    # Check if a file was uploaded
    if 'video' not in request.files:
      return 'No video file was uploaded.', 400

    # Get the uploaded video file
    video_file = request.files['video']

    # Save the uploaded video file
    video_filename = video_file.filename
    video_path = os.path.join('uploads', video_filename)
    video_file.save(video_path)

    #Giving the uploaded video file a base name
    base_name = os.path.splitext(video_filename)[0]

    try:
      # Get the selected voice ID from the form
      selected_voice_id = request.form.get('voice_id')

      # Process the video and get the modified audio
      modified_audio = change_audio_accent(video_path, selected_voice_id, base_name)

      # Combine the modified audio with the original video
      output_video = combine_audio_video(video_path, modified_audio, base_name)

      # Ensure files are cleaned up before sending the response
      files_to_remove = [
        os.path.join('uploads', video_filename),
        f'{base_name}_final_audio.mp3',
        f'{base_name}_modified_vocals.mp3',
        f'{base_name}_original_audio.mp3',
        f'{base_name}_output_video.mp4'
      ]
      dirs_to_remove = [
        os.path.join('output', f'{base_name}_original_audio')
      ]

      async_cleanup_files(5, files_to_remove, dirs_to_remove)

      # Serve the output video
      return send_file(output_video, mimetype='video/mp4', as_attachment=True)
    except Exception as e:
      return f'An error occurred: {str(e)}', 500

  return render_template('index.html', voice_options=voice_options)



# Change Audio Accent
def change_audio_accent(video_path, selected_voice_id, base_name):
  audio_file_path = extract_audio(video_path, base_name)
  vocals_path, music_paths, noise_path = separate_vocals_and_music(audio_file_path, base_name)

  # Eleven Labs API
  url = f'https://api.elevenlabs.io/v1/speech-to-speech/{selected_voice_id}/stream'
  headers = {
      'Accept': 'application/json',
      'xi-api-key': 'ac492f6210f306789084ed7b3741f6f2'
  }
  data = {
      'model_id': 'eleven_multilingual_sts_v2',
      'voice_settings': json.dumps({
          "stability": 0.5,
          "similarity_boost": 0.8,
          "style": 0.0,
          "use_speaker_boost": True
      })
  }
  files = {
    'audio': (os.path.basename(vocals_path), open(vocals_path, 'rb'), 'audio/mpeg')
  }
  response = requests.post(url, headers=headers, data=data, files=files, stream=True)
  modified_vocals_path = f'{base_name}_modified_vocals.mp3'
  
  if response.ok:
    with open(modified_vocals_path, 'wb') as f:
      for chunk in response.iter_content(chunk_size=1024):
        f.write(chunk)
    print("Modified audio stream saved successfully.")
  
  else:
    print(f"Failed to process audio: {response.text}")
    files['audio'][1].close()
    return None

  files['audio'][1].close()

  final_audio_path = combine_vocals_and_music(modified_vocals_path, music_paths, base_name)

  return final_audio_path



# Extract audio from the video file
def extract_audio(video_path, base_name):
  audio_filename = f'{base_name}_original_audio.mp3'
  stream = ffmpeg.input(video_path)
  audio = stream.audio
  stream = ffmpeg.output(audio, audio_filename)
  ffmpeg.run(stream)
  return audio_filename



# Separate Vocals and Music from the original audio
def separate_vocals_and_music(audio_file_path, base_name):
  output_dir = os.path.join('output', f'{base_name}_original_audio')

  if not os.path.exists(output_dir):
      os.makedirs(output_dir)

  separator = Separator('spleeter:5stems')
  separator.separate_to_file(audio_file_path, output_dir, codec='mp3')
  potential_subdir = os.path.join(output_dir, os.path.basename(audio_file_path).replace('.mp3', ''))

  if os.path.exists(potential_subdir):
      output_dir = potential_subdir

  vocals_path = os.path.join(output_dir, 'vocals.mp3')
  music_paths = [
      os.path.join(output_dir, 'bass.mp3'),
      os.path.join(output_dir, 'drums.mp3'),
      os.path.join(output_dir, 'other.mp3'),
      os.path.join(output_dir, 'piano.mp3')
  ]

  noise_path = os.path.join(output_dir, 'other.mp3')
  return vocals_path, music_paths, noise_path



# Combine Vocals and Music into a single audio file
def combine_vocals_and_music(vocals_path, music_paths, base_name):
  output_path = f'{base_name}_final_audio.mp3'

  cmd = ['ffmpeg']
  for path in music_paths:
    cmd += ['-i', path]
  cmd += ['-i', vocals_path]

  filter_complex = ''
  inputs = len(music_paths) + 1
  
  for i in range(inputs):
    filter_complex += f'[{i}:a]'
  filter_complex += f'amix=inputs={inputs}:duration=longest'

  cmd += ['-filter_complex', filter_complex,
    '-ac', '2', output_path]

  subprocess.run(cmd, check=True)
  return output_path



#Combine audio and video and produce final output video
def combine_audio_video(video_path, audio_path, base_name):
  output_video_path = f'{base_name}_output_video.mp4'
  cmd = [
    'ffmpeg',
    '-i', video_path,
    '-i', audio_path,
    '-c:v', 'copy',
    '-map', '0:v:0',
    '-map', '1:a:0',
    '-shortest',
    output_video_path
  ]
  try:
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  except subprocess.CalledProcessError as e:
    print(f"ffmpeg command failed: {e.stderr}")
    return None
  return output_video_path



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)