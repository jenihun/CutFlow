"""README용 스크린샷 생성기 — 모의 데이터로 각 화면을 PNG로 저장."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from core.silence_detector import VideoInfo, Segment, SubtitleEntry, ClipResult

app = QApplication([])
from ui.main_window import MainWindow

OUT = os.path.join(os.path.dirname(__file__), 'images')
W, H = 700, 800

# ── 모의 데이터 ──────────────────────────────────────────────
info = VideoInfo(path='/Volumes/SSD/2025.10/DJI_0042_D.mp4', name='DJI_0042_D.mp4',
                 duration=298.0, fps=30000 / 1001, width=2688, height=1512)
segs = [Segment(23, 31), Segment(60, 78), Segment(83, 96),
        Segment(120, 138), Segment(150, 162), Segment(200, 224)]
subs = [
    SubtitleEntry(24, 27, '오늘은 새 카메라 들고 나왔어요'),
    SubtitleEntry(61, 64, '날씨가 진짜 좋네요'),
    SubtitleEntry(64, 67, '이런 날 촬영하면 너무 좋죠'),
    SubtitleEntry(84, 88, '여기 풍경 한번 보세요'),
    SubtitleEntry(122, 126, '편집은 CutFlow로 합니다'),
    SubtitleEntry(127, 131, '무음은 자동으로 잘리거든요'),
    SubtitleEntry(151, 155, '자막도 같이 만들어줘요'),
    SubtitleEntry(201, 205, '오늘 영상 여기까지'),
    SubtitleEntry(206, 209, '구독과 좋아요 부탁드려요'),
]


def grab(w, name):
    w.resize(W, H)
    app.processEvents()
    w.grab().save(os.path.join(OUT, name))
    print('saved', name)


# 1) 설정 화면
w = MainWindow()
w.file_paths = [info.path, '/Volumes/SSD/2025.10/DJI_0043_D.mp4']
w._refresh_list()
w.whisper_check.setChecked(True)
w.stack.setCurrentIndex(0)
w.status_label.setText('클립 2개 · 컷 기준: 음성 감지(VAD)')
grab(w, '1_settings.png')

# 2) 작업 화면 (실시간 자막 스트리밍 모습)
w.stack.setCurrentIndex(1)
w.progress_bar.setVisible(True)
w.progress_bar.setMaximum(2)
w.progress_bar.setValue(1)
w.pause_btn.setVisible(True)
w.cancel_btn.setVisible(True)
w._work_results = []
w._on_clip_started(ClipResult(info=info, segments=segs))
for s in subs:
    w._on_subtitle_made(0, s)
w.work_sub_list.setCurrentRow(4)
w.work_timeline.set_highlight(info.path, 122, 126)
w.status_label.setText('자막 생성: DJI_0042_D.mp4')
grab(w, '2_work.png')

# 3) 결과 화면
res = ClipResult(info=info, segments=segs, subtitles=subs)
w.timeline.set_results([res])
w.export_btn.setEnabled(True)
w.export_srt_btn.setEnabled(True)
w.copy_subs_btn.setEnabled(True)
w.stack.setCurrentIndex(2)
w.status_label.setText('6구간 (215.0초 제거 예정)  |  자막 생성 완료')
grab(w, '3_result.png')

print('완료')
