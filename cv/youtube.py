"""
YouTube links -> a direct video URL OpenCV/FFmpeg can read.

* Live streams are HLS (m3u8); the format choice prefers HLS up to --max-height
  (720p is plenty for YOLO at imgsz 640 and keeps decoding cheap).
* The direct URL is signed and expires (the `expire=` parameter, usually ~6 h), so the
  reader asks for a fresh one 10 minutes before that, and after every lost connection.
* A recorded (not live) video is flagged, so the reader plays it at its own frame rate
  instead of as fast as possible.
YouTube changes often: if extraction fails, update first:  pip install -U yt-dlp
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse


def is_youtube(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(h in host for h in ('youtube.com', 'youtu.be', 'youtube-nocookie.com'))


@dataclass
class ResolvedStream:
    url: str
    title: str
    is_live: bool
    height: Optional[int]
    fps: Optional[float]
    expires_at: Optional[float]          # unix time, None if unknown


def _expiry(direct_url: str) -> Optional[float]:
    q = parse_qs(urlparse(direct_url).query)
    if 'expire' in q:
        try:
            return float(q['expire'][0])
        except ValueError:
            return None
    # HLS manifests carry it in the path: .../expire/1793120000/...
    parts = urlparse(direct_url).path.split('/')
    if 'expire' in parts:
        i = parts.index('expire')
        if i + 1 < len(parts) and parts[i + 1].isdigit():
            return float(parts[i + 1])
    return None


def deno_path() -> Optional[str]:
    """yt-dlp needs a JavaScript runtime (Deno) for YouTube since late 2025."""
    import os
    import shutil
    found = shutil.which('deno')
    if found:
        return found
    home = os.path.expanduser('~')
    for cand in (os.path.join(home, '.deno', 'bin', 'deno.exe'), os.path.join(home, '.deno', 'bin', 'deno'),
                 os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WinGet', 'Links', 'deno.exe')):
        if cand and os.path.exists(cand):
            os.environ['PATH'] = os.path.dirname(cand) + os.pathsep + os.environ.get('PATH', '')
            return cand
    if os.name == 'nt':                       # winget package folder
        import glob
        hits = glob.glob(os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WinGet',
                                      'Packages', 'DenoLand.Deno*', '**', 'deno.exe'), recursive=True)
        if hits:
            os.environ['PATH'] = os.path.dirname(hits[0]) + os.pathsep + os.environ.get('PATH', '')
            return hits[0]
    return None


# Only the picture is needed (no audio), so video-only formats are fine too.
def _format(max_height: int) -> str:
    h = max_height
    return (f'best[height<={h}][protocol^=m3u8][vcodec!=none]/best[height<={h}][vcodec!=none]'
            f'/bestvideo[height<={h}]/best[vcodec!=none]/bestvideo/best')


# Different YouTube player clients expose different formats; try them in turn.
_CLIENT_ATTEMPTS = (None, ['tv', 'web_safari'], ['mweb', 'web_embedded'], ['android_vr', 'ios'])


def resolve(url: str, max_height: int = 720) -> ResolvedStream:
    try:
        import yt_dlp
    except ImportError as e:
        raise RuntimeError('YouTube sources need yt-dlp:  pip install -U "yt-dlp[default]"') from e
    deno = deno_path()
    errors = []
    info = None
    for clients in _CLIENT_ATTEMPTS:
        opts = {'format': _format(max_height), 'quiet': True, 'no_warnings': True,
                'noplaylist': True, 'skip_download': True, 'socket_timeout': 15}
        if clients:
            opts['extractor_args'] = {'youtube': {'player_client': clients}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            break
        except Exception as e:                      # try the next client
            msg = str(e).replace('ERROR: ', '').strip()
            errors.append(f'{"/".join(clients) if clients else "default"}: {msg[:160]}')
            if 'Private video' in msg or 'removed' in msg or 'unavailable' in msg.lower() \
                    and 'format' not in msg.lower():
                break
    if info is None:
        hint = '' if deno else ('\n  -> Deno NOT found: install it (winget install DenoLand.Deno), '
                                'then open a NEW window and try again.')
        raise RuntimeError('YouTube gave no playable video format.\n  ' + '\n  '.join(errors) + hint +
                           '\n  -> Check that the stream is live in a browser; update: '
                           'pip install -U "yt-dlp[default]"')
    if info.get('live_status') == 'is_upcoming':
        raise RuntimeError('the stream has not started yet')
    direct = info.get('url') or (info.get('requested_formats') or [{}])[0].get('url')
    if not direct:
        raise RuntimeError('no playable video format found')
    return ResolvedStream(
        url=direct,
        title=str(info.get('title') or url)[:120],
        is_live=bool(info.get('is_live')),
        height=info.get('height'),
        fps=info.get('fps'),
        expires_at=_expiry(direct),
    )


def refresh_due(stream: ResolvedStream, margin_s: float = 600) -> bool:
    return stream.expires_at is not None and time.time() > stream.expires_at - margin_s
