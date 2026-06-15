# 🎬 CutFlow

맥북용 **브이로그 컷 편집 보조 툴**. 액션캠으로 촬영한 여러 클립을 드래그 앤 드롭하면, 음성이 없는 구간을 자동으로 잘라내고 **Final Cut Pro에서 바로 열 수 있는 FCPXML**을 만들어 줍니다. Whisper AI로 자막(SRT 또는 FCP title)도 함께 생성합니다.

> 액션캠(DJI 등)으로 브이로그를 찍고 Final Cut Pro로 편집하는 사람을 위한 도구입니다.

---

## ✨ 주요 기능

- **음성 기준 컷 (VAD)** — 사람 목소리가 있는 구간만 남깁니다. 엔진음·바람 같은 큰 소음이 있어도 음성만 정확히 골라내며, 음량(dB) 기준 컷도 선택할 수 있습니다.
- **FCPXML 생성** — 원본을 재인코딩하지 않고 편집 정보만 저장. 비표준 해상도(2.7K/4K), 29.97fps, DJI 내장 타임코드까지 정확히 처리해 FCP가 오류 없이 가져옵니다.
- **Whisper 자막** — Apple Silicon GPU(mlx-whisper) 가속. 단어 단위 타임스탬프로 정확한 싱크, 긴 자막 자동 분할, 비발화 환각 자막 필터링.
  - **SRT 파일** 또는 **FCP title 자막**(Namsieon YT 모션 템플릿, 폰트 지정 가능)으로 내보내기
- **실시간 모니터링** — 자막이 생성되는 즉시 타임라인과 로그에 표시. 자막을 클릭하면 타임라인 위치가 강조됩니다.
- **AI 제목/썸네일 추천** — 자막을 바탕으로 Gemini·Claude·ChatGPT가 유튜브 제목과 썸네일 문구를 추천.

---

## 🖥 작업 흐름 (3단계)

```
1. 설정          2. 작업                3. 결과
─────────    ──────────────────    ─────────────────
파일 추가     진행 + 실시간 타임라인     최종 타임라인 미리보기
컷 기준 선택   자막 로그 스트리밍         FCPXML / SRT 내보내기
Whisper 옵션  (클릭 → 위치 강조)        AI 제목·썸네일 추천
```

---

## 📦 설치

### 1. 시스템 의존성 (Homebrew)
```bash
brew install ffmpeg
```

### 2. Python 패키지
```bash
pip3 install -r requirements.txt
```
> `mlx-whisper`는 Apple Silicon(M-시리즈) Mac 권장입니다. 그 외 환경에서는 `faster-whisper`로 자동 폴백합니다.

---

## ▶️ 실행

```bash
cd ~/CutFlow
python3 main.py
```

1. **설정** 화면에서 영상 클립을 드래그하고, 컷 기준(음성 VAD 권장)과 Whisper 옵션을 정합니다.
2. **작업 시작 →** 을 누르면 컷 감지·자막 생성이 진행되며 타임라인에 실시간으로 채워집니다.
3. **결과** 화면에서 `FCPXML 내보내기` → Final Cut Pro로 바로 열어 편집을 이어갑니다.

---

## 🗂 프로젝트 구조

```
CutFlow/
├── main.py                   # 진입점
├── core/
│   ├── silence_detector.py   # VAD/dB 컷 감지, 데이터 모델, 노이즈 분석
│   ├── transcriber.py        # Whisper 래퍼 (mlx/faster), 스트리밍·분할·필터
│   ├── fcpxml_generator.py   # ClipResult → FCPXML (+ title 자막)
│   ├── srt_writer.py         # SRT 파일 생성
│   └── ai_advisor.py         # AI 제목/썸네일 추천
├── ui/
│   ├── main_window.py        # PyQt6 3단계 GUI, AnalysisWorker
│   └── timeline_widget.py    # 타임라인 시각화 (컷/자막/미리보기)
└── docs/
    ├── FEATURES.md           # 기능 명세
    └── CHANGELOG.md          # 버전 이력
```

---

## 📝 참고

- macOS / Python 3.9+ / PyQt6 기준
- FCP title 자막 기능은 **Namsieon YT 모션 템플릿**이 Final Cut Pro에 설치되어 있어야 합니다.
- AI 추천은 해당 제공자의 API 키가 필요합니다 (`~/.cutflow/config.json`에 로컬 저장).

자세한 기능은 [docs/FEATURES.md](docs/FEATURES.md), 버전 이력은 [docs/CHANGELOG.md](docs/CHANGELOG.md)를 참고하세요.
