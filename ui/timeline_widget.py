import subprocess
import shutil
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QPoint
from PyQt6.QtGui import QPainter, QPen, QColor, QFont, QBrush

from core.silence_detector import ClipResult

_FFPLAY = shutil.which('ffplay') or '/opt/homebrew/bin/ffplay'

# 클립 1개의 행 높이 / 간격
_ROW_H  = 48
_GAP    = 10
_LABEL_W = 160
_PAD    = 10
_BAR_H  = 22
_SUB_H  = 6      # 자막 레인 높이
_SUB_GAP = 3     # 본 막대와 자막 레인 사이 간격


class TimelineWidget(QWidget):
    """클립별 타임라인. 클릭 → 해당 시점 ffplay 미리보기."""

    preview_started = pyqtSignal(str)  # 상태 메시지

    def __init__(self):
        super().__init__()
        self._results: List[ClipResult] = []
        self._hover_row: int = -1
        self._hover_t: float = -1.0
        self._highlight: Optional[tuple] = None   # (path, start, end)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMinimumHeight(60)

    def set_highlight(self, path: str, start: float, end: float):
        """특정 자막 구간을 강조 표시."""
        self._highlight = (path, start, end)
        self.update()

    # ── 데이터 갱신 ───────────────────────────────────────────────────

    def set_results(self, results: List[ClipResult]):
        self._results = [r for r in results if not r.error and r.info.duration > 0]
        if not self._results:
            self._highlight = None
        h = _PAD * 2 + len(self._results) * (_ROW_H + _GAP)
        self.setMinimumHeight(max(60, h))
        self.setMaximumHeight(max(60, h))
        self.update()

    # ── 좌표 헬퍼 ─────────────────────────────────────────────────────

    def _bar_rect(self, row: int) -> QRect:
        y = _PAD + row * (_ROW_H + _GAP)
        bx = _LABEL_W
        bw = self.width() - bx - _PAD
        by = y + 5   # 막대는 위쪽, 아래에 자막 레인 공간을 남김
        return QRect(bx, by, max(bw, 1), _BAR_H)

    def _time_at(self, row: int, x: int) -> float:
        rect = self._bar_rect(row)
        rel = (x - rect.x()) / max(rect.width(), 1)
        return max(0.0, min(1.0, rel)) * self._results[row].info.duration

    def _row_at(self, y: int) -> int:
        for i in range(len(self._results)):
            ry = _PAD + i * (_ROW_H + _GAP)
            if ry <= y <= ry + _ROW_H:
                return i
        return -1

    # ── 그리기 ───────────────────────────────────────────────────────

    def paintEvent(self, event):
        if not self._results:
            p = QPainter(self)
            p.setPen(QColor('#aaa'))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       '분석을 완료하면 타임라인이 표시됩니다')
            p.end()
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        label_font = QFont()
        label_font.setPointSize(11)
        time_font = QFont()
        time_font.setPointSize(9)

        for i, result in enumerate(self._results):
            rect = self._bar_rect(i)
            dur = result.info.duration
            row_y = _PAD + i * (_ROW_H + _GAP)

            # ── 파일명 라벨
            name = result.info.name
            if len(name) > 22:
                name = name[:20] + '…'
            p.setFont(label_font)
            p.setPen(QColor('#333'))
            p.drawText(QRect(0, row_y, _LABEL_W - 6, _ROW_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, name)

            # ── 배경 (제거 구간 = 연한 빨강)
            p.fillRect(rect, QColor('#ffd6d6'))

            # ── 유지 구간 (초록)
            for seg in result.segments:
                sx = rect.x() + int(seg.start / dur * rect.width())
                sw = max(2, int(seg.duration / dur * rect.width()))
                p.fillRect(sx, rect.y(), sw, rect.height(), QColor('#43a047'))

            # ── 테두리
            p.setPen(QPen(QColor('#bbb'), 1))
            p.drawRect(rect)

            # ── 컷 포인트 마커 (파란 점선)
            cut_pen = QPen(QColor('#1565c0'), 1, Qt.PenStyle.DashLine)
            p.setPen(cut_pen)
            for seg in result.segments:
                for t in [seg.start, seg.end]:
                    if 0.05 < t < dur - 0.05:
                        mx = rect.x() + int(t / dur * rect.width())
                        p.drawLine(mx, rect.y() - 3, mx, rect.y() + rect.height() + 3)

            # ── 자막 마커 (막대 아래 노란 레인)
            sub_y = rect.y() + rect.height() + _SUB_GAP
            if result.subtitles:
                p.setPen(Qt.PenStyle.NoPen)
                for sub in result.subtitles:
                    sx = rect.x() + int(sub.start / dur * rect.width())
                    sw = max(2, int((sub.end - sub.start) / dur * rect.width()))
                    p.fillRect(sx, sub_y, sw, _SUB_H, QColor('#ffb300'))

            # ── 선택된 자막 강조 (막대 + 레인에 주황 박스)
            if self._highlight and self._highlight[0] == result.info.path:
                _, hs, he = self._highlight
                hx = rect.x() + int(hs / dur * rect.width())
                hw = max(3, int((he - hs) / dur * rect.width()))
                p.setPen(Qt.PenStyle.NoPen)
                p.fillRect(hx, sub_y, hw, _SUB_H, QColor('#ff6d00'))
                p.setPen(QPen(QColor('#ff6d00'), 2))
                p.setBrush(QBrush(QColor(255, 109, 0, 45)))
                p.drawRect(hx, rect.y(), hw, rect.height())
                p.setBrush(Qt.BrushStyle.NoBrush)

            # ── 호버 인디케이터 (주황 실선)
            if self._hover_row == i and self._hover_t >= 0:
                hx = rect.x() + int(self._hover_t / dur * rect.width())
                p.setPen(QPen(QColor('#e65100'), 2))
                p.drawLine(hx, row_y, hx, row_y + _ROW_H)

                # 시간 레이블
                p.setFont(time_font)
                p.setPen(QColor('#e65100'))
                ts = f'{self._hover_t:.1f}s'
                tx = hx + 4 if hx + 36 < rect.right() else hx - 34
                p.drawText(tx, row_y + 13, ts)

        p.end()

    # ── 마우스 이벤트 ─────────────────────────────────────────────────

    def mouseMoveEvent(self, event):
        # Qt6: event.x()/y() 제거됨 → position() 사용
        pos = event.position()
        ex, ey = int(pos.x()), int(pos.y())

        row = self._row_at(ey)
        if row >= 0:
            rect = self._bar_rect(row)
            if rect.x() <= ex <= rect.right():
                self._hover_row = row
                self._hover_t = self._time_at(row, ex)
                self.update()
                return

        if self._hover_row != -1:
            self._hover_row = -1
            self._hover_t = -1.0
            self.update()

    def mousePressEvent(self, event):
        pos = event.position()
        ex, ey = int(pos.x()), int(pos.y())

        row = self._row_at(ey)
        if row < 0:
            return
        rect = self._bar_rect(row)
        if not (rect.x() <= ex <= rect.right()):
            return

        result = self._results[row]
        t = self._time_at(row, ex)
        self._play_preview(result.info.path, t, result.info.duration)

    def leaveEvent(self, event):
        self._hover_row = -1
        self._hover_t = -1.0
        self.update()

    # ── ffplay 미리보기 ───────────────────────────────────────────────

    def _play_preview(self, path: str, seek_t: float, duration: float):
        window = 5.0
        start = max(0.0, seek_t - window / 2)
        actual_dur = min(window, duration - start)

        name = Path(path).name
        self.preview_started.emit(f'▶  {name}  —  {seek_t:.1f}초 부근 재생 중')

        subprocess.Popen([
            _FFPLAY,
            '-ss', str(start),
            '-i', path,
            '-t', str(actual_dur),
            '-autoexit',
            '-x', '800', '-y', '450',
            '-window_title', f'미리보기 — {name} ({seek_t:.1f}s)',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
