import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem,
    QSlider, QFileDialog, QProgressBar, QFrame,
    QMessageBox, QCheckBox, QComboBox,
    QLineEdit, QApplication, QScrollArea, QStackedWidget, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

from core.silence_detector import detect_keep_segments, ClipResult
from core.fcpxml_generator import generate_fcpxml
from core.srt_writer import write_srt
from core.ai_advisor import (
    get_recommendations, save_api_key, load_api_key, AIRecommendation
)
from ui.timeline_widget import TimelineWidget

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.mts', '.m2ts', '.mkv', '.avi', '.insv'}


# ── AI Worker ────────────────────────────────────────────────────────────────

class NoiseDetectWorker(QThread):
    finished = pyqtSignal(float)

    def __init__(self, file_path: str, nice_level: int):
        super().__init__()
        self.file_path = file_path
        self.nice_level = nice_level

    def run(self):
        from core.silence_detector import detect_noise_floor
        result = detect_noise_floor(self.file_path, self.nice_level)
        self.finished.emit(result)


class AIWorker(QThread):
    finished = pyqtSignal(object)   # AIRecommendation
    error = pyqtSignal(str)

    def __init__(self, provider: str, api_key: str, results: List[ClipResult]):
        super().__init__()
        self.provider = provider
        self.api_key = api_key
        self.results = results

    def run(self):
        try:
            rec = get_recommendations(self.provider, self.api_key, self.results)
            self.finished.emit(rec)
        except Exception as e:
            self.error.emit(str(e))


# ── Analysis Worker ───────────────────────────────────────────────────────────

class AnalysisWorker(QThread):
    progress = pyqtSignal(int, int, str)
    clip_started = pyqtSignal(object)       # ClipResult (무음 감지 직후, 자막 전)
    subtitle_made = pyqtSignal(int, object)  # (클립 index, SubtitleEntry) 실시간
    clip_done = pyqtSignal(object)
    finished = pyqtSignal(list)

    def __init__(self, file_paths, noise_db, min_duration,
                 whisper_enabled, whisper_model_size, language, nice_level,
                 cut_mode='vad'):
        super().__init__()
        self.file_paths = file_paths
        self.noise_db = noise_db
        self.min_duration = min_duration
        self.whisper_enabled = whisper_enabled
        self.whisper_model_size = whisper_model_size
        self.language = language
        self.nice_level = nice_level
        self.cut_mode = cut_mode

        self._paused = False
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._current_proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()
        self._stop = False

    # ── pause / resume / stop ────────────────────────────────────────

    def toggle_pause(self):
        if self._paused:
            self._do_resume()
        else:
            self._do_pause()

    def _do_pause(self):
        self._paused = True
        self._pause_event.clear()
        with self._proc_lock:
            if self._current_proc and self._current_proc.poll() is None:
                try:
                    os.kill(self._current_proc.pid, signal.SIGSTOP)
                except OSError:
                    pass

    def _do_resume(self):
        with self._proc_lock:
            if self._current_proc and self._current_proc.poll() is None:
                try:
                    os.kill(self._current_proc.pid, signal.SIGCONT)
                except OSError:
                    pass
        self._paused = False
        self._pause_event.set()

    def stop(self):
        self._stop = True
        self._pause_event.set()
        with self._proc_lock:
            if self._current_proc and self._current_proc.poll() is None:
                self._current_proc.terminate()

    def _register_proc(self, proc: subprocess.Popen):
        with self._proc_lock:
            self._current_proc = proc
            if self._paused:
                try:
                    os.kill(proc.pid, signal.SIGSTOP)
                except OSError:
                    pass

    def _clear_proc(self):
        with self._proc_lock:
            self._current_proc = None

    def _checkpoint(self) -> bool:
        """자막 청크 사이 호출 — 일시정지 시 대기, 중단 요청 시 False."""
        self._pause_event.wait()
        return not self._stop

    # ── main loop ────────────────────────────────────────────────────

    def run(self):
        whisper_model = None
        if self.whisper_enabled:
            self.progress.emit(0, len(self.file_paths), 'Whisper 모델 로딩 중…')
            try:
                from core.transcriber import load_model
                whisper_model = load_model(self.whisper_model_size, self.nice_level)
            except Exception as e:
                self.progress.emit(0, len(self.file_paths), f'모델 로드 실패: {e}')

        results: List[ClipResult] = []
        total = len(self.file_paths)

        for i, path in enumerate(self.file_paths):
            self._pause_event.wait()
            if self._stop:
                break

            filename = Path(path).name

            # 1. 컷 구간 감지 (VAD 음성 기준 또는 dB 음량 기준)
            if self.cut_mode == 'vad':
                self.progress.emit(i, total, f'음성 감지: {filename}')
                from core.silence_detector import detect_keep_segments_vad
                result = detect_keep_segments_vad(
                    path, self.min_duration,
                    nice_level=self.nice_level,
                    on_proc=self._register_proc,
                )
            else:
                self.progress.emit(i, total, f'무음 감지: {filename}')
                result = detect_keep_segments(
                    path, self.noise_db, self.min_duration,
                    nice_level=self.nice_level,
                    on_proc=self._register_proc,
                )
            self._clear_proc()
            self.clip_started.emit(result)   # 컷을 타임라인에 바로 표시

            if self._stop:
                break

            # 2. Whisper 자막 (생성되는 대로 실시간 전송)
            if whisper_model and not result.error:
                self._pause_event.wait()
                if self._stop:
                    break
                self.progress.emit(i, total, f'자막 생성: {filename}')
                try:
                    from core.transcriber import transcribe
                    result.subtitles = transcribe(
                        path, whisper_model,
                        language=self.language,
                        nice_level=self.nice_level,
                        on_proc=self._register_proc,
                        on_subtitle=lambda s, idx=i: self.subtitle_made.emit(idx, s),
                        checkpoint=self._checkpoint,
                    )
                    self._clear_proc()
                except Exception as e:
                    import traceback
                    print(f'[자막 생성 실패] {filename}: {e}')
                    traceback.print_exc()
                    result.subtitles = []

            results.append(result)
            self.clip_done.emit(result)

        if not self._stop:
            self.progress.emit(total, total, '완료')
            self.finished.emit(results)


# ── Drop Zone ─────────────────────────────────────────────────────────────────

class DropZone(QFrame):
    files_dropped = pyqtSignal(list)
    files_selected = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(120)
        self._idle()

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(5)

        icon = QLabel('🎬')
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet('font-size: 28px; border: none;')

        text = QLabel('영상 클립을 여기에 드래그하세요')
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text.setStyleSheet('color: #555; font-size: 14px; border: none;')

        or_lbl = QLabel('또는')
        or_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        or_lbl.setStyleSheet('color: #999; font-size: 11px; border: none;')

        btn = QPushButton('파일 선택')
        btn.setFixedWidth(88)
        btn.setStyleSheet('''
            QPushButton {
                background: transparent; color: #0066cc;
                border: 1px solid #0066cc; border-radius: 4px;
                padding: 3px 8px; font-size: 12px;
            }
            QPushButton:hover { background: #e8f0fe; }
        ''')
        btn.clicked.connect(self._open_dialog)

        layout.addWidget(icon)
        layout.addWidget(text)
        layout.addWidget(or_lbl)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignHCenter)

    def _idle(self):
        self.setStyleSheet('DropZone { border: 2px dashed #bbb; border-radius: 10px; background: #f8f8f8; }')

    def _hover(self):
        self.setStyleSheet('DropZone { border: 2px dashed #0066cc; border-radius: 10px; background: #e8f0fe; }')

    def _open_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, '영상 파일 선택', str(Path.home()),
            '영상 파일 (*.mp4 *.mov *.m4v *.mts *.m2ts *.mkv *.avi *.insv *.MP4 *.MOV *.MTS)'
        )
        if paths:
            self.files_selected.emit(paths)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._hover()

    def dragLeaveEvent(self, event):
        self._idle()

    def dropEvent(self, event):
        self._idle()
        paths = [
            url.toLocalFile() for url in event.mimeData().urls()
            if Path(url.toLocalFile()).suffix.lower() in VIDEO_EXTENSIONS
        ]
        if paths:
            self.files_dropped.emit(paths)


# ── Main Window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.file_paths: List[str] = []
        self.results: List[ClipResult] = []
        self.worker: Optional[AnalysisWorker] = None
        self.ai_worker: Optional[AIWorker] = None
        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle('CutFlow')
        self.setMinimumSize(640, 720)
        self._work_results: List[ClipResult] = []

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 3단계 화면 스택
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.stack.addWidget(self._wrap_scroll(self._build_settings_page()))
        self.stack.addWidget(self._wrap_scroll(self._build_work_page()))
        self.stack.addWidget(self._wrap_scroll(self._build_results_page()))

        # 항상 보이는 하단 상태바 (모든 화면 공용)
        self.status_label = QLabel('')
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            'color: #333333; font-size: 12px; padding: 7px 18px; '
            'background: #f4f4f4; border-top: 1px solid #e2e2e2;'
        )
        outer.addWidget(self.status_label)

        self.stack.setCurrentIndex(0)

    def _wrap_scroll(self, w: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(w)
        return scroll

    def _step_header(self, active: int) -> QWidget:
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 6)
        h.setSpacing(6)
        for i, name in enumerate(['1. 설정', '2. 작업', '3. 결과']):
            lbl = QLabel(name)
            if i == active:
                lbl.setStyleSheet('background:#0066cc; color:white; border-radius:11px; '
                                  'padding:4px 12px; font-weight:bold; font-size:12px;')
            else:
                lbl.setStyleSheet('background:#e8e8e8; color:#999; border-radius:11px; '
                                  'padding:4px 12px; font-size:12px;')
            h.addWidget(lbl)
            if i < 2:
                arrow = QLabel('→')
                arrow.setStyleSheet('color:#bbb; border:none;')
                h.addWidget(arrow)
        h.addStretch()
        return bar

    # ── 1. 설정 화면 ───────────────────────────────────────────────────

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setSpacing(12)
        root.setContentsMargins(20, 18, 20, 18)

        root.addWidget(self._step_header(0))

        # Drop zone
        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._add_files)
        self.drop_zone.files_selected.connect(self._add_files)
        root.addWidget(self.drop_zone)

        # Clip list header
        row = QHBoxLayout()
        row.addWidget(self._bold_label('클립 목록'))
        row.addStretch()
        clear_btn = QPushButton('지우기')
        clear_btn.setFixedWidth(58)
        clear_btn.clicked.connect(self._clear_files)
        row.addWidget(clear_btn)
        root.addLayout(row)

        # Clip list
        self.file_list = QListWidget()
        self.file_list.setMinimumHeight(120)
        self.file_list.setStyleSheet('''
            QListWidget {
                border: 1px solid #d0d0d0; border-radius: 6px;
                background: white; color: #111111; font-size: 13px;
            }
            QListWidget::item { padding: 4px 6px; color: #111111; }
            QListWidget::item:selected { background: #e8f0fe; color: #111111; }
        ''')
        root.addWidget(self.file_list)

        # Settings box
        root.addWidget(self._bold_label('설정'))
        box = self._make_box()
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(14, 10, 14, 12)
        box_layout.setSpacing(10)

        # 컷 기준 (음성 VAD / 음량 dB)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self._lbl('컷 기준:'))
        self.cut_mode_combo = QComboBox()
        self.cut_mode_combo.addItems(['음성 감지 (VAD · 권장)', '음량 기준 (dB)'])
        self.cut_mode_combo.setToolTip(
            '음성 감지: 사람 목소리가 있는 구간만 남김 — 엔진음·바람 등 큰 소음에도 정확\n'
            '음량 기준: 설정한 dB 이하를 무음으로 간주 (단순 영상에 적합)'
        )
        self.cut_mode_combo.currentIndexChanged.connect(lambda _: self._apply_cut_mode_state())
        mode_row.addWidget(self.cut_mode_combo)
        mode_row.addStretch()
        box_layout.addLayout(mode_row)
        box_layout.addWidget(self._divider())

        # Noise slider + 자동 감지 버튼
        noise_row = self._slider_row(
            '무음 기준', -60, -10, -30,
            display_fn=str,
            parse_fn=lambda s: int(float(s)),
            unit='dB',
            slider_attr='noise_slider', val_attr='noise_val'
        )
        self.auto_detect_btn = QPushButton('🎯 자동')
        self.auto_detect_btn.setFixedWidth(62)
        self.auto_detect_btn.setFixedHeight(26)
        self.auto_detect_btn.setEnabled(False)
        self.auto_detect_btn.setToolTip('첫 번째 클립의 오디오를 분석해 최적 무음 기준을 자동 설정합니다')
        self.auto_detect_btn.setStyleSheet('''
            QPushButton {
                background: #f0f4ff; color: #0055cc;
                border: 1px solid #99bbee; border-radius: 5px; font-size: 11px;
            }
            QPushButton:hover { background: #dde8ff; }
            QPushButton:disabled { background: #f0f0f0; color: #aaaaaa; border-color: #ddd; }
        ''')
        self.auto_detect_btn.clicked.connect(self._auto_detect_threshold)
        noise_row.addWidget(self.auto_detect_btn)
        box_layout.addLayout(noise_row)

        # Duration slider
        box_layout.addLayout(self._slider_row(
            '최소 길이', 1, 30, 5,
            display_fn=lambda v: f'{v/10:.1f}',
            parse_fn=lambda s: round(float(s) * 10),
            unit='초',
            slider_attr='dur_slider', val_attr='dur_val'
        ))

        box_layout.addWidget(self._divider())

        # Whisper
        whisper_row = QHBoxLayout()
        self.whisper_check = QCheckBox('Whisper AI 자막 자동 생성')
        self.whisper_check.setStyleSheet('border: none; color: #111111;')
        self.whisper_check.toggled.connect(self._toggle_whisper)
        whisper_row.addWidget(self.whisper_check)
        whisper_row.addStretch()
        box_layout.addLayout(whisper_row)

        self.whisper_options = QWidget()
        self.whisper_options.setVisible(False)
        wo_layout = QVBoxLayout(self.whisper_options)
        wo_layout.setContentsMargins(20, 0, 0, 0)
        wo_layout.setSpacing(6)

        # 1행: 모델 + 언어
        wo_row1 = QHBoxLayout()
        wo_row1.setSpacing(8)
        wo_row1.addWidget(self._lbl('모델:'))
        self.model_combo = QComboBox()
        self.model_combo.addItems([
            'tiny (빠름)',
            'base',
            'small',
            'medium',
            'large-v3',
            'large-v3-turbo (최신 권장)',
        ])
        self.model_combo.setCurrentIndex(5)
        wo_row1.addWidget(self.model_combo)
        wo_row1.addSpacing(16)
        wo_row1.addWidget(self._lbl('언어:'))
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(['한국어', 'English', '日本語', '中文'])
        wo_row1.addWidget(self.lang_combo)
        wo_row1.addStretch()
        wo_layout.addLayout(wo_row1)

        # 2행: 프레임 레이트
        wo_row2 = QHBoxLayout()
        wo_row2.setSpacing(8)
        wo_row2.addWidget(self._lbl('프레임 레이트:'))
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(['원본 유지', '23.976', '24', '25', '29.97', '30', '50', '59.94', '60'])
        wo_row2.addWidget(self.fps_combo)
        wo_row2.addStretch()
        wo_layout.addLayout(wo_row2)

        # 3행: FCPXML 자막 삽입 (Namsieon YT title 템플릿)
        wo_row3 = QHBoxLayout()
        self.embed_subs_check = QCheckBox('FCPXML에 자막 삽입 (Namsieon YT 템플릿)')
        self.embed_subs_check.setStyleSheet('border: none; color: #111111;')
        self.embed_subs_check.setToolTip(
            'FCPXML 내보낼 때 Whisper 자막을 FCP title 클립으로 삽입합니다.\n'
            'Namsieon YT 모션 템플릿이 FCP에 설치되어 있어야 합니다.'
        )
        wo_row3.addWidget(self.embed_subs_check)
        wo_row3.addStretch()
        wo_layout.addLayout(wo_row3)

        box_layout.addWidget(self.whisper_options)

        box_layout.addWidget(self._divider())

        # CPU 사용량
        cpu_row = QHBoxLayout()
        self.cpu_bg_check = QCheckBox('백그라운드 모드 (저소음)')
        self.cpu_bg_check.setStyleSheet('border: none; color: #111111;')
        self.cpu_bg_check.setToolTip(
            '체크 시 CPU 우선순위를 낮춰 팬 소음 최소화\n'
            '분석 중 다른 작업을 할 때 유용 — 처리 속도는 약간 느려집니다'
        )
        cpu_row.addWidget(self.cpu_bg_check)
        cpu_row.addStretch()
        box_layout.addLayout(cpu_row)

        root.addWidget(box)
        root.addStretch()

        # 작업 시작
        self.analyze_btn = self._action_btn('작업 시작  →', '#0066cc', '#0055aa')
        self.analyze_btn.clicked.connect(self._start_analysis)
        root.addWidget(self.analyze_btn)

        self._apply_cut_mode_state()   # 초기 컷 기준 상태 반영
        return page

    def _get_cut_mode(self) -> str:
        return 'db' if self.cut_mode_combo.currentIndex() == 1 else 'vad'

    def _apply_cut_mode_state(self):
        """dB 모드일 때만 무음 슬라이더·자동 버튼을 활성화."""
        is_db = self._get_cut_mode() == 'db'
        self.noise_slider.setEnabled(is_db)
        self.noise_val.setEnabled(is_db)
        self.auto_detect_btn.setEnabled(is_db and bool(self.file_paths))

    # ── 2. 작업 화면 ───────────────────────────────────────────────────

    def _build_work_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setSpacing(12)
        root.setContentsMargins(20, 18, 20, 18)

        root.addWidget(self._step_header(1))

        # 진행 행
        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet('''
            QProgressBar {
                border: none; border-radius: 6px;
                background: #e4e4e4;
                min-height: 10px; max-height: 10px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #0055cc, stop:1 #0099ff);
                border-radius: 6px;
            }
        ''')
        self.progress_bar.setVisible(False)
        self.pause_btn = QPushButton('일시정지')
        self.pause_btn.setFixedWidth(80)
        self.pause_btn.setVisible(False)
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.cancel_btn = QPushButton('취소')
        self.cancel_btn.setFixedWidth(60)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setStyleSheet('''
            QPushButton {
                background: #dc3545; color: white;
                border-radius: 5px; font-size: 13px; font-weight: bold;
                border: none;
            }
            QPushButton:hover { background: #c82333; }
        ''')
        self.cancel_btn.clicked.connect(self._cancel_analysis)
        progress_row.addWidget(self.progress_bar)
        progress_row.addWidget(self.pause_btn)
        progress_row.addWidget(self.cancel_btn)
        root.addLayout(progress_row)

        # 작업 중 타임라인 (컷 + 자막이 채워지는 모습)
        root.addWidget(self._bold_label('타임라인  —  초록: 유지 / 빨강: 제거 / 노랑: 자막'))
        tl_scroll = QScrollArea()
        tl_scroll.setWidgetResizable(True)
        tl_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tl_scroll.setMinimumHeight(90)
        tl_scroll.setMaximumHeight(260)
        self.work_timeline = TimelineWidget()
        tl_scroll.setWidget(self.work_timeline)
        root.addWidget(tl_scroll)

        # 생성된 자막 리스트 (몇 분 몇 초에 어떤 자막)
        root.addWidget(self._bold_label('생성된 자막'))
        self.work_sub_list = QListWidget()
        self.work_sub_list.setMinimumHeight(150)
        self.work_sub_list.setToolTip('자막을 클릭하면 타임라인에서 위치가 강조됩니다')
        self.work_sub_list.setStyleSheet('''
            QListWidget {
                border: 1px solid #d0d0d0; border-radius: 6px;
                background: white; color: #111111; font-size: 13px;
            }
            QListWidget::item { padding: 4px 8px; border-bottom: 1px solid #f0f0f0; }
            QListWidget::item:selected { background: #fff3d6; color: #111111; }
        ''')
        self.work_sub_list.itemClicked.connect(self._on_sub_list_clicked)
        root.addWidget(self.work_sub_list, 1)

        # 화면 이동
        nav = QHBoxLayout()
        self.back_to_settings_btn = self._action_btn('←  설정', '#6b7785', '#586471')
        self.back_to_settings_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        self.goto_results_btn = self._action_btn('결과 보기  →', '#28a745', '#218838')
        self.goto_results_btn.setEnabled(False)
        self.goto_results_btn.clicked.connect(lambda: self.stack.setCurrentIndex(2))
        nav.addWidget(self.back_to_settings_btn)
        nav.addStretch()
        nav.addWidget(self.goto_results_btn)
        root.addLayout(nav)

        return page

    # ── 3. 결과 화면 ───────────────────────────────────────────────────

    def _build_results_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setSpacing(12)
        root.setContentsMargins(20, 18, 20, 18)

        root.addWidget(self._step_header(2))

        # 최종 타임라인 (클릭 미리보기)
        root.addWidget(self._bold_label('최종 타임라인'))
        self.preview_frame = self._build_preview_section()
        root.addWidget(self.preview_frame)

        # 내보내기
        root.addWidget(self._bold_label('내보내기'))
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.export_btn = self._action_btn('FCPXML 내보내기', '#28a745', '#218838')
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        self.export_srt_btn = self._action_btn('SRT 내보내기', '#17a2b8', '#138496')
        self.export_srt_btn.setEnabled(False)
        self.export_srt_btn.setToolTip('Whisper 자막을 SRT 파일로 저장')
        self.export_srt_btn.clicked.connect(self._export_srt)
        self.copy_subs_btn = self._action_btn('📋 자막 복사', '#7048e8', '#5f3dc4')
        self.copy_subs_btn.setEnabled(False)
        self.copy_subs_btn.setToolTip('자막 전체를 클립보드로 복사 — claude.ai에 붙여넣어 제목·썸네일 추천을 받아보세요')
        self.copy_subs_btn.clicked.connect(self._copy_subtitle_text)
        btn_row.addWidget(self.export_btn)
        btn_row.addWidget(self.export_srt_btn)
        btn_row.addWidget(self.copy_subs_btn)
        root.addLayout(btn_row)

        # AI 섹션
        self.ai_toggle_btn = QPushButton('🤖  AI 제목 / 썸네일 추천  ▸')
        self.ai_toggle_btn.setCheckable(True)
        self.ai_toggle_btn.setStyleSheet('''
            QPushButton {
                background: #f0f0f0; color: #333;
                border: 1px solid #ccc; border-radius: 8px;
                font-size: 13px; font-weight: bold;
                padding: 8px; text-align: left;
            }
            QPushButton:checked { background: #e8f0fe; border-color: #0066cc; color: #0066cc; }
            QPushButton:hover { background: #e8e8e8; }
        ''')
        self.ai_toggle_btn.toggled.connect(self._toggle_ai_section)
        root.addWidget(self.ai_toggle_btn)

        self.ai_frame = self._build_ai_section()
        self.ai_frame.setVisible(False)
        root.addWidget(self.ai_frame)

        root.addStretch()

        # 처음으로 (새 작업)
        nav = QHBoxLayout()
        back_btn = self._action_btn('←  처음으로 (새 작업)', '#6b7785', '#586471')
        back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        nav.addWidget(back_btn)
        nav.addStretch()
        root.addLayout(nav)

        return page

    # ── 미리보기 섹션 빌더 ─────────────────────────────────────────────

    def _build_preview_section(self) -> QFrame:
        frame = self._make_box()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)

        hint = QLabel('타임라인을 클릭하면 해당 시점 앞뒤 5초를 미리볼 수 있어요  '
                      '|  초록: 유지  /  빨강: 제거  /  파란 점선: 컷 포인트')
        hint.setStyleSheet('color: #444444; font-size: 11px; border: none;')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 스크롤 가능한 타임라인
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(80)
        scroll.setMaximumHeight(320)

        self.timeline = TimelineWidget()
        self.timeline.preview_started.connect(self._on_preview_started)
        scroll.setWidget(self.timeline)
        layout.addWidget(scroll)

        self.preview_status = QLabel('— 클릭해서 미리보기')
        self.preview_status.setStyleSheet('color: #333333; font-size: 12px; border: none;')
        layout.addWidget(self.preview_status)

        return frame

    def _on_preview_started(self, msg: str):
        self.preview_status.setText(msg)

    # ── AI 섹션 빌더 ───────────────────────────────────────────────────

    def _build_ai_section(self) -> QFrame:
        frame = self._make_box()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        # API 설정 행
        api_row = QHBoxLayout()
        api_row.setSpacing(8)

        provider_lbl = QLabel('AI:')
        provider_lbl.setStyleSheet('border: none; color: #111111; font-weight: bold;')
        provider_lbl.setFixedWidth(22)
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(['Gemini', 'Claude', 'ChatGPT'])
        self.provider_combo.setFixedWidth(100)
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)

        key_lbl = QLabel('API 키:')
        key_lbl.setStyleSheet('border: none; color: #111111;')
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText('AIza... / sk-ant-... / sk-...')
        self.api_key_input.setStyleSheet('color: #111111; background: white; border: 1px solid #d0d0d0; border-radius: 4px; padding: 3px 6px;')
        self.api_key_input.setText(load_api_key('Gemini'))

        save_key_btn = QPushButton('저장')
        save_key_btn.setFixedWidth(50)
        save_key_btn.setStyleSheet('border: 1px solid #ccc; border-radius: 4px; padding: 3px;')
        save_key_btn.clicked.connect(self._save_api_key)

        api_row.addWidget(provider_lbl)
        api_row.addWidget(self.provider_combo)
        api_row.addWidget(key_lbl)
        api_row.addWidget(self.api_key_input)
        api_row.addWidget(save_key_btn)
        layout.addLayout(api_row)

        # 실행 행
        run_row = QHBoxLayout()
        self.ai_run_btn = QPushButton('AI 추천 받기')
        self.ai_run_btn.setFixedHeight(36)
        self.ai_run_btn.setStyleSheet('''
            QPushButton {
                background: #6f42c1; color: white;
                border-radius: 7px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background: #5a32a3; }
            QPushButton:disabled { background: #ccc; color: #888; }
        ''')
        self.ai_run_btn.clicked.connect(self._run_ai)
        self.ai_status_lbl = QLabel('자막 생성 완료 후 사용 가능')
        self.ai_status_lbl.setStyleSheet('color: #888; font-size: 12px; border: none;')
        run_row.addWidget(self.ai_run_btn)
        run_row.addWidget(self.ai_status_lbl)
        run_row.addStretch()
        layout.addLayout(run_row)

        layout.addWidget(self._divider())

        # 결과 영역 (초기 숨김)
        self.ai_results_frame = QWidget()
        self.ai_results_frame.setVisible(False)
        res_layout = QVBoxLayout(self.ai_results_frame)
        res_layout.setContentsMargins(0, 0, 0, 0)
        res_layout.setSpacing(10)

        # 제목 섹션
        title_header = QHBoxLayout()
        t_lbl = QLabel('📝  유튜브 제목 추천')
        t_lbl.setStyleSheet('font-weight: bold; font-size: 13px; border: none; color: #111111;')
        copy_titles_btn = QPushButton('전체 복사')
        copy_titles_btn.setFixedWidth(70)
        copy_titles_btn.setStyleSheet('border: 1px solid #ccc; border-radius: 4px; padding: 3px; font-size: 11px;')
        copy_titles_btn.clicked.connect(lambda: self._copy_section('titles'))
        title_header.addWidget(t_lbl)
        title_header.addStretch()
        title_header.addWidget(copy_titles_btn)
        res_layout.addLayout(title_header)

        self.title_list = QListWidget()
        self.title_list.setMaximumHeight(140)
        self.title_list.setStyleSheet('''
            QListWidget {
                border: 1px solid #ddd; border-radius: 6px;
                background: white; font-size: 13px;
            }
            QListWidget::item { padding: 5px 8px; }
            QListWidget::item:selected { background: #e8f0fe; color: #333; }
        ''')
        self.title_list.itemClicked.connect(
            lambda item: QApplication.clipboard().setText(item.text())
        )
        self.title_list.setToolTip('클릭하면 클립보드에 복사됩니다')
        res_layout.addWidget(self.title_list)

        # 썸네일 섹션
        thumb_header = QHBoxLayout()
        th_lbl = QLabel('🖼  썸네일 문구 추천')
        th_lbl.setStyleSheet('font-weight: bold; font-size: 13px; border: none; color: #111111;')
        copy_thumbs_btn = QPushButton('전체 복사')
        copy_thumbs_btn.setFixedWidth(70)
        copy_thumbs_btn.setStyleSheet('border: 1px solid #ccc; border-radius: 4px; padding: 3px; font-size: 11px;')
        copy_thumbs_btn.clicked.connect(lambda: self._copy_section('thumbs'))
        thumb_header.addWidget(th_lbl)
        thumb_header.addStretch()
        thumb_header.addWidget(copy_thumbs_btn)
        res_layout.addLayout(thumb_header)

        self.thumb_widget = QWidget()
        self.thumb_widget.setStyleSheet('border: 1px solid #ddd; border-radius: 6px; background: white;')
        self.thumb_layout = QVBoxLayout(self.thumb_widget)
        self.thumb_layout.setContentsMargins(8, 6, 8, 6)
        self.thumb_layout.setSpacing(4)
        res_layout.addWidget(self.thumb_widget)

        layout.addWidget(self.ai_results_frame)
        return frame

    # ── UI helpers ────────────────────────────────────────────────────

    def _bold_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet('font-weight: bold; font-size: 13px; color: #111111;')
        return lbl

    def _lbl(self, text: str, color: str = '#111111') -> QLabel:
        l = QLabel(text)
        l.setStyleSheet(f'color: {color}; border: none;')
        return l

    def _make_box(self) -> QFrame:
        box = QFrame()
        box.setStyleSheet('QFrame { border: 1px solid #d0d0d0; border-radius: 8px; background: #fafafa; }')
        return box

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet('color: #d0d0d0; border: none; background: #d0d0d0; max-height: 1px;')
        return line

    def _slider_row(self, label, min_v, max_v, default,
                    display_fn, parse_fn, unit,
                    slider_attr, val_attr) -> QHBoxLayout:
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFixedWidth(72)
        lbl.setStyleSheet('border: none; color: #111111;')

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setStyleSheet('''
            QSlider { border: none; }
            QSlider::groove:horizontal {
                height: 6px; background: #e0e0e0; border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #0055cc, stop:1 #0099ff);
                border-radius: 3px;
            }
            QSlider::add-page:horizontal {
                background: #e0e0e0; border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: white; border: 2px solid #0066cc;
                width: 16px; height: 16px;
                margin: -5px 0; border-radius: 9px;
            }
            QSlider::handle:horizontal:hover { background: #e8f0fe; }
            QSlider:disabled::groove:horizontal { background: #ececec; }
            QSlider:disabled::sub-page:horizontal { background: #c0c0c0; }
            QSlider:disabled::handle:horizontal {
                background: #e8e8e8; border-color: #b0b0b0;
            }
        ''')
        slider.setRange(min_v, max_v)
        slider.setValue(default)

        val_input = QLineEdit(display_fn(default))
        val_input.setFixedWidth(52)
        val_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        val_input.setStyleSheet('''
            QLineEdit {
                border: 1px solid #c8c8c8; border-radius: 5px;
                background: white; color: #111111;
                font-weight: bold; font-size: 13px; padding: 2px 4px;
            }
            QLineEdit:focus { border-color: #0066cc; }
            QLineEdit:disabled { background: #f2f2f2; color: #aaaaaa; border-color: #e0e0e0; }
        ''')

        unit_lbl = QLabel(unit)
        unit_lbl.setStyleSheet('border: none; color: #555555; font-size: 12px;')
        unit_lbl.setFixedWidth(22)

        slider.valueChanged.connect(lambda v: val_input.setText(display_fn(v)))

        def on_input_edited():
            try:
                new_val = max(min_v, min(max_v, parse_fn(val_input.text())))
                slider.setValue(new_val)
            except (ValueError, ZeroDivisionError):
                pass
            val_input.setText(display_fn(slider.value()))

        val_input.editingFinished.connect(on_input_edited)

        setattr(self, slider_attr, slider)
        setattr(self, val_attr, val_input)

        row.addWidget(lbl)
        row.addWidget(slider)
        row.addWidget(val_input)
        row.addWidget(unit_lbl)
        return row

    def _action_btn(self, text: str, color: str, hover: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedHeight(42)
        btn.setMinimumWidth(150)
        # 텍스트 길이와 무관하게 같은 행 안에서 균일한 너비로 확장
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setStyleSheet(f'''
            QPushButton {{
                background: {color}; color: white;
                border-radius: 8px; font-size: 14px; font-weight: bold;
                padding: 0 8px;
            }}
            QPushButton:hover {{ background: {hover}; }}
            QPushButton:disabled {{ background: #ccc; color: #888; }}
        ''')
        return btn

    # ── file management ───────────────────────────────────────────────

    def _add_files(self, paths: List[str]):
        for path in paths:
            if path not in self.file_paths:
                self.file_paths.append(path)
        self.file_paths.sort(key=lambda p: Path(p).name)
        self.results = []
        self.export_btn.setEnabled(False)
        self._apply_cut_mode_state()
        self._refresh_list()

    def _clear_files(self):
        self.file_paths = []
        self.results = []
        self.file_list.clear()
        self.export_btn.setEnabled(False)
        self.auto_detect_btn.setEnabled(False)
        self.auto_detect_btn.setText('🎯 자동')
        self.status_label.setText('')

    def _refresh_list(self, results: Optional[List[ClipResult]] = None):
        self.file_list.clear()
        for i, path in enumerate(self.file_paths):
            name = Path(path).name
            if results and i < len(results):
                r = results[i]
                if r.error:
                    suffix = '  ⚠️ 오류'
                else:
                    removed = r.info.duration - sum(s.duration for s in r.segments)
                    sub_info = f'  🗣 {len(r.subtitles)}줄' if r.subtitles else ''
                    suffix = f'  ✅ {len(r.segments)}구간 ({removed:.1f}초 제거){sub_info}'
                self.file_list.addItem(QListWidgetItem(f'  {name}{suffix}'))
            else:
                self.file_list.addItem(QListWidgetItem(f'  {name}'))

    # ── settings ──────────────────────────────────────────────────────

    def _toggle_whisper(self, checked: bool):
        self.whisper_options.setVisible(checked)

    def _get_nice_level(self) -> int:
        return 15 if self.cpu_bg_check.isChecked() else 0

    def _get_model_size(self) -> str:
        return self.model_combo.currentText().split(' ')[0]

    def _get_language(self) -> str:
        mapping = {'한국어': 'ko', 'English': 'en', '日本語': 'ja', '中文': 'zh'}
        return mapping.get(self.lang_combo.currentText(), 'ko')

    def _get_fps_override(self) -> Optional[float]:
        val = self.fps_combo.currentText()
        if val == '원본 유지':
            return None
        return float(val)

    # ── analysis ──────────────────────────────────────────────────────

    def _start_analysis(self):
        if not self.file_paths:
            QMessageBox.warning(self, '경고', '영상 파일을 먼저 추가하세요.')
            return

        self.analyze_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.noise_slider.setEnabled(False)
        self.noise_val.setEnabled(False)
        self.dur_slider.setEnabled(False)
        self.dur_val.setEnabled(False)
        self.cpu_bg_check.setEnabled(False)
        self.auto_detect_btn.setEnabled(False)
        self.cut_mode_combo.setEnabled(False)
        self.pause_btn.setText('일시정지')
        self.pause_btn.setVisible(True)
        self.cancel_btn.setVisible(True)
        self.progress_bar.setMaximum(len(self.file_paths))
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.status_label.setText('분석 준비 중…')

        # 작업 화면 초기화 + 전환
        self._work_results = []
        self.work_timeline.set_results([])
        self.work_sub_list.clear()
        self.goto_results_btn.setEnabled(False)
        self.back_to_settings_btn.setEnabled(False)
        self.stack.setCurrentIndex(1)

        self.worker = AnalysisWorker(
            file_paths=self.file_paths,
            noise_db=float(self.noise_slider.value()),
            min_duration=self.dur_slider.value() / 10.0,
            whisper_enabled=self.whisper_check.isChecked(),
            whisper_model_size=self._get_model_size(),
            language=self._get_language(),
            nice_level=self._get_nice_level(),
            cut_mode=self._get_cut_mode(),
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.clip_started.connect(self._on_clip_started)
        self.worker.subtitle_made.connect(self._on_subtitle_made)
        self.worker.finished.connect(self._on_done)
        self.worker.start()

    def _on_clip_started(self, result: ClipResult):
        """무음 감지 직후 컷을 타임라인에 표시 (자막은 아직 비어 있음)."""
        # UI 전용 복사본을 보관해 워커 스레드와 객체 공유를 피한다.
        live = ClipResult(info=result.info,
                          segments=list(result.segments),
                          subtitles=[], error=result.error)
        self._work_results.append(live)
        self.work_timeline.set_results(self._work_results)

    def _on_subtitle_made(self, idx: int, sub):
        """자막 1줄이 생성될 때마다 로그와 타임라인을 실시간 갱신."""
        if 0 <= idx < len(self._work_results):
            live = self._work_results[idx]
            live.subtitles.append(sub)
            self.work_timeline.set_results(self._work_results)
            m, s = divmod(int(sub.start), 60)
            item = QListWidgetItem(f'[{m:02d}:{s:02d}]  {sub.text}')
            item.setData(Qt.ItemDataRole.UserRole,
                         (live.info.path, sub.start, sub.end))
            self.work_sub_list.addItem(item)
            self.work_sub_list.scrollToBottom()

    def _on_sub_list_clicked(self, item: QListWidgetItem):
        """자막 클릭 → 타임라인에서 해당 위치 강조."""
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            path, start, end = data
            self.work_timeline.set_highlight(path, start, end)

    def _toggle_pause(self):
        if not self.worker:
            return
        if self.worker._paused:
            self.worker.toggle_pause()
            self.pause_btn.setText('일시정지')
            self.status_label.setText('재개 중…')
        else:
            self.worker.toggle_pause()
            self.pause_btn.setText('재개')
            self.status_label.setText('⏸ 일시정지됨')

    def _auto_detect_threshold(self):
        if not self.file_paths:
            return
        self.auto_detect_btn.setEnabled(False)
        self.auto_detect_btn.setText('분석 중…')
        self.status_label.setText('🔍 노이즈 플로어 분석 중… (최대 60초)')
        self._noise_worker = NoiseDetectWorker(self.file_paths[0], self._get_nice_level())
        self._noise_worker.finished.connect(self._on_noise_detected)
        self._noise_worker.start()

    def _on_noise_detected(self, threshold: float):
        old_val = self.noise_slider.value()
        self.noise_slider.setValue(int(threshold))
        self.auto_detect_btn.setEnabled(True)
        self.auto_detect_btn.setText('🎯 자동')
        self.status_label.setText(
            f'✅ 자동 감지 완료: {int(threshold)} dB  (이전: {old_val} dB)'
        )

    def _unlock_settings(self):
        self.analyze_btn.setEnabled(True)
        self.dur_slider.setEnabled(True)
        self.dur_val.setEnabled(True)
        self.cpu_bg_check.setEnabled(True)
        self.cut_mode_combo.setEnabled(True)
        self._apply_cut_mode_state()   # 컷 기준에 따라 무음 슬라이더·자동 버튼 상태

    def _cancel_analysis(self):
        if self.worker:
            self.worker.stop()
        self._unlock_settings()
        self.progress_bar.setVisible(False)
        self.pause_btn.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.back_to_settings_btn.setEnabled(True)
        self.status_label.setText('⚠️ 분석이 취소되었습니다 — 「설정」으로 돌아가 다시 시작하세요')

    def _on_progress(self, current: int, total: int, msg: str):
        self.progress_bar.setValue(current)
        self.status_label.setText(msg)

    def _on_done(self, results: List[ClipResult]):
        self.results = results
        self._unlock_settings()
        self.progress_bar.setVisible(False)
        self.pause_btn.setVisible(False)
        self.cancel_btn.setVisible(False)
        self._refresh_list(results)

        errors = [r for r in results if r.error]
        valid = [r for r in results if not r.error]
        total_removed = sum(
            r.info.duration - sum(s.duration for s in r.segments)
            for r in valid
        )
        has_subs = any(r.subtitles for r in valid)

        whisper_requested = self.whisper_check.isChecked()
        if errors:
            self.status_label.setText(f'⚠️ {len(errors)}개 파일 오류 포함')
        elif whisper_requested and not has_subs:
            self.status_label.setText(
                f'✅ {len(valid)}개 클립 분석 완료 — 총 {total_removed:.1f}초 제거 예정  '
                f'|  ⚠️ 자막 생성 실패 (터미널 로그 확인)'
            )
        else:
            sub_txt = f'  |  자막 생성 완료' if has_subs else ''
            self.status_label.setText(
                f'✅ {len(valid)}개 클립 분석 완료 — 총 {total_removed:.1f}초 제거 예정{sub_txt}'
            )

        if valid:
            self.export_btn.setEnabled(True)

        self.export_srt_btn.setEnabled(has_subs)
        self.copy_subs_btn.setEnabled(has_subs)

        # 결과 화면 타임라인 + 작업 화면 내비게이션
        self.timeline.set_results(results)
        self.goto_results_btn.setEnabled(bool(valid))
        self.back_to_settings_btn.setEnabled(True)
        if valid:
            self.status_label.setText(
                self.status_label.text() + '   →  「결과 보기」로 이동하세요'
            )

    # ── export ────────────────────────────────────────────────────────

    def _export(self):
        if not self.results:
            return

        save_path, _ = QFileDialog.getSaveFileName(
            self, 'FCPXML 저장',
            str(Path.home() / 'Desktop' / 'auto_cut.fcpxml'),
            'FCPXML 파일 (*.fcpxml)'
        )
        if not save_path:
            return

        try:
            embed = self.embed_subs_check.isChecked()
            generate_fcpxml(self.results, save_path,
                            fps_override=self._get_fps_override(),
                            embed_subtitles=embed)
            sub_note = ' (자막 삽입)' if embed else ''
            self.status_label.setText(f'✅ FCPXML 저장 완료{sub_note}: {Path(save_path).name}')

            reply = QMessageBox.question(
                self, '저장 완료',
                f'저장 완료: {Path(save_path).name}\n\nFinal Cut Pro로 바로 열까요?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                subprocess.run(['open', save_path])
            else:
                subprocess.run(['open', '-R', save_path])

        except Exception as e:
            QMessageBox.critical(self, '오류', f'내보내기 실패:\n{e}')

    def _export_srt(self):
        if not self.results:
            return

        save_path, _ = QFileDialog.getSaveFileName(
            self, 'SRT 저장',
            str(Path.home() / 'Desktop' / 'auto_cut.srt'),
            'SRT 자막 파일 (*.srt)'
        )
        if not save_path:
            return

        try:
            write_srt(self.results, save_path)
            self.status_label.setText(f'✅ SRT 저장 완료: {Path(save_path).name}')
            subprocess.run(['open', '-R', save_path])
        except Exception as e:
            QMessageBox.critical(self, '오류', f'SRT 내보내기 실패:\n{e}')

    # ── AI 추천 ───────────────────────────────────────────────────────

    def _toggle_ai_section(self, checked: bool):
        self.ai_frame.setVisible(checked)
        arrow = '▾' if checked else '▸'
        self.ai_toggle_btn.setText(f'🤖  AI 제목 / 썸네일 추천  {arrow}')

    def _on_provider_changed(self, provider: str):
        self.api_key_input.setText(load_api_key(provider))

    def _save_api_key(self):
        provider = self.provider_combo.currentText()
        key = self.api_key_input.text().strip()
        if key:
            save_api_key(provider, key)
            self.ai_status_lbl.setText('✅ API 키 저장됨')
        else:
            self.ai_status_lbl.setText('⚠️ API 키를 입력하세요')

    def _run_ai(self):
        has_subs = self.results and any(r.subtitles for r in self.results)
        if not has_subs:
            QMessageBox.warning(self, '안내',
                'Whisper 자막이 필요합니다.\n설정에서 자막 생성을 켜고 분석을 먼저 실행하세요.')
            return

        api_key = self.api_key_input.text().strip()
        if not api_key:
            QMessageBox.warning(self, '안내', 'API 키를 입력하고 저장하세요.')
            return

        provider = self.provider_combo.currentText()
        self.ai_run_btn.setEnabled(False)
        self.ai_status_lbl.setText('AI에게 물어보는 중…')
        self.ai_results_frame.setVisible(False)

        self.ai_worker = AIWorker(provider, api_key, self.results)
        self.ai_worker.finished.connect(self._on_ai_done)
        self.ai_worker.error.connect(self._on_ai_error)
        self.ai_worker.start()

    def _on_ai_done(self, rec: AIRecommendation):
        self.ai_run_btn.setEnabled(True)
        self.ai_status_lbl.setText(
            f'✅ 제목 {len(rec.titles)}개 · 썸네일 {len(rec.thumbnail_texts)}개'
        )

        # 제목 목록 채우기
        self.title_list.clear()
        for title in rec.titles:
            self.title_list.addItem(title)

        # 썸네일 문구 채우기
        while self.thumb_layout.count():
            child = self.thumb_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        for text in rec.thumbnail_texts:
            row = QHBoxLayout()
            lbl = QLabel(text)
            lbl.setStyleSheet('border: none; font-size: 13px; color: #111111;')
            copy_btn = QPushButton('📋')
            copy_btn.setFixedSize(28, 28)
            copy_btn.setStyleSheet('border: 1px solid #ddd; border-radius: 4px; background: #f5f5f5;')
            copy_btn.setToolTip('복사')
            copy_btn.clicked.connect(lambda _, t=text: QApplication.clipboard().setText(t))
            row.addWidget(lbl)
            row.addStretch()
            row.addWidget(copy_btn)
            self.thumb_layout.addLayout(row)

        self._store_rec(rec)
        self.ai_results_frame.setVisible(True)

    def _on_ai_error(self, msg: str):
        self.ai_run_btn.setEnabled(True)
        self.ai_status_lbl.setText(f'⚠️ 오류')
        QMessageBox.critical(self, 'AI 오류', msg)

    def _store_rec(self, rec: AIRecommendation):
        self._last_rec = rec

    def _copy_subtitle_text(self):
        text = ' '.join(
            sub.text for r in self.results
            for sub in r.subtitles if sub.text
        )
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.status_label.setText('✅ 자막 텍스트가 클립보드에 복사되었습니다 — claude.ai에 붙여넣어 보세요')

    def _copy_section(self, section: str):
        rec: Optional[AIRecommendation] = getattr(self, '_last_rec', None)
        if not rec:
            return
        if section == 'titles':
            QApplication.clipboard().setText('\n'.join(rec.titles))
        else:
            QApplication.clipboard().setText('\n'.join(rec.thumbnail_texts))

    # ── 창 활성화 변경 시 재그리기 (macOS 네이티브 렌더링 버그 우회) ─────

    def changeEvent(self, event):
        super().changeEvent(event)
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.ActivationChange:
            self.update()

    # ── 종료 처리 ─────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(2000)
        if self.ai_worker and self.ai_worker.isRunning():
            self.ai_worker.terminate()
        event.accept()
