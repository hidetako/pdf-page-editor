# PDF Page Editor

Windows 向けの軽量 PDF ビューア + ページ編集 + 電子印鑑ツール。1 つの画面で「見る」「ページ構成を直す」「押印して保存」ができる。

## 機能
- PDF 表示（全ページを縦に並べた連続スクロール。幅合わせ / ズーム 50〜300% / Ctrl+ホイール、PgUp/PgDn でページ移動。表示中のページがサムネイルで選択される）
- サムネイル一覧をドラッグ&ドロップで並べ替え（複数選択可）
- 回転（左右 90°）、削除、前後移動
- 他の PDF・画像（PNG/JPG/TIFF など）をページとして挿入（選択ページの後ろ）
- 選択ページだけを別 PDF として抽出
- 元に戻す / やり直し（20 段階）
- 保存（上書き）/ 名前を付けて保存。一時ファイル経由のアトミック書き込み
- ウィンドウへのファイルドロップで開く / 追加

未対応: パスワード付き PDF、注釈・テキスト編集、印刷。

## 動作環境
- Windows 10 / 11 (64bit)、Python 3.10 以上（exe 化後は Python 不要）
- ライブラリ: PyMuPDF (AGPL-3.0)、PySide6 (LGPL-3.0)

## 押印の手順
1. 右側「電子印鑑」パネルで印鑑を選ぶ（「新規」「編集」で文字や大きさを変更）
2. 「押印モード」ボタン（または F2）を押す
3. ページ上の押したい位置をクリック。続けて何か所でも押せる。Esc で終了
4. 「保存」。押印を取り消すには、印影を右クリック →「この押印を取り消す」（または Ctrl+Z で直前の操作を戻す）

## 他ソフトの印影を取り込む
「おまかせ電子印鑑 for Business」でエクスポートした `.entstmp` は、印鑑パネルの「インポート」からそのまま読み込める（複数選択可）。大きさの情報は含まれていないので 15 mm で登録される。「編集」で直径や名前を直す。

## インストール（Windows、ダウンロードして実行するだけ）
1. [Releases](https://github.com/hidetako/pdf-page-editor/releases) から `PdfPageEditor-Setup-x.y.z.exe` をダウンロード
2. ダブルクリックして「次へ」。管理者権限は不要（ユーザー領域にインストールされる）
3. スタートメニューの「PDF Page Editor」から起動。エクスプローラーで PDF を右クリック →「プログラムから開く」にも出る

未署名のため SmartScreen の警告が出る。「詳細情報」→「実行」で進める。
アンインストールは Windows の「アプリと機能」から。印鑑データは `%APPDATA%\PdfPageEditor\stamps.json` に残る。

## 使い方（ソースから）
1. このフォルダを `C:\dev\pdf-page-editor` などに置く
2. `run.bat` をダブルクリック（初回は仮想環境作成とライブラリ取得で数分）

## exe・インストーラーを自分で作る
- ローカル: `build_windows.bat` をダブルクリック。`dist\PdfPageEditor.exe` ができ、Inno Setup 6 が入っていれば `dist\PdfPageEditor-Setup-x.y.z.exe` も作られる
- GitHub Actions: `app.py` の `__version__` を上げてタグ `vX.Y.Z` を push すると、`.github/workflows/release.yml` が Windows でビルドし Releases に公開する

```
git tag v1.1.0
git push origin v1.1.0
```

## 構成
- `pdfmodel.py` : PyMuPDF をラップした編集ロジック（Qt 非依存、単体テスト可）
- `stamps.py`   : 印影の定義・描画・ライブラリ保存（Qt 非依存）
- `stamp_ui.py` : 印鑑パネルと印鑑編集ダイアログ
- `app.py`      : PySide6 GUI 本体
- `assets/icon.ico` : アプリアイコン（`assets/make_icon.py` で生成）
- `installer/PdfPageEditor.iss` : Inno Setup のインストーラー定義
- `tests/test_model.py`, `tests/test_stamps.py` : 自動テスト（直接実行）
- `tests/test_e2e_gui.py` : GUI の E2E テスト（オフスクリーンで実際の画面を操作。`E2E_SHOTS=出力先` で各段階のスクリーンショットを保存）

## ライセンス注意
PyMuPDF は AGPL-3.0 のため、本アプリを配布する場合はソース公開義務が生じる（社内利用・個人利用なら問題なし）。
