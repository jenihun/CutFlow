# FCPXML 이벤트명/프로젝트명 직접 설정

## 배경 / 문제

현재 `generate_fcpxml()`은 Final Cut Pro의 이벤트명과 프로젝트명을
`'Auto Cut'` 문자열로 하드코딩한다 (`core/fcpxml_generator.py:132-133`).

```python
event   = ET.SubElement(library, 'event',   name='Auto Cut')
project = ET.SubElement(event,   'project',  name='Auto Cut')
```

그 결과 FCPXML을 Final Cut으로 가져오면 타임라인(프로젝트)과 이벤트
이름이 항상 "Auto Cut"으로 고정된다. 저장 다이얼로그의 파일명
(`main_window.py:1227`, `auto_cut.fcpxml`)을 바꿔도 FCP 내부에서 보이는
이름은 바뀌지 않는다 — 파일명과 FCPXML 내부 name 속성은 별개이기 때문이다.

사용자가 작업물마다 이벤트명/프로젝트명을 직접 지정할 수 있어야 한다.

## 목표

- 사용자가 내보내기 전에 **이벤트명**과 **프로젝트명**을 직접 입력할 수 있다.
- 프로젝트명 기본값 = 오늘 날짜 (`YYYY.MM.DD` 형식, 예: `2026.06.15`), 수정 가능.
- 이벤트명 기본값 = 공백. 사용자가 직접 입력.
- 이벤트명이 빈 칸이면 내보내기를 막고 입력을 강제한다.

## 비목표 (YAGNI)

- 이벤트명/프로젝트명 입력값 저장(기억)하기 — 매번 새로 입력.
- 저장 파일명과의 자동 동기화.
- 설정 화면(1단계)에서의 사전 입력.

## 설계

### 1. `core/fcpxml_generator.py`

`generate_fcpxml()`에 키워드 인자 두 개 추가 (Python 3.9 호환을 위해
`Optional` 불필요한 기본 문자열 사용, 기존 호출 비파괴):

```python
def generate_fcpxml(results, output_path, fps_override=None,
                    embed_subtitles=False,
                    event_name="Auto Cut", project_name="Auto Cut"):
```

132-133번 줄의 하드코딩 문자열을 인자값으로 교체:

```python
event   = ET.SubElement(library, 'event',   name=event_name)
project = ET.SubElement(event,   'project',  name=project_name)
```

기본값을 `"Auto Cut"`으로 유지하므로 인자를 넘기지 않는 기존/외부 호출은
동작이 그대로 유지된다.

### 2. `ui/main_window.py` — 결과 화면 (내보내기 근처)

FCPXML 내보내기 버튼 위에 입력칸 두 개(`QLineEdit`)를 추가한다:

- **이벤트명** (`self.event_name_edit`): placeholder "이벤트명을 입력하세요", 기본값 공백.
- **프로젝트명** (`self.project_name_edit`): 기본값 = `datetime.now().strftime('%Y.%m.%d')`.

라벨 + 입력칸은 기존 결과 화면의 위젯 배치 패턴(`embed_subs_check` 등)을 따른다.

### 3. `_export()` 로직 (`ui/main_window.py:1221`)

저장 다이얼로그를 띄우기 **전에** 검증:

```python
event_name = self.event_name_edit.text().strip()
if not event_name:
    QMessageBox.warning(self, '이벤트명 필요',
                        '이벤트명을 입력해주세요.')
    return
project_name = self.project_name_edit.text().strip() or \
    datetime.now().strftime('%Y.%m.%d')
```

검증 통과 후 `generate_fcpxml(...)` 호출에 두 이름을 전달:

```python
generate_fcpxml(self.results, save_path,
                fps_override=self._get_fps_override(),
                embed_subtitles=embed,
                event_name=event_name,
                project_name=project_name)
```

## 데이터 흐름

```
결과 화면 입력칸 (event_name_edit, project_name_edit)
  → _export()에서 .text() 읽기
  → 검증 (이벤트명 빈 칸이면 경고 후 중단)
  → generate_fcpxml(event_name=…, project_name=…)
  → FCPXML <event name=…> / <project name=…>
```

### 4. 문서 갱신 (프로젝트 컨벤션)

이 프로젝트는 기능 변경 시 FEATURES.md / CHANGELOG.md를 함께 갱신한다.

- **`docs/FEATURES.md` 5-1 (FCPXML)**: 이벤트명/프로젝트명을 사용자가 직접
  지정할 수 있다는 항목 추가. 프로젝트명 기본값(오늘 날짜 `YYYY.MM.DD`),
  이벤트명 필수 입력을 명시.
- **`docs/CHANGELOG.md`**: 하위 호환 새 기능이므로 SemVer **MINOR** 항목
  (`[0.9.0]`)의 "추가"에 기록. (버전 번호 확정은 릴리스 시점에 사용자와 합의)

## 파일명과의 관계 (중요)

FCPXML **디스크 파일명**(`auto_cut.fcpxml`)과 SRT 파일명 규칙(FEATURES 5-2,
"FCPXML과 동일 이름")은 **이번 변경의 대상이 아니다**. 이번 작업은 FCPXML
**내부의** `<event>`/`<project>` name 속성만 바꾼다. 저장 다이얼로그 기본
파일명은 그대로 두므로 기존 SRT 동시 출력 동작과 충돌하지 않는다.

## 테스트

- `generate_fcpxml`에 `event_name`/`project_name`을 넘겼을 때, 생성된
  FCPXML의 `<event>`와 `<project>`의 `name` 속성이 인자와 일치하는지
  확인하는 단위 테스트 1개.

## 주의사항

- Python 3.9 기준 — 유니온 타입 문법 사용 금지.
- `datetime` import 필요 여부 확인 (UI 파일).
