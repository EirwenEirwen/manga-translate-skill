#!/usr/bin/env python3
"""第6步: 把最终译文写回 manga-translator-ui 的检测 JSON, 生成渲染任务。

用法(工程根执行):
  python build_output.py --detect work/detect/raw_json --annotations work/annotations \
      --merged work/translation/merged.json --out work/out

输出:
  out/render/<页>.json  仅含需翻译区域(keep_original 区域已剔除) —— 优先交工具, 防乱码
  out/full/<页>.json    全量(保护区域 translation=原文) —— 工具不支持剔除时兜底
  out/manifest.json     每页统计

匹配策略: 先按 OCR 文本(归一化)匹配标注气泡, 多候选/失败时按 bbox IoU。写回时尽量
沿用原 JSON 的 translation 字段名, 没有则新建 "translation"。
"""
import argparse
import copy
import json
import re
import sys
import unicodedata
from pathlib import Path

TEXT_KEYS = ("text", "ocr", "txt", "content", "value", "str", "string", "chars", "raw")
TRANS_KEYS = ("translation", "translated", "trans", "translation_text")
CONF_KEYS = ("conf", "confidence", "score", "prob", "precision")
COORD_KEYS = ("bbox", "box", "region", "rect", "xyxy", "polygon", "poly",
              "quadrilateral", "quad", "coords", "points", "position", "pos", "lines")
STRIP_CHARS = "。、，,．.！!？?…‥ー—―‐-『』「」『』()（）[]【】<>《》・~～;；:：\"'“”‘’ \t"


def get_text(node):
    for k in TEXT_KEYS:
        v = node.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
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


def _flatten_points(v):
    """递归展平任意嵌套的 [x,y] 点集(如 manga-translator-ui 的 lines)。失败返回 None。"""
    if isinstance(v, (list, tuple)):
        if len(v) == 2 and all(isinstance(t, (int, float)) and not isinstance(t, bool) for t in v):
            return [float(v[0]), float(v[1])]
        out = []
        for x in v:
            sub = _flatten_points(x)
            if sub is None:
                return None
            out.extend(sub)
        return out
    return None


def parse_bbox(v):
    if isinstance(v, list) or isinstance(v, tuple):
        flat = _flatten_points(v)
        if flat and len(flat) >= 4 and len(flat) % 2 == 0:
            xs, ys = flat[0::2], flat[1::2]
            return [min(xs), min(ys), max(xs), max(ys)]
        return None
    if isinstance(v, dict):
        g = {str(k).lower(): val for k, val in v.items()}
        try:
            if all(k in g for k in ("x1", "y1", "x2", "y2")):
                x1, y1, x2, y2 = (float(g[k]) for k in ("x1", "y1", "x2", "y2"))
                return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
            if "x" in g and "y" in g and ("w" in g or "width" in g):
                x, y = float(g["x"]), float(g["y"])
                w = float(g.get("w", g.get("width", 0)))
                h = float(g.get("h", g.get("height", 0)))
                return [x, y, x + w, y + h] if w > 0 and h > 0 else None
        except (TypeError, ValueError):
            return None
        return None
    if isinstance(v, (list, tuple)):
        flat = _flat_nums(v)
        if flat is None or len(flat) < 4 or len(flat) % 2:
            return None
        if len(flat) == 4:
            x1, y1, x2, y2 = flat
            return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
        xs, ys = flat[0::2], flat[1::2]
        return [min(xs), min(ys), max(xs), max(ys)]
    return None


def get_bbox(node):
    for k in COORD_KEYS:
        if k in node:
            b = parse_bbox(node[k])
            if b:
                return b
    for k, v in node.items():
        if isinstance(v, (list, tuple)) and len(v) == 4 \
                and all(isinstance(t, (int, float)) and not isinstance(t, bool) for t in v):
            b = parse_bbox(v)
            if b:
                return b
    return None


def iter_regions(node, parent=None, key=None):
    """产出 (parent, key, node)。node 是"同时含文本与坐标"的区域对象; 不再深入其内部。"""
    if isinstance(node, dict):
        if get_text(node) and get_bbox(node):
            yield parent, key, node
        else:
            for k, v in node.items():
                yield from iter_regions(v, node, k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from iter_regions(v, node, i)


def norm_text(s):
    s = unicodedata.normalize("NFKC", s or "")
    return "".join(ch for ch in s if ch not in STRIP_CHARS and not ch.isspace())


def iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def set_translation(node, text):
    for k in TRANS_KEYS:
        if k in node:
            node[k] = text
            return
    node["translation"] = text


def main():
    ap = argparse.ArgumentParser(description="译文写回渲染任务", epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--detect", default="work/detect/raw_json")
    ap.add_argument("--annotations", default="work/annotations")
    ap.add_argument("--merged", default="work/translation/merged.json")
    ap.add_argument("--out", default="work/out")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    merged = json.loads(Path(args.merged).read_text(encoding="utf-8"))
    merged = {uid: (v.get("translation", "") if isinstance(v, dict) else str(v))
              for uid, v in merged.items()}

    out = Path(args.out)
    (out / "render").mkdir(parents=True, exist_ok=True)
    (out / "full").mkdir(parents=True, exist_ok=True)
    manifest = {}
    warn_total = 0

    for jf in sorted(Path(args.detect).glob("*.json"), key=lambda p: p.stem):
        stem = re.sub(r"_translations$", "", jf.stem)
        ann_path = Path(args.annotations) / f"{stem}.json"
        if not ann_path.exists():
            print(f"[warn] {stem}: 无标注文件, 跳过")
            warn_total += 1
            continue
        doc = json.loads(jf.read_text(encoding="utf-8"))
        ann = json.loads(ann_path.read_text(encoding="utf-8"))
        bubbles = ann.get("bubbles", [])
        by_text = {}
        for b in bubbles:
            by_text.setdefault(norm_text(b.get("ocr_text", "")), []).append(b)

        doc_render, doc_full = copy.deepcopy(doc), copy.deepcopy(doc)
        matched, protected, translated = 0, 0, 0
        to_remove = []  # (parent, node) 渲染版里剔除

        for variant, vdoc in (("render", doc_render), ("full", doc_full)):
            for parent, key, node in iter_regions(vdoc):
                text = get_text(node)
                bbox = get_bbox(node)
                uid, bub = None, None
                cands = by_text.get(norm_text(text), [])
                if len(cands) == 1:
                    bub = cands[0]
                else:
                    best, best_iou = None, 0.0
                    for b in (cands or bubbles):
                        v = iou(bbox, [float(x) for x in b["bbox"]])
                        if v > best_iou:
                            best, best_iou = b, v
                    if best and best_iou >= 0.3:
                        bub = best
                if bub is None:
                    continue
                uid = f"{stem}:{bub['id']}"
                if variant == "render":
                    matched += 1
                if bub.get("keep_original"):
                    if variant == "render":
                        protected += 1
                    if variant == "render":
                        if parent is not None:
                            to_remove.append((parent, node))
                    else:
                        set_translation(node, bub.get("source") or text)
                else:
                    tr = merged.get(uid, "")
                    if tr:
                        if variant == "render":
                            translated += 1
                        set_translation(node, tr)
                    else:
                        print(f"[warn] {uid}: 无译文, 该区域将原样保留")
            if variant == "render":
                for parent, node in to_remove:
                    if isinstance(parent, list):
                        try:
                            parent.remove(node)
                        except ValueError:
                            pass
                    elif isinstance(parent, dict):
                        parent.pop(next((k for k, v in parent.items() if v is node), None), None)

        (out / "render" / f"{stem}.json").write_text(
            json.dumps(doc_render, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "full" / f"{stem}.json").write_text(
            json.dumps(doc_full, ensure_ascii=False, indent=1), encoding="utf-8")
        manifest[stem] = {"regions_matched": matched, "translated": translated, "protected": protected}
        print(f"{stem}: 匹配 {matched} | 译 {translated} | 保护 {protected}")

    Path(out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"渲染任务 -> {out}/render/ (优先) | 兜底 {out}/full/ | 清单 {out}/manifest.json")
    if warn_total:
        print(f"[warn] {warn_total} 页缺标注未处理")


if __name__ == "__main__":
    main()
