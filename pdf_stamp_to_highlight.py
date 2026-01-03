# 导入必要的库
import argparse  # 用于解析命令行参数
import math  # 数学运算
import uuid  # 用于生成唯一标识符
from typing import Iterable, List, Optional, Sequence, Tuple  # 类型提示支持

import pikepdf  # PDF处理库
from pikepdf import Matrix, Name  # PDF矩阵和名称对象


def bounds_from_points(points: Iterable[Tuple[float, float]]) -> Optional[List[float]]:
    """
    从点集合计算边界框

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

    参数:
        p0, p1, p2, p3: 贝塞尔曲线的四个控制点
        steps: 采样步数，默认为20

    返回:
        采样点列表
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

    参数:
        existing: 现有边界框[x_min, y_min, x_max, y_max]
        new: 新边界框[x_min, y_min, x_max, y_max]

    返回:
        合并后的边界框
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

    只支持最小操作子集(路径、Do、cm、q/Q、w、gs)，这对于典型的由表单或图像构建的
    印章外观流是足够的。

    参数:
        stream: PDF内容流
        resources: 资源字典
        ctm: 当前变换矩阵，默认为单位矩阵
        depth: 递归深度，用于避免无限递归

    返回:
        绘制边界框[x_min, y_min, x_max, y_max]
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
    # Step 1: apply the appearance stream's /Matrix to painted bounds.
    matrix = Matrix(*appearance_matrix)
    corners = [
        matrix.transform((bbox[0], bbox[1])),
        matrix.transform((bbox[2], bbox[1])),
        matrix.transform((bbox[2], bbox[3])),
        matrix.transform((bbox[0], bbox[3])),
    ]

    bx0, by0, bx1, by1 = [float(v) for v in appearance_bbox]
    rx0, ry0, rx1, ry1 = [float(v) for v in annot_rect]
    sx = (rx1 - rx0) / (bx1 - bx0) if bx1 != bx0 else 1.0
    sy = (ry1 - ry0) / (by1 - by0) if by1 != by0 else 1.0

    mapped = []
    for x, y in corners:
        mapped.append((rx0 + (x - bx0) * sx, ry0 + (y - by0) * sy))

    return [
        min(p[0] for p in mapped),
        min(p[1] for p in mapped),
        max(p[0] for p in mapped),
        max(p[1] for p in mapped),
    ]


def convert_stamp_to_highlight(
    pdf: pikepdf.Pdf, page: pikepdf.Page, annot: pikepdf.Object
) -> Optional[pikepdf.Object]:
    # Alpha value may be stored as /CA (common) or /ca (non-stroking only).
    ca = float(annot.get("/CA", annot.get("/ca", 1)))
    ap = annot.get(Name.AP)
    if ap is None:
        return None

    ap_normal = ap.get(Name.N)
    if ap_normal is None:
        return None
    if isinstance(ap_normal, pikepdf.Stream):
        appearance_stream = ap_normal
    elif isinstance(ap_normal, pikepdf.Dictionary):
        as_name = annot.get(Name.AS)
        if as_name and as_name in ap_normal:
            appearance_stream = ap_normal[as_name]
        else:
            # Pick the first available appearance state.
            appearance_stream = next(iter(ap_normal.values()))
    else:
        return None

    appearance_bbox = appearance_stream.get(Name.BBox)
    if not appearance_bbox:
        return None
    appearance_matrix = appearance_stream.get(Name.Matrix, [1, 0, 0, 1, 0, 0])
    appearance_resources = appearance_stream.get(Name.Resources, pikepdf.Dictionary())

    painted_bounds = compute_painted_bounds(
        appearance_stream, appearance_resources
    )
    if painted_bounds is None:
        return None

    rect = annot.get(Name.Rect)
    if rect is None:
        return None

    new_rect = map_bbox_from_appearance_to_page(
        painted_bounds, appearance_bbox, appearance_matrix, rect
    )

    quadpoints = [
        new_rect[0],
        new_rect[3],
        new_rect[2],
        new_rect[3],
        new_rect[0],
        new_rect[1],
        new_rect[2],
        new_rect[1],
    ]

    color = annot.get(Name.C, pikepdf.Array([1, 1, 0]))
    alpha = float(annot.get("/CA", annot.get("/ca", 1)))

    def rounded_rect_path(w: float, h: float) -> str:
        # Approximate quarter circles with Bezier curves (kappa)
        r = min(w, h) * 0.18
        k = 0.5522847498  # control point offset for a circle
        cmds = []
        cmds.append(f"{r:.3f} 0 m")
        cmds.append(f"{w - r:.3f} 0 l")
        cmds.append(f"{w - r + k*r:.3f} 0 {w:.3f} {r - k*r:.3f} {w:.3f} {r:.3f} c")
        cmds.append(f"{w:.3f} {h - r:.3f} l")
        cmds.append(f"{w:.3f} {h - r + k*r:.3f} {w - r + k*r:.3f} {h:.3f} {w - r:.3f} {h:.3f} c")
        cmds.append(f"{r:.3f} {h:.3f} l")
        cmds.append(f"{r - k*r:.3f} {h:.3f} 0 {h - r + k*r:.3f} 0 {h - r:.3f} c")
        cmds.append(f"0 {r:.3f} l")
        cmds.append(f"0 {r - k*r:.3f} {r - k*r:.3f} 0 {r:.3f} 0 c")
        return "\n".join(cmds)

    # Build a simple highlight appearance to improve viewer compatibility.
    width = new_rect[2] - new_rect[0]
    height = new_rect[3] - new_rect[1]
    gs_dict = pikepdf.Dictionary({
        "/Type": Name.ExtGState,
        "/CA": alpha,
        "/ca": alpha,
        "/BM": Name.Multiply,
    })
    gs_ref = pdf.make_indirect(gs_dict)
    resources = pikepdf.Dictionary({
        "/ExtGState": pikepdf.Dictionary({"/GS": gs_ref})
    })
    r, g, b = [float(c) for c in color]
    rounded_path = rounded_rect_path(width, height)
    content = f"q /GS gs {r} {g} {b} rg\n{rounded_path}\nf Q\n"
    appearance_stream = pdf.make_stream(
        content.encode("ascii"),
        pikepdf.Dictionary({
            "/Type": Name.XObject,
            "/Subtype": Name.Form,
            "/BBox": pikepdf.Array([0, 0, width, height]),
            "/Resources": resources,
        }),
    )

    highlight = pikepdf.Dictionary(
        {
            "/Type": Name.Annot,
            "/Subtype": Name.Highlight,
            "/Rect": pikepdf.Array(new_rect),
            "/QuadPoints": pikepdf.Array(quadpoints),
            "/C": color,
            "/CA": alpha,
            "/F": annot.get(Name.F, 4),
            "/P": page.obj,
            "/NM": pikepdf.String(str(uuid.uuid4())),
            "/AP": pikepdf.Dictionary({"/N": appearance_stream}),
        }
    )

    return pdf.make_indirect(highlight)


def _parse_pages_filter(pages: Optional[Sequence[int]]) -> Optional[set[int]]:
    if pages is None:
        return None
    return set(int(p) for p in pages)


def process(pdf_path: str, output_path: str, pages: Optional[Sequence[int]] = None) -> None:
    pages_filter = _parse_pages_filter(pages)
    pdf = pikepdf.Pdf.open(pdf_path)
    converted = 0

    for page_index, page in enumerate(pdf.pages):
        if pages_filter is not None and page_index not in pages_filter:
            continue
        annots = page.get(Name.Annots)
        if not annots:
            continue

        new_annots = pikepdf.Array()
        changed = False
        for annot in annots:
            subtype = annot.get(Name.Subtype)
            ca_val = float(annot.get("/CA", annot.get("/ca", 1)))
            if subtype == Name.Stamp and ca_val < 1:
                highlight = convert_stamp_to_highlight(pdf, page, annot)
                if highlight is not None:
                    new_annots.append(highlight)
                    converted += 1
                    changed = True
                    continue
            new_annots.append(annot)

        if changed:
            page[Name.Annots] = new_annots

    pdf.save(output_path)
    print(f"Converted {converted} translucent stamp annotations -> highlights.")
    print(f"Saved to: {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert translucent /Stamp annotations to /Highlight using appearance painted bounds."
    )
    parser.add_argument(
        "input_pdf", help="Path to the PDF containing stamp annotations (e.g., input.pdf)"
    )
    parser.add_argument(
        "output_pdf",
        nargs="?",
        default="output.pdf",
        help="Where to write the updated PDF (default: output.pdf)",
    )
    parser.add_argument(
        "--pages",
        help="Comma-separated page numbers to process (1-based). Example: 1,3,5-7",
    )
    return parser.parse_args()


def parse_page_ranges(pages_arg: Optional[str]) -> Optional[List[int]]:
    if not pages_arg:
        return None
    result: List[int] = []
    for part in pages_arg.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            start_i = int(start) - 1
            end_i = int(end) - 1
            result.extend(list(range(start_i, end_i + 1)))
        else:
            result.append(int(part) - 1)
    return result


if __name__ == "__main__":
    args = parse_args()
    pages = parse_page_ranges(args.pages)
    process(args.input_pdf, args.output_pdf, pages)
