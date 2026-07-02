"""Small Tkinter pagination helper used by record/list panels."""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Iterable, Optional


class PaginationBar:
    def __init__(
        self,
        parent: ttk.Frame,
        *,
        page_size_options: Iterable[int] = (2000, 5000, 10000, 20000),
        default_page_size: int = 2000,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        self.parent = parent
        self.on_change = on_change
        self.page = 1
        self.total = 0
        self.page_size_var = tk.StringVar(value=str(default_page_size))
        self.info_var = tk.StringVar(value="第 1 / 1 页，共 0 条")

        self.frame = ttk.Frame(parent)
        ttk.Button(self.frame, text="首页", command=self.first_page, width=6).pack(side="left", padx=(0, 4))
        ttk.Button(self.frame, text="上一页", command=self.prev_page, width=7).pack(side="left", padx=(0, 4))
        ttk.Button(self.frame, text="下一页", command=self.next_page, width=7).pack(side="left", padx=(0, 4))
        ttk.Button(self.frame, text="末页", command=self.last_page, width=6).pack(side="left", padx=(0, 10))
        ttk.Label(self.frame, text="每页").pack(side="left", padx=(0, 4))
        combo = ttk.Combobox(
            self.frame,
            textvariable=self.page_size_var,
            state="readonly",
            values=[str(x) for x in page_size_options],
            width=6,
        )
        combo.pack(side="left", padx=(0, 8))
        combo.bind("<<ComboboxSelected>>", lambda _event: self.reset_and_notify())
        ttk.Label(self.frame, textvariable=self.info_var, foreground="#666").pack(side="left")

    def grid(self, **kwargs: Any) -> None:
        self.frame.grid(**kwargs)

    def pack(self, **kwargs: Any) -> None:
        self.frame.pack(**kwargs)

    def page_size(self) -> int:
        try:
            return max(1, int(self.page_size_var.get()))
        except ValueError:
            self.page_size_var.set("2000")
            return 2000

    def offset(self) -> int:
        return (max(1, self.page) - 1) * self.page_size()

    def page_count(self) -> int:
        return max(1, math.ceil(max(0, self.total) / self.page_size()))

    def set_total(self, total: int) -> None:
        self.total = max(0, int(total))
        self.page = min(max(1, self.page), self.page_count())
        self.info_var.set(f"第 {self.page} / {self.page_count()} 页，共 {self.total} 条")

    def reset(self) -> None:
        self.page = 1
        self.set_total(self.total)

    def reset_and_notify(self) -> None:
        self.page = 1
        self._notify()

    def first_page(self) -> None:
        if self.page != 1:
            self.page = 1
            self._notify()

    def prev_page(self) -> None:
        if self.page > 1:
            self.page -= 1
            self._notify()

    def next_page(self) -> None:
        if self.page < self.page_count():
            self.page += 1
            self._notify()

    def last_page(self) -> None:
        last = self.page_count()
        if self.page != last:
            self.page = last
            self._notify()

    def _notify(self) -> None:
        if self.on_change:
            self.on_change()
