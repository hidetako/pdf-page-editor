"""自動アップデートのテスト（ネットワークなし）。python tests/test_updater.py"""
import io
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import updater
from updater import DOWNLOAD_PREFIX, UpdateInfo, check_for_update, download_installer, install_scope_arg, installer_args, installer_env, is_newer, parse_version


def release(tag="v1.3.0", assets=None, **extra):
    d = {"tag_name": tag, "html_url": f"https://github.com/hidetako/pdf-page-editor/releases/tag/{tag}",
         "body": "## 変更\n- 印刷", "draft": False, "prerelease": False,
         "assets": assets if assets is not None else [
             {"name": "PdfPageEditor.exe", "browser_download_url": DOWNLOAD_PREFIX + f"{tag}/PdfPageEditor.exe", "size": 10},
             {"name": f"PdfPageEditor-Setup-{tag[1:]}.exe", "browser_download_url": DOWNLOAD_PREFIX + f"{tag}/PdfPageEditor-Setup-{tag[1:]}.exe", "size": 12345},
         ]}
    d.update(extra)
    return lambda url: json.dumps(d).encode("utf-8")


class FakeResp(io.BytesIO):
    headers = {}


def main():
    assert parse_version("v1.2.0") == (1, 2, 0) and parse_version("1.10") == (1, 10, 0) and parse_version("1.2.3-dev.abc") == (1, 2, 3)
    assert is_newer("v1.3.0", "1.2.0") and is_newer("v2.0.0", "1.99.9") and not is_newer("v1.2.0", "1.2.0") and not is_newer("1.1.9", "1.2.0")

    info = check_for_update("1.2.0", release())
    assert info and info.version == "1.3.0" and info.tag == "v1.3.0" and info.installer_name == "PdfPageEditor-Setup-1.3.0.exe"
    assert info.installer_url.startswith(DOWNLOAD_PREFIX) and info.installer_size == 12345 and "印刷" in info.notes
    assert check_for_update("1.3.0", release()) is None, "同じ版は更新なし"
    assert check_for_update("1.4.0", release()) is None, "新しい版を使っていれば更新なし"
    assert check_for_update("1.2.0", release(prerelease=True)) is None and check_for_update("1.2.0", release(draft=True)) is None
    assert check_for_update("1.2.0", release(assets=[])).installer_url == "", "インストーラーが無ければ URL は空"
    bad = release(assets=[{"name": "PdfPageEditor-Setup-1.3.0.exe", "browser_download_url": "https://evil.example/x.exe", "size": 1}])
    assert check_for_update("1.2.0", bad).installer_url == "", "想定外の配布元は使わない"
    try:
        check_for_update("1.2.0", lambda url: (_ for _ in ()).throw(OSError("offline")))
        raise AssertionError("通信エラーが握りつぶされた")
    except OSError:
        pass

    # ダウンロード: サイズ検証、進捗、中止、配布元チェック
    data = os.urandom(300 * 1024)
    info = UpdateInfo("1.3.0", "v1.3.0", "", "", DOWNLOAD_PREFIX + "v1.3.0/PdfPageEditor-Setup-1.3.0.exe", "PdfPageEditor-Setup-1.3.0.exe", len(data))
    seen = []
    path = download_installer(info, lambda r, t: seen.append((r, t)) or True, lambda url: FakeResp(data))
    assert os.path.exists(path) and path.endswith("PdfPageEditor-Setup-1.3.0.exe") and open(path, "rb").read() == data
    assert seen and seen[-1] == (len(data), len(data)) and len(seen) >= 2
    os.remove(path)
    wrong = UpdateInfo("1.3.0", "v1.3.0", "", "", info.installer_url, info.installer_name, len(data) + 1)
    try:
        download_installer(wrong, None, lambda url: FakeResp(data)); raise AssertionError("サイズ不一致が通った")
    except OSError:
        pass
    try:
        download_installer(info, lambda r, t: False, lambda url: FakeResp(data)); raise AssertionError("中止できない")
    except InterruptedError:
        pass
    assert not os.path.exists(path + ".part"), "途中ファイルが残らない"
    try:
        download_installer(UpdateInfo("1", "v1", "", "", "https://evil.example/a.exe", "a.exe", 0), None, lambda url: FakeResp(b"x"))
        raise AssertionError("想定外の配布元からダウンロードした")
    except ValueError:
        pass
    env = {"ProgramFiles": r"C:\Program Files", "ProgramFiles(x86)": r"C:\Program Files (x86)", "LOCALAPPDATA": r"C:\Users\u\AppData\Local"}
    # 現在のユーザー用（%LOCALAPPDATA%\Programs）で動いていれば /CURRENTUSER
    assert install_scope_arg(r"C:\Users\u\AppData\Local\Programs\PdfPageEditor\PdfPageEditor.exe", env) == "/CURRENTUSER"
    # Program Files 配下なら /ALLUSERS（大文字小文字・区切り文字の違いは無視）
    assert install_scope_arg(r"c:\program files\PdfPageEditor\PdfPageEditor.exe", env) == "/ALLUSERS"
    assert install_scope_arg("C:/Program Files (x86)/PdfPageEditor/PdfPageEditor.exe", env) == "/ALLUSERS"
    # 似た名前の別フォルダや、exe 不明のときは現在のユーザー用に倒す
    assert install_scope_arg(r"C:\Program Files2\PdfPageEditor\PdfPageEditor.exe", env) == "/CURRENTUSER"
    assert install_scope_arg(None, env) == "/CURRENTUSER"
    assert install_scope_arg(r"C:\dev\pdf-page-editor\dist\PdfPageEditor.exe", {}) == "/CURRENTUSER"
    assert installer_args(r"C:\x\Setup.exe", r"C:\Users\u\AppData\Local\Programs\PdfPageEditor\PdfPageEditor.exe", env) == \
        [r"C:\x\Setup.exe", "/SILENT", "/NORESTART", "/LAUNCH=1", "/CURRENTUSER"]
    assert installer_args(r"C:\x\Setup.exe", r"C:\Program Files\PdfPageEditor\PdfPageEditor.exe", env)[-1] == "/ALLUSERS"
    # インストーラーには PyInstaller 用の環境変数を渡さない（更新後に起動し直したアプリが、古い一時フォルダーを使おうとして失敗するため）
    frozen = {"PATH": r"C:\Windows", "TEMP": r"C:\T", "_PYI_ARCHIVE_FILE": r"C:\app\PdfPageEditor.exe",
              "_PYI_APPLICATION_HOME_DIR": r"C:\T\_MEI123", "_PYI_PARENT_PROCESS_LEVEL": "1", "_pyi_splash_ipc": "1", "_MEIPASS2": r"C:\T\_MEI123"}
    cleaned = installer_env(frozen)
    assert cleaned == {"PATH": r"C:\Windows", "TEMP": r"C:\T"}, cleaned
    assert "_PYI_ARCHIVE_FILE" in frozen, "元の辞書は書き換えない"
    assert installer_env({"A": "1"}) == {"A": "1"}
    # アプリの一時フォルダーを指しているもの（PATH の一部、Qt のプラグインの場所）も渡さない
    frozen = {"Path": r"C:\T\_MEI123;C:\Windows;c:\t\_mei123\PySide6;C:\T\_MEI1234", "QT_PLUGIN_PATH": r"C:\T\_MEI123\PySide6\plugins",
              "QML2_IMPORT_PATH": r"C:\T\_MEI123\PySide6\qml", "TEMP": r"C:\T", "OTHER": r"C:\T\_MEI1234\x", "EMPTY": ""}
    cleaned = installer_env(frozen, r"C:\T\_MEI123")
    assert cleaned == {"Path": r"C:\Windows;C:\T\_MEI1234", "TEMP": r"C:\T", "OTHER": r"C:\T\_MEI1234\x", "EMPTY": ""}, cleaned
    assert isinstance(installer_env(), dict) and not any(k.upper().startswith("_PYI_") for k in installer_env())
    test_tls()
    test_fallback_without_api()
    print("updater tests passed")


def test_fallback_without_api():
    """API が使えないとき（403 rate limit exceeded など）は、Releases のページの転送先から最新版を調べる。"""
    import urllib.error

    def limited(url):
        raise urllib.error.HTTPError(url, 403, "rate limit exceeded", {}, None)

    base = "https://github.com/hidetako/pdf-page-editor/releases/"
    asked = []

    def page(tag):
        def resolve(url):
            asked.append(url)
            return base + "tag/" + tag
        return resolve

    info = check_for_update("1.5.3", limited, page("v1.5.4"))
    assert asked == [updater.RELEASES_PAGE] and info is not None
    assert (info.version, info.tag, info.notes, info.installer_size) == ("1.5.4", "v1.5.4", "", 0)
    assert info.installer_name == "PdfPageEditor-Setup-1.5.4.exe" and info.page_url == base + "tag/v1.5.4"
    assert info.installer_url == DOWNLOAD_PREFIX + "v1.5.4/PdfPageEditor-Setup-1.5.4.exe"
    # 最新版と同じ・古いなら「更新なし」
    assert check_for_update("1.5.4", limited, page("v1.5.4")) is None
    assert check_for_update("1.6.0", limited, page("v1.5.4")) is None
    # API が使えるときはページを見ない
    asked.clear()
    assert check_for_update("1.5.3", release("v1.5.4"), page("v9.9.9")).version == "1.5.4" and asked == []
    # ページからも分からない（リリースが無い・想定外の転送先・通信できない）ときは、API のエラーを返す
    for bad in (lambda url: base, lambda url: "https://evil.example/releases/tag/v9.9.9",
                lambda url: base + "tag/v9.9.9/../../x", lambda url: (_ for _ in ()).throw(OSError("圏外"))):
        try:
            check_for_update("1.5.3", limited, bad); raise AssertionError("エラーにならない")
        except urllib.error.HTTPError as e:
            assert e.code == 403
    # fetch だけを差し替えたとき（テスト）は、実際のネットワークには出ない
    try:
        check_for_update("1.5.3", limited); raise AssertionError("エラーにならない")
    except urllib.error.HTTPError:
        pass
    # ページから調べたときはファイルサイズが分からないので、サーバーが知らせた大きさ（Content-Length）と比べる
    class Resp:
        def __init__(self, data, length):
            self._data, self.headers = data, {"Content-Length": str(length)}

        def read(self, n):
            chunk, self._data = self._data[:n], self._data[n:]
            return chunk

    path = download_installer(info, None, lambda url: Resp(b"x" * 1000, 1000))
    assert os.path.getsize(path) == 1000
    os.remove(path)
    try:
        download_installer(info, None, lambda url: Resp(b"x" * 900, 1000)); raise AssertionError("途中で切れたのに成功した")
    except OSError as e:
        assert "一致しません" in str(e)
    assert not os.path.exists(path) and not os.path.exists(path + ".part")
    text = updater.describe_error(urllib.error.HTTPError("u", 403, "rate limit exceeded", {}, None))
    assert updater.RELEASES_PAGE in text and "rate limit exceeded" in text and "待つ" in text, text
    assert updater.describe_error(urllib.error.HTTPError("u", 500, "Server Error", {}, None)) == "HTTP Error 500: Server Error"


def test_tls():
    """接続先の証明書の確かめ方: まず truststore（Windows 自身のしくみ）、だめなら Python 標準。"""
    import ssl
    import urllib.error

    contexts = updater._ssl_contexts()
    assert len(contexts) >= 2 and type(contexts[0]).__module__.startswith("truststore"), contexts
    assert isinstance(contexts[-1], ssl.SSLContext)

    cert_err = urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get issuer certificate"))
    assert updater.is_cert_error(cert_err) and updater.is_cert_error(cert_err.reason)
    assert not updater.is_cert_error(urllib.error.URLError("timed out")) and not updater.is_cert_error(OSError("x"))
    text = updater.describe_error(cert_err)
    assert updater.RELEASES_PAGE in text and "CERTIFICATE_VERIFY_FAILED" in text and "証明書" in text, text
    assert updater.describe_error(OSError("圏外")) == "圏外" and updater.describe_error(TimeoutError()) == "TimeoutError"

    real_contexts, real_urlopen = updater._ssl_contexts, updater.urllib.request.urlopen
    calls = []

    def fake_urlopen(behaviour):
        def urlopen(req, timeout=None, context=None):
            calls.append(context)
            result = behaviour[context]
            if isinstance(result, Exception):
                raise result
            return result
        return urlopen

    try:
        updater._ssl_contexts = lambda: ["windows", "python"]
        # 1 つ目で通ればそれを使う
        updater.urllib.request.urlopen = fake_urlopen({"windows": "ok1", "python": "ok2"})
        assert updater._http_open("https://api.github.com/x") == "ok1" and calls == ["windows"]
        # 1 つ目が証明書エラーなら 2 つ目で試す
        calls.clear()
        updater.urllib.request.urlopen = fake_urlopen({"windows": cert_err, "python": "ok2"})
        assert updater._http_open("https://api.github.com/x") == "ok2" and calls == ["windows", "python"]
        # 証明書以外のエラーは、方法を変えずにそのまま返す
        calls.clear()
        updater.urllib.request.urlopen = fake_urlopen({"windows": urllib.error.URLError("timed out"), "python": "ok2"})
        try:
            updater._http_open("https://api.github.com/x"); raise AssertionError("エラーにならない")
        except urllib.error.URLError as e:
            assert not updater.is_cert_error(e) and calls == ["windows"]
        # どちらも証明書エラーなら、そのエラーを返す
        calls.clear()
        updater.urllib.request.urlopen = fake_urlopen({"windows": cert_err, "python": cert_err})
        try:
            updater._http_open("https://api.github.com/x"); raise AssertionError("エラーにならない")
        except urllib.error.URLError as e:
            assert updater.is_cert_error(e) and calls == ["windows", "python"]
    finally:
        updater._ssl_contexts, updater.urllib.request.urlopen = real_contexts, real_urlopen


if __name__ == "__main__":
    main()
