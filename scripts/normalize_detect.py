#!/usr/bin/env python3
"""第1步: 把 manga-translator-ui 的检测 JSON 归一化成 norm 格式。

用法(工程根执行):
  python normalize_detect.py --in work/detect/raw_json --out work/detect [--images raw]
  python normalize_detect.py --in work/detect/raw_json/001.json --schema-report

norm 格式: work/detect/<页>.norm.json
  {"page": "001", "image": "raw/001.png", "bubbles": [
     {"id": 1, "bbox": [x1,y1,x2,y2], "ocr_text": "...", "ocr_conf": 0.9, "polygon": [[x,y],...]}]}

策略: 递归遍历 JSON, 把"同时含文本字段与坐标字段"的对象抽成气泡(自动适配常见字段名)。
某页抽出 0 个或数量明显偏少 -> 用 --schema-report 看结构, 写几行定制转换再跑。
"""
import argparse
import json
import re
import sys
from pathlib import Path

TEXT_KEYS = ("text", "ocr", "txt", "content", "value", "str", "string", "chars", "raw")
CONF_KEYS = ("conf", "confidence", "score", "prob", "precision")
COORD_KEYS = ("bbox", "box", "region", "rect", "xyxy", "polygon", "poly",
              "quadrilateral", "quad", "coords", "points", "position", "pos", "lines")
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")


def extract(doc):
    # manga-translator-ui 的 *_translations.json 专有格式快速路径:
    # {"<图片路径>": {"regions": [{"text","lines":[[[x,y]..]..],"prob",...}], "original_width", ...}}
    if isinstance(doc, dict) and "regions" not in doc and doc:
        vals = [v for v in doc.values()
                if isinstance(v, dict) and isinstance(v.get("regions"), list)]
        if vals:
            doc = vals[0]
    if isinstance(doc, dict) and isinstance(doc.get("regions"), list) \
            and any(isinstance(r, dict) and "lines" in r for r in doc["regions"]):
        found = []
        for i, r in enumerate(doc["regions"], 1):
            if not isinstance(r, dict):
                continue
            text = (r.get("text") or "").strip()
            pts = [p for line in (r.get("lines") or []) if isinstance(line, list)
                   for p in line if isinstance(p, (list, tuple)) and len(p) >= 2
                   and all(isinstance(t, (int, float)) and not isinstance(t, bool) for t in p[:2])]
            bbox = None
            if pts:
                xs = [float(p[0]) for p in pts]
                ys = [float(p[1]) for p in pts]
                bbox = [min(xs), min(ys), max(xs), max(ys)]
            if not text and bbox is None:
                continue
            item = {"id": i,
                    "bbox": [int(round(v)) for v in bbox] if bbox else [0, 0, 0, 0],
                    "ocr_text": text,
                    "ocr_conf": float(r["prob"]) if isinstance(r.get("prob"), (int, float)) else None}
            if pts and len(pts) >= 3:
                item["polygon"] = [[int(round(float(p[0]))), int(round(float(p[1])))] for p in pts]
            found.append(item)
        if found:
            return found
    return extract_generic(doc)


def iter_nodes(node, path=()):
    if isinstance(node, dict):
        yield node, path
        for k, v in node.items():
            yield from iter_nodes(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from iter_nodes(v, path + (i,))


def get_text(node):
    for k in TEXT_KEYS:
        v = node.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def get_conf(node):
    for k in CONF_KEYS:
        v = node.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= float(v) <= 1.0:
            return float(v)
    return None


def _flat_nums(seq):
    out = []
    for x in seq:
        if isinstance(x, bool):
            return None
        if isinstance(x, (int, float)):
            out.append(float(x))
        elif isinstance(x, dict) and isinstance(x.get("x"), (int, float)) and isinstance(x.get("y"), (int, float)):
            out.extend([float(x["x"]), float(x["y"])])
        elif isinstance(x, (list, tuple)) and len(x) >= 2 and all(isinstance(t, (int, float)) for t in x[:2]):
            out.extend([float(x[0]), float(x[1])])
        else:
            return None
    return out


def parse_coords(v):
    """各种坐标表达 -> (bbox, polygon|None); 认不出返回 None。"""
    if isinstance(v, dict):
        g = {str(k).lower(): val for k, val in v.items()}
        try:
            if all(k in g for k in ("x1", "y1", "x2", "y2")):
                x1, y1, x2, y2 = (float(g[k]) for k in ("x1", "y1", "x2", "y2"))
                return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)], None
            if "x" in g and "y" in g and ("w" in g or "width" in g):
                x, y = float(g["x"]), float(g["y"])
                w = float(g.get("w", g.get("width", 0)))
                h = float(g.get("h", g.get("height", 0)))
                if w <= 0 or h <= 0:
                    return None
                return [x, y, x + w, y + h], None
        except (TypeError, ValueError):
            return None
        return None
    if isinstance(v, (list, tuple)):
        flat = _flat_nums(v)
        if flat is None or len(flat) < 4 or len(flat) % 2 != 0:
            return None
        if len(flat) == 4:
            x1, y1, x2, y2 = flat
            return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)], None
        pts = list(zip(flat[0::2], flat[1::2]))
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return [min(xs), min(ys), max(xs), max(ys)], pts
    return None


def get_coords(node):
    for k in COORD_KEYS:
        if k in node:
            r = parse_coords(node[k])
            if r:
                return r
    for k, v in node.items():
        if isinstance(v, (list, tuple)) and len(v) == 4 \
                and all(isinstance(t, (int, float)) and not isinstance(t, bool) for t in v):
            r = parse_coords(v)
            if r:
                return r
    return None


def find_image(images_dir, stem):
    if not images_dir:
        return None
    d = Path(images_dir)
    if not d.is_dir():
        return None
    for e in IMG_EXTS:
        p = d / f"{stem}{e}"
        if p.exists():
            return p
    for p in d.rglob("*"):
        if p.suffix.lower() in IMG_EXTS and p.stem == stem:
            return p
    return None


def extract_generic(doc):
    found = []
    for node, _path in iter_nodes(doc):
        text = get_text(node)
        if text is None:
            continue
        coords = get_coords(node)
        if not coords:
            continue
        bbox, poly = coords
        if bbox[2] - bbox[0] < 2 or bbox[3] - bbox[1] < 2:
            continue
        item = {"id": 0, "bbox": [int(round(v)) for v in bbox], "ocr_text": text,
                "ocr_conf": get_conf(node)}
        if poly:
            item["polygon"] = [[int(round(x)), int(round(y))] for x, y in poly]
        found.append(item)
    uniq, seen = [], set()
    for f in found:
        key = (f["ocr_text"], tuple(f["bbox"]))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    for i, f in enumerate(uniq, 1):
        f["id"] = i
    return uniq


def main():
    ap = argparse.ArgumentParser(description="归一化检测 JSON", epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True, help="工具原始 JSON 的文件或目录")
    ap.add_argument("--out", dest="out", help="norm 输出目录(默认与 --in 目录相同)")
    ap.add_argument("--images", default=None, help="原图目录, 用于把图片路径写进 norm")
    ap.add_argument("--schema-report", action="store_true", help="只打印 JSON 结构线索, 不写文件")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    src = Path(args.inp)
    files = [src] if src.is_file() else sorted(p for p in src.iterdir() if p.suffix.lower() == ".json")
    if not files:
        sys.exit(f"找不到 JSON: {src}")

    if args.schema_report:
        for f in files[:2]:
            doc = json.loads(f.read_text(encoding="utf-8"))
            top = list(doc)[:12] if isinstance(doc, dict) else type(doc).__name__
            print(f"=== {f.name} 顶层: {top}")
            n = 0
            for node, path in iter_nodes(doc):
                if any(k in node for k in TEXT_KEYS):
                    keys = [k for k in (*TEXT_KEYS, *COORD_KEYS, *CONF_KEYS) if k in node]
                    print("  /".join(map(str, path)) or "  <root>", "->", keys)
                    n += 1
                    if n >= 30:
                        print("  ...(截断)")
                        break
        return

    out = Path(args.out) if args.out else (src if src.is_dir() else src.parent)
    out.mkdir(parents=True, exist_ok=True)
    bad, noimg = [], []
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        bubbles = extract(doc)
        stem = re.sub(r"_translations$", "", f.stem)
        img = find_image(args.images, stem) or find_image("raw", stem)
        norm = {"page": stem, "image": img.as_posix() if img else None, "bubbles": bubbles}
        (out / f"{stem}.norm.json").write_text(json.dumps(norm, ensure_ascii=False, indent=1), encoding="utf-8")
        mark = ""
        if not bubbles:
            bad.append(stem)
            mark = "  <-- 0 个气泡, 用 --schema-report 排查"
        if img is None:
            noimg.append(stem)
            mark += "  [未找到图片]"
        print(f"{stem}: {len(bubbles)} 个气泡{mark}")
    if noimg:
        print("[warn] 未找到图片的页(预览将跳过): " + ", ".join(noimg))
    if bad:
        sys.exit("以下页解析为 0 气泡: " + ", ".join(bad))
    print("done")


if __name__ == "__main__":
    main()
