# psd2spine

把 **See-through**(单图动漫/角色分层 AI)导出的分层 PSD,自动转成可导入 **Spine** 的工程:
导出每层 PNG + 生成 `skeleton.json`(完整人形骨架 + slot + 按语义自动绑骨)。

两个版本:

| 版本 | 文件 | 说明 |
|---|---|---|
| 独立程序版 | `psd2spine_app.py` | pywebview 网页 UI,可打包成单 exe 独立运行 |
| 命令行 | `psd2spine.py` | 核心逻辑,可脚本/批处理调用 |
| Photoshop 插件版 | `PsdToSpine.jsx` | 在 PS 里直接对当前文档一键导出 |

## Spine 版本号
导入 Spine 时数据版本号需与编辑器版本一致,否则会有警告。三处都支持:
1. **自动探测**:扫 `<用户目录>\Spine[Trial]\updates\<版本号>`;
2. **手动指定**:CLI 用 `--spine-version 4.3.17`,GUI/插件里直接改输入框;
3. 都没有时退默认值并提示。

## 用法

### 独立版(开发运行)
```
pip install -r requirements.txt
python psd2spine_app.py
```

### 命令行
```
# 单文件,默认同时输出 Essential + Professional 两套
python psd2spine.py <输入.psd> <输出目录> [--spine-version 4.3.17] [--profile both]

# 批处理:输入为目录时,递归处理其中所有 PSD(自动排除 *_depth.psd)
python psd2spine.py <输入目录> <输出根目录> --profile both
```
`--profile` 取值:`essential`(刚性 region)/ `professional`(mesh+权重,可弯)/ `both`(默认)。

### 打包成单 exe(分发)
```
pip install -r requirements.txt pyinstaller
build_exe.bat
```
产物:`dist\psd2spine.exe`(依赖全内置,目标机无需装 Python)。

### Photoshop 插件
把 `PsdToSpine.jsx` 放进 Photoshop 的 `Presets/Scripts` 目录,重启 PS,
打开 See-through 的分层文件,运行 `文件 > 脚本 > PsdToSpine`,
按提示确认 Spine 版本、选择输出目录即可。

## 当前能力 / 待办
- [x] Essential 版:8 根人形骨架 + slot + region 附件,导入即为组装好的角色。
- [x] Professional 版:为四肢/躯干生成条带网格 + 双骨距离权重(可弯),关节在 Spine 里手调。
- [x] 批处理整个 See-through 输出目录(`--profile both` 各出两套)。
- [x] GUI(pywebview)+ PS 插件(JSX)均支持版本/profile/批量,带 logo。
- [ ] 可选:利用 `_depth.psd` 做 2.5D 视差/转头(深度为层内自归一化,仅适合伪 3D,非 2D 必需)。
