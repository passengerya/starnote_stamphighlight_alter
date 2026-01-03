# 导入必要的库
import os  # 用于操作系统相关功能，如环境变量
import tempfile  # 用于创建临时文件和目录
from pathlib import Path  # 提供面向对象的路径操作
from typing import List, Optional  # 类型提示支持

# 导入Flask框架相关组件
from flask import Flask, render_template, request, send_file, jsonify, after_this_request

# 导入自定义PDF处理模块
from pdf_stamp_to_highlight import parse_page_ranges, process

# 创建Flask应用实例，指定模板和静态文件目录
app = Flask(__name__, template_folder="templates", static_folder="static")


# 定义根路由，返回主页模板
@app.route("/")
def index():
    """渲染并返回主页HTML模板"""
    return render_template("index.html")


# 定义PDF处理路由，只接受POST请求
@app.route("/process", methods=["POST"])
def process_pdf():
    """处理上传的PDF文件，将PDF中的批注转换为高亮"""
    # 获取上传的文件和页码参数
    uploaded = request.files.get("file")
    pages_arg = request.form.get("pages")

    # 检查是否上传了文件，如果没有则返回错误信息
    if not uploaded:
        return jsonify({"error": "no file uploaded"}), 400

    # 解析用户指定的页码范围
    pages: Optional[List[int]] = parse_page_ranges(pages_arg)

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


# 主程序入口点
if __name__ == "__main__":
    # 从环境变量获取端口号，默认为5000
    port = int(os.environ.get("PORT", "5000"))
    # 启动Flask应用，监听所有网络接口，开启调试模式
    app.run(host="0.0.0.0", port=port, debug=True)
