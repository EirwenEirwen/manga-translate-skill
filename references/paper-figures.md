# 论文图（figure）翻译管线

适用：论文插图、图表、流程图、示意图、实验表格截图（通常英→中，也可能是日文论文）。
与漫画管线的区别：**没有阅读顺序与说话人**；默认交付「逐图对照表」，不默认重渲染。

## 流程（对应 SKILL.md 第 1–6 步的差异）

### 1. 检测与 OCR

与漫画同一套命令，但配置不同（实测结论，2026-10-07）：

- **OCR 引擎必须换 `paddleocr`**：默认的 48px 是日文模型，对英文小字会幻觉出日文句子（实测把 "Measured with TensorRT 10.0" 认成日文）。paddleocr 对印刷体英文几乎全对（置信度 0.99+），模型自动从 ModelScope 下载。
- **检测器调参**：`detection_size: 3072 + box_threshold: 0.25 + text_threshold: 0.4`。漫画检测器对论文图有固有偏向：标题、图题、大标注能检出；**图例条目、轴标签、刻度数字常漏检**，相邻小字可能粘成一个 region。调参能拆开粘连，但救不回漏检。
- 漏检的可译元素（图例、轴标签）按 SKILL 铁律 1 的论文图例外**由 Agent 对原图补区域**：bbox 尽量贴文字，notes 写"agent补充(工具漏检…)"，id 顺延。刻度数字等纯数值不用补（本来就不译）。
- 已知局限：agent 补充的区域不在工具 JSON 里，**重渲染时不会回填译文**（若其位置落在别的区域的 inpaint 蒙版附近还可能被误擦）。所以论文图主交付是对照表；确需渲染时优先在 Origin/matplotlib 里按对照表改源图。

### 2. 预览（paper 模式）

```bash
python "$SKILL/scripts/draw_preview.py" --detect work/detect --images raw --out work/preview --annotations work/annotations --mode paper
```

`--mode paper`：不画阅读顺序连线、编号即检测 id，只画框 + OCR? 存疑标记。
放大图 `crops/<图>/b<id>.png` 照常生成，核对原文用。

### 3. 标注（对照放大图逐个核实）

字段用法与漫画不同：

- `type` 填元素角色：`axis`（轴标签/刻度）/ `legend`（图例）/ `caption`（图题）/ `callout`（箭头标注、文字说明）/ `table`（表格文字）/ `title` / `other`。
- `source`：核对后的原文。公式与数值（R²、p<0.05、η）也填进 source，是否翻译由 keep_original 决定。
- `speaker`/`audience`/`order` 留空不填。
- `keep_original: true`：公式、变量、单位、纯数值、代码/命令、文件名、模型/数据集专名（BERT、ImageNet）、GPU 型号等**不译元素**。
- glossary 换用论文版模板（见文末）。

### 4. 分批翻译

与漫画同一套命令，`--rules` 传 `$SKILL/references/paper-rules.md`，glossary 传论文版。
批建议按「图」分组（同图元素同批，利于术语一致）；批次大小可放大到 30–50（论文文字短）。

### 5. 检查与审阅

check_translation 相同。审阅重点改为：

- **术语一致**：同一概念跨图、跨批必须同译，以术语表为准（check 的"同句异译"在论文图里几乎都是真问题）。
- **不译元素**确实没被翻译：变量、单位、数值原样出现在译文里是对的，出现在"被翻译改写"里是错的。
- 轴标签、图例精简：中文学术表述用名词短语（Accuracy→准确率，Training steps→训练步数）。
- 译文长度：图内空间比气泡更紧，超长 WARN 基本都要改。

### 6. 交付

**默认交付对照表**：check 已生成 `work/review/<图>.md`（按 id 排好的原文/译文表），把它们合并成一份 `work/out/对照表.md` 交用户，供改图或在 Origin/matplotlib/PPT 里替换。

仅当用户明确要"替换成中文的图"才走渲染：

1. `build_output.py` 产出 render/full JSON（写回各 region 的 `translation` 字段）。
2. **把 `work/out/render/<名>.json` 覆盖到 `<图片目录>/manga_translator_work/json/<名>_translations.json`**（渲染驱动从工具标准位置读取）。
3. 用 `render_with_tool.py` 渲染（命令见 manga-translator-ui.md 环境记录；`render.direction` 用 `horizontal`，论文图不要 auto 竖排）。
4. 渲染后逐区域核对（实测：柱状图/曲线图线条能保住，但小字残影、补充元素丢失都可能发生）；把握不大就只交对照表。

## 论文版 glossary 模板

```markdown
# 论文图术语表：<论文/主题>
## 术语表
| 英文 | 中文译名 | 备注 |
|---|---|---|
## 不译清单（keep_original）
| 元素 | 理由 |
|---|---|
## 待沉淀（完成后筛通用项进 Skill lessons）
```

术语表小节标题含"术语"即可被 check_translation 解析为术语对齐依据。
