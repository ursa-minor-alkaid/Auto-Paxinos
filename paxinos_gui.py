# -*- coding: utf-8 -*-
"""Paxinos 图谱自动测量 GUI（tkinter）。

与命令行共用同一套测量逻辑：以 SimpleNamespace 构造与 argparse 同形的
args 对象，直接调用 paxinos_auto 的 cmd_* 函数。运行期间把 sys.stdout
临时替换为写入队列的代理对象，core 与 cmd 层的全部 print 输出因此自动
进入日志区；测量在后台线程执行，主线程经 queue + after 轮询刷新界面。

用法
----
    python paxinos_gui.py
"""
from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from PIL import Image, ImageTk

import atlas_core as core
import paxinos_auto as cli


def _base_dir():
    """程序基准目录。打包成单文件 exe 后 __file__ 位于临时解压目录，
    此时改用 exe 所在目录，保证输出与配置落在 exe 旁边。"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _temp_dir():
    """运行环境解压所在的临时目录。单文件 exe 取 _MEIxxxxxxx 的上级目录，
    未打包时取系统临时目录。"""
    mei = getattr(sys, '_MEIPASS', None)
    if getattr(sys, 'frozen', False) and mei and Path(mei).name.startswith('_MEI'):
        return Path(mei).parent
    return Path(tempfile.gettempdir())


_BASE = _base_dir()
_CONFIG_DIR = Path(os.environ.get('APPDATA') or Path.home()) / 'PaxinosMeasure'
_CONFIG_FILE = _CONFIG_DIR / 'config.json'

MODES = (
    ('nucleus', '核团测量'),
    ('all', '标定+轮廓+核团'),
    ('calibrate', '仅标定'),
    ('outline', '轮廓极值'),
)
_MODE_FUNCS = {'nucleus': cli.cmd_nucleus, 'all': cli.cmd_all,
               'calibrate': cli.cmd_calibrate, 'outline': cli.cmd_outline}


class _QueueWriter:
    """sys.stdout 代理：print 内容写入队列，由主线程取走显示。"""

    def __init__(self, q):
        self._q = q

    def write(self, s):
        if s:
            self._q.put(s)

    def flush(self):
        pass


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.worker = None
        self._photos = []
        self._cfg = self._load_config()

        root.title('Paxinos 图谱自动测量')
        root.minsize(980, 620)
        root.protocol('WM_DELETE_WINDOW', self._on_close)

        self._build_form()
        self._build_buttons()
        self._build_output()
        self._fill_from_config()
        self._show_overlays([])
        root.after(100, self._poll)

    # ---------------- 界面 ----------------
    def _build_form(self):
        self.mode_var = tk.StringVar(value='nucleus')
        frm = ttk.LabelFrame(self.root, text='测量参数', padding=10)
        frm.pack(fill='x', padx=12, pady=(12, 4))

        ttk.Label(frm, text='图谱 PDF:').grid(row=0, column=0, sticky='w')
        self.pdf_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.pdf_var).grid(row=0, column=1, sticky='we', padx=6)
        ttk.Button(frm, text='浏览…', command=self._browse_pdf).grid(row=0, column=2)

        ttk.Label(frm, text='PDF 页码:').grid(row=1, column=0, sticky='w')
        self.page_var = tk.StringVar(value='43')
        tk.Spinbox(frm, from_=1, to=9999, textvariable=self.page_var, width=7).grid(row=1, column=1, sticky='w', padx=6)
        ttk.Label(frm, text='（页码 = Figure 号 + 6）', foreground='#666').grid(row=1, column=2, sticky='w')

        ttk.Label(frm, text='核团缩写:').grid(row=2, column=0, sticky='w')
        self.abbr_var = tk.StringVar(value='AD')
        ttk.Entry(frm, textvariable=self.abbr_var, width=10).grid(row=2, column=1, sticky='w', padx=6)
        ttk.Label(frm, text='（与页面标签一致，如 AD、MD、AVDM）', foreground='#666').grid(row=2, column=2, sticky='w')

        ttk.Label(frm, text='输出目录:').grid(row=3, column=0, sticky='w')
        self.out_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.out_var).grid(row=3, column=1, sticky='we', padx=6)
        ttk.Button(frm, text='浏览…', command=self._browse_out).grid(row=3, column=2)

        ttk.Label(frm, text='运行模式:').grid(row=4, column=0, sticky='w')
        box = ttk.Frame(frm)
        box.grid(row=4, column=1, columnspan=2, sticky='w', padx=6)
        for val, txt in MODES:
            ttk.Radiobutton(box, text=txt, value=val, variable=self.mode_var).pack(side='left', padx=(0, 12))

        adv = ttk.LabelFrame(frm, text='高级参数（一般无需修改）', padding=8)
        adv.grid(row=5, column=0, columnspan=3, sticky='we', pady=(10, 0))
        self.dpi_var = tk.StringVar(value='900')
        self.window_var = tk.StringVar(value='0.8')
        self.minarea_var = tk.StringVar(value='0.01')
        self.maxarea_var = tk.StringVar(value='1.5')
        for c, (label, var) in enumerate((
                ('渲染 dpi', self.dpi_var), ('窗口半径 mm', self.window_var),
                ('最小面积 mm²', self.minarea_var), ('最大面积 mm²', self.maxarea_var))):
            ttk.Label(adv, text=label).grid(row=0, column=c * 2, sticky='e', padx=(0, 4))
            ttk.Entry(adv, textvariable=var, width=8).grid(row=0, column=c * 2 + 1, padx=(0, 14))
        self.list_var = tk.BooleanVar(value=False)
        self.nosave_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(adv, text='列出候选区域', variable=self.list_var).grid(row=0, column=8, sticky='w')
        ttk.Checkbutton(adv, text='只看不保存', variable=self.nosave_var).grid(row=0, column=9, sticky='w')

        frm.columnconfigure(1, weight=1)

    def _build_buttons(self):
        bar = ttk.Frame(self.root)
        bar.pack(fill='x', padx=12, pady=4)
        self.run_btn = ttk.Button(bar, text='开始测量', command=self._on_run)
        self.run_btn.pack(side='left')
        ttk.Button(bar, text='打开输出目录', command=self._open_out).pack(side='left', padx=8)
        ttk.Button(bar, text='打开临时目录', command=self._open_temp).pack(side='left')
        self.progress = ttk.Progressbar(bar, mode='indeterminate', length=160)
        self.progress.pack(side='left', padx=8)
        self.status_var = tk.StringVar(value='就绪')
        ttk.Label(bar, textvariable=self.status_var).pack(side='left', padx=8)

    def _build_output(self):
        pane = ttk.Frame(self.root)
        pane.pack(fill='both', expand=True, padx=12, pady=(4, 12))
        self.log = scrolledtext.ScrolledText(pane, width=52, height=26,
                                             state='disabled', font=('Consolas', 10))
        self.log.pack(side='left', fill='both', expand=True)
        self.preview = ttk.Frame(pane)
        self.preview.pack(side='left', fill='both', expand=True, padx=(8, 0))
        self.preview.columnconfigure(0, weight=1)
        self.preview.columnconfigure(1, weight=1)

    # ---------------- 运行 ----------------
    def _on_run(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            args = self._collect_args()
        except ValueError as e:
            messagebox.showerror('参数有误', str(e))
            return
        mode = self.mode_var.get()
        fn = _MODE_FUNCS[mode]
        self._save_config()
        self.run_btn.configure(state='disabled')
        self.status_var.set('测量中…')
        self.progress.start(12)
        self._append_log('\n' + '=' * 60 + '\n')
        self.worker = threading.Thread(target=self._run, args=(fn, args, mode), daemon=True)
        self.worker.start()

    def _run(self, fn, args, mode):
        """工作线程：替换 stdout 后执行测量，print 输出全部进入日志队列。"""
        old, sys.stdout = sys.stdout, _QueueWriter(self.q)
        try:
            fn(args)
            self.q.put('\n[完成]\n')
        except Exception:
            self.q.put('\n[出错]\n' + traceback.format_exc() + '\n')
        finally:
            sys.stdout = old
            self.q.put(('DONE', mode))

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple) and item[0] == 'DONE':
                    self._on_done(item[1])
                else:
                    self._append_log(item)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _on_done(self, mode):
        self.progress.stop()
        self.run_btn.configure(state='normal')
        self.status_var.set('完成')
        if mode in ('nucleus', 'all') and not self.nosave_var.get():
            self._refresh_preview()
        else:
            self._show_overlays([])

    # ---------------- 参数与配置 ----------------
    def _collect_args(self):
        try:
            page = int(self.page_var.get())
        except ValueError:
            raise ValueError('页码必须是整数')
        if page < 1:
            raise ValueError('页码从 1 开始')
        abbr = self.abbr_var.get().strip()
        if self.mode_var.get() in ('nucleus', 'all') and not abbr:
            raise ValueError('核团测量需要填写缩写')
        try:
            dpi = int(self.dpi_var.get())
            window = float(self.window_var.get())
            min_area = float(self.minarea_var.get())
            max_area = float(self.maxarea_var.get())
        except ValueError:
            raise ValueError('高级参数中 dpi / 窗口 / 面积阈值必须是数字')
        if not (72 <= dpi <= 2400):
            raise ValueError('dpi 建议在 300 至 1200 之间')
        pdf = self.pdf_var.get().strip()
        out = self.out_var.get().strip() or str(_BASE / 'output')
        return SimpleNamespace(pdf=pdf or None, page=page, abbr=abbr or 'AD',
                               dpi=dpi, window=window, min_area=min_area,
                               max_area=max_area, out=out,
                               no_save=self.nosave_var.get(), list=self.list_var.get())

    def _load_config(self):
        try:
            return json.loads(_CONFIG_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {}

    def _fill_from_config(self):
        c = self._cfg
        self.pdf_var.set(c.get('pdf', '') or self._default_pdf())
        self.out_var.set(c.get('out', str(_BASE / 'output')))
        self.page_var.set(str(c.get('page', 43)))
        self.abbr_var.set(c.get('abbr', 'AD'))

    @staticmethod
    def _default_pdf():
        """常见位置的图谱 PDF：atlas_core 的默认路径，以及 resources/ 子目录。"""
        cands = [core.DEFAULT_PDF,
                 _BASE / 'resources' / 'Paxinos脑图谱(第五版)-示意图.pdf']
        for c in cands:
            if c and Path(c).is_file():
                return str(c)
        return ''

    def _save_config(self):
        try:
            _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = {'pdf': self.pdf_var.get().strip(), 'out': self.out_var.get().strip(),
                    'page': self.page_var.get(), 'abbr': self.abbr_var.get().strip()}
            _CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass

    def _on_close(self):
        self._save_config()
        self.root.destroy()

    # ---------------- 预览与杂项 ----------------
    def _refresh_preview(self):
        out = Path(self.out_var.get().strip() or (_BASE / 'output'))
        try:
            abbr = self.abbr_var.get().strip() or 'AD'
            page = int(self.page_var.get())
            pat = 'nucleus_%s_p%d_*.png' % (abbr, page)
            paths = sorted(out.glob(pat)) if out.is_dir() else []
        except ValueError:
            paths = []
        self._show_overlays(paths)

    def _show_overlays(self, paths):
        for w in self.preview.winfo_children():
            w.destroy()
        self._photos.clear()
        if not paths:
            ttk.Label(self.preview, text='无预览图', foreground='#888').grid(row=0, column=0, pady=40)
            return
        for i, p in enumerate(paths[:4]):
            im = Image.open(p)
            im.thumbnail((440, 440))
            ph = ImageTk.PhotoImage(im)
            self._photos.append(ph)
            cell = ttk.Frame(self.preview)
            cell.grid(row=i // 2, column=i % 2, padx=4, pady=4, sticky='n')
            ttk.Label(cell, image=ph).pack()
            ttk.Label(cell, text=Path(p).name, foreground='#666').pack()

    def _append_log(self, s):
        self.log.configure(state='normal')
        self.log.insert('end', s)
        self.log.see('end')
        self.log.configure(state='disabled')

    def _browse_pdf(self):
        p = filedialog.askopenfilename(title='选择图谱 PDF',
                                       filetypes=[('PDF 文件', '*.pdf'), ('所有文件', '*.*')])
        if p:
            self.pdf_var.set(p)

    def _browse_out(self):
        d = filedialog.askdirectory(title='选择输出目录')
        if d:
            self.out_var.set(d)

    def _open_out(self):
        out = self.out_var.get().strip() or str(_BASE / 'output')
        Path(out).mkdir(parents=True, exist_ok=True)
        os.startfile(out)

    def _open_temp(self):
        d = _temp_dir()
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(d)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
