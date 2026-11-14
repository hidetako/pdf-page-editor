"""自動アップデート（Qt 非依存）。GitHub Releases の最新版を調べ、インストーラーをダウンロードする。

- 参照先は本リポジトリの Releases だけ。まず API で調べ、API が使えないとき（同じ回線からの利用が多いと出る
  「403 rate limit exceeded」など）は、Releases のページの転送先（…/releases/tag/vX.Y.Z）から最新版の番号を読む。
  ダウンロード元も同リポジトリの Releases の URL に限定する
- 通信は urllib（標準ライブラリ）。接続先の証明書は、まず truststore（Windows 自身のしくみ。ブラウザーと同じ判定）で確かめ、
  それで通らないときだけ Python 標準の方法でも試す（_ssl_contexts 参照）
- ネットワーク不通・API エラーは例外にし、呼び出し側（起動時チェック）は黙って無視する
"""
from __future__ import annotations

import json
import os
import re
import socket
import ssl
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

GITHUB_REPO = "hidetako/pdf-page-editor"
API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{GITHUB_REPO}/releases/download/"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
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


TAG_URL_RE = re.compile(r"^https://github\.com/" + re.escape(GITHUB_REPO) + r"/releases/tag/(v?\d+(?:\.\d+){0,3})/?$")


def latest_tag_from_page(resolve: Callable[[str], str] | None = None) -> tuple[str, str]:
    """API を使わずに最新版のタグを調べる。戻り値は (タグ, リリースページの URL)。

    https://github.com/<repo>/releases/latest は、最新の正式リリースのページ（…/releases/tag/vX.Y.Z）へ転送される。
    その転送先の URL からタグを読む。API と違って「1 時間あたり 60 回まで（同じ回線の合計）」の制限を受けない。
    resolve は URL を渡すと転送先の URL を返す関数（テストで差し替える）。
    """
    final = (resolve or _resolve_url)(RELEASES_PAGE)
    m = TAG_URL_RE.match(str(final))
    if not m:
        raise OSError(f"最新版のページを確認できませんでした（{final}）。")
    return m.group(1), m.group(0).rstrip("/")


def _info_from_tag(tag: str, page_url: str) -> UpdateInfo:
    """タグだけ分かっているときの UpdateInfo。インストーラーの名前は workflow が付ける決まった形から組み立てる。
    リリースノートとファイルサイズは分からない（サイズはダウンロード時に Content-Length を使う）。"""
    name = f"PdfPageEditor-Setup-{tag.lstrip('vV')}.exe"
    return UpdateInfo(version=".".join(str(n) for n in parse_version(tag)), tag=tag, notes="", page_url=page_url,
                      installer_url=f"{DOWNLOAD_PREFIX}{tag}/{name}", installer_name=name, installer_size=0)


def check_for_update(current_version: str, fetch: Callable[[str], bytes] | None = None,
                     resolve: Callable[[str], str] | None = None) -> UpdateInfo | None:
    """新しい版があれば UpdateInfo、無ければ None。

    まず Releases API で調べる。API が使えないときは、Releases のページの転送先から最新版のタグを読む
    （latest_tag_from_page）。どちらもだめなら、API のエラーを返す。
    fetch だけを差し替えたとき（テスト）は、実際のネットワークに出ないよう、ページからの確認はしない。
    """
    try:
        data = fetch_latest_release(fetch)
    except Exception as api_error:
        if fetch is not None and resolve is None:
            raise
        try:
            tag, page_url = latest_tag_from_page(resolve)
        except Exception:
            raise api_error from None
        return _info_from_tag(tag, page_url) if is_newer(tag, current_version) else None
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
    expected = info.installer_size or total      # API で調べられなかったときは、サーバーが知らせた大きさ（Content-Length）と比べる
    if expected and received != expected:
        os.remove(tmp)
        raise OSError(f"ダウンロードしたサイズが一致しません（{received} / {expected}）。")
    os.replace(tmp, dest)
    return dest


def install_scope_arg(exe_path: str | None, env: dict | None = None) -> str:
    """実行中の exe の場所から、インストーラーに渡すインストール範囲を決める。

    Program Files 配下で動いていれば /ALLUSERS（全ユーザー用）、それ以外（%LOCALAPPDATA%\\Programs など）は
    /CURRENTUSER（現在のユーザー用）。更新を必ず同じ場所へ上書きさせ、別の範囲に二重インストールされて
    「プログラムから開く」に同名の項目が 2 つ並ぶのを防ぐ。
    """
    env = os.environ if env is None else env

    def norm(p: str) -> str:
        return p.replace("\\", "/").rstrip("/").lower()

    if exe_path:
        exe = norm(exe_path)
        for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            d = env.get(key)
            if d and exe.startswith(norm(d) + "/"):
                return "/ALLUSERS"
    return "/CURRENTUSER"


def installer_args(path: str, exe_path: str | None = None, env: dict | None = None) -> list[str]:
    """インストーラーをサイレント実行し、完了後にアプリを起動させる引数（Inno Setup）。

    exe_path は今動いているアプリの exe。その場所に合わせて /ALLUSERS か /CURRENTUSER を付ける。
    """
    return [path, "/SILENT", "/NORESTART", "/LAUNCH=1", install_scope_arg(exe_path, env)]


def installer_env(environ: dict | None = None, bundle_dir: str | None = None) -> dict:
    """インストーラーを起動するときの環境変数。PyInstaller が自分用に入れたものを取り除いたコピーを返す。

    1 ファイル版の exe は、起動のたびに一時フォルダー（_MEI…）へ中身を展開し、その場所を環境変数
    （_PYI_APPLICATION_HOME_DIR など）で自分の子プロセスに伝える。これがインストーラー経由で
    「更新後に起動し直したアプリ」まで引き継がれると、新しいアプリは古いアプリの一時フォルダー（すでに削除済み）を
    使おうとして、「Failed to load Python DLL …\\_MEI…\\python312.dll」で起動に失敗する。

    bundle_dir は今のアプリの一時フォルダー（sys._MEIPASS）。PATH などに入っているその場所も取り除く。
    """
    env = dict(os.environ if environ is None else environ)
    for key in list(env):
        k = key.upper()
        if k.startswith("_PYI_") or k == "_MEIPASS2":
            del env[key]
    if bundle_dir:
        def norm(p: str) -> str:
            return p.replace("\\", "/").rstrip("/").lower()

        base = norm(bundle_dir)

        def inside(p: str) -> bool:
            n = norm(p)
            return n == base or n.startswith(base + "/")

        sep = ";" if ("\\" in bundle_dir or os.pathsep == ";") else os.pathsep
        for key in list(env):
            value = env[key]
            if key.upper() == "PATH":
                env[key] = sep.join(part for part in value.split(sep) if not inside(part))
            elif value and inside(value.split(sep)[0]):      # QT_PLUGIN_PATH など、一時フォルダーの中を指すもの
                del env[key]
    return env


def _ssl_contexts() -> list:
    """接続先の証明書を確かめる方法を、試す順に返す。

    1. truststore … Windows 自身のしくみ（ブラウザーや PowerShell と同じ）で確かめる。
    2. Python 標準 … Windows の証明書ストアに「すでに入っている」証明書だけで、Python（OpenSSL）が自分で確かめる。

    2 だけだと、PC によっては正しい接続先でも CERTIFICATE_VERIFY_FAILED（unable to get issuer certificate）になる。
    Windows はルート証明書を必要になったときに取り寄せるので、まだストアに無い PC がある。また、セキュリティソフトや
    社内の機器が通信を検査している環境では、ストアの中身だけでは証明書のつながりをたどれないことがある。
    1 はどちらの場合も Windows と同じ判定になる。
    """
    contexts = []
    try:
        import truststore
        contexts.append(truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    except Exception:
        pass
    contexts.append(ssl.create_default_context())
    return contexts


def is_cert_error(exc: BaseException) -> bool:
    """接続先の証明書を確かめられなかったエラーか（urllib は URLError に包んで返す）。"""
    reason = getattr(exc, "reason", None)
    return isinstance(exc, ssl.SSLCertVerificationError) or isinstance(reason, ssl.SSLCertVerificationError)


def describe_error(exc: BaseException) -> str:
    """利用者に見せるエラー文。証明書のエラーは、理由の見当と手動で更新する方法を添える。"""
    detail = str(exc) or type(exc).__name__
    if is_cert_error(exc):
        return ("接続先（GitHub）の証明書を確かめられませんでした。\n"
                "セキュリティソフトや職場のネットワークが通信を検査していると起こることがあります。\n\n"
                "最新版は、ブラウザーで次のページを開くと入手できます。\n"
                f"{RELEASES_PAGE}\n\n"
                f"（詳細: {detail}）")
    if getattr(exc, "code", None) in (403, 429):
        return ("GitHub が一時的に問い合わせを受け付けていません（同じ回線からの利用が多いときに出る制限です）。\n"
                "しばらく（最長 1 時間ほど）待つと、また確認できるようになります。\n\n"
                "最新版は、ブラウザーで次のページを開いても入手できます。\n"
                f"{RELEASES_PAGE}\n\n"
                f"（詳細: {detail}）")
    return detail


def tls_selftest(host: str = "api.github.com", timeout: float = 6.0) -> str:
    """自己診断用: truststore で host に TLS 接続できるか。結果を 1 行で返す。

    truststore が入っていない・証明書を確かめられないときは RuntimeError。ネットワークに出られないだけなら
    「skipped」と返す（診断は失敗にしない）。
    """
    try:
        import truststore
    except Exception as e:
        raise RuntimeError(f"truststore is not available: {e}") from e
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                return f"TLS (truststore) to {host}: OK, {tls.version()}"
    except ssl.SSLCertVerificationError as e:
        raise RuntimeError(f"TLS (truststore) to {host}: certificate verification failed: {e}") from e
    except OSError as e:
        return f"TLS (truststore) to {host}: skipped ({type(e).__name__}: {e})"


def _http_get(url: str) -> bytes:
    with _http_open(url) as r:
        return r.read()


def _resolve_url(url: str) -> str:
    """url を開き、転送をたどった先の URL を返す（中身は読まない）。"""
    with _http_open(url, accept="text/html") as r:
        return r.geturl()


def _http_open(url: str, accept: str = "application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    last: BaseException | None = None
    for context in _ssl_contexts():
        try:
            return urllib.request.urlopen(req, timeout=TIMEOUT, context=context)
        except Exception as e:
            if not is_cert_error(e):        # 証明書以外のエラー（圏外・タイムアウトなど）は、方法を変えても同じ
                raise
            last = e
    raise last if last is not None else OSError("no TLS context")
