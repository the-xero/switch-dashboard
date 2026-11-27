"""경량 다국어(i18n) 번역 로더 및 언어 해석 모듈."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 프로젝트 루트 기준 번역 디렉터리 및 기본 언어 상수
TRANSLATIONS_DIR: Path = Path(__file__).resolve().parent / "translations"
DEFAULT_LANGUAGE: str = "en"
AUTO_LANGUAGE: str = "auto"

_translations_cache: dict[str, dict[str, Any]] = {}


class _SafeFormatDict(dict):
    """누락된 포맷 키를 예외 대신 원형 플레이스홀더로 보존하는 딕셔너리."""

    def __missing__(self, key: str) -> str:
        """누락된 키가 있을 때 `{key}` 형태로 반환한다."""
        return "{" + key + "}"


def load_translations(
    force_reload: bool = False,
    translations_dir: Optional[Path] = None,
) -> dict[str, dict[str, Any]]:
    """translations 디렉터리에서 모든 언어 JSON 파일을 로드한다.

    Args:
        force_reload: 캐시를 무시하고 디스크에서 다시 읽을지 여부.
        translations_dir: 테스트 등에서 대체 번역 디렉터리를 지정할 때 사용.

    Returns:
        언어 코드를 키로, 파싱된 번역 딕셔너리를 값으로 갖는 매핑.
    """
    global _translations_cache

    target_dir = translations_dir or TRANSLATIONS_DIR
    use_global_cache = translations_dir is None

    if use_global_cache and _translations_cache and not force_reload:
        return _translations_cache

    loaded: dict[str, dict[str, Any]] = {}
    if not target_dir.exists() or not target_dir.is_dir():
        logger.warning("번역 디렉터리를 찾을 수 없습니다: %s", target_dir)
        return loaded

    for json_path in sorted(target_dir.glob("*.json")):
        lang_code = json_path.stem.strip().lower()
        if not lang_code:
            continue
        try:
            with json_path.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
            if not isinstance(data, dict):
                logger.warning("유효하지 않은 번역 파일 형식(dict 아님): %s", json_path.name)
                continue
            meta = data.get("_meta")
            if isinstance(meta, dict) and isinstance(meta.get("code"), str) and meta["code"].strip():
                lang_code = meta["code"].strip().lower()
            loaded[lang_code] = data
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("번역 파일 로드 실패 (%s): %s", json_path.name, exc)

    if use_global_cache:
        _translations_cache = loaded
    return loaded


def get_available_languages(
    translations_dir: Optional[Path] = None,
) -> list[dict[str, str]]:
    """사용 가능한 언어 목록을 메타데이터와 함께 반환한다.

    `en`을 항상 첫 번째로 배치하고 나머지 언어는 코드 알파벳 순으로 정렬한다.
    새로운 `<lang>.json` 파일이 추가되면 별도 코드 수정 없이 목록에 포함된다.

    Args:
        translations_dir: 테스트용 대체 번역 디렉터리 경로.

    Returns:
        `code`, `name`, `locale` 키를 가진 언어 정보 딕셔너리 리스트.
    """
    catalog = load_translations(translations_dir=translations_dir)
    languages: list[dict[str, str]] = []
    for code, data in catalog.items():
        meta = data.get("_meta") if isinstance(data.get("_meta"), dict) else {}
        name = str(meta.get("name") or code.upper())
        locale = str(meta.get("locale") or ("ko-KR" if code == "ko" else "en-US"))
        languages.append({
            "code": code,
            "name": name,
            "locale": locale,
        })

    # 기본 영어(en)를 항상 최상단에 두고 나머지는 코드 순으로 정렬해 UI 일관성을 유지한다.
    languages.sort(key=lambda item: (0 if item["code"] == DEFAULT_LANGUAGE else 1, item["code"]))
    return languages


def _parse_accept_language(header_value: Optional[str]) -> list[str]:
    """HTTP Accept-Language 헤더를 우선순위(q) 내림차순으로 파싱한다."""
    if not header_value:
        return []

    parsed: list[tuple[float, int, str]] = []
    for idx, raw_part in enumerate(header_value.split(",")):
        part = raw_part.strip()
        if not part:
            continue
        segments = [seg.strip() for seg in part.split(";")]
        tag = segments[0].lower()
        if not tag or tag == "*":
            continue
        q_val = 1.0
        for seg in segments[1:]:
            if seg.startswith("q="):
                try:
                    q_val = float(seg[2:].strip())
                except ValueError:
                    q_val = 0.0
                break
        if q_val > 0:
            parsed.append((q_val, -idx, tag))

    parsed.sort(reverse=True)
    return [tag for _, _, tag in parsed]


def resolve_language(
    configured_lang: Optional[str],
    accept_language_header: Optional[str] = None,
    translations_dir: Optional[Path] = None,
) -> str:
    """설정값과 요청 헤더를 바탕으로 최종 활성 언어 코드를 결정한다.

    Args:
        configured_lang: `config.json`에 저장된 언어 설정(`auto`, `en`, `ko` 등).
        accept_language_header: HTTP 요청의 `Accept-Language` 헤더 문자열.
        translations_dir: 테스트용 대체 번역 디렉터리 경로.

    Returns:
        해석된 지원 언어 코드(지원 언어 없으면 `en` 폴백).
    """
    catalog = load_translations(translations_dir=translations_dir)
    fallback = DEFAULT_LANGUAGE if DEFAULT_LANGUAGE in catalog else (next(iter(catalog), DEFAULT_LANGUAGE))

    normalized = (configured_lang or AUTO_LANGUAGE).strip().lower()
    if normalized and normalized != AUTO_LANGUAGE:
        if normalized in catalog:
            return normalized
        # `ko-kr` 등 지역 접미사가 저장된 경우에도 primary subtag로 매칭한다.
        primary = normalized.split("-", 1)[0]
        if primary in catalog:
            return primary
        return fallback

    # 자동 감지(auto) 모드에서는 브라우저가 선호하는 언어 순으로 지원 언어를 찾는다.
    for tag in _parse_accept_language(accept_language_header):
        if tag in catalog:
            return tag
        primary = tag.split("-", 1)[0]
        if primary in catalog:
            return primary

    return fallback


def is_valid_language_setting(
    value: Optional[str],
    translations_dir: Optional[Path] = None,
) -> bool:
    """설정 저장용 언어 코드가 유효한지(`auto` 또는 등록된 언어) 검증한다.

    Args:
        value: 검증할 언어 설정 값.
        translations_dir: 테스트용 대체 번역 디렉터리 경로.

    Returns:
        유효한 설정 값이면 True, 아니면 False.
    """
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    if normalized == AUTO_LANGUAGE:
        return True
    catalog = load_translations(translations_dir=translations_dir)
    return normalized in catalog


def get_locale(lang: str, translations_dir: Optional[Path] = None) -> str:
    """언어 코드에 대응하는 BCP-47 로케일 문자열(예: `ko-KR`, `en-US`)을 반환한다.

    Args:
        lang: 활성 언어 코드.
        translations_dir: 테스트용 대체 번역 디렉터리 경로.

    Returns:
        프론트엔드 `toLocaleString` 등에 전달할 로케일 문자열.
    """
    catalog = load_translations(translations_dir=translations_dir)
    lang_data = catalog.get(lang) or catalog.get(DEFAULT_LANGUAGE) or {}
    meta = lang_data.get("_meta") if isinstance(lang_data.get("_meta"), dict) else {}
    locale = meta.get("locale")
    if isinstance(locale, str) and locale.strip():
        return locale.strip()
    return "ko-KR" if lang == "ko" else "en-US"


def _lookup_nested(mapping: dict[str, Any], key: str) -> Optional[str]:
    """점(`.`) 표기법 키로 중첩 딕셔너리에서 문자열 값을 조회한다."""
    current: Any = mapping
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, str) else None


def translate(
    key: str,
    lang: str = DEFAULT_LANGUAGE,
    translations_dir: Optional[Path] = None,
    **kwargs: Any,
) -> str:
    """번역 키를 활성 언어로 해석하고 필요 시 플레이스홀더를 치환한다.

    조회 우선순위: 활성 언어 -> 기본 언어(`en`) -> 키 원문.

    Args:
        key: 점으로 구분된 번역 키(예: `nav.dashboard`).
        lang: 대상 언어 코드.
        translations_dir: 테스트용 대체 번역 디렉터리 경로.
        **kwargs: 번역 문자열 내 `{name}` 플레이스홀더 치환 값.

    Returns:
        번역 및 포매팅이 적용된 최종 문자열.
    """
    catalog = load_translations(translations_dir=translations_dir)
    text: Optional[str] = None

    if lang in catalog:
        text = _lookup_nested(catalog[lang], key)
    if text is None and DEFAULT_LANGUAGE in catalog:
        text = _lookup_nested(catalog[DEFAULT_LANGUAGE], key)
    if text is None:
        text = key

    if not kwargs:
        return text

    try:
        return text.format_map(_SafeFormatDict(kwargs))
    except (ValueError, KeyError) as exc:
        logger.warning("번역 포매팅 오류 (key=%s, lang=%s): %s", key, lang, exc)
        return text


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """영문 베이스 딕셔너리 위에 활성 언어 딕셔너리를 재귀 병합한다."""
    merged: dict[str, Any] = {}
    for key, val in base.items():
        if key == "_meta":
            continue
        merged[key] = dict(val) if isinstance(val, dict) else val
        if isinstance(val, dict):
            merged[key] = _deep_merge(val, {})

    for key, val in override.items():
        if key == "_meta":
            continue
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def get_client_bundle(
    lang: str,
    translations_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """프론트엔드 JS에서 사용할 전체 번역 번들을 폴백 병합해 반환한다.

    일부 키가 활성 언어에 누락되더라도 브라우저에서 키 원문이 노출되지 않도록
    `en` 번들을 기본으로 깔고 활성 언어 번들을 덮어쓴다.

    Args:
        lang: 활성 언어 코드.
        translations_dir: 테스트용 대체 번역 디렉터리 경로.

    Returns:
        `_meta`를 제외한 중첩 번역 딕셔너리.
    """
    catalog = load_translations(translations_dir=translations_dir)
    base = catalog.get(DEFAULT_LANGUAGE, {})
    target = catalog.get(lang, {})
    return _deep_merge(base, target)


def serialize_for_script(data: dict[str, Any]) -> str:
    """HTML `<script>` 태그 내부 삽입에 안전하도록 JSON을 직렬화한다.

    Args:
        data: 직렬화할 딕셔너리.

    Returns:
        `</` 시퀀스가 이스케이프된 JSON 문자열.
    """
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
