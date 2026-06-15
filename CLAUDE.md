# CLAUDE.md — CutFlow

## 프로젝트 개요

맥북용 브이로그 컷 편집 보조 툴. 액션캠으로 촬영한 여러 클립을 드래그 앤 드롭하면 음성이 없는 구간을 자동으로 제거하고, Final Cut Pro에서 바로 열 수 있는 FCPXML을 생성한다. Whisper AI로 자막(SRT 또는 FCP title)도 함께 생성 가능.

## 실행 방법

```bash
cd ~/CutFlow
python3 main.py
```

의존성 설치:
```bash
pip3 install -r requirements.txt   # PyQt6, mlx-whisper, faster-whisper, google-genai, anthropic, openai
# ffmpeg는 Homebrew로 설치 필요: brew install ffmpeg
```

## 디렉토리 구조

```
CutFlow/
├── main.py                   # 진입점
├── core/
│   ├── silence_detector.py   # VAD/dB 컷 감지, 노이즈 분석, 타임코드 파싱, 데이터 모델
│   ├── transcriber.py        # Whisper 래퍼(mlx 우선·faster 폴백), 스트리밍·분할·환각필터
│   ├── fcpxml_generator.py   # ClipResult 목록 → FCPXML (+ title 자막 임베드)
│   ├── srt_writer.py         # 자막 타임라인 재계산 → SRT 파일
│   └── ai_advisor.py         # AI 제목/썸네일 추천 (Gemini/Claude/ChatGPT)
├── ui/
│   ├── main_window.py        # PyQt6 3단계 GUI, AnalysisWorker(QThread)
│   └── timeline_widget.py    # 타임라인 시각화(컷/자막 마커/강조/ffplay 미리보기)
└── docs/
    ├── FEATURES.md           # 기능 명세
    ├── CHANGELOG.md          # 버전 이력
    ├── images/               # README 스크린샷
    └── make_screenshots.py   # 스크린샷 생성기(모의 데이터 + QWidget.grab)
```

## 핵심 데이터 흐름

```
[파일 드롭]
    → file_paths (이름순 정렬)
    → AnalysisWorker.run()  (3단계 화면 중 '작업' 화면으로 전환)
        클립마다:
        → detect_keep_segments_vad()  (기본, VAD 음성 구간)
          또는 detect_keep_segments() (dB silencedetect)
          → clip_started 시그널 (컷을 타임라인에 즉시 표시)
        → transcribe(on_subtitle=…, checkpoint=…)
          → 자막 1줄마다 subtitle_made 시그널 (로그·타임라인 실시간 갱신)
    → finished → ClipResult(info, segments, subtitles) 목록
    → ('결과' 화면) generate_fcpxml(embed_subtitles=…)  # FCPXML (+ title 자막)
    →            write_srt()                            # SRT (자막 켠 경우)
```

UI는 `QStackedWidget` 3페이지: **1.설정 → 2.작업 → 3.결과** (단계별 수동 버튼 이동, 하단 공용 상태바).

## 핵심 타입

| 타입 | 위치 | 설명 |
|------|------|------|
| `VideoInfo` | `silence_detector.py` | 경로, 해상도, fps, 길이, `start_tc`(내장 타임코드 초) |
| `Segment` | `silence_detector.py` | 유지할 구간 (start, end 초 단위) |
| `SubtitleEntry` | `silence_detector.py` | 자막 1줄 (start, end, text) |
| `ClipResult` | `silence_detector.py` | 클립 1개의 분석 결과 전체 |
| `AIRecommendation` | `ai_advisor.py` | AI 추천 결과 (titles, thumbnail_texts) |

## 기능 확장 가이드

### 새 분석 기능 추가 (예: 장면 전환 감지)
1. `core/` 에 새 모듈 작성, `ClipResult`에 필드 추가
2. `AnalysisWorker.run()`에 호출 추가
3. UI 설정 화면에 토글 체크박스 추가 (`_toggle_whisper` 패턴 참고)

### AI 제공자 추가 (예: Gemini)
1. `core/ai_advisor.py`에 `ask_xxx()` 함수 추가 (`ask_claude` 시그니처 동일)
2. `get_recommendations()` 분기에 추가
3. `ui/main_window.py` `provider_combo`에 항목 추가

### 새 내보내기 형식 추가 (예: DaVinci Resolve XML)
1. `core/` 에 새 writer 모듈 작성 (`generate_fcpxml` 시그니처 참고)
2. 결과 화면 내보내기 버튼 행 + `_export()` 패턴 참고

### 컷 감지 방식
- 기본은 **VAD**(`detect_keep_segments_vad`) — 음성 구간만 유지. dB(`detect_keep_segments`)는 선택지.
- 두 함수 모두 `(min_duration, padding, nice_level, on_proc)` 키워드 인자 유지. 추가 파라미터는 키워드로만.

## 주의사항

- Python 3.9 기준 작성 — `X | Y` 유니온 타입 문법 사용 불가, `Optional[X]` 사용
- ffmpeg 프로세스는 `on_proc` 콜백으로 `AnalysisWorker`에 전달해 SIGSTOP/SIGCONT 제어. 자막 청크 사이는 `checkpoint()`로 일시정지/취소
- Whisper: Apple Silicon은 mlx-whisper(GPU), 그 외는 faster-whisper(CPU) 자동 폴백. `load_model`이 str(mlx 모델 ID) 또는 WhisperModel 반환 → `transcribe`에서 `isinstance(model, str)`로 분기
- Whisper 오디오는 외장 하드 부담 감소를 위해 로컬 `/tmp`에 임시 추출 후 삭제
- **FCPXML 함정** (메모리 `fcpxml-nameless-format` 참고): format에 `name` 생략(비표준 해상도), 모든 시간값 정수 프레임 스냅, DJI 내장 타임코드(`start_tc`)를 asset/asset-clip `start` 기준점으로 사용. 시간값은 `fractions.Fraction`
- AI 설정·API 키는 `~/.cutflow/config.json`에 저장
