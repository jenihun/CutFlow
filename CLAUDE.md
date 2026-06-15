# CLAUDE.md — Video Controller

## 프로젝트 개요

맥북용 브이로그 컷 편집 보조 툴. 액션캠으로 촬영한 여러 클립을 드래그 앤 드롭하면 무음 구간을 자동으로 제거하고, Final Cut Pro에서 바로 열 수 있는 FCPXML을 생성한다. Whisper AI로 자막 SRT도 함께 생성 가능.

## 실행 방법

```bash
cd ~/vedio_controller
python3 main.py
```

의존성 설치:
```bash
pip3 install PyQt6 faster-whisper
# ffmpeg는 Homebrew로 설치 필요: brew install ffmpeg
```

## 디렉토리 구조

```
vedio_controller/
├── main.py                   # 진입점
├── core/
│   ├── silence_detector.py   # ffmpeg 무음 감지, VideoInfo / Segment / ClipResult 데이터 모델
│   ├── fcpxml_generator.py   # ClipResult 목록 → FCPXML 파일
│   ├── transcriber.py        # faster-whisper 래퍼, 오디오 추출 포함
│   └── srt_writer.py         # 자막 타임라인 재계산 → SRT 파일
├── ui/
│   └── main_window.py        # PyQt6 GUI, AnalysisWorker(QThread) 포함
└── docs/
    ├── FEATURES.md            # 기능 명세
    └── CHANGELOG.md           # 버전 이력
```

## 핵심 데이터 흐름

```
[파일 드롭]
    → file_paths (이름순 정렬)
    → AnalysisWorker.run()
        → detect_keep_segments()   # ffmpeg silencedetect → List[Segment]
        → transcribe()             # faster-whisper → List[SubtitleEntry]
    → ClipResult(info, segments, subtitles)
    → generate_fcpxml()            # FCPXML 파일
    → write_srt()                  # SRT 파일 (자막 켠 경우)
```

## 핵심 타입

| 타입 | 위치 | 설명 |
|------|------|------|
| `VideoInfo` | `silence_detector.py` | 경로, 해상도, fps, 길이 |
| `Segment` | `silence_detector.py` | 유지할 구간 (start, end 초 단위) |
| `SubtitleEntry` | `silence_detector.py` | 자막 1줄 (start, end, text) |
| `ClipResult` | `silence_detector.py` | 클립 1개의 분석 결과 전체 |
| `AIRecommendation` | `ai_advisor.py` | AI 추천 결과 (titles, thumbnail_texts) |

## 기능 확장 가이드

### 새 분석 기능 추가 (예: 장면 전환 감지)
1. `core/` 에 새 모듈 작성, `ClipResult`에 필드 추가
2. `AnalysisWorker.run()`에 호출 추가
3. UI에 토글 체크박스 추가 (`_toggle_whisper` 패턴 참고)

### AI 제공자 추가 (예: Gemini)
1. `core/ai_advisor.py`에 `ask_gemini()` 함수 추가 (`ask_claude` 시그니처 동일)
2. `get_recommendations()` 분기에 추가
3. `ui/main_window.py` `provider_combo`에 항목 추가

### 새 내보내기 형식 추가 (예: DaVinci Resolve XML)
1. `core/` 에 새 writer 모듈 작성 (`generate_fcpxml` 시그니처 참고)
2. `_export()` 메서드에 분기 추가

### 무음 감지 파라미터 확장
- `detect_keep_segments(noise_db, min_duration, padding, nice_level, on_proc)` 시그니처 유지
- 추가 파라미터는 키워드 인자로만 추가

## 주의사항

- Python 3.9 기준 작성 — `X | Y` 유니온 타입 문법 사용 불가, `Optional[X]` 사용
- ffmpeg 프로세스는 `on_proc` 콜백으로 `AnalysisWorker`에 전달해 SIGSTOP/SIGCONT 제어
- 일시정지는 OS 시그널 방식 — 파일 간 이동 시 `threading.Event`로도 대기
- Whisper 오디오는 외장 하드 부담 감소를 위해 로컬 `/tmp`에 임시 추출 후 삭제
- FCPXML 시간값은 `fractions.Fraction`으로 유리수 표현 (FCP 요구사항)
