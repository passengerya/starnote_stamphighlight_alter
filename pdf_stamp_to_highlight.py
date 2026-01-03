# 导入必要的库
import argparse  # 用于解析命令行参数
import math  # 数学运算
import uuid  # 用于生成唯一标识符
from typing import Iterable, List, Optional, Sequence, Tuple  # 类型提示支持

import pikepdf  # PDF处理库，用于读取、修改和创建PDF文件
from pikepdf import Matrix, Name  # PDF矩阵和名称对象，用于处理PDF中的图形变换和命名对象


def bounds_from_points(points: Iterable[Tuple[float, float]]) -> Optional[List[float]]:
    """
    从点集合计算边界框
    
    该函数接收一系列点坐标，计算并返回能够包含所有这些点的最小矩形边界框。
    边界框以[x_min, y_min, x_max, y_max]的形式返回，其中(x_min, y_min)是左下角坐标，
    (x_max, y_max)是右上角坐标。

    参数:
        points: 点坐标的迭代器，每个点为(x, y)元组

    返回:
        包含边界框的列表[x_min, y_min, x_max, y_max]，如果点集为空则返回None
    """
    pts = list(points)
    if not pts:
        return None
    xs = [p[0] for p in pts]  # 提取所有x坐标
    ys = [p[1] for p in pts]  # 提取所有y坐标
    return [min(xs), min(ys), max(xs), max(ys)]  # 返回边界框


def bezier_sample(
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    p3: Tuple[float, float],
    steps: int = 20,
) -> List[Tuple[float, float]]:
    """
    采样三次贝塞尔曲线以近似其边界
    
    三次贝塞尔曲线由四个控制点定义：起点(p0)、两个控制点(p1, p2)和终点(p3)。
    该函数通过在参数t从0到1的范围内采样，计算曲线上的多个点，从而近似表示
    整条曲线。这些采样点可用于计算曲线的边界框。
    
    贝塞尔曲线公式：
    B(t) = (1-t)³p0 + 3(1-t)²tp1 + 3(1-t)t²p2 + t³p3

    参数:
        p0, p1, p2, p3: 贝塞尔曲线的四个控制点
        steps: 采样步数，默认为20，值越大曲线越精确

    返回:
        采样点列表，包含从起点到终点的曲线点
    """
    pts = []
    for i in range(steps + 1):
        t = i / steps  # 参数t从0到1
        mt = 1 - t  # 1-t
        # 计算贝塞尔曲线上的点
        x = (
            mt**3 * p0[0]
            + 3 * mt * mt * t * p1[0]
            + 3 * mt * t * t * p2[0]
            + t**3 * p3[0]
        )
        y = (
            mt**3 * p0[1]
            + 3 * mt * mt * t * p1[1]
            + 3 * mt * t * t * p2[1]
            + t**3 * p3[1]
        )
        pts.append((x, y))
    return pts


def max_scale_from_matrix(matrix: Matrix) -> float:
    """
    从变换矩阵中计算最大缩放因子
    
    PDF变换矩阵是一个3x3矩阵，通常表示为[a b 0; c d 0; e f 1]，其中：
    - a和d分别表示x和y方向的缩放因子
    - b和c分别表示y和x方向的倾斜因子
    - e和f分别表示x和y方向的平移量
    
    该函数计算x和y方向的实际缩放因子（考虑旋转和倾斜），并返回两者中的最大值。
    这对于确定图形元素的实际大小很有用，特别是在计算描边宽度时。

    参数:
        matrix: PDF变换矩阵

    返回:
        最大缩放因子
    """
    sx = math.hypot(matrix.a, matrix.c)  # x方向缩放因子
    sy = math.hypot(matrix.b, matrix.d)  # y方向缩放因子
    return max(sx, sy)  # 返回最大值


def update_bounds(existing: Optional[List[float]], new: Optional[List[float]]) -> Optional[List[float]]:
    """
    更新边界框，合并两个边界框
    
    该函数接收两个边界框，并返回一个能够同时包含这两个边界框的新边界框。
    如果其中一个边界框为None，则直接返回另一个边界框。这种操作在计算多个图形
    元素的总体边界框时非常有用，例如计算一个页面上所有注释的总边界。
    
    合并过程：
    - 新边界框的x_min取两个边界框x_min中的较小值
    - 新边界框的y_min取两个边界框y_min中的较小值
    - 新边界框的x_max取两个边界框x_max中的较大值
    - 新边界框的y_max取两个边界框y_max中的较大值

    参数:
        existing: 现有边界框[x_min, y_min, x_max, y_max]
        new: 新边界框[x_min, y_min, x_max, y_max]

    返回:
        合并后的边界框，能够同时包含两个输入边界框
    """
    if new is None:
        return existing
    if existing is None:
        return list(new)
    # 合并两个边界框，取最小的x_min和y_min，最大的x_max和y_max
    return [
        min(existing[0], new[0]),
        min(existing[1], new[1]),
        max(existing[2], new[2]),
        max(existing[3], new[3]),
    ]


def compute_painted_bounds(
    stream: pikepdf.Stream,
    resources: Optional[pikepdf.Dictionary],
    *,
    ctm: Optional[Matrix] = None,
    depth: int = 0,
) -> Optional[List[float]]:
    """
    解析内容流并近似计算当前CTM下的绘制边界框
    
    该函数解析PDF内容流，计算在当前变换矩阵(CTM)下所有绘制操作的边界框。
    PDF内容流是一系列图形操作指令的集合，如绘制路径、显示图像等。
    
    该函数只支持最小操作子集(路径、Do、cm、q/Q、w、gs)，这对于典型的由表单或图像构建的
    印章外观流是足够的。这些操作包括：
    - 路径操作(m, l, c, v, y, h, re)
    - 绘制操作(S, s, f, F, f*, B, B*, b, b*, n)
    - 图形状态操作(q, Q, cm, w, gs)
    - XObject操作(Do)
    
    通过解析这些操作，函数可以计算出所有绘制内容的边界框，这对于将印章转换为
    高亮注释非常重要。

    参数:
        stream: PDF内容流，包含一系列图形操作指令
        resources: 资源字典，包含字体、图像、XObject等资源引用
        ctm: 当前变换矩阵，用于将用户空间坐标转换为设备空间坐标，默认为单位矩阵
        depth: 递归深度，用于避免在处理嵌套XObject时出现无限递归

    返回:
        绘制边界框[x_min, y_min, x_max, y_max]，表示所有绘制内容的最小包围矩形
    """

    # 避免在异常文件上出现无限递归
    if depth > 6:
        return None

    resources = resources or pikepdf.Dictionary()
    ctm = ctm or Matrix.identity()
    bounds: Optional[List[float]] = None

    # 初始化绘图状态
    line_width = 1.0  # 线宽
    gs_stack: List[Tuple[Matrix, float]] = []  # 图形状态栈
    path_points: List[Tuple[float, float]] = []  # 路径点集合
    current_point_user: Optional[Tuple[float, float]] = None  # 当前点
    subpath_start_user: Optional[Tuple[float, float]] = None  # 子路径起点

    # 移动到指定点
    def move_to(x: float, y: float):
        nonlocal current_point_user, subpath_start_user
        current_point_user = (float(x), float(y))
        subpath_start_user = current_point_user
        path_points.append(ctm.transform(current_point_user))

    # 画线到指定点
    def line_to(x: float, y: float):
        nonlocal current_point_user
        current_point_user = (float(x), float(y))
        path_points.append(ctm.transform(current_point_user))

    # 画三次贝塞尔曲线
    def curve_to(
        x1: float, y1: float, x2: float, y2: float, x3: float, y3: float
    ):
        nonlocal current_point_user
        if current_point_user is None:
            current_point_user = (0.0, 0.0)
            path_points.append(ctm.transform(current_point_user))
        p0 = ctm.transform(current_point_user)
        p1 = ctm.transform((float(x1), float(y1)))
        p2 = ctm.transform((float(x2), float(y2)))
        p3 = ctm.transform((float(x3), float(y3)))
        sampled = bezier_sample(p0, p1, p2, p3)
        # 跳过第一个点以避免重复
        for pt in sampled[1:]:
            path_points.append(pt)
        current_point_user = (float(x3), float(y3))

    # 闭合路径
    def close_path():
        nonlocal current_point_user
        if (
            subpath_start_user is not None
            and current_point_user is not None
            and current_point_user != subpath_start_user
        ):
            path_points.append(ctm.transform(subpath_start_user))
            current_point_user = subpath_start_user

    # 绘制路径（描边或填充）
    def paint(stroke: bool, fill: bool):
        nonlocal bounds, path_points, current_point_user, subpath_start_user
        if not path_points:
            return
        bbox = bounds_from_points(path_points)
        if stroke and bbox is not None:
            # 为描边添加线宽的一半作为填充
            pad = 0.5 * line_width * max_scale_from_matrix(ctm)
            bbox = [bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad]
        if fill or stroke:
            bounds = update_bounds(bounds, bbox)
        # 重置路径状态
        path_points = []
        current_point_user = None
        subpath_start_user = None

    # 处理XObject操作
    def handle_do(xobject_name: Name):
        nonlocal bounds
        xobjs = resources.get(Name.XObject, pikepdf.Dictionary())
        target = xobjs.get(xobject_name)
        if target is None:
            return
        subtype = target.get(Name.Subtype)
        if subtype == Name.Image:
            # 处理图像XObject
            width = float(target.get(Name.Width, 0))
            height = float(target.get(Name.Height, 0))
            pts = [
                ctm.transform((0, 0)),
                ctm.transform((width, 0)),
                ctm.transform((width, height)),
                ctm.transform((0, height)),
            ]
            bounds = update_bounds(bounds, bounds_from_points(pts))
        elif subtype == Name.Form and isinstance(target, pikepdf.Stream):
            # 递归处理表单XObject
            child_ctm = ctm @ Matrix(*target.get(Name.Matrix, [1, 0, 0, 1, 0, 0]))
            child_resources = target.get(Name.Resources, resources)
            child_bounds = compute_painted_bounds(
                target, child_resources, ctm=child_ctm, depth=depth + 1
            )
            bounds = update_bounds(bounds, child_bounds)

    # 解析内容流中的指令
    instructions = pikepdf.parse_content_stream(stream)

    # 处理每个指令
    for inst in instructions:
        op = str(inst.operator)
        operands = list(inst.operands)

        # 保存图形状态
        if op == "q":
            gs_stack.append((ctm, line_width))
        # 恢复图形状态
        elif op == "Q":
            if gs_stack:
                ctm, line_width = gs_stack.pop()
        # 修改变换矩阵
        elif op == "cm":
            if len(operands) == 6:
                m = Matrix(*[float(o) for o in operands])
                ctm = ctm @ m
        # 设置线宽
        elif op == "w":
            if operands:
                line_width = float(operands[0])
        # 设置图形状态
        elif op == "gs":
            extgstate = resources.get(Name.ExtGState, pikepdf.Dictionary())
            gs_name = operands[0] if operands else None
            if gs_name is not None and gs_name in extgstate:
                state = extgstate[gs_name]
                if Name.LW in state:
                    line_width = float(state[Name.LW])
        # 移动到指定点
        elif op == "m":
            move_to(*operands)
        # 画线到指定点
        elif op == "l":
            line_to(*operands)
        elif op == "c":
            curve_to(*operands)
        elif op == "v":
            if current_point_user is None:
                continue
            x2, y2, x3, y3 = [float(v) for v in operands]
            curve_to(current_point_user[0], current_point_user[1], x2, y2, x3, y3)
        elif op == "y":
            if not operands:
                continue
            x1, y1, x3, y3 = [float(v) for v in operands]
            if current_point_user is None:
                continue
            curve_to(x1, y1, x3, y3, x3, y3)
        elif op == "h":
            close_path()
        elif op == "re":
            x, y, w, h = [float(v) for v in operands]
            move_to(x, y)
            line_to(x + w, y)
            line_to(x + w, y + h)
            line_to(x, y + h)
            close_path()
        elif op in {"S", "s"}:
            close_path()
            paint(stroke=True, fill=False)
        elif op in {"f", "F", "f*"}:
            close_path()
            paint(stroke=False, fill=True)
        elif op in {"B", "B*"}:
            close_path()
            paint(stroke=True, fill=True)
        elif op in {"b", "b*"}:
            close_path()
            paint(stroke=True, fill=True)
        elif op == "n":
            path_points = []
            current_point_user = None
            subpath_start_user = None
        elif op == "Do":
            if operands:
                handle_do(operands[0])

    return bounds


def map_bbox_from_appearance_to_page(
    bbox: Sequence[float],
    appearance_bbox: Sequence[float],
    appearance_matrix: Sequence[float],
    annot_rect: Sequence[float],
) -> List[float]:
    """
    将外观流中的边界框映射到页面坐标系
    
    该函数将注释外观流中的边界框转换为页面坐标系中的边界框。PDF注释有三个不同的
    坐标系需要考虑：
    1. 外观流坐标系 - 定义注释外观的内部坐标系
    2. 外观边界框 - 外观流中的边界矩形
    3. 注释矩形 - 页面上的注释位置和大小
    
    转换过程分为两步：
    1. 应用外观流的变换矩阵到边界框，得到外观流坐标系中的变换后边界框
    2. 将外观流坐标系中的边界框映射到页面坐标系

    参数:
        bbox: 外观流中绘制内容的边界框[x_min, y_min, x_max, y_max]
        appearance_bbox: 外观流的边界框[x_min, y_min, x_max, y_max]
        appearance_matrix: 外观流的变换矩阵[a, b, c, d, e, f]
        annot_rect: 页面上注释的矩形[x_min, y_min, x_max, y_max]

    返回:
        页面坐标系中的边界框[x_min, y_min, x_max, y_max]
    """
    # Step 1: apply the appearance stream's /Matrix to painted bounds.
    # 应用外观流的变换矩阵到绘制边界框，得到变换后的四个角点
    matrix = Matrix(*appearance_matrix)
    corners = [
        matrix.transform((bbox[0], bbox[1])),  # 左下角
        matrix.transform((bbox[2], bbox[1])),  # 右下角
        matrix.transform((bbox[2], bbox[3])),  # 右上角
        matrix.transform((bbox[0], bbox[3])),  # 左上角
    ]

    # 提取外观边界框和注释矩形的坐标
    bx0, by0, bx1, by1 = [float(v) for v in appearance_bbox]  # 外观边界框坐标
    rx0, ry0, rx1, ry1 = [float(v) for v in annot_rect]       # 注释矩形坐标
    
    # 计算从外观坐标系到页面坐标系的缩放因子
    sx = (rx1 - rx0) / (bx1 - bx0) if bx1 != bx0 else 1.0  # x方向缩放因子
    sy = (ry1 - ry0) / (by1 - by0) if by1 != by0 else 1.0  # y方向缩放因子

    # 将变换后的角点映射到页面坐标系
    mapped = []
    for x, y in corners:
        mapped.append((rx0 + (x - bx0) * sx, ry0 + (y - by0) * sy))

    # 计算映射后点的最小边界框
    return [
        min(p[0] for p in mapped),  # x_min
        min(p[1] for p in mapped),  # y_min
        max(p[0] for p in mapped),  # x_max
        max(p[1] for p in mapped),  # y_max
    ]


def convert_stamp_to_highlight(
    pdf: pikepdf.Pdf, page: pikepdf.Page, annot: pikepdf.Object
) -> Optional[pikepdf.Object]:
    """
    将半透明印章注释转换为高亮注释
    
    该函数接收一个PDF文档、页面和印章注释对象，将其转换为高亮注释。
    转换过程包括：
    1. 提取印章注释的外观流
    2. 计算外观流中绘制内容的边界框
    3. 将边界框从外观流坐标系映射到页面坐标系
    4. 创建新的高亮注释，使用计算出的边界框和原始颜色/透明度
    
    这种转换对于提高PDF阅读器的兼容性很有用，因为一些阅读器可能不支持
    半透明的印章注释，但通常都支持高亮注释。

    参数:
        pdf: PDF文档对象
        page: 页面对象
        annot: 印章注释对象

    返回:
        新的高亮注释对象，如果转换失败则返回None
    """
    # Alpha值可能存储在/CA(通用)或/ca(仅非描边)中
    ca = float(annot.get("/CA", annot.get("/ca", 1)))
    
    # 获取注释的外观流字典
    ap = annot.get(Name.AP)
    if ap is None:
        return None

    # 获取正常状态下的外观流
    ap_normal = ap.get(Name.N)
    if ap_normal is None:
        return None
        
    # 外观流可能是一个流对象或字典(包含多种状态)
    if isinstance(ap_normal, pikepdf.Stream):
        # 直接使用外观流
        appearance_stream = ap_normal
    elif isinstance(ap_normal, pikepdf.Dictionary):
        # 获取当前状态的外观流
        as_name = annot.get(Name.AS)
        if as_name and as_name in ap_normal:
            appearance_stream = ap_normal[as_name]
        else:
            # 如果没有指定状态，使用第一个可用的外观流
            appearance_stream = next(iter(ap_normal.values()))
    else:
        return None

    # 获取外观流的边界框，这是外观流中的默认边界矩形
    appearance_bbox = appearance_stream.get(Name.BBox)
    if not appearance_bbox:
        return None
        
    # 获取外观流的变换矩阵，如果没有指定则使用单位矩阵
    appearance_matrix = appearance_stream.get(Name.Matrix, [1, 0, 0, 1, 0, 0])
    # 获取外观流的资源字典，包含字体、图像等资源
    appearance_resources = appearance_stream.get(Name.Resources, pikepdf.Dictionary())

    # 计算外观流中绘制内容的边界框
    painted_bounds = compute_painted_bounds(
        appearance_stream, appearance_resources
    )
    if painted_bounds is None:
        return None

    # 获取注释在页面上的矩形区域
    rect = annot.get(Name.Rect)
    if rect is None:
        return None

    # 将外观流中的边界框映射到页面坐标系
    new_rect = map_bbox_from_appearance_to_page(
        painted_bounds, appearance_bbox, appearance_matrix, rect
    )

    # 创建高亮注释所需的四点坐标
    # 四点按左上、右上、左下、右下的顺序排列
    quadpoints = [
        new_rect[0], new_rect[3],  # 左上角 (x_min, y_max)
        new_rect[2], new_rect[3],  # 右上角 (x_max, y_max)
        new_rect[0], new_rect[1],  # 左下角 (x_min, y_min)
        new_rect[2], new_rect[1],  # 右下角 (x_max, y_min)
    ]

    # 获取注释的颜色，默认为黄色
    color = annot.get(Name.C, pikepdf.Array([1, 1, 0]))
    # 获取注释的透明度，默认为不透明
    alpha = float(annot.get("/CA", annot.get("/ca", 1)))

    def rounded_rect_path(w: float, h: float) -> str:
        """
        生成圆角矩形的PDF路径命令
        
        使用贝塞尔曲线近似四分之一圆来创建圆角矩形。圆角半径是矩形宽度和高度的
        18%，这是一个经验值，看起来比较自然。kappa值(0.5522847498)是用于近似
        圆弧的控制点偏移量，这是贝塞尔曲线近似圆弧的标准值。
        
        参数:
            w: 矩形宽度
            h: 矩形高度
            
        返回:
            PDF路径命令字符串
        """
        # 计算圆角半径，取宽度和高度的18%
        r = min(w, h) * 0.18
        # kappa值，用于贝塞尔曲线近似圆弧
        k = 0.5522847498  # control point offset for a circle
        cmds = []
        # 移动到右下角的圆弧起点
        cmds.append(f"{r:.3f} 0 m")
        # 画线到右上角的圆弧起点
        cmds.append(f"{w - r:.3f} 0 l")
        # 右上角圆弧
        cmds.append(f"{w - r + k*r:.3f} 0 {w:.3f} {r - k*r:.3f} {w:.3f} {r:.3f} c")
        # 画线到左上角的圆弧起点
        cmds.append(f"{w:.3f} {h - r:.3f} l")
        # 左上角圆弧
        cmds.append(f"{w:.3f} {h - r + k*r:.3f} {w - r + k*r:.3f} {h:.3f} {w - r:.3f} {h:.3f} c")
        # 画线到左下角的圆弧起点
        cmds.append(f"{r:.3f} {h:.3f} l")
        # 左下角圆弧
        cmds.append(f"{r - k*r:.3f} {h:.3f} 0 {h - r + k*r:.3f} 0 {h - r:.3f} c")
        # 画线到右下角的圆弧起点
        cmds.append(f"0 {r:.3f} l")
        # 右下角圆弧，闭合路径
        cmds.append(f"0 {r - k*r:.3f} {r - k*r:.3f} 0 {r:.3f} 0 c")
        return "\n".join(cmds)

    # 创建简单的高亮外观以提高查看器兼容性
    width = new_rect[2] - new_rect[0]  # 高亮区域宽度
    height = new_rect[3] - new_rect[1]  # 高亮区域高度
    
    # 创建扩展图形状态字典，设置透明度和混合模式
    gs_dict = pikepdf.Dictionary({
        "/Type": Name.ExtGState,  # 对象类型为扩展图形状态
        "/CA": alpha,              # 描边透明度
        "/ca": alpha,              # 填充透明度
        "/BM": Name.Multiply,      # 混合模式为正片叠底
    })
    # 将图形状态字典转换为间接对象
    gs_ref = pdf.make_indirect(gs_dict)
    # 创建资源字典，包含图形状态
    resources = pikepdf.Dictionary({
        "/ExtGState": pikepdf.Dictionary({"/GS": gs_ref})
    })
    
    # 提取颜色分量
    r, g, b = [float(c) for c in color]
    # 生成圆角矩形路径
    rounded_path = rounded_rect_path(width, height)
    # 创建PDF内容流，包含图形状态设置、颜色设置和路径绘制
    content = f"q /GS gs {r} {g} {b} rg\n{rounded_path}\nf Q\n"
    # 创建外观流对象
    appearance_stream = pdf.make_stream(
        content.encode("ascii"),
        pikepdf.Dictionary({
            "/Type": Name.XObject,        # 对象类型为XObject
            "/Subtype": Name.Form,        # 子类型为表单
            "/BBox": pikepdf.Array([0, 0, width, height]),  # 边界框
            "/Resources": resources,      # 资源字典
        }),
    )

    # 创建高亮注释字典
    highlight = pikepdf.Dictionary(
        {
            "/Type": Name.Annot,                    # 对象类型为注释
            "/Subtype": Name.Highlight,              # 子类型为高亮
            "/Rect": pikepdf.Array(new_rect),        # 注释矩形区域
            "/QuadPoints": pikepdf.Array(quadpoints), # 四点坐标
            "/C": color,                            # 注释颜色
            "/CA": alpha,                           # 透明度
            "/F": annot.get(Name.F, 4),             # 注释标志
            "/P": page.obj,                         # 父对象(页面)
            "/NM": pikepdf.String(str(uuid.uuid4())), # 唯一名称
            "/AP": pikepdf.Dictionary({"/N": appearance_stream}), # 外观流
        }
    )

    # 将高亮注释转换为间接对象并返回
    return pdf.make_indirect(highlight)


def _parse_pages_filter(pages: Optional[Sequence[int]]) -> Optional[set[int]]:
    """
    将页码序列转换为集合，用于过滤页面
    
    该函数接收一个页码序列，将其转换为整数集合。这用于过滤需要处理的页面，
    只有在集合中的页面才会被处理。页码从0开始计数。

    参数:
        pages: 页码序列，如果为None则表示处理所有页面

    返回:
        页码集合，如果输入为None则返回None
    """
    if pages is None:
        return None
    # 确保所有页码都是整数
    return set(int(p) for p in pages)


def overlay_pages(
    source_pdf_path: str,
    target_pdf_path: str,
    output_path: str,
    source_pages: Sequence[int],
    target_pages: Sequence[int],
) -> None:
    """
    用源PDF中的页面替换目标PDF中的页面，并保存到输出路径
    
    该函数接收两个PDF文件路径和两组页码，将源PDF中指定的页面替换到目标PDF
    中对应的位置，然后保存结果到输出路径。这对于合并不同PDF文档的部分内容
    非常有用。
    
    页码从0开始计数，源页面列表和目标页面列表的长度必须相同，每个源页面将
    替换对应位置的目标页面。

    参数:
        source_pdf_path: 源PDF文件路径
        target_pdf_path: 目标PDF文件路径
        output_path: 输出PDF文件路径
        source_pages: 源PDF中的页码列表(0-based)
        target_pages: 目标PDF中的页码列表(0-based)
        
    异常:
        ValueError: 当源页面列表和目标页面列表长度不同时
        IndexError: 当页码超出PDF页面范围时
    """
    # 检查源页面列表和目标页面列表长度是否相同
    if len(source_pages) != len(target_pages):
        raise ValueError("source_pages and target_pages must have the same length")

    # 使用上下文管理器确保文件正确关闭
    with pikepdf.Pdf.open(source_pdf_path) as source_pdf, pikepdf.Pdf.open(
        target_pdf_path
    ) as target_pdf:
        # 遍历源页面和目标页面的对应关系
        for src_idx, tgt_idx in zip(source_pages, target_pages):
            # 检查源页面索引是否有效
            if src_idx < 0 or src_idx >= len(source_pdf.pages):
                raise IndexError(f"source page {src_idx + 1} out of range")
            # 检查目标页面索引是否有效
            if tgt_idx < 0 or tgt_idx >= len(target_pdf.pages):
                raise IndexError(f"target page {tgt_idx + 1} out of range")
            # 让pikepdf的pages接口完成跨文档复制（会自动 copy_foreign）
            target_pdf.pages[tgt_idx] = source_pdf.pages[src_idx]

        # 保存修改后的目标PDF
        target_pdf.save(output_path)


def process(pdf_path: str, output_path: str, pages: Optional[Sequence[int]] = None) -> None:
    """
    处理PDF文件，将半透明印章注释转换为高亮注释
    
    该函数打开指定的PDF文件，遍历所有页面(或指定的页面)，查找半透明的印章注释，
    并将它们转换为高亮注释。转换后的PDF将保存到指定的输出路径。
    
    转换过程：
    1. 打开PDF文件
    2. 遍历所有页面(或指定的页面)
    3. 查找每个页面上的注释
    4. 对于半透明的印章注释，转换为高亮注释
    5. 保存修改后的PDF文件

    参数:
        pdf_path: 输入PDF文件路径
        output_path: 输出PDF文件路径
        pages: 要处理的页码列表，如果为None则处理所有页面
    """
    # 将页码列表转换为集合，用于过滤页面
    pages_filter = _parse_pages_filter(pages)
    # 打开PDF文件
    pdf = pikepdf.Pdf.open(pdf_path)
    converted = 0  # 记录转换的注释数量

    # 遍历PDF中的所有页面
    for page_index, page in enumerate(pdf.pages):
        # 如果指定了页面过滤器，且当前页面不在过滤器中，则跳过
        if pages_filter is not None and page_index not in pages_filter:
            continue
            
        # 获取页面上的注释列表
        annots = page.get(Name.Annots)
        if not annots:
            continue  # 如果没有注释，跳过当前页面

        # 创建新的注释数组，用于存储转换后的注释
        new_annots = pikepdf.Array()
        changed = False  # 标记当前页面是否有注释被转换
        
        # 遍历页面上的所有注释
        for annot in annots:
            # 获取注释的子类型
            subtype = annot.get(Name.Subtype)
            # 获取注释的透明度值
            ca_val = float(annot.get("/CA", annot.get("/ca", 1)))
            
            # 如果是半透明的印章注释，则转换为高亮注释
            if subtype == Name.Stamp and ca_val < 1:
                highlight = convert_stamp_to_highlight(pdf, page, annot)
                if highlight is not None:
                    # 将转换后的高亮注释添加到新注释列表
                    new_annots.append(highlight)
                    converted += 1  # 增加转换计数
                    changed = True  # 标记页面已更改
                    continue  # 跳过添加原始注释
            # 如果不是印章注释或转换失败，保留原始注释
            new_annots.append(annot)

        # 如果页面有更改，更新页面的注释列表
        if changed:
            page[Name.Annots] = new_annots

    # 保存修改后的PDF文件
    pdf.save(output_path)
    # 打印转换结果
    print(f"Converted {converted} translucent stamp annotations -> highlights.")
    print(f"Saved to: {output_path}")


def parse_args() -> argparse.Namespace:
    """
    解析命令行参数
    
    该函数设置并解析命令行参数，包括输入PDF文件路径、输出PDF文件路径和要处理的页面范围。
    页码是1-based的，与用户习惯一致，但内部处理时会转换为0-based。
    
    返回:
        包含解析后参数的命名空间对象
    """
    parser = argparse.ArgumentParser(
        description="Convert translucent /Stamp annotations to /Highlight using appearance painted bounds."
    )
    # 输入PDF文件路径（必需参数）
    parser.add_argument(
        "input_pdf", help="Path to the PDF containing stamp annotations (e.g., input.pdf)"
    )
    # 输出PDF文件路径（可选参数，默认为output.pdf）
    parser.add_argument(
        "output_pdf",
        nargs="?",
        default="output.pdf",
        help="Where to write the updated PDF (default: output.pdf)",
    )
    # 要处理的页面范围（可选参数）
    parser.add_argument(
        "--pages",
        help="Comma-separated page numbers to process (1-based). Example: 1,3,5-7",
    )
    return parser.parse_args()


def parse_page_ranges(pages_arg: Optional[str]) -> Optional[List[int]]:
    """
    解析页面范围字符串为页码列表
    
    该函数将逗号分隔的页码字符串（可能包含范围）转换为页码列表。
    页码是1-based的，但函数会将其转换为0-based以供内部使用。
    
    支持的格式：
    - 单个页码：1, 2, 3
    - 页码范围：1-5（包含1到5的所有页面）
    - 混合：1,3,5-7（表示第1、3页和第5到7页）
    
    参数:
        pages_arg: 页面范围字符串，如"1,3,5-7"
        
    返回:
        0-based页码列表，如果输入为None或空字符串则返回None
    """
    if not pages_arg:
        return None
        
    result: List[int] = []
    # 按逗号分割页面范围字符串
    for part in pages_arg.split(","):
        part = part.strip()
        if not part:
            continue  # 跳过空字符串
            
        # 处理页码范围（如"5-7"）
        if "-" in part:
            start, end = part.split("-", 1)
            # 将1-based页码转换为0-based
            start_i = int(start) - 1
            end_i = int(end) - 1
            # 添加范围内的所有页码
            result.extend(list(range(start_i, end_i + 1)))
        else:
            # 处理单个页码
            result.append(int(part) - 1)  # 转换为0-based
            
    return result


if __name__ == "__main__":
    # 解析命令行参数
    args = parse_args()
    # 解析页面范围字符串为页码列表
    pages = parse_page_ranges(args.pages)
    # 处理PDF文件，将半透明印章注释转换为高亮注释
    process(args.input_pdf, args.output_pdf, pages)
