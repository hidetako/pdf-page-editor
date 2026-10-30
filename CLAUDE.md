# PDF Page Editor — 開発ルール（Claude 向け）

- 作業ブランチの PR は、単体テスト（`tests/test_model.py`, `tests/test_stamps.py`）と GUI E2E（`tests/test_e2e_gui.py`）が通過したら、ユーザーの確認を待たずに `main` へマージしてよい（2026-10-30 ユーザー指示）。
- リリースは `app.py` の `__version__` を上げ、タグ `vX.Y.Z` を push する。`.github/workflows/release.yml` が Windows でビルドして Releases に公開する。
- 開発は Windows の `C:\dev\pdf-page-editor` で確認される。OneDrive 配下のフォルダは使わない。
- ユーザーへの操作案内は PowerShell コマンドを 1 行ずつ別のコードブロックで示す。
