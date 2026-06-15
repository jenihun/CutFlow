"""generate_fcpxml의 이벤트명/프로젝트명 인자 검증.

pytest 없이 `python3 tests/test_fcpxml_names.py`로 직접 실행 가능.
"""
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.silence_detector import VideoInfo, Segment, ClipResult
from core.fcpxml_generator import generate_fcpxml


def _make_results():
    info = VideoInfo(
        path='/tmp/sample_clip.mp4',
        name='sample_clip.mp4',
        duration=10.0,
        fps=30.0,
        width=1920,
        height=1080,
    )
    return [ClipResult(info=info, segments=[Segment(0.0, 5.0)])]


def test_custom_event_and_project_names():
    out = Path(tempfile.gettempdir()) / 'cutflow_test_names.fcpxml'
    generate_fcpxml(_make_results(), str(out),
                    event_name='나의 이벤트',
                    project_name='2026.06.15')
    tree = ET.parse(out)
    event = tree.find('.//event')
    project = tree.find('.//project')
    assert event is not None and project is not None, 'event/project 요소가 없음'
    assert event.get('name') == '나의 이벤트', f"이벤트명 불일치: {event.get('name')}"
    assert project.get('name') == '2026.06.15', f"프로젝트명 불일치: {project.get('name')}"
    out.unlink(missing_ok=True)


def test_default_names_backward_compatible():
    """인자를 생략하면 기존 동작('Auto Cut') 유지."""
    out = Path(tempfile.gettempdir()) / 'cutflow_test_default.fcpxml'
    generate_fcpxml(_make_results(), str(out))
    tree = ET.parse(out)
    assert tree.find('.//event').get('name') == 'Auto Cut'
    assert tree.find('.//project').get('name') == 'Auto Cut'
    out.unlink(missing_ok=True)


if __name__ == '__main__':
    test_custom_event_and_project_names()
    test_default_names_backward_compatible()
    print('✅ 모든 테스트 통과')
