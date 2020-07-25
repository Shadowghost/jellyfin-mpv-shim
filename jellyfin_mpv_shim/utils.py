import socket
import ipaddress
import urllib.request
import urllib.parse
from threading import Lock
import logging
import sys
import os.path

from .conf import settings
from datetime import datetime
from functools import wraps
from .constants import USER_APP_NAME

log = logging.getLogger('utils')

seq_num = 0
seq_num_lock = Lock()

class Timer(object):
    def __init__(self):
        self.restart()

    def restart(self):
        self.started = datetime.now()

    def elapsedMs(self):
        return  self.elapsed() * 1e3

    def elapsed(self):
        return (datetime.now()-self.started).total_seconds()

def synchronous(tlockname):
    """
    A decorator to place an instance based lock around a method.
    From: http://code.activestate.com/recipes/577105-synchronization-decorator-for-class-methods/
    """

    def _synched(func):
        @wraps(func)
        def _synchronizer(self,*args, **kwargs):
            tlock = self.__getattribute__( tlockname)
            tlock.acquire()
            try:
                return func(self, *args, **kwargs)
            finally:
                tlock.release()
        return _synchronizer
    return _synched

def is_local_domain(client):
    # With Jellyfin, it is significantly more likely the user will be using
    # an address that is a hairpin NAT. We want to detect this and avoid
    # imposing limits in this case.
    url = client.config.data.get("auth.server", "")
    domain = urllib.parse.urlparse(url).hostname

    addr_info = socket.getaddrinfo(domain,8096)[0]
    ip = addr_info[4][0]
    is_local = ipaddress.ip_address(ip).is_private

    if not is_local:
        if addr_info[0] == socket.AddressFamily.AF_INET:
            try:
                wan_ip = (urllib.request.urlopen("https://checkip.amazonaws.com/")
                   .read().decode('ascii').replace('\n','').replace('\r',''))
                return ip == wan_ip
            except Exception:
                log.warning("checkip.amazonaws.com is unavailable. Assuming potential WAN ip is remote.", exc_info=True)
                return False
        elif addr_info[0] == socket.AddressFamily.AF_INET6:
            return False
    return True

def mpv_color_to_plex(color):
    return '#'+color.lower()[3:]

def plex_color_to_mpv(color):
    return '#FF'+color.upper()[1:]

def get_profile(is_remote=False, video_bitrate=None, force_transcode=False, is_tv=False):
    if video_bitrate is None:
        if is_remote:
            video_bitrate = settings.remote_kbps
        else:
            video_bitrate = settings.local_kbps

    if settings.transcode_h265:
        transcode_codecs = "h264,mpeg4,mpeg2video"
    elif settings.transcode_to_h265:
        transcode_codecs = "h265,hevc,h264,mpeg4,mpeg2video"
    else:
        transcode_codecs = "h264,h265,hevc,mpeg4,mpeg2video"

    profile = {
        "Name": USER_APP_NAME,
        "MaxStreamingBitrate": video_bitrate * 1000,
        "MusicStreamingTranscodingBitrate": 1280000,
        "TimelineOffsetSeconds": 5,
        "TranscodingProfiles": [
            {
                "Type": "Audio"
            },
            {
                "Container": "ts",
                "Type": "Video",
                "Protocol": "hls",
                "AudioCodec": "aac,mp3,ac3,opus,flac,vorbis",
                "VideoCodec": transcode_codecs,
                "MaxAudioChannels": "6"
            },
            {
                "Container": "jpeg",
                "Type": "Photo"
            }
        ],
        "DirectPlayProfiles": [
            {
                "Type": "Video"
            },
            {
                "Type": "Audio"
            },
            {
                "Type": "Photo"
            }
        ],
        "ResponseProfiles": [],
        "ContainerProfiles": [],
        "CodecProfiles": [],
        "SubtitleProfiles": [
            {
                "Format": "srt",
                "Method": "External"
            },
            {
                "Format": "srt",
                "Method": "Embed"
            },
            {
                "Format": "ass",
                "Method": "External"
            },
            {
                "Format": "ass",
                "Method": "Embed"
            },
            {
                "Format": "sub",
                "Method": "Embed"
            },
            {
                "Format": "sub",
                "Method": "External"
            },
            {
                "Format": "ssa",
                "Method": "Embed"
            },
            {
                "Format": "ssa",
                "Method": "External"
            },
            {
                "Format": "smi",
                "Method": "Embed"
            },
            {
                "Format": "smi",
                "Method": "External"
            },
            # Jellyfin currently refuses to serve these subtitle types as external.
            {
                "Format": "pgssub",
                "Method": "Embed"
            },
            #{
            #    "Format": "pgssub",
            #    "Method": "External"
            #},
            {
                "Format": "dvdsub",
                "Method": "Embed"
            },
            #{
            #    "Format": "dvdsub",
            #    "Method": "External"
            #},
            {
                "Format": "pgs",
                "Method": "Embed"
            },
            #{
            #    "Format": "pgs",
            #    "Method": "External"
            #}
        ]
    }

    if settings.transcode_hi10p:
        profile['CodecProfiles'].append(
            {
                'Type': 'Video',
                'codec': 'h264',
                'Conditions': [
                    {
                        'Condition': "LessThanEqual",
                        'Property': "VideoBitDepth",
                        'Value': "8"
                    }
                ]
            }
        )

    if settings.always_transcode or force_transcode:
        profile['DirectPlayProfiles'] = []

    if is_tv:
        profile['TranscodingProfiles'].insert(0, {
            "Container": "ts",
            "Type": "Video",
            "AudioCodec": "mp3,aac",
            "VideoCodec": "h264",
            "Context": "Streaming",
            "Protocol": "hls",
            "MaxAudioChannels": "2",
            "MinSegments": "1",
            "BreakOnNonKeyFrames": True
        })

    return profile

def get_sub_display_title(stream):
    return "{0}{1} ({2})".format(
        stream.get("Language", "Unkn").capitalize(),
        " Forced" if stream.get("IsForced") else "",
        stream.get("Codec")
    )

def get_seq():
    global seq_num
    seq_num_lock.acquire()
    current = seq_num
    seq_num += 1
    seq_num_lock.release()
    return current

def none_fallback(value, fallback):
    if value is None:
        return fallback
    return value

def get_resource(*path):
    # Detect if bundled via pyinstaller.
    # From: https://stackoverflow.com/questions/404744/
    if getattr(sys, '_MEIPASS', False):
        application_path = os.path.join(sys._MEIPASS, "jellyfin_mpv_shim")
    else:
        application_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(application_path, *path)

def get_text(*path):
    with open(get_resource(*path)) as fh:
        return fh.read()
