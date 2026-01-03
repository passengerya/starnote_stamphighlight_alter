# PDF Stamp → Highlight 转换器
# 转换startnote软件导出的可编辑pdf的荧光笔注释（属性为stamp）为高亮属性属性注释，并为处理后高亮注释四角添加一定圆角
将透明度小于 1 的 `/Stamp` 图章注释，缩减到真实绘制范围后替换为圆角 `/Highlight` 高亮。

## 核心逻辑
1. **筛选目标注释**：遍历页面 `/Annots`，仅处理 `Subtype=/Stamp` 且 `/CA` 或 `/ca` < 1 的图章。
2. **获取外观流**：读取 `/AP` -> `/N`（若为字典取当前状态或第一个）。提取 `/BBox`、`/Matrix`、`/Resources`。
3. **解析绘制边界**：用 `pikepdf.parse_content_stream` 解析 appearance stream，支持 `q/Q/cm/w/gs`、路径构造与绘制、`Do` 递归 Form/Image，计算“实际绘制边界” painted_bounds（含线宽扩展、局部 CTM、嵌套 Form）。
4. **映射到页面**：将 painted_bounds 先乘外观 `/Matrix`，再按 `/BBox` → `/Rect` 线性映射到页面坐标得到 new_rect，并生成 QuadPoints。
5. **替换为高亮**：删除原图章，新增 `/Subtype /Highlight` 注释：
   - `/Rect` 使用 new_rect，`/QuadPoints` 覆盖矩形四角。
   - `/C`、`/CA`、`F` 继承；`/NM` 生成 UUID。
   - 构建 `/AP`：使用圆角矩形路径（贝塞尔近似，角半径约 18% 边长）填充原色，混合模式 Multiply，透明度继承。

## 目录结构
- `pdf_stamp_to_highlight.py`：核心转换脚本（CLI 与库入口）。
- `app.py`：Flask Web 服务。
- `templates/index.html`：前端页面（点击选页、批量页码输入、日/夜模式，Google 鲜艳风格）。
- `requirements.txt`：依赖清单。
- `input.pdf`：示例输入（可替换）。

## 安装依赖
```bash
pip install -r requirements.txt
```

## 命令行使用
```bash
# 全部页面
python pdf_stamp_to_highlight.py input.pdf output.pdf

# 指定页（1,3,5-7）
python pdf_stamp_to_highlight.py input.pdf output.pdf --pages 1,3,5-7
```
输出文件名默认 `output.pdf`（若用前端上传则自动添加 `_convert` 后缀）。

## Web 界面使用
1) 启动服务（保持终端开启）：
```bash
cd /d e:\桌面\starnote_alter
python app.py
```
2) 浏览器打开 `http://localhost:5000`（务必用 http，勿用 file://）。
3) 选择 PDF：
   - 点击缩略图可多选页面。
   - 也可在“自定义批量页码”输入如 `1,3,5-7`，留空或不选即处理全部。
4) 点击“处理所选页面”或“处理全部页面”，完成后自动下载 `原名_convert.pdf`。
5) 日/夜模式：右上角“日/夜”按钮切换。

## 依赖与版本
- Python 3.11+
- pikepdf ≥ 10.0.0（使用 `parse_content_stream`、`Matrix` 等）
- Flask ≥ 3.0.0（Web 服务）
- 前端使用 CDN 的 PDF.js 3.11.174

## 备注与边界
- 仅转换透明图章（CA<1）的 `/Stamp`，其它注释保持原样。
- 解析支持基础绘图指令及 Form/Image 递归，极端复杂内容流或非常深的嵌套（>6 层）会被跳过。
- 高亮外观已内嵌圆角 AP，避免不同阅读器渲染不一致。
