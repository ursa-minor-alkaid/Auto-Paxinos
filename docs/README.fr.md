<h1 align="center">Outil de mesure automatique de l'atlas de Paxinos</h1>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="python">
  <img src="https://img.shields.io/badge/segmentation-OpenCV-5C3EE8?logo=opencv&logoColor=white" alt="OpenCV">
  <img src="https://img.shields.io/badge/output-PNG%20%7C%20JSON-brightgreen" alt="output">
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <b>Français</b>
</p>

Lit automatiquement les coordonnées stéréotaxiques d'un noyau (origine au bregma) à partir du PDF schématique de *Paxinos and Franklin's The Mouse Brain in Stereotaxic Coordinates (5e édition)*, puis écrit une image annotée avec grille millimétrique et un fichier JSON.

---

## Système de coordonnées

La préface de l'atlas (page xi) définit la signification des échelles imprimées sur les quatre bords de la page :

| Position                    | Signification                                                       | Sens positif         |
| --------------------------- | ------------------------------------------------------------------- | -------------------- |
| Chiffres noirs 0–5, à gauche | Distance dorso-ventrale au plan horizontal de la ligne interauriculaire | Vers le haut (dorsal) |
| Chiffres rouges 1–6, à droite | Distance dorso-ventrale au plan crânien bregma–lambda              | Vers le bas (ventral) |
| Chiffres, bords supérieur et inférieur | Distance à la ligne médiane (ML, médio-latérale)          | Ligne médiane = 0    |
| Grand chiffre, en bas à gauche | Distance antéro-postérieure au plan vertical interauriculaire (Interaural) |              |
| Chiffre rouge, en haut à droite | Distance antéro-postérieure au plan vertical du bregma (Bregma)  | Vers l'avant         |

La plus petite graduation est de 0,25 mm et un chiffre est imprimé tous les 1 mm. Les coordonnées d'injection s'écrivent généralement `x(ML) = ±… mm, y(AP) = valeur bregma, z(DV) = profondeur depuis le plan bregma–lambda`.

---

## Principe

### Calibration de la page (atlas_core.calibrate)

Le PDF schématique est un fichier vectoriel : le texte possède une couche texte et les traits une couche de tracés, ce qui permet de retrouver avec précision la relation linéaire entre les points PDF et les millimètres de l'atlas :

1. `get_text('dict')` fournit tous les fragments de texte avec leur cadre et leur corps de police ; trois groupes de chiffres de l'échelle (bord supérieur, bord gauche, bord droit) sont sélectionnés par position. La valeur des chiffres est la valeur en millimètres.
2. Chaque groupe est ajusté par moindres carrés selon `coordonnée = origine + points par mm × valeur en mm`. La médiane des trois pentes sert d'échelle (environ 69,08 pt/mm pour cet atlas).
3. `get_drawings()` récupère les petits traits de graduation des quatre marges, ce qui permet de déduire que la graduation minimale est de 0,25 mm et d'obtenir la grille de graduation.
4. Le centre du cadre des chiffres présente un décalage systématique d'environ 1 pt par rapport aux traits, dû à la métrique de la police, et les traits des glyphes peuvent être pris pour des graduations. La phase de la grille est donc déterminée en deux étapes : une tolérance stricte (0,35 pt) élimine d'abord les fausses graduations de type trait de glyphe, puis, parmi les phases candidates, on retient celle qui minimise la distance moyenne entre les centres des chiffres et le point de grille le plus proche.
5. L'origine est l'ordonnée à l'origine de l'ajustement, ramenée au point de grille le plus proche.

Après calibration, on obtient trois origines (ligne médiane `X0`, origine bregma `YR0`, origine interauriculaire `YB0`) et l'échelle `MM`.

### Extrêmes du contour (atlas_core.outline_extremes)

La page est rendue en bitmap, puis les pixels de la couleur du contour de coupe (bleu foncé, RGB≈(16,103,153), détectée automatiquement à partir de la couleur du tracé vectoriel) sont sélectionnés. Le point le plus haut et le point le plus bas sont mesurés et convertis dans les deux systèmes DV. Cela répond à des questions telles que la position du sommet du cerveau et la raison pour laquelle 0 ne se trouve pas au bas du cerveau, et sert aussi à calculer la profondeur d'injection depuis la surface du cerveau.

### Segmentation de la région du noyau (atlas_nucleus.measure)

1. **Localiser l'étiquette** : rechercher dans la couche texte le fragment identique à l'abréviation (en excluant la liste d'abréviations en petits caractères en bas de page), prendre le centre de l'étiquette, puis la répartir en une occurrence gauche et une occurrence droite de part et d'autre de la ligne médiane.
2. **Rendre une fenêtre** : prendre un rectangle centré sur l'étiquette, de rayon `--window` (0,8 mm par défaut), et le rendre en bitmap à `--dpi` (900 par défaut).
3. **Construire les barrières** : l'intérieur du noyau est blanc et la frontière est un trait plein ou pointillé. Les pixels non blancs servent de barrières. Les pointillés comportent des lacunes (1,44 pt pour cet atlas) que l'on ferme par dilatation morphologique. Le rayon de dilatation vaut `lacune / 2 + 4` pixels, ce qui garantit 2K supérieur à la largeur de la lacune.
4. **Étiquetage des composantes connexes** : l'espace blanc restant est analysé par `connectedComponentsWithStats`, avec un filtrage par aire (`--min-area` à `--max-area`) pour obtenir les candidats.
5. **Sélection de la cible** : on retient le candidat présent au voisinage de l'étiquette (rayon 0,2 mm) dont le barycentre est le plus proche de celle-ci. Cette étape exploite la convention selon laquelle les étiquettes de l'atlas sont imprimées à l'intérieur du noyau ou juste à côté. Si aucune région de taille adaptée n'apparaît près de l'étiquette, le programme se rabat sur le barycentre le plus proche et émet un avertissement.
6. **Conversion et sortie** : le rectangle englobant et le barycentre sont convertis des pixels en millimètres, ce qui donne `plage ML / plage DV / aire / barycentre`, puis combinés en coordonnées d'origine bregma `x(ML), y(AP), z(DV)`. La hauteur de la surface cérébrale à cette ML est également fournie, d'où la profondeur depuis la surface du cerveau.

---

## Utilisation

```bash
python paxinos_auto.py calibrate --page 43                    # calibration seule : origines et résidus
python paxinos_auto.py outline   --page 43                    # extrêmes du contour
python paxinos_auto.py nucleus   --page 43 --abbr AD          # mesure du noyau, image et JSON
python paxinos_auto.py all       --page 43 --abbr AD --list   # calibration + contour + noyau, liste des candidats
```

Paramètres optionnels :

| Paramètre               | Description                                                                                    |
| ----------------------- | ---------------------------------------------------------------------------------------------- |
| `--pdf`                 | Chemin du PDF de l'atlas ; par défaut `Paxinos脑图谱/Paxinos脑图谱(第五版)-示意图.pdf` au niveau supérieur |
| `--page`                | Numéro de page du PDF (à partir de 1), soit le numéro de la figure plus 6                      |
| `--abbr`                | Abréviation du noyau, identique à l'étiquette de la page, par exemple `AD`, `MD`, `AVDM`       |
| `--dpi`                 | Résolution de rendu pour la segmentation, 900 par défaut ; à porter à 1200 pour les petites régions |
| `--window`              | Rayon de la fenêtre autour de l'étiquette (mm), 0,8 par défaut                                  |
| `--min-area/--max-area` | Plage d'aire des candidats (mm²), 0,01 à 1,5 par défaut                                        |
| `--out`                 | Répertoire de sortie, `output/` dans ce répertoire par défaut                                  |
| `--list`                | Affiche tous les candidats de la fenêtre pour vérification manuelle                            |
| `--no-save`             | Affiche les résultats sans écrire de fichier                                                   |

Le répertoire de sortie contient :

- `nucleus_<abréviation>_p<page>_<gauche/droite>.png` : région cible colorée, avec grille de 0,5 mm et rectangle englobant en surimpression
- `measure_<abréviation>_p<page>.json` : paramètres de calibration, plages des deux côtés, barycentres, coordonnées d'injection, liste des candidats

---

## Précision et limites

- Le résidu de calibration est généralement inférieur à 1 pt (environ 0,015 mm) ; après alignement sur la grille, l'incertitude sur l'origine est d'environ ±0,02 mm, du même ordre que l'écart avec une lecture manuelle sur la figure.
- Seul le PDF schématique est pris en charge (traits vectoriels). Les planches colorées sont des bitmaps photographiques dont les frontières ne peuvent pas être segmentées par couleur et composantes connexes.
- Le noyau doit former une région fermée sur la figure. Si une frontière n'est pas tracée ou si la lacune est trop grande, la région fusionne avec une voisine et son aire devient anormalement grande. Il faut alors examiner les candidats avec `--list` et ajuster `--min-area/--max-area`, ou mesurer la région voisine puis corriger manuellement.
- Pour les noyaux dont l'étiquette est dessinée à l'extérieur avec une ligne de rappel, le centre de la fenêtre doit être déplacé à la main vers le noyau, ou la bonne région localisée avec `--list`.
- L'aire et le barycentre décrivent la frontière dessinée, non la frontière histologique. Utiliser les plages et les barycentres pour les figures et les statistiques.

---

## Fichiers

| Fichier            | Rôle                                                                                 |
| ------------------ | ------------------------------------------------------------------------------------ |
| `atlas_core.py`    | Extraction texte/vecteurs, calibration de page, extrêmes du contour, rendu de région |
| `atlas_nucleus.py` | Segmentation des composantes connexes du noyau, sélection de région, conversion des coordonnées, image annotée |
| `paxinos_auto.py`  | Point d'entrée en ligne de commande (calibrate / outline / nucleus / all)            |
| `requirements.txt` | Dépendances : pymupdf, numpy, opencv-python, pillow                                  |

---

## Vérification de reproduction (page 43 / Figure 37 / Bregma -0,71 mm)

```text
calibration : X0=391.54 pt  YR0=21.10 pt  YB0=421.68 pt  MM=69.08 pt/mm
contour :     point le plus haut DV_bregma 0.26 / interauriculaire +5.54 ; à la ligne médiane 0.42 ; point le plus bas DV_bregma 6.01 / interauriculaire -0.21
AD   gauche : ML -0.94 .. -0.49  DV 2.61 .. 3.15  aire 0.081 mm²  barycentre (-0.70, 2.84)
AD   droite : ML +0.52 .. +0.94  DV 2.64 .. 3.16  aire 0.078 mm²  barycentre (+0.70, 2.86)
     coordonnées d'injection : x(ML)=±0.70  y(AP)=-0.71  z(DV)=2.85 mm
     surface cérébrale à cette ML : DV_bregma≈0.3, profondeur depuis la surface≈2.55 mm
```

L'écart avec les valeurs obtenues par la procédure manuelle initiale ne dépasse pas 0,02 mm ; il provient de la position de l'origine de calibration sur la grille de graduation (décalage systématique entre le centre des chiffres de l'échelle et les traits).

---

## Structure du projet

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
│   └── Paxinos脑图谱(第五版)-示意图.pdf    # à fournir séparément, non versionné
└── output/                                 # généré à l'exécution
```

`build_env/`, `dist/` et `local/` sont l'environnement de packaging local et la copie de diffusion, exclus par `.gitignore`.
