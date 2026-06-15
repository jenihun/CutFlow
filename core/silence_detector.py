import os
import signal
import subprocess
import re
import json
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Callable


@dataclass
class VideoInfo:
    path: str
    name: str
    duration: float
    fps: float
    width: int
    height: int
    start_tc: float = 0.0   # 내장 타임코드 시작 시각(초). DJI 등은 촬영 시각 기반 TC를 가짐.


@dataclass
class Segment:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class SubtitleEntry:
    start: float
    end: float
    text: str


@dataclass
class ClipResult:
    info: VideoInfo
    segments: List[Segment] = field(default_factory=list)
    subtitles: List[SubtitleEntry] = field(default_factory=list)
    error: Optional[str] = None


def _timecode_to_seconds(tc: str, fps: float) -> float:
    """SMPTE 타임코드 문자열("HH:MM:SS:FF" 또는 드롭프레임 "HH:MM:SS;FF")을 초로 변환."""
    m = re.match(r'(\d+):(\d+):(\d+)[:;](\d+)', tc.strip())
    if not m:
        return 0.0
    hh, mm, ss, ff = (int(g) for g in m.groups())
    drop = ';' in tc
    nominal = round(fps)                      # 29.97 → 30, 59.94 → 60
    frame_number = (hh * 3600 + mm * 60 + ss) * nominal + ff
    if drop and nominal in (30, 60):
        drop_per_min = nominal // 15          # 30fps→2, 60fps→4
        total_minutes = 60 * hh + mm
        frame_number -= drop_per_min * (total_minutes - total_minutes // 10)
    return frame_number / fps


def get_video_info(video_path: str) -> VideoInfo:
    cmd = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-show_streams', '-show_format', video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    data = json.loads(result.stdout)

    video_stream = next(
        (s for s in data['streams'] if s['codec_type'] == 'video'), None
    )
    if not video_stream:
        raise ValueError(f"영상 스트림 없음: {video_path}")

    duration = float(data['format']['duration'])
    fps_str = video_stream.get('r_frame_rate', '30/1')
    num, den = map(int, fps_str.split('/'))
    fps = num / den if den else 30.0

    # 내장 타임코드: 스트림 태그 우선, 없으면 포맷 태그
    tc_str = (video_stream.get('tags', {}).get('timecode')
              or data['format'].get('tags', {}).get('timecode'))
    start_tc = _timecode_to_seconds(tc_str, fps) if tc_str else 0.0

    return VideoInfo(
        path=video_path,
        name=Path(video_path).name,
        duration=duration,
        fps=fps,
        width=int(video_stream.get('width', 1920)),
        height=int(video_stream.get('height', 1080)),
        start_tc=start_tc,
    )


def _extract_audio_array(video_path: str, nice_level: int = 0,
                         on_proc: Optional[Callable] = None,
                         max_seconds: Optional[int] = None):
    """영상에서 16kHz 모노 오디오를 추출해 numpy float32 배열로 반환."""
    import numpy as np
    import wave

    tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
    tmp.close()
    cmd = ['ffmpeg', '-hide_banner', '-i', video_path]
    if max_seconds:
        cmd += ['-t', str(max_seconds)]
    cmd += ['-vn', '-ar', '16000', '-ac', '1', '-f', 'wav', tmp.name, '-y']
    if nice_level > 0:
        cmd = ['nice', '-n', str(nice_level)] + cmd

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if on_proc:
            on_proc(proc)
        proc.communicate()
        with wave.open(tmp.name, 'rb') as wf:
            audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        return audio.astype(np.float32) / 32768.0
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def detect_noise_floor(video_path: str, nice_level: int = 7) -> float:
    """
    VAD(음성 활동 감지)로 발화/비발화 구간을 나눈 뒤, 각 0.5초 윈도우의 RMS를
    두 그룹으로 분류해 그 사이에 무음 기준을 잡는다.
    "자막이 안 달리는 곳(=비발화)이 곧 무음"이라는 관점을 반영한 방식.
    VAD를 쓸 수 없으면 전체 RMS 분포 기반으로 폴백한다.
    """
    import numpy as np
    SR = 16000
    try:
        audio = _extract_audio_array(video_path, nice_level, max_seconds=120)
        if audio.size < SR:                      # 1초 미만이면 분석 불가
            return -30.0

        # 0.5초 윈도우별 RMS(dBFS)
        win = SR // 2
        nwin = audio.size // win
        if nwin < 5:
            return -30.0
        frames = audio[:nwin * win].reshape(nwin, win)
        rms = np.sqrt(np.mean(frames ** 2, axis=1))
        rms_db = 20.0 * np.log10(np.maximum(rms, 1e-6))

        # VAD로 발화 구간 마스크 생성
        speech_mask = None
        try:
            from faster_whisper.vad import get_speech_timestamps
            segs = get_speech_timestamps(audio, sampling_rate=SR)
            if segs:
                speech_mask = np.zeros(nwin, dtype=bool)
                for s in segs:
                    a = int(s['start']) // win
                    b = min(nwin, int(s['end']) // win + 1)
                    speech_mask[a:b] = True
        except Exception:
            speech_mask = None

        if speech_mask is not None and speech_mask.any() and (~speech_mask).any():
            ns_hi = float(np.percentile(rms_db[~speech_mask], 90))  # 비발화 중 큰 편
            sp_lo = float(np.percentile(rms_db[speech_mask], 10))   # 발화 중 작은 편
            if sp_lo > ns_hi + 2:
                threshold = (ns_hi + sp_lo) / 2     # 뚜렷이 분리되면 중간값
            else:
                threshold = ns_hi + 2               # 겹치면 비발화 바로 위
        else:
            floor = float(np.percentile(rms_db, 10))
            speech = float(np.percentile(rms_db, 85))
            threshold = floor + min(10.0, max(4.0, (speech - floor) * 0.3))

        return float(max(-55, min(-10, round(threshold))))
    except Exception:
        return -30.0


def detect_keep_segments_vad(
    video_path: str,
    min_duration: float = 0.5,
    padding: float = 0.05,
    nice_level: int = 0,
    on_proc: Optional[Callable] = None,
) -> ClipResult:
    """
    VAD로 음성이 있는 구간만 유지 구간으로 잡는다 (음량 dB 기준 대신).
    엔진음·바람 같은 큰 비발화음이 있어도 '음성'만 정확히 골라낸다.
    min_duration → 이보다 짧은 침묵은 자르지 않고 이어 붙임.
    padding      → 각 발화 구간 앞뒤 여유(단어 잘림 방지, 최소 0.15초).
    """
    try:
        info = get_video_info(video_path)
        from faster_whisper.vad import get_speech_timestamps, VadOptions

        audio = _extract_audio_array(video_path, nice_level, on_proc=on_proc)
        SR = 16000
        opts = VadOptions(
            min_silence_duration_ms=int(min_duration * 1000),
            speech_pad_ms=int(max(padding, 0.15) * 1000),
        )
        segs = get_speech_timestamps(audio, vad_options=opts, sampling_rate=SR)

        segments: List[Segment] = []
        for s in segs:
            st = s['start'] / SR
            en = min(s['end'] / SR, info.duration)
            if en - st > 0.05:
                segments.append(Segment(st, en))

        if not segments:
            segments = [Segment(0.0, info.duration)]

        return ClipResult(info=info, segments=segments)

    except Exception as e:
        placeholder = VideoInfo(
            path=video_path, name=Path(video_path).name,
            duration=0, fps=30, width=1920, height=1080,
        )
        return ClipResult(info=placeholder, error=str(e))


def detect_keep_segments(
    video_path: str,
    noise_db: float = -30.0,
    min_duration: float = 0.5,
    padding: float = 0.05,
    nice_level: int = 0,
    on_proc: Optional[Callable] = None,
) -> ClipResult:
    try:
        info = get_video_info(video_path)

        cmd = [
            'ffmpeg', '-i', video_path,
            '-vn',                  # 영상 디코딩 건너뜀 (CPU 절약)
            '-threads', '2',
            '-af', f'silencedetect=noise={noise_db}dB:d={min_duration}',
            '-f', 'null', '-'
        ]
        if nice_level > 0:
            cmd = ['nice', '-n', str(nice_level)] + cmd

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if on_proc:
            on_proc(proc)

        _, stderr_bytes = proc.communicate()
        output = stderr_bytes.decode('utf-8', errors='replace')

        starts = [float(x) for x in re.findall(r'silence_start: ([\d.]+)', output)]
        ends = [float(x) for x in re.findall(r'silence_end: ([\d.]+)', output)]

        silences: List[Tuple[float, float]] = []
        for i, start in enumerate(starts):
            end = ends[i] if i < len(ends) else info.duration
            silences.append((start, end))

        segments: List[Segment] = []
        current = 0.0

        for s_start, s_end in sorted(silences):
            keep_end = s_start + padding
            keep_start = max(0.0, current)
            keep_end = min(keep_end, info.duration)
            if keep_start < keep_end - 0.05:
                segments.append(Segment(keep_start, keep_end))
            current = max(current, s_end - padding)

        if current < info.duration - 0.05:
            segments.append(Segment(max(0.0, current), info.duration))

        if not segments:
            segments = [Segment(0.0, info.duration)]

        return ClipResult(info=info, segments=segments)

    except Exception as e:
        placeholder = VideoInfo(
            path=video_path, name=Path(video_path).name,
            duration=0, fps=30, width=1920, height=1080,
        )
        return ClipResult(info=placeholder, error=str(e))
