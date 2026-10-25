# -*- coding: utf-8 -*-
"""Paxinos 小鼠脑图谱（第五版·示意图版）自动测量：页面标定与轮廓极值。

复现的测量步骤
--------------
1. 页面标定 calibrate(page)
   页面标尺给出三套线性映射：
       x_pt = X0 + ML * MM
       y_pt = YR0 + DV_bregma * MM       （右缘红字标尺，向下为正）
       y_pt = YB0 - DV_interaural * MM   （左缘黑字标尺，向上为正）
   X0、YR0、YB0 由标尺数字位置最小二乘拟合得到，再吸附到 0.25 mm 刻度栅格；
   MM（每毫米对应的 PDF 点数）由相邻标尺数字的间距得到。

2. 轮廓极值 outline_extremes / dorsal_dv
   把页面渲染成位图，按切片轮廓线的颜色筛选像素，取切片最高点、最低点，
   以及任意 ML 处背侧表面（脑表面）的高度。

坐标约定（图谱前言第 xi 页）
---------------------------
左缘黑字：距内耳线（interaural line）水平面的背腹距离，向上为正；
右缘红字：距 bregma–lambda 颅顶平面的背腹距离，向下为正；
上、下缘数字：距中线的距离（ML）。单位均为 mm，最小刻度 0.25 mm。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pymupdf

# 打包成单文件 exe 时 __file__ 位于临时解压目录，改用 exe 所在目录
_SCRIPT_DIR = (Path(sys.executable).resolve().parent
               if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent)
DEFAULT_PDF = _SCRIPT_DIR.parent / 'Paxinos脑图谱' / 'Paxinos脑图谱(第五版)-示意图.pdf'

FALLBACK_MM_PT = 69.09                  # 每毫米点数（标尺缺失时的兜底值）
FALLBACK_OUTLINE_RGB = (16, 103, 153)   # 切片轮廓线颜色（深蓝）


# --------------------------------------------------------------------------
# 基础提取
# --------------------------------------------------------------------------
def open_doc(pdf_path=None):
    return pymupdf.open(str(pdf_path or DEFAULT_PDF))


def page_spans(page):
    """页面全部文字片段，返回 [(text, rect, color, size)]。"""
    out = []
    for b in page.get_text('dict')['blocks']:
        if b['type'] != 0:
            continue
        for line in b['lines']:
            for s in line['spans']:
                t = s['text'].strip()
                if t:
                    out.append((t, s['bbox'], s['color'], s['size']))
    return out


def collect_ticks(page):
    """收集四条页边上的短刻度线坐标（pt）。"""
    ticks = {'left': [], 'right': [], 'top': [], 'bottom': []}
    for dr in page.get_drawings():
        for it in dr['items']:
            if it[0] != 'l':
                continue
            a, b = it[1], it[2]
            dx, dy = abs(a.x - b.x), abs(a.y - b.y)
            if dy < 0.6 and 1.5 < dx < 6.0:          # 水平短线
                x0, y = min(a.x, b.x), (a.y + b.y) / 2.0
                if x0 < 60:
                    ticks['left'].append(y)
                elif x0 > 720:
                    ticks['right'].append(y)
            elif dx < 0.6 and 1.5 < dy < 6.0:        # 垂直短线
                y0, x = min(a.y, b.y), (a.x + b.x) / 2.0
                if y0 < 45:
                    ticks['top'].append(x)
                elif y0 > 440:
                    ticks['bottom'].append(x)
    for key in ticks:
        ticks[key] = sorted(ticks[key])
    return ticks


def dash_gap_pt(page, max_pt=20.0):
    """页面虚线样式中的最大间隙（pt），用于封闭虚线缺口。

    虚线样式形如 "[ 2.88 1.44 ] 0"，第二个数为间隙；
    只解析方括号内的数字，并排除异常值。
    """
    gaps = []
    for dr in page.get_drawings():
        d = dr.get('dashes')
        if not d:
            continue
        m = re.search(r'\[([^\]]*)\]', str(d))
        if not m:
            continue
        nums = [float(x) for x in re.findall(r'\d*\.?\d+', m.group(1))]
        if len(nums) >= 2 and 0 < nums[1] < max_pt:
            gaps.append(nums[1])
    return max(gaps) if gaps else 1.44


def outline_rgb(page):
    """切片轮廓线颜色：在跨度很大的蓝色系描边中取出现最多的那种。"""
    counts = {}
    for dr in page.get_drawings():
        color = dr.get('color')
        if not color or dr['rect'].width < 300:
            continue
        r, g, b = (int(round(v * 255)) for v in color)
        if b > r + 30 and b >= g + 20:
            counts[(r, g, b)] = counts.get((r, g, b), 0) + 1
    if not counts:
        return FALLBACK_OUTLINE_RGB
    return max(counts, key=counts.get)


def render_array(page, dpi=300):
    """整页渲染为 numpy 数组，返回 (rgb 数组, dpi)。"""
    pix = page.get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8)
    arr = arr.reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()
    return arr, dpi


def render_clip(page, clip, dpi=900):
    """按矩形区域渲染为 numpy 数组（clip 为 pt 单位）。"""
    doc = pymupdf.open()
    pg = doc.new_page(width=clip.width, height=clip.height)
    pg.show_pdf_page(pymupdf.Rect(0, 0, clip.width, clip.height), page.parent, page.number, clip=clip)
    pix = pg.get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8)
    arr = arr.reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()
    doc.close()
    return arr


# --------------------------------------------------------------------------
# 标定
# --------------------------------------------------------------------------
def _digits_in_box(spans, box, size_range=(5.0, 9.0)):
    """给定区域内提取标尺数字，返回按坐标排序的 [(值, 中心x, 中心y)]。"""
    x_lo, y_lo, x_hi, y_hi = box
    out = []
    for t, rect, color, size in spans:
        if not re.fullmatch(r'-?\d{1,2}', t) or not (size_range[0] <= size <= size_range[1]):
            continue
        x0, y0, x1, y1 = rect
        if x_lo <= x0 <= x_hi and y_lo <= y0 <= y_hi:
            out.append((int(t), (x0 + x1) / 2.0, (y0 + y1) / 2.0))
    return out


def _linfit(values, coords):
    """最小二乘拟合 coord = a + b * value，返回 (a, b, 残差rms)。"""
    v = np.asarray(values, dtype=float)
    c = np.asarray(coords, dtype=float)
    b, a = np.polyfit(v, c, 1)
    resid = float(np.sqrt(np.mean((c - (a + b * v)) ** 2)))
    return float(a), float(b), resid


def _subdivision(ticks, mm_pt):
    """由刻度间距推断最小刻度是 0.25 mm 的几分之一，返回分母 n。"""
    vals = sorted(ticks)
    diffs = [b - a for a, b in zip(vals, vals[1:]) if 2.0 <= b - a <= 60.0]
    if not diffs:
        return 4
    return max(1, int(round(mm_pt / float(np.median(diffs)))))


def _grid_phase(ticks, step, anchors, tol=0.35):
    """确定刻度栅格的相位。

    刻度线本身只给出一列等间距点，相位存在整周期的歧义；另外页面上
    文字笔画可能被误当成刻度。做法分两步：
      1) 先按严格容差统计每个候选相位能覆盖多少刻度点，只保留覆盖数
         最多的那几个相位（文字笔画通常偏离真实栅格 0.6 pt 以上，会被
         这一步排除）；
      2) 在候选相位中取「标尺数字中心到最近栅格点平均距离」最小的那个，
         因为标尺数字正好画在整数毫米处。
    返回 (相位, 平均偏差pt)。
    """
    vals = sorted(set(round(t, 3) for t in ticks))
    if len(vals) < 6:
        return None
    scored = []
    for t0 in vals:
        phase = t0 % step
        members = sum(1 for x in vals
                      if abs(((x - phase + step / 2) % step) - step / 2) <= tol)
        scored.append((members, phase))
    best_members = max(m for m, _ in scored)
    cand = [p for m, p in scored if m == best_members]

    def _score(phase):
        return float(np.mean([abs(((a - phase + step / 2) % step) - step / 2) for a in anchors]))

    phase = min(cand, key=_score)
    return phase, _score(phase)


def _zero(fit_a, ticks, anchors, mm_pt):
    """由数字拟合截距与刻度栅格确定零点。"""
    if not ticks or not anchors:
        return fit_a, 'fit'
    step = mm_pt / _subdivision(ticks, mm_pt)
    gp = _grid_phase(ticks, step, anchors)
    if gp is None:
        return fit_a, 'fit'
    phase, score = gp
    zero = phase + round((fit_a - phase) / step) * step
    return zero, 'grid(偏差%.2fpt)' % score


def page_ap(page):
    """读取页面的 bregma / interaural 数值（mm）。"""
    text = page.get_text()
    b = re.search(r'Bregma\s*(-?\d+(?:\.\d+)?)\s*mm', text)
    i = re.search(r'Interaural\s*(\d+(?:\.\d+)?)\s*mm', text)
    return (float(b.group(1)) if b else None), (float(i.group(1)) if i else None)


def calibrate(page, verbose=False):
    """页面标定，返回 {'X0', 'YR0', 'YB0', 'MM', 'bregma', 'interaural', ...}。"""
    spans = page_spans(page)
    ticks = collect_ticks(page)

    top = _digits_in_box(spans, (60, 20, 760, 40))          # 上缘：中线距离
    left = _digits_in_box(spans, (25, 40, 45, 460))         # 左缘：内耳线距离
    right = _digits_in_box(spans, (735, 40, 750, 460))      # 右缘：bregma 距离
    if len(top) < 3 or len(left) < 3 or len(right) < 3:
        raise RuntimeError('标尺数字提取不足，请确认页码对应图谱示意图页')

    zero = [d for d in top if d[0] == 0]
    x_zero = zero[0][1] if zero else (top[0][1] + top[-1][1]) / 2.0
    vals_top = [(d[0] if d[1] > x_zero else -d[0], d[1]) for d in top]

    a_x, b_x, r_x = _linfit([v for v, _ in vals_top], [c for _, c in vals_top])
    a_l, b_l, r_l = _linfit([d[0] for d in left], [d[2] for d in left])
    a_r, b_r, r_r = _linfit([d[0] for d in right], [d[2] for d in right])

    # 比例尺：三条标尺的拟合斜率取中位数（斜率即每毫米点数）
    MM = float(np.median([abs(b_x), abs(b_l), abs(b_r)]))

    X0, how_x = _zero(a_x, ticks['top'] or ticks['bottom'], [c for _, c in vals_top], MM)
    YB0, how_b = _zero(a_l, ticks['left'], [d[2] for d in left], MM)
    YR0, how_r = _zero(a_r, ticks['right'], [d[2] for d in right], MM)

    bregma, interaural = page_ap(page)
    info = {
        'X0': X0, 'YR0': YR0, 'YB0': YB0, 'MM': MM,
        'bregma': bregma, 'interaural': interaural,
        'digit_residual_pt': {'top': r_x, 'left': r_l, 'right': r_r},
        'snap': {'X0': how_x, 'YB0': how_b, 'YR0': how_r},
    }
    if verbose:
        print('标定: X0=%.2f pt  YR0=%.2f pt  YB0=%.2f pt  MM=%.2f pt/mm' % (X0, YR0, YB0, MM))
        print('      数字拟合残差(pt): 上缘 %.2f  左缘 %.2f  右缘 %.2f' % (r_x, r_l, r_r))
        print('      零点吸附方式: %s' % info['snap'])
    return info


# --------------------------------------------------------------------------
# 轮廓极值
# --------------------------------------------------------------------------
def _mask_of_color(arr, rgb, tol=45):
    return ((np.abs(arr[:, :, 0].astype(int) - rgb[0]) < tol) &
            (np.abs(arr[:, :, 1].astype(int) - rgb[1]) < tol) &
            (np.abs(arr[:, :, 2].astype(int) - rgb[2]) < tol))


def outline_extremes(page, cal=None, dpi=300, tol=45):
    """切片轮廓的最高点、最低点，返回 pt 与两套 DV 坐标。"""
    cal = cal or calibrate(page)
    arr, dpi = render_array(page, dpi)
    m = _mask_of_color(arr, outline_rgb(page), tol)
    ys, xs = np.where(m)
    if len(ys) == 0:
        raise RuntimeError('未找到轮廓线像素，请检查轮廓颜色')
    pt = 72.0 / dpi
    top_i, bot_i = int(np.argmin(ys)), int(np.argmax(ys))
    res = {}
    for name, i in (('top', top_i), ('bottom', bot_i)):
        y_pt, x_pt = ys[i] * pt, xs[i] * pt
        res[name] = {
            'y_pt': y_pt, 'x_pt': x_pt,
            'ML': (x_pt - cal['X0']) / cal['MM'],
            'DV_bregma': (y_pt - cal['YR0']) / cal['MM'],
            'DV_interaural': (cal['YB0'] - y_pt) / cal['MM'],
        }
    # 中线附近的最高点（中线上有矢状窦凹口，单独给出）
    sel = np.abs(xs * pt - cal['X0']) <= 0.1 * cal['MM']
    if sel.any():
        y_mid = float(ys[sel].min() * pt)
        res['top_midline'] = {
            'y_pt': y_mid,
            'DV_bregma': (y_mid - cal['YR0']) / cal['MM'],
            'DV_interaural': (cal['YB0'] - y_mid) / cal['MM'],
        }
    return res


def dorsal_dv(page, cal, ml, half_width_mm=0.05, dpi=300, tol=45):
    """指定 ML 处背侧表面（脑表面）的 DV_bregma。"""
    arr, dpi = render_array(page, dpi)
    m = _mask_of_color(arr, outline_rgb(page), tol)
    pt = 72.0 / dpi
    x_pt = cal['X0'] + ml * cal['MM']
    x0 = max(0, int((x_pt - half_width_mm * cal['MM']) / pt))
    x1 = min(m.shape[1] - 1, int((x_pt + half_width_mm * cal['MM']) / pt))
    sub = m[:, x0:x1 + 1]
    ys, _ = np.where(sub)
    if len(ys) == 0:
        return None
    y_top = float(ys.min() * pt)
    return (y_top - cal['YR0']) / cal['MM']