---
name: manga-translate
description: 日文漫画整册汉化 + 论文插图（figure）图表翻译全流程，自动分辨图片是漫画还是论文图并分流处理。漫画：manga-translator-ui 检测气泡并出 OCR 初稿，脚本绘制编号、阅读顺序与存疑标记预览图，Agent 逐页检视修正（阅读顺序、说话人、气泡类型、原文校对），分批携带上下文提交翻译并支持中断续接，自动检查漏翻、超长、前后译法不一，逐页审校后写回工具渲染成图，完成后沉淀经验。论文图：检测元素→核对→术语表→分批翻译→对照表交付。用户提到漫画翻译、汉化、翻译漫画或本子、manga translation、漫画本地化、气泡 OCR、翻译论文图片/图表/figure/插图，或给出漫画页面或论文插图要求翻译时，使用本 Skill。
---

# 漫画翻译（JP→CN 全流程）

## 分工与铁律

三个执行者各司其职，不要越界：

| 执行者 | 只负责 | 绝不做 |
|---|---|---|
| manga-translator-ui | 气泡检测、擦除与排版坐标、OCR 初稿、最终渲染 | — |
| `$SKILL/scripts/` 的脚本 | 排序初稿、画预览、分批调度、自动检查、写回 | — |
| 你（Agent） | 看图理解剧情、修正顺序、校对原文、标注说话人、翻译与审校 | 从零识别文字、手写坐标 |

三条铁律，违反即废稿：

1. **坐标只能来自工具检测**。你能看懂画面，但给不出像素级气泡坐标；禁止绕过检测输出凭眼力报坐标或识别文字位置。**论文图例外**：工具对图例条目、轴标签等小字印刷体常漏检，允许对着原图补区域，但 notes 必须写"agent补充"强制复核，且这些区域不会参与重渲染；漫画气泡无此例外。
2. **对白/心理/旁白必须全部翻译，无论 OCR 初稿多差**。读该气泡的放大图逐字恢复原文再翻译；只有"无法识别完整的拟声词"允许 `keep_original: true` 保留原文不译。
3. **交出成图前必须走完自动检查和逐页审阅**。审阅中发现人称、肯定否定、因果逻辑错误直接修正；只有"放大原图仍看不清"或"存在多种合理解读"才汇总问用户。

## 前置

- Python 3.9+、Pillow：`python -m pip install pillow`
- `$SKILL` = 本 Skill 的 Base directory（加载本文件时系统会给出），脚本在 `$SKILL/scripts/`。
- 第 1 步前必读 `$SKILL/references/manga-translator-ui.md`（工具安装/自动启动/配置与实测记录，两条流程共用）。
- 流程 A 第 3 步前必读 `references/translation-guide.md`（日中翻译守则）；流程 B 第 1 步前必读 `references/paper-figures.md`（论文图实测参数与局限），`paper-rules.md` 随翻译批次下发。
- 动手前都过一遍 `$SKILL/references/lessons.md`（持续更新的实战经验）。
- 所有命令在**工程根**（图片所在文件夹）执行；路径含空格或中文要加引号。

## 图类分拣（拿到图片先做，漫画/论文图分流）

抽看 2–3 张图（Read 图片文件），判定类型后分流；混合输入建议分两个 work 目录各跑各的管线，脚本与数据格式完全通用：

| | 漫画页 | 论文图（figure） |
|---|---|---|
| 文字载体 | 气泡、拟声词、旁白框 | 坐标轴、图例、标注、表格、图题 |
| 画面 | 人物、分格 | 曲线/柱状/流程图/示意图 |
| 方向 | 通常日→中 | 通常英→中 |
| 管线 | 下面「流程 A」 | 下面「流程 B」（单列流程，无阅读顺序/说话人；默认交付对照表，不重渲染） |

单张图判不准时看上下文（同一批其他图、用户说法）；type 取值两套体系互不混用。

## 工程目录约定

在图片所在文件夹（工程根）内创建，漫画与论文图工程通用：

```
raw/                        原图（或记录实际文件夹，命令里 --images 传它）
work/
├── detect/raw_json/        manga-translator-ui 的原始检测 JSON（每页一份）
├── detect/<页>.norm.json   归一化后的坐标+OCR（脚本生成）
├── preview/<页>.jpg        预览图（编号/顺序线/存疑标记）
├── preview/crops/<页>/b<id>.png   每个气泡的放大图
├── annotations/<页>.json   标注文件（Agent 编辑，核心数据）
├── annotations/_reading_order.txt  跨页阅读顺序覆盖（可选）
├── glossary.md             作品档案：剧情概要/人物表/专有名词表
├── translation/
│   ├── plan.json           分批计划
│   ├── batches/batch_NNN.prompt.md / .result.json  每批提示词与结果（状态即文件）
│   ├── overrides.json      审校人工修正 {"页:气泡id": "译文"}
│   ├── merged.json         合并后的最终译文（含 overrides）
│   └── check_report.md     自动检查报告
├── review/<页>.md          逐页审阅对照表
├── questions.md            需用户判断的问题（可选）
└── out/
    ├── render/<页>.json    渲染任务（保护区域已剔除）→ 优先交这个
    ├── full/<页>.json      全量渲染任务（兜底用）
    └── images/             最终成图
```

中断续接：所有中间产物都在磁盘上。接手旧工程时按 `work/` 里已存在的产物判断进度（检测→预览→标注→plan→批次结果→merged→review→out/images），从第一个未完成的环节继续，**不要重做已完成步骤**。

## 流程 A：漫画（通常日→中）

### 第 0 步 · 初始化

```bash
mkdir -p work/detect/raw_json work/preview work/annotations work/translation/batches work/review work/out
```

建 `work/glossary.md`（模板见文末）。确认原图位置。

### 第 1 步 · 预处理（工具出坐标与 OCR 初稿，不做翻译）

按 `$SKILL/references/manga-translator-ui.md` 驱动 manga-translator-ui，**只要检测+OCR，不让它翻译**。把每页检测 JSON 放进 `work/detect/raw_json/`，文件名与图片同名（后缀 .json）。然后归一化：

```bash
python "$SKILL/scripts/normalize_detect.py" --in work/detect/raw_json --out work/detect --images raw
```

完成标准：每页报出的气泡数与目测原图相当（抽查 2–3 页）。解析为 0 或明显偏少的页，用 `--schema-report` 看原始结构，写几行定制转换代码修正后重跑。

### 第 2 步 · 生成预览图

```bash
python "$SKILL/scripts/draw_preview.py" --detect work/detect --images raw --out work/preview --annotations work/annotations
```

产出三样：预览图（橙框=正常，红框+OCR?=存疑，编号=初步阅读顺序，青线=顺序连线）、每泡放大图 `work/preview/crops/<页>/b<id>.png`、标注骨架 `work/annotations/<页>.json`（已存在则跳过，绝不覆盖人工编辑）。

初稿排序只是坐标启发式（右→左分列、列内上→下），**跨页大格、斜排气泡必然有错**，留给第 3 步修。

### 第 3 步 · 逐页检视（核心步骤，最花时间，不可省）

对每一页：先 Read 预览图通读；逐泡核对原文时 Read 对应 `crops/<页>/b<id>.png` 放大图。

在 `work/annotations/<页>.json` 里为每个气泡改这些字段（用 Edit 精准修改，**不要整文件重写**，坐标字段一个都不能丢）：

- `order`：修正阅读顺序。
- `source`：对照放大图改正 OCR——认错的字、只认一半的句子都要恢复完整。对白/心理/旁白此字段必须非空且已核实。
- `type`：`dialogue` / `thought` / `narration` / `onomatopoeia` / `sign` / `bg_text` / `unclear`。
- `speaker` / `audience`：用人物表里的名字；没名字的用特征代称（少女A、黑发男）；旁白填"叙事者"。
- `keep_original`：仅限无法识别完整的拟声词，或用户点名保留项（签绘、作者 ID 等）。
- `notes`：潦草、多解、特殊梗等备注。

排序修正规则：日漫右→左、上→下；同格内斜排（阶梯状）从右上到左下；**跨页大格**阅读起点在右页、延续到左页——单页 order 表达不了跨页先后时，在 `work/annotations/_reading_order.txt` 里按真实阅读顺序一行一个 `页:气泡id` 写全（存在此文件时，分批与审阅按它排序）。

同时维护 `work/glossary.md`：剧情概要（随进度滚动更新，作品级而非流水账）、人物表（自称/语气/称呼他人）、专有名词表（原文→译名，首次出现即登记）。

完成标准：每页每个气泡 `source` 非空且已对照原图核实（keep_original 的残缺拟声词除外）；type/speaker 已填；glossary 三节齐。修正 order 后可用下面命令重画预览（编号会跟着新顺序走）：

```bash
python "$SKILL/scripts/draw_preview.py" --from-annotations --annotations work/annotations --images raw --out work/preview
```

### 第 4 步 · 分批翻译

```bash
python "$SKILL/scripts/batch_translate.py" plan --annotations work/annotations \
  --out work/translation --glossary work/glossary.md --rules "$SKILL/references/translation-guide.md"
```

翻译执行二选一：

- **API 模式**（推荐，OpenAI 兼容接口）：`export MANGA_LLM_BASE_URL=... MANGA_LLM_API_KEY=... MANGA_LLM_MODEL=...`，然后：
  ```bash
  python "$SKILL/scripts/batch_translate.py" run --all --out work/translation --glossary work/glossary.md --rules "$SKILL/references/translation-guide.md"
  ```
- **手动模式**：循环执行 `run`（不带 `--all`）→ 它生成下一个待译批次的 `batches/batch_NNN.prompt.md` 并停下 → 你按提示词内容翻译，写成 `batches/batch_NNN.result.json`（格式在提示词末尾）→ 再 `run`，直到没有待译批次。

每批提示词自动携带：glossary 全文 + 通用规则 + **上一批中日对照**（保持前后一致）。中断续接：状态就是 batches/ 里的文件，任何时候重跑 `run` 即从断点继续。

全部批次完成后合并（此后所有人工修正都写 `overrides.json`，改完重跑 merge 生效）：

```bash
python "$SKILL/scripts/batch_translate.py" merge --out work/translation
```

### 第 5 步 · 自动检查 + 逐页审阅

```bash
python "$SKILL/scripts/check_translation.py" --annotations work/annotations \
  --merged work/translation/merged.json --glossary work/glossary.md \
  --report work/translation/check_report.md --review-dir work/review
```

1. 清零报告里的 **ERROR**（漏翻：该译没译、译文≈原文）。
2. 逐页审阅：Read `work/review/<页>.md`（按阅读顺序排好的中日对照表）+ 预览图，必要时回看 crops 与原图。三必查：**人称**是否符合 speaker、**肯定否定**有没有翻反（双重否定、反问）、**因果逻辑**（から/ので/のに 连接方式）。另查术语与称呼一致性、语气是否出戏。发现问题 → 改 `work/translation/overrides.json` → 重跑 `merge` → 重跑本检查。
3. 只有"放大原图仍看不清"或"存在多种合理解读"的条目，汇总进 `work/questions.md` 交用户；其余一律自己定夺。

WARN 类（超长/同句异译/术语未对齐）逐条过：该改就改，确实合理的在 notes 或 overrides 旁注明理由。

### 第 6 步 · 写回与出图

```bash
python "$SKILL/scripts/build_output.py" --detect work/detect/raw_json \
  --annotations work/annotations --merged work/translation/merged.json --out work/out
```

- `work/out/render/`：只含需翻译区域（keep_original 区域已剔除）——**优先**交这个，防止工具重排保留区产生乱码。
- `work/out/full/`：全量版本（保护区域 translation=原文），工具不支持剔除区域时用。

按 reference 手册渲染：`build_output` 后**先把 `work/out/render/<名>.json` 覆盖到工具标准位置** `<图片目录>/manga_translator_work/json/<名>_translations.json`，再跑：

```bash
cd /d/tools/manga-translator-ui && PYTHONUTF8=1 \
  uv run --no-sync python "$SKILL/scripts/render_with_tool.py" \
  --images <图片文件或目录> --config <渲染配置.json> -o work/out/images --overwrite
```

渲染配置要点见 `references/manga-translator-ui.md`（inpainter 选真模型、`render.font_family` 用中文字体、`direction` 取 auto/horizontal/vertical）。成图**逐页 Read 抽查**：乱码、译文溢出、误擦画面、保护区域被动。有问题 → 回 overrides/标注修正 → 重跑 build_output → 重渲。论文图默认只交对照表，重渲染后更要逐区域核对。

### 第 7 步 · 复盘沉淀

把本书**可泛化**的经验按 `- YYYY-MM-DD《作品》： 经验` 一行一条追加到 `$SKILL/references/lessons.md` 对应小节；工具对接中实测可用的命令/端点/字段名追加到 `references/manga-translator-ui.md` 末尾的环境记录。书内专属设定留在工程 `work/glossary.md`，不要搬进 Skill。

## 流程 B：论文图（通常英→中，单列流程）

第 0 步初始化与工具启动同流程 A；细节与实测参数以 `references/paper-figures.md` 为准。与漫画的本质差异：**没有阅读顺序与说话人**，默认交付「逐图对照表」而非重渲染。

1. **检测 + OCR（专用配置，与漫画不同）**：OCR 引擎必须 `paddleocr`（默认 48px 是日文模型，对英文小字会幻觉日文）；检测器调参 `detection_size: 3072 + box_threshold: 0.25 + text_threshold: 0.4`；translator 必须 `original`（`none` 会清空 regions）；inpainter/renderer 用 `none`。命令与产物位置同流程 A 第 1 步。已知覆盖面：标题/图题/大标注能检出，**图例条目、轴标签、刻度常漏检**，相邻小字会粘连。
2. **预览（paper 模式）**：`draw_preview.py` 加 `--mode paper`——不画顺序连线，编号即检测 id，只画框与 OCR? 标记；放大图照常生成。
3. **标注（对照放大图逐个核实，重点在补漏与校错）**：
   - OCR 残错当场修（`source` 填核对后的原文，`ocr_text` 保留原样供追溯）；
   - 粘连 region 的 source 拆成完整各句分别处理；
   - 漏检的可译元素（图例、轴标签）**由你对着原图补区域**：id 顺延、bbox 贴文字、notes 写 `agent补充(工具漏检…)`；纯数值刻度不补（本来就不译）；
   - `type` 用 `axis` / `legend` / `caption` / `callout` / `table` / `title` / `other`；
   - 公式、变量、单位、纯数值、专名（模型名/数据集/产品型号/量化格式）一律 `keep_original: true`；
   - glossary 用论文版模板（术语表 + 不译清单，见 paper-figures.md 末尾），术语表小节标题含"术语"即可被 check 解析。
4. **分批翻译**：与流程 A 同一套 plan/run/merge 命令，`--rules` 传 `$SKILL/references/paper-rules.md`；文字短，批可放大到 30–50，同图元素尽量同批。
5. **检查与交付**：check 相同；审阅三查换成——**术语跨图跨批一致**（check 的"同句异译/术语未对齐"在论文图里几乎都是真问题，但"GLUE 基准"这类惯用简写要先改术语表再判错译）、**不译元素没被翻动**、**译文长度**（图内空间比气泡更紧）。**默认交付**：把 `work/review/<图>.md` 合并成 `work/out/对照表.md` 交用户，在 Origin/matplotlib/PPT 里按表改图。
6. **渲染（仅用户点名要中文图时）**：build_output 后**先把 `work/out/render/<名>.json` 覆盖到 `<图片目录>/manga_translator_work/json/<名>_translations.json`**，再 `render_with_tool.py`（`render.direction: horizontal`，不要 auto）。已实测的风险：曲线图细线会被 inpaint 连带抹掉、补充的图例/轴标签不会回填——渲染后逐区域核对，不达标就退回只交对照表。
7. **复盘**：论文图专属经验进 `references/lessons.md` 的「论文图」小节。

## 标注文件格式（work/annotations/<页>.json）

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

`id` 与 `bbox` 来自检测，是稳定键，任何情况下不要改；`ocr_text`/`ocr_conf`/`flags` 保留原始参考；其余字段由你填写。

## work/glossary.md 模板（流程 A 漫画版；论文图版见 paper-figures.md）

```markdown
# 作品档案：<书名·卷>
## 剧情概要（随进度滚动更新）
## 人物表
| 人物 | 自称 | 语气特征 | 称呼他人 | 备注 |
|---|---|---|---|---|
## 专有名词表
| 原文 | 译名 | 首次出现 | 备注 |
|---|---|---|---|
## 本卷待沉淀规则（第 7 步筛出通用项后清空）
```

## 判断速查

- **type 判定**：带尾巴指向嘴=dialogue；云朵/波浪框、括号省略号=thought；无框或画外矩形框=narration；穿透画面的手写大字=onomatopoeia；路牌/海报/字幕条=sign；店铺名等画面内文字=bg_text。拿不准填 unclear 并写 notes。
- **论文图 type**：`axis`（轴/刻度）/ `legend` / `caption` / `callout` / `table` / `title` / `other`；公式、变量、单位、纯数值、专名模型名一律 `keep_original: true`。
- **keep_original 只有两类**：无法识别完整的拟声词；用户点名保留项。
- **要不要问用户**：能放大看清且只有一种合理读法 → 不问，自己定；看不清或多解 → 进 questions.md。
- **审阅三必查**：人称对不对 speaker、肯否有没有翻反、因果有没有断。

## 常见坑

- 工具输出若自带"翻译"字段，一律弃用，只取坐标/文本/置信度。
- 预览图编号是**初步**顺序，改了 order 后预览不会自动更新，需 `--from-annotations` 重画。
- check 的"超长"是提醒不是死刑：拟声词、怒吼台词确认合理后可保留，注明理由。
- 渲染乱码大概率是保护区域被重排 → 确认交给工具的是 `out/render/` 版本。
- `plan` 之后又改了 annotations 里的 `source`（原文变了）→ 对应气泡要重译：删掉该批次 result 重跑，或直接写 overrides；增删了气泡则 `plan --force` 重建（旧结果按 uid 仍可续用）。
- 中日同形异义词（手紙=信、勉強=学习、丈夫=结实）是高频翻错点，审阅时专门过一遍。
