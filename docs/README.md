<h1 align="center">Paxinos Atlas Automatic Measurement Tool</h1>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="python">
  <img src="https://img.shields.io/badge/segmentation-OpenCV-5C3EE8?logo=opencv&logoColor=white" alt="OpenCV">
  <img src="https://img.shields.io/badge/output-PNG%20%7C%20JSON-brightgreen" alt="output">
</p>

<p align="center">
  <b>English</b> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.fr.md">Français</a>
</p>

Reads the stereotaxic coordinates of a nucleus (origin at bregma) directly from the schematic PDF of *Paxinos and Franklin's The Mouse Brain in Stereotaxic Coordinates (5th edition)*, and writes an annotated millimetre-grid image and a JSON file.

---

## Coordinate system

The atlas preface (page xi) defines the rulers printed along the four page margins:

| Position                | Meaning                                                       | Positive direction  |
| ----------------------- | ------------------------------------------------------------- | ------------------- |
| Black numbers 0–5, left | Dorsoventral distance from the horizontal interaural plane     | Up (dorsal)         |
| Red numbers 1–6, right  | Dorsoventral distance from the bregma–lambda skull-top plane   | Down (ventral)      |
| Numbers, top and bottom | Distance from the midline (ML, mediolateral)                   | Midline = 0         |
| Large number, bottom-left | Anteroposterior distance from the vertical interaural plane (Interaural) |           |
| Red number, top-right   | Anteroposterior distance from the vertical bregma plane (Bregma) | Anterior          |

The finest tick is 0.25 mm and every 1 mm carries a number. Injection coordinates are usually written as `x(ML) = ±… mm, y(AP) = bregma value, z(DV) = depth from the bregma–lambda plane`.

---

## How it works

### Page calibration (atlas_core.calibrate)

The schematic PDF is a vector file: text has a text layer and lines have a path layer, so the linear relation between PDF points and atlas millimetres can be recovered exactly:

1. `get_text('dict')` returns every text span with its bounding box and font size; three groups of ruler numbers (top edge, left edge, right edge) are picked by position. The number values are the millimetre values.
2. Each group is fitted by least squares as `coordinate = zero + points per mm × mm value`. The median of the three slopes is used as the scale (about 69.08 pt/mm for this atlas).
3. `get_drawings()` collects the short tick marks on the four page margins, from which the finest tick is inferred to be 0.25 mm and the tick grid is obtained.
4. The centres of the number boxes sit about 1 pt away from the tick marks because of font metrics, and glyph strokes can be mistaken for ticks. The grid phase is therefore fixed in two steps: a strict tolerance (0.35 pt) first removes false ticks such as strokes, then among the candidate phases the one minimizing the mean distance from number centres to the nearest grid point is chosen.
5. Zero = the fitted intercept snapped to the nearest point of that grid.

Calibration yields three zero points (midline `X0`, bregma zero `YR0`, interaural zero `YB0`) and the scale `MM`.

### Outline extremes (atlas_core.outline_extremes)

The page is rendered to a bitmap and pixels matching the colour of the section outline (dark blue, RGB≈(16,103,153), detected automatically from the vector stroke colour) are selected. The highest and lowest points are measured and converted into both DV coordinates. This answers questions such as where the top of the brain lies and why 0 is not at the bottom of the brain, and it also gives the injection depth measured from the brain surface.

### Nucleus region segmentation (atlas_nucleus.measure)

1. **Locate the label**: search the text layer for the span identical to the abbreviation (excluding the smaller abbreviation list at the bottom of the page), take its centre, and split it into a left and a right instance across the midline.
2. **Render a window**: take a rectangle centred on the label with radius `--window` (default 0.8 mm) and render it at `--dpi` (default 900).
3. **Build barriers**: the interior of the nucleus is white and its boundary is a solid or dashed line. Non-white pixels are treated as barriers. Dashed lines have gaps (1.44 pt in this atlas), which are closed by morphological dilation with a radius of `gap / 2 + 4` pixels, so that 2K exceeds the gap width.
4. **Connected components**: label the remaining white space with `connectedComponentsWithStats` and filter by area (`--min-area` to `--max-area`) to obtain candidates.
5. **Select the target**: take the candidate that occurs near the label (within 0.2 mm) and whose centroid is closest to the label. This exploits the convention that atlas labels are printed inside or immediately next to the nucleus. If no suitably sized region is found near the label, the code falls back to "closest centroid" and prints a warning.
6. **Convert and output**: convert the bounding rectangle and centroid from pixels to millimetres to obtain `ML range / DV range / area / centroid`, and combine them into bregma-centred coordinates `x(ML), y(AP), z(DV)`. The brain-surface height at that ML is reported as well, which gives the depth from the brain surface.

---

## Usage

```bash
python paxinos_auto.py calibrate --page 43                    # calibration only: zero points and residuals
python paxinos_auto.py outline   --page 43                    # outline extremes
python paxinos_auto.py nucleus   --page 43 --abbr AD          # measure the nucleus, save image and JSON
python paxinos_auto.py all       --page 43 --abbr AD --list   # calibration + outline + nucleus, list candidates
```

Optional arguments:

| Argument                | Description                                                                                 |
| ----------------------- | ------------------------------------------------------------------------------------------- |
| `--pdf`                 | Atlas PDF path; defaults to `Paxinos脑图谱/Paxinos脑图谱(第五版)-示意图.pdf` one level above |
| `--page`                | PDF page number (1-based), i.e. the figure number plus 6                                     |
| `--abbr`                | Nucleus abbreviation, exactly as printed on the page, e.g. `AD`, `MD`, `AVDM`                |
| `--dpi`                 | Render resolution for segmentation, default 900; raise to 1200 for very small regions        |
| `--window`              | Window radius around the label (mm), default 0.8                                             |
| `--min-area/--max-area` | Candidate area range (mm²), default 0.01 to 1.5                                              |
| `--out`                 | Output directory, default `output/` in this directory                                        |
| `--list`                | Print all candidates in the window for manual checking                                       |
| `--no-save`             | Print results only, write no files                                                           |

The output directory contains:

- `nucleus_<abbr>_p<page>_<left/right>.png`: the target region coloured, with a 0.5 mm grid and the bounding box overlaid
- `measure_<abbr>_p<page>.json`: calibration parameters, ranges on both sides, centroids, injection coordinates, candidate list

---

## Accuracy and limitations

- Calibration residuals are usually below 1 pt (about 0.015 mm); after grid snapping the zero-point uncertainty is about ±0.02 mm, the same order as the difference from manual reading on the figure.
- Only the schematic PDF is supported (vector lines). The stained plates are photographic bitmaps whose boundaries cannot be segmented by colour and connected components.
- The nucleus must be a closed region in the drawing. If a boundary is not drawn or the gap is too large, the region merges with a neighbour and its area becomes abnormally large. In that case use `--list` to inspect the candidates and adjust `--min-area/--max-area`, or measure the adjacent region and correct manually.
- For nuclei whose labels are drawn outside the nucleus with a leader line, the window centre has to be moved to the nucleus by hand, or the correct region located with `--list`.
- Area and centroid describe the drawn boundary, not the histological boundary. Use ranges and centroids for plotting and statistics.

---

## Files

| File               | Purpose                                                              |
| ------------------ | -------------------------------------------------------------------- |
| `atlas_core.py`    | Text/vector extraction, page calibration, outline extremes, region rendering |
| `atlas_nucleus.py` | Nucleus connected-component segmentation, region selection, coordinate conversion, annotated image |
| `paxinos_auto.py`  | Command-line entry point (calibrate / outline / nucleus / all)       |
| `requirements.txt` | Dependencies: pymupdf, numpy, opencv-python, pillow                  |

---

## Reproduction check (page 43 / Figure 37 / Bregma -0.71 mm)

```text
calibration: X0=391.54 pt  YR0=21.10 pt  YB0=421.68 pt  MM=69.08 pt/mm
outline:     highest point DV_bregma 0.26 / interaural +5.54; at midline 0.42; lowest point DV_bregma 6.01 / interaural -0.21
AD   left:   ML -0.94 .. -0.49  DV 2.61 .. 3.15  area 0.081 mm²  centroid (-0.70, 2.84)
AD   right:  ML +0.52 .. +0.94  DV 2.64 .. 3.16  area 0.078 mm²  centroid (+0.70, 2.86)
     injection: x(ML)=±0.70  y(AP)=-0.71  z(DV)=2.85 mm
     brain surface at this ML: DV_bregma≈0.3, depth from surface≈2.55 mm
```

The values agree with the original manual procedure to within 0.02 mm; the difference comes from where the calibration zero lands on the tick grid (systematic offset between the centres of the ruler numbers and the tick marks).

---

## Project structure

```text
Paxinos_auto/
├── atlas_core.py
├── atlas_nucleus.py
├── paxinos_auto.py
├── paxinos_gui.py
├── requirements.txt
├── PaxinosMeasure.spec
├── .gitignore
├── docs/
│   ├── README.md
│   ├── README.zh-CN.md
│   ├── README.fr.md
│   ├── 使用说明.md
│   └── 使用说明.pdf
├── resources/
│   └── Paxinos脑图谱(第五版)-示意图.pdf    # to be provided separately, not tracked
└── output/                                 # generated at runtime
```

`build_env/`, `dist/` and `local/` are the local packaging environment and release copy, excluded by `.gitignore`.
