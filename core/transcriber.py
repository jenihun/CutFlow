import subprocess
import tempfile
import os
import re
from typing import List, Optional, Callable

from .silence_detector import SubtitleEntry

_MLX_MODEL_MAP = {
    'tiny':            'mlx-community/whisper-tiny',
    'base':            'mlx-community/whisper-base',
    'small':           'mlx-community/whisper-small',
    'medium':          'mlx-community/whisper-medium',
    'large-v3':        'mlx-community/whisper-large-v3',
    'large-v3-turbo':  'mlx-community/whisper-large-v3-turbo',
}


_WORD_RE = re.compile(r'\w', re.UNICODE)
_MAX_SUB_CHARS = 20            # 자막 한 줄 최대 글자 수
_BREAK_PUNCT = ('.', '?', '!', ',', '…', '~')


def _split_words(words, max_chars: int = _MAX_SUB_CHARS):
    """
    (텍스트, 시작, 끝) 단어 목록을 max_chars 이하의 조각으로 나눈다.
    단어 경계와 문장부호에서 끊어 각 조각이 정확한 시각을 갖게 한다.
    반환: [(start, end, text), ...]
    """
    chunks = []
    cur = []
    cur_len = 0
    for token, wstart, wend in words:
        token = token.strip()
        if not token:
            continue
        add_len = len(token) + (1 if cur else 0)
        if cur and cur_len + add_len > max_chars:
            chunks.append(cur)
            cur, cur_len = [], 0
            add_len = len(token)
        cur.append((token, wstart, wend))
        cur_len += add_len
        # 문장부호에서 적당히 길면 끊기
        if token.endswith(_BREAK_PUNCT) and cur_len >= max_chars * 0.5:
            chunks.append(cur)
            cur, cur_len = [], 0

    if cur:
        chunks.append(cur)

    out = []
    for ch in chunks:
        text = ' '.join(t for t, _, _ in ch).strip()
        if text:
            out.append((float(ch[0][1]), float(ch[-1][2]), text))
    return out


def _is_hallucination(text: str, compression_ratio: float, duration: float) -> bool:
    """비발화 구간의 환각 자막(엔진음에 붙는 반복 텍스트 등)을 걸러낸다."""
    t = (text or '').strip()
    if not t:
        return True
    if duration <= 0:
        return True
    if '�' in t:                       # 디코딩 깨진 문자 (예: 휴�OS)
        return True
    if compression_ratio and compression_ratio > 2.4:  # 반복 환각 (подоб подоб…)
        return True
    if not _WORD_RE.search(t):              # 구두점/기호만 (예: "!", "...")
        return True
    return False


def _mlx_available() -> bool:
    try:
        import mlx_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def extract_audio(video_path: str, nice_level: int = 0,
                  on_proc: Optional[Callable] = None) -> str:
    """오디오를 로컬 임시 파일로 추출 (외장 하드 부담 감소)"""
    tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
    tmp.close()

    cmd = [
        'ffmpeg', '-i', video_path,
        '-vn', '-ar', '16000', '-ac', '1',
        '-f', 'wav', tmp.name, '-y'
    ]
    if nice_level > 0:
        cmd = ['nice', '-n', str(nice_level)] + cmd

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if on_proc:
        on_proc(proc)
    proc.communicate()
    return tmp.name


def load_model(model_size: str = 'large-v3-turbo', nice_level: int = 0):
    """
    Apple Silicon이면 mlx-whisper 모델 ID(str)를 반환,
    그 외에는 faster-whisper WhisperModel 객체를 반환.
    """
    if _mlx_available():
        return _MLX_MODEL_MAP.get(model_size, 'mlx-community/whisper-large-v3-turbo')

    from faster_whisper import WhisperModel
    if nice_level > 0:
        return WhisperModel(model_size, device='cpu',
                            compute_type='int8', cpu_threads=2)
    return WhisperModel(model_size, device='cpu',
                        compute_type='float32', cpu_threads=0)


def _seg_to_subs(text, compression_ratio, start, end, words, offset=0.0):
    """세그먼트 1개 → 환각 필터 + 길이 분할을 거친 SubtitleEntry 목록."""
    text = (text or '').strip()
    if _is_hallucination(text, compression_ratio, end - start):
        return []
    subs = []
    if words and len(text) > _MAX_SUB_CHARS:
        for st, en, tx in _split_words(words):
            subs.append(SubtitleEntry(float(st) + offset, float(en) + offset, tx))
    else:
        subs.append(SubtitleEntry(float(start) + offset, float(end) + offset, text))
    return subs


def transcribe(
    video_path: str,
    model,
    language: str = 'ko',
    nice_level: int = 0,
    on_proc: Optional[Callable] = None,
    on_subtitle: Optional[Callable] = None,   # 자막 1줄 생성될 때마다 호출 (실시간)
    checkpoint: Optional[Callable] = None,    # 청크 사이 호출; False 반환 시 중단
) -> List[SubtitleEntry]:
    audio_path = extract_audio(video_path, nice_level=nice_level, on_proc=on_proc)
    out: List[SubtitleEntry] = []

    def _emit(sub: SubtitleEntry):
        out.append(sub)
        if on_subtitle:
            on_subtitle(sub)

    try:
        # ── mlx-whisper (Apple Silicon GPU): 30초 청크로 나눠 실시간 스트리밍 ──
        if isinstance(model, str):
            import mlx_whisper
            from mlx_whisper.audio import load_audio, SAMPLE_RATE

            audio = load_audio(audio_path)
            chunk = 30 * SAMPLE_RATE
            for i0 in range(0, len(audio), chunk):
                if checkpoint and not checkpoint():
                    break
                base = i0 / SAMPLE_RATE
                result = mlx_whisper.transcribe(
                    audio[i0:i0 + chunk],
                    path_or_hf_repo=model,
                    language=language,
                    word_timestamps=True,          # 정밀한 발화 타이밍 (싱크)
                    condition_on_previous_text=False,  # 반복 환각 억제
                    no_speech_threshold=0.6,
                    compression_ratio_threshold=2.4,
                )
                for seg in result.get('segments', []):
                    words = [(w['word'], w['start'], w['end'])
                             for w in seg.get('words', [])]
                    for sub in _seg_to_subs(seg.get('text', ''),
                                            seg.get('compression_ratio', 0.0),
                                            seg['start'], seg['end'], words, base):
                        _emit(sub)
            return out

        # ── faster-whisper (CPU 폴백): 세그먼트 제너레이터를 그대로 스트리밍 ──
        segments, _ = model.transcribe(
            audio_path,
            language=language,
            beam_size=5,
            condition_on_previous_text=False,
            word_timestamps=True,
            vad_filter=True,                   # 비발화(엔진음 등) 제거
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        for seg in segments:
            if checkpoint and not checkpoint():
                break
            words = [(w.word, w.start, w.end) for w in (seg.words or [])]
            for sub in _seg_to_subs(seg.text, getattr(seg, 'compression_ratio', 0.0),
                                    seg.start, seg.end, words):
                _emit(sub)
        return out
    finally:
        try:
            os.unlink(audio_path)
        except OSError:
            pass
