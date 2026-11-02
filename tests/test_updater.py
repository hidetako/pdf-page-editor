"""自動アップデートのテスト（ネットワークなし）。python tests/test_updater.py"""
import io
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import updater
from updater import DOWNLOAD_PREFIX, UpdateInfo, check_for_update, download_installer, installer_args, is_newer, parse_version


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
    assert installer_args(r"C:\x\Setup.exe") == [r"C:\x\Setup.exe", "/SILENT", "/NORESTART", "/LAUNCH=1"]
    print("updater tests passed")


if __name__ == "__main__":
    main()
