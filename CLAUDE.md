# PDF Page Editor — 開発ルール（Claude 向け）

- 作業ブランチの PR は、単体テスト（`tests/test_model.py`, `tests/test_stamps.py`）と GUI E2E（`tests/test_e2e_gui.py`）が通過したら、ユーザーの確認を待たずに `main` へマージしてよい（2026-10-30 ユーザー指示）。
- リリースは `app.py` の `__version__` を上げて `main` にマージするだけ。`.github/workflows/release.yml` が Linux で E2E を含む全テストを実行し（必須）、Windows で単体テストとビルドを行い、タグ `vX.Y.Z` を自動で打って Releases に公開する（手動のタグ push は不要）。Windows 上の E2E は参考扱い（オフスクリーンにフォントが無く寸法も異なるため）。
- 開発は Windows の `C:\dev\pdf-page-editor` で確認される。OneDrive 配下のフォルダは使わない。
- ユーザーへの操作案内は PowerShell コマンドを 1 行ずつ別のコードブロックで示す。
- 起動時は印鑑パネルを非表示にする（押印モードに入ると自動で開く）。2026-10-31 ユーザー指示。
