# ADB File Transfer GUI

Windows上で `adb push` / `adb pull` をGUI操作するためのシンプルなファイル転送ツールです。

## バージョン

- v0.2.2


## v0.2.2 修正内容

- Android側ディレクトリ読込を別スレッド化しました。
- microSD上のフォルダを開いたときにGUIが「応答なし」になりにくいようにしました。
- Android側の `ls` / `stat` / フォルダ判定にタイムアウトを追加しました。

## 主な機能

- 複数Android端末のプルダウン選択
- Windows側ファイルブラウザ
- Android側ファイルブラウザ
- Android側の外部ストレージ候補を自動検出
- `adb push` / `adb pull`
- 転送進捗インジケータ
- ディレクトリ表示の更新日時・容量列
- Windows側のデスクトップ / ダウンロード移動ボタン
- Windows側 / Android側の新しいフォルダ作成
- ADB実行ログ表示
- `adb kill-server` / `adb start-server` によるADB再起動

## スクリーンショット

必要に応じて、ここにアプリ画面のスクリーンショットを追加してください。

```md
![screenshot](docs/screenshot.png)
```

## 必要なもの

- Windows 10 / 11
- Python 3.11 以降推奨
- Android SDK Platform-Tools for Windows

## Platform-Tools の配置

このリポジトリには `adb.exe` やDLLは含めません。

Google公式の Windows版 Platform-Tools を展開し、以下のファイルを `platform-tools` フォルダへ配置してください。

```text
platform-tools/
├─ adb.exe
├─ AdbWinApi.dll
└─ AdbWinUsbApi.dll
```

`adb.exe` だけではなく、DLLも同じフォルダに置いてください。

## フォルダ構成

```text
adb_file_transfer_gui/
├─ adb_gui.py
├─ build_exe.bat
├─ requirements.txt
├─ README.md
├─ .gitignore
└─ platform-tools/
   └─ PUT_PLATFORM_TOOLS_HERE.txt
```

## Pythonで直接起動

```powershell
py adb_gui.py
```

## exe化

```bat
build_exe.bat
```

完了後、以下にexeが生成されます。

```text
dist/AdbFileTransfer.exe
```

## 使い方

1. Android端末で開発者向けオプションを有効化します。
2. USBデバッグを有効化します。
3. Windows PCにUSB接続します。
4. 端末側でUSBデバッグ許可を承認します。
5. アプリを起動し、端末をプルダウンから選択します。
6. Windows側とAndroid側のペインでコピー元・コピー先を選択します。
7. `Push` または `Pull` を実行します。

## 新しいフォルダ作成

- `Windows側に新しいフォルダ`
  - 現在表示中のWindowsフォルダ直下に作成します。
- `Android側に新しいフォルダ`
  - 現在表示中のAndroidフォルダ直下に作成します。

## 更新日時・容量表示について

- Windows側はOSのファイル情報を表示します。
- Android側は `adb shell stat -c '%s|%Y'` の結果を利用します。
- Android端末側で `stat` が使えない場合、更新日時・容量が空欄になる場合があります。

## microSDについて

microSDは `/storage/xxxx-xxxx/` のようなパスとして検出されることが多いため、`/storage` 配下を列挙して外部ストレージ候補として表示します。

ただし、端末やAndroidバージョンによっては以下の制限があります。

- microSDが内部ストレージ化されている場合、通常のUUIDパスで出ないことがあります。
- `/Android/data/` などはAndroid側の制限によりアクセスできない場合があります。
- メーカー独自実装により、ADB shellとMTPで見え方が異なる場合があります。

## 注意

- 転送進捗はADB出力の `%` を拾って表示します。
- フォルダ転送時は、全体進捗ではなく現在処理中ファイルの進捗として表示される場合があります。
- 端末が `unauthorized` の場合、Android端末側のUSBデバッグ許可を確認してください。

## ライセンス

このプロジェクトは MIT License で公開しています。

個人利用、改変、再配布は自由に行えます。
ただし、本ソフトウェアは無保証で提供されます。利用によって発生した問題について、作者は責任を負いません。

詳細は `LICENSE` ファイルを確認してください。
