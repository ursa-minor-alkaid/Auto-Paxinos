# -*- coding: utf-8 -*-
"""核团区域测量：以图谱缩写标签定位，做连通域分割并换算为立体定位坐标。

原理
----
示意图版 PDF 中，每个核团的边界由实线或虚线绘制，区域内部为白色。
把非白色像素当作“屏障”，用形态学膨胀把虚线缺口封住（膨胀半径由页面
虚线样式的间隙决定，2K >= 间隙像素数），再对空白区域做连通域标记。
包含目标缩写标签的那个连通域即该核团在切片上的绘图区域。取其外接范围、
质心与面积，并由页面标定换算成 mm，最后给出以 bregma 为原点的坐标。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageDraw

import atlas_core as core


def _components_near(labmap, cx, cy, radius):
    """标签附近（半径 radius 像素内）出现过的连通域编号。"""
    y0, y1 = max(0, cy - radius), min(labmap.shape[0], cy + radius + 1)
    x0, x1 = max(0, cx - radius), min(labmap.shape[1], cx + radius + 1)
    sub = labmap[y0:y1, x0:x1]
    ids, _ = np.unique(sub[sub > 0], return_counts=True)
    return set(int(i) for i in ids)


def find_labels(page, cal, abbr, y_max_pt=450.0, min_size=5.0, spans=None):
    """定位页面上的缩写标签（排除底部缩略语表），返回标签中心与 mm 坐标。"""
    labels = []
    for t, rect, color, size in (spans if spans is not None else core.page_spans(page)):
        if t != abbr or size < min_size or rect[1] > y_max_pt:
            continue
        xc, yc = (rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0
        labels.append({'x_pt': xc, 'y_pt': yc,
                       'ml': (xc - cal['X0']) / cal['MM'],
                       'dvb': (yc - cal['YR0']) / cal['MM']})
    return labels


def measure(pdf_path=None, page_no=43, abbr='AD', cal=None, dpi=900, window=0.8,
            min_area=0.01, max_area=1.5, outline=None, out_dir=None, save=True):
    """测量指定核团，返回结果字典（含左右两侧区域与注射坐标）。"""
    doc = core.open_doc(pdf_path)
    page = doc[page_no - 1]
    cal = cal or core.calibrate(page)
    X0, YR0, MM = cal['X0'], cal['YR0'], cal['MM']
    bregma = cal['bregma']
    gap_pt = core.dash_gap_pt(page)
    k = int(round(gap_pt * dpi / 72.0 / 2.0)) + 4      # 膨胀半径（像素）
    px = 72.0 / dpi                                    # pt / 像素
    mm_px = px / MM                                    # mm / 像素

    spans = core.page_spans(page)
    labels = find_labels(page, cal, abbr, spans=spans)
    if not labels:
        raise RuntimeError('页面中未找到缩写标签 %r，请检查页码或缩写' % abbr)

    results = []
    for lab in labels:
        clip = pymupdf.Rect(X0 + (lab['ml'] - window) * MM, YR0 + (lab['dvb'] - window) * MM,
                            X0 + (lab['ml'] + window) * MM, YR0 + (lab['dvb'] + window) * MM)
        img = core.render_clip(page, clip, dpi)
        white = (img > 235).all(axis=2)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))
        barrier = cv2.dilate((~white).astype(np.uint8), kernel)
        free = (barrier == 0).astype(np.uint8)
        n, labmap, stats, cent = cv2.connectedComponentsWithStats(free, 8)

        cx = int(round((lab['x_pt'] - clip.x0) / px))
        cy = int(round((lab['y_pt'] - clip.y0) / px))
        near_ids = _components_near(labmap, cx, cy, int(round(0.2 * MM / px)))

        candidates = []
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            area_mm = area * mm_px ** 2
            if not (min_area <= area_mm <= max_area):
                continue
            candidates.append({
                'id': i, 'area_mm2': area_mm,
                'ml_lo': (clip.x0 + x * px - X0) / MM, 'ml_hi': (clip.x0 + (x + w) * px - X0) / MM,
                'dv_lo': (clip.y0 + y * px - YR0) / MM, 'dv_hi': (clip.y0 + (y + h) * px - YR0) / MM,
                'ml_c': (clip.x0 + cent[i][0] * px - X0) / MM,
                'dv_c': (clip.y0 + cent[i][1] * px - YR0) / MM,
            })

        # 选择区域：优先取标签附近（半径 0.2 mm）出现过、且质心离标签最近的候选
        hits = [c for c in candidates if c['id'] in near_ids]
        pool = hits or candidates
        chosen, warning = None, None
        if pool:
            chosen = min(pool, key=lambda c: (c['ml_c'] - lab['ml']) ** 2 + (c['dv_c'] - lab['dvb']) ** 2)
            if not hits:
                warning = '标签附近没有合适大小的连通域，改用质心最近的候选区域'
        if chosen is None:
            raise RuntimeError('窗口内没有合适大小的连通域，建议加大 --window 或调整 --min-area/--max-area')

        mask = (labmap == chosen['id'])
        side = 'left' if lab['ml'] < 0 else 'right'
        res = {
            'side': side,
            'label_ml': lab['ml'], 'label_dvb': lab['dvb'],
            'area_mm2': chosen['area_mm2'],
            'ml_lo': chosen['ml_lo'], 'ml_hi': chosen['ml_hi'],
            'dv_lo': chosen['dv_lo'], 'dv_hi': chosen['dv_hi'],
            'ml_c': chosen['ml_c'], 'dv_c': chosen['dv_c'],
            'warning': warning,
            'candidates': candidates,
        }
        # 以 bregma 为原点的坐标
        res['coordinate'] = {'x_ML': chosen['ml_c'], 'y_AP': bregma, 'z_DV_bregma': chosen['dv_c']}
        surf = core.dorsal_dv(page, cal, chosen['ml_c'])
        if surf is not None:
            res['brain_surface_dv_bregma'] = surf
            res['depth_from_surface'] = chosen['dv_c'] - surf

        if save and out_dir:
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            png = out_dir / ('nucleus_%s_p%d_%s.png' % (abbr, page_no, side))
            _save_overlay(img, mask, cal, clip, dpi, png, abbr, res)
            res['overlay'] = str(png)

        results.append(res)

    return {'pdf': str(pdf_path or core.DEFAULT_PDF), 'page': page_no, 'abbreviation': abbr,
            'calibration': cal, 'sides': results}


def _save_overlay(img, mask, cal, clip, dpi, path, abbr, res):
    """保存标注图：目标区域着色 + 0.5 mm 网格 + 范围标注。"""
    X0, YR0, MM = cal['X0'], cal['YR0'], cal['MM']
    px = 72.0 / dpi
    base = img.copy()
    base[mask] = (255, 90, 90)
    out = (0.45 * img + 0.55 * base).astype(np.uint8)
    im = Image.fromarray(out)
    dr = ImageDraw.Draw(im)

    ml0, ml1 = (clip.x0 - X0) / MM, (clip.x1 - X0) / MM
    dv0, dv1 = (clip.y0 - YR0) / MM, (clip.y1 - YR0) / MM
    v = np.ceil(ml0 * 2) / 2
    while v <= ml1:
        x = (X0 + v * MM - clip.x0) / px
        dr.line([(x, 0), (x, im.height)], fill=(220, 0, 0), width=1)
        dr.text((x + 2, 2), '%.1f' % v, fill=(220, 0, 0))
        v += 0.5
    v = np.ceil(dv0 * 2) / 2
    while v <= dv1:
        y = (YR0 + v * MM - clip.y0) / px
        dr.line([(0, y), (im.width, y)], fill=(0, 150, 0), width=1)
        dr.text((2, y - 12), '%.1f' % v, fill=(0, 150, 0))
        v += 0.5
    dr.rectangle([(X0 + res['ml_lo'] * MM - clip.x0) / px, (YR0 + res['dv_lo'] * MM - clip.y0) / px,
                  (X0 + res['ml_hi'] * MM - clip.x0) / px, (YR0 + res['dv_hi'] * MM - clip.y0) / px],
                 outline=(0, 120, 255), width=2)
    dr.text((4, im.height - 16), '%s ML %.2f..%.2f  DVb %.2f..%.2f  centre (%.2f, %.2f)' % (
        abbr, res['ml_lo'], res['ml_hi'], res['dv_lo'], res['dv_hi'], res['ml_c'], res['dv_c']),
        fill=(0, 0, 255))
    im.save(path)