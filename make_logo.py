# -*- coding: utf-8 -*-
"""生成 psd2spine 的 logo:堆叠分层(PSD) -> 骨骼关节(Spine)。
输出 logo.png(1024)、logo_256.png、logo.ico(多尺寸)。"""
import math
from PIL import Image, ImageDraw

S = 1024
SS = 4               # 超采样
W = S * SS
BG = (31, 36, 48)    # #1f2430
PANEL = (39, 45, 58)
ORANGE = (255, 122, 89)   # #ff7a59
ORANGE_D = (224, 96, 66)
LAYER_COLS = [(92, 200, 196), (86, 150, 230), (140, 120, 230)]  # 青->蓝->紫


def rounded(draw, box, r, fill):
    draw.rounded_rectangle(box, radius=r, fill=fill)


def para(draw, cx, cy, w, h, skew, fill, outline=None, ow=0):
    """以 (cx,cy) 为中心画一个等距斜视平行四边形(代表一张图层)。"""
    hw, hh = w / 2, h / 2
    pts = [(cx - hw + skew, cy - hh), (cx + hw + skew, cy - hh),
           (cx + hw - skew, cy + hh), (cx - hw - skew, cy + hh)]
    draw.polygon(pts, fill=fill, outline=outline, width=ow)


def img():
    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    # 背景圆角方
    m = 40 * SS
    rounded(d, [m, m, W - m, W - m], 180 * SS, BG)
    # 内描边
    d.rounded_rectangle([m, m, W - m, W - m], radius=180 * SS,
                        outline=PANEL, width=10 * SS)

    cx = W * 0.40
    # 三张堆叠分层(从下到上,带透明叠加感)
    base_y = W * 0.64
    step = W * 0.115
    lw, lh, skew = W * 0.40, W * 0.20, W * 0.085
    for i in range(3):
        cy = base_y - i * step
        col = LAYER_COLS[i]
        para(d, cx, cy, lw, lh, skew, col + (235,),
             outline=(255, 255, 255, 60), ow=3 * SS)

    # Spine 骨骼:从下方关节指向右上的锥形 + 两端关节圆
    j0 = (W * 0.50, W * 0.66)   # 基部关节
    j1 = (W * 0.74, W * 0.34)   # 顶端关节
    # 骨身(锥形 kite):基部宽,顶端收窄
    ang = math.atan2(j1[1] - j0[1], j1[0] - j0[0])
    nx, ny = -math.sin(ang), math.cos(ang)
    bw = W * 0.052   # 基部半宽
    tw = W * 0.012   # 顶端半宽
    body = [(j0[0] + nx * bw, j0[1] + ny * bw),
            (j1[0] + nx * tw, j1[1] + ny * tw),
            (j1[0] - nx * tw, j1[1] - ny * tw),
            (j0[0] - nx * bw, j0[1] - ny * bw)]
    d.polygon(body, fill=ORANGE)
    # 关节圆
    r0, r1 = W * 0.060, W * 0.034
    d.ellipse([j0[0] - r0, j0[1] - r0, j0[0] + r0, j0[1] + r0], fill=ORANGE_D)
    d.ellipse([j0[0] - r0 * 0.5, j0[1] - r0 * 0.5,
               j0[0] + r0 * 0.5, j0[1] + r0 * 0.5], fill=BG)
    d.ellipse([j1[0] - r1, j1[1] - r1, j1[0] + r1, j1[1] + r1], fill=ORANGE)
    d.ellipse([j1[0] - r1 * 0.45, j1[1] - r1 * 0.45,
               j1[0] + r1 * 0.45, j1[1] + r1 * 0.45], fill=BG)

    return im.resize((S, S), Image.LANCZOS)


def main():
    im = img()
    im.save("logo.png")
    im.resize((256, 256), Image.LANCZOS).save("logo_256.png")
    im.save("logo.ico", sizes=[(16, 16), (32, 32), (48, 48),
                               (64, 64), (128, 128), (256, 256)])
    print("OK -> logo.png / logo_256.png / logo.ico")


if __name__ == "__main__":
    main()
