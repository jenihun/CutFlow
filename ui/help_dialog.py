"""CutFlow 인앱 사용 설명서 다이얼로그.

화면 어디서나 ❓ 버튼으로 열 수 있는 빠른 시작 가이드.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTextBrowser, QPushButton,
)


_HELP_HTML = """
<h2>CutFlow 사용법</h2>
<p>액션캠 영상을 드래그하면 음성 없는 구간을 자동으로 잘라
Final Cut Pro에서 열 수 있는 파일을 만들어 줍니다.</p>

<h3>① 파일 추가</h3>
<ul>
  <li>영상 파일을 창에 <b>드래그 앤 드롭</b>하거나 <b>파일 선택</b> 버튼으로 추가합니다.</li>
  <li>파일명 순서대로 자동 정렬됩니다.</li>
</ul>

<h3>② 설정</h3>
<ul>
  <li><b>컷 기준</b>: VAD(권장) — 사람 목소리가 있는 구간만 남깁니다. dB는 단순한 영상용.</li>
  <li><b>최소 길이</b>: 이보다 짧은 무음은 자르지 않습니다.</li>
  <li>필요하면 <b>Whisper AI 자막 자동 생성</b>을 켭니다.</li>
</ul>

<h3>③ 분석 시작</h3>
<ul>
  <li><b>작업 시작</b>을 누르면 진행 상황과 타임라인이 실시간으로 표시됩니다.</li>
  <li>도중에 <b>일시정지 / 취소</b>할 수 있습니다.</li>
</ul>

<h3>④ 내보내기</h3>
<ul>
  <li>결과 화면에서 <b>이벤트명·프로젝트명</b>을 입력한 뒤
      <b>FCPXML 내보내기</b>(필요 시 SRT)로 저장합니다.</li>
  <li>저장 후 <b>Final Cut Pro로 바로 열기</b>를 선택할 수 있습니다.</li>
</ul>

<h3>💡 팁</h3>
<ul>
  <li>VAD는 엔진음·바람 같은 소음이 있어도 음성 구간만 정확히 남깁니다.</li>
  <li>자막을 켜면 <b>AI 제목 / 썸네일 추천</b>도 받을 수 있습니다.</li>
</ul>
"""


class HelpDialog(QDialog):
    """빠른 시작 가이드를 보여주는 모달 다이얼로그."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('CutFlow 사용법')
        self.resize(560, 640)

        layout = QVBoxLayout(self)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(_HELP_HTML)
        browser.setStyleSheet('color: #111111; background: white;')
        layout.addWidget(browser)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton('닫기')
        close_btn.setFixedWidth(80)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
