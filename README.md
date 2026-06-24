# psd2spine

把分层 PSD 自动转成可导入 **Spine**(Esoteric Software 2D 骨骼动画)的工程:
逐层导出 PNG + 生成带**骨架**的 `skeleton.json`,导入即为组装好、可动的角色。

支持 **See-through**(单图角色分层 AI)的输出,也支持**任意分层 PSD**,并能用
**姿态 AI + 图层语义融合**对普通角色图自动绑出人形骨架。

| 形态 | 文件 | 说明 |
|---|---|---|
| 独立程序版 | `psd2spine_app.py` | pywebview 网页 UI,可打包成单 exe 独立运行(带 logo) |
| 命令行 | `psd2spine.py` | 核心逻辑,可脚本 / 批处理调用 |
| Photoshop 插件 | `PsdToSpine.jsx` | 在 PS 里对当前打开文档一键导出(仅 See-through 逻辑) |

---

## 识别模式 `--mode`

| 模式 | 做法 | 适合 |
|---|---|---|
| `auto`(默认) | 命中 See-through 结构走 `seethrough`,否则走 `smart` | 一律推荐 |
| `seethrough` | 语义人形骨架(face/handwear/topwear… 命名驱动) | See-through 导出 |
| `generic` | 不猜语义,每个图层一根骨(组→骨层级、重名去重) | 任意 PSD 的保底 |
| `ml` | 纯 MediaPipe 姿态识别 → 人形骨架 | 正常比例角色 |
| `smart` | **姿态 + 图层名/bbox + 可选 AI 视觉 融合** | 任意角色,效果最佳 |

`--profile`(seethrough/generic 用):`essential`(刚性 region)/ `professional`
(mesh + 权重,可弯)/ `both`(默认,各出一份)。

---

## 技术实现

### 坐标系
PSD 像素(y 向下)→ Spine 骨架坐标(y 向上、x 以画布中心为 0):
`skel = (px - W/2, H - py)`。`root` 落在画布底部中心(skel 原点 0,0)。

### 骨骼朝向与附件补偿
每根骨带 `length` + `rotation`(指向子骨/肢体末端),显示成连贯火柴人骨架。
骨头一旦带旋转,其子骨局部坐标、以及挂在它上面的图片/网格都按累积旋转做**反向
补偿**(region 附件加 `rotation = -骨朝向`、网格顶点按 `-骨朝向` 旋转),保证 setup
pose 像素级不变形。

### See-through 智能人形(`seethrough`)
图层语义名 → 固定人形骨架模板(root→hip→torso→neck→head、torso→arm、hip→foot),
锚点由匹配图层的真实 bbox 推算。Professional 版给四肢/躯干生成**条带网格**,左右列
按每行真实 alpha 剪影走(贴合轮廓)+ 双骨**距离权重**;3.8 兼容补 `edges`、去空动画。

### 通用模式(`generic`)
递归遍历图层树:每个叶子层一根骨(带可见长度/朝向)、组→骨层级、重名加后缀去重、
绘制顺序按图层顺序。Professional 版把每层做成 `n×n` 网格(无权重)供自由网格形变 FFD。

### 姿态识别(`ml`)
拍平 PSD → **MediaPipe Pose Landmarker**(BlazePose 33 点,heavy float16 模型)识别
关节 → 建 Spine 人形骨架 → 图层按"质心到骨段最近"分配。正常比例角色效果好;
Q 版/夸张比例会识别不全(自动跳过缺失骨头)。

### 融合(`smart`)—— 核心
多源综合,关节按可信度取最优:

```
优先级:图层名/bbox(最准) > ML 姿态(补缺) > AI 视觉(先验)
```

- **图层名 → 部位**:多语言关键词词典(head/torso/arm/leg…),从匹配层 bbox 推关节;
- **左右**:不信任图层名的 `-l/-r`(真实文件常错标),一律按几何 x 判定(与 BlazePose
  一致:L = 图右 = 较大 x);
- **手臂**:不按 bbox 上下边(那假设手臂竖直),而是对手臂层的不透明像素求 **PCA 主轴**,
  沿手臂真实方向定肩/腕,肘按上/下臂面积比例放(任意姿势都对);
- **腿**:单层含双腿时按 x 切两半;
- ML 仅补图层缺失处;无语义命名时自动回退 ML。

### AI 视觉增强(可选)
`smart` 模式可接 **OpenAI 兼容**视觉模型(填 base_url + api_key + model)。把"标了红框
编号的整图 + 图层清单"发给模型,让它**分类每层部位**(枚举受限、不分左右)。为保证可
解析:强制 `response_format=json_object` + `temperature=0` + 容错解析(去 markdown / 抽
JSON / 逐项校验,坏数据丢弃)。**任何失败都被忽略,自动回退本地融合**。AI 结果只补
"名字认不出"的图层,不喧宾夺主。

---

## Spine 版本号
导入时数据版本号需与编辑器一致,否则有警告。三处都支持:
1. **自动探测**:扫 `<用户目录>\Spine[Trial]\updates\<版本号>`;
2. **手动指定**:CLI `--spine-version 4.3.17`,GUI/插件输入框;
3. 都没有时退默认值。

输出格式跨 **Spine 3.8 ~ 4.3** 通用(skins 两版都是数组;mesh 带 `edges`、无空动画)。

---

## 用法

### 独立版(开发运行)
```
pip install -r requirements.txt
python psd2spine_app.py
```

### 命令行
```
# 单文件
python psd2spine.py <输入.psd> <输出目录> [--spine-version 4.3.17] [--profile both] [--mode auto]

# 批处理:输入为目录,递归处理其中所有 PSD(排除 *_depth.psd)
python psd2spine.py <输入目录> <输出根目录> --mode smart

# 接 AI 视觉增强(smart 生效)
python psd2spine.py x.psd out --mode smart \
  --ai-base-url https://api.openai.com/v1 --ai-key sk-xxx --ai-model gpt-4o
```

### 打包成单 exe(分发)
```
pip install -r requirements.txt pyinstaller
build_exe.bat        # 产物 dist\psd2spine.exe,依赖全内置,目标机无需 Python
```

### Photoshop 插件
把 `PsdToSpine.jsx` 放进 Photoshop 的 `Presets/Scripts` 目录,重启 PS,打开分层文件,
运行 `文件 > 脚本 > PsdToSpine`,按提示确认 Spine 版本、选输出目录。

---

## ML 模型(`ml` / `smart` 需要)
姿态模型 `pose_landmarker.task`(约 30MB,未入库)。源码运行前下载到项目根目录:
```
https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task
```
另存为 `pose_landmarker.task`。打包的 exe 已内置,最终用户无需下载。

---

## 文件总览
- `psd2spine.py` — 核心 + CLI(模式调度、See-through/通用骨架、坐标/朝向)
- `ml_rig.py` — 姿态识别 + 名字词典融合(`smart`/`ml`)
- `ai_vision.py` — OpenAI 兼容视觉模型分类(可选)
- `psd2spine_app.py` — pywebview GUI
- `PsdToSpine.jsx` — Photoshop 插件
- `make_logo.py` / `logo.png` / `logo.ico` — 图标
- `build_exe.bat` / `requirements.txt`

## 待办 / 已知限制
- [ ] AI 视觉"关节校正"Pass(让模型在本地骨架基础上挑错微调);
- [ ] Q 版"大头小身"比例仍不完美(头骨偏短,需手调);
- [ ] 通用网格用完整轮廓+三角剖分贴合任意形状;
- [ ] JSX 插件加 generic/smart 模式;
- [ ] 可选:用 `_depth.psd` 做 2.5D 视差。
