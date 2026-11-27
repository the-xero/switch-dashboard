"""다국어(i18n) 번역 정합성, 언어 해석, Flask 렌더링 및 API 응답 검증 테스트."""

from __future__ import annotations

import importlib
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import i18n  # noqa: E402


def _flatten_keys(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """중첩 번역 딕셔너리를 점 표기법 키-값 매핑으로 평탄화한다."""
    result: dict[str, str] = {}
    for key, value in data.items():
        if key == "_meta":
            continue
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flatten_keys(value, full_key))
        elif isinstance(value, str):
            result[full_key] = value
    return result


def _extract_placeholders(text: str) -> set[str]:
    """번역 문자열에서 `{name}` 플레이스홀더 집합을 추출한다."""
    return set(re.findall(r"\{([a-zA-Z0-9_]+)\}", text))


class TranslationCatalogTest(unittest.TestCase):
    """번역 파일(`en.json`, `ko.json`) 구조와 코드 내 키 참조 정합성 테스트."""

    def test_en_and_ko_keys_and_placeholders_match(self) -> None:
        """영문/한국어 번역 파일의 모든 키와 플레이스홀더가 100% 일치해야 한다."""
        catalog = i18n.load_translations(force_reload=True)
        self.assertIn("en", catalog)
        self.assertIn("ko", catalog)

        en_flat = _flatten_keys(catalog["en"])
        ko_flat = _flatten_keys(catalog["ko"])

        self.assertEqual(
            set(en_flat.keys()),
            set(ko_flat.keys()),
            f"키 불일치 - en에만 있음: {set(en_flat) - set(ko_flat)}, ko에만 있음: {set(ko_flat) - set(en_flat)}",
        )

        for key, en_val in en_flat.items():
            ko_val = ko_flat[key]
            self.assertTrue(ko_val.strip(), f"빈 한국어 번역 발견: {key}")
            self.assertEqual(
                _extract_placeholders(en_val),
                _extract_placeholders(ko_val),
                f"플레이스홀더 불일치 (key={key}): en={en_val!r}, ko={ko_val!r}",
            )

    def test_all_referenced_keys_exist_in_translations(self) -> None:
        """app.py 및 모든 HTML 템플릿에서 참조한 번역 키가 en/ko 사전에 존재해야 한다."""
        catalog = i18n.load_translations(force_reload=True)
        en_flat = _flatten_keys(catalog["en"])
        ko_flat = _flatten_keys(catalog["ko"])

        # 동적 접두사 조합 키(`t('config.columns_settings.' + col_id)`)는 별도로 검증한다.
        static_key_pattern = re.compile(r"""\bt\(\s*(['"])([a-zA-Z0-9_.-]+)\1""")
        target_files = [PROJECT_ROOT / "app.py"] + sorted((PROJECT_ROOT / "templates").glob("*.html"))

        missing_en: list[tuple[str, str]] = []
        missing_ko: list[tuple[str, str]] = []

        for file_path in target_files:
            content = file_path.read_text(encoding="utf-8")
            for match in static_key_pattern.finditer(content):
                key = match.group(2)
                if key.endswith("."):
                    continue
                if key not in en_flat:
                    missing_en.append((file_path.name, key))
                if key not in ko_flat:
                    missing_ko.append((file_path.name, key))

        # config.html 동적 컬럼 키 검증
        for col_id in ["port", "status", "speed", "packets", "bytes", "info", "notes"]:
            dyn_key = f"config.columns_settings.{col_id}"
            self.assertIn(dyn_key, en_flat)
            self.assertIn(dyn_key, ko_flat)

        self.assertEqual(missing_en, [], f"en.json 누락 키: {missing_en}")
        self.assertEqual(missing_ko, [], f"ko.json 누락 키: {missing_ko}")


class I18nUnitTest(unittest.TestCase):
    """`i18n.py` 단위 동작(언어 해석, 폴백, 동적 언어 추가) 테스트."""

    def test_resolve_language_auto_and_explicit(self) -> None:
        """설정값과 Accept-Language 헤더 우선순위에 따라 언어를 정확히 해석해야 한다."""
        self.assertEqual(i18n.resolve_language("ko", "en-US,en;q=0.9"), "ko")
        self.assertEqual(i18n.resolve_language("en", "ko-KR,ko;q=0.9"), "en")
        self.assertEqual(i18n.resolve_language("auto", "ko-KR,ko;q=0.9,en;q=0.8"), "ko")
        self.assertEqual(i18n.resolve_language("auto", "en-US;q=0.9,ko;q=0.5"), "en")
        self.assertEqual(i18n.resolve_language("auto", "fr-FR,ja;q=0.8"), "en")
        self.assertEqual(i18n.resolve_language(None, "ko"), "ko")
        self.assertEqual(i18n.resolve_language("unknown-lang", "ko"), "en")

    def test_translate_fallback_and_safe_formatting(self) -> None:
        """누락 키/언어에 대해 안전하게 폴백하고 포매팅 오류를 발생시키지 않아야 한다."""
        self.assertEqual(i18n.translate("nav.dashboard", lang="ko"), "대시보드")
        self.assertEqual(i18n.translate("nav.dashboard", lang="en"), "Dashboard")
        self.assertEqual(i18n.translate("nonexistent.key.path", lang="ko"), "nonexistent.key.path")
        self.assertEqual(
            i18n.translate("status.live_update_in", lang="ko", seconds=15),
            "실시간 (15초 후 갱신)",
        )
        # 누락된 포맷 인자가 있어도 예외 없이 플레이스홀더를 보존해야 한다.
        self.assertEqual(
            i18n.translate("status.live_update_in", lang="ko"),
            "실시간 ({seconds}초 후 갱신)",
        )

    def test_dynamic_language_discovery(self) -> None:
        """새 언어 JSON 파일을 추가하면 코드 수정 없이 목록과 번역이 자동 인식되어야 한다."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "en.json").write_text(
                json.dumps({"_meta": {"code": "en", "name": "English", "locale": "en-US"}, "hello": "Hello"}),
                encoding="utf-8",
            )
            (tmp_path / "ja.json").write_text(
                json.dumps({"_meta": {"code": "ja", "name": "日本語", "locale": "ja-JP"}, "hello": "こんにちは"}),
                encoding="utf-8",
            )

            langs = i18n.get_available_languages(translations_dir=tmp_path)
            self.assertEqual([item["code"] for item in langs], ["en", "ja"])
            self.assertEqual(i18n.resolve_language("auto", "ja-JP,ja;q=0.9", translations_dir=tmp_path), "ja")
            self.assertEqual(i18n.translate("hello", lang="ja", translations_dir=tmp_path), "こんにちは")


class FlaskI18nIntegrationTest(unittest.TestCase):
    """격리된 임시 데이터 디렉터리에서 Flask 앱 렌더링 및 API 다국어 통합 테스트."""

    @classmethod
    def setUpClass(cls) -> None:
        """백그라운드 네트워크 다운로드나 워크스페이스 오염 없이 Flask 앱을 초기화한다."""
        cls._tmpdir = tempfile.TemporaryDirectory()
        data_dir = Path(cls._tmpdir.name)
        (data_dir / "config.json").write_text(
            json.dumps({
                "title": "Switch Dashboard",
                "language": "auto",
                "refresh_interval": 30,
                "switches": [],
                "clients": {},
                "settings": {"log_level": "INFO"},
            }),
            encoding="utf-8",
        )
        # OUI 백그라운드 다운로드 스레드 실행을 방지하기 위해 더미 파일을 미리 배치한다.
        (data_dir / "oui.txt").write_text("", encoding="utf-8")
        (data_dir / "oui36.txt").write_text("", encoding="utf-8")

        cls._prev_data_dir = os.environ.get("DASHBOARD_DATA_DIR")
        os.environ["DASHBOARD_DATA_DIR"] = str(data_dir)

        if "app" in sys.modules:
            cls.app_module = importlib.reload(sys.modules["app"])
        else:
            cls.app_module = importlib.import_module("app")

        # 테스트 중 백그라운드 폴링 루프가 config/로그에 간섭하지 않도록 중지 플래그를 설정한다.
        cls.app_module._stop_thread = True
        cls.app_module._stop_scanner_thread = True
        cls.client = cls.app_module.app.test_client()

    @classmethod
    def tearDownClass(cls) -> None:
        """임시 데이터 디렉터리와 환경변수를 정리한다."""
        if cls._prev_data_dir is None:
            os.environ.pop("DASHBOARD_DATA_DIR", None)
        else:
            os.environ["DASHBOARD_DATA_DIR"] = cls._prev_data_dir
        cls._tmpdir.cleanup()

    def _set_language(self, lang_setting: str) -> None:
        """테스트용 설정 언어를 변경한다."""
        resp = self.client.post("/api/settings", json={"language": lang_setting})
        self.assertEqual(resp.status_code, 200)

    def test_all_eight_pages_render_in_ko_en_and_auto(self) -> None:
        """8개 웹 페이지가 en/ko/auto 모드 모두에서 200 OK로 렌더링되고 언어가 반영되어야 한다."""
        pages = [
            ("/", "대시보드"),
            ("/map", "네트워크 맵"),
            ("/scanner", "서브넷 검색 및 스캐너"),
            ("/scanner/history", "호스트 연결 타임라인"),
            ("/config", "노드 설정"),
            ("/backups", "스위치 백업 관리자"),
            ("/logs", "스위치 대시보드 로그"),
            ("/api-docs", "대시보드 REST API 문서"),
        ]

        # 1) auto 모드 + 한국어 브라우저 헤더
        self._set_language("auto")
        for route, expected_ko in pages:
            resp = self.client.get(route, headers={"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})
            self.assertEqual(resp.status_code, 200, f"Route failed in auto(ko): {route}")
            html = resp.get_data(as_text=True)
            self.assertIn('<html lang="ko">', html, f"lang attr mismatch on {route}")
            self.assertIn(expected_ko, html, f"Expected Korean text missing on {route}")
            self.assertIn("window.I18N=", html, f"Missing window.I18N on {route}")

        # 2) auto 모드 + 영문 브라우저 헤더
        for route, _ in pages:
            resp = self.client.get(route, headers={"Accept-Language": "en-US,en;q=0.9"})
            self.assertEqual(resp.status_code, 200, f"Route failed in auto(en): {route}")
            html = resp.get_data(as_text=True)
            self.assertIn('<html lang="en">', html, f"lang attr mismatch on {route}")

        # 3) 명시적 ko 고정 (영문 Accept-Language 무시)
        self._set_language("ko")
        resp = self.client.get("/", headers={"Accept-Language": "en-US"})
        html = resp.get_data(as_text=True)
        self.assertIn('<html lang="ko">', html)
        self.assertIn("대시보드", html)

        # 4) 명시적 en 고정 (한국어 Accept-Language 무시)
        self._set_language("en")
        resp = self.client.get("/", headers={"Accept-Language": "ko-KR"})
        html = resp.get_data(as_text=True)
        self.assertIn('<html lang="en">', html)
        self.assertIn("Dashboard", html)

    def test_config_form_saves_language_setting(self) -> None:
        """/config 폼 저장 시 language 설정이 config.json 및 /api/settings에 반영되어야 한다."""
        resp = self.client.post(
            "/config",
            data={
                "language": "ko",
                "title": "테스트 스위치",
                "refresh_interval": "30",
                "mac_refresh_multiplier": "5",
                "ports_wrap_threshold": "0",
                "max_request_retries": "5",
                "grid_columns": "auto",
                "ignored_macs": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)

        settings_resp = self.client.get("/api/settings")
        self.assertEqual(settings_resp.status_code, 200)
        self.assertEqual(settings_resp.get_json()["language"], "ko")

        # config 페이지 드롭다운에 한국어 선택 상태가 반영되는지 확인
        cfg_html = self.client.get("/config").get_data(as_text=True)
        self.assertIn('<option value="ko" selected>', cfg_html)

    def test_backend_api_messages_are_translated(self) -> None:
        """UI에 노출되는 백엔드 API 에러/성공 메시지가 활성 언어로 반환되어야 한다."""
        self._set_language("ko")
        r1 = self.client.get("/api/switches/10.0.0.99/transceiver")
        self.assertEqual(r1.status_code, 404)
        self.assertEqual(r1.get_json()["error"], "스위치를 찾을 수 없습니다.")

        r2 = self.client.post("/api/clients/update_host", json={"mac": ""})
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(r2.get_json()["error"], "MAC 주소가 누락되었습니다.")

        r3 = self.client.get("/api/backups/..secret.bin/download")
        self.assertEqual(r3.status_code, 400)
        self.assertEqual(r3.get_json()["error"], "유효하지 않은 파일명입니다.")

        r3b = self.client.get("/api/backups/nonexistent.bin/download")
        self.assertEqual(r3b.status_code, 404)
        self.assertEqual(r3b.get_json()["error"], "파일을 찾을 수 없습니다.")

        self._set_language("en")
        r4 = self.client.get("/api/switches/10.0.0.99/transceiver")
        self.assertEqual(r4.status_code, 404)
        self.assertEqual(r4.get_json()["error"], "Switch not found")


if __name__ == "__main__":
    unittest.main()
