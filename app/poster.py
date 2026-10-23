from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

StatusFn = Callable[[str], None]


class PostError(Exception):
    def __init__(self, message: str, screenshot: str | None = None):
        super().__init__(message)
        self.message = message
        self.screenshot = screenshot


def submit_on_page(page, comment: str, delay_ms: int) -> None:
    editor = page.locator("#contenteditable-root")
    editor.click()
    editor.press_sequentially(comment, delay=delay_ms)
    page.locator("#submit-button button").click()


def _shot(page, shots_dir: Path, video_id: str) -> str:
    shots_dir.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now():%Y%m%d-%H%M%S}_{video_id}.png"
    try:
        page.screenshot(path=str(shots_dir / name))
    except Exception:
        return ""
    return name


def _dismiss_consent(page) -> None:
    if "consent.youtube" not in page.url and "consent.google" not in page.url:
        return
    for name in ("모두 동의", "동의", "Accept all", "I agree"):
        button = page.get_by_role("button", name=name)
        if button.count():
            button.first.click()
            page.wait_for_load_state("domcontentloaded")
            return


def _comment_root(page):
    page.locator("ytd-comments, #comments").first.scroll_into_view_if_needed(timeout=20000)
    page.mouse.wheel(0, 900)
    page.wait_for_timeout(800)
    root = page.locator("ytd-comment-simplebox-renderer").first
    root.wait_for(state="visible", timeout=20000)
    return root


def _ensure_korean_login(root) -> None:
    text = root.inner_text(timeout=5000)
    if "댓글이 사용 중지" in text:
        raise PostError("이 영상은 댓글이 꺼져 있습니다.")
    if "댓글 추가" not in text and "로그인" in text:
        raise PostError("이 크롬 프로필은 유튜브에 로그인되어 있지 않습니다. 프로필 로그인 창에서 구글 계정을 로그인해 주세요.")
    if "Add a comment" in text or "Sign in" in text:
        raise PostError("유튜브가 한국어 화면이 아닙니다. 프로필 언어를 한국어로 바꿔 주세요.")


def _fail(page, shots_dir: Path, video_id: str, exc: Exception) -> PostError:
    screenshot = None
    if page is not None:
        screenshot = _shot(page, shots_dir, video_id)
    if isinstance(exc, PostError):
        if not exc.screenshot:
            exc.screenshot = screenshot
        return exc
    return PostError(f"댓글 게시 중 오류가 났습니다. {exc}", screenshot)


def post_comment(
    *,
    profile_dir: Path,
    video_id: str,
    comment: str,
    shots_dir: Path,
    delay_ms: int,
    hold_open_sec: int,
    on_status: StatusFn,
) -> None:
    on_status("프로필 여는 중")
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = None
        page = None
        try:
            try:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    channel="chrome",
                    headless=False,
                    chromium_sandbox=True,
                    viewport={"width": 1440, "height": 900},
                    locale="ko-KR",
                    args=["--lang=ko-KR", "--disable-blink-features=AutomationControlled"],
                )
            except Exception as exc:
                message = str(exc).lower()
                if "processsingleton" in message or "already in use" in message or "existing browser" in message:
                    raise PostError("이 프로필 크롬이 이미 열려 있습니다. 그 창을 닫고 다시 누르세요.") from exc
                raise PostError(f"크롬 프로필을 열지 못했습니다. {exc}") from exc
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(20000)
            page.goto(
                f"https://www.youtube.com/watch?v={video_id}&hl=ko&gl=KR&persist_hl=1",
                wait_until="domcontentloaded",
                timeout=45000,
            )
            _dismiss_consent(page)
            try:
                root = _comment_root(page)
            except PlaywrightTimeout as exc:
                body = ""
                try:
                    body = page.inner_text("body")
                except Exception:
                    body = ""
                if "댓글이 사용 중지" in body:
                    raise PostError("이 영상은 댓글이 꺼져 있습니다.") from exc
                raise PostError("댓글 칸을 찾지 못했습니다. 한국어 유튜브 화면인지 확인해 주세요.") from exc
            _ensure_korean_login(root)
            on_status("입력 중")
            placeholder = root.locator("#placeholder-area")
            if placeholder.count():
                placeholder.click()
            editor = root.locator("#contenteditable-root")
            if not editor.count():
                editor = root.locator("[contenteditable='true']")
            editor.wait_for(state="visible", timeout=10000)
            editor.click()
            editor.press_sequentially(comment, delay=delay_ms)
            button = root.locator("#submit-button button")
            if not button.count():
                button = root.get_by_role("button", name="댓글")
            button.wait_for(state="visible", timeout=10000)
            if button.is_disabled():
                raise PostError("댓글 버튼이 활성화되지 않았습니다. 댓글 칸을 확인하세요.")
            button.click()
            needle = comment.strip()[:24]
            try:
                page.wait_for_function(
                    """(needle) => {
                        const nodes = document.querySelectorAll(
                            'ytd-comment-thread-renderer, ytd-comment-view-model'
                        );
                        for (const node of nodes) {
                            if ((node.innerText || '').includes(needle)) return true;
                        }
                        return false;
                    }""",
                    arg=needle,
                    timeout=20000,
                )
            except PlaywrightTimeout as exc:
                raise PostError("게시 버튼을 눌렀지만 내 댓글이 화면에 보이지 않습니다.") from exc
            on_status("게시 완료 · 댓글 보기")
            page.wait_for_timeout(max(hold_open_sec, 1) * 1000)
        except Exception as exc:
            failed = _fail(page, shots_dir, video_id, exc)
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            raise failed
        if context is not None:
            context.close()


def _open_installed_chrome(
    profile_dir: Path,
    url: str,
    on_status: StatusFn,
    *,
    opening: str,
    ready: str,
    busy: str,
) -> None:
    """구글 로그인은 자동화 표식이 없는 설치 크롬에서만 통과한다."""
    on_status(opening)
    if _chrome_pids(profile_dir):
        raise PostError(busy)
    profile_dir.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            str(_chrome_executable()),
            f"--user-data-dir={profile_dir}",
            "--lang=ko-KR",
            "--no-first-run",
            "--no-default-browser-check",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    appeared = False
    for _ in range(20):
        if _chrome_pids(profile_dir):
            appeared = True
            break
        time.sleep(0.5)
    if not appeared:
        raise PostError("크롬 창을 확인하지 못했습니다. 열려 있던 크롬을 닫고 다시 누르세요.")
    on_status(ready)
    for _ in range(600):
        if not _chrome_pids(profile_dir):
            return
        time.sleep(1)
    raise PostError("로그인 대기 시간이 끝났습니다. 크롬 창을 닫고 다시 누르세요.")


def open_login(profile_dir: Path, on_status: StatusFn) -> None:
    _open_installed_chrome(
        profile_dir,
        "https://www.youtube.com/?hl=ko&gl=KR",
        on_status,
        opening="프로필 여는 중",
        ready="로그인 창이 열려 있습니다. 구글 계정으로 로그인한 뒤 창을 모두 닫으세요.",
        busy="이 프로필 크롬이 이미 열려 있습니다. 그 창을 닫고 다시 누르세요.",
    )


def _launch_profile(playwright, profile_dir: Path):
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        return playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=False,
            chromium_sandbox=True,
            viewport={"width": 1280, "height": 900},
            locale="ko-KR",
            args=["--lang=ko-KR"],
        )
    except Exception as exc:
        message = str(exc).lower()
        if "processsingleton" in message or "already in use" in message or "existing browser" in message:
            raise PostError("LiveWiki 크롬이 이미 열려 있습니다. 그 창을 닫고 다시 누르세요.") from exc
        raise PostError(f"LiveWiki 크롬을 열지 못했습니다. {exc}") from exc


def _chrome_executable() -> Path:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise PostError("구글 크롬을 찾지 못했습니다. 크롬을 설치한 뒤 다시 누르세요.")


def _chrome_pids(profile_dir: Path) -> set[int]:
    marker = str(profile_dir).lower()
    script = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "Get-CimInstance Win32_Process -Filter \"Name = 'chrome.exe'\" | "
        "ForEach-Object { '{0}`t{1}' -f $_.ProcessId, $_.CommandLine }"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    found: set[int] = set()
    for line in completed.stdout.splitlines():
        pid_text, _, command = line.partition("\t")
        if marker in command.lower() and pid_text.strip().isdigit():
            found.add(int(pid_text.strip()))
    return found


def open_livewiki_login(profile_dir: Path, home_url: str, on_status: StatusFn) -> None:
    _open_installed_chrome(
        profile_dir,
        home_url,
        on_status,
        opening="LiveWiki 프로필 여는 중",
        ready="LiveWiki 창이 열려 있습니다. 구글 계정으로 로그인한 뒤 창을 모두 닫으세요.",
        busy="LiveWiki 크롬이 이미 열려 있습니다. 그 창을 닫고 다시 누르세요.",
    )


def _clean_summary(text: str) -> str:
    noise = {
        "LiveWiki",
        "로그인",
        "Login",
        "빠르고 정확한 유튜브 요약!",
        "유튜브 링크를 붙여넣으세요",
        "요약하기",
        "공유",
        "복사",
    }
    lines = [line.strip() for line in (text or "").splitlines()]
    kept = [line for line in lines if len(line) >= 4 and line not in noise]
    return "\n".join(kept).strip()


def fetch_livewiki_summary(
    *,
    profile_dir: Path,
    home_url: str,
    video_url: str,
    shots_dir: Path,
    video_id: str,
    on_status: StatusFn,
) -> str:
    on_status("LiveWiki 요약 가져오는 중")
    login_message = "LiveWiki 로그인이 필요합니다. 2번의 LiveWiki 로그인을 누르고 구글 계정으로 로그인한 뒤 창을 닫으세요."
    with sync_playwright() as playwright:
        context = _launch_profile(playwright, profile_dir)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(home_url, wait_until="domcontentloaded", timeout=45000)
            box = page.locator("input[type='text']").first
            box.wait_for(state="visible", timeout=20000)
            if box.is_disabled():
                raise PostError(login_message, _shot(page, shots_dir, video_id))
            before = len(page.locator("body").inner_text())
            box.click()
            box.fill(video_url)
            box.press("Enter")
            page.wait_for_timeout(1500)
            if "accounts.google.com" in page.url:
                raise PostError(login_message, _shot(page, shots_dir, video_id))
            try:
                page.wait_for_function(
                    """(before) => {
                        const text = document.body.innerText || '';
                        const path = location.pathname || '';
                        const leftHome = path !== '/ko' && path !== '/ko/' && path !== '/';
                        const ready = !/요약\\s*중|생성\\s*중|불러오는\\s*중|loading/i.test(text);
                        if (leftHome) return text.length > 260 && ready;
                        return text.length > before + 240 && ready;
                    }""",
                    arg=before,
                    timeout=90000,
                )
                page.wait_for_timeout(1800)
            except PlaywrightTimeout as exc:
                raise PostError("LiveWiki 요약이 화면에 나타나지 않았습니다. 로그인 상태를 확인해 주세요.", _shot(page, shots_dir, video_id)) from exc
            if "accounts.google.com" in page.url:
                raise PostError(login_message, _shot(page, shots_dir, video_id))
            summary = ""
            for selector in ("article", "main"):
                node = page.locator(selector).first
                if node.count():
                    summary = _clean_summary(node.inner_text())
                    if len(summary) >= 80:
                        break
            if len(summary) < 80:
                summary = _clean_summary(page.locator("body").inner_text())
            if len(summary) < 40:
                raise PostError("LiveWiki 요약문을 읽지 못했습니다.", _shot(page, shots_dir, video_id))
            return summary[:2400]
        except PostError:
            raise
        except Exception as exc:
            screenshot = _shot(page, shots_dir, video_id)
            raise PostError(f"LiveWiki 요약을 가져오지 못했습니다. {exc}", screenshot) from exc
        finally:
            try:
                context.close()
            except Exception:
                pass
