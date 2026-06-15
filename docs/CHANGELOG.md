# CHANGELOG

버전 형식: [Semantic Versioning](https://semver.org/) — `MAJOR.MINOR.PATCH`

- **MAJOR**: 기존 출력 형식이나 파일 구조와 호환되지 않는 변경
- **MINOR**: 하위 호환되는 새 기능 추가
- **PATCH**: 버그 수정, 성능 개선

---

## [0.8.1] — 2026-06-15

### 변경
- **프로그램명 변경**: `Video Controller` (vedio_controller) → **CutFlow**. 창 제목·앱 이름·문서·저장소·폴더에 반영.
- API 키 설정 디렉토리 `~/.vedio_controller/` → `~/.cutflow/` (기존 설정 자동 마이그레이션).

---

## [0.8.0] — 2026-06-15

### 추가 (워크플로우 — 3단계 화면)
- **설정 → 작업 → 결과** 3단계 화면(`QStackedWidget`)으로 재구성. 상단에 단계 표시, 하단에 공용 상태바, 단계별 수동 이동 버튼.
- **작업 화면 실시간 모니터링** — 자막이 생성되는 즉시(클립 완료 전부터) `[mm:ss] 자막내용` 로그에 누적되고, 타임라인의 노란 마커도 동시에 차오름. mlx-whisper를 30초 청크로 처리해 스트리밍 구현.
- **자막 클릭 → 타임라인 강조** — 로그의 자막을 클릭하면 타임라인에서 해당 위치가 주황 박스로 표시됨.

### 추가 (자막 정확도)
- **VAD 컷 모드 (기본)** — 음성 활동 감지(Silero VAD)로 사람 목소리가 있는 구간만 유지. 엔진음·바람 등 큰 비발화 소음이 있어도 정확히 음성만 분리. "자막 없는 곳 = 무음"을 그대로 구현. dB 음량 기준은 선택지로 유지.
- **FCP title 자막 (Namsieon YT 템플릿)** — `FCPXML에 자막 삽입` 옵션. Whisper 자막을 FCP title 클립(`effect` 참조 + asset-clip 내 lane 1 중첩)으로 삽입, 소스 타임코드 공간에 정확히 매핑. 폰트/스타일은 모션 템플릿이 담당.
- **자막 길이 분할** — 긴 자막을 단어 타임스탬프 기준으로 20자 이하 조각으로 분할(각 조각이 정확한 시각 유지).

### 변경
- **Whisper 디코딩 개선** — `word_timestamps=True`로 발화 타이밍 정밀화(2초 격자 → 실제 경계), `condition_on_previous_text=False`로 반복 환각 억제.
- **자동 무음 기준(🎯) 재설계** — VAD로 발화/비발화를 구분한 뒤 각 구간 RMS 분포 사이로 임계값 계산(이전 volumedetect 평균 방식 대체). dB 모드에서만 사용.
- **버튼 크기 통일** — 텍스트 길이와 무관하게 같은 행에서 균일한 너비(`Expanding` + 최소너비 150). 회색 "자막 복사" 버튼 → 보라색.

### 수정
- **자막 생성 0줄 버그** — mlx-whisper 파라미터명 오류(`path_or_hq_transcriber` → `path_or_hf_repo`)로 자막이 항상 비어 자막 복사·SRT가 동작하지 않던 문제 해결.
- **비발화 환각 자막 제거** — 배기음 등에 붙던 반복 텍스트(`compression_ratio > 2.4`), 디코딩 깨진 문자(`�`), 구두점만 있는 세그먼트를 필터링.
- 자막 생성 실패 시 상태바·터미널에 명확히 표시.

---

## [0.7.0] — 2026-06-15

### 수정 (FCPXML — Final Cut Pro 가져오기 오류 해결)
DJI 액션캠 원본을 FCP로 가져올 때 발생하던 "예기치 않은 값(format)" + "각각의 미디어가 없는 유효하지 않은 편집입니다" 오류를 근본 원인 3가지로 나눠 해결:

- **포맷 이름 제거** — `<format>`에 `name="FFVideoFormat..."`을 강제로 붙이면 비표준 해상도(예 DJI 2.7K 2688×1512)에서는 존재하지 않는 프리셋이라 FCP가 format 요소를 거부 → 모든 참조가 연쇄 실패. FCP가 직접 내보낸 파일도 소스 포맷엔 이름을 붙이지 않음. 이름을 생략한 커스텀 포맷으로 변경(모든 해상도에서 안전).
- **프레임 정렬** — 모든 시간값(offset / duration / start)을 정수 프레임 배수로 스냅. 기존 raw 초 단위(예 `5/1s` = 29.97fps에서 149.85프레임)는 프레임 비정렬로 "유효하지 않은 편집" 유발. 타임라인 위치도 정수 프레임으로 누적해 드리프트 방지.
- **내장 타임코드 반영** — DJI는 촬영 시각 기반 드롭프레임 TC(예 `03:48:16;14`)를 파일에 내장. 원본 프레임은 0초가 아니라 해당 TC 지점(≈13696초)부터 번호가 매겨지므로 `start="0s"`는 "미디어 없음" 오류. ffprobe로 TC를 읽어 asset.start = TC, asset-clip.start = TC + 세그먼트 오프셋으로 설정.
- `asset`에 `videoSources / audioSources / audioChannels / audioRate` 속성 추가, `media-rep` 자식 요소(`kind="original-media"` + `src`) 사용으로 DTD 준수.
- 자막을 FCPXML 내 `title` 클립으로 임베드하던 기능 제거 — `ref`(모션 템플릿 UID) 요구사항이 버전 의존적이라 DTD 검증 실패의 원인. 자막은 SRT로만 내보냄.
- spine 클립을 `clip` → `asset-clip`으로 교정.

### 변경 (성능 — Apple Silicon)
- **mlx-whisper 도입** — M3 등 Apple Silicon에서 GPU/Neural Engine 사용. faster-whisper(CPU) 대비 대폭 빠름. `mlx_whisper` 미설치 시 faster-whisper로 자동 폴백.
- 폴백 경로 정확도 개선 — `float32` + 전체 코어(`cpu_threads=0`) + `beam_size=5`.

### 추가
- **자동 무음 기준 탐지** — 🎯 버튼으로 ffmpeg `volumedetect` 분석(첫 60초) → mean/max 볼륨의 다이나믹 레인지 기반으로 최적 dB 임계값 자동 추천. `NoiseDetectWorker(QThread)`.
- **작업 취소 버튼** — 분석 중 즉시 중단(빨강, `worker.stop()`).
- **XML / SRT 분리 내보내기** — FCPXML과 SRT를 각각의 버튼으로 개별 출력.
- **직접 수치 입력** — 슬라이더 옆 입력란으로 무음 기준·최소 길이 등을 정밀 입력.

### 변경
- **CPU 제어 단순화** — 3단 라디오(낮음/보통/최대) → "백그라운드 모드" 체크박스 단일화(켜짐 = nice 15, 꺼짐 = nice 0 최대 속도).
- **Gemini SDK 교체** — 지원 종료된 `google.generativeai` → `google-genai`(`genai.Client`). 모델 `gemini-1.5-flash` → `gemini-2.0-flash`. 429(무료 티어 한도) 시 10s/20s 백오프 재시도 + 친화적 안내 메시지.
- 분석 시작 시 슬라이더·입력란·CPU 설정 잠금, 완료/취소 시 `_unlock_settings()`로 해제.

### 수정 (UI)
- **슬라이더·진행바 사라짐 해결** — 창 포커스 이동 시 macOS 네이티브 렌더링 버그로 위젯이 사라지던 문제. 전체 CSS 스타일시트로 비네이티브 렌더링 강제 + `changeEvent`(ActivationChange)에서 강제 repaint.
- 진행바 리스타일(높이 10px, 파란 그라데이션, 테두리 없음), 슬라이더 스타일 개선.

### 비고
- 0.6.0의 "FCPXML 자막 임베드", "자막 폰트 설정" 기능은 위 DTD 문제로 제거됨.

---

## [0.6.0] — 2026-06-14

### 추가
- **Gemini AI 지원** — Google Gemini 1.5 Flash 모델 연동 (무료 API 티어 지원), provider 드롭다운에 추가
- **자막 텍스트 복사 버튼** — 분석 완료 후 Whisper 자막 전체를 클립보드로 즉시 복사, claude.ai 등 외부 AI에 붙여넣기 가능
- **Whisper 최신 모델 추가** — `large-v3`, `large-v3-turbo` 모델 선택 가능, 기본값을 `large-v3-turbo`로 변경
- **프레임 레이트 설정** — Whisper 옵션 내 FCPXML 출력 시퀀스 프레임 레이트 선택 (원본 유지 / 23.976 / 24 / 25 / 29.97 / 30 / 50 / 59.94 / 60)
- **자막 폰트 설정** — Whisper 옵션 내 FCPXML 임베드 자막 폰트 선택 (남시언 YT 기본 / Apple 고딕 / Arial / Helvetica Neue)
- **FCPXML 자막 임베드** — Whisper + 폰트 설정 시 자막이 FCPXML 내 title 클립(lane 1)으로 삽입되어 FCP에서 바로 확인 가능

### 변경
- Whisper 옵션 섹션이 2행으로 확장 (1행: 모델·언어 / 2행: 프레임 레이트·자막 폰트)
- AI 섹션 provider 기본값을 Gemini로 변경

### 수정
- 전체 UI 텍스트 색상을 명시적으로 `#111111`로 고정 — macOS 다크모드·시스템 테마에 무관하게 가시성 확보
- `main.py`에 QPalette + 글로벌 스타일시트 적용으로 근본적 해결

---

## [0.5.0] — 2026-06-14

### 추가
- **지식 그래프 생성** — `/understand` 명령으로 코드베이스 분석, `.understand-anything/knowledge-graph.json` 생성
- **대시보드** — `/understand-dashboard`로 로컬 대시보드 실행, 39개 노드·86개 엣지·4개 레이어·13단계 투어 포함

---

## [0.4.0] — 2026-06-14

### 추가
- **컷 포인트 미리보기** — 분석 후 클립별 타임라인 시각화 (초록=유지, 빨강=제거, 파란 점선=컷 포인트)
- **타임라인 클릭 미리보기** — 클릭한 시점 앞뒤 5초를 ffplay로 즉시 재생, 재생 완료 후 자동 종료
- **호버 시간 표시** — 마우스 호버 시 주황 선 + 해당 시점(초) 실시간 표시
- `ui/timeline_widget.py` — `TimelineWidget(QWidget)` 신규 모듈

---

## [0.3.0] — 2026-06-14

### 추가
- **AI 제목 / 썸네일 추천** — Claude(claude-sonnet-4-6) 또는 ChatGPT(gpt-4o-mini) 선택 가능
- **유튜브 제목 5개 추천** — 자막 기반, 클릭률 고려
- **썸네일 문구 5개 추천** — 15자 이내 임팩트 문구
- **API 키 로컬 저장** — `~/.vedio_controller/config.json`, 재시작 시 자동 복원
- **클립보드 복사** — 항목 클릭 또는 📋 버튼으로 개별 복사, 전체 복사 버튼 별도 제공
- **전체 스크롤 가능 UI** — QScrollArea로 래핑, 창 크기 무관하게 모든 기능 접근 가능

### 변경
- AI 섹션은 토글 버튼으로 접고 펼칠 수 있어 화면 공간 절약
- Whisper 자막 없으면 AI 추천 버튼 비활성 안내

---

## [0.2.0] — 2026-06-14

### 추가
- **일시정지 / 재개** — OS SIGSTOP/SIGCONT로 ffmpeg 프로세스 실제 정지. 재개 시 처음부터 다시 시작하지 않음
- **Whisper AI 자막 생성** — `faster-whisper` 기반. tiny / base / small / medium 모델 선택 가능
- **자막 타임라인 재계산** — 제거된 무음 구간을 반영해 SRT 자막 시각 자동 보정
- **SRT 파일 내보내기** — FCPXML과 동일 이름으로 함께 저장
- **CPU 사용량 제어** — 낮음(nice 15) / 보통(nice 7) / 최대(nice 0) 선택
- **언어 선택** — 한국어 / English / 日本語 / 中文

### 변경
- ffmpeg 명령에 `-vn` 추가 — 영상 디코딩 생략으로 CPU 사용량 절감
- ffmpeg 명령에 `-threads 2` 추가 — 전체 코어 독점 방지
- Whisper 오디오를 외장 하드가 아닌 로컬 `/tmp`에 임시 추출 후 처리
- `ClipResult`에 `subtitles` 필드 추가

### 수정
- 창 닫을 때 진행 중인 ffmpeg 프로세스가 남아있던 문제 수정 (`closeEvent` 처리)

---

## [0.1.0] — 2026-06-14

### 추가
- 드래그 앤 드롭 및 파일 선택 다이얼로그로 영상 클립 추가
- 파일명 기준 자동 정렬 (액션캠 타임스탬프 파일명 전제)
- ffmpeg `silencedetect`로 무음 구간 자동 감지
- 무음 기준(dB) / 최소 길이(초) 슬라이더 설정
- FCPXML v1.10 내보내기 (Final Cut Pro 호환)
- 분석 결과: 구간 수 + 제거 시간 표시
- 내보내기 후 Final Cut Pro로 바로 열기 또는 파인더에서 위치 표시 선택

### 기술 스택
- Python 3.9
- PyQt6 6.10
- ffmpeg 8.0 (Homebrew)
- faster-whisper 1.2 (선택)

---

## 버전 계획

### [0.9.0] — 예정
- 설정 프리셋 저장 / 불러오기
- 수동 컷 포인트 추가 / 삭제
- DaVinci Resolve XML 내보내기
- 표준 해상도(1080p/720p) 클립은 명명 포맷 사용해 FCP 프로젝트 인스펙터 표기 개선
- 자막 청크 크기 조절(현재 30초) 옵션

### [1.0.0] — 예정
- macOS 앱 번들로 패키징 (.app)
- 코드 서명 및 공증
