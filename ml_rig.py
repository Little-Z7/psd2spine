# -*- coding: utf-8 -*-
"""ML 自动绑骨:用 MediaPipe Pose 在拍平的角色图上识别关节,
建 Spine 人形骨架,并把每个图层按"质心最近骨头"分配过去。

依赖:mediapipe(tasks)+ pose_landmarker.task 模型。
对正常比例角色效果好;Q 版/夸张比例可能识别不全,会自动跳过缺失骨头。
"""
import os
import re
import sys
import math
import numpy as np
from PIL import Image

from psd2spine import _rot, center, slug

# BlazePose 33 点里我们用到的关节
BLAZE = {0: "nose", 11: "L_sh", 12: "R_sh", 13: "L_el", 14: "R_el",
         15: "L_wr", 16: "R_wr", 23: "L_hip", 24: "R_hip",
         25: "L_kn", 26: "R_kn", 27: "L_an", 28: "R_an"}
VIS_T = 0.3   # 关节可见度阈值


def _model_path():
    cands = []
    if hasattr(sys, "_MEIPASS"):                       # PyInstaller 打包内
        cands.append(os.path.join(sys._MEIPASS, "pose_landmarker.task"))
    cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "pose_landmarker.task"))
    cands.append("pose_landmarker.task")
    for c in cands:
        if os.path.isfile(c):
            return c
    raise FileNotFoundError("找不到 pose_landmarker.task 模型文件")


def detect_joints(pil_rgb):
    """返回 {关节名: (px, py, vis)};检测不到返回 None。"""
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    with open(_model_path(), "rb") as f:    # 用字节流避免中文路径在原生层失败
        model_buf = f.read()
    opts = vision.PoseLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_buffer=model_buf),
        min_pose_detection_confidence=0.1, min_pose_presence_confidence=0.1)
    W, Hh = pil_rgb.size
    with vision.PoseLandmarker.create_from_options(opts) as lmk:
        mpimg = mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=np.asarray(pil_rgb))
        res = lmk.detect(mpimg)
    if not res.pose_landmarks:
        return None
    lms = res.pose_landmarks[0]
    return {nm: (lms[i].x * W, lms[i].y * Hh, lms[i].visibility)
            for i, nm in BLAZE.items()}


def _pt_seg_dist(p, a, b):
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-9:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _collect_leaves(psd, img_dir, cx, H):
    """导出每个叶子层 PNG,返回 [{key,name,bbox,c(skel质心)}]。"""
    os.makedirs(img_dir, exist_ok=True)
    used, leaves = set(), []

    def uniq(n):
        base = slug(n) or "layer"
        k, i = base, 2
        while k in used:
            k = "%s_%d" % (base, i)
            i += 1
        used.add(k)
        return k

    def walk(container):
        for layer in container:
            bb = layer.bbox
            if layer.is_group():
                walk(layer)
                continue
            if bb == (0, 0, 0, 0):
                continue
            key = uniq(layer.name)
            png = layer.composite()
            png.save(os.path.join(img_dir, key + ".png"))
            ccx, ccy = center(bb)
            alpha = np.asarray(png.convert("RGBA"))[..., 3] > 10
            leaves.append({"key": key, "name": layer.name, "bbox": bb,
                           "c": (ccx - cx, H - ccy), "alpha": alpha})

    walk(psd)
    return leaves


def _ml_jdict(psd, cx, H):
    """ML 姿态 -> {关节: (skelx,skely) 或 None(可见度不足)};检测不到返回 {}。"""
    comp = psd.composite().convert("RGBA")
    bg = Image.new("RGBA", comp.size, (255, 255, 255, 255))
    bg.alpha_composite(comp)
    joints = detect_joints(bg.convert("RGB"))
    if not joints:
        return {}
    return {n: ((x - cx, H - y) if v >= VIS_T else None)
            for n, (x, y, v) in joints.items()}


def _build_from_joints(jdict, leaves, part_of, W, H, cx, spine_version, tag):
    """通用内核:给定关节坐标 + 图层 + 可选"图层->骨"分配,产出 Spine 骨架。"""
    def has(n):
        return jdict.get(n) is not None

    def P(n):
        return jdict[n]

    def midj(a, b):
        if has(a) and has(b):
            pa, pb = P(a), P(b)
            return ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
        return P(a) if has(a) else (P(b) if has(b) else None)

    pelvis, chest = midj("L_hip", "R_hip"), midj("L_sh", "R_sh")
    if pelvis is None or chest is None:
        raise RuntimeError("躯干关节不足,无法绑骨;请改用 --mode generic")
    nose = P("nose") if has("nose") else (chest[0], chest[1] + 80)

    specs = [("hip", "root", pelvis, chest), ("head", "hip", chest, nose)]
    for s in ("L", "R"):
        sh = P(s + "_sh") if has(s + "_sh") else None
        el = P(s + "_el") if has(s + "_el") else None
        wr = P(s + "_wr") if has(s + "_wr") else None
        if sh and el:
            specs.append(("upperarm_" + s, "hip", sh, el))
            if wr:
                specs.append(("lowerarm_" + s, "upperarm_" + s, el, wr))
        hp = P(s + "_hip") if has(s + "_hip") else None
        kn = P(s + "_kn") if has(s + "_kn") else None
        an = P(s + "_an") if has(s + "_an") else None
        if hp and kn:
            specs.append(("thigh_" + s, "hip", hp, kn))
            if an:
                specs.append(("shin_" + s, "thigh_" + s, kn, an))

    bone_world = {"root": (0.0, 0.0)}
    world_rot = {"root": 0.0}
    length = {"root": 0.0}
    created = {"root"}
    for name, parent, start, aim in specs:
        bone_world[name] = start
        dx, dy = aim[0] - start[0], aim[1] - start[1]
        dist = math.hypot(dx, dy)
        world_rot[name] = math.degrees(math.atan2(dy, dx)) if dist > 1e-3 \
            else world_rot.get(parent, 0.0)
        length[name] = round(dist, 2) if dist > 1e-3 else 40.0
        created.add(name)

    bones = [{"name": "root"}]
    for name, parent, start, aim in specs:
        p = parent if parent in created else "hip"
        pax, pay = bone_world[p]
        ax, ay = bone_world[name]
        lx, ly = _rot(ax - pax, ay - pay, -world_rot[p])
        e = {"name": name, "parent": p, "x": round(lx, 2), "y": round(ly, 2),
             "length": length[name]}
        lr = world_rot[name] - world_rot[p]
        if abs(lr) > 1e-4:
            e["rotation"] = round(lr, 2)
        bones.append(e)

    seg = []
    for name in bone_world:
        if name == "root":
            continue
        x1, y1 = bone_world[name]
        r = math.radians(world_rot[name])
        L = length[name]
        seg.append((name, (x1, y1),
                    (x1 + L * math.cos(r), y1 + L * math.sin(r))))
    if not seg:
        seg = [("hip", bone_world["hip"], bone_world["hip"])]

    slots, atts = [], {}
    for lf in leaves:
        key, c = lf["key"], lf["c"]
        bone = None
        if part_of and part_of.get(key) in created:   # 名字分类优先
            bone = part_of[key]
        if bone is None:                               # 否则就近分配
            bone = min(seg, key=lambda s: _pt_seg_dist(c, s[1], s[2]))[0]
        bx, by = bone_world[bone]
        wr = world_rot[bone]
        lx, ly = _rot(c[0] - bx, c[1] - by, -wr)
        l, t, r, b = lf["bbox"]
        att = {"x": round(lx, 2), "y": round(ly, 2),
               "width": r - l, "height": b - t}
        if abs(wr) > 1e-4:
            att["rotation"] = round(-wr, 2)
        slots.append({"name": key, "bone": bone, "attachment": key})
        atts[key] = {key: att}

    print("%s:骨头 %d 根,图层 %d 个" % (tag, len(bones), len(leaves)))
    return {
        "skeleton": {"spine": spine_version, "images": "./images/",
                     "width": W, "height": H},
        "bones": bones, "slots": slots,
        "skins": [{"name": "default", "attachments": atts}],
    }


def build_ml_skeleton(psd, img_dir, W, H, cx, spine_version):
    leaves = _collect_leaves(psd, img_dir, cx, H)
    jd = _ml_jdict(psd, cx, H)
    if not jd:
        raise RuntimeError("未检测到姿态,无法 ML 自动绑骨;请改用 --mode generic")
    return _build_from_joints(jd, leaves, None, W, H, cx,
                              spine_version, "ML 绑骨")


# ---------------- 名字词典(融合用)----------------
HEAD_KW = ["hair", "face", "head", "ear", "eye", "brow", "lash", "iris",
           "irid", "pupil", "mouth", "nose", "horn", "jiao", "bang", "fringe",
           "角", "头", "发", "脸", "眼", "眉", "嘴", "耳", "刘海"]
TORSO_KW = ["body", "torso", "chest", "spine", "waist", "dress", "skirt",
            "cloth", "shirt", "身", "躯", "胸", "腰", "衣", "裙"]
ARM_KW = ["arm", "hand", "sleeve", "手", "臂", "胳", "袖", "腕"]
LEG_KW = ["leg", "thigh", "shin", "calf", "knee", "foot", "feet", "shoe",
          "boot", "脚", "腿", "膝", "鞋", "靴"]


def _classify(name):
    """图层名 -> (部位, 段);部位 ∈ head/torso/arm/leg/None。"""
    s = name.lower()

    def hit(kws):
        return any(k in s for k in kws)

    if hit(LEG_KW):
        if "thigh" in s or "大腿" in s:
            seg = "upper"
        elif any(k in s for k in ["shin", "calf", "小腿", "knee", "膝"]):
            seg = "lower"
        elif any(k in s for k in ["foot", "feet", "脚", "shoe", "boot",
                                  "鞋", "靴"]):
            seg = "foot"
        else:
            seg = "whole"
        return ("leg", seg)
    if hit(ARM_KW):
        if "arm1" in s or "upper" in s:
            seg = "upper"
        elif "arm2" in s or "lower" in s or "fore" in s:
            seg = "lower"
        elif "hand" in s or "腕" in s:
            seg = "hand"
        else:
            seg = "whole"
        return ("arm", seg)
    if hit(HEAD_KW):
        return ("head", None)
    if hit(TORSO_KW):
        return ("torso", None)
    return (None, None)


# AI 粗部位 -> (部位, 段)
AI2PS = {"head": ("head", None), "torso": ("torso", None),
         "upper_arm": ("arm", "upper"), "lower_arm": ("arm", "lower"),
         "hand": ("arm", "hand"), "thigh": ("leg", "upper"),
         "shin": ("leg", "lower"), "foot": ("leg", "foot")}


def _limb_pts(leaf_list):
    """把若干图层的不透明像素汇成画布坐标点集(用于求肢体主轴)。"""
    allp = []
    for lf in leaf_list:
        m = lf.get("alpha")
        if m is None:
            continue
        ys, xs = np.nonzero(m)
        if xs.size == 0:
            continue
        l, t = lf["bbox"][0], lf["bbox"][1]
        allp.append(np.stack([xs + l, ys + t], axis=1))
    if not allp:
        return None
    return np.concatenate(allp, axis=0).astype(float)


def _pca_ends(pts):
    """点集沿主轴的两个极端点(画布像素 (x,y))。"""
    if pts is None or len(pts) < 2:
        return None
    c = pts - pts.mean(axis=0)
    _w, v = np.linalg.eigh(c.T @ c)
    proj = c @ v[:, -1]
    return tuple(pts[proj.argmin()]), tuple(pts[proj.argmax()])


def _name_data(leaves, cx, H, ai_parts=None):
    """从图层名/bbox(+可选 AI 分类)推出关节坐标(skel)与 图层->骨 分配。"""
    def tsk(px, py):
        return (px - cx, H - py)

    def dist(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def union(boxes):
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def topc(bb):
        return tsk((bb[0] + bb[2]) / 2.0, bb[1])

    def botc(bb):
        return tsk((bb[0] + bb[2]) / 2.0, bb[3])

    torso_layers = [lf for lf in leaves if _classify(lf["name"])[0] == "torso"]
    torso = None
    if torso_layers:
        torso = max(torso_layers, key=lambda lf: (lf["bbox"][2] - lf["bbox"][0])
                    * (lf["bbox"][3] - lf["bbox"][1]))["bbox"]
        bcx = (torso[0] + torso[2]) / 2.0
    else:
        bcx = cx

    arms = {"L": {}, "R": {}}
    legs = {"L": {}, "R": {}, "_whole": []}
    head_cs, part_of = [], {}
    for i, lf in enumerate(leaves):
        part, seg = _classify(lf["name"])
        if part is None and ai_parts and i in ai_parts:   # 名字认不出 -> 用 AI
            part, seg = AI2PS.get(ai_parts[i], (None, None))
        l, t, r, b = lf["bbox"]
        mx = (l + r) / 2.0
        side = "L" if mx >= bcx else "R"   # 与 BlazePose 一致:L=图右=较大x
        if part == "head":
            head_cs.append(lf["c"])
            part_of[lf["key"]] = "head"
        elif part == "torso":
            part_of[lf["key"]] = "hip"
        elif part == "arm":
            arms[side].setdefault(seg, []).append(lf)
            part_of[lf["key"]] = ("lowerarm_" if seg in ("lower", "hand")
                                  else "upperarm_") + side
        elif part == "leg":
            if seg == "whole":
                legs["_whole"].append(lf["bbox"])
                part_of[lf["key"]] = "hip"
            else:
                legs[side].setdefault(seg, []).append(lf["bbox"])
                part_of[lf["key"]] = ("shin_" if seg in ("lower", "foot")
                                      else "thigh_") + side

    nj = {}
    if torso is not None:
        l, t, r, b = torso
        nj["L_sh"], nj["R_sh"] = tsk(r, t), tsk(l, t)
        nj["L_hip"], nj["R_hip"] = tsk(r, b), tsk(l, b)
    chest_px = (((torso[0] + torso[2]) / 2.0, torso[1]) if torso is not None
                else (bcx, min((lf["bbox"][1] for s in ("L", "R")
                                for seg in arms[s].values() for lf in seg),
                               default=0)))
    for side in ("L", "R"):
        up = arms[side].get("upper", []) + arms[side].get("whole", [])
        lo = arms[side].get("lower", []) + arms[side].get("hand", [])
        arm_leaves = up + lo
        if not arm_leaves:
            continue
        ends = _pca_ends(_limb_pts(arm_leaves))
        if ends is None:
            continue
        a, b = ends                                  # 主轴两端(画布像素)
        sh, wr = (a, b) if dist(a, chest_px) <= dist(b, chest_px) else (b, a)
        nu = sum(int(lf["alpha"].sum()) for lf in up)
        nl = sum(int(lf["alpha"].sum()) for lf in lo)
        f = nu / (nu + nl) if (nu + nl) > 0 else 0.5  # 肘按上/下臂面积比
        el = (sh[0] + (wr[0] - sh[0]) * f, sh[1] + (wr[1] - sh[1]) * f)
        nj[side + "_sh"] = tsk(*sh)
        nj[side + "_el"] = tsk(*el)
        nj[side + "_wr"] = tsk(*wr)
    for side in ("L", "R"):
        th = legs[side].get("upper") or legs[side].get("lower") \
            or legs[side].get("foot")
        if th:
            bb = union(list(legs[side].get("upper", []))
                       + list(legs[side].get("lower", []))
                       + list(legs[side].get("foot", [])))
            nj[side + "_hip"] = tsk((bb[0] + bb[2]) / 2.0, bb[1])
            nj[side + "_kn"] = tsk((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)
            nj[side + "_an"] = tsk((bb[0] + bb[2]) / 2.0, bb[3])
    for bb in legs["_whole"]:        # 单层含双腿:按 x 左右切
        l, t, r, b = bb
        mxx = (l + r) / 2.0
        for side, xa, xb in (("R", l, mxx), ("L", mxx, r)):
            ccx = (xa + xb) / 2.0
            nj[side + "_hip"] = tsk(ccx, t)
            nj[side + "_kn"] = tsk(ccx, (t + b) / 2.0)
            nj[side + "_an"] = tsk(ccx, b)
    if head_cs:
        nj["nose"] = (sum(c[0] for c in head_cs) / len(head_cs),
                      sum(c[1] for c in head_cs) / len(head_cs))
    return nj, part_of


def _fuse(*sources):
    """多源关节融合,按参数顺序优先(ML > 名字 > AI):取第一个非空值。"""
    out, keys = {}, set()
    for s in sources:
        keys |= set(s or {})
    for k in keys:
        for s in sources:
            if s and s.get(k) is not None:
                out[k] = s[k]
                break
    return out


def _ai_joints_to_skel(ai_joints, W, H, cx):
    """AI 归一化关节 -> skel;左右按 x 重新归类(大x=L,与BlazePose一致)。"""
    word = {"shoulder": "_sh", "elbow": "_el", "wrist": "_wr",
            "hip": "_hip", "knee": "_kn", "ankle": "_an"}
    out, groups = {}, {}
    for k, (u, v) in ai_joints.items():
        sx, sy = u * W - cx, H - v * H
        if "nose" in k or "head" in k:
            out["nose"] = (sx, sy)
            continue
        for w, suf in word.items():
            if w in k:
                groups.setdefault(suf, []).append((sx, sy))
                break
    for suf, pts in groups.items():
        if len(pts) == 1:
            p = pts[0]
            out[("L" if p[0] >= 0 else "R") + suf] = p
        else:
            pts.sort(key=lambda p: p[0])
            out["R" + suf], out["L" + suf] = pts[0], pts[-1]
    return out


def build_smart_skeleton(psd, img_dir, W, H, cx, spine_version, ai_cfg=None):
    """融合 ML 姿态 + 图层名/bbox(+可选 AI 视觉)综合判断骨架。"""
    leaves = _collect_leaves(psd, img_dir, cx, H)
    ml = _ml_jdict(psd, cx, H)            # 可能为 {}

    ai_parts, ai_joints = {}, {}
    if ai_cfg and ai_cfg.get("api_key") and ai_cfg.get("base_url") \
            and ai_cfg.get("model"):
        import ai_vision
        comp = psd.composite().convert("RGBA")
        bg = Image.new("RGBA", comp.size, (255, 255, 255, 255))
        bg.alpha_composite(comp)
        ai_parts, ai_joints = ai_vision.classify(bg.convert("RGB"),
                                                 leaves, ai_cfg)

    name_j, part_of = _name_data(leaves, cx, H, ai_parts)
    aj = _ai_joints_to_skel(ai_joints, W, H, cx)
    # 优先级:图层名/bbox(最准) > ML(补缺) > AI 先验
    fused = _fuse(name_j, ml, aj)
    return _build_from_joints(fused, leaves, part_of, W, H, cx,
                              spine_version, "智能融合绑骨")
