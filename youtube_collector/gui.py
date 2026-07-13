from __future__ import annotations

import json
import logging
import queue
import shutil
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .api import YouTubeApiClient
from .collector import CollectOptions, YouTubeCollector
from .crawler import BrowserCrawler


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = application_root()


def _logger() -> logging.Logger:
    (ROOT / "logs").mkdir(exist_ok=True)
    logger = logging.getLogger("youtube_collector")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(
            ROOT / "logs" / f"{datetime.now():%Y-%m-%d}.log", encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YouTube 公开博主信息采集工具")
        self.geometry("920x750")
        self.minsize(760, 600)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self._build()
        self._load_config()
        self.after(100, self._drain_events)

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        self.api_key = tk.StringVar()
        self.collection_mode = tk.StringVar(value="api")
        self.show_browser = tk.BooleanVar(value=False)
        self.email_only = tk.BooleanVar(value=False)
        self.scan_public_websites = tk.BooleanVar(value=True)
        self.keywords = tk.StringVar()
        self.countries = tk.StringVar()
        self.min_subs = tk.StringVar(value="0")
        self.max_subs = tk.StringVar(value="0")
        self.pages = tk.StringVar(value="1")
        default_out = ROOT / "output" / f"youtube_channels_{datetime.now():%Y%m%d_%H%M%S}.csv"
        self.output = tk.StringVar(value=str(default_out))

        row = 0
        ttk.Label(frame, text="采集方式").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=7)
        mode_frame = ttk.Frame(frame)
        mode_frame.grid(row=row, column=1, columnspan=3, sticky="w", pady=7)
        ttk.Radiobutton(
            mode_frame,
            text="官方 API（速度快、需 Key）",
            variable=self.collection_mode,
            value="api",
            command=self._on_mode_change,
        ).pack(side="left")
        ttk.Radiobutton(
            mode_frame,
            text="浏览器爬虫（速度慢、无需 Key）",
            variable=self.collection_mode,
            value="crawler",
            command=self._on_mode_change,
        ).pack(side="left", padx=(16, 0))
        row += 1

        ttk.Label(frame, text="YouTube API Key").grid(
            row=row, column=0, sticky="w", padx=(0, 12), pady=7
        )
        self.api_key_entry = ttk.Entry(frame, textvariable=self.api_key, show="*")
        self.api_key_entry.grid(row=row, column=1, columnspan=3, sticky="ew", pady=7)
        row += 1

        fields = [
            ("搜索关键词（用 | 分隔）", self.keywords, False),
            ("国家/地区（中文、英文或代码，用 | 分隔）", self.countries, False),
        ]
        for label, variable, secret in fields:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=7)
            ttk.Entry(frame, textvariable=variable, show="*" if secret else "").grid(
                row=row, column=1, columnspan=3, sticky="ew", pady=7
            )
            row += 1

        ttk.Label(frame, text="粉丝数范围").grid(row=row, column=0, sticky="w", pady=7)
        ttk.Entry(frame, textvariable=self.min_subs, width=14).grid(row=row, column=1, sticky="w")
        ttk.Label(frame, text="至").grid(row=row, column=2, padx=8)
        ttk.Entry(frame, textvariable=self.max_subs, width=14).grid(row=row, column=3, sticky="w")
        row += 1

        ttk.Label(frame, text="每个关键词采集页数").grid(row=row, column=0, sticky="w", pady=7)
        ttk.Entry(frame, textvariable=self.pages, width=14).grid(row=row, column=1, sticky="w")
        ttk.Label(frame, text="每页最多 50 个视频；-1 表示直到无下一页").grid(row=row, column=2, columnspan=2, sticky="w")
        row += 1

        ttk.Label(frame, text="爬虫设置").grid(row=row, column=0, sticky="w", pady=7)
        self.show_browser_check = ttk.Checkbutton(
            frame, text="显示浏览器窗口（便于观察采集过程）", variable=self.show_browser
        )
        self.show_browser_check.grid(row=row, column=1, columnspan=3, sticky="w")
        row += 1

        ttk.Label(frame, text="邮箱采集").grid(row=row, column=0, sticky="w", pady=7)
        email_frame = ttk.Frame(frame)
        email_frame.grid(row=row, column=1, columnspan=3, sticky="w")
        self.email_only_check = ttk.Checkbutton(
            email_frame, text="只保存有邮箱或需人工验证的频道", variable=self.email_only
        )
        self.email_only_check.pack(side="left")
        self.scan_websites_check = ttk.Checkbutton(
            email_frame,
            text="简介无邮箱时检查频道公开官网",
            variable=self.scan_public_websites,
        )
        self.scan_websites_check.pack(side="left", padx=(16, 0))
        row += 1

        ttk.Label(frame, text="CSV 输出").grid(row=row, column=0, sticky="w", pady=7)
        ttk.Entry(frame, textvariable=self.output).grid(row=row, column=1, columnspan=2, sticky="ew")
        ttk.Button(frame, text="选择…", command=self._choose_output).grid(row=row, column=3, padx=(8, 0))
        row += 1

        buttons = ttk.Frame(frame)
        buttons.grid(row=row, column=0, columnspan=4, sticky="w", pady=(12, 8))
        self.start_button = ttk.Button(buttons, text="开始采集", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="停止", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="保存配置", command=self._save_config).pack(side="left")
        row += 1

        ttk.Label(frame, text="运行日志").grid(row=row, column=0, columnspan=4, sticky="w")
        row += 1
        self.log = tk.Text(frame, height=18, wrap="word", state="disabled")
        self.log.grid(row=row, column=0, columnspan=4, sticky="nsew", pady=(6, 0))
        frame.rowconfigure(row, weight=1)
        self._on_mode_change()

    def _choose_output(self) -> None:
        selected = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV 文件", "*.csv")]
        )
        if selected:
            self.output.set(selected)

    def _config(self) -> dict:
        return {
            "youtube_api_key": self.api_key.get().strip(),
            "collection_mode": self.collection_mode.get(),
            "show_browser": self.show_browser.get(),
            "email_only": self.email_only.get(),
            "scan_public_websites": self.scan_public_websites.get(),
            "request_interval_seconds": 0.2,
            "request_timeout_seconds": 30,
        }

    def _save_config(self, quiet: bool = False) -> None:
        (ROOT / "config.json").write_text(
            json.dumps(self._config(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if not quiet:
            messagebox.showinfo("已保存", "配置已保存到本机 config.json，请勿分享该文件。")

    def _load_config(self) -> None:
        path = ROOT / "config.json"
        if not path.exists() and (ROOT / "config.example.json").exists():
            shutil.copyfile(ROOT / "config.example.json", path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                key = data.get("youtube_api_key", "")
                if not key.startswith("把你的"):
                    self.api_key.set(key)
                self.collection_mode.set(data.get("collection_mode", "api"))
                self.show_browser.set(bool(data.get("show_browser", False)))
                self.email_only.set(bool(data.get("email_only", False)))
                self.scan_public_websites.set(bool(data.get("scan_public_websites", True)))
                self._on_mode_change()
            except (OSError, ValueError):
                pass

    def _options(self) -> CollectOptions:
        keywords = [x.strip() for x in self.keywords.get().split("|") if x.strip()]
        if not keywords:
            raise ValueError("请至少填写一个搜索关键词。")
        if self.collection_mode.get() == "api" and not self.api_key.get().strip():
            raise ValueError("请填写 YouTube Data API v3 Key。")
        minimum, maximum, pages = int(self.min_subs.get()), int(self.max_subs.get()), int(self.pages.get())
        if min(minimum, maximum) < 0 or pages == 0 or pages < -1:
            raise ValueError("粉丝数不能为负；页数应为正整数或 -1。")
        if maximum and minimum > maximum:
            raise ValueError("粉丝数下限不能大于上限。")
        countries = {x.strip().upper() for x in self.countries.get().split("|") if x.strip()}
        return CollectOptions(
            keywords,
            countries,
            minimum,
            maximum,
            pages,
            Path(self.output.get()),
            email_only=self.email_only.get(),
            scan_public_websites=self.scan_public_websites.get(),
        )

    def _start(self) -> None:
        try:
            options = self._options()
        except (ValueError, OSError) as exc:
            messagebox.showerror("参数有误", str(exc))
            return
        self._save_config(quiet=True)
        self.stop_event.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        mode_text = "官方 API" if self.collection_mode.get() == "api" else "浏览器爬虫"
        self._append(f"开始采集，当前方式：{mode_text}。")
        config = self._config()

        def work() -> None:
            try:
                status = lambda text: self.events.put(("log", text))
                if config["collection_mode"] == "crawler":
                    collector = BrowserCrawler(
                        _logger(),
                        status,
                        show_browser=config["show_browser"],
                        timeout_seconds=config["request_timeout_seconds"],
                    )
                else:
                    api = YouTubeApiClient(
                        config["youtube_api_key"],
                        config["request_timeout_seconds"],
                        config["request_interval_seconds"],
                    )
                    collector = YouTubeCollector(api, _logger(), status)
                count = collector.run(options, self.stop_event)
                self.events.put(("done", count))
            except Exception as exc:
                _logger().exception("采集失败")
                self.events.put(("error", str(exc)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        self.stop_event.set()
        self._append("正在停止；当前请求完成后退出…")
        self.stop_button.configure(state="disabled")

    def _on_mode_change(self) -> None:
        crawler = self.collection_mode.get() == "crawler"
        self.api_key_entry.configure(state="disabled" if crawler else "normal")
        self.show_browser_check.configure(state="normal" if crawler else "disabled")
        self.scan_websites_check.configure(state="normal" if crawler else "disabled")

    def _drain_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self._append(str(value))
                elif kind == "done":
                    self._finish()
                    messagebox.showinfo("完成", f"本次新增 {value} 条记录。")
                elif kind == "error":
                    self._finish()
                    messagebox.showerror("采集失败", str(value))
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"[{datetime.now():%H:%M:%S}] {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _finish(self) -> None:
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")


def run() -> None:
    App().mainloop()
