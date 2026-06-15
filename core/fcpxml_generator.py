from fractions import Fraction
from pathlib import Path
from typing import List, Optional
import xml.etree.ElementTree as ET
from xml.dom import minidom

from .silence_detector import ClipResult


def _frame_duration_fraction(fps: float) -> Fraction:
    """프레임 1장의 길이를 정확한 유리수로 반환."""
    known = {
        23.976: Fraction(1001, 24000),
        24.0:   Fraction(1, 24),
        25.0:   Fraction(1, 25),
        29.97:  Fraction(1001, 30000),
        30.0:   Fraction(1, 30),
        50.0:   Fraction(1, 50),
        59.94:  Fraction(1001, 60000),
        60.0:   Fraction(1, 60),
        120.0:  Fraction(1, 120),
    }
    for known_fps, frac in known.items():
        if abs(fps - known_fps) < 0.02:
            return frac
    return Fraction(1 / fps).limit_denominator(90000)


def _frame_duration(fps: float) -> str:
    fd = _frame_duration_fraction(fps)
    return f"{fd.numerator}/{fd.denominator}s"


def _seconds_to_frames(seconds: float, fd: Fraction) -> int:
    """초 단위 값을 가장 가까운 정수 프레임 수로 스냅."""
    if seconds <= 0:
        return 0
    return round(seconds / fd)


def _time_at_frames(frames: int, fd: Fraction) -> str:
    """정수 프레임 수를 FCP이 요구하는 프레임 정렬된 시간 문자열로 변환."""
    if frames <= 0:
        return "0s"
    total = fd * frames
    return f"{total.numerator}/{total.denominator}s"


# Namsieon YT 자막 모션 템플릿 (FCP에 설치되어 있어야 함).
# uid의 '~'는 사용자의 Motion Templates 폴더로 해석되어 설치 위치와 무관하게 동작.
_SUBTITLE_EFFECT_NAME = 'Namsieon YT'
_SUBTITLE_EFFECT_UID = '~/Titles.localized/!Whisper Auto Caption/Namsieon YT/Namsieon YT.moti'


def _add_title(parent, effect_id: str, text: str,
               offset_frames: int, dur_frames: int, fd: Fraction, ts_id: str):
    """asset-clip 안 lane 1에 자막 title을 중첩 추가."""
    title = ET.SubElement(parent, 'title',
        ref=effect_id,
        lane='1',
        offset=_time_at_frames(offset_frames, fd),
        name=text[:40],
        start='0s',
        duration=_time_at_frames(dur_frames, fd))
    text_el = ET.SubElement(title, 'text')
    style = ET.SubElement(text_el, 'text-style', ref=ts_id)
    style.text = text
    tsdef = ET.SubElement(title, 'text-style-def', id=ts_id)
    ET.SubElement(tsdef, 'text-style',
        font='Helvetica', fontSize='28.9', fontFace='Regular',
        fontColor='1 1 1 1', alignment='center')


def generate_fcpxml(results: List[ClipResult], output_path: str,
                    fps_override: Optional[float] = None,
                    embed_subtitles: bool = False) -> str:
    valid = [r for r in results if r.error is None and r.segments]
    if not valid:
        raise ValueError("내보낼 수 있는 클립이 없습니다.")

    first = valid[0].info
    fps = fps_override if fps_override is not None else first.fps
    width = first.width
    height = first.height
    fd = _frame_duration_fraction(fps)

    root = ET.Element('fcpxml', version='1.10')
    resources = ET.SubElement(root, 'resources')

    # name 속성은 FCP 내장 프리셋 이름과 정확히 일치할 때만 유효하다.
    # 비표준 해상도(2.7K, 4K 등)에서는 이름을 생략한 커스텀 포맷이 안전하다.
    ET.SubElement(resources, 'format',
        id='r1',
        frameDuration=_frame_duration(fps),
        width=str(width),
        height=str(height),
        colorSpace='1-1-1 (Rec. 709)')

    for i, result in enumerate(valid):
        asset_id = f'r{i + 2}'
        src_uri = Path(result.info.path).resolve().as_uri()
        # DJI 등은 촬영 시각 기반 내장 타임코드를 가진다. 원본 프레임은 0초가 아니라
        # 이 타임코드 지점부터 번호가 매겨지므로, asset/asset-clip의 start를 거기에 맞춘다.
        tc_frames = _seconds_to_frames(result.info.start_tc, fd)
        asset_frames = _seconds_to_frames(result.info.duration, fd)
        asset_elem = ET.SubElement(resources, 'asset',
            id=asset_id,
            name=Path(result.info.path).stem,
            format='r1',
            start=_time_at_frames(tc_frames, fd),
            duration=_time_at_frames(asset_frames, fd),
            hasVideo='1',
            hasAudio='1',
            videoSources='1',
            audioSources='1',
            audioChannels='2',
            audioRate='48000')
        ET.SubElement(asset_elem, 'media-rep',
            kind='original-media',
            src=src_uri)

    # 자막 임베드용 모션 템플릿 effect (asset 다음 id)
    sub_effect_id = None
    if embed_subtitles and any(r.subtitles for r in valid):
        sub_effect_id = f'r{len(valid) + 2}'
        ET.SubElement(resources, 'effect',
            id=sub_effect_id,
            name=_SUBTITLE_EFFECT_NAME,
            uid=_SUBTITLE_EFFECT_UID)

    library = ET.SubElement(root, 'library')
    event = ET.SubElement(library, 'event', name='Auto Cut')
    project = ET.SubElement(event, 'project', name='Auto Cut')

    sequence = ET.SubElement(project, 'sequence',
        format='r1',
        tcStart='0s',
        tcFormat='NDF',
        audioLayout='stereo',
        audioRate='48k')

    spine = ET.SubElement(sequence, 'spine')

    # 타임라인 위치를 정수 프레임으로 누적해 모든 컷이 프레임 경계에 정렬되도록 한다.
    timeline_frames = 0
    ts_seq = 0   # text-style-def 고유 id 카운터
    for i, result in enumerate(valid):
        asset_id = f'r{i + 2}'
        clip_name = Path(result.info.path).stem
        tc_frames = _seconds_to_frames(result.info.start_tc, fd)
        for seg in result.segments:
            seg_frames = _seconds_to_frames(seg.duration, fd)
            if seg_frames <= 0:
                continue
            # 소스 in-point = 내장 타임코드 베이스 + 세그먼트 시작 오프셋
            start_frames = tc_frames + _seconds_to_frames(seg.start, fd)
            clip_el = ET.SubElement(spine, 'asset-clip',
                name=clip_name,
                ref=asset_id,
                offset=_time_at_frames(timeline_frames, fd),
                duration=_time_at_frames(seg_frames, fd),
                start=_time_at_frames(start_frames, fd),
                format='r1',
                tcFormat='NDF')

            # 이 세그먼트와 겹치는 자막을 title로 삽입.
            # 중첩 title의 offset은 asset-clip의 소스 타임코드 공간(tc_frames 기준)을 따른다.
            if sub_effect_id:
                seg_end = seg.start + seg.duration
                for sub in result.subtitles:
                    if not sub.text or sub.end <= seg.start or sub.start >= seg_end:
                        continue
                    s_start = max(sub.start, seg.start)
                    s_end = min(sub.end, seg_end)
                    t_dur = _seconds_to_frames(s_end - s_start, fd)
                    if t_dur <= 0:
                        continue
                    t_off = tc_frames + _seconds_to_frames(s_start, fd)
                    ts_seq += 1
                    _add_title(clip_el, sub_effect_id, sub.text,
                               t_off, t_dur, fd, f'ts{ts_seq}')

            timeline_frames += seg_frames

    sequence.set('duration', _time_at_frames(timeline_frames, fd))

    xml_str = ET.tostring(root, encoding='unicode')
    dom = minidom.parseString(xml_str)
    pretty = dom.toprettyxml(indent='    ', encoding=None)

    lines = pretty.split('\n')
    lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'
    lines.insert(1, '<!DOCTYPE fcpxml>')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    return output_path
