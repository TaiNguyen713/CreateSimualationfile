"""
ui_app.py — Tkinter desktop UI for the Sim File Generator.

Run with:
    python ui_app.py
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pandas as pd

# ── Patch schema-mismatch prompt BEFORE any DataLoader is created ─────────────
# db_schema._prompt_skip normally calls input() — replace it with a GUI dialog.
# The background thread puts a request on _PROMPT_Q and blocks; the main thread
# reads the queue, shows a dialog, and unblocks the thread with the answer.
import src.db_schema as _db

_PROMPT_Q: queue.Queue = queue.Queue()


def _gui_prompt_skip(file: Path, reason: str) -> None:
    """Called from background thread — hands off to main-thread dialog."""
    event: threading.Event = threading.Event()
    answer: list[bool] = [True]
    _PROMPT_Q.put((str(file), reason, event, answer))
    event.wait()
    if not answer[0]:
        sys.exit(1)   # caught by _run() as SystemExit


_db._prompt_skip = _gui_prompt_skip  # type: ignore[attr-defined]

# ── Register all protocols (after patch, before UI uses the factory) ──────────
import makes.hyundai.protocols.can_uds  # noqa: F401
from core.protocol_factory import get_protocol, list_protocols


# ── Queue-based log handler ───────────────────────────────────────────────────

class _QueueHandler(logging.Handler):
    def __init__(self, q: queue.Queue) -> None:
        super().__init__()
        self._q = q

    def emit(self, record: logging.LogRecord) -> None:
        self._q.put(self.format(record))


# ── App ───────────────────────────────────────────────────────────────────────

class SimGeneratorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title('Sim File Generator')
        self.minsize(980, 640)
        self._center(980, 680)

        self._log_q: queue.Queue[str] = queue.Queue()
        self._running = False

        self._setup_logging()
        self._build_ui()
        self._poll_logs()
        self._poll_prompts()

        # Auto-load vehicles if default config exists
        if Path(self._config_var.get()).exists():
            self._load_vehicles()

    # ── Logging setup ─────────────────────────────────────────────────────────

    def _setup_logging(self) -> None:
        handler = _QueueHandler(self._log_q)
        handler.setFormatter(logging.Formatter(
            '%(asctime)s  %(levelname)-8s  %(message)s', datefmt='%H:%M:%S',
        ))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.handlers.clear()
        root.addHandler(handler)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._build_left()
        self._build_right()

    def _build_left(self) -> None:
        outer = ttk.Frame(self, padding=10)
        outer.grid(row=0, column=0, sticky='ns')
        outer.columnconfigure(0, weight=1)

        r = 0

        # ── Config file ───────────────────────────────────────────────────────
        ttk.Label(outer, text='Config File', font=('', 9, 'bold')).grid(
            row=r, column=0, columnspan=2, sticky='w')
        r += 1
        self._config_var = tk.StringVar(value='config/Vehicle_infor.xlsx')
        ttk.Entry(outer, textvariable=self._config_var, width=34).grid(
            row=r, column=0, sticky='ew', padx=(0, 4))
        ttk.Button(outer, text='…', width=3, command=self._browse_config).grid(
            row=r, column=1)
        r += 1

        ttk.Separator(outer).grid(row=r, column=0, columnspan=2, sticky='ew', pady=8)
        r += 1

        # ── DB folders ────────────────────────────────────────────────────────
        ttk.Label(outer, text='DB Folders', font=('', 9, 'bold')).grid(
            row=r, column=0, columnspan=2, sticky='w')
        r += 1

        self._db_vars: dict[str, tk.StringVar] = {}
        for key, ds in _db.DB_REGISTRY.items():
            ttk.Label(outer, text=f'{key}:').grid(row=r, column=0, sticky='w', pady=(2, 0))
            r += 1
            var = tk.StringVar(value=str(ds.folder))
            self._db_vars[key] = var
            ttk.Entry(outer, textvariable=var, width=34).grid(
                row=r, column=0, sticky='ew', padx=(0, 4))
            ttk.Button(outer, text='…', width=3,
                       command=lambda k=key: self._browse_db(k)).grid(row=r, column=1)
            r += 1

        ttk.Separator(outer).grid(row=r, column=0, columnspan=2, sticky='ew', pady=8)
        r += 1

        # ── Output dir ────────────────────────────────────────────────────────
        ttk.Label(outer, text='Output Directory', font=('', 9, 'bold')).grid(
            row=r, column=0, columnspan=2, sticky='w')
        r += 1
        self._output_var = tk.StringVar(value='output')
        ttk.Entry(outer, textvariable=self._output_var, width=34).grid(
            row=r, column=0, sticky='ew', padx=(0, 4))
        ttk.Button(outer, text='…', width=3, command=self._browse_output).grid(
            row=r, column=1)
        r += 1

        ttk.Separator(outer).grid(row=r, column=0, columnspan=2, sticky='ew', pady=8)
        r += 1

        # ── Protocol selectors ────────────────────────────────────────────────
        makes_list    = sorted({m for m, _ in list_protocols()})
        protocol_list = sorted({p for _, p in list_protocols()})

        ttk.Label(outer, text='Make', font=('', 9, 'bold')).grid(
            row=r, column=0, sticky='w')
        r += 1
        self._make_var = tk.StringVar(value=makes_list[0] if makes_list else 'hyundai')
        ttk.Combobox(outer, textvariable=self._make_var, values=makes_list,
                     state='readonly', width=33).grid(
            row=r, column=0, columnspan=2, sticky='ew')
        r += 1

        ttk.Label(outer, text='Protocol', font=('', 9, 'bold')).grid(
            row=r, column=0, sticky='w', pady=(4, 0))
        r += 1
        self._proto_var = tk.StringVar(value=protocol_list[0] if protocol_list else 'can_uds')
        ttk.Combobox(outer, textvariable=self._proto_var, values=protocol_list,
                     state='readonly', width=33).grid(
            row=r, column=0, columnspan=2, sticky='ew')
        r += 1

        ttk.Separator(outer).grid(row=r, column=0, columnspan=2, sticky='ew', pady=8)
        r += 1

        # ── Buttons ───────────────────────────────────────────────────────────
        ttk.Button(outer, text='Load Vehicles', command=self._load_vehicles).grid(
            row=r, column=0, columnspan=2, sticky='ew', pady=(0, 4))
        r += 1
        self._gen_btn = ttk.Button(outer, text='▶  Generate .sim Files',
                                   command=self._generate)
        self._gen_btn.grid(row=r, column=0, columnspan=2, sticky='ew')
        r += 1
        self._progress = ttk.Progressbar(outer, mode='indeterminate')
        self._progress.grid(row=r, column=0, columnspan=2, sticky='ew', pady=(6, 0))

    def _build_right(self) -> None:
        outer = ttk.Frame(self, padding=(0, 10, 10, 10))
        outer.grid(row=0, column=1, sticky='nsew')
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        # ── Vehicle table ─────────────────────────────────────────────────────
        vf = ttk.LabelFrame(outer, text='Vehicles', padding=4)
        vf.grid(row=0, column=0, sticky='nsew', pady=(0, 6))
        vf.columnconfigure(0, weight=1)
        vf.rowconfigure(0, weight=1)

        COLS = ('VIN', 'Year', 'Manufacturer', 'Make', 'Model', 'Engine')
        WIDTHS = {'VIN': 175, 'Year': 46, 'Manufacturer': 90,
                  'Make': 90, 'Model': 150, 'Engine': 140}
        self._tree = ttk.Treeview(vf, columns=COLS, show='headings', height=5)
        for c in COLS:
            self._tree.heading(c, text=c)
            self._tree.column(c, width=WIDTHS.get(c, 100), anchor='w', stretch=True)
        vsb = ttk.Scrollbar(vf, orient='vertical', command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')

        # ── Log area ──────────────────────────────────────────────────────────
        lf = ttk.LabelFrame(outer, text='Log', padding=4)
        lf.grid(row=1, column=0, sticky='nsew')
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)

        self._log_text = tk.Text(
            lf, state='disabled', font=('Consolas', 9),
            wrap='none', bg='#1e1e1e', fg='#d4d4d4',
        )
        vsb2 = ttk.Scrollbar(lf, orient='vertical', command=self._log_text.yview)
        hsb2 = ttk.Scrollbar(lf, orient='horizontal', command=self._log_text.xview)
        self._log_text.configure(yscrollcommand=vsb2.set, xscrollcommand=hsb2.set)
        self._log_text.grid(row=0, column=0, sticky='nsew')
        vsb2.grid(row=0, column=1, sticky='ns')
        hsb2.grid(row=1, column=0, sticky='ew')

        ttk.Button(lf, text='Clear Log', command=self._clear_log).grid(
            row=2, column=0, columnspan=2, sticky='e', pady=(4, 0))

        # Color tags
        self._log_text.tag_configure('INFO',    foreground='#9cdcfe')
        self._log_text.tag_configure('WARNING', foreground='#dcdcaa')
        self._log_text.tag_configure('ERROR',   foreground='#f44747')
        self._log_text.tag_configure('SKIP',    foreground='#c586c0')
        self._log_text.tag_configure('DONE',    foreground='#4ec9b0')
        self._log_text.tag_configure('SEP',     foreground='#555555')

    # ── Browse helpers ────────────────────────────────────────────────────────

    def _browse_config(self) -> None:
        p = filedialog.askopenfilename(
            title='Select Config File',
            filetypes=[('Excel files', '*.xlsx *.xls'), ('All files', '*.*')],
        )
        if p:
            self._config_var.set(p)
            self._load_vehicles()

    def _browse_db(self, key: str) -> None:
        p = filedialog.askdirectory(title=f'Select {key} Folder')
        if p:
            self._db_vars[key].set(p)

    def _browse_output(self) -> None:
        p = filedialog.askdirectory(title='Select Output Directory')
        if p:
            self._output_var.set(p)

    # ── Vehicle loader ────────────────────────────────────────────────────────

    def _load_vehicles(self) -> None:
        config = Path(self._config_var.get())
        if not config.exists():
            return
        try:
            df = pd.read_excel(str(config), sheet_name='VIN_YMME')
        except Exception as exc:
            messagebox.showerror('Error', f'Cannot read VIN_YMME:\n{exc}')
            return

        for item in self._tree.get_children():
            self._tree.delete(item)

        count = 0
        for _, row in df.iterrows():
            vin = str(row.get('VIN', '')).strip()
            if not vin or vin.lower() == 'nan':
                continue
            self._tree.insert('', 'end', values=(
                vin,
                str(row.get('Year', '')).strip(),
                str(row.get('Manufacturer', '')).strip(),
                str(row.get('Make', '')).strip(),
                str(row.get('Model', '')).strip(),
                str(row.get('Engine', '')).strip(),
            ))
            count += 1

        self._append_log(f'Loaded {count} vehicle(s) from {config.name}', 'INFO')

    # ── Generation ────────────────────────────────────────────────────────────

    def _generate(self) -> None:
        if self._running:
            return

        config = Path(self._config_var.get())
        output = Path(self._output_var.get())
        make   = self._make_var.get()
        proto  = self._proto_var.get()

        if not config.exists():
            messagebox.showerror('Error', f'Config file not found:\n{config}')
            return

        # Push updated folder paths into DB_REGISTRY before loading
        for key, var in self._db_vars.items():
            if key in _db.DB_REGISTRY:
                _db.DB_REGISTRY[key].folder = Path(var.get())

        self._running = True
        self._gen_btn.config(state='disabled')
        self._progress.start(12)
        self._append_log('─' * 70, 'SEP')
        self._append_log(f'Starting  make={make}  protocol={proto}', 'INFO')

        def _run() -> None:
            try:
                protocol_obj = get_protocol(make, proto)
                n = protocol_obj.generate_sim_files(config, output)
                self.after(0, lambda: self._on_done(n, None))
            except SystemExit:
                self.after(0, lambda: self._on_done(0, 'Aborted by user (schema mismatch).'))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda: self._on_done(0, err))

        threading.Thread(target=_run, daemon=True).start()

    def _on_done(self, n: int, error: str | None) -> None:
        self._running = False
        self._gen_btn.config(state='normal')
        self._progress.stop()
        if error:
            self._append_log(f'FAILED — {error}', 'ERROR')
            messagebox.showerror('Generation Failed', error)
        else:
            self._append_log(
                f'Done — {n} vehicle folder(s) written to {self._output_var.get()}/', 'DONE',
            )
            messagebox.showinfo('Done', f'{n} vehicle folder(s) written to:\n{self._output_var.get()}/')

    # ── Polling loops ─────────────────────────────────────────────────────────

    def _poll_logs(self) -> None:
        """Drain the logging queue and append to the text widget."""
        while True:
            try:
                msg = self._log_q.get_nowait()
            except queue.Empty:
                break
            tag = 'INFO'
            upper = msg.upper()
            if '  ERROR   ' in upper or '  ERROR  ' in upper:
                tag = 'ERROR'
            elif '  WARNING ' in upper or '  WARNING  ' in upper:
                tag = 'WARNING'
            elif '[SKIP]' in msg:
                tag = 'SKIP'
            self._append_log(msg, tag)
        self.after(80, self._poll_logs)

    def _poll_prompts(self) -> None:
        """Check for schema-mismatch prompts from the background thread."""
        try:
            file_str, reason, event, answer = _PROMPT_Q.get_nowait()
            result = messagebox.askyesno(
                title='Schema Mismatch — Skip file?',
                message=(
                    f'A file does not match the expected schema.\n\n'
                    f'File:\n  {file_str}\n\n'
                    f'Reason:\n  {reason}\n\n'
                    f'Skip this file and continue?'
                ),
                icon='warning',
                default='yes',
            )
            answer[0] = result
            event.set()
        except queue.Empty:
            pass
        self.after(100, self._poll_prompts)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _append_log(self, msg: str, tag: str = 'INFO') -> None:
        self._log_text.config(state='normal')
        self._log_text.insert('end', msg + '\n', tag)
        self._log_text.see('end')
        self._log_text.config(state='disabled')

    def _clear_log(self) -> None:
        self._log_text.config(state='normal')
        self._log_text.delete('1.0', 'end')
        self._log_text.config(state='disabled')

    def _center(self, w: int, h: int) -> None:
        self.update_idletasks()
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f'{w}x{h}+{x}+{y}')


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app = SimGeneratorApp()
    app.mainloop()
