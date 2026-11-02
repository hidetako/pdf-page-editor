"""自動アップデート（Qt 非依存）。GitHub Releases の最新版を調べ、インストーラーをダウンロードする。

- 参照先は本リポジトリの Releases API だけ。ダウンロード元も同リポジトリの Releases の URL に限定する
- 通信は urllib（標準ライブラリ）。exe 化後は Windows の証明書ストアで TLS 検証される
- ネットワーク不通・API エラーは例外にし、呼び出し側（起動時チェック）は黙って無視する
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.request
from dataclasses import dataclass
from typing import Callable

GITHUB_REPO = "hidetako/pdf-page-editor"
API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{GITHUB_REPO}/releases/download/"
INSTALLER_RE = re.compile(r"^PdfPageEditor-Setup-.*\.exe$", re.I)
USER_AGENT = "PdfPageEditor-updater"
TIMEOUT = 8


@dataclass
class UpdateInfo:
    version: str            # "1.3.0"
    tag: str                # "v1.3.0"
    notes: str              # リリースノート（Markdown）
    page_url: str           # リリースページ
    installer_url: str      # インストーラー exe（無ければ ""）
    installer_name: str
    installer_size: int


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.0' / '1.2' / '1.2.0-dev.abc' → (1, 2, 0)。数字以外は無視する。"""
    core = re.split(r"[-+ ]", text.strip().lstrip("vV"), 1)[0]
    nums = [int(p) for p in core.split(".") if p.isdigit()]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def fetch_latest_release(fetch: Callable[[str], bytes] | None = None) -> dict:
    """Releases API の JSON を返す。fetch を差し替えるとテストでネットワークなしで動く。"""
    if fetch is None:
        fetch = _http_get
    return json.loads(fetch(API_LATEST).decode("utf-8"))


def check_for_update(current_version: str, fetch: Callable[[str], bytes] | None = None) -> UpdateInfo | None:
    """新しい版があれば UpdateInfo、無ければ None。"""
    data = fetch_latest_release(fetch)
    tag = str(data.get("tag_name", ""))
    if not tag or data.get("draft") or data.get("prerelease"):
        return None
    if not is_newer(tag, current_version):
        return None
    asset = next((a for a in data.get("assets", []) if INSTALLER_RE.match(str(a.get("name", "")))), None)
    url = str(asset.get("browser_download_url", "")) if asset else ""
    if url and not url.startswith(DOWNLOAD_PREFIX):
        url = ""   # 想定外の配布元は使わない
    return UpdateInfo(
        version=".".join(str(n) for n in parse_version(tag)),
        tag=tag,
        notes=str(data.get("body") or ""),
        page_url=str(data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}"),
        installer_url=url,
        installer_name=str(asset.get("name", "")) if asset else "",
        installer_size=int(asset.get("size") or 0) if asset else 0,
    )


def download_installer(info: UpdateInfo, progress: Callable[[int, int], bool] | None = None,
                       opener: Callable[[str], object] | None = None) -> str:
    """インストーラーを一時フォルダへダウンロードしてパスを返す。
    progress(received, total) が False を返したら中断して FileNotFoundError ではなく InterruptedError。
    opener を差し替えるとテストでネットワークなしで動く（read(n) を持つファイル風オブジェクトを返す）。"""
    if not info.installer_url.startswith(DOWNLOAD_PREFIX):
        raise ValueError("インストーラーの配布元が想定と異なります。")
    d = os.path.join(tempfile.gettempdir(), "PdfPageEditor-update")
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, info.installer_name or "PdfPageEditor-Setup.exe")
    tmp = dest + ".part"
    resp = opener(info.installer_url) if opener else _http_open(info.installer_url)
    total = info.installer_size or int(getattr(resp, "headers", {}).get("Content-Length", 0) or 0)
    received = 0
    try:
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
                if progress is not None and progress(received, total) is False:
                    raise InterruptedError("ダウンロードを中止しました。")
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    finally:
        close = getattr(resp, "close", None)
        if close:
            close()
    if info.installer_size and received != info.installer_size:
        os.remove(tmp)
        raise OSError(f"ダウンロードしたサイズが一致しません（{received} / {info.installer_size}）。")
    os.replace(tmp, dest)
    return dest


def installer_args(path: str) -> list[str]:
    """インストーラーをサイレント実行し、完了後にアプリを起動させる引数（Inno Setup）。"""
    return [path, "/SILENT", "/NORESTART", "/LAUNCH=1"]


def _http_get(url: str) -> bytes:
    with _http_open(url) as r:
        return r.read()


def _http_open(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=TIMEOUT)
