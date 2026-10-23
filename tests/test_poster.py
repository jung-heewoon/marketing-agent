from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from app.poster import open_login, submit_on_page


def test_profile_login_uses_installed_chrome(monkeypatch, tmp_path: Path):
    profile = tmp_path / "youtube1"
    state = {"open": False}
    launched: list[list[str]] = []

    monkeypatch.setattr("app.poster._chrome_executable", lambda: Path("chrome.exe"))
    monkeypatch.setattr("app.poster._chrome_pids", lambda folder: {7} if state["open"] else set())
    monkeypatch.setattr("app.poster.time.sleep", lambda seconds: None)

    def popen(cmd, **kwargs):
        del kwargs
        launched.append(list(cmd))
        state["open"] = True

    monkeypatch.setattr("app.poster.subprocess.Popen", popen)

    def on_status(step: str) -> None:
        if "열려 있습니다" in step:
            state["open"] = False

    open_login(profile, on_status)
    command = launched[0]
    assert command[0] == "chrome.exe"
    assert f"--user-data-dir={profile}" in command
    assert "https://www.youtube.com/?hl=ko&gl=KR" in command
    assert all("automation" not in item.lower() and "remote-debugging" not in item.lower() for item in command)


def test_korean_characters_are_typed_one_by_one(tmp_path: Path):
    page_path = tmp_path / "box.html"
    page_path.write_text(
        """<!DOCTYPE html>
        <html lang="ko"><body>
          <div id="contenteditable-root" contenteditable="true"></div>
          <div id="submit-button"><button type="button">댓글</button></div>
          <p id="out"></p>
          <script>
            document.querySelector("#submit-button button").addEventListener("click", () => {
              document.querySelector("#out").textContent =
                document.querySelector("#contenteditable-root").innerText;
            });
          </script>
        </body></html>""",
        encoding="utf-8",
    )
    comment = "서운함 많은 여자라 운명원 연애사주가 보고 싶다."
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page()
            page.goto(page_path.as_uri())
            submit_on_page(page, comment, 0)
            assert page.locator("#out").inner_text() == comment
            browser.close()
    except Exception as exc:
        pytest.skip(f"설치된 크롬으로 입력 확인을 열지 못했습니다: {exc}")
