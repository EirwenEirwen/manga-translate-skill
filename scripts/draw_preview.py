#!/usr/bin/env python3
"""第2步: 生成预览图(气泡编号/阅读顺序连线/OCR存疑标记)与每泡放大图, 并写标注骨架。

用法(工程根执行):
  # 初次: 从 norm 生成
  python draw_preview.py --detect work/detect --images raw --out work/preview --annotations work/annotations
  # 第3步修正 order/type/speaker 后重画:
  python draw_preview.py --from-annotations --annotations work/annotations --images raw --out work/preview

预览图: 橙框=正常, 红框=OCR存疑, 编号=阅读顺序, 青线=顺序连线
放大图: work/preview/crops/<页>/b<id>.png (核对原文用, 仅初次模式生成)
标注骨架: work/annotations/<页>.json (已存在则跳过, 绝不覆盖人工编辑)
"""
import argparse
import json
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("缺 Pillow: python -m pip install pillow")

TYPE_TAG = {"dialogue": "对", "thought": "心", "narration": "旁", "onomatopoeia": "拟",
            "sign": "牌", "bg_text": "背", "unclear": "?",
            "axis": "轴", "legend": "例", "caption": "题", "callout": "注",
            "table": "表", "title": "标", "other": "他"}
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")


def natural_key(s):
    parts, num = [], ""
    for ch in str(s):
        if ch.isdigit():
            num += ch
        else:
            if num:
                parts.append((1, int(num)))
                num = ""
            parts.append((0, ch))
    if num:
        parts.append((1, int(num)))
    return parts


def cluster_order(bubbles):
    """初稿阅读顺序(日漫习惯): 按 x 区间重叠聚类成列, 列靠右先读, 列内按 y。
    只是启发式, 跨页大格/斜排交给第3步人工修。"""
    n = len(bubbles)
    if n == 0:
        return []
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            ax1, ay1, ax2, ay2 = bubbles[i]["bbox"]
            bx1, by1, bx2, by2 = bubbles[j]["bbox"]
            overlap = min(ax2, bx2) - max(ax1, bx1)
            minw = min(ax2 - ax1, bx2 - bx1)
            if minw > 0 and overlap > 0.5 * minw:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    clusters = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    def cluster_x(idxs):
        return sum(bubbles[i]["bbox"][0] + bubbles[i]["bbox"][2] for i in idxs) / (2 * len(idxs))

    order = []
    for idxs in sorted(clusters.values(), key=cluster_x, reverse=True):
        idxs.sort(key=lambda i: (bubbles[i]["bbox"][1] + bubbles[i]["bbox"][3]) / 2)
        order.extend(idxs)
    return order


def ocr_flags(b):
    flags = []
    conf = b.get("ocr_conf")
    text = (b.get("ocr_text") or "").strip()
    if conf is not None and conf < 0.85:
        flags.append("ocr_uncertain")
    if len(text) < 2:
        flags.append("ocr_short_or_empty")
    if any(ch in text for ch in "□■\ufffd"):
        flags.append("ocr_placeholder")
    return flags


def load_font(size):
    for name in ("msyh.ttc", "msyhbd.ttc", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    try:
        return ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", size)
    except Exception:
        pass
    return ImageFont.load_default()


def find_image(images_dir, stem, hint=None):
    if hint:
        p = Path(hint)
        if p.exists():
            return p
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


def draw_preview(img, bubbles, order_ids, flagged, page_label, draw_lines=True):
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    thick = max(2, w // 700)
    fs = max(16, min(40, w // 32))
    font = load_font(fs)
    small = load_font(max(12, fs // 2))

    centers = {b["id"]: ((b["bbox"][0] + b["bbox"][2]) // 2, (b["bbox"][1] + b["bbox"][3]) // 2)
               for b in bubbles}
    if draw_lines:
        for a, b in zip(order_ids, order_ids[1:]):
            if a in centers and b in centers:
                d.line([centers[a], centers[b]], fill=(0, 190, 255, 190), width=thick)

    def chip(xy, text, fill, f):
        x, y = xy
        bb = d.textbbox((0, 0), text, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        pad = 3
        x = max(0, min(int(x), w - tw - 2 * pad))
        y = max(0, min(int(y), h - th - 2 * pad))
        d.rectangle([x, y, x + tw + 2 * pad, y + th + 2 * pad], fill=fill)
        d.text((x + pad - bb[0], y + pad - bb[1]), text, font=f, fill=(255, 255, 255, 255))

    for b in bubbles:
        x1, y1, x2, y2 = b["bbox"]
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        uncertain = b["id"] in flagged
        color = (255, 60, 60, 255) if uncertain else (255, 150, 0, 255)
        d.rectangle([x1, y1, x2, y2], outline=color, width=thick)
        seq = order_ids.index(b["id"]) + 1
        label = str(seq)
        extra = ""
        if b.get("type"):
            extra += TYPE_TAG.get(b["type"], "?")
        if b.get("speaker"):
            extra += " " + str(b["speaker"])[:8]
        ty = y1 - fs - 10 if y1 - fs - 10 > 0 else y1 + 2
        chip((x1, ty), label, (20, 20, 20, 235), font)
        if extra:
            chip((x1 + int(fs * (len(label) + 0.8)), ty), extra, (20, 20, 20, 215), small)
        if uncertain:
            chip((x2 - int(fs * 2.2), ty), "OCR?", (200, 0, 0, 235), small)

    legend = f"{page_label} · N={len(bubbles)} · 青=顺序线 红=OCR存疑"
    lb = d.textbbox((6, 4), legend, font=small)
    d.rectangle([0, 0, lb[2] + 8, lb[3] + 8], fill=(0, 0, 0, 200))
    d.text((6, 4), legend, font=small, fill=(255, 255, 255, 255))

    out = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    m = max(out.size)
    if m > 2000:
        r = 2000 / m
        out = out.resize((int(out.width * r), int(out.height * r)), Image.LANCZOS)
    return out


def save_crops(img, bubbles, cdir):
    cdir.mkdir(parents=True, exist_ok=True)
    for b in bubbles:
        x1, y1, x2, y2 = b["bbox"]
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        pad = int(max(x2 - x1, y2 - y1) * 0.25) + 6
        cx1, cy1 = max(0, x1 - pad), max(0, y1 - pad)
        cx2, cy2 = min(img.width, x2 + pad), min(img.height, y2 + pad)
        if cx2 <= cx1 or cy2 <= cy1:
            continue
        crop = img.crop((cx1, cy1, cx2, cy2))
        m = max(crop.size)
        if m < 420:
            f = min(3.0, 420 / m)
            crop = crop.resize((int(crop.width * f), int(crop.height * f)), Image.LANCZOS)
        crop.save(cdir / f"b{b['id']}.png")


def main():
    ap = argparse.ArgumentParser(description="生成预览图", epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--detect", help="work/detect 目录(含 *.norm.json)")
    ap.add_argument("--annotations", help="标注目录")
    ap.add_argument("--from-annotations", action="store_true",
                    help="从标注文件重画(反映修正后的 order/type/speaker)")
    ap.add_argument("--images", default="raw", help="原图目录")
    ap.add_argument("--out", default="work/preview", help="预览输出目录")
    ap.add_argument("--no-crops", action="store_true", help="不生成放大图")
    ap.add_argument("--mode", choices=("manga", "paper"), default="manga",
                    help="manga=画阅读顺序连线; paper=论文图, 无顺序连线, 编号即检测 id")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ann_dir = Path(args.annotations) if args.annotations else None

    if args.from_annotations:
        if not ann_dir:
            sys.exit("--from-annotations 需要 --annotations")
        pages = sorted(ann_dir.glob("*.json"), key=lambda p: natural_key(p.stem))
    else:
        if not args.detect:
            sys.exit("需要 --detect 或 --from-annotations")
        pages = sorted(Path(args.detect).glob("*.norm.json"), key=lambda p: natural_key(p.stem))
    if not pages:
        sys.exit("没找到页面输入(norm json 或标注 json)")

    for pf in pages:
        data = json.loads(pf.read_text(encoding="utf-8"))
        stem = pf.stem[:-5] if pf.stem.endswith(".norm") else pf.stem
        bubbles = data.get("bubbles", [])
        if not bubbles:
            print(f"[warn] {stem}: 0 个气泡, 跳过")
            continue
        img_path = find_image(args.images, stem, data.get("image"))
        if not img_path:
            print(f"[warn] {stem}: 找不到图片, 跳过")
            continue
        img = Image.open(img_path).convert("RGB")

        if args.from_annotations:
            order_ids = [b["id"] for b in sorted(bubbles, key=lambda b: (b.get("order") or 10**9, b["id"]))]
            flagged = {b["id"] for b in bubbles if "ocr_uncertain" in (b.get("flags") or [])}
            make_crops = False
        else:
            flagged = set()
            if args.mode == "paper":
                order_ids = sorted(b["id"] for b in bubbles)
                for b in bubbles:
                    fl = ocr_flags(b)
                    if fl:
                        b["flags"] = fl
                        flagged.add(b["id"])
            else:
                idx_order = cluster_order(bubbles)
                order_ids = [bubbles[i]["id"] for i in idx_order]
                bmap = {b["id"]: b for b in bubbles}
                for seq, bid in enumerate(order_ids, 1):
                    b = bmap[bid]
                    b["order"] = seq
                    fl = ocr_flags(b)
                    if fl:
                        b["flags"] = fl
                        flagged.add(bid)
            make_crops = not args.no_crops
            if ann_dir:
                ann_dir.mkdir(parents=True, exist_ok=True)
                apf = ann_dir / f"{stem}.json"
                if apf.exists():
                    print(f"[skip] 标注已存在, 不覆盖: {apf}")
                else:
                    for b in bubbles:
                        b.setdefault("source", "")
                        b.setdefault("type", "")
                        b.setdefault("speaker", "")
                        b.setdefault("audience", "")
                        b.setdefault("keep_original", False)
                        b.setdefault("notes", "")
                    apf.write_text(json.dumps(
                        {"page": data.get("page") or stem, "image": img_path.as_posix(),
                         "bubbles": bubbles}, ensure_ascii=False, indent=1), encoding="utf-8")

        prev = draw_preview(img, bubbles, order_ids, flagged, stem, draw_lines=(args.mode != "paper"))
        prev.save(out / f"{stem}.jpg", quality=90)
        if make_crops:
            save_crops(img, bubbles, out / "crops" / stem)
        print(f"{stem}: 顺序 {'->'.join(map(str, order_ids))} | 存疑 {sorted(flagged)}")

    print(f"预览图 -> {out}/  (放大图在 {out}/crops/<页>/b<id>.png)")


if __name__ == "__main__":
    main()
