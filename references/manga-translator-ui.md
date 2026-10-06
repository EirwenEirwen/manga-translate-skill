# manga-translator-ui 对接手册

"manga-translator-ui" = 基于 manga-image-translator 的漫画翻译工具（检测→OCR→翻译→消字→嵌字）。
本 Skill 只用它的**检测+OCR**与**渲染**两段，翻译由 Skill 自己完成——绝不能让它机器翻译的结果混进译文。

## 本机安装现状（已装好，2026-10-07）

- 位置：`D:\tools\manga-translator-ui`（git clone + `uv sync --no-default-groups --group cpu`，CPU 版，本机无 NVIDIA GPU）
- Python 3.12 由 uv 托管在 `D:\uv-python`；依赖缓存在 `D:\uv-cache`（**C 盘只剩 ~20G，uv 相关一律放 D 盘**）
- 运行统一用：`cd /d/tools/manga-translator-ui && PYTHONUTF8=1 uv run --no-sync python -m manga_translator <子命令>…`
- **网络补丁**：本机访问 GitHub release 资产被墙。`pyproject.toml` 与 `uv.lock` 里 pydensecrf 的 3 个 wheel URL 已改为 `https://gh-proxy.com/https://github.com/…` 镜像（hash 校验一致）。若日后 `git pull` 更新了这两个文件，需要重新打同样的补丁再 `uv sync`。
- 模型权重自动下载到 `D:\tools\manga-translator-ui\models\`（首次运行各引擎时按需下载）。若下载走 HuggingFace 且超时，加环境变量 `HF_ENDPOINT=https://hf-mirror.com`；若走 GitHub release 且超时，手动用 gh-proxy 前缀下载放到对应 models 子目录。

## 启动方式（Skill 自动处理，无需人工）

两种模式，按需选：

- **CLI 模式（默认，推荐）**：无常驻服务，每条命令直接起进程跑完即退。Skill 每本书调用一两次 CLI 即可，天然满足"自己启动"。
- **Web 服务（要看 UI 或调 API 时）**：
  ```bash
  cd /d/tools/manga-translator-ui && PYTHONUTF8=1 \
    uv run --no-sync python -m manga_translator web --host 127.0.0.1 --port 8000
  # 放后台跑; 就绪探测: curl -s -m 3 http://127.0.0.1:8000/ 返回 200 即可用
  ```
  端口占用/已启动探测：先 `curl -s -m 3 http://127.0.0.1:8000/`，通了就复用，不要再起一个。

GPU 探测：`nvidia-smi` 存在且显存够 → 配置里 `use_gpu: true`；本机当前无 GPU，一律 `use_gpu: false`（CPU 上 detection 2048 一页约几秒到几十秒，inpaint lama_large 一页分钟级——能不用就不用）。

## 检测 + OCR（第 1 步）

写一份 detect-only 配置（要点：`cli.save_text=true` 出 JSON；`translator=none` 跳过翻译；`inpainter=none`、`renderer=none` 跳过消字渲染，快且不碰原图）：

```json
{
  "cli": {"save_text": true, "use_gpu": false},
  "translator": {"translator": "none", "target_lang": "CHS"},
  "detector": {"detector": "default", "detection_size": 2048},
  "ocr": {"ocr": "48px"},
  "inpainter": {"inpainter": "none"},
  "render": {"renderer": "none"}
}
```

```bash
cd /d/tools/manga-translator-ui && PYTHONUTF8=1 \
  uv run --no-sync python -m manga_translator local \
  -i <图片或文件夹> -o <输出目录> --config <上面的.json> [--overwrite]
```

产物：**每张图片旁**的 `<图片目录>/manga_translator_work/json/<名>_translations.json`，格式：

```json
{"regions": [{"text": "OCR原文", "texts": ["逐行"], "translation": "",
  "lines": [[[x,y],…], …], "center": [x,y], "angle": 0.0, "prob": 0.97,
  "font_size": 40.0, "direction": "h", "alignment": "center",
  "fg_colors": […], "bg_colors": […]}],
 "original_width": 1100, "original_height": 1600}
```

`scripts/normalize_detect.py` 认这个格式（regions/lines 快速路径）。验收：抽 2–3 页数一下 region 数与目测气泡数相当；预览图框套在气泡上。

参数微调参考：`detector.detection_size`（默认2048，小字漏检调大）、`detector.box_threshold`（默认0.5，漏检调低）、`ocr.ocr` 引擎（48px 默认；`mocr` 专攻日文手写；`paddleocr_vl` 是 VLM 版更准但慢）、`ocr.ignore_bubble`（过滤非气泡文字，论文图/背景字多时调）。

## 渲染（第 6 步：按我们的译文出图）

流程：`scripts/build_output.py` 把最终译文写回 `<名>_translations.json` 各 region 的 `translation` 字段（protect 区域按 render/full 两版处理）→ 再跑一次 CLI 让它按 JSON 渲染。

- 写回后的 JSON 与图片放在原相对位置，用**渲染配置**跑（与 detect-only 的区别：`inpainter` 要选真模型如 `lama_large`、`render.renderer` 用默认、`render.font_family` 指定中文字体如 `msyh.ttc`）。
- 工具会按 region 的 `font_size`/`direction`/`alignment`/`fg_colors` 排版；我们没动的 region（keep_original 剔除版）不会被打扰。
- 渲染成图在 `-o` 输出目录。**逐页 Read 成图抽查**：乱码（字体缺字/保护区域被动）、译文溢出、误擦画面。
- 实测记录（渲染回写的确切命令与开关）见下方环境记录，首次跑通后补记。

## 环境记录（实测可用的命令，持续追加）

- 2026-10-07 安装：`D:\tools\manga-translator-ui`，`uv sync --no-default-groups --group cpu` 通过（pydensecrf 走 gh-proxy 镜像补丁）。`local --help` 正常。
- 2026-10-07 检测+OCR 实测通过（日文漫画页 4/4 检出，置信度 0.99+）：
  ```bash
  cd /d/tools/manga-translator-ui && PYTHONUTF8=1 \
    uv run --no-sync python -m manga_translator local -i <图/目录> -o <输出> \
    --config <detect_only.json> --overwrite
  ```
  - 配置里 translator 必须 `original`；用 `none` 会因"空译文"导致 regions 被清空、JSON 落盘 0 区域。
  - 产物在**图片所在目录**的 `manga_translator_work/json/<名>_translations.json`；外层 JSON 以图片路径为键，内层才是 `{regions, original_width, original_height}`。
  - OCR 引擎选择：日文漫画用 `48px`/`mocr`；**英文/论文图必须用 `paddleocr`**——48px 是日文模型，对英文小字会幻觉出日文句子。paddle 模型自动从 ModelScope 下载（国内直连可用）。
  - 论文图检测调参：`detection_size: 3072 + box_threshold: 0.25 + text_threshold: 0.4` 能拆开粘连区域，但图例条目/轴标签/刻度仍漏检（漫画检测器的固有偏向），漏的部分按 SKILL 铁律 1 的论文图例外补区域。
- 2026-10-07 渲染实测通过（漫画页：日文擦净、中文按原方向回排；论文图：柱状图线条完好、译文入位）：
  ```bash
  # 前置: 把 build_output 的 work/out/render/<名>.json 覆盖到
  #       <图片目录>/manga_translator_work/json/<名>_translations.json
  cd /d/tools/manga-translator-ui && PYTHONUTF8=1 \
    uv run --no-sync python "$SKILL/scripts/render_with_tool.py" \
    --images <图/目录> --config <渲染配置.json> -o <输出目录> --overwrite
  ```
  - 渲染配置：`inpainter: lama_large`（inpainting_size 1024 时 CPU 数秒/页）、`render.renderer: default`、`render.font_family: msyh.ttc`（fonts/ 目录已带）、`render.direction` 只接受 `auto/horizontal/vertical`。
  - 写回 JSON 时 keep_original 区域已在 render 版剔除，不会被碰。
  - 渲染设置存档：`detect_only.json`（检测）与 `render_paper.json`（渲染）样例见示例工程 `.paper_test/`。
- 模型缓存：`D:\tools\manga-translator-ui\models\`（detection 295MB、ocr 48px、paddle PP-OCRv6）。
