# -*- coding: utf-8 -*-
"""
psd2spine — 把 See-through 导出的分层 PSD 转成可导入 Spine 的工程。
v1: Essential 版输出 —— 完整人形骨架 + slot + region 附件(无 mesh/权重)。

用法:
    python psd2spine.py <input.psd> <out_dir> [--spine-version 4.3.17]

不指定 --spine-version 时,自动探测本机安装的 Spine 版本;探测不到则用默认值。
"""
import os
import re
import sys
import json
import math
import argparse
import numpy as np
from psd_tools import PSDImage


def _rot(vx, vy, deg):
    """把向量 (vx,vy) 旋转 deg 度。"""
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return (vx * c - vy * s, vx * s + vy * c)

DEFAULT_SPINE_VERSION = "4.2.00"


def detect_spine_version():
    """探测本机 Spine 版本。

    Spine 4.x 启动器(正式版与试用版)会把各版本下载到
    `<用户目录>\\Spine[Trial]\\updates\\<版本号>\\`。扫这些目录,
    取版本号最高的子目录名作为版本(原样返回,如 '4.3.17')。
    """
    home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    local = os.environ.get("LOCALAPPDATA", "")
    roots = []
    for base in (home, local):
        if not base:
            continue
        for app in ("Spine", "SpineTrial"):
            roots.append(os.path.join(base, app, "updates"))

    found = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            # updates 下的条目名即版本号(可能是目录或文件/junction,都接受)
            if re.fullmatch(r"\d+\.\d+\.\d+", name):
                found.add(name)

    if not found:
        return None
    best = max(found, key=lambda v: tuple(int(x) for x in v.split(".")))
    print("[auto] detected Spine version -> %s" % best)
    return best


VERSION_RE = re.compile(r"\d+\.\d+\.\d+")


def resolve_spine_version(cli_value=None, allow_prompt=True):
    """版本号解析顺序:命令行指定 -> 自动探测 -> 手动输入兜底 -> 默认值。

    allow_prompt=True 且处于交互式终端时,探测失败会提示用户手动输入。
    """
    if cli_value:
        return cli_value.strip()
    ver = detect_spine_version()
    if ver:
        return ver
    if allow_prompt and sys.stdin and sys.stdin.isatty():
        print("[warn] 未能自动探测到 Spine 版本。")
        while True:
            raw = input("请手动输入你的 Spine 版本号(如 4.3.17,直接回车用默认 %s):"
                        % DEFAULT_SPINE_VERSION).strip()
            if not raw:
                return DEFAULT_SPINE_VERSION
            if VERSION_RE.fullmatch(raw):
                return raw
            print("  格式应为 x.y.z,请重输。")
    print("[warn] 未指定也未探测到 Spine 版本,使用默认 %s。"
          "可加 --spine-version <你的版本> 覆盖。" % DEFAULT_SPINE_VERSION)
    return DEFAULT_SPINE_VERSION


def slug(name):
    return re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip()).strip("_").lower()


# 语义图层名 -> 绑定的骨骼名
LAYER_TO_BONE = {
    "footwear": "foot",
    "handwear_r": "arm-r",
    "handwear_l": "arm-l",
    "topwear": "torso",
    "neck": "neck",
    "back_hair": "head",
    "face": "head",
    "headwear": "head",
    "ears_l": "head", "ears_r": "head",
    "nose": "head", "mouth": "head",
    "eyebrow_l": "head", "eyebrow_r": "head",
    "eyelash_l": "head", "eyelash_r": "head",
    "irides_l": "head", "irides_r": "head",
    "eyewhite_l": "head", "eyewhite_r": "head",
}

# 骨骼层级模板:bone -> parent
BONE_PARENT = {
    "root": None,
    "hip": "root",
    "torso": "hip",
    "neck": "torso",
    "head": "neck",
    "arm-l": "torso",
    "arm-r": "torso",
    "foot": "hip",
}


# Professional 版:可形变图层 -> 沿垂直方向的骨链(顶 -> 底)
DEFORM_CHAINS = {
    "handwear_l": ["arm-l", "arm-l-2"],
    "handwear_r": ["arm-r", "arm-r-2"],
    "topwear": ["torso", "hip"],
}
MESH_ROWS = 10  # 网格沿主轴的分段数(越大越平滑、越贴合剪影)
DEFAULT_PROFILE = "both"


def center(bbox):
    l, t, r, b = bbox
    return ((l + r) / 2.0, (t + b) / 2.0)


def _export_layers(psd, img_dir):
    """导出每层 PNG,返回 (layers, bboxes)。顺序 = 文件顺序 = 绘制顺序(底->顶)。"""
    os.makedirs(img_dir, exist_ok=True)
    layers, bboxes = [], {}
    for layer in psd:
        if layer.bbox == (0, 0, 0, 0):
            continue
        key = slug(layer.name)
        png = layer.composite()
        png.save(os.path.join(img_dir, key + ".png"))
        l, t, r, b = layer.bbox
        alpha = np.asarray(png.convert("RGBA"))[..., 3] > 10  # 不透明像素掩码
        layers.append({"key": key, "bbox": layer.bbox, "w": r - l, "h": b - t,
                       "alpha": alpha})
        bboxes[key] = layer.bbox
    return layers, bboxes


def _row_extent(mask, frac):
    """取掩码第 frac(0~1)行的最左/最右不透明列;空行向邻近行搜索。"""
    h, w = mask.shape
    ri = max(0, min(h - 1, int(round(frac * (h - 1)))))
    for d in range(h):
        for rr in (ri - d, ri + d):
            if 0 <= rr < h:
                cols = np.nonzero(mask[rr])[0]
                if cols.size:
                    return int(cols[0]), int(cols[-1])
    return 0, w - 1


def _bone_anchors(bboxes, W, H, cx, professional):
    """推算骨骼锚点(PSD 坐标)、层级、顺序。"""
    def cx_of(*keys):
        for k in keys:
            if k in bboxes:
                return center(bboxes[k])[0]
        return cx

    neck_bb = bboxes.get("neck")
    face_bb = bboxes.get("face")
    top_bb = bboxes.get("topwear")
    foot_bb = bboxes.get("footwear")
    hl = bboxes.get("handwear_l")
    hr = bboxes.get("handwear_r")

    head_cx = cx_of("face", "neck", "headwear")
    neck_top = neck_bb[1] if neck_bb else (face_bb[3] if face_bb else 232)
    neck_bot = neck_bb[3] if neck_bb else neck_top + 100
    torso_cx = cx_of("topwear")
    top_t = top_bb[1] if top_bb else neck_bot
    top_b = top_bb[3] if top_bb else H * 0.7
    hip_y = top_t + 0.70 * (top_b - top_t)

    bone_psd = {
        # root 落在画布底部中心 -> skel 坐标 (0,0),与 Spine 原点约定一致
        "root": (cx, H),
        "hip": (torso_cx, hip_y),
        "torso": (torso_cx, neck_bot),
        "neck": (head_cx, neck_bot),
        "head": (head_cx, neck_top),
        "arm-l": (hl[0], hl[1]) if hl else (torso_cx + 80, neck_bot),
        "arm-r": (hr[2], hr[1]) if hr else (torso_cx - 80, neck_bot),
        "foot": center(foot_bb) if foot_bb else (cx, H * 0.95),
    }
    order = ["root", "hip", "torso", "neck", "head", "arm-l", "arm-r", "foot"]
    parent = dict(BONE_PARENT)

    if professional:
        # 为可弯肢体加肘/腕子骨(置于该层包围盒底部中心)
        if hl:
            bone_psd["arm-l-2"] = ((hl[0] + hl[2]) / 2.0, hl[3])
            parent["arm-l-2"] = "arm-l"
            order.append("arm-l-2")
        if hr:
            bone_psd["arm-r-2"] = ((hr[0] + hr[2]) / 2.0, hr[3])
            parent["arm-r-2"] = "arm-r"
            order.append("arm-r-2")
    return bone_psd, order, parent


def _strip_mesh(rec, chain, bone_idx, bone_abs, world_rot, to_skel,
                rows=MESH_ROWS):
    """为可形变层生成沿垂直方向的条带网格 + 双骨距离权重(可弯,关节后续手调)。"""
    l, t, r, b = rec["bbox"]
    w, h = rec["w"], rec["h"]
    T, B = chain[0], chain[1]
    Tx, Ty = bone_abs[T]
    Bx, By = bone_abs[B]
    iT, iB = bone_idx[T], bone_idx[B]
    rT, rB = world_rot[T], world_rot[B]   # 各骨朝向,顶点局部坐标需反向旋转

    # 周边顶点:左右列按每行真实剪影(最左/最右不透明像素)走,贴合边缘
    mask = rec.get("alpha")
    if mask is not None and mask.any():
        exts = [_row_extent(mask, i / rows) for i in range(rows + 1)]
    else:
        exts = [(0, w - 1) for _ in range(rows + 1)]   # 兜底:矩形
    pts = [(l + exts[i][0], t + i / rows * h) for i in range(rows + 1)]
    pts += [(l + exts[rows - i][1], b - i / rows * h) for i in range(rows + 1)]

    uvs, verts = [], []
    for px, py in pts:
        uvs += [round((px - l) / w, 5), round((py - t) / h, 5)]
        sx, sy = to_skel(px, py)
        lTx, lTy = _rot(sx - Tx, sy - Ty, -rT)
        lBx, lBy = _rot(sx - Bx, sy - By, -rB)
        tw = 0.5 if Ty == By else max(0.0, min(1.0, (Ty - sy) / (Ty - By)))
        wB, wT = tw, 1.0 - tw
        if wT < 0.001:
            verts += [1, iB, round(lBx, 2), round(lBy, 2), 1]
        elif wB < 0.001:
            verts += [1, iT, round(lTx, 2), round(lTy, 2), 1]
        else:
            verts += [2, iT, round(lTx, 2), round(lTy, 2), round(wT, 4),
                      iB, round(lBx, 2), round(lBy, 2), round(wB, 4)]

    tris = []
    last = 2 * rows + 1
    for i in range(rows):
        a, b2 = i, i + 1
        ri, ri1 = last - i, last - (i + 1)
        tris += [a, b2, ri1, a, ri1, ri]

    # hull 边(Spine 边索引 = 顶点序号 * 2);3.8 解析器要求 mesh 带 edges
    n = len(pts)
    edges = []
    for i in range(n):
        j = (i + 1) % n
        edges += [i * 2, j * 2]

    return {"type": "mesh", "uvs": uvs, "triangles": tris,
            "vertices": verts, "hull": n, "edges": edges,
            "width": w, "height": h}


# 人形骨架延续主链的"主子骨"(用于给骨头定朝向);其余为末端骨
PRIMARY_CHILD = {"hip": "torso", "torso": "neck", "neck": "head"}


def _bone_orient(order, parent, bone_abs, bboxes, cx, H, professional):
    """给每根骨计算世界朝向(度)与长度,使其指向子骨/肢体末端,显示成连贯骨架。
    root 保持 0 长度、0 旋转作为原点。"""
    def to_skel(px, py):
        return (px - cx, H - py)

    pc = dict(PRIMARY_CHILD)
    if professional:
        if "arm-l-2" in bone_abs:
            pc["arm-l"] = "arm-l-2"
        if "arm-r-2" in bone_abs:
            pc["arm-r"] = "arm-r-2"

    hl, hr = bboxes.get("handwear_l"), bboxes.get("handwear_r")
    foot = bboxes.get("footwear")
    # 末端骨的几何目标点(世界坐标)
    head_tip_y = min([bboxes[k][1] for k in ("headwear", "face", "back_hair")
                      if k in bboxes] or [bone_abs["head"][1]])

    def leaf_target(name):
        if name == "head":
            return to_skel(bone_abs["head"][0] + cx, head_tip_y)
        if name == "arm-l" and hl:
            return to_skel((hl[0] + hl[2]) / 2.0, hl[3])
        if name == "arm-r" and hr:
            return to_skel((hr[0] + hr[2]) / 2.0, hr[3])
        if name == "arm-l-2" and hl:
            return to_skel((hl[0] + hl[2]) / 2.0, hl[3] + (hl[3] - hl[1]) * 0.3)
        if name == "arm-r-2" and hr:
            return to_skel((hr[0] + hr[2]) / 2.0, hr[3] + (hr[3] - hr[1]) * 0.3)
        if name == "foot" and foot:
            return to_skel((foot[0] + foot[2]) / 2.0, foot[3])
        bx, by = bone_abs[name]      # 兜底:向下默认一截
        return (bx, by - 50)

    world_rot, length = {"root": 0.0}, {"root": 0.0}
    for name in order:
        if name == "root":
            continue
        bx, by = bone_abs[name]
        tgt = bone_abs[pc[name]] if name in pc and pc[name] in bone_abs \
            else leaf_target(name)
        dx, dy = tgt[0] - bx, tgt[1] - by
        dist = math.hypot(dx, dy)
        if dist < 1e-3:
            world_rot[name] = world_rot.get(parent[name], 0.0)
            length[name] = 40.0
        else:
            world_rot[name] = math.degrees(math.atan2(dy, dx))
            length[name] = round(dist, 2)
    return world_rot, length


def _build_skeleton(layers, bboxes, W, H, cx, spine_version, professional):
    bone_psd, order, parent = _bone_anchors(bboxes, W, H, cx, professional)

    def to_skel(px, py):
        return (px - cx, H - py)

    bone_abs = {k: to_skel(*v) for k, v in bone_psd.items()}
    bone_idx = {name: i for i, name in enumerate(order)}
    world_rot, length = _bone_orient(order, parent, bone_abs, bboxes,
                                     cx, H, professional)

    bones = []
    for name in order:
        p = parent[name]
        ax, ay = bone_abs[name]
        e = {"name": name}
        if p:
            e["parent"] = p
            pax, pay = bone_abs[p]
            # 子骨局部坐标 = 在父骨(已旋转)坐标系中的位置
            lx, ly = _rot(ax - pax, ay - pay, -world_rot[p])
            e["x"], e["y"] = round(lx, 2), round(ly, 2)
            lrot = world_rot[name] - world_rot[p]
            if abs(lrot) > 1e-4:
                e["rotation"] = round(lrot, 2)
        if length.get(name):
            e["length"] = length[name]
        bones.append(e)

    slots, attachments = [], {}
    for lay in layers:
        key = lay["key"]
        chain = DEFORM_CHAINS.get(key) if professional else None
        if chain and all(b in bone_idx for b in chain):
            bone = chain[0]
            attachments[key] = {key: _strip_mesh(lay, chain, bone_idx,
                                                  bone_abs, world_rot, to_skel)}
        else:
            bone = LAYER_TO_BONE.get(key, "root")
            ccx, ccy = center(lay["bbox"])
            sx, sy = to_skel(ccx, ccy)
            bax, bay = bone_abs[bone]
            wr = world_rot[bone]
            # 旋转补偿:偏移按 -骨朝向旋转,附件加 -骨朝向 使图片保持正立
            lx, ly = _rot(sx - bax, sy - bay, -wr)
            att = {"x": round(lx, 2), "y": round(ly, 2),
                   "width": lay["w"], "height": lay["h"]}
            if abs(wr) > 1e-4:
                att["rotation"] = round(-wr, 2)
            attachments[key] = {key: att}
        slots.append({"name": key, "bone": bone, "attachment": key})

    return {
        "skeleton": {"spine": spine_version, "images": "./images/",
                     "width": W, "height": H},
        "bones": bones,
        "slots": slots,
        "skins": [{"name": "default", "attachments": attachments}],
        # 不写空动画:3.8 解析器会拒绝无时间轴的空动画;动画留给用户在 Spine 里建
    }


def _write(skeleton, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(skeleton, f, ensure_ascii=False, indent=2)
    print("OK ->", path, "| bones:", len(skeleton["bones"]),
          "slots:", len(skeleton["slots"]))


# ---------------- 通用模式(任意 PSD)----------------
# See-through 的结构性关键层;命中足够多则判定为 See-through 智能模式
SEETHROUGH_SIGNATURE = {"face", "topwear", "handwear_l", "handwear_r",
                        "footwear", "neck"}


def _looks_seethrough(psd):
    keys = set()
    for layer in psd:
        if not layer.is_group():
            keys.add(slug(layer.name))
    return len(SEETHROUGH_SIGNATURE & keys) >= 3


GENERIC_MESH_GRID = 3   # generic-pro 每层网格的格数(cols=rows)


def _grid_mesh(bbox, bone_xy, to_skel, wrot=0.0, n=GENERIC_MESH_GRID):
    """把一张图层做成 n×n 网格(无权重),供 Spine 里自由网格形变 FFD。
    顶点相对该层自己的骨骼(骨在层中心,朝向 wrot,顶点需反向旋转)。"""
    l, t, r, b = bbox
    w, h = r - l, b - t
    bx, by = bone_xy

    # 顶点 (col i, row j) 排序:先周边(顺时针一圈),再内部
    order = []
    for i in range(n + 1):      # 上边 左->右
        order.append((i, 0))
    for j in range(1, n + 1):   # 右边 上->下
        order.append((n, j))
    for i in range(n - 1, -1, -1):  # 下边 右->左
        order.append((i, n))
    for j in range(n - 1, 0, -1):   # 左边 下->上
        order.append((0, j))
    hull = len(order)
    for j in range(1, n):       # 内部
        for i in range(1, n):
            order.append((i, j))
    idx = {ij: k for k, ij in enumerate(order)}

    uvs, verts = [], []
    for i, j in order:
        px, py = l + i / n * w, t + j / n * h
        uvs += [round(i / n, 5), round(j / n, 5)]
        sx, sy = to_skel(px, py)
        lx, ly = _rot(sx - bx, sy - by, -wrot)   # 反向旋转到骨局部
        verts += [round(lx, 2), round(ly, 2)]

    tris = []
    for j in range(n):
        for i in range(n):
            a, b2 = idx[(i, j)], idx[(i + 1, j)]
            c, d = idx[(i + 1, j + 1)], idx[(i, j + 1)]
            tris += [a, b2, c, a, c, d]

    edges = []
    for k in range(hull):
        edges += [k * 2, ((k + 1) % hull) * 2]

    return {"type": "mesh", "uvs": uvs, "triangles": tris, "vertices": verts,
            "hull": hull, "edges": edges, "width": w, "height": h}


def _generic_collect(psd, img_dir, to_skel):
    """遍历一次:建骨架(每叶子层一根骨,组->层级)、导出 PNG、收集叶子记录。"""
    os.makedirs(img_dir, exist_ok=True)
    bones = [{"name": "root"}]
    bone_abs = {"root": (0.0, 0.0)}   # root 在画布底部中心 -> (0,0)
    world_rot = {"root": 0.0}
    used, leaves = set(), []

    def uniq(name):
        base = slug(name) or "layer"
        k, i = base, 2
        while k in used:
            k = "%s_%d" % (base, i)
            i += 1
        used.add(k)
        return k

    def add_bone(name, parent, ax, ay, wrot=0.0, wlen=0.0):
        pax, pay = bone_abs[parent]
        e = {"name": name, "parent": parent,
             "x": round(ax - pax, 2), "y": round(ay - pay, 2)}
        if abs(wrot) > 1e-4:      # 父骨 rotation=0,故局部旋转=世界旋转
            e["rotation"] = round(wrot, 2)
        if wlen:
            e["length"] = round(wlen, 2)
        bones.append(e)
        bone_abs[name] = (ax, ay)
        world_rot[name] = wrot

    def walk(container, parent_bone):
        for layer in container:   # 自然顺序 = 底->顶 = 绘制顺序
            bb = layer.bbox
            if layer.is_group():
                if bb == (0, 0, 0, 0):
                    walk(layer, parent_bone)
                    continue
                key = uniq(layer.name)
                add_bone(key, parent_bone, *to_skel(*center(bb)))
                walk(layer, key)
            else:
                if bb == (0, 0, 0, 0):
                    continue
                key = uniq(layer.name)
                layer.composite().save(os.path.join(img_dir, key + ".png"))
                l, t, r, b = bb
                lw, lh = r - l, b - t
                # 可见骨:沿图层长边,长度取长边一半
                wrot = 90.0 if lh >= lw else 0.0
                add_bone(key, parent_bone, *to_skel(*center(bb)),
                         wrot=wrot, wlen=0.5 * max(lw, lh))
                leaves.append({"key": key, "bbox": bb})

    walk(psd, "root")
    return bones, bone_abs, leaves, world_rot


def _generic_skeleton(bones, bone_abs, leaves, world_rot, W, H, cx,
                      spine_version, professional, to_skel):
    slots, atts = [], {}
    for lf in leaves:
        key, bb = lf["key"], lf["bbox"]
        wr = world_rot.get(key, 0.0)
        slots.append({"name": key, "bone": key, "attachment": key})
        if professional:
            atts[key] = {key: _grid_mesh(bb, bone_abs[key], to_skel, wr)}
        else:
            l, t, r, b = bb
            att = {"x": 0, "y": 0, "width": r - l, "height": b - t}
            if abs(wr) > 1e-4:    # 骨已旋转,附件反向旋转保持图片正立
                att["rotation"] = round(-wr, 2)
            atts[key] = {key: att}
    return {
        "skeleton": {"spine": spine_version, "images": "./images/",
                     "width": W, "height": H},
        "bones": bones,
        "slots": slots,
        "skins": [{"name": "default", "attachments": atts}],
    }


def main(psd_path, out_dir, spine_version=DEFAULT_SPINE_VERSION,
         profile=DEFAULT_PROFILE, mode="auto", ai_cfg=None):
    psd = PSDImage.open(psd_path)
    W, H = psd.width, psd.height
    cx = W / 2.0
    img_dir = os.path.join(out_dir, "images")

    # auto:命中 See-through 结构走智能人形;否则走 smart(ML+名字融合),失败回退通用
    auto_fallback = False
    if mode == "auto":
        if _looks_seethrough(psd):
            mode = "seethrough"
        else:
            mode, auto_fallback = "smart", True

    # ML / smart 绑骨:关节识别 -> 人形骨架 -> 图层分配
    if mode in ("ml", "smart"):
        try:
            import ml_rig
            if mode == "smart":
                skel = ml_rig.build_smart_skeleton(psd, img_dir, W, H, cx,
                                                   spine_version, ai_cfg)
            else:
                skel = ml_rig.build_ml_skeleton(psd, img_dir, W, H, cx,
                                                spine_version)
            print("spine version:", spine_version, "| mode:", mode)
            _write(skel, os.path.join(out_dir, "skeleton.json"))
            return out_dir
        except Exception as e:
            if not auto_fallback:
                raise        # 显式指定模式失败则报错
            print("[%s] 失败,回退通用模式:%s" % (mode, e))
            mode = "generic"

    # 通用模式:任意 PSD,逐层一根骨。essential=region;professional=每层网格(FFD,无权重)
    if mode == "generic":
        def to_skel(px, py):
            return (px - cx, H - py)
        bones, bone_abs, leaves, world_rot = _generic_collect(
            psd, img_dir, to_skel)
        print("spine version:", spine_version, "| images:", len(leaves),
              "| mode: generic | profile:", profile)
        want_ess = profile in ("essential", "both")
        want_pro = profile in ("professional", "both")
        if want_ess:
            ess = _generic_skeleton(bones, bone_abs, leaves, world_rot,
                                    W, H, cx, spine_version, False, to_skel)
            _write(ess, os.path.join(
                out_dir, "skeleton_essential.json" if profile == "both"
                else "skeleton.json"))
        if want_pro:
            pro = _generic_skeleton(bones, bone_abs, leaves, world_rot,
                                    W, H, cx, spine_version, True, to_skel)
            _write(pro, os.path.join(
                out_dir, "skeleton_professional.json" if profile == "both"
                else "skeleton.json"))
        return out_dir

    # See-through 智能模式
    layers, bboxes = _export_layers(psd, img_dir)
    print("spine version:", spine_version, "| images:", len(layers),
          "| mode: seethrough | profile:", profile)
    want_ess = profile in ("essential", "both")
    want_pro = profile in ("professional", "both")
    if want_ess:
        ess = _build_skeleton(layers, bboxes, W, H, cx, spine_version, False)
        _write(ess, os.path.join(
            out_dir, "skeleton_essential.json" if profile == "both"
            else "skeleton.json"))
    if want_pro:
        pro = _build_skeleton(layers, bboxes, W, H, cx, spine_version, True)
        _write(pro, os.path.join(
            out_dir, "skeleton_professional.json" if profile == "both"
            else "skeleton.json"))
    return out_dir


def find_psds(in_dir):
    """递归找出所有 See-through 分层 PSD(排除 *_depth.psd)。"""
    out = []
    for root, _dirs, files in os.walk(in_dir):
        for fn in files:
            low = fn.lower()
            if low.endswith(".psd") and not low.endswith("_depth.psd"):
                out.append(os.path.join(root, fn))
    return sorted(out)


def batch(in_dir, out_root, spine_version=DEFAULT_SPINE_VERSION,
          profile=DEFAULT_PROFILE, mode="auto", ai_cfg=None):
    """批处理:in_dir 下所有 PSD,各自输出到 out_root/<相对路径(去扩展名)>/。"""
    psds = find_psds(in_dir)
    if not psds:
        print("[batch] 未找到 PSD:", in_dir)
        return []
    print("[batch] 共 %d 个 PSD" % len(psds))
    done = []
    for i, p in enumerate(psds, 1):
        rel = os.path.splitext(os.path.relpath(p, in_dir))[0]
        out = os.path.join(out_root, rel)
        print("\n[%d/%d] %s" % (i, len(psds), p))
        try:
            main(p, out, spine_version, profile, mode, ai_cfg)
            done.append(out)
        except Exception as e:
            print("  [skip] 出错:", e)
    print("\n[batch] 完成 %d/%d" % (len(done), len(psds)))
    return done


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="See-through PSD -> Spine 工程")
    ap.add_argument("psd", help="输入 PSD;若为目录则批处理其中所有 PSD")
    ap.add_argument("out_dir", help="输出目录(批处理时为输出根目录)")
    ap.add_argument("--spine-version", default=None,
                    help="目标 Spine 版本号,如 4.3.17;不填则自动探测")
    ap.add_argument("--profile", default=DEFAULT_PROFILE,
                    choices=["essential", "professional", "both"],
                    help="输出版本(仅 See-through 模式):essential/professional/both")
    ap.add_argument("--mode", default="auto",
                    choices=["auto", "seethrough", "generic", "ml", "smart"],
                    help="auto/seethrough/generic/ml(纯姿态)/smart(姿态+图层名融合)")
    ap.add_argument("--ai-base-url", default=None, help="AI 视觉:OpenAI 兼容 base_url")
    ap.add_argument("--ai-key", default=None, help="AI 视觉:API key")
    ap.add_argument("--ai-model", default=None, help="AI 视觉:模型 id")
    args = ap.parse_args()

    ver = resolve_spine_version(args.spine_version)
    ai_cfg = None
    if args.ai_base_url and args.ai_key and args.ai_model:
        ai_cfg = {"base_url": args.ai_base_url, "api_key": args.ai_key,
                  "model": args.ai_model}
    if os.path.isdir(args.psd):
        batch(args.psd, args.out_dir, ver, args.profile, args.mode, ai_cfg)
    else:
        main(args.psd, args.out_dir, ver, args.profile, args.mode, ai_cfg)
