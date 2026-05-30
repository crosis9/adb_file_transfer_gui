import os
import re
import string
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog


APP_VERSION = "0.2.2"
APP_TITLE = f"ADB File Transfer GUI v{APP_VERSION} | kuroha"

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


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


def quote_shell_path(path: str) -> str:
    """
    Android shell向けのシンプルなシングルクォートエスケープ。
    """
    return "'" + path.replace("'", "'\"'\"'") + "'"


def normalize_android_dir(path: str) -> str:
    path = path.strip() or "/sdcard/"
    if not path.startswith("/"):
        path = "/" + path
    if not path.endswith("/"):
        path += "/"
    return path


def format_bytes(size: int | None) -> str:
    if size is None or size < 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def format_datetime_from_timestamp(timestamp: float | int | None) -> str:
    if timestamp is None:
        return ""
    try:
        return datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


@dataclass
class AndroidItem:
    name: str
    path: str
    is_dir: bool
    size: int | None = None
    modified: str = ""


class AdbFileTransferGui(tk.Tk):
    PROGRESS_RE = re.compile(r"(?<!\d)(\d{1,3})\s*%")

    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1280x760")
        self.minsize(1080, 660)

        self.adb_path = tk.StringVar(value=default_adb_path())
        self.selected_device = tk.StringVar()

        self.local_current_path = tk.StringVar(value=str(Path.home()))
        self.local_selected_path = tk.StringVar(value="")

        self.android_current_path = tk.StringVar(value="/sdcard/")
        self.android_selected_path = tk.StringVar(value="")
        self.selected_storage = tk.StringVar()
        self.android_roots: dict[str, str] = {}

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text = tk.StringVar(value="待機中")
        self.progress_mode_known = False
        self.running = False
        self.android_loading = False

        self._build_ui()
        self.refresh_devices()
        self.load_local_dir(Path(self.local_current_path.get()))

    # -----------------------------
    # UI
    # -----------------------------
    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        top = ttk.LabelFrame(root, text="ADB / Device", padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="ADB:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.adb_path).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(top, text="adb.exe参照", command=self.browse_adb).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(top, text="端末再検出", command=self.refresh_devices).grid(row=0, column=3)

        ttk.Label(top, text="Device:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.device_combo = ttk.Combobox(top, textvariable=self.selected_device, state="readonly")
        self.device_combo.grid(row=1, column=1, sticky="ew", padx=8, pady=(8, 0))
        self.device_combo.bind("<<ComboboxSelected>>", lambda _e: self.on_device_changed())

        ttk.Label(top, text="Storage:").grid(row=1, column=2, sticky="e", pady=(8, 0))
        self.storage_combo = ttk.Combobox(top, textvariable=self.selected_storage, state="readonly")
        self.storage_combo.grid(row=1, column=3, sticky="ew", pady=(8, 0))
        self.storage_combo.bind("<<ComboboxSelected>>", lambda _e: self.on_storage_changed())

        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)

        # Path bar
        path_frame = ttk.Frame(root)
        path_frame.pack(fill="x", pady=(10, 0))

        local_path_frame = ttk.LabelFrame(path_frame, text="Windows", padding=8)
        local_path_frame.pack(side="left", fill="x", expand=True, padx=(0, 5))

        ttk.Label(local_path_frame, text="現在:").grid(row=0, column=0, sticky="w")
        local_entry = ttk.Entry(local_path_frame, textvariable=self.local_current_path)
        local_entry.grid(row=0, column=1, sticky="ew", padx=6)
        local_entry.bind("<Return>", lambda _e: self.load_local_dir(Path(self.local_current_path.get())))
        ttk.Button(local_path_frame, text="移動", command=lambda: self.load_local_dir(Path(self.local_current_path.get()))).grid(row=0, column=2)
        ttk.Button(local_path_frame, text="デスクトップ", command=self.goto_desktop).grid(row=0, column=3, padx=(6, 0))
        ttk.Button(local_path_frame, text="ダウンロード", command=self.goto_downloads).grid(row=0, column=4, padx=(6, 0))

        ttk.Label(local_path_frame, text="選択:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(local_path_frame, textvariable=self.local_selected_path, state="readonly").grid(row=1, column=1, columnspan=4, sticky="ew", padx=6, pady=(6, 0))
        local_path_frame.columnconfigure(1, weight=1)

        android_path_frame = ttk.LabelFrame(path_frame, text="Android", padding=8)
        android_path_frame.pack(side="right", fill="x", expand=True, padx=(5, 0))

        ttk.Label(android_path_frame, text="現在:").grid(row=0, column=0, sticky="w")
        android_entry = ttk.Entry(android_path_frame, textvariable=self.android_current_path)
        android_entry.grid(row=0, column=1, sticky="ew", padx=6)
        android_entry.bind("<Return>", lambda _e: self.load_android_dir(self.android_current_path.get()))
        ttk.Button(android_path_frame, text="移動", command=lambda: self.load_android_dir(self.android_current_path.get())).grid(row=0, column=2)

        ttk.Label(android_path_frame, text="選択:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(android_path_frame, textvariable=self.android_selected_path, state="readonly").grid(row=1, column=1, columnspan=2, sticky="ew", padx=6, pady=(6, 0))
        android_path_frame.columnconfigure(1, weight=1)

        # Explorer panes
        panes = ttk.PanedWindow(root, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=(10, 0))

        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=1)

        self.local_tree = self._create_tree(left)
        self.android_tree = self._create_tree(right)

        self.local_tree.bind("<<TreeviewSelect>>", self.on_local_select)
        self.local_tree.bind("<Double-1>", self.on_local_double_click)

        self.android_tree.bind("<<TreeviewSelect>>", self.on_android_select)
        self.android_tree.bind("<Double-1>", self.on_android_double_click)

        # Transfer controls
        controls = ttk.LabelFrame(root, text="Transfer / 操作", padding=10)
        controls.pack(fill="x", pady=(10, 0))

        ttk.Button(controls, text="→ Push  WindowsからAndroidへ", command=self.push_selected).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="← Pull  AndroidからWindowsへ", command=self.pull_selected).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Windows側に新しいフォルダ", command=self.create_local_folder).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Android側に新しいフォルダ", command=self.create_android_folder).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Android側更新", command=lambda: self.load_android_dir(self.android_current_path.get())).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Windows側更新", command=lambda: self.load_local_dir(Path(self.local_current_path.get()))).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="ADB再起動", command=self.restart_adb).pack(side="right")

        # Progress
        progress = ttk.LabelFrame(root, text="転送進捗", padding=10)
        progress.pack(fill="x", pady=(10, 0))

        self.progress_bar = ttk.Progressbar(progress, variable=self.progress_var, maximum=100, mode="determinate")
        self.progress_bar.pack(fill="x")
        ttk.Label(progress, textvariable=self.progress_text).pack(anchor="w", pady=(6, 0))

        # Log
        log_frame = ttk.LabelFrame(root, text="ログ", padding=10)
        log_frame.pack(fill="both", expand=False, pady=(10, 0))

        self.log = tk.Text(log_frame, height=9, wrap="word")
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)

    def _create_tree(self, parent: ttk.Frame) -> ttk.Treeview:
        tree = ttk.Treeview(
            parent,
            columns=("path", "kind", "modified", "size"),
            show="tree headings",
            selectmode="browse"
        )
        tree.heading("#0", text="名前")
        tree.heading("path", text="パス")
        tree.heading("kind", text="種別")
        tree.heading("modified", text="更新日時")
        tree.heading("size", text="容量")
        tree.column("#0", width=220, stretch=True)
        tree.column("path", width=360, stretch=True)
        tree.column("kind", width=70, stretch=False)
        tree.column("modified", width=150, stretch=False)
        tree.column("size", width=95, stretch=False, anchor="e")

        yscroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        xscroll = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        return tree

    def log_write(self, text: str):
        self.log.insert("end", text)
        self.log.see("end")

    # -----------------------------
    # ADB helpers
    # -----------------------------
    def browse_adb(self):
        path = filedialog.askopenfilename(
            title="adb.exeを選択",
            filetypes=[("adb.exe", "adb.exe"), ("Executable", "*.exe"), ("All files", "*.*")]
        )
        if path:
            self.adb_path.set(path)

    def selected_serial(self) -> str | None:
        value = self.selected_device.get().strip()
        if not value:
            return None
        return value.split()[0]

    def adb_cmd(self, args: list[str], require_device: bool = True) -> list[str]:
        base = [self.adb_path.get()]
        serial = self.selected_serial()
        if require_device and serial:
            base += ["-s", serial]
        return base + args

    def run_capture(self, args: list[str], require_device: bool = True, timeout: int | None = 20) -> str:
        cmd = self.adb_cmd(args, require_device=require_device)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=timeout
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "ADB command failed").strip())
        return result.stdout

    def refresh_devices(self):
        try:
            out = self.run_capture(["devices", "-l"], require_device=False)
        except FileNotFoundError:
            messagebox.showerror("ADBエラー", "adb.exe が見つかりません。platform-toolsを配置するか、adb.exeを参照してください。")
            return
        except Exception as e:
            messagebox.showerror("ADBエラー", str(e))
            return

        devices = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                serial = parts[0]
                status = parts[1]
                details = " ".join(parts[2:])
                devices.append(f"{serial} {status} {details}".strip())

        self.device_combo["values"] = devices

        if devices:
            self.selected_device.set(devices[0])
            self.log_write("[INFO] 端末を検出しました。\n")
            self.on_device_changed()
        else:
            self.selected_device.set("")
            self.storage_combo["values"] = []
            self.selected_storage.set("")
            self.log_write("[WARN] 接続端末が見つかりません。USBデバッグ許可を確認してください。\n")

    def on_device_changed(self):
        self.refresh_android_roots()

    def restart_adb(self):
        if self.running:
            messagebox.showwarning("実行中", "転送中はADBを再起動できません。")
            return
        try:
            self.log_write("\n[INFO] adb kill-server\n")
            self.run_capture(["kill-server"], require_device=False)
            self.log_write("[INFO] adb start-server\n")
            self.run_capture(["start-server"], require_device=False)
            self.refresh_devices()
        except Exception as e:
            messagebox.showerror("ADBエラー", str(e))

    # -----------------------------
    # Storage roots
    # -----------------------------
    def refresh_android_roots(self):
        serial = self.selected_serial()
        if not serial:
            return

        roots: list[tuple[str, str]] = [
            ("内部ストレージ /sdcard", "/sdcard/"),
            ("内部ストレージ /storage/emulated/0", "/storage/emulated/0/"),
        ]

        try:
            out = self.run_capture(["shell", "ls", "-1", "/storage"])
            for name in out.splitlines():
                name = name.strip()
                if not name or name in {"emulated", "self"}:
                    continue

                remote_path = f"/storage/{name}/"
                # 代表的なmicroSDは 1234-5678 のようなUUID形式。
                roots.append((f"外部ストレージ候補 {remote_path}", remote_path))

        except Exception as e:
            self.log_write(f"[WARN] 外部ストレージ検出失敗: {e}\n")

        self.android_roots = dict(roots)
        labels = list(self.android_roots.keys())
        self.storage_combo["values"] = labels

        if labels:
            self.selected_storage.set(labels[0])
            self.android_current_path.set(self.android_roots[labels[0]])
            self.load_android_dir(self.android_roots[labels[0]])

    def on_storage_changed(self):
        label = self.selected_storage.get()
        path = self.android_roots.get(label)
        if path:
            self.load_android_dir(path)

    # -----------------------------
    # Windows explorer
    # -----------------------------
    def load_local_dir(self, path: Path):
        try:
            path = path.expanduser().resolve()
        except Exception:
            messagebox.showwarning("パスエラー", "Windows側パスが不正です。")
            return

        if not path.exists() or not path.is_dir():
            messagebox.showwarning("パスエラー", "Windows側のフォルダが存在しません。")
            return

        self.local_current_path.set(str(path))
        self.local_selected_path.set("")
        self.local_tree.delete(*self.local_tree.get_children())

        parent = path.parent
        if parent != path:
            try:
                stat = parent.stat()
                modified = format_datetime_from_timestamp(stat.st_mtime)
            except Exception:
                modified = ""
            self.local_tree.insert("", "end", text="📁 ..", values=(str(parent), "DIR", modified, ""))

        # ドライブ一覧をルートに近いところで出す
        if os.name == "nt":
            for drive in self.list_windows_drives():
                if Path(drive) != path:
                    self.local_tree.insert("", "end", text=f"💽 {drive}", values=(drive, "DIR", "", ""))

        try:
            items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            messagebox.showwarning("権限エラー", "このフォルダを開けません。")
            return
        except Exception as e:
            messagebox.showerror("エラー", str(e))
            return

        for item in items:
            try:
                stat = item.stat()
                modified = format_datetime_from_timestamp(stat.st_mtime)
                size = "" if item.is_dir() else format_bytes(stat.st_size)
            except Exception:
                modified = ""
                size = ""

            icon = "📁" if item.is_dir() else "📄"
            kind = "DIR" if item.is_dir() else "FILE"
            self.local_tree.insert("", "end", text=f"{icon} {item.name}", values=(str(item), kind, modified, size))

    def list_windows_drives(self) -> list[str]:
        drives = []
        bitmask = 0
        if os.name == "nt":
            try:
                import ctypes
                bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            except Exception:
                bitmask = 0

        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drives.append(f"{letter}:\\")
            bitmask >>= 1
        return drives

    def goto_desktop(self):
        path = Path.home() / "Desktop"
        if not path.exists():
            messagebox.showwarning("パスエラー", "デスクトップフォルダが見つかりません。")
            return
        self.load_local_dir(path)

    def goto_downloads(self):
        path = Path.home() / "Downloads"
        if not path.exists():
            messagebox.showwarning("パスエラー", "ダウンロードフォルダが見つかりません。")
            return
        self.load_local_dir(path)

    def create_local_folder(self):
        base_path = Path(self.local_current_path.get())
        if not base_path.exists() or not base_path.is_dir():
            messagebox.showwarning("パスエラー", "Windows側の現在フォルダが不正です。")
            return

        name = simpledialog.askstring("新しいフォルダ", "Windows側に作成するフォルダ名:")
        if not name:
            return

        name = name.strip()
        if not name:
            return

        invalid_chars = '<>:"/\\|?*'
        if any(ch in name for ch in invalid_chars):
            messagebox.showwarning("名前エラー", f"Windowsで使用できない文字が含まれています: {invalid_chars}")
            return

        new_path = base_path / name
        try:
            new_path.mkdir(exist_ok=False)
            self.log_write(f"[INFO] Windows側フォルダ作成: {new_path}\n")
            self.load_local_dir(base_path)
        except FileExistsError:
            messagebox.showwarning("作成失敗", "同名のフォルダまたはファイルが既に存在します。")
        except Exception as e:
            messagebox.showerror("作成失敗", str(e))

    def on_local_select(self, _event=None):
        item_id = self.local_tree.focus()
        if not item_id:
            return
        values = self.local_tree.item(item_id, "values")
        if values:
            self.local_selected_path.set(values[0])

    def on_local_double_click(self, _event=None):
        item_id = self.local_tree.focus()
        if not item_id:
            return
        values = self.local_tree.item(item_id, "values")
        if len(values) >= 2 and values[1] == "DIR":
            self.load_local_dir(Path(values[0]))

    # -----------------------------
    # Android explorer
    # -----------------------------
    def android_stat_info(self, path: str) -> tuple[int | None, str]:
        qpath = quote_shell_path(path)
        try:
            out = self.run_capture(["shell", f"stat -c '%s|%Y' {qpath}"], timeout=3).strip()
            if "|" not in out:
                return None, ""
            size_text, mtime_text = out.split("|", 1)
            size = int(size_text.strip())
            modified = format_datetime_from_timestamp(float(mtime_text.strip()))
            return size, modified
        except Exception:
            return None, ""

    def list_android_dir(self, path: str) -> list[AndroidItem]:
        path = normalize_android_dir(path)
        qpath = quote_shell_path(path)

        # toybox/statの有無やOEM差を避けるため、まずlsで名前だけ取り、各項目をtest -dで判定。
        out = self.run_capture(["shell", f"ls -1 {qpath}"], timeout=20)
        items: list[AndroidItem] = []

        for raw_name in out.splitlines():
            name = raw_name.strip()
            if not name or name in {".", ".."}:
                continue

            full_path = path + name
            qfull = quote_shell_path(full_path)
            try:
                kind = self.run_capture(["shell", f"[ -d {qfull} ] && echo DIR || echo FILE"], timeout=3).strip()
            except Exception:
                kind = "FILE"

            is_dir = kind == "DIR"
            size, modified = self.android_stat_info(full_path)
            items.append(AndroidItem(
                name=name,
                path=full_path,
                is_dir=is_dir,
                size=None if is_dir else size,
                modified=modified
            ))

        return items

    def load_android_dir(self, path: str):
        """
        Android側ディレクトリの読み込み。
        microSDや大量ファイルのフォルダでGUIが固まらないよう、実処理は別スレッドで実行する。
        """
        serial = self.selected_serial()
        if not serial:
            self.log_write("[WARN] 端末が選択されていません。\n")
            return

        if self.android_loading:
            self.log_write("[INFO] Android側ディレクトリを読み込み中です。\n")
            return

        path = normalize_android_dir(path)
        self.android_loading = True
        self.android_current_path.set(path)
        self.android_selected_path.set("")
        self.android_tree.delete(*self.android_tree.get_children())
        self.android_tree.insert("", "end", text="読み込み中...", values=(path, "", "", ""))
        self.log_write(f"[INFO] Android側ディレクトリ読込開始: {path}\n")

        def worker():
            try:
                items = self.list_android_dir(path)
                self.after(0, self.apply_android_dir_items, path, items, None)
            except Exception as e:
                self.after(0, self.apply_android_dir_items, path, [], e)

        threading.Thread(target=worker, daemon=True).start()

    def apply_android_dir_items(self, path: str, items: list[AndroidItem], error: Exception | None):
        self.android_tree.delete(*self.android_tree.get_children())

        if error is not None:
            self.android_loading = False
            self.log_write(f"[ERROR] Android側ディレクトリ取得失敗: {error}\n")
            return

        if path not in {"/", "/sdcard/", "/storage/emulated/0/"}:
            parent = str(PurePosixPath(path.rstrip("/")).parent)
            if not parent.endswith("/"):
                parent += "/"
            self.android_tree.insert("", "end", text="📁 ..", values=(parent, "DIR", "", ""))

        for item in sorted(items, key=lambda x: (not x.is_dir, x.name.lower())):
            icon = "📁" if item.is_dir else "📄"
            kind = "DIR" if item.is_dir else "FILE"
            display_path = item.path + "/" if item.is_dir and not item.path.endswith("/") else item.path
            size = "" if item.is_dir else format_bytes(item.size)
            self.android_tree.insert(
                "",
                "end",
                text=f"{icon} {item.name}",
                values=(display_path, kind, item.modified, size)
            )

        self.android_loading = False
        self.log_write(f"[INFO] Android側ディレクトリ読込完了: {path} ({len(items)} items)\n")

    def on_android_select(self, _event=None):
        item_id = self.android_tree.focus()
        if not item_id:
            return
        values = self.android_tree.item(item_id, "values")
        if values:
            self.android_selected_path.set(values[0])

    def on_android_double_click(self, _event=None):
        item_id = self.android_tree.focus()
        if not item_id:
            return
        values = self.android_tree.item(item_id, "values")
        if len(values) >= 2 and values[1] == "DIR":
            self.load_android_dir(values[0])

    def create_android_folder(self):
        serial = self.selected_serial()
        if not serial:
            messagebox.showwarning("未選択", "端末を選択してください。")
            return

        base_path = normalize_android_dir(self.android_current_path.get())
        name = simpledialog.askstring("新しいフォルダ", "Android側に作成するフォルダ名:")
        if not name:
            return

        name = name.strip().strip("/")
        if not name:
            return

        if "/" in name:
            messagebox.showwarning("名前エラー", "フォルダ名に / は使用できません。")
            return

        new_path = base_path + name
        qpath = quote_shell_path(new_path)

        try:
            self.run_capture(["shell", f"mkdir {qpath}"])
            self.log_write(f"[INFO] Android側フォルダ作成: {new_path}\n")
            self.load_android_dir(base_path)
        except Exception as e:
            messagebox.showerror("作成失敗", str(e))

    # -----------------------------
    # Progress
    # -----------------------------
    def reset_progress(self):
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_var.set(0)
        self.progress_text.set("待機中")
        self.progress_mode_known = False

    def start_unknown_progress(self):
        self.progress_mode_known = False
        self.progress_var.set(0)
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start(10)
        self.progress_text.set("転送中... 進捗率取得中")

    def set_progress_percent(self, percent: int):
        percent = max(0, min(100, percent))

        if not self.progress_mode_known:
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")
            self.progress_mode_known = True

        self.progress_var.set(percent)
        self.progress_text.set(f"{percent}% 転送中...")

    def complete_progress(self):
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_var.set(100)
        self.progress_text.set("100% 完了")

    def fail_progress(self):
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_text.set("失敗")

    def parse_progress_from_text(self, text: str) -> int | None:
        matches = self.PROGRESS_RE.findall(text)
        if not matches:
            return None

        try:
            percent = int(matches[-1])
        except ValueError:
            return None

        if 0 <= percent <= 100:
            return percent
        return None

    # -----------------------------
    # Transfer
    # -----------------------------
    def selected_android_destination_dir(self) -> str:
        selected = self.android_selected_path.get().strip()
        item_id = self.android_tree.focus()

        if item_id:
            values = self.android_tree.item(item_id, "values")
            if len(values) >= 2 and values[1] == "DIR":
                return normalize_android_dir(values[0])

        return normalize_android_dir(self.android_current_path.get())

    def selected_local_destination_dir(self) -> str:
        selected = self.local_selected_path.get().strip()
        if selected and Path(selected).exists() and Path(selected).is_dir():
            return selected
        return self.local_current_path.get().strip()

    def push_selected(self):
        if self.running:
            messagebox.showwarning("実行中", "転送中です。")
            return

        serial = self.selected_serial()
        local = self.local_selected_path.get().strip()
        remote_dir = self.selected_android_destination_dir()

        if not serial:
            messagebox.showwarning("未選択", "端末を選択してください。")
            return

        if not local or not Path(local).exists():
            messagebox.showwarning("未選択", "Windows側のコピー元ファイル/フォルダを選択してください。")
            return

        cmd = self.adb_cmd(["push", local, remote_dir])
        self.run_adb_async(cmd, on_success=lambda: self.load_android_dir(self.android_current_path.get()))

    def pull_selected(self):
        if self.running:
            messagebox.showwarning("実行中", "転送中です。")
            return

        serial = self.selected_serial()
        remote = self.android_selected_path.get().strip()
        local_dir = self.selected_local_destination_dir()

        if not serial:
            messagebox.showwarning("未選択", "端末を選択してください。")
            return

        if not remote:
            messagebox.showwarning("未選択", "Android側のコピー元ファイル/フォルダを選択してください。")
            return

        if not local_dir or not Path(local_dir).exists() or not Path(local_dir).is_dir():
            messagebox.showwarning("未選択", "Windows側のコピー先フォルダを選択してください。")
            return

        cmd = self.adb_cmd(["pull", remote, local_dir])
        self.run_adb_async(cmd, on_success=lambda: self.load_local_dir(Path(self.local_current_path.get())))

    def run_adb_async(self, cmd: list[str], on_success=None):
        if self.running:
            messagebox.showwarning("実行中", "ADBコマンドを実行中です。")
            return

        self.running = True
        self.reset_progress()
        self.start_unknown_progress()

        display_cmd = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        self.log_write("\n$ " + display_cmd + "\n")

        def worker():
            last_percent = None
            buffer = ""

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                    creationflags=CREATE_NO_WINDOW
                )

                assert proc.stdout is not None

                while True:
                    chunk = proc.stdout.read(1)
                    if not chunk:
                        break

                    char = chunk.decode("utf-8", errors="replace")
                    buffer += char

                    if char in ("\r", "\n"):
                        text = buffer.strip()
                        if text:
                            percent = self.parse_progress_from_text(text)
                            if percent is not None and percent != last_percent:
                                last_percent = percent
                                self.after(0, self.set_progress_percent, percent)
                            self.after(0, self.log_write, text + "\n")
                        buffer = ""
                    else:
                        percent = self.parse_progress_from_text(buffer[-160:])
                        if percent is not None and percent != last_percent:
                            last_percent = percent
                            self.after(0, self.set_progress_percent, percent)

                if buffer.strip():
                    text = buffer.strip()
                    percent = self.parse_progress_from_text(text)
                    if percent is not None:
                        self.after(0, self.set_progress_percent, percent)
                    self.after(0, self.log_write, text + "\n")

                code = proc.wait()
                self.after(0, self.log_write, f"\n[EXIT CODE] {code}\n")

                if code == 0:
                    self.after(0, self.complete_progress)
                    if on_success:
                        self.after(0, on_success)
                    self.after(0, messagebox.showinfo, "完了", "ADB転送が完了しました。")
                else:
                    self.after(0, self.fail_progress)
                    self.after(0, messagebox.showerror, "エラー", f"ADBコマンドが失敗しました。終了コード: {code}")

            except Exception as e:
                self.after(0, self.fail_progress)
                self.after(0, self.log_write, f"\n[ERROR] {e}\n")
                self.after(0, messagebox.showerror, "エラー", str(e))
            finally:
                self.running = False

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    app = AdbFileTransferGui()
    app.mainloop()
