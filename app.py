# Flask 入口：提供批注转换、页面叠加与矩形注释转移的 HTTP 接口
import os  # 用于操作系统相关功能，如获取环境变量中的端口号
import tempfile  # 用于创建临时文件和目录，处理上传的PDF文件
from pathlib import Path  # 提供面向对象的路径操作，更安全地处理文件路径
from typing import List, Optional  # 类型提示支持，提高代码可读性和IDE支持

# 导入Flask框架相关组件
from flask import Flask, render_template, request, send_file, jsonify, after_this_request
# Flask: Web应用框架
# render_template: 渲染HTML模板
# request: 处理HTTP请求数据
# send_file: 发送文件给客户端
# jsonify: 将Python对象转换为JSON响应
# after_this_request: 在请求完成后执行清理操作

# 导入自定义PDF处理模块
from pdf_stamp_to_highlight import (
    overlay_pages,  # 将一个PDF的页面叠加到另一个PDF的指定页面上
    parse_page_ranges,  # 解析页码范围字符串，如"1,3-5"
    process,  # 处理PDF文件，将半透明印章注释转换为高亮注释
    transfer_square_annotations,  # 将源PDF中的矩形框注释复制到目标PDF
)

# 创建Flask应用实例，指定模板和静态文件目录
app = Flask(__name__, template_folder="templates", static_folder="static")


# 定义根路由，返回主页模板
@app.route("/")
def index():
    """
    渲染并返回主页HTML模板
    
    路由: /
    方法: GET
    功能: 显示PDF印章转高亮的主页面
    返回: 渲染后的index.html模板
    """
    return render_template("index.html")


@app.route("/transfer")
def transfer_page():
    """
    矩形注释转移子页面
    
    路由: /transfer
    方法: GET
    功能: 显示矩形注释转移的页面
    返回: 渲染后的transfer.html模板
    """
    return render_template("transfer.html")


# 定义PDF处理路由，只接受POST请求
@app.route("/process", methods=["POST"])
def process_pdf():
    """
    处理上传的PDF文件，将PDF中的批注转换为高亮
    
    路由: /process
    方法: POST
    参数:
        - file: 上传的PDF文件 (multipart/form-data)
        - pages: 可选，指定处理的页码范围，格式如"1,3-5"
    
    返回:
        - 成功: 处理后的PDF文件，以附件形式下载
        - 失败: JSON格式的错误信息和HTTP状态码
    """
    # 获取上传的文件和页码参数
    uploaded = request.files.get("file")  # 获取上传的PDF文件
    pages_arg = request.form.get("pages")  # 获取用户指定的页码范围

    # 检查是否上传了文件，如果没有则返回错误信息
    if not uploaded:
        return jsonify({"error": "no file uploaded"}), 400  # 返回400错误和错误信息

    # 解析用户指定的页码范围
    pages: Optional[List[int]] = parse_page_ranges(pages_arg)  # 将字符串转换为页码列表

    # 创建临时目录用于处理PDF文件
    tmpdir = tempfile.mkdtemp()
    # 定义输入和输出文件路径
    tmp_in = Path(tmpdir) / "in.pdf"
    tmp_out = Path(tmpdir) / "out.pdf"

    # 保存上传的文件到临时输入路径
    uploaded.save(tmp_in)
    # 处理PDF文件，将批注转换为高亮
    process(str(tmp_in), str(tmp_out), pages)

    # 构建下载文件名，保留原始文件名并添加_convert后缀
    original_name = Path(uploaded.filename or "processed.pdf")
    stem = original_name.stem or "processed"
    download_name = f"{stem}_convert.pdf"

    # 定义清理函数，在请求完成后删除临时文件
    @after_this_request
    def cleanup(response):
        """清理临时文件和目录"""
        try:
            # 删除输出文件
            if tmp_out.exists():
                tmp_out.unlink(missing_ok=True)
            # 删除输入文件
            if tmp_in.exists():
                tmp_in.unlink(missing_ok=True)
            # 删除临时目录
            Path(tmpdir).rmdir()
        except Exception:
            # 忽略清理过程中的异常
            pass
        return response

    # 返回处理后的PDF文件供用户下载
    return send_file(
        tmp_out,
        download_name=download_name,
        as_attachment=True,
        mimetype="application/pdf",
    )


# 定义PDF叠加处理路由，只接受POST请求
@app.route("/process_overlay", methods=["POST"])
def process_and_overlay():
    """
    处理上传的PDF文件，将一个PDF的页面替换到另一个PDF的指定页面上
    该功能用于将需转换 PDF 处理后的页面叠加到目标PDF的指定位置
    """
    # 获取上传的需转换 PDF 文件、目标文件和页码参数
    uploaded = request.files.get("file")  # 需转换 PDF 文件
    target_pdf = request.files.get("target_file")  # 目标PDF文件
    pages_arg = request.form.get("pages")  # 要处理的需转换 PDF 页码范围
    source_pages_arg = request.form.get("source_pages")  # 用于叠加的需转换 PDF 页码
    target_pages_arg = request.form.get("target_pages")  # 要叠加到的目标PDF页码

    # 检查是否上传了需转换 PDF 文件和目标文件，如果缺少任一文件则返回错误信息
    if not uploaded or not target_pdf:
        return jsonify({"error": "both source and target PDF files are required"}), 400

    # 解析用户指定的页码范围
    pages: Optional[List[int]] = parse_page_ranges(pages_arg)  # 需转换 PDF 要处理的页码
    source_pages: Optional[List[int]] = parse_page_ranges(source_pages_arg)  # 需转换 PDF 用于叠加的页码
    target_pages: Optional[List[int]] = parse_page_ranges(target_pages_arg)  # 目标PDF被叠加的页码

    # 验证目标页码参数是否提供，这是叠加操作的必要参数
    if target_pages is None:
        return jsonify({"error": "target_pages is required for overlay"}), 400

    # 如果未指定源页码，则默认使用目标页码
    if source_pages is None:
        source_pages = target_pages

    # 确保源页码和目标页码数量相同，以便一一对应
    if len(source_pages) != len(target_pages):
        return jsonify({"error": "source_pages and target_pages must have the same length"}), 400

    # 创建临时目录用于处理PDF文件
    tmpdir = tempfile.mkdtemp()
    # 定义各种临时文件路径
    tmp_in = Path(tmpdir) / "in.pdf"  # 源PDF输入文件
    tmp_out = Path(tmpdir) / "out.pdf"  # 源PDF处理后输出文件
    tmp_target = Path(tmpdir) / "target.pdf"  # 目标PDF文件
    tmp_overlay = Path(tmpdir) / "overlay.pdf"  # 最终叠加结果文件

    # 保存上传的文件到临时路径
    uploaded.save(str(tmp_in))
    target_pdf.save(str(tmp_target))

    # 定义临时文件清理函数
    def _cleanup_temp():
        """清理所有临时文件和目录"""
        try:
            # 依次删除所有临时文件
            for path in (tmp_overlay, tmp_out, tmp_in, tmp_target):
                if path.exists():
                    path.unlink(missing_ok=True)
            # 删除临时目录
            Path(tmpdir).rmdir()
        except Exception:
            # 忽略清理过程中的异常
            pass

    # 处理PDF文件，将批注转换为高亮，然后进行页面叠加
    try:
        # 第一步：处理源PDF，将批注转换为高亮
        process(str(tmp_in), str(tmp_out), pages)

        # 第二步：将处理后的源PDF页面叠加到目标PDF的指定页面上
        overlay_pages(
            str(tmp_out),  # 处理后的源PDF
            str(tmp_target),  # 目标PDF
            source_pages,  # 源PDF页码
            target_pages,  # 目标PDF页码
            output_path=str(tmp_target),  # 原地更新以保留ID
            preserve_id=True,
        )
    except Exception as exc:
        # 如果处理过程中出现异常，清理临时文件并返回错误信息
        _cleanup_temp()
        return jsonify({"error": str(exc)}), 400

    # 构建下载文件名，保留目标文件名
    target_name = Path(target_pdf.filename or "merged.pdf")
    download_name = target_name.name or "merged.pdf"

    # 定义清理函数，在请求完成后删除临时文件
    @after_this_request
    def cleanup(response):
        """清理叠加处理过程中创建的临时文件"""
        _cleanup_temp()
        return response

    # 返回处理后的PDF文件供用户下载
    return send_file(
        tmp_target,
        download_name=download_name,
        as_attachment=True,
        mimetype="application/pdf",
    )


@app.route("/transfer_rects", methods=["POST"])
def transfer_rect_annotations():
    """
    将需转移注释 PDF 中的矩形框注释复制到目标 PDF 的指定页面
    """
    source_file = request.files.get("source_file")
    target_file = request.files.get("target_file")
    source_pages_arg = request.form.get("source_pages")
    target_pages_arg = request.form.get("target_pages")

    if not source_file or not target_file:
        return (
            jsonify({"error": "source_file and target_file are required"}),
            400,
        )

    source_pages = parse_page_ranges(source_pages_arg)
    target_pages = parse_page_ranges(target_pages_arg)

    tmpdir = tempfile.mkdtemp()
    tmp_source = Path(tmpdir) / "source.pdf"
    tmp_target = Path(tmpdir) / "target.pdf"

    source_file.save(str(tmp_source))
    target_file.save(str(tmp_target))

    def _cleanup():
        try:
            for p in (tmp_source, tmp_target):
                if p.exists():
                    p.unlink(missing_ok=True)
            Path(tmpdir).rmdir()
        except Exception:
            pass

    try:
        transfer_square_annotations(
            str(tmp_source),
            str(tmp_target),
            output_path=str(tmp_target),  # 原地更新目标文件以保持指纹
            source_pages=source_pages,
            target_pages=target_pages,
        )
    except Exception as exc:
        _cleanup()
        return jsonify({"error": str(exc)}), 400

    target_name = Path(target_file.filename or "rect-transfer.pdf")
    download_name = target_name.name or "rect-transfer.pdf"

    @after_this_request
    def cleanup(response):
        _cleanup()
        return response

    return send_file(
        tmp_target,
        download_name=download_name,
        as_attachment=True,
        mimetype="application/pdf",
    )


# 主程序入口点
if __name__ == "__main__":
    # 从环境变量获取端口号，默认为5000
    port = int(os.environ.get("PORT", "5000"))
    # 启动Flask应用，监听所有网络接口，开启调试模式
    app.run(host="0.0.0.0", port=port, debug=True)
