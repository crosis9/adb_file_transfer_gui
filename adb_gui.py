import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
    BaseTk = TkinterDnD.Tk
except Exception:
    DND_FILES = None
    DND_AVAILABLE = False
    BaseTk = tk.Tk


APP_VERSION = "0.3.2"
APP_TITLE = f"ADB Command Builder v{APP_VERSION} | kuroha"

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@dataclass
class AndroidEntry:
    name: str
    path: str
    is_dir: bool


def app_base_dir() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def resource_path(relative_path: str) -> Path:
    return app_base_dir() / relative_path


def default_adb_path() -> str:
    bundled = resource_path("platform-tools/adb.exe")
    if bundled.exists():
        return str(bundled)
    return "adb"


def config_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("APPDATA")
        if root:
            return Path(root) / "AdbCommandBuilder"
    return Path.home() / ".adb_command_builder"


def config_path() -> Path:
    return config_dir() / "settings.json"


def normalize_remote_dir(path: str) -> str:
    path = (path or "").strip()
    if not path:
        path = "/sdcard/Download/"
    if not path.startswith("/"):
        path = "/" + path
    if not path.endswith("/"):
        path += "/"
    return path


def ps_single_quote(value: str) -> str:
    """
    PowerShell single-quoted literal.
    ' は '' にする。
    """
    return "'" + value.replace("'", "''") + "'"


def quote_android_shell_path(path: str) -> str:
    """
    Android shell用の単純なsingle quote。
    ' は '\'' にする。
    """
    return "'" + path.replace("'", "'\\''") + "'"


def sanitize_windows_filename(name: str, fallback: str = "adb_push") -> str:
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" ._")
    if not name:
        name = fallback
    return name[:120]


def make_safe_temp_filename(filename: str, index: int) -> str:
    """
    ADB push時のリモート一時名。
    非ASCIIやshell上で面倒な文字は _ に寄せる。
    最終名は mv で元名へ戻す。
    """
    p = Path(filename)
    suffix = p.suffix
    stem_source = p.stem or "file"

    name = unicodedata.normalize("NFKC", stem_source)
    chars = []
    for ch in name:
        if ch.isascii() and (ch.isalnum() or ch in "._-"):
            chars.append(ch)
        else:
            chars.append("_")

    stem = "".join(chars)
    stem = re.sub(r"_+", "_", stem).strip("._-")

    if not stem:
        stem = "file"

    return f"{stem}.adbtmp-{int(time.time())}-{index}{suffix}"


def remote_join(remote_dir: str, filename: str) -> str:
    return normalize_remote_dir(remote_dir) + filename


class AdbCommandBuilder(BaseTk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1120x860")
        self.minsize(980, 740)

        self.adb_path = tk.StringVar(value=default_adb_path())
        self.selected_device = tk.StringVar()
        self.remote_dir = tk.StringVar(value="/sdcard/Download/")
        self.browser_current_dir = tk.StringVar(value="/sdcard/Download/")
        self.command_status = tk.StringVar(value="未生成")
        self.browser_status = tk.StringVar(value="Android側ファイラー: 未読込")
        self.preserve_filename = tk.BooleanVar(value=True)
        self.stop_on_error = tk.BooleanVar(value=False)

        self.local_files: list[str] = []
        self.remote_history: list[str] = []
        self.android_loading = False

        self.load_settings()
        self.build_ui()
        self.refresh_file_list()
        self.generate_command()

    # -----------------------------
    # Settings
    # -----------------------------
    def load_settings(self):
        try:
            path = config_path()
            if not path.exists():
                return

            data = json.loads(path.read_text(encoding="utf-8"))

            if isinstance(data.get("adb_path"), str) and data["adb_path"]:
                self.adb_path.set(data["adb_path"])

            if isinstance(data.get("remote_dir"), str) and data["remote_dir"]:
                remote = normalize_remote_dir(data["remote_dir"])
                self.remote_dir.set(remote)
                self.browser_current_dir.set(remote)

            if isinstance(data.get("remote_history"), list):
                self.remote_history = [
                    normalize_remote_dir(str(x)) for x in data["remote_history"]
                    if isinstance(x, str) and x.strip()
                ][:20]

        except Exception:
            pass

    def save_settings(self):
        try:
            config_dir().mkdir(parents=True, exist_ok=True)

            data = {
                "adb_path": self.adb_path.get(),
                "remote_dir": normalize_remote_dir(self.remote_dir.get()),
                "remote_history": self.remote_history[:20],
            }

            config_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # -----------------------------
    # UI
    # -----------------------------
    def build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        self.build_adb_frame(root)

        middle = ttk.PanedWindow(root, orient="horizontal")
        middle.pack(fill="both", expand=True, pady=(10, 0))

        left = ttk.Frame(middle)
        right = ttk.Frame(middle)

        middle.add(left, weight=1)
        middle.add(right, weight=1)

        self.build_files_frame(left)
        self.build_remote_frame(right)
        self.build_android_browser_frame(right)

        self.build_options_frame(root)
        self.build_command_frame(root)

    def build_adb_frame(self, root):
        frame = ttk.LabelFrame(root, text="ADB / Device", padding=10)
        frame.pack(fill="x")

        ttk.Label(frame, text="ADB:").grid(row=0, column=0, sticky="w")

        adb_entry = ttk.Entry(frame, textvariable=self.adb_path)
        adb_entry.grid(row=0, column=1, sticky="ew", padx=8)
        adb_entry.bind("<KeyRelease>", lambda _e: self.on_input_changed())

        ttk.Button(frame, text="adb.exe参照", command=self.browse_adb).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(frame, text="端末検出", command=self.detect_devices).grid(row=0, column=3, padx=(0, 6))
        ttk.Button(frame, text="ADB接続キャッシュクリア", command=self.clear_device_connection_cache).grid(row=0, column=4)

        ttk.Label(frame, text="Device:").grid(row=1, column=0, sticky="w", pady=(8, 0))

        self.device_combo = ttk.Combobox(frame, textvariable=self.selected_device)
        self.device_combo.grid(row=1, column=1, sticky="ew", padx=8, pady=(8, 0))
        self.device_combo.bind("<<ComboboxSelected>>", lambda _e: self.generate_command())
        self.device_combo.bind("<KeyRelease>", lambda _e: self.generate_command())

        ttk.Label(frame, text="複数端末接続時はDeviceを選択。空欄なら -s なしで生成します。").grid(
            row=1, column=2, columnspan=3, sticky="w", pady=(8, 0)
        )

        frame.columnconfigure(1, weight=1)

    def build_files_frame(self, root):
        frame = ttk.LabelFrame(root, text="転送元ファイル", padding=10)
        frame.pack(fill="both", expand=True)

        self.drop_label = ttk.Label(
            frame,
            text="ここにファイルをドラッグ&ドロップ、または [ファイル追加] から複数選択",
            anchor="center",
            relief="ridge",
            padding=14
        )
        self.drop_label.pack(fill="x")

        if DND_AVAILABLE:
            self.drop_label.drop_target_register(DND_FILES)
            self.drop_label.dnd_bind("<<Drop>>", self.on_drop_files)
        else:
            self.drop_label.configure(
                text="D&Dは無効です。tkinterdnd2 が利用できません。[ファイル追加] を使用してください。"
            )

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=True, pady=(8, 0))

        self.file_listbox = tk.Listbox(list_frame, height=14, selectmode="extended")
        self.file_listbox.pack(side="left", fill="both", expand=True)

        yscroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.file_listbox.yview)
        yscroll.pack(side="right", fill="y")
        self.file_listbox.configure(yscrollcommand=yscroll.set)

        if DND_AVAILABLE:
            self.file_listbox.drop_target_register(DND_FILES)
            self.file_listbox.dnd_bind("<<Drop>>", self.on_drop_files)

        btns = ttk.Frame(frame)
        btns.pack(fill="x", pady=(8, 0))

        ttk.Button(btns, text="ファイル追加", command=self.browse_files).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="選択削除", command=self.remove_selected_files).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="全クリア", command=self.clear_files).pack(side="left", padx=(0, 8))

    def build_remote_frame(self, root):
        frame = ttk.LabelFrame(root, text="Android側保存先ディレクトリ", padding=10)
        frame.pack(fill="x")

        ttk.Label(frame, text="Remote Dir:").grid(row=0, column=0, sticky="w")

        self.remote_combo = ttk.Combobox(frame, textvariable=self.remote_dir, values=self.remote_history)
        self.remote_combo.grid(row=0, column=1, sticky="ew", padx=8)
        self.remote_combo.bind("<<ComboboxSelected>>", lambda _e: self.on_remote_changed())
        self.remote_combo.bind("<KeyRelease>", lambda _e: self.on_remote_key_changed())

        ttk.Button(frame, text="履歴保存", command=self.save_current_remote).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(frame, text="履歴クリア", command=self.clear_remote_history).grid(row=0, column=3)

        presets = ttk.Frame(frame)
        presets.grid(row=1, column=1, columnspan=3, sticky="w", pady=(8, 0))

        preset_values = [
            "/sdcard/Download/",
            "/sdcard/ROMs/",
            "/storage/emulated/0/Download/",
            "/storage/emulated/0/ROMs/",
        ]

        for value in preset_values:
            ttk.Button(
                presets,
                text=value,
                command=lambda v=value: self.set_remote_dir(v, save=False)
            ).pack(side="left", padx=(0, 6))

        frame.columnconfigure(1, weight=1)

    def build_android_browser_frame(self, root):
        frame = ttk.LabelFrame(root, text="Android側ファイラー（軽量表示）", padding=10)
        frame.pack(fill="both", expand=True, pady=(10, 0))

        path_frame = ttk.Frame(frame)
        path_frame.pack(fill="x")

        ttk.Label(path_frame, text="現在:").pack(side="left")
        path_entry = ttk.Entry(path_frame, textvariable=self.browser_current_dir)
        path_entry.pack(side="left", fill="x", expand=True, padx=8)
        path_entry.bind("<Return>", lambda _e: self.load_android_dir(self.browser_current_dir.get()))

        ttk.Button(path_frame, text="開く", command=lambda: self.load_android_dir(self.browser_current_dir.get())).pack(side="left", padx=(0, 6))
        ttk.Button(path_frame, text="親へ", command=self.open_android_parent).pack(side="left", padx=(0, 6))

        browser_buttons = ttk.Frame(frame)
        browser_buttons.pack(fill="x", pady=(8, 0))

        ttk.Button(browser_buttons, text="ストレージ候補検出", command=self.detect_storage_roots).pack(side="left", padx=(0, 6))
        ttk.Button(browser_buttons, text="現在の場所を保存先に設定", command=self.use_browser_dir_as_remote).pack(side="left", padx=(0, 6))
        ttk.Button(browser_buttons, text="選択フォルダを保存先に設定", command=self.use_selected_android_dir_as_remote).pack(side="left", padx=(0, 6))

        self.android_tree = ttk.Treeview(
            frame,
            columns=("path", "kind"),
            show="tree headings",
            selectmode="browse",
            height=10
        )
        self.android_tree.heading("#0", text="名前")
        self.android_tree.heading("path", text="パス")
        self.android_tree.heading("kind", text="種別")

        self.android_tree.column("#0", width=220, stretch=True)
        self.android_tree.column("path", width=320, stretch=True)
        self.android_tree.column("kind", width=60, stretch=False)

        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.android_tree.yview)
        self.android_tree.configure(yscrollcommand=yscroll.set)

        self.android_tree.pack(side="left", fill="both", expand=True, pady=(8, 0))
        yscroll.pack(side="right", fill="y", pady=(8, 0))

        self.android_tree.bind("<<TreeviewSelect>>", self.on_android_select)
        self.android_tree.bind("<Double-1>", self.on_android_double_click)

        ttk.Label(frame, textvariable=self.browser_status).pack(anchor="w", pady=(8, 0))

    def build_options_frame(self, root):
        frame = ttk.LabelFrame(root, text="生成オプション", padding=10)
        frame.pack(fill="x", pady=(10, 0))

        ttk.Checkbutton(
            frame,
            text="非ASCIIファイル名は一時ASCII名でpush後、Android側で元の日本語名へmvする",
            variable=self.preserve_filename,
            command=self.generate_command
        ).pack(anchor="w")

        ttk.Checkbutton(
            frame,
            text="エラー発生時にそこで停止する",
            variable=self.stop_on_error,
            command=self.generate_command
        ).pack(anchor="w", pady=(4, 0))

    def build_command_frame(self, root):
        frame = ttk.LabelFrame(root, text="生成PowerShellコマンド", padding=10)
        frame.pack(fill="both", expand=True, pady=(10, 0))

        btns = ttk.Frame(frame)
        btns.pack(fill="x")

        ttk.Button(btns, text="コマンド生成", command=self.generate_command).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="クリップボードへコピー", command=self.copy_command).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text=".ps1として保存", command=self.save_ps1).pack(side="left", padx=(0, 8))

        ttk.Label(btns, textvariable=self.command_status).pack(side="left", padx=(12, 0))

        self.command_text = tk.Text(frame, height=15, wrap="none")
        self.command_text.pack(fill="both", expand=True, pady=(8, 0))

        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.command_text.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.command_text.xview)
        self.command_text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        yscroll.pack(side="right", fill="y")
        xscroll.pack(side="bottom", fill="x")

    # -----------------------------
    # File handling
    # -----------------------------
    def on_drop_files(self, event):
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]

        self.add_files(paths)

    def browse_files(self):
        paths = filedialog.askopenfilenames(
            title="転送するファイルを選択",
            filetypes=[
                ("All files", "*.*"),
                ("ISO files", "*.iso"),
                ("Compressed files", "*.zip *.7z *.rar"),
                ("ROM files", "*.iso *.bin *.cue *.chd *.zip *.7z"),
            ]
        )
        self.add_files(paths)

    def add_files(self, paths):
        added = 0
        skipped = []

        for raw in paths:
            raw = str(raw).strip()
            if not raw:
                continue

            p = Path(raw)

            if not p.exists():
                skipped.append(f"存在しない: {raw}")
                continue

            if not p.is_file():
                skipped.append(f"ファイルではない: {raw}")
                continue

            normalized = str(p)

            if normalized not in self.local_files:
                self.local_files.append(normalized)
                added += 1

        self.refresh_file_list()
        self.generate_command()

        if skipped:
            messagebox.showwarning(
                "一部スキップ",
                "\n".join(skipped[:10]) + ("\n..." if len(skipped) > 10 else "")
            )

        if added:
            self.save_settings()

    def refresh_file_list(self):
        self.file_listbox.delete(0, "end")
        for path in self.local_files:
            self.file_listbox.insert("end", path)

    def remove_selected_files(self):
        selected = list(self.file_listbox.curselection())
        if not selected:
            return

        for idx in reversed(selected):
            del self.local_files[idx]

        self.refresh_file_list()
        self.generate_command()

    def clear_files(self):
        self.local_files.clear()
        self.refresh_file_list()
        self.generate_command()

    # -----------------------------
    # ADB / device
    # -----------------------------
    def browse_adb(self):
        path = filedialog.askopenfilename(
            title="adb.exeを選択",
            filetypes=[
                ("adb.exe", "adb.exe"),
                ("Executable", "*.exe"),
                ("All files", "*.*"),
            ]
        )

        if path:
            self.adb_path.set(path)
            self.save_settings()
            self.generate_command()

    def on_input_changed(self):
        self.save_settings()
        self.generate_command()

    def adb_base_cmd(self) -> list[str]:
        adb = self.adb_path.get().strip() or "adb"
        serial = self.get_serial_only()
        cmd = [adb]
        if serial:
            cmd += ["-s", serial]
        return cmd

    def run_adb_capture(self, args: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
        return subprocess.run(
            self.adb_base_cmd() + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=timeout,
        )

    def clear_device_connection_cache(self):
        """
        安全側の接続キャッシュクリア。
        Windows OSのドライバ/デバイス履歴は削除せず、アプリ上の端末リストとADBサーバー状態だけをリセットする。
        """
        adb = self.adb_path.get().strip() or "adb"

        self.selected_device.set("")
        self.device_combo["values"] = []

        messages = ["アプリ上の端末選択リストをクリアしました。"]

        try:
            result = subprocess.run(
                [adb, "kill-server"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                timeout=10,
            )

            if result.returncode == 0:
                messages.append("adb kill-server を実行しました。")
            else:
                messages.append("adb kill-server が失敗しました。")
                if result.stderr or result.stdout:
                    messages.append((result.stderr or result.stdout).strip())

        except Exception as e:
            messages.append(f"adb kill-server 実行時にエラー: {e}")

        self.command_status.set("ADB接続キャッシュをクリアしました。")
        self.browser_status.set("ADB接続キャッシュをクリアしました。必要に応じて端末検出を再実行してください。")
        self.generate_command()

        messagebox.showinfo(
            "ADB接続キャッシュクリア",
            "\n".join(messages) + "\n\n必要に応じてUSBを抜き差ししてから、端末検出を再実行してください。"
        )

    def detect_devices(self):
        adb = self.adb_path.get().strip() or "adb"

        try:
            result = subprocess.run(
                [adb, "devices", "-l"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                timeout=10,
            )
        except Exception as e:
            messagebox.showerror("ADBエラー", str(e))
            return

        if result.returncode != 0:
            messagebox.showerror("ADBエラー", result.stderr or result.stdout or "adb devices failed")
            return

        devices = []
        for line in result.stdout.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue

            parts = line.split()
            if len(parts) < 2:
                continue

            serial = parts[0]
            state = parts[1]
            details = " ".join(parts[2:])
            devices.append(f"{serial} {state} {details}".strip())

        self.device_combo["values"] = devices

        if devices:
            self.selected_device.set(devices[0])
            self.generate_command()
        else:
            messagebox.showwarning("未検出", "接続端末が見つかりません。USBデバッグ許可を確認してください。")

    def detect_storage_roots(self):
        """
        ストレージ候補は毎回クリアして再生成する。
        候補キャッシュが増え続けるのを防ぐ。
        """
        candidates = [
            "/sdcard/Download/",
            "/sdcard/ROMs/",
            "/storage/emulated/0/Download/",
            "/storage/emulated/0/ROMs/",
        ]

        try:
            result = self.run_adb_capture(["shell", "ls", "-1", "/storage"], timeout=10)
        except Exception as e:
            messagebox.showerror("ADBエラー", str(e))
            return

        if result.returncode != 0:
            messagebox.showerror("ADBエラー", result.stderr or result.stdout or "storage detect failed")
            return

        for name in result.stdout.splitlines():
            name = name.strip()
            if not name or name in {"emulated", "self"}:
                continue
            candidates.append(f"/storage/{name}/")
            candidates.append(f"/storage/{name}/ROMs/")
            candidates.append(f"/storage/{name}/ROMs/ps2/")

        clean = []
        for item in candidates:
            item = normalize_remote_dir(item)
            if item not in clean:
                clean.append(item)

        self.remote_history = clean[:20]
        self.remote_combo["values"] = self.remote_history

        if self.remote_history:
            self.remote_dir.set(self.remote_history[0])
            self.browser_current_dir.set(self.remote_history[0])
            self.load_android_dir(self.remote_history[0])

        self.save_settings()
        self.generate_command()
        self.browser_status.set(f"ストレージ候補を再生成しました: {len(self.remote_history)}件")

    def get_serial_only(self) -> str:
        value = self.selected_device.get().strip()
        if not value:
            return ""
        return value.split()[0]

    # -----------------------------
    # Remote dir
    # -----------------------------
    def set_remote_dir(self, value: str, save: bool = False):
        remote = normalize_remote_dir(value)
        self.remote_dir.set(remote)
        self.browser_current_dir.set(remote)
        if save:
            self.save_current_remote()
        else:
            self.save_settings()
            self.generate_command()

    def on_remote_changed(self):
        remote = normalize_remote_dir(self.remote_dir.get())
        self.remote_dir.set(remote)
        self.browser_current_dir.set(remote)
        self.save_settings()
        self.generate_command()

    def on_remote_key_changed(self):
        self.generate_command()

    def save_current_remote(self):
        current = normalize_remote_dir(self.remote_dir.get())
        self.remote_dir.set(current)

        self.remote_history = [current] + [x for x in self.remote_history if x != current]
        self.remote_history = self.remote_history[:20]
        self.remote_combo["values"] = self.remote_history

        self.save_settings()
        self.generate_command()

    def clear_remote_history(self):
        self.remote_history = []
        self.remote_combo["values"] = []
        self.save_settings()
        self.generate_command()
        self.browser_status.set("Android側ディレクトリ候補履歴をクリアしました。")

    # -----------------------------
    # Android browser
    # -----------------------------
    def list_android_dir(self, path: str) -> list[AndroidEntry]:
        path = normalize_remote_dir(path)
        qpath = quote_android_shell_path(path)

        # -p でディレクトリ末尾に / を付ける。statは取らないので軽い。
        result = self.run_adb_capture(["shell", f"ls -1Ap {qpath}"], timeout=15)

        if result.returncode != 0:
            # Android環境によって -A や -p が怪しい場合のフォールバック
            result = self.run_adb_capture(["shell", f"ls -1 {qpath}"], timeout=15)
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout or "ls failed")

            entries = []
            for line in result.stdout.splitlines():
                name = line.strip()
                if not name or name in {".", ".."}:
                    continue

                full_path = path + name
                qfull = quote_android_shell_path(full_path)
                check = self.run_adb_capture(["shell", f"[ -d {qfull} ] && echo DIR || echo FILE"], timeout=3)
                is_dir = check.stdout.strip() == "DIR"

                entries.append(AndroidEntry(
                    name=name,
                    path=full_path + "/" if is_dir and not full_path.endswith("/") else full_path,
                    is_dir=is_dir
                ))

            return entries

        entries = []
        for line in result.stdout.splitlines():
            name = line.strip()
            if not name or name in {".", ".."}:
                continue

            is_dir = name.endswith("/")
            display_name = name.rstrip("/")
            full_path = path + display_name

            entries.append(AndroidEntry(
                name=display_name,
                path=full_path + "/" if is_dir else full_path,
                is_dir=is_dir
            ))

        return entries

    def load_android_dir(self, path: str):
        if self.android_loading:
            self.browser_status.set("Android側ファイラー: 読込中です。")
            return

        path = normalize_remote_dir(path)
        self.android_loading = True
        self.browser_current_dir.set(path)
        self.android_tree.delete(*self.android_tree.get_children())
        self.android_tree.insert("", "end", text="読み込み中...", values=(path, "", ""))
        self.browser_status.set(f"Android側ファイラー: 読込中 {path}")

        def worker():
            try:
                entries = self.list_android_dir(path)
                self.after(0, self.apply_android_entries, path, entries, None)
            except Exception as e:
                self.after(0, self.apply_android_entries, path, [], e)

        threading.Thread(target=worker, daemon=True).start()

    def apply_android_entries(self, path: str, entries: list[AndroidEntry], error: Exception | None):
        self.android_tree.delete(*self.android_tree.get_children())

        if error is not None:
            self.android_loading = False
            self.browser_status.set(f"Android側ファイラー: 読込失敗 {error}")
            return

        if path not in {"/", "/sdcard/", "/storage/emulated/0/"}:
            parent = str(PurePosixPath(path.rstrip("/")).parent)
            if not parent.endswith("/"):
                parent += "/"
            self.android_tree.insert("", "end", text="📁 ..", values=(parent, "DIR"))

        for entry in sorted(entries, key=lambda x: (not x.is_dir, x.name.lower())):
            icon = "📁" if entry.is_dir else "📄"
            kind = "DIR" if entry.is_dir else "FILE"
            self.android_tree.insert(
                "",
                "end",
                text=f"{icon} {entry.name}",
                values=(entry.path, kind)
            )

        self.android_loading = False
        self.browser_status.set(f"Android側ファイラー: 読込完了 {path} / {len(entries)}件")

    def on_android_select(self, _event=None):
        item_id = self.android_tree.focus()
        if not item_id:
            return

        values = self.android_tree.item(item_id, "values")
        if len(values) >= 2 and values[1] == "DIR":
            self.remote_dir.set(normalize_remote_dir(values[0]))
            self.generate_command()

    def on_android_double_click(self, _event=None):
        item_id = self.android_tree.focus()
        if not item_id:
            return

        values = self.android_tree.item(item_id, "values")
        if len(values) >= 2 and values[1] == "DIR":
            self.load_android_dir(values[0])

    def open_android_parent(self):
        current = normalize_remote_dir(self.browser_current_dir.get())
        parent = str(PurePosixPath(current.rstrip("/")).parent)
        if not parent.endswith("/"):
            parent += "/"
        self.load_android_dir(parent)

    def use_browser_dir_as_remote(self):
        self.set_remote_dir(self.browser_current_dir.get(), save=False)
        self.command_status.set("保存先ディレクトリを現在の場所に設定しました。")

    def use_selected_android_dir_as_remote(self):
        item_id = self.android_tree.focus()
        if not item_id:
            self.use_browser_dir_as_remote()
            return

        values = self.android_tree.item(item_id, "values")
        if len(values) >= 2 and values[1] == "DIR":
            self.set_remote_dir(values[0], save=False)
            self.command_status.set("保存先ディレクトリを選択フォルダに設定しました。")
        else:
            self.use_browser_dir_as_remote()

    # -----------------------------
    # Command generation
    # -----------------------------
    def generate_command(self):
        self.save_settings()

        script = self.build_powershell_script()

        self.command_text.delete("1.0", "end")
        self.command_text.insert("1.0", script)

        now = datetime.now().strftime("%H:%M:%S")
        self.command_status.set(f"コマンド生成済み {now} / {len(self.local_files)}ファイル")

    def build_powershell_script(self) -> str:
        adb = self.adb_path.get().strip() or "adb"
        serial = self.get_serial_only()
        remote_dir = normalize_remote_dir(self.remote_dir.get())
        stop_on_error = "$true" if self.stop_on_error.get() else "$false"

        lines = [
            "# Generated by ADB Command Builder",
            f"# Version: {APP_VERSION}",
            "",
            "$ErrorActionPreference = \"Continue\"",
            f"$adb = {ps_single_quote(adb)}",
            f"$serial = {ps_single_quote(serial)}",
            f"$stopOnError = {stop_on_error}",
            "",
            "function Invoke-Adb {",
            "    param([string[]]$Arguments)",
            "",
            "    if ([string]::IsNullOrWhiteSpace($serial)) {",
            "        & $adb @Arguments",
            "    } else {",
            "        & $adb -s $serial @Arguments",
            "    }",
            "}",
            "",
            "function Quote-AndroidPath {",
            "    param([string]$Path)",
            "    return \"'\" + ($Path -replace \"'\", \"'\\''\") + \"'\"",
            "}",
            "",
            "$items = @(",
        ]

        if not self.local_files:
            lines += [
                "    # ここに転送対象がありません。",
                "    # GUIでファイルをD&D、または [ファイル追加] から選択してください。",
            ]
        else:
            for idx, local in enumerate(self.local_files, start=1):
                file_name = Path(local).name
                final_remote = remote_join(remote_dir, file_name)

                rename = False
                temp_remote = final_remote

                if self.preserve_filename.get() and not file_name.isascii():
                    temp_name = make_safe_temp_filename(file_name, idx)
                    temp_remote = remote_join(remote_dir, temp_name)
                    rename = True

                rename_text = "$true" if rename else "$false"

                lines += [
                    "    [pscustomobject]@{",
                    f"        Local = {ps_single_quote(local)}",
                    f"        TempRemote = {ps_single_quote(temp_remote)}",
                    f"        FinalRemote = {ps_single_quote(final_remote)}",
                    f"        Rename = {rename_text}",
                    "    }",
                ]

        lines += [
            ")",
            "",
            "foreach ($item in $items) {",
            "    Write-Host \"\"",
            "    Write-Host \"Push: $($item.Local)\"",
            "    Write-Host \"  -> $($item.TempRemote)\"",
            "",
            "    Invoke-Adb @(\"push\", $item.Local, $item.TempRemote)",
            "",
            "    if ($LASTEXITCODE -ne 0) {",
            "        Write-Warning \"Push failed: $($item.Local)\"",
            "        if ($stopOnError) { exit $LASTEXITCODE }",
            "        continue",
            "    }",
            "",
            "    if ($item.Rename) {",
            "        $src = Quote-AndroidPath $item.TempRemote",
            "        $dst = Quote-AndroidPath $item.FinalRemote",
            "        Write-Host \"Rename: $($item.TempRemote) -> $($item.FinalRemote)\"",
            "        Invoke-Adb @(\"shell\", \"mv -f $src $dst\")",
            "",
            "        if ($LASTEXITCODE -ne 0) {",
            "            Write-Warning \"Rename failed. Temporary file remains: $($item.TempRemote)\"",
            "            if ($stopOnError) { exit $LASTEXITCODE }",
            "        }",
            "    }",
            "}",
            "",
            "Write-Host \"\"",
            "Write-Host \"Done.\"",
        ]

        return "\n".join(lines)

    def default_ps1_filename(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if not self.local_files:
            return f"adb_push_{timestamp}.ps1"

        first = Path(self.local_files[0])
        stem = sanitize_windows_filename(first.stem, fallback="adb_push")

        if len(self.local_files) == 1:
            return f"adb_push_{stem}.ps1"

        return f"adb_push_{stem}_and_{len(self.local_files) - 1}_more.ps1"

    def default_ps1_initial_dir(self) -> str:
        if self.local_files:
            parent = Path(self.local_files[0]).parent
            if parent.exists():
                return str(parent)
        return str(Path.home())

    def copy_command(self):
        script = self.command_text.get("1.0", "end").strip()
        if not script:
            return

        self.clipboard_clear()
        self.clipboard_append(script)
        self.update()

        now = datetime.now().strftime("%H:%M:%S")
        self.command_status.set(f"クリップボードへコピー済み {now}")
        messagebox.showinfo("コピー完了", "PowerShellコマンドをクリップボードへコピーしました。")

    def save_ps1(self):
        path = filedialog.asksaveasfilename(
            title="PowerShellスクリプトとして保存",
            initialdir=self.default_ps1_initial_dir(),
            initialfile=self.default_ps1_filename(),
            defaultextension=".ps1",
            filetypes=[
                ("PowerShell script", "*.ps1"),
                ("All files", "*.*"),
            ]
        )

        if not path:
            return

        script = self.command_text.get("1.0", "end").strip() + "\n"
        Path(path).write_text(script, encoding="utf-8-sig")

        now = datetime.now().strftime("%H:%M:%S")
        self.command_status.set(f".ps1保存済み {now}: {Path(path).name}")
        messagebox.showinfo("保存完了", f"保存しました:\n{path}")


if __name__ == "__main__":
    app = AdbCommandBuilder()
    app.mainloop()
