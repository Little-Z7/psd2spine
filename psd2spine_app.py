# -*- coding: utf-8 -*-
"""
psd2spine 独立程序版(pywebview 网页 UI + Python 核心)。

依赖:pywebview(Windows 上走系统自带 WebView2)。
打包成单 exe(依赖全内置、独立运行):
    pip install pyinstaller pywebview
    pyinstaller --onefile --windowed --name psd2spine ^
        --collect-all psd_tools --collect-all webview psd2spine_app.py
运行:psd2spine.exe
"""
import io
import os
import traceback
from contextlib import redirect_stdout

import webview  # pywebview
import psd2spine  # 复用核心逻辑


HTML = r"""
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<style>
  :root { --bg:#1f2430; --panel:#272d3a; --fg:#e6e9ef; --muted:#9aa3b2;
          --accent:#ff7a59; --accent2:#3a4252; --ok:#5fd08a; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:"Segoe UI","Microsoft YaHei",sans-serif;
         background:var(--bg); color:var(--fg); font-size:14px; }
  .wrap { padding:18px 20px; }
  h1 { font-size:16px; margin:0 0 14px; display:flex; align-items:center; gap:8px;}
  h1 .dot{ width:10px;height:10px;border-radius:50%;background:var(--accent);}
  .row { margin-bottom:12px; }
  label { display:block; color:var(--muted); margin-bottom:5px; font-size:12px;}
  .field { display:flex; gap:8px; }
  input[type=text]{ flex:1; background:var(--panel); border:1px solid #353c4a;
    color:var(--fg); padding:8px 10px; border-radius:6px; outline:none; }
  input[type=text]:focus{ border-color:var(--accent); }
  button { background:var(--accent2); color:var(--fg); border:1px solid #444c5e;
    padding:8px 14px; border-radius:6px; cursor:pointer; white-space:nowrap; }
  button:hover{ background:#454e62; }
  button.primary{ background:var(--accent); border-color:var(--accent);
    color:#1a1a1a; font-weight:600; width:100%; padding:11px; margin-top:4px;}
  button.primary:hover{ filter:brightness(1.07); }
  button:disabled{ opacity:.5; cursor:default; }
  #log { background:#12151c; border:1px solid #2c3340; border-radius:6px;
    height:190px; overflow:auto; padding:10px; font-family:Consolas,monospace;
    font-size:12px; white-space:pre-wrap; color:#cdd3df; margin-top:6px; }
  .hint { color:var(--muted); font-size:11px; margin-top:3px; }
</style>
</head>
<body>
<div class="wrap">
  <h1><span class="dot"></span> psd2spine — See-through PSD → Spine</h1>

  <div class="row">
    <label>See-through 分层 PSD<span id="psdlabel"></span></label>
    <div class="field">
      <input id="psd" type="text" placeholder="选择 .psd 文件…">
      <button onclick="pickPsd()">浏览…</button>
    </div>
    <label style="margin-top:6px;cursor:pointer;">
      <input type="checkbox" id="batch" onchange="onBatch()"> 批量处理(输入改为选目录,递归处理其中所有 PSD)
    </label>
  </div>

  <div class="row">
    <label>识别模式</label>
    <div class="field">
      <select id="mode" style="flex:1;background:var(--panel);color:var(--fg);
              border:1px solid #353c4a;padding:8px;border-radius:6px;">
        <option value="auto">自动判别(推荐)</option>
        <option value="smart">智能融合(姿态 + 图层名,补全骨架)</option>
        <option value="ml">纯姿态绑骨(只靠 AI 识别)</option>
        <option value="seethrough">See-through 智能人形</option>
        <option value="generic">通用(任意PSD,逐层一根骨)</option>
      </select>
    </div>
  </div>

  <div class="row">
    <label>输出版本(ML 模式忽略)</label>
    <div class="field">
      <select id="profile" style="flex:1;background:var(--panel);color:var(--fg);
              border:1px solid #353c4a;padding:8px;border-radius:6px;">
        <option value="both">两套都出(Essential + Professional)</option>
        <option value="essential">仅 Essential(region 附件,刚性)</option>
        <option value="professional">仅 Professional(mesh+权重,可弯)</option>
      </select>
    </div>
  </div>

  <div class="row">
    <label>输出目录</label>
    <div class="field">
      <input id="out" type="text" placeholder="生成的 Spine 工程放哪…">
      <button onclick="pickOut()">浏览…</button>
    </div>
  </div>

  <div class="row">
    <label>Spine 版本(自动探测,可手动修改兜底)</label>
    <div class="field">
      <input id="ver" type="text" placeholder="如 4.3.17">
      <button onclick="redetect()">重新探测</button>
    </div>
    <div class="hint">探测不到时请手填,使其与你的 Spine 版本一致以消除导入警告。</div>
  </div>

  <div class="row">
    <label style="cursor:pointer;">
      <input type="checkbox" id="aion" onchange="document.getElementById('aibox').style.display=this.checked?'block':'none'">
      启用 AI 视觉增强(smart 模式下,用视觉大模型补全部位识别)
    </label>
    <div id="aibox" style="display:none;margin-top:6px;">
      <div class="field" style="margin-bottom:6px;">
        <input id="aiurl" type="text" placeholder="Base URL,如 https://api.openai.com/v1">
      </div>
      <div class="field" style="margin-bottom:6px;">
        <input id="aikey" type="text" placeholder="API Key">
      </div>
      <div class="field">
        <input id="aimodel" type="text" placeholder="模型 id,如 gpt-4o">
      </div>
      <div class="hint">OpenAI 兼容接口;仅 smart 模式生效,失败会自动忽略只用本地融合。</div>
    </div>
  </div>

  <button class="primary" id="run" onclick="run()">生成 Spine 工程</button>

  <div class="row" style="margin-top:14px;">
    <label>日志</label>
    <div id="log"></div>
  </div>
</div>

<script>
  function log(s){ const el=document.getElementById('log');
    el.textContent += s; el.scrollTop = el.scrollHeight; }
  function val(id){ return document.getElementById(id).value.trim(); }
  function set(id,v){ document.getElementById(id).value = v; }

  function isBatch(){ return document.getElementById('batch').checked; }
  function onBatch(){
    document.getElementById('psdlabel').textContent =
      isBatch() ? '(目录)' : '';
    document.getElementById('psd').value='';
  }
  async function pickPsd(){
    const p = await pywebview.api.pick_input(isBatch());
    if(p){ set('psd', p);
      if(!val('out')) set('out', await pywebview.api.default_out(p)); }
  }
  async function pickOut(){
    const p = await pywebview.api.pick_out(); if(p) set('out', p);
  }
  async function redetect(){
    const v = await pywebview.api.detect();
    if(v){ set('ver', v); log('已探测到 Spine 版本: '+v+'\n'); }
    else log('未探测到 Spine 版本,请手动填写。\n');
  }
  async function run(){
    const btn=document.getElementById('run'); btn.disabled=true;
    log('\n开始生成…\n');
    let ai = null;
    if(document.getElementById('aion').checked){
      ai = {base_url: val('aiurl'), api_key: val('aikey'), model: val('aimodel')};
    }
    const r = await pywebview.api.generate(val('psd'), val('out'), val('ver'),
        document.getElementById('profile').value, isBatch(),
        document.getElementById('mode').value, ai);
    log(r.log);
    log(r.ok ? ('\n✅ 完成 -> '+r.out+'\n') : ('\n❌ 失败\n'));
    btn.disabled=false;
  }
  window.addEventListener('pywebviewready', redetect);
</script>
</body>
</html>
"""


class Api:
    def __init__(self):
        self.window = None

    def pick_input(self, batch):
        if batch:
            res = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        else:
            res = self.window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=("PSD 文件 (*.psd)", "所有文件 (*.*)"))
        return res[0] if res else ""

    def pick_out(self):
        res = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        return res[0] if res else ""

    def default_out(self, psd):
        if not psd:
            return ""
        base = psd if os.path.isdir(psd) else os.path.dirname(psd)
        return os.path.join(base, "spine_out")

    def detect(self):
        return psd2spine.detect_spine_version() or ""

    def generate(self, psd, out, ver, profile="both", batch=False,
                 mode="auto", ai=None):
        ver = ((ver or "").strip()
               or psd2spine.detect_spine_version()
               or psd2spine.DEFAULT_SPINE_VERSION)
        if profile not in ("essential", "professional", "both"):
            profile = "both"
        if mode not in ("auto", "seethrough", "generic", "ml", "smart"):
            mode = "auto"
        ai_cfg = None
        if ai and ai.get("base_url") and ai.get("api_key") and ai.get("model"):
            ai_cfg = {"base_url": ai["base_url"].strip(),
                      "api_key": ai["api_key"].strip(),
                      "model": ai["model"].strip()}
        if not psd or not os.path.exists(psd):
            return {"ok": False, "log": "错误:请选择有效的输入\n", "out": ""}
        if batch and not os.path.isdir(psd):
            return {"ok": False, "log": "错误:批量模式下请选择目录\n", "out": ""}
        if not batch and not os.path.isfile(psd):
            return {"ok": False, "log": "错误:请选择 PSD 文件\n", "out": ""}
        if not out:
            return {"ok": False, "log": "错误:请选择输出目录\n", "out": ""}
        if not psd2spine.VERSION_RE.fullmatch(ver):
            return {"ok": False,
                    "log": "错误:版本号格式应为 x.y.z(如 4.3.17)\n", "out": ""}
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                if batch:
                    psd2spine.batch(psd, out, ver, profile, mode, ai_cfg)
                else:
                    psd2spine.main(psd, out, ver, profile, mode, ai_cfg)
            return {"ok": True, "log": buf.getvalue(), "out": out}
        except Exception:
            return {"ok": False,
                    "log": buf.getvalue() + "\n" + traceback.format_exc(),
                    "out": ""}


def main():
    api = Api()
    window = webview.create_window(
        "psd2spine", html=HTML, js_api=api, width=680, height=620,
        min_size=(560, 520), background_color="#1f2430")
    api.window = window
    webview.start()


if __name__ == "__main__":
    main()
