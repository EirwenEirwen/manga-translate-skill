# manga-translate

**ZCode 漫画/论文图翻译 Skill** —— 一个让 AI Agent 稳定完成「日文漫画整册汉化」与「英文论文插图翻译」的 Skill，内置人机分工约束、坐标驱动的预览标注体系、可断点续传的分批翻译、机器+人工双重质检，以及随使用持续沉淀的实战经验库。

> 设计目标：Agent 擅长"看懂"，不擅长"定位"。本 Skill 把像素级定位交给专门的工具（manga-translator-ui），把理解、校对、翻译、审校交给 Agent，把重复性调度交给脚本——三者各司其职，谁也不越界。

---

## 目录

- [功能特性](#功能特性)
- [整体架构](#整体架构)
- [目录结构](#目录结构)
- [环境要求与安装](#环境要求与安装)
- [快速开始](#快速开始)
- [流程 A：漫画整册汉化（日→中）](#流程-a漫画整册汉化日中)
- [流程 B：论文图翻译（英→中）](#流程-b论文图翻译英中单列流程)
- [脚本参考](#脚本参考)
- [数据格式](#数据格式)
- [质量保障机制](#质量保障机制)
- [实测数据与已知限制](#实测数据与已知限制)
- [经验沉淀机制](#经验沉淀机制)
- [常见问题 FAQ](#常见问题-faq)
- [致谢](#致谢)

---

## 功能特性

- **双流程自动分流**：Agent 先看图判断是漫画页还是论文图，再走各自的处理管线（SKILL.md 中两条流程单列）。
- **坐标只信工具**：气泡/文字区域坐标一律来自 manga-translator-ui 的检测，Agent 不从零猜坐标；论文图的印刷体小字漏检允许 Agent 补区域，但强制标注复核。
- **可视预览 + 逐页检视**：脚本把编号、阅读顺序连线、OCR 存疑标记画到原图上生成预览图，并为每个气泡/文字块导出放大图，Agent 逐页核对原文、修正阅读顺序、标注说话人与气泡类型。
- **分批翻译带上下文**：每批提示词自动携带术语表、翻译守则全文和**上一批的中日对照**，保证自称、称呼、术语全册一致；批次状态即文件，**任意中断后重跑即续接**。
- **双模式翻译执行**：API 模式（任意 OpenAI 兼容接口，环境变量配置）或手动模式（Agent 自己逐批翻译）。
- **机器 + 人工双重质检**：脚本自动查漏翻、译文超长、同句异译、假名残留、术语未对齐；Agent 再按阅读顺序对照原图逐页审校人称/肯否/因果；只有"放大也看不清或多解"的才升级给用户。
- **安全写回与渲染**：译文写回工具工程 JSON，`keep_original` 区域（残缺拟声词、公式、专名等）从渲染版剔除，防乱码防误伤。
- **论文图专用管线**：paddleocr 引擎、检测调参、术语表+不译清单、默认交付对照表——针对"漫画检测器遇到印刷体"的全部坑都有预案。
- **经验持续沉淀**：每完成一本/一批，可泛化的经验自动归档进 `references/lessons.md`，Skill 越用越准。

## 整体架构

```
                    ┌─────────────────────────────────────────┐
                    │              SKILL.md (主控)             │
                    │   图类分拣 → 流程A(漫画) / 流程B(论文图)   │
                    └───────────────┬─────────────────────────┘
                                    │ 指挥
          ┌─────────────────────────┼──────────────────────────┐
          ▼                         ▼                          ▼
┌──────────────────┐   ┌────────────────────────┐   ┌──────────────────┐
│ manga-translator-│   │   scripts/*.py (6个)    │   │   Agent(你)      │
│ ui (外部工具)     │   │  normalize/draw_preview │   │  看图·校对·翻译   │
│ 检测·OCR·擦除·渲染│   │  batch/check/build      │   │  标注·审校·决策   │
└──────────────────┘   │  render_with_tool       │   └──────────────────┘
                       └────────────────────────┘
```

三条铁律贯穿全流程（违反即废稿）：

1. **坐标只能来自工具检测**（论文图印刷体漏检除外，补区域必须标注 `agent补充` 供复核）。
2. **对白/心理/旁白必须全部翻译**，无论 OCR 多差——读放大图恢复原文；只有无法识别完整的拟声词可保留原文。
3. **交稿前必须过自动检查 + 逐页审阅**，发现错误当场修，只有看不清/多解的才问用户。

## 目录结构

```
manga-translate/
├── SKILL.md                        # 主控文档：分工铁律 + 流程A/B + 数据格式 + 速查
├── README.md                       # 本文档
├── references/
│   ├── manga-translator-ui.md      # 工具对接手册：安装/启动/检测/渲染/环境记录
│   ├── translation-guide.md        # 日中翻译守则（随每批提示词下发）
│   ├── paper-figures.md            # 论文图管线细节：实测参数/局限/交付方式
│   ├── paper-rules.md              # 英中论文图翻译规则（随批次下发）
│   └── lessons.md                  # 经验沉淀库（按书追加，持续增长）
└── scripts/
    ├── normalize_detect.py         # 检测JSON归一化（识别多种工具输出格式）
    ├── draw_preview.py             # 预览图+放大图+标注骨架（--mode paper）
    ├── batch_translate.py          # 分批调度：plan/run/merge/status
    ├── check_translation.py        # 自动质检 + 生成逐页审阅表
    ├── build_output.py             # 译文写回工具工程JSON（render/full双版本）
    └── render_with_tool.py         # 渲染驱动（调工具Python API按JSON出图）
```

## 环境要求与安装

### 1. Skill 本体

把本目录放到 ZCode 的技能发现路径之一：

- `~/.agents/skills/manga-translate/`（用户级，推荐）
- `<project>/.agents/skills/manga-translate/`（项目级）

依赖仅 **Python 3.9+ 与 Pillow**（脚本用）：

```bash
python -m pip install pillow
```

### 2. manga-translator-ui（外部工具）

本 Skill 的检测/OCR/擦除/渲染都依赖 [manga-translator-ui](https://github.com/hgmzhn/manga-translator-ui)（manga-image-translator 分支）。Windows CPU 版安装实录：

```bash
# 1) 克隆（建议非系统盘，模型+依赖约 2-3 GB）
git clone --depth 1 https://github.com/hgmzhn/manga-translator-ui /d/tools/manga-translator-ui
cd /d/tools/manga-translator-ui

# 2) 依赖（uv 管理；无 GPU 用 cpu 组）
#    C 盘紧张时把 uv 缓存导到大盘：
export UV_CACHE_DIR="D:/uv-cache" UV_PYTHON_INSTALL_DIR="D:/uv-python"
uv sync --no-default-groups --group cpu

# 3) 验证
PYTHONUTF8=1 uv run --no-sync python -m manga_translator local --help
```

**国内网络补丁**（GitHub release 资产直连失败时）：`pyproject.toml` 与 `uv.lock` 中 pydensecrf 的 3 个 wheel URL 需替换为 `https://gh-proxy.com/https://github.com/...` 镜像（sha256 校验一致，已验证）。`git pull` 更新后若 sync 失败需重打补丁。模型权重首次运行时自动下载（检测模型 ~295MB；PaddleOCR 走 ModelScope 国内直连）。

> GPU 机器把配置里的 `use_gpu` 打开并把 sync 组换成对应 CUDA 版即可，参数详见上游 README。

安装完成后**不需要人工启动任何服务**：Skill 的 CLI 用法是每条命令独立起进程跑完即退；对接成功的实测命令会记录在 `references/manga-translator-ui.md` 末尾的「环境记录」。

## 快速开始

在 ZCode 里触发（Skill 已装好的前提下）：

```
翻译这本漫画 D:\comics\某漫画第1卷          # → 流程 A
帮我把这几张论文插图翻成中文 D:\paper\figs  # → 流程 B
```

Agent 会自动：分拣图类 → 启动工具做检测+OCR → 生成预览图逐页检视 → 建术语表 → 分批翻译 → 双重质检 → 写回渲染（漫画）/出对照表（论文图）→ 沉淀经验。

全部中间产物在工程根的 `work/` 下，**任何时候中断，重发同一句话即可从断点继续**（检测→预览→标注→批次→合并→审阅→成图，做完的步骤不会重做）。

---

## 流程 A：漫画整册汉化（日→中）

| 步 | 做什么 | 谁做 | 关键产物 |
|---|---|---|---|
| 0 | 初始化工程目录、建术语表骨架 | Agent | `work/` 目录树、`glossary.md` |
| 1 | 检测+OCR（只要坐标，不让它翻译） | 工具 CLI | `work/detect/raw_json/*.json` |
| 2 | 归一化 + 生成预览图/放大图/标注骨架 | 脚本 | `*.norm.json`、`preview/*.jpg`、`crops/` |
| 3 | **逐页检视**：修阅读顺序、校对原文、标注说话人/类型 | **Agent（核心）** | `work/annotations/<页>.json` |
| 4 | 分批翻译（携带前批对照，断点续传） | API 或 Agent | `batches/batch_NNN.*`、`merged.json` |
| 5 | 自动检查清零 ERROR + 逐页审阅修语义 | 脚本 + Agent | `check_report.md`、`review/*.md`、`overrides.json` |
| 6 | 写回 + 渲染出图 + 抽查 | 脚本 + 工具 + Agent | `out/render/*.json`、`out/images/*.png` |
| 7 | 复盘，经验进 `lessons.md` | Agent | Skill 内经验库 |

要点：

- **阅读顺序**：初稿是坐标启发式（右→左分列、列内上→下），跨页大格、斜排气泡必然要人工修；单页 order 表达不了的跨页顺序写进 `work/annotations/_reading_order.txt`。
- **气泡类型**：`dialogue` / `thought` / `narration` / `onomatopoeia` / `sign` / `bg_text` / `unclear`；`speaker`/`audience` 用术语表人物表的称呼体系。
- **翻译执行**（第 4 步二选一）：
  - API 模式：`export MANGA_LLM_BASE_URL=... MANGA_LLM_API_KEY=... MANGA_LLM_MODEL=...` 后 `batch_translate.py run --all`；
  - 手动模式：`run` 生成 `batch_NNN.prompt.md` → Agent 翻译写成 `batch_NNN.result.json` → 再 `run`，循环至完成。
- **审阅修错**：统一写 `work/translation/overrides.json`（`{"页:气泡id": "改后译文"}`）→ `merge` → `check`，改完即生效。

## 流程 B：论文图翻译（英→中，单列流程）

与漫画的本质差异：**没有阅读顺序与说话人**，默认交付「逐图对照表」而非重渲染。

| 步 | 做什么 | 与流程 A 的差异 |
|---|---|---|
| 1 | 检测+OCR | **OCR 必须 `paddleocr`**（48px 是日文模型，对英文幻觉日文）；检测调参 `detection_size:3072, box_threshold:0.25, text_threshold:0.4` |
| 2 | 预览 | `draw_preview.py --mode paper`：无顺序连线，编号即检测 id |
| 3 | 标注 | type 换成 `axis/legend/caption/callout/table/title/other`；公式/变量/单位/数值/专名一律 `keep_original`；**工具漏检的图例/轴标签由 Agent 补区域**（notes 标 `agent补充`）；粘连 region 拆开处理 |
| 4 | 分批翻译 | `--rules` 传 `paper-rules.md`；glossary 用论文版（术语表+不译清单） |
| 5 | 检查与交付 | 审阅三查换成：术语跨图一致 / 不译元素没被动 / 译文长度；**默认交付对照表**（`work/review/*.md` 合并） |
| 6 | 渲染（可选） | 仅用户点名要中文图时；写回 JSON 先覆盖到工具标准位置再 `render_with_tool.py`；细线图表有被 inpaint 抹掉的风险，逐区域核对 |

> 为什么论文图默认不渲染：实测中工具对图例条目、轴标签、刻度漏检严重（漫画检测器的固有偏向），Agent 补充的区域不参与重渲染，且曲线图的细线可能被 inpaint 连带抹掉（柱状图安全）。对照表 + 用户在 Origin/matplotlib/PPT 里改源图，是当前最稳的交付方式。

---

## 脚本参考

所有脚本在**工程根**执行，`$SKILL` 指 Skill 安装目录。

### normalize_detect.py — 检测 JSON 归一化

```bash
python "$SKILL/scripts/normalize_detect.py" --in work/detect/raw_json --out work/detect --images raw
python "$SKILL/scripts/normalize_detect.py" --in work/detect/raw_json/001.json --schema-report   # 排查结构
```

- 优先走 manga-translator-ui 的 `*_translations.json` 专有格式（外层按图片路径为键，内层 `regions[].text/lines/prob`）；
- 兜底通用解析：递归识别常见字段名（`text/ocr/content` × `bbox/polygon/rect...`，含嵌套点集）；
- 某页解析出 0 个气泡会显式报错退出——那是配置问题，不是"没文字"。

### draw_preview.py — 预览图 + 放大图 + 标注骨架

```bash
# 初次生成
python "$SKILL/scripts/draw_preview.py" --detect work/detect --images raw --out work/preview --annotations work/annotations
# 标注修正后重画（编号跟随新 order）
python "$SKILL/scripts/draw_preview.py" --from-annotations --annotations work/annotations --images raw --out work/preview
# 论文图模式
python "$SKILL/scripts/draw_preview.py" ... --mode paper
```

- 预览图：橙框=正常，红框+`OCR?`=低置信/可疑，青线=阅读顺序连线（paper 模式不画），编号=顺序；
- 放大图 `preview/crops/<页>/b<id>.png`（放大到 ≥420px，核对原文用）；
- 标注骨架**已存在则跳过，绝不覆盖人工编辑**。

### batch_translate.py — 分批翻译调度

```bash
python "$SKILL/scripts/batch_translate.py" plan   --annotations work/annotations --out work/translation \
      --glossary work/glossary.md --rules "$SKILL/references/translation-guide.md" [--batch-size 20] [--force]
python "$SKILL/scripts/batch_translate.py" run    [--all] [--out work/translation] [--glossary ...] [--rules ...]
python "$SKILL/scripts/batch_translate.py" merge  --out work/translation
python "$SKILL/scripts/batch_translate.py" status --out work/translation
```

- `plan` 按阅读顺序切批（存在 `_reading_order.txt` 时按它排，支持跨页大格）；改了原文/增删气泡后 `plan --force` 重建，旧结果按 uid（`页:气泡id`）续用；
- `run`：API 模式连续跑（`--all`）；手动模式每次生成一个待译批的 `prompt.md` 就停；
- 每批提示词自动包含：术语表全文 + 规则全文 + **上一批中日对照（末 30 条）**；
- `merge` 合并全部批次并叠加 `overrides.json`（人工修正的唯一入口）。

### check_translation.py — 自动质检 + 审阅表

```bash
python "$SKILL/scripts/check_translation.py" --annotations work/annotations \
  --merged work/translation/merged.json --glossary work/glossary.md \
  --report work/translation/check_report.md --review-dir work/review
```

| 级别 | 检查项 | 说明 |
|---|---|---|
| ERROR | 漏翻 | 该译没译 / 译文≈原文（退出码 1） |
| ERROR | source 为空 | 非拟声词的气泡没核对原文 |
| WARN | 译文超长 | 中文字数 > max(原文×2.2, 15) |
| WARN | 同句异译 | 相同原文（去标点）出现多个译法 |
| WARN | 假名残留 | 译文含日文假名（刻意保留需 notes 说明） |
| WARN | 术语未对齐 | 原文含术语表词条但译文缺既定译名（支持 `/` 多选译名） |

同时生成 `work/review/<页>.md`（按阅读顺序的中日对照表，带 ⚠ 标记），供 Agent 对照原图审校。

### build_output.py — 译文写回

```bash
python "$SKILL/scripts/build_output.py" --detect work/detect/raw_json \
  --annotations work/annotations --merged work/translation/merged.json --out work/out
```

- `out/render/`：**剔除** keep_original 区域（防工具重排保留区产生乱码），优先交这个；
- `out/full/`：全量版本，保护区域 `translation`=原文，兜底用；
- 匹配策略：OCR 文本归一化精确匹配 → 多候选/失败时按 bbox IoU≥0.3 兜底。

### render_with_tool.py — 渲染驱动

```bash
# 前置：把 out/render/<名>.json 覆盖到 <图片目录>/manga_translator_work/json/<名>_translations.json
cd /d/tools/manga-translator-ui && PYTHONUTF8=1 \
  uv run --no-sync python "$SKILL/scripts/render_with_tool.py" \
  --images <图/目录> --config <渲染配置.json> -o <输出目录> [--overwrite] [--skip-font-scaling]
```

CLI 没有现成的"按已编辑 JSON 渲染"开关，本脚本直接调工具的 Python API（加载 JSON regions → 蒙版细化 → inpaint → 渲染 → 保存），等价于其编辑器的导出渲染流程。

## 数据格式

### 标注文件 `work/annotations/<页>.json`（核心数据）

```json
{
  "page": "001",
  "image": "raw/001.png",
  "bubbles": [
    {
      "id": 1, "order": 1,
      "bbox": [812, 40, 1040, 210],
      "ocr_text": "今日もいい天気ネ",
      "ocr_conf": 0.71,
      "flags": ["ocr_uncertain"],
      "source": "今日もいい天気だね",
      "type": "dialogue",
      "speaker": "少女A", "audience": "田中",
      "keep_original": false,
      "notes": ""
    }
  ]
}
```

- `id`/`bbox` 来自检测，是**稳定键，任何情况不改**；`ocr_text`/`ocr_conf`/`flags` 保留工具原始输出供追溯；
- `source` 是 Agent 核对后的原文（翻译的输入），`order`/`type`/`speaker`/`audience`/`keep_original`/`notes` 由 Agent 填写；
- 论文图的 type 体系：`axis/legend/caption/callout/table/title/other`，`speaker`/`order` 留空。

### 其他文件速览

| 文件 | 内容 |
|---|---|
| `work/detect/<页>.norm.json` | 归一化坐标+OCR（脚本输入） |
| `work/annotations/_reading_order.txt` | 跨页阅读顺序覆盖（每行 `页:气泡id`） |
| `work/glossary.md` | 作品档案：剧情概要/人物表（自称·语气·称呼）/专有名词表；论文图版为术语表+不译清单 |
| `work/translation/plan.json` | 分批计划 |
| `work/translation/batches/batch_NNN.{prompt.md,result.json}` | 每批提示词/结果（状态即文件，断点续传的载体） |
| `work/translation/overrides.json` | 人工修正 `{"页:气泡id": "译文"}` |
| `work/translation/merged.json` | 最终译文（含 overrides 叠加） |
| `work/questions.md` | 仅"放大也看不清/多解"的问题，交用户裁决 |

## 质量保障机制

1. **检测验收**：抽 2–3 页核对 region 数与目测相当，预览图框必须套在目标上；
2. **原文零脑补**：每个 `source` 都要对照放大图核实，OCR 只认一半的句子必须恢复完整；
3. **翻译一致性**：术语表 + 前批对照双保险，check 五项机检兜底；
4. **语义三查**（漫画：人称对 speaker / 肯否不翻反 / 因果连词成立；论文图：术语跨图一致 / 不译元素未动 / 长度可控）；
5. **成图抽查**：渲染后逐页看乱码、溢出、误擦；
6. **升级通道**：只有放大原图仍看不清或存在多种合理解读时才写 `work/questions.md` 交用户。

## 实测数据与已知限制

以下均来自 2026-10 的真实运行（CPU 版，记录于 `references/` 各文件）：

| 项目 | 结果 |
|---|---|
| 日文漫画页检测+OCR | 4/4 文字块检出，置信度 0.99+，竖排方向识别正确 |
| 渲染回排（漫画） | 日文擦净，中文按原方向回排（竖排气泡竖排、拟声词大字横排） |
| 论文图检测覆盖 | 标题/图题/大标注可检出；**图例条目、轴标签、刻度基本漏检** |
| 论文图 OCR | `48px` 引擎对英文幻觉日文 → **必须 `paddleocr`**（0.99+，ModelScope 自动下载） |
| 论文图渲染 | 柱状图粗色块安全、译文入位；**曲线图细线被 inpaint 连带抹掉** → 默认只交对照表 |
| 速度（CPU） | 检测+OCR 数秒/页；lama_large@1024 渲染数秒/页；inpainting_size 2048 分钟级 |

已知限制：

- 论文图 Agent 补充的区域不参与重渲染（工具工程里没有对应 region）；
- 相邻小字可能被粘成一个 region，需在标注阶段拆分；
- 工具 `translator: none` 会导致空区域（内部机制），检测配置必须用 `original`——脚本与文档已规避，但自写配置时要注意。

## 经验沉淀机制

- `references/lessons.md`：每完成一本/一批，把**可泛化**的经验按 `- 日期《对象》： 经验` 追加到对应小节（排序与检视 / 翻译与审校 / 论文图 / 工具与渲染）。作品专属设定留在工程 `work/glossary.md`，不进 Skill。
- `references/manga-translator-ui.md` 末尾「环境记录」：每次对接成功的命令、参数、坑（含日期）都追加一条，下次直接复用。

## 常见问题 FAQ

**Q: 渲染出来是乱码/方块？**
保护区域被工具重排（确认交的是 `out/render/` 剔除版）或字体缺字（`render.font_family` 换 `msyh.ttc` 等中文字体）。

**Q: 检测出 0 个区域？**
先确认不是合成图太"干净"（无分格/文字过稀疏）或配置里 translator 误用了 `none`；再用 `--schema-report` 看 JSON 结构是否走了通用解析。

**Q: 译文里出现日文假名？**
check 会报 WARN。刻意保留的拟声词/专名需在 notes 说明理由，其余打回重译。

**Q: 中断后怎么续？**
`work/` 里状态即文件。`batch_translate.py status` 看批次进度，重跑 `run` 从第一个未完成批继续；`plan --force` 重建后旧结果按 uid 自动复用。

**Q: 想换翻译 API？**
任何 OpenAI 兼容 `/chat/completions` 接口都行：`MANGA_LLM_BASE_URL` / `MANGA_LLM_API_KEY` / `MANGA_LLM_MODEL` 三个环境变量。

**Q: 为什么不让工具直接翻译？**
工具的机翻质量不可控且不可审校。本 Skill 只用它的检测/OCR/擦除/渲染四项原子能力，翻译由 Agent/API 按"术语表+上下文+规则"完成，每一句都可追溯、可修正。

## 致谢

- [manga-image-translator](https://github.com/zyddnys/manga-image-translator) 及其分支 [manga-translator-ui](https://github.com/hgmzhn/manga-translator-ui) —— 提供检测/OCR/擦除/渲染引擎
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) —— 论文图英文识别
- 所有经验条目来自真实翻译工程的踩坑记录
