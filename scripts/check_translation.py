#!/usr/bin/env python3
"""第5步: 翻译结果自动检查 + 生成逐页审阅对照表。

检查项:
  [ERROR] 漏翻   需要翻译但译文缺失 / 译文≈原文
  [WARN]  超长   中文长度 > max(日文长度*2.2, 40) —— 排进气泡会挤, 逐条确认
  [WARN]  同句异译 相同原文(去标点空白)出现多个不同译文 —— 以 glossary 定夺
  [WARN]  假名残留 日文来源的译文里含假名
  [WARN]  术语未对齐 原文含术语表词条但译文没出现既定译名

输出: --report 检查报告; --review-dir 逐页审阅表(按阅读顺序排好, 供 Agent 对照原图审)
退出码: 有 ERROR 时为 1。
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

KANA = re.compile(r"[ぁ-んァ-ン]")
STRIP_CHARS = "。、，,．.！!？?…‥ー—―‐-『』「」『』()（）[]【】<>《》・~～;；:：\"'“”‘’ \t"


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


def norm_text(s):
    s = unicodedata.normalize("NFKC", s or "")
    return "".join(ch for ch in s if ch not in STRIP_CHARS and not ch.isspace())


def cjk_len(s):
    return len([c for c in (s or "") if not c.isspace()])


def parse_glossary_terms(path):
    """解析 glossary.md 专有名词表小节: | 原文 | 译名 | ..."""
    terms = []
    if not path or not Path(path).exists():
        return terms
    in_sec = False
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            in_sec = ("专有名词" in line) or ("术语" in line and "表" in line)
            continue
        if not in_sec or not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0] or set(cells[0]) <= {"-", " "}:
            continue
        alts = [a for a in re.split(r"[/／]", cells[1]) if a.strip()]
        if alts:
            terms.append((cells[0], alts))
    return terms


def main():
    ap = argparse.ArgumentParser(description="翻译自动检查", epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--annotations", default="work/annotations")
    ap.add_argument("--merged", default="work/translation/merged.json")
    ap.add_argument("--glossary", default="work/glossary.md")
    ap.add_argument("--report", default="work/translation/check_report.md")
    ap.add_argument("--review-dir", default="work/review")
    ap.add_argument("--preview-dir", default="work/preview")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    merged = {}
    mp = Path(args.merged)
    if mp.exists():
        merged = {uid: v.get("translation", "") if isinstance(v, dict) else str(v)
                  for uid, v in json.loads(mp.read_text(encoding="utf-8")).items()}
    terms = parse_glossary_terms(args.glossary)

    errors, warns = [], []
    pages = sorted(Path(args.annotations).glob("*.json"), key=lambda p: natural_key(p.stem))
    if not pages:
        sys.exit(f"没有标注文件: {args.annotations}")

    # 同句异译检测: norm(原文) -> {译文: [uid]}
    by_src = {}
    for pf in pages:
        data = json.loads(pf.read_text(encoding="utf-8"))
        for b in data.get("bubbles", []):
            uid = f"{pf.stem}:{b['id']}"
            if b.get("keep_original") or not (b.get("source") or "").strip():
                continue
            tr = merged.get(uid, "")
            if tr:
                by_src.setdefault(norm_text(b["source"]), {}).setdefault(tr, []).append(uid)
    for src_norm, tr_map in by_src.items():
        if len(tr_map) > 1:
            detail = " | ".join(f"{t!r} <- {','.join(us)}" for t, us in tr_map.items())
            warns.append(f"同句异译: {detail}")

    warn_uids = {}
    for pf in pages:
        data = json.loads(pf.read_text(encoding="utf-8"))
        for b in data.get("bubbles", []):
            uid = f"{pf.stem}:{b['id']}"
            src = (b.get("source") or "").strip()
            if b.get("keep_original"):
                continue
            if not src:
                if b.get("type") != "onomatopoeia":
                    errors.append(f"{uid}: source 为空且未 keep_original (type={b.get('type')})")
                continue
            tr = (merged.get(uid) or "").strip()
            if not tr:
                errors.append(f"{uid}: 漏翻 (该译未译)")
                continue
            if norm_text(tr) == norm_text(src):
                errors.append(f"{uid}: 译文与原文相同, 疑似未翻译")
                continue
            if KANA.search(tr):
                warns.append(f"{uid}: 假名残留 -> {tr[:30]}")
                warn_uids.setdefault(pf.stem, set()).add(uid)
            if cjk_len(tr) > max(2.2 * cjk_len(src), 15):
                warns.append(f"{uid}: 译文超长 ({cjk_len(src)}->{cjk_len(tr)} 字) type={b.get('type')}")
                warn_uids.setdefault(pf.stem, set()).add(uid)
            for term, alts in terms:
                if term in src and not any(a in tr for a in alts):
                    warns.append(f"{uid}: 术语未对齐 '{term}' 应含 {'/'.join(alts)}")
                    warn_uids.setdefault(pf.stem, set()).add(uid)

    # 报告
    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    with rp.open("w", encoding="utf-8") as f:
        f.write(f"# 翻译检查报告 ({len(errors)} ERROR / {len(warns)} WARN)\n\n")
        f.write("## ERROR（必须清零）\n")
        if errors:
            f.writelines(f"- {e}\n" for e in errors)
        else:
            f.write("（无）\n")
        f.write("\n## WARN（逐条确认）\n")
        if warns:
            f.writelines(f"- {w}\n" for w in warns)
        else:
            f.write("（无）\n")

    # 逐页审阅表
    rdir = Path(args.review_dir)
    rdir.mkdir(parents=True, exist_ok=True)
    for pf in pages:
        data = json.loads(pf.read_text(encoding="utf-8"))
        rows = sorted(data.get("bubbles", []), key=lambda b: (b.get("order") or 10**9, b["id"]))
        lines = [f"# 审阅 · {pf.stem}",
                 f"- 预览图: ../preview/{pf.stem}.jpg （放大图: ../preview/crops/{pf.stem}/b<id>.png）"]
        if pf.stem in warn_uids:
            lines.append(f"- 需重点复核: {', '.join(sorted(warn_uids[pf.stem]))}")
        lines += ["", "| # | uid | 类型 | 说话人 | 原文 | 译文 |", "|---|---|---|---|---|---|"]
        for b in rows:
            uid = f"{pf.stem}:{b['id']}"
            src = (b.get("source") or "").replace("|", "\\|")
            if b.get("keep_original"):
                tr = "（保留原文）"
            else:
                tr = (merged.get(uid) or "（缺）").replace("|", "\\|")
                if uid in warn_uids.get(pf.stem, set()):
                    tr = "⚠ " + tr
            sp = (b.get("speaker") or "").replace("|", "\\|") or "—"
            lines.append(f"| {b.get('order') or b['id']} | {uid} | {b.get('type') or '?'} | {sp} | {src} | {tr} |")
        lines.append("")
        lines.append("审阅重点：漫画=人称/肯否/因果；论文图=术语一致/不译元素/长度。")
        (rdir / f"{pf.stem}.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"ERROR {len(errors)} | WARN {len(warns)} | 审阅表 {len(pages)} 页 -> {rdir}/")
    print(f"报告 -> {rp}")
    if errors:
        for e in errors[:10]:
            print("  [ERROR]", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
