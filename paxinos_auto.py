# -*- coding: utf-8 -*-
"""Paxinos 图谱自动测量命令行入口。

用法示例
--------
    python paxinos_auto.py calibrate --page 43
    python paxinos_auto.py outline   --page 43
    python paxinos_auto.py nucleus   --page 43 --abbr AD
    python paxinos_auto.py all       --page 43 --abbr AD

默认读取的图谱为本目录上一级的
    Paxinos脑图谱/Paxinos脑图谱(第五版)-示意图.pdf
输出（标注图与 json）默认写入本目录下的 output/。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import atlas_core as core
import atlas_nucleus as nuc

# 打包成单文件 exe 时 __file__ 位于临时解压目录，改用 exe 所在目录
HERE = (Path(sys.executable).resolve().parent
        if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent)
DEFAULT_OUT = HERE / 'output'


def _paginate(args):
    doc = core.open_doc(args.pdf)
    return doc, doc[args.page - 1]


def cmd_calibrate(args):
    doc, page = _paginate(args)
    cal = core.calibrate(page, verbose=True)
    print('Bregma %s mm   Interaural %s mm' % (cal['bregma'], cal['interaural']))
    return cal


def cmd_outline(args):
    doc, page = _paginate(args)
    cal = core.calibrate(page)
    ext = core.outline_extremes(page, cal, dpi=args.dpi)
    print('切片轮廓极值（Bregma %s mm 层面）' % cal['bregma'])
    print('  最高点: ML %+.2f  DV_bregma %.2f  DV_interaural %+.2f' %
          (ext['top']['ML'], ext['top']['DV_bregma'], ext['top']['DV_interaural']))
    if 'top_midline' in ext:
        print('  中线处: DV_bregma %.2f  DV_interaural %+.2f' %
              (ext['top_midline']['DV_bregma'], ext['top_midline']['DV_interaural']))
    print('  最低点: ML %+.2f  DV_bregma %.2f  DV_interaural %+.2f' %
          (ext['bottom']['ML'], ext['bottom']['DV_bregma'], ext['bottom']['DV_interaural']))
    return ext


def cmd_nucleus(args):
    doc, page = _paginate(args)
    res = nuc.measure(pdf_path=args.pdf, page_no=args.page, abbr=args.abbr,
                      dpi=args.dpi, window=args.window,
                      min_area=args.min_area, max_area=args.max_area,
                      out_dir=args.out, save=not args.no_save)
    cal = res['calibration']
    print('核团 %s  第 %d 页  (Bregma %s mm, Interaural %s mm)' %
          (args.abbr, args.page, cal['bregma'], cal['interaural']))
    for s in res['sides']:
        print('  [%s] ML %+.2f .. %+.2f   DV_bregma %.2f .. %.2f   area %.3f mm2   质心 (ML %+.2f, DVb %.2f)%s' % (
            s['side'], s['ml_lo'], s['ml_hi'], s['dv_lo'], s['dv_hi'], s['area_mm2'],
            s['ml_c'], s['dv_c'], ('  <' + s['warning'] + '>') if s['warning'] else ''))
        print('        注射坐标（以 bregma 为原点）: x(ML)=%+.2f  y(AP)=%.2f  z(DV)=%.2f mm' %
              (s['coordinate']['x_ML'], s['coordinate']['y_AP'], s['coordinate']['z_DV_bregma']))
        if 'depth_from_surface' in s:
            print('        该处脑表面 DV_bregma %.2f，自脑表面起算深度 %.2f mm' %
                  (s['brain_surface_dv_bregma'], s['depth_from_surface']))
        if args.list:
            print('        候选区域:')
            for c in sorted(s['candidates'], key=lambda c: -c['area_mm2']):
                print('          area %.3f  ML %+.2f..%+.2f  DVb %.2f..%.2f  质心 (%+.2f, %.2f)' %
                      (c['area_mm2'], c['ml_lo'], c['ml_hi'], c['dv_lo'], c['dv_hi'], c['ml_c'], c['dv_c']))
        if 'overlay' in s:
            print('        标注图: %s' % s['overlay'])
    if not args.no_save:
        out = Path(args.out or DEFAULT_OUT)
        out.mkdir(parents=True, exist_ok=True)
        jf = out / ('measure_%s_p%d.json' % (args.abbr, args.page))
        slim = dict(res)
        slim['sides'] = [{k: v for k, v in s.items() if k != 'candidates'} for s in res['sides']]
        jf.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding='utf-8')
        print('结果 json: %s' % jf)
    return res


def cmd_all(args):
    cal = cmd_calibrate(args)
    print()
    cmd_outline(args)
    print()
    cmd_nucleus(args)


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--pdf', default=None, help='图谱 PDF 路径，默认使用第五版示意图')
    common.add_argument('--page', type=int, required=True, help='PDF 页码（从 1 开始）')
    common.add_argument('--abbr', default='AD', help='核团缩写，如 AD')
    common.add_argument('--dpi', type=int, default=900, help='核团测量渲染 dpi，默认 900')
    common.add_argument('--window', type=float, default=0.8, help='标签周围取样窗口半径（mm），默认 0.8')
    common.add_argument('--min-area', type=float, default=0.01, help='候选区域最小面积（mm2）')
    common.add_argument('--max-area', type=float, default=1.5, help='候选区域最大面积（mm2）')
    common.add_argument('--out', default=str(DEFAULT_OUT), help='输出目录，默认本目录下 output')
    common.add_argument('--no-save', action='store_true', help='只打印结果，不保存图片与 json')
    common.add_argument('--list', action='store_true', help='列出窗口内全部候选区域')

    parser = argparse.ArgumentParser(description='Paxinos 图谱自动测量（标定 / 轮廓极值 / 核团坐标）')
    sub = parser.add_subparsers(dest='cmd')
    for name, fn in (('calibrate', cmd_calibrate), ('outline', cmd_outline),
                     ('nucleus', cmd_nucleus), ('all', cmd_all)):
        sp = sub.add_parser(name, parents=[common])
        sp.set_defaults(func=fn)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, 'func', None):
        parser.print_help()
        return 1
    args.func(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())