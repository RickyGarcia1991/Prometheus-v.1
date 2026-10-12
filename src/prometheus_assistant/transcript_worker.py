"""Isolated fixed YouTube caption operation. Invoked with Python -I -S -B.

yt-dlp only interprets public YouTube responses. All of its HTTP requests are
limited to public YouTube hosts and global IPs in this isolated process. No media downloader runs.
"""
import hashlib
import functools
import ipaddress
import socket
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

WHEEL = 'yt_dlp-2026.8.19-py3-none-any.whl'
WHEEL_SHA256 = '1d57897e94c6665a0a6f9bc54b34e584284e32c034ffab3a7df25d8f7b24eedf'
HOSTS = {'www.youtube.com', 'youtube.com', 'youtubei.googleapis.com'}


def raw_response_guard(request, response):
    """Runs before yt-dlp's eager decompression and urllib error/redirect reads."""
    encoding = response.headers.get('Content-Encoding', 'identity').lower()
    length = response.headers.get('Content-Length')
    if (response.code != 200 or encoding not in {'', 'identity'}
            or length is not None and (not length.isdigit() or int(length)>5_000_000)):
        response.close()
        raise OSError('The caption source returned an unsupported or oversized response.')
    return response


def caption_segments(body):
    data = json.loads(body)
    if not isinstance(data, dict) or not isinstance(data.get('events'), list):
        raise ValueError('The caption source returned an invalid transcript.')
    lines, size = [], 0
    truncated = False
    for row in data['events']:
        if not isinstance(row, dict):
            continue
        text = ''.join(x.get('utf8', '') for x in row.get('segs', [])
                       if isinstance(x, dict) and isinstance(x.get('utf8'), str)).strip()
        start = row.get('tStartMs')
        if not text or type(start) not in (int, float) or not 0 <= start < 100_000_000:
            continue
        if len(text) > 2000:
            truncated = True
        text = text[:2000]
        if size + len(text) > 48_000 or len(lines) >= 1200:
            truncated = True
            break
        lines.append({'seconds': round(start/1000, 3), 'text': text})
        size += len(text)
    if not lines:
        raise ValueError('YouTube returned no readable captions for this video.')
    return lines, truncated


def main(url):
    folder = Path(__file__).resolve().parent
    wheel = folder/'vendor'/WHEEL
    if not wheel.is_file() or hashlib.sha256(wheel.read_bytes()).hexdigest() != WHEEL_SHA256:
        raise RuntimeError('The pinned transcript reader is missing or fails verification.')
    sys.path[:0] = [str(folder.parent), str(wheel)]
    from prometheus_assistant.public_reach import youtube_url
    os.environ['YTDLP_NO_PLUGINS'] = '1'
    from yt_dlp import YoutubeDL
    from yt_dlp.globals import plugin_dirs
    from yt_dlp.networking._urllib import HTTPHandler, UrllibRH
    plugin_dirs.value = []
    HTTPHandler.http_response = lambda self, req, resp: raw_response_guard(req, resp)
    HTTPHandler.https_response = HTTPHandler.http_response
    from yt_dlp.networking.common import Request
    url = youtube_url(url)
    deadline, calls, total_bytes = time.monotonic()+55, 0, 0
    original_resolve, original_connect = socket.getaddrinfo, socket.socket.connect

    def resolve(host, port, *args, **kwargs):
        # urllib connects to the numeric addresses returned here, without a
        # second DNS resolution. Redirects must pass the same host/IP check.
        if host not in HOSTS or port != 443:
            raise OSError('The caption reader may only contact public YouTube HTTPS endpoints.')
        records = original_resolve(host, port, *args, **kwargs)
        if not records or any(not ipaddress.ip_address(row[4][0]).is_global for row in records):
            raise OSError('A non-public caption destination was refused.')
        return records

    def connect(sock, address):
        if address[1] != 443 or not ipaddress.ip_address(address[0]).is_global:
            raise OSError('A non-public caption connection was refused.')
        return original_connect(sock, address)

    socket.getaddrinfo, socket.socket.connect = resolve, connect

    class LimitedBody:
        def __init__(self, stream):
            self.stream, self.size = stream, 0
        def read(self, amount=-1):
            nonlocal total_bytes
            if time.monotonic() > deadline:
                raise OSError('The caption reader exceeded its time budget.')
            cap = min(5_000_001-self.size, 20_000_001-total_bytes)
            block = self.stream.read(cap if amount is None or amount < 0 else min(amount, cap))
            self.size += len(block)
            total_bytes += len(block)
            if self.size > 5_000_000 or total_bytes > 20_000_000:
                raise OSError('The caption reader exceeded its response budget.')
            return block
        def close(self): self.stream.close()
        @property
        def closed(self): return self.stream.closed

    class Quiet:
        def debug(self, message): pass
        def warning(self, message): pass
        def error(self, message): pass

    class Reader(YoutubeDL):
        @functools.cached_property
        def _request_director(self):
            # Keep the reviewed raw-response guard authoritative even if the
            # signed host package later includes another HTTP client library.
            return self.build_request_director([UrllibRH])

        def urlopen(self, request):
            nonlocal calls
            calls += 1
            if calls > 24 or time.monotonic() >= deadline:
                raise OSError('The caption reader exceeded its request budget.')
            target = request if isinstance(request, str) else request.url
            parsed = urlsplit(target)
            if (parsed.scheme != 'https' or parsed.hostname not in HOSTS
                    or parsed.port not in (None,443) or parsed.username is not None or parsed.password is not None):
                raise OSError('The caption reader may only contact public YouTube endpoints.')
            if isinstance(request, str):
                request = Request(request)
            request.headers['Accept-Encoding'] = 'identity'
            response = super().urlopen(request)
            response.fp = LimitedBody(response.fp)
            return response

    options = {'quiet': True, 'no_warnings': True, 'logger': Quiet(),
               'cachedir': False, 'cookiefile': None, 'cookiesfrombrowser': None,
               'js_runtimes': {}, 'remote_components': set(), 'plugin_dirs': [],
               'proxy': '', 'noplaylist': True, 'skip_download': True, 'ignoreerrors': False,
               'ignore_no_formats_error': True,
               'extractor_retries': 0, 'retries': 0, 'socket_timeout': 10,
               'extractor_args': {'youtube': {'skip': ['dash', 'hls', 'translated_subs']}},
               'allowed_extractors': ['youtube'], 'check_formats': False}
    with Reader(options) as reader:
        info = reader.extract_info(url, download=False)
    manual = info.get('subtitles') or {}
    automatic = info.get('automatic_captions') or {}
    chosen = None
    for collection, kind in [(manual, 'published'), (automatic, 'automatic')]:
        for language in ['en', 'en-US', 'en-GB', 'en-orig']:
            for item in collection.get(language, []):
                if item.get('ext') == 'json3':
                    chosen = (item['url'], language, kind)
                    break
            if chosen:
                break
        if chosen:
            break
    if not chosen:
        raise ValueError('No public English captions are available for this video.')
    with Reader(options) as reader:
        with reader.urlopen(Request(chosen[0])) as response:
            body = response.read()
    segments, truncated = caption_segments(body)
    return {'title': str(info.get('title') or 'YouTube transcript')[:240], 'url': url,
            'language': chosen[1], 'caption_kind': chosen[2], 'segments': segments,
            'truncated': truncated, 'response_sha256': hashlib.sha256(body).hexdigest(),
            'response_bytes': len(body), 'reader_version': 'yt-dlp 2026.8.19', 'network_requests': calls}


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise ValueError('Use one public YouTube video URL.')
        print(json.dumps(main(sys.argv[1]), ensure_ascii=True))
    except Exception as error:
        # URLs in extractor errors may contain temporary caption access tokens.
        # Preserve a useful cause without echoing full upstream request details.
        message = str(error)
        if 'http' in message.lower() or len(message) > 240:
            message = 'Public captions could not be retrieved. The service may be limiting access or this video may require sign-in.'
        print(json.dumps({'error': message}))
        sys.exit(1)
