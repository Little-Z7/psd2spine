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
import argparse
from psd_tools import PSDImage

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
MESH_ROWS = 6  # 网格沿主轴的分段数(越大越平滑、可弯)
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
        layer.composite().save(os.path.join(img_dir, key + ".png"))
        l, t, r, b = layer.bbox
        layers.append({"key": key, "bbox": layer.bbox, "w": r - l, "h": b - t})
        bboxes[key] = layer.bbox
    return layers, bboxes


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


def _strip_mesh(rec, chain, bone_idx, bone_abs, to_skel, rows=MESH_ROWS):
    """为可形变层生成沿垂直方向的条带网格 + 双骨距离权重(可弯,关节后续手调)。"""
    l, t, r, b = rec["bbox"]
    w, h = rec["w"], rec["h"]
    T, B = chain[0], chain[1]
    Tx, Ty = bone_abs[T]
    Bx, By = bone_abs[B]
    iT, iB = bone_idx[T], bone_idx[B]

    # 周边顶点:左列 上->下,右列 下->上(全部在凸包上,hull=全部)
    pts = [(l, t + i / rows * h) for i in range(rows + 1)]
    pts += [(r, b - i / rows * h) for i in range(rows + 1)]

    uvs, verts = [], []
    for px, py in pts:
        uvs += [round((px - l) / w, 5), round((py - t) / h, 5)]
        sx, sy = to_skel(px, py)
        tw = 0.5 if Ty == By else max(0.0, min(1.0, (Ty - sy) / (Ty - By)))
        wB, wT = tw, 1.0 - tw
        if wT < 0.001:
            verts += [1, iB, round(sx - Bx, 2), round(sy - By, 2), 1]
        elif wB < 0.001:
            verts += [1, iT, round(sx - Tx, 2), round(sy - Ty, 2), 1]
        else:
            verts += [2, iT, round(sx - Tx, 2), round(sy - Ty, 2), round(wT, 4),
                      iB, round(sx - Bx, 2), round(sy - By, 2), round(wB, 4)]

    tris = []
    last = 2 * rows + 1
    for i in range(rows):
        a, b2 = i, i + 1
        ri, ri1 = last - i, last - (i + 1)
        tris += [a, b2, ri1, a, ri1, ri]

    return {"type": "mesh", "uvs": uvs, "triangles": tris,
            "vertices": verts, "hull": len(pts), "width": w, "height": h}


def _build_skeleton(layers, bboxes, W, H, cx, spine_version, professional):
    bone_psd, order, parent = _bone_anchors(bboxes, W, H, cx, professional)

    def to_skel(px, py):
        return (px - cx, H - py)

    bone_abs = {k: to_skel(*v) for k, v in bone_psd.items()}
    bone_idx = {name: i for i, name in enumerate(order)}

    bones = []
    for name in order:
        p = parent[name]
        ax, ay = bone_abs[name]
        e = {"name": name}
        if p:
            e["parent"] = p
            pax, pay = bone_abs[p]
            e["x"] = round(ax - pax, 2)
            e["y"] = round(ay - pay, 2)
        bones.append(e)

    slots, attachments = [], {}
    for lay in layers:
        key = lay["key"]
        chain = DEFORM_CHAINS.get(key) if professional else None
        if chain and all(b in bone_idx for b in chain):
            bone = chain[0]
            attachments[key] = {key: _strip_mesh(lay, chain, bone_idx,
                                                  bone_abs, to_skel)}
        else:
            bone = LAYER_TO_BONE.get(key, "root")
            ccx, ccy = center(lay["bbox"])
            sx, sy = to_skel(ccx, ccy)
            bax, bay = bone_abs[bone]
            attachments[key] = {key: {
                "x": round(sx - bax, 2), "y": round(sy - bay, 2),
                "width": lay["w"], "height": lay["h"]}}
        slots.append({"name": key, "bone": bone, "attachment": key})

    return {
        "skeleton": {"spine": spine_version, "images": "./images/",
                     "width": W, "height": H},
        "bones": bones,
        "slots": slots,
        "skins": [{"name": "default", "attachments": attachments}],
        "animations": {"setup": {}},
    }


def _write(skeleton, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(skeleton, f, ensure_ascii=False, indent=2)
    print("OK ->", path, "| bones:", len(skeleton["bones"]),
          "slots:", len(skeleton["slots"]))


def main(psd_path, out_dir, spine_version=DEFAULT_SPINE_VERSION,
         profile=DEFAULT_PROFILE):
    psd = PSDImage.open(psd_path)
    W, H = psd.width, psd.height
    cx = W / 2.0
    layers, bboxes = _export_layers(psd, os.path.join(out_dir, "images"))
    print("spine version:", spine_version, "| images:", len(layers),
          "| profile:", profile)

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
          profile=DEFAULT_PROFILE):
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
            main(p, out, spine_version, profile)
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
                    help="输出版本:essential / professional / both(默认)")
    args = ap.parse_args()

    ver = resolve_spine_version(args.spine_version)
    if os.path.isdir(args.psd):
        batch(args.psd, args.out_dir, ver, args.profile)
    else:
        main(args.psd, args.out_dir, ver, args.profile)
