# -*- coding: utf-8 -*-
"""AI 视觉增强:调用 OpenAI 兼容的视觉模型,给图层分类身体部位(可选关节先验)。

用户提供 base_url + api_key + model。为保证输出可解析,严格约束:
- response_format=json_object、temperature=0、枚举受限;
- 容错解析(去 markdown / 抽 JSON / 逐项校验),坏数据丢弃;
- 任何失败都不抛到主流程(AI 只是增强,失败则当未参与)。
"""
import io
import json
import base64
import urllib.request
from PIL import Image, ImageDraw

# 受限的部位枚举(不带左右,左右由几何 x 判定,避免 L/R 歧义)
PARTS = ["head", "torso", "upper_arm", "lower_arm", "hand",
         "thigh", "shin", "foot", "prop", "none"]
# 可选关节(归一化全图坐标);左右最终按 x 重新归类
JOINT_KEYS = ["nose", "shoulder", "elbow", "wrist", "hip", "knee", "ankle"]

PROMPT = """你是 2D 角色绑骨助手。下图是一个角色,上面用红框标了各图层并编了号。
另给出图层清单(编号、名字、bbox=[left,top,right,bottom])。

请判断每个编号图层属于身体哪个部位,只能从这个枚举里选:
%s
(upper_arm=大臂, lower_arm=小臂, hand=手, thigh=大腿, shin=小腿, foot=脚/鞋,
 prop=道具非身体, none=判断不了)。不要区分左右,左右由程序按位置定。

只输出一个 JSON 对象,不要任何解释、不要 markdown 代码块。格式:
{
  "layers": [{"index": 0, "part": "head"}, {"index": 3, "part": "upper_arm"}],
  "joints": {}   // 可选;若能判断,给归一化(0~1,相对整图)坐标,如 "knee_left":[0.45,0.8]
}
part 必须是枚举之一。index 必须是清单里的编号。""" % ", ".join(PARTS)


def _annotate(image, layers):
    im = image.convert("RGB").copy()
    d = ImageDraw.Draw(im)
    for i, lf in enumerate(layers):
        l, t, r, b = lf["bbox"]
        d.rectangle([l, t, r, b], outline=(255, 30, 30), width=3)
        tag = str(i)
        d.rectangle([l, t, l + 9 * len(tag) + 6, t + 18], fill=(255, 30, 30))
        d.text((l + 3, t + 3), tag, fill=(255, 255, 255))
    return im


def _data_url(im, max_side=1024):
    im = im.copy()
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _extract_json(text):
    t = text.strip()
    if "```" in t:                       # 去掉 markdown 围栏
        t = t.split("```")[1] if t.count("```") >= 2 else t
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        t = t[a:b + 1]
    return json.loads(t)


def _post(cfg, body, timeout):
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg["api_key"]})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def classify(image, layers, cfg, timeout=90):
    """返回 (parts_by_index: {int:part}, joints: {key:(u,v)});失败返回 ({},{})。"""
    listing = "\n".join("%d: name=%r bbox=%s" % (i, lf["name"], lf["bbox"])
                        for i, lf in enumerate(layers))
    content = [
        {"type": "text", "text": PROMPT + "\n\n图层清单:\n" + listing},
        {"type": "image_url",
         "image_url": {"url": _data_url(_annotate(image, layers))}}]
    base = {"model": cfg["model"], "temperature": 0,
            "messages": [{"role": "user", "content": content}]}
    try:
        try:                              # 优先强制 JSON 输出
            data = _post(cfg, dict(base, response_format={"type": "json_object"}),
                         timeout)
        except Exception:                 # 有些兼容服务不认 response_format
            data = _post(cfg, base, timeout)
        text = data["choices"][0]["message"]["content"]
        obj = _extract_json(text)
    except Exception as e:
        print("[ai] 视觉增强失败(忽略):", e)
        return {}, {}

    parts = {}
    for item in obj.get("layers", []) if isinstance(obj, dict) else []:
        try:
            idx = int(item["index"])
            part = str(item["part"]).lower().strip()
            if 0 <= idx < len(layers) and part in PARTS:
                parts[idx] = part
        except Exception:
            continue
    joints = {}
    for k, v in (obj.get("joints", {}) or {}).items():
        try:
            u, w = float(v[0]), float(v[1])
            if 0 <= u <= 1 and 0 <= w <= 1:
                joints[str(k).lower()] = (u, w)
        except Exception:
            continue
    print("[ai] 视觉增强:分类 %d 层,关节先验 %d 个" % (len(parts), len(joints)))
    return parts, joints
