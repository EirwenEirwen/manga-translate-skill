#!/usr/bin/env python3
"""第4步: 分批翻译。每批提示词自动携带 术语表+通用规则+上一批中日对照; 状态即文件, 支持中断续接。

用法(工程根执行):
  python batch_translate.py plan --annotations work/annotations --out work/translation \
      --glossary work/glossary.md --rules <translation-guide.md> [--batch-size 20]
  python batch_translate.py run [--all] --out work/translation \
      [--glossary work/glossary.md] [--rules <translation-guide.md>]
  python batch_translate.py merge --out work/translation
  python batch_translate.py status --out work/translation

翻译执行方式二选一:
  1) API: 环境变量 MANGA_LLM_BASE_URL / MANGA_LLM_API_KEY / MANGA_LLM_MODEL (OpenAI 兼容 /chat/completions)
  2) 手动: run 生成 batches/batch_NNN.prompt.md 并停下; Agent 按提示词翻译,
     写 batches/batch_NNN.result.json 后重跑 run, 直到没有待译批次。

人工修正统一写 work/translation/overrides.json ({"页:气泡id": "译文"}), 由 merge 最后叠加。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

SYSTEM_PROMPT = ("你是资深日中漫画翻译。严格遵守用户给出的术语表与规则，只输出规定格式的 JSON，"
                 "不输出任何解释或多余文本。")


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


def read_text(path):
    if not path:
        return ""
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def load_items(ann_dir):
    """按阅读顺序收集待译气泡。存在 _reading_order.txt 时按它排序(跨页大格用)。"""
    items = []
    ann = Path(ann_dir)
    for pf in sorted(ann.glob("*.json"), key=lambda p: natural_key(p.stem)):
        data = json.loads(pf.read_text(encoding="utf-8"))
        for b in data.get("bubbles", []):
            src = (b.get("source") or "").strip()
            if b.get("keep_original") or not src:
                continue
            items.append({"uid": f"{pf.stem}:{b['id']}", "page": pf.stem,
                          "order": b.get("order") or 0, "type": b.get("type") or "unclear",
                          "speaker": b.get("speaker") or "", "source": src})
    ro = ann / "_reading_order.txt"
    if ro.exists():
        seq = {ln.strip(): i for i, ln in enumerate(ro.read_text(encoding="utf-8").splitlines()) if ln.strip()}
        items.sort(key=lambda it: seq.get(it["uid"], 10**9))
    else:
        items.sort(key=lambda it: (natural_key(it["page"]), it["order"] or 0))
    return items


def result_path(out, batch_no):
    return Path(out) / "batches" / f"batch_{batch_no:03d}.result.json"


def prompt_path(out, batch_no):
    return Path(out) / "batches" / f"batch_{batch_no:03d}.prompt.md"


def build_prompt(glossary, rules, prev_pairs, items, batch_no, total_batches):
    lines = [f"# 漫画翻译 · 批次 {batch_no:03d}/{total_batches:03d}", ""]
    if glossary.strip():
        lines += ["## 作品设定与术语表", "```markdown", glossary.strip(), "```", ""]
    if rules.strip():
        lines += ["## 通用翻译规则（必须遵守）", "```markdown", rules.strip(), "```", ""]
    if prev_pairs:
        lines += ["## 上一批中日对照（术语、自称、语气保持一致）"]
        lines += [f"- {s} → {t}" for s, t in prev_pairs[-30:]]
        lines.append("")
    lines += ["## 本批待译（按阅读顺序）", "",
              "uid | 类型 | 说话人 | 原文", "--- | --- | --- | ---"]
    for it in items:
        lines.append(f"{it['uid']} | {it['type']} | {it['speaker'] or '—'} | {it['source']}")
    lines += ["", "## 输出要求", "- 只输出如下格式的 JSON（本批每个 uid 一条，顺序与上表一致）：",
              "```json",
              '{"translations": [{"uid": "' + items[0]["uid"] + '", "translation": "<中文译文>"}, ...]}',
              "```",
              "- 中文口语自然，长度尽量不超过原文 1.4 倍；拟声词从短",
              "- 术语/自称/称呼严格遵守术语表与上一批对照；除规则明确允许保留的（公式、符号、单位、专名等）外，译文不得残留源语言文字", ""]
    return "\n".join(lines)


def parse_result(content, valid_uids):
    valid = list(valid_uids)
    s = (content or "").strip()
    s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s.strip(), flags=re.M).strip()
    data = None
    try:
        data = json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}|\[.*\]", s, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = None
    if data is None:
        return None, "输出不是合法 JSON"
    items = data.get("translations") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None, "缺少 translations 数组"
    got = {}
    for it in items:
        if not isinstance(it, dict) or "uid" not in it:
            continue
        uid, tr = str(it["uid"]), str(it.get("translation", "")).strip()
        if uid in valid and tr:
            got[uid] = tr
    missing = [u for u in valid if u not in got]
    if missing:
        return None, "缺少 uid: " + ", ".join(missing[:5]) + (" 等" if len(missing) > 5 else "")
    return {u: got[u] for u in valid}, None


def load_result(out, batch):
    """读取并校验某批结果, 返回 (dict|None, err|None)。"""
    rp = result_path(out, batch["batch"])
    if not rp.exists():
        return None, "not-done"
    try:
        raw = json.loads(rp.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"结果文件损坏: {e}"
    uids = [it["uid"] for it in batch["items"]]
    return parse_result(json.dumps(raw, ensure_ascii=False), uids)


def call_llm(base_url, api_key, model, user_prompt):
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": user_prompt}],
        "temperature": 0.3,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def api_env():
    return (os.environ.get("MANGA_LLM_BASE_URL"), os.environ.get("MANGA_LLM_API_KEY"),
            os.environ.get("MANGA_LLM_MODEL"))


def cmd_plan(args):
    items = load_items(args.annotations)
    if not items:
        sys.exit("没有待译气泡(检查 annotations: source 为空或全 keep_original)")
    out = Path(args.out)
    (out / "batches").mkdir(parents=True, exist_ok=True)
    batches, i = [], 1
    for start in range(0, len(items), args.batch_size):
        batches.append({"batch": i, "items": items[start:start + args.batch_size]})
        i += 1
    plan = {"meta": {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "batch_size": args.batch_size,
                     "glossary": args.glossary or "", "rules": args.rules or "",
                     "total_items": len(items)},
            "batches": batches}
    pf = out / "plan.json"
    if pf.exists() and not args.force:
        sys.exit(f"已存在 {pf}; 重建请加 --force (旧批次结果按 uid 仍可续用)")
    pf.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"共 {len(items)} 条待译, 分 {len(batches)} 批 (每批 {args.batch_size})")
    for b in batches:
        print(f"  batch_{b['batch']:03d}: {len(b['items'])} 条  [{b['items'][0]['uid']} .. {b['items'][-1]['uid']}]")
    print(f"plan -> {pf}")


def cmd_run(args):
    out = Path(args.out)
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    glossary, rules = read_text(args.glossary or plan["meta"].get("glossary")), \
        read_text(args.rules or plan["meta"].get("rules"))
    base, key, model = api_env()
    api_mode = bool(base and key and model)
    total = len(plan["batches"])

    while True:
        pending = None
        for b in plan["batches"]:
            res, err = load_result(out, b)
            if err == "not-done" or err:
                pending = (b, res, err)
                break
        if pending is None:
            print("全部批次已完成。执行 merge 合并。")
            return
        b, existing, err = pending
        uids = [it["uid"] for it in b["items"]]
        if existing is None and err and err != "not-done":
            print(f"[warn] batch_{b['batch']:03d} 结果无效({err}), 将重做")

        prev = None
        for pb in plan["batches"]:
            if pb["batch"] >= b["batch"]:
                break
            res, e = load_result(out, pb)
            if res:
                prev = (pb, res)
        prev_pairs = []
        if prev:
            pb, res = prev
            for it in pb["items"]:
                if it["uid"] in res:
                    prev_pairs.append((it["source"], res[it["uid"]]))

        prompt = build_prompt(glossary, rules, prev_pairs, b["items"], b["batch"], total)
        pp = prompt_path(out, b["batch"])
        pp.parent.mkdir(parents=True, exist_ok=True)
        pp.write_text(prompt, encoding="utf-8")
        print(f"batch_{b['batch']:03d}: {len(b['items'])} 条 | 提示词 -> {pp}")

        if not api_mode:
            rp = result_path(out, b["batch"])
            tpl = {"translations": [{"uid": u, "translation": ""} for u in uids]}
            print("手动模式: 按提示词翻译, 结果写到下面文件后重跑 run:")
            print(f"  {rp}")
            print("  模板: " + json.dumps(tpl, ensure_ascii=False)[:200] + " …")
            return
        try:
            content = call_llm(base, key, model, prompt)
            parsed, perr = parse_result(content, uids)
            if perr and "缺少 uid" in perr:
                content = call_llm(base, key, model,
                                   prompt + f"\n\n注意: 上一次输出{perr}, 请补全后重新输出完整 JSON。")
                parsed, perr = parse_result(content, uids)
            if perr:
                print(f"[error] batch_{b['batch']:03d} {perr}; 保留待译, 重跑 run 重试")
                return
            rp = result_path(out, b["batch"])
            rp.write_text(json.dumps(
                {"batch": b["batch"],
                 "translations": [{"uid": u, "translation": t} for u, t in parsed.items()]},
                ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  API 翻译完成 -> {rp}")
        except Exception as e:
            print(f"[error] API 调用失败: {e}; 断点已保留, 重跑 run 续接")
            return
        if not args.all:
            return


def cmd_merge(args):
    out = Path(args.out)
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    merged, missing = {}, []
    for b in plan["batches"]:
        res, err = load_result(out, b)
        if not res:
            missing.append(b["batch"])
            continue
        for uid, tr in res.items():
            merged[uid] = {"translation": tr, "src": f"batch_{b['batch']:03d}"}
    ov = out / "overrides.json"
    if ov.exists():
        for uid, tr in json.loads(ov.read_text(encoding="utf-8")).items():
            merged[uid] = {"translation": str(tr), "src": "override"}
    (out / "merged.json").write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    n_ov = sum(1 for v in merged.values() if v["src"] == "override")
    print(f"merged: {len(merged)} 条 (override {n_ov} 条) -> {out / 'merged.json'}")
    if missing:
        print("[warn] 未完成批次: " + ", ".join(f"{m:03d}" for m in missing))
        sys.exit(1)


def cmd_status(args):
    out = Path(args.out)
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    for b in plan["batches"]:
        res, err = load_result(out, b)
        state = "done" if res else ("invalid: " + str(err) if err != "not-done" else "pending")
        print(f"batch_{b['batch']:03d}: {len(b['items']):3d} 条  {state}")
    ov = out / "overrides.json"
    if ov.exists():
        print(f"overrides: {len(json.loads(ov.read_text(encoding='utf-8')))} 条")


def main():
    ap = argparse.ArgumentParser(description="分批翻译调度", epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--annotations", default="work/annotations")
    p.add_argument("--out", default="work/translation")
    p.add_argument("--glossary")
    p.add_argument("--rules")
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_plan)
    p = sub.add_parser("run")
    p.add_argument("--out", default="work/translation")
    p.add_argument("--glossary")
    p.add_argument("--rules")
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("merge")
    p.add_argument("--out", default="work/translation")
    p.set_defaults(func=cmd_merge)
    p = sub.add_parser("status")
    p.add_argument("--out", default="work/translation")
    p.set_defaults(func=cmd_status)
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args.func(args)


if __name__ == "__main__":
    main()
