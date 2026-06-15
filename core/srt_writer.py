from typing import List, Optional, Tuple
from .silence_detector import ClipResult, SubtitleEntry, Segment


def _fmt(seconds: float) -> str:
    ms = int((seconds % 1) * 1000)
    s = int(seconds) % 60
    m = int(seconds // 60) % 60
    h = int(seconds // 3600)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _remap(sub: SubtitleEntry, segments: List[Segment], clip_offset: float) -> Optional[Tuple[float, float, str]]:
    """자막 시간을 컷 타임라인 기준으로 재계산"""
    tl_pos = clip_offset
    for seg in segments:
        overlap_start = max(sub.start, seg.start)
        overlap_end = min(sub.end, seg.end)
        if overlap_start < overlap_end:
            new_start = tl_pos + (overlap_start - seg.start)
            new_end = tl_pos + (overlap_end - seg.start)
            return (new_start, new_end, sub.text)
        tl_pos += seg.duration
    return None


def write_srt(results: List[ClipResult], output_path: str) -> str:
    entries: List[Tuple[float, float, str]] = []
    clip_offset = 0.0

    for result in results:
        kept_duration = sum(s.duration for s in result.segments)

        if result.subtitles and result.segments:
            for sub in result.subtitles:
                remapped = _remap(sub, result.segments, clip_offset)
                if remapped:
                    entries.append(remapped)

        clip_offset += kept_duration

    entries.sort(key=lambda x: x[0])

    lines = []
    for i, (start, end, text) in enumerate(entries, 1):
        lines.append(str(i))
        lines.append(f"{_fmt(start)} --> {_fmt(end)}")
        lines.append(text)
        lines.append("")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    return output_path
