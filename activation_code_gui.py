import os
import tkinter as tk
from tkinter import messagebox, ttk

from generate_activation_code import (
    generate_activation_code,
    load_activation_secret,
    normalize_machine_code,
)


class ActivationCodeGUI:
    def __init__(self) -> None:
        self.secret = load_activation_secret()
        self.root = tk.Tk()
        self.root.title("D365 激活码生成器")
        self.root.geometry("760x520")
        self.root.resizable(False, False)
        self.machine_code_var = tk.StringVar()
        self.activation_code_var = tk.StringVar()
        self.history_items = []
        self._build()

    def _build(self) -> None:
        wrap = ttk.Frame(self.root, padding=16)
        wrap.pack(fill="both", expand=True)

        ttk.Label(wrap, text="D365 激活码生成器", font=("", 12, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            wrap,
            text="客户付款后，把机器码发给你；你粘贴机器码后点击生成，再把激活码发给客户。",
            foreground="#555",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 14))

        ttk.Label(wrap, text="机器码：").grid(row=2, column=0, sticky="w")
        machine_entry = ttk.Entry(wrap, textvariable=self.machine_code_var, width=64)
        machine_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(0, 10))

        ttk.Label(wrap, text="激活码：").grid(row=3, column=0, sticky="w")
        activation_entry = ttk.Entry(wrap, textvariable=self.activation_code_var, width=64, state="readonly")
        activation_entry.grid(row=3, column=1, columnspan=2, sticky="ew")

        button_row = ttk.Frame(wrap)
        button_row.grid(row=4, column=0, columnspan=3, sticky="w", pady=(16, 8))
        ttk.Button(button_row, text="生成激活码", command=self.on_generate).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(button_row, text="复制激活码", command=self.on_copy_activation).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(button_row, text="清空", command=self.on_clear).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(button_row, text="复制选中记录激活码", command=self.on_copy_selected_history).grid(row=0, column=3)

        ttk.Label(wrap, text="最近发码记录（最多10条）", font=("", 10, "bold")).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(8, 4)
        )
        history_frame = ttk.Frame(wrap)
        history_frame.grid(row=6, column=0, columnspan=3, sticky="nsew")
        history_scroll = ttk.Scrollbar(history_frame, orient="vertical")
        self.history_listbox = tk.Listbox(history_frame, height=10, yscrollcommand=history_scroll.set)
        history_scroll.config(command=self.history_listbox.yview)
        self.history_listbox.grid(row=0, column=0, sticky="nsew")
        history_scroll.grid(row=0, column=1, sticky="ns")
        history_frame.columnconfigure(0, weight=1)
        history_frame.rowconfigure(0, weight=1)

        ttk.Label(
            wrap,
            text="提示：机器码必须是 24 位十六进制字符串；本工具请仅在你自己电脑使用。",
            foreground="#666",
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))

        wrap.columnconfigure(1, weight=1)
        wrap.rowconfigure(6, weight=1)
        machine_entry.focus_set()

    def on_generate(self) -> None:
        if not self.secret:
            messagebox.showerror(
                "配置缺失",
                "activation_secret 未配置，请在 config.json 中设置或配置 D365_ACTIVATION_SECRET 环境变量。",
                parent=self.root,
            )
            return
        raw_machine_code = self.machine_code_var.get().strip()
        try:
            machine_code = normalize_machine_code(raw_machine_code)
        except ValueError as ex:
            messagebox.showerror("输入错误", str(ex), parent=self.root)
            return
        activation_code = generate_activation_code(machine_code, self.secret)
        self.machine_code_var.set(machine_code)
        self.activation_code_var.set(activation_code)
        self._append_history(machine_code, activation_code)

    def on_copy_activation(self) -> None:
        activation_code = self.activation_code_var.get().strip()
        if not activation_code:
            messagebox.showwarning("提示", "请先生成激活码。", parent=self.root)
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(activation_code)
        messagebox.showinfo("已复制", "激活码已复制到剪贴板。", parent=self.root)

    def on_clear(self) -> None:
        self.machine_code_var.set("")
        self.activation_code_var.set("")

    def on_copy_selected_history(self) -> None:
        selected = self.history_listbox.curselection()
        if not selected:
            messagebox.showwarning("提示", "请先选中一条历史记录。", parent=self.root)
            return
        idx = selected[0]
        machine_code, activation_code = self.history_items[idx]
        self.root.clipboard_clear()
        self.root.clipboard_append(activation_code)
        self.machine_code_var.set(machine_code)
        self.activation_code_var.set(activation_code)
        messagebox.showinfo("已复制", "已复制选中记录的激活码。", parent=self.root)

    def _append_history(self, machine_code: str, activation_code: str) -> None:
        item = (machine_code, activation_code)
        self.history_items = [x for x in self.history_items if x[0] != machine_code]
        self.history_items.insert(0, item)
        self.history_items = self.history_items[:10]
        self.history_listbox.delete(0, tk.END)
        for mc, ac in self.history_items:
            self.history_listbox.insert(tk.END, f"{mc}  ->  {ac}")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    ActivationCodeGUI().run()
