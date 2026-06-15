import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .silence_detector import ClipResult

_CONFIG_FILE = Path.home() / '.cutflow' / 'config.json'


# ── 설정 저장/불러오기 ────────────────────────────────────────────────────────

def _load_config() -> dict:
    if _CONFIG_FILE.exists():
        try:
            return json.loads(_CONFIG_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_api_key(provider: str, key: str):
    _CONFIG_FILE.parent.mkdir(exist_ok=True)
    config = _load_config()
    config[f'{provider}_api_key'] = key
    _CONFIG_FILE.write_text(json.dumps(config, indent=2))


def load_api_key(provider: str) -> str:
    return _load_config().get(f'{provider}_api_key', '')


# ── 데이터 모델 ───────────────────────────────────────────────────────────────

@dataclass
class AIRecommendation:
    titles: List[str] = field(default_factory=list)
    thumbnail_texts: List[str] = field(default_factory=list)


# ── 자막 텍스트 추출 ──────────────────────────────────────────────────────────

def extract_subtitle_text(results: List[ClipResult], max_chars: int = 4000) -> str:
    lines = []
    for r in results:
        for sub in r.subtitles:
            if sub.text:
                lines.append(sub.text)
    text = ' '.join(lines)
    return text[:max_chars]


# ── 프롬프트 ──────────────────────────────────────────────────────────────────

def _build_prompt(subtitle_text: str) -> str:
    return f"""아래는 브이로그 영상의 자막 내용입니다.

{subtitle_text}

위 내용을 바탕으로 다음 두 섹션을 작성해주세요.

[제목]
1.
2.
3.
4.
5.

[썸네일]
1.
2.
3.
4.
5.

작성 규칙:
- [제목]: 유튜브 클릭률이 높은 매력적인 제목, 각 50자 이내, 한국어
- [썸네일]: 썸네일 이미지에 들어갈 짧고 강렬한 문구, 각 15자 이내, 한국어
- 번호와 텍스트만 작성, 설명 없이"""


# ── 응답 파싱 ─────────────────────────────────────────────────────────────────

def _parse(text: str) -> AIRecommendation:
    titles: List[str] = []
    thumbnails: List[str] = []
    current: Optional[str] = None

    for line in text.splitlines():
        line = line.strip()
        if '[제목]' in line:
            current = 'title'
            continue
        if '[썸네일]' in line:
            current = 'thumb'
            continue

        m = re.match(r'^\d+\.\s*(.+)', line)
        if m:
            content = m.group(1).strip()
            if content:
                if current == 'title':
                    titles.append(content)
                elif current == 'thumb':
                    thumbnails.append(content)

    return AIRecommendation(titles=titles[:5], thumbnail_texts=thumbnails[:5])


# ── Gemini API ───────────────────────────────────────────────────────────────

def ask_gemini(api_key: str, subtitle_text: str) -> AIRecommendation:
    import time
    from google import genai

    client = genai.Client(api_key=api_key)
    last_err = None
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model='gemini-2.0-flash',
                contents=_build_prompt(subtitle_text),
            )
            return _parse(response.text)
        except Exception as e:
            last_err = e
            msg = str(e)
            if '429' in msg or 'RESOURCE_EXHAUSTED' in msg:
                if attempt < 2:
                    time.sleep(10 * (attempt + 1))
                    continue
                raise ValueError(
                    'Gemini 무료 티어 요청 한도 초과입니다.\n'
                    '잠시 후 다시 시도하거나, 내일 다시 이용해 주세요.\n'
                    '(무료 플랜: 분당 15회 / 1일 1,500회 제한)'
                ) from e
            raise
    raise last_err


# ── Claude API ────────────────────────────────────────────────────────────────

def ask_claude(api_key: str, subtitle_text: str) -> AIRecommendation:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=1024,
        messages=[{'role': 'user', 'content': _build_prompt(subtitle_text)}],
    )
    return _parse(message.content[0].text)


# ── OpenAI API ────────────────────────────────────────────────────────────────

def ask_openai(api_key: str, subtitle_text: str) -> AIRecommendation:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model='gpt-4o-mini',
        messages=[{'role': 'user', 'content': _build_prompt(subtitle_text)}],
        max_tokens=1024,
    )
    return _parse(response.choices[0].message.content)


# ── 통합 진입점 ───────────────────────────────────────────────────────────────

def get_recommendations(provider: str, api_key: str, results: List[ClipResult]) -> AIRecommendation:
    subtitle_text = extract_subtitle_text(results)
    if not subtitle_text.strip():
        raise ValueError('자막 내용이 없습니다. 먼저 Whisper 자막을 생성하세요.')

    if provider == 'Claude':
        return ask_claude(api_key, subtitle_text)
    elif provider == 'ChatGPT':
        return ask_openai(api_key, subtitle_text)
    elif provider == 'Gemini':
        return ask_gemini(api_key, subtitle_text)
    else:
        raise ValueError(f'지원하지 않는 제공자: {provider}')
