import pytubefix
import os
import pathlib
import unicodedata
import re
import winsound
from concurrent import futures
from functools import partial
from itertools import repeat
from time import sleep
import json
import ffmpeg
import shutil
import asyncio

HOME_DIR = r'I:\SharedFolder\Youtube'
INPUT_STRING = 'Enter youtube url, if you want to download the given urls press "d":\n'
MAX_THREADS = 10
SYNC = True
TEMP_FOLDER_NAME = 'temp'
MAX_RES = 1080
# TODO: automatically generate token with https://github.com/YunzheZJU/youtube-po-token-generator
# To generate manually po token read https://github.com/JuanBindez/pytubefix/blob/main/docs/user/po_token.rst
TOKEN_FILE = 'youtube-tokens.json'

def to_file_name(value: str):
    #value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[\/:*?"<>|]', '', value)
    return value.strip('-_ ')

def to_folder_name(value: str) -> str:
    value = to_file_name(value).replace('.', '')
    value = re.sub(r'[^\w\s.-]', '', value.lower())
    value = re.sub(r'[-\s]+', '-', value).strip('-_')
    return value

def play_finished_sound():
    hz = 2500
    length = 500
    
    winsound.Beep(hz, length)
    sleep(length / 2000)
    winsound.Beep(hz, length)
    sleep(length / 2000)
    winsound.Beep(hz, length*2)


def apply_with_kwargs(args):
    return args[0](**args[1])

def get_res(stream: pytubefix.Stream):
    if stream.resolution is None:
        return 0
    return int(re.search(r'\d+', stream.resolution).group())

def get_audio_kbps(stream: pytubefix.Stream):
    abr: str = stream.abr
    if abr.endswith('kbps'):
        return int(abr[:-4])
    return 0

def get_preffered_video_stream(streams: pytubefix.query.StreamQuery) -> pytubefix.streams.Stream | None:
    # TODO: don't filter out other extensions, as ffmpeg do support them, just tweak this function to preffer mp4
    streams = streams.filter(type='video', file_extension='mp4')
    streams = list(filter(lambda s: get_res(s) <= MAX_RES, streams))
    if len(streams) == 0:
        return None
    return max(streams, key=get_res)

def get_preffered_audio_stream(streams: pytubefix.query.StreamQuery) -> pytubefix.streams.Stream | None:
    streams = streams.filter(type='audio')
    if len(streams) == 0:
        return None
    return max(streams, key=get_audio_kbps)

def fetch_stream(stream: pytubefix.Stream | None, folder_name: str, **kwargs) -> str | None:
    if stream is None:
        return None
    
    return stream.download(folder_name, **kwargs)
    
def download_video(url: str, video_id: int | None = None, output_dir=HOME_DIR):   
    print(f'getting {url}')
    video = pytubefix.YouTube(url,
                              #token_file=TOKEN_FILE, use_po_token=True,
                              use_oauth=True, allow_oauth_cache=True)
    while True:
        try:
            stream: pytubefix.streams.Stream | None = get_preffered_video_stream(video.streams)
        except AttributeError:
            print("Blocked from youtube, trying again in 1 minute")
            sleep(60)
        else:
            break
    
    if stream is None:
        print(f"Could't find a valid stream for video: '{video.title}'")
        return

    audio_stream: pytubefix.streams.Stream | None = None
    if not stream.is_progressive:
        audio_stream = get_preffered_audio_stream(video.streams)
        if audio_stream is None:
            print(f"Could't find a valid audio for video: '{video.title}'")
            return None
        
    prefix = '' if video_id is None else f'{video_id+1:02d}. '

    streams_output_dir = output_dir
    if not stream.is_progressive:
        streams_output_dir = os.path.join(HOME_DIR, TEMP_FOLDER_NAME)
        pathlib.Path(streams_output_dir).mkdir(parents=True, exist_ok=True)
    
    print(f"downloading video '{video.title}'.\n{stream=}")
    with futures.ThreadPoolExecutor(1 + (audio_stream is not None)) as pool:
        stream_path, audio_stream_path = pool.map(apply_with_kwargs, [
            (fetch_stream, dict(stream=stream,       folder_name=streams_output_dir, filename_prefix=prefix, skip_existing=False)),
            (fetch_stream, dict(stream=audio_stream, folder_name=streams_output_dir, filename_prefix=prefix, skip_existing=False)),
        ])
    print(f"finished downloading '{video.title}'")


    if stream_path is None:
        return None
    if audio_stream is not None and audio_stream_path is None:
        return None
    

    if not stream.is_progressive:      
        ffmpeg_video_stream = ffmpeg.input(stream_path)
        ffmpeg_audio_stream = ffmpeg.input(audio_stream_path)

        full_path: str = os.path.join(output_dir, to_file_name(f'{prefix}{stream.default_filename}'))
        if os.path.exists(full_path):
            os.remove(full_path)
        
        ffmpeg.output(ffmpeg_audio_stream, ffmpeg_video_stream, full_path, vcodec='copy', acodec='copy').run()
              
    return stream



def get_playlist_urls(url: str) -> list[dict]:
    playlist = pytubefix.Playlist(url)

    output_dir = os.path.join(HOME_DIR, to_folder_name(playlist.title))
    pathlib.Path(output_dir).mkdir(parents=True, exist_ok=True)
    print(f'Outputing to: {output_dir}')
    print(f'seeing {len(playlist)} videos')

    return [{'url': url, 'video_id': i, 'output_dir': output_dir}
            for url, i in zip(playlist.video_urls, range(len(playlist)))]


            
def download_from_inputs():
    urls = []
    input_url = str(input(INPUT_STRING))
    while input_url != 'd':
        if input_url.startswith('https://'):
            urls.append(input_url)
        else:
            print('URL must start with "https://"')
        input_url = str(input())

    print('Downloading')

    download_kwargs: list[dict] = []

    for url in urls:
        if 'playlist' in url:
            download_kwargs.extend(get_playlist_urls(url))
        else:
            download_kwargs.append({'url': url})
            
    streams = []
    args_for_starmap = zip(repeat(download_video), download_kwargs)

    if SYNC:
        streams = [ apply_with_kwargs(args) for args in args_for_starmap]
    else:
        with futures.ThreadPoolExecutor(min(MAX_THREADS, len(download_kwargs))) as pool:
            streams = pool.map(apply_with_kwargs, args_for_starmap)

    for (index, stream) in enumerate(streams):
        print(f'{index + 1:02d}. ', end='')
        if stream is None:
            print('None')
        else:
            print(stream.resolution, f'{stream.fps}fps')
    
    shutil.rmtree(os.path.join(HOME_DIR, TEMP_FOLDER_NAME), ignore_errors=True) 
    
    play_finished_sound()

if __name__ == '__main__':
    while True:
        download_from_inputs()

