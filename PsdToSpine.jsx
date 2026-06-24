// PsdToSpine.jsx — Photoshop 插件版(ExtendScript / ScriptUI)
// 把当前打开的 See-through 分层文档导出为 Spine 工程(images/*.png + skeleton.json)。
// 与独立 Python 版逻辑一致:同一套人形骨架 + 命名映射 + Professional 网格权重。
// 安装:放入 Photoshop 的 Presets/Scripts 目录,重启后 文件>脚本>PsdToSpine。
#target photoshop

// ---------- 配置 ----------
var LAYER_TO_BONE = {
    "footwear":"foot", "handwear-r":"arm-r", "handwear-l":"arm-l",
    "topwear":"torso", "neck":"neck",
    "back hair":"head", "face":"head", "headwear":"head",
    "ears-l":"head", "ears-r":"head", "nose":"head", "mouth":"head",
    "eyebrow-l":"head", "eyebrow-r":"head", "eyelash-l":"head", "eyelash-r":"head",
    "irides-l":"head", "irides-r":"head", "eyewhite-l":"head", "eyewhite-r":"head"
};
var BONE_PARENT = {
    "root":null, "hip":"root", "torso":"hip", "neck":"torso", "head":"neck",
    "arm-l":"torso", "arm-r":"torso", "foot":"hip"
};
// 可形变层 -> 垂直骨链(顶->底);Professional 版生成网格+权重
var DEFORM_CHAINS = { "handwear_l":["arm-l","arm-l-2"],
                      "handwear_r":["arm-r","arm-r-2"], "topwear":["torso","hip"] };
var MESH_ROWS = 6;

function slug(s){ return s.replace(/[^a-zA-Z0-9_]+/g,"_").replace(/^_+|_+$/g,"").toLowerCase(); }
function round2(v){ return Math.round(v*100)/100; }
function round5(v){ return Math.round(v*100000)/100000; }
// 同时支持原名与 slug 键
(function(){ var o=LAYER_TO_BONE, m={}; for(var k in o){ m[k]=o[k]; m[slug(k)]=o[k]; } LAYER_TO_BONE=m; })();

// ---------- 自动探测 Spine 版本 ----------
function cmpVer(a,b){ var x=a.split("."),y=b.split(".");
    for(var i=0;i<3;i++){ var d=parseInt(x[i],10)-parseInt(y[i],10); if(d) return d; } return 0; }
function detectSpineVersion(){
    var bases=[Folder("~").fsName, Folder.userData.fsName], apps=["Spine","SpineTrial"], best=null;
    for(var b=0;b<bases.length;b++) for(var a=0;a<apps.length;a++){
        var d=Folder(bases[b]+"/"+apps[a]+"/updates"); if(!d.exists) continue;
        var it=d.getFiles();
        for(var i=0;i<it.length;i++){ var nm=decodeURIComponent(it[i].name);
            if(/^\d+\.\d+\.\d+$/.test(nm)){ if(best===null||cmpVer(nm,best)>0) best=nm; } }
    }
    return best;
}

// ---------- 选项对话框(版本 + 输出版本)----------
function promptOptions(detected){
    var dlg=new Window("dialog","PsdToSpine 选项"); dlg.alignChildren="fill";
    dlg.add("statictext",undefined, detected?("已探测到 Spine 版本: "+detected):"未探测到 Spine 版本,请手填");
    var vg=dlg.add("group"); vg.add("statictext",undefined,"Spine 版本:");
    var et=vg.add("edittext",undefined,detected?detected:"4.2.00"); et.characters=12;
    var pp=dlg.add("panel",undefined,"输出版本"); pp.alignChildren="left"; pp.margins=12;
    var rb1=pp.add("radiobutton",undefined,"两套都出(Essential + Professional)");
    var rb2=pp.add("radiobutton",undefined,"仅 Essential(刚性 region)");
    var rb3=pp.add("radiobutton",undefined,"仅 Professional(网格+权重,可弯)");
    rb1.value=true;
    var g=dlg.add("group"); g.alignment="right";
    var ok=g.add("button",undefined,"确定",{name:"ok"}); g.add("button",undefined,"取消",{name:"cancel"});
    var res={v:null,p:null};
    ok.onClick=function(){ var s=et.text.replace(/^\s+|\s+$/g,"");
        if(!/^\d+\.\d+\.\d+$/.test(s)){ alert("版本号格式应为 x.y.z"); return; }
        res.v=s; res.p=rb2.value?"essential":(rb3.value?"professional":"both"); dlg.close(); };
    dlg.show(); return res.v?res:null;
}

// ---------- 图层收集 / 导出 ----------
function collectLayers(doc){ var out=[];
    for(var i=doc.layers.length-1;i>=0;i--){ var ly=doc.layers[i];
        if(ly.typename==="ArtLayer" && ly.kind===LayerKind.NORMAL) out.push(ly); }
    return out;
}
function exportLayerPNG(doc, lay, imgDir, key, l,t,r,b){
    var tmp=doc.duplicate(key+"_tmp", false), keep=lay.name;
    for(var i=tmp.layers.length-1;i>=0;i--){ if(tmp.layers[i].name!==keep) tmp.layers[i].remove(); }
    tmp.crop([l,t,r,b]);
    var f=File(imgDir.fsName+"/"+key+".png"), opt=new PNGSaveOptions(); opt.interlaced=false;
    tmp.saveAs(f, opt, true, Extension.LOWERCASE);
    tmp.close(SaveOptions.DONOTSAVECHANGES);
}

// ---------- 骨骼锚点 ----------
function computeAnchors(bb, W,H,cx, professional){
    function ctr(r){ return [(r.l+r.r)/2.0,(r.t+r.b)/2.0]; }
    function cxOf(){ for(var i=0;i<arguments.length;i++){ if(bb[arguments[i]]) return ctr(bb[arguments[i]])[0]; } return cx; }
    var face=bb["face"],neck=bb["neck"],top=bb["topwear"],foot=bb["footwear"],hl=bb["handwear_l"],hr=bb["handwear_r"];
    var headCx=cxOf("face","neck","headwear");
    var neckTop=neck?neck.t:(face?face.b:232), neckBot=neck?neck.b:(neckTop+100);
    var torsoCx=cxOf("topwear"), topT=top?top.t:neckBot, topB=top?top.b:H*0.7;
    var hipY=topT+0.70*(topB-topT);
    var P={ "root":[cx,H], "hip":[torsoCx,hipY], "torso":[torsoCx,neckBot],
            "neck":[headCx,neckBot], "head":[headCx,neckTop],
            "arm-l": hl?[hl.l,hl.t]:[torsoCx+80,neckBot], "arm-r": hr?[hr.r,hr.t]:[torsoCx-80,neckBot],
            "foot": foot?ctr(foot):[cx,H*0.95] };
    var order=["root","hip","torso","neck","head","arm-l","arm-r","foot"];
    var parent={}; for(var k in BONE_PARENT) parent[k]=BONE_PARENT[k];
    if(professional){
        if(hl){ P["arm-l-2"]=[(hl.l+hl.r)/2.0, hl.b]; parent["arm-l-2"]="arm-l"; order.push("arm-l-2"); }
        if(hr){ P["arm-r-2"]=[(hr.l+hr.r)/2.0, hr.b]; parent["arm-r-2"]="arm-r"; order.push("arm-r-2"); }
    }
    return {psd:P, order:order, parent:parent};
}

// ---------- Professional 条带网格 ----------
function stripMesh(rec, chain, boneIdx, boneAbs, W,H,cx){
    var l=rec.l,t=rec.t,r=rec.r,b=rec.b,w=rec.w,h=rec.h, R=MESH_ROWS;
    var T=chain[0],B=chain[1], Tx=boneAbs[T][0],Ty=boneAbs[T][1], Bx=boneAbs[B][0],By=boneAbs[B][1];
    var iT=boneIdx[T], iB=boneIdx[B], i, pts=[];
    for(i=0;i<=R;i++) pts.push([l, t+i/R*h]);
    for(i=0;i<=R;i++) pts.push([r, b-i/R*h]);
    var uvs=[], verts=[];
    for(i=0;i<pts.length;i++){ var px=pts[i][0],py=pts[i][1];
        uvs.push(round5((px-l)/w)); uvs.push(round5((py-t)/h));
        var sx=px-cx, sy=H-py, tw=(Ty===By)?0.5:Math.max(0,Math.min(1,(Ty-sy)/(Ty-By)));
        var wB=tw, wT=1-tw;
        if(wT<0.001){ verts.push(1,iB,round2(sx-Bx),round2(sy-By),1); }
        else if(wB<0.001){ verts.push(1,iT,round2(sx-Tx),round2(sy-Ty),1); }
        else { verts.push(2,iT,round2(sx-Tx),round2(sy-Ty),round2(wT),
                          iB,round2(sx-Bx),round2(sy-By),round2(wB)); }
    }
    var tris=[], last=2*R+1;
    for(i=0;i<R;i++){ var ri=last-i, ri1=last-(i+1); tris.push(i,i+1,ri1,i,ri1,ri); }
    var n=pts.length, edges=[];   // 3.8 解析器要求 mesh 带 edges(索引=顶点序号*2)
    for(i=0;i<n;i++){ var j=(i+1)%n; edges.push(i*2, j*2); }
    return '{"type":"mesh","uvs":['+uvs.join(",")+'],"triangles":['+tris.join(",")+
           '],"vertices":['+verts.join(",")+'],"hull":'+n+',"edges":['+edges.join(",")+
           '],"width":'+w+',"height":'+h+'}';
}

// ---------- 构建 skeleton JSON ----------
function buildSkeleton(recs, bb, W,H,cx, ver, professional){
    var an=computeAnchors(bb,W,H,cx,professional), order=an.order, parent=an.parent;
    var boneAbs={}, boneIdx={}, i;
    for(i=0;i<order.length;i++){ var p=an.psd[order[i]]; boneAbs[order[i]]=[p[0]-cx, H-p[1]]; boneIdx[order[i]]=i; }
    var bones=[];
    for(i=0;i<order.length;i++){ var nm=order[i], pa=parent[nm];
        if(pa===null||pa===undefined){ bones.push('{"name":"'+nm+'"}'); }
        else { var dx=round2(boneAbs[nm][0]-boneAbs[pa][0]), dy=round2(boneAbs[nm][1]-boneAbs[pa][1]);
            bones.push('{"name":"'+nm+'","parent":"'+pa+'","x":'+dx+',"y":'+dy+'}'); }
    }
    var slots=[], atts=[];
    for(i=0;i<recs.length;i++){ var rc=recs[i], chain=professional?DEFORM_CHAINS[rc.key]:null, bone, val;
        if(chain && boneIdx[chain[0]]!==undefined && boneIdx[chain[1]]!==undefined){
            bone=chain[0]; val=stripMesh(rc,chain,boneIdx,boneAbs,W,H,cx);
        } else {
            bone=LAYER_TO_BONE[rc.name]||LAYER_TO_BONE[rc.key]||"root";
            var cxx=(rc.l+rc.r)/2.0, cyy=(rc.t+rc.b)/2.0, sx=cxx-cx, sy=H-cyy, ba=boneAbs[bone];
            val='{"x":'+round2(sx-ba[0])+',"y":'+round2(sy-ba[1])+',"width":'+rc.w+',"height":'+rc.h+'}';
        }
        slots.push('{"name":"'+rc.key+'","bone":"'+bone+'","attachment":"'+rc.key+'"}');
        atts.push('"'+rc.key+'":{"'+rc.key+'":'+val+'}');
    }
    return '{\n"skeleton":{"spine":"'+ver+'","images":"./images/","width":'+W+',"height":'+H+'},\n'+
           '"bones":['+bones.join(",")+'],\n"slots":['+slots.join(",")+'],\n'+
           '"skins":[{"name":"default","attachments":{'+atts.join(",")+'}}]\n}';
}

function writeJson(outDir, name, txt){ var f=File(outDir.fsName+"/"+name);
    f.encoding="UTF-8"; f.open("w"); f.write(txt); f.close(); }

// ---------- 主流程 ----------
function run(){
    if(app.documents.length===0){ alert("请先在 Photoshop 中打开 See-through 的分层文件。"); return; }
    var doc=app.activeDocument;
    var opt=promptOptions(detectSpineVersion()); if(!opt) return;
    var outDir=Folder.selectDialog("选择输出目录"); if(!outDir) return;
    var imgDir=Folder(outDir.fsName+"/images"); if(!imgDir.exists) imgDir.create();

    var W=doc.width.as("px"), H=doc.height.as("px"), cx=W/2.0;
    var layers=collectLayers(doc); if(layers.length===0){ alert("没找到可导出的像素图层。"); return; }

    var i, recs=[];
    for(i=0;i<layers.length;i++) layers[i].visible=false;
    for(i=0;i<layers.length;i++){ var lay=layers[i], b=lay.bounds;
        var l=b[0].as("px"),t=b[1].as("px"),r=b[2].as("px"),bo=b[3].as("px");
        if(r-l<=0||bo-t<=0) continue;
        var key=slug(decodeURIComponent(lay.name));
        exportLayerPNG(doc,lay,imgDir,key,l,t,r,bo);
        recs.push({key:key, name:decodeURIComponent(lay.name), l:l,t:t,r:r,b:bo, w:(r-l), h:(bo-t)});
    }
    for(i=0;i<layers.length;i++) layers[i].visible=true;

    var bb={}; for(i=0;i<recs.length;i++) bb[recs[i].key]=recs[i];
    var p=opt.p;
    if(p==="both"){
        writeJson(outDir,"skeleton_essential.json", buildSkeleton(recs,bb,W,H,cx,opt.v,false));
        writeJson(outDir,"skeleton_professional.json", buildSkeleton(recs,bb,W,H,cx,opt.v,true));
    } else {
        writeJson(outDir,"skeleton.json", buildSkeleton(recs,bb,W,H,cx,opt.v, p==="professional"));
    }
    alert("完成!\n输出: "+outDir.fsName+"\n版本: "+opt.v+"  模式: "+p+"\n图层 "+recs.length+" 个。");
}

run();
