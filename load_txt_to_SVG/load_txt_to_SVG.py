#!/usr/bin/env python
# coding: utf-8
r"""
MD→SVG flowed text auto-fill for layers p1..pN

要点:
- Inkscapeは拡張実行時にSVGの一時コピーを%TEMP%に作る。従来は%TEMP%を作業フォルダと誤認。
- 本版は base_dir（UI引数）を優先。未指定時は自動探索で Downloads/Documents/Desktop なども見る。
"""
from __future__ import annotations
import re, json
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import inkex
from inkex import NSS
from lxml import etree
from copy import deepcopy
import subprocess, sys

PX_PER_MM = 96.0 / 25.4
FULLWIDTH_SPACE = "\u3000"
INK_LABEL = inkex.addNS('label', 'inkscape')
INK_GROUPMODE = inkex.addNS('groupmode', 'inkscape')
SODO_INSEN = inkex.addNS('insensitive', 'sodipodi')


def _find_layer_by_label(root, label: str):
    for g in root.iterfind('.//svg:g', namespaces=inkex.NSS):
        if g.get(INK_GROUPMODE) == 'layer' and g.get(INK_LABEL) == label:
            return g
    return None

def _find_layer_by_labels(root, labels):
    for lb in labels:
        node = _find_layer_by_label(root, lb)
        if node is not None:
            return node
    return None

def _delete_layer_if_exists(root, label: str):
    node = _find_layer_by_label(root, label)
    if node is not None and node.getparent() is not None:
        node.getparent().remove(node)

def _new_layer(root, label: str):
    g = inkex.Group()
    g.set(INK_GROUPMODE, 'layer')
    g.set(INK_LABEL, label)
    g.set('id', root.get_unique_id(label.replace('[','').replace(']','')))
    root.append(g)
    return g

def _clone_children(dst_parent, src_parent):
    for child in list(src_parent):
        dst_parent.append(deepcopy(child))

def ensure_page_horizontal(self, n: int, step_px: float = 220.0, logs=None):
    """
    p3以降のページレイヤーを横方向へ整列して再作成。
    ・既存 p{n} があれば削除
    ・[フォーマット]/f2 を複製
    ・レイヤー transform=translate(x,0) を設定
    """
    # root = self.svg.getroot()
    root = self.document.getroot()

    if n < 3:
        return  # p1/p2は現状維持

    # 既存p{n}削除→新規作成
    label = f"p{n}"
    _delete_layer_if_exists(root, label)
    page_layer = _new_layer(root, label)

    # 位置を横方向へ
    x = (n - 1) * step_px
    page_layer.set('transform', f"translate({x},0)")
    if logs is not None:
        logs.append(f"[POS] page{n} x={(n-1)*step_px} dx_px={step_px}")

    # フォーマット取得（[フォーマット] または フォーマット）
    fmt_root = _find_layer_by_labels(root, ['[フォーマット]', 'フォーマット'])
    if fmt_root is None:
        raise inkex.AbortExtension('[ERR] レイヤー「[フォーマット]」が見つかりません')

    # f2取得
    f2 = _find_layer_by_label(fmt_root, 'f2')
    if f2 is None:
        raise inkex.AbortExtension('[ERR] 「[フォーマット]」配下に「f2」が見つかりません')

    # 一時ロック解除 → 複製 → 復帰
    was_locked = fmt_root.get(SODO_INSEN) == 'true'
    if was_locked:
        fmt_root.set(SODO_INSEN, 'false')
    _clone_children(page_layer, f2)
    if was_locked:
        fmt_root.set(SODO_INSEN, 'true')

    return page_layer



def ensure_pages_horizontal_from_p3(self, total_pages: int, step_px: float = 220.0, logs=None):
    """
    必要ページ数 total_pages に対し、p3...pN を横並びで用意。
    """
    for n in range(3, max(3, total_pages + 1)):
        ensure_page_horizontal(self, n, step_px=step_px, logs=logs)


def _ensure_pages(docroot):
    nv = _namedview(docroot)
    if nv is None:
        nv = etree.Element(inkex.addNS("namedview","sodipodi"))
        docroot.insert(0, nv)
    pgs = _list_pages(nv)
    if not pgs:
        # ルートの viewBox or width/height から1枚作る
        vb = (docroot.get("viewBox") or "0 0 210 297").split()
        w = float(docroot.get("width",  vb[2])); h = float(docroot.get("height", vb[3]))
        pg = etree.Element(inkex.addNS("page","inkscape"))
        pg.set("x","0"); pg.set("y","0"); pg.set("width",str(w)); pg.set("height",str(h))
        pg.set("id","page1"); nv.append(pg)


def _namedview(root):
    res = root.xpath("//sodipodi:namedview", namespaces=NSS)
    return res[0] if res else None

def _list_pages(nv):
    out = []
    for pg in nv.xpath("./inkscape:page", namespaces=NSS):
        out.append((pg, float(pg.get("x","0")), float(pg.get("y","0")),
                        float(pg.get("width","0")), float(pg.get("height","0"))))
    return out


def _doc_units(nv):
    return nv.get(inkex.addNS("document-units","inkscape"), "px").lower()

def _append_page_like(nv, last_page, gap_units=10.0):
    _, lx, ly, lw, lh = last_page
    nx = lx + lw + gap_units
    new_pg = etree.Element(inkex.addNS("page","inkscape"))
    new_pg.set("x", str(nx)); new_pg.set("y", str(ly))
    new_pg.set("width", str(lw)); new_pg.set("height", str(lh))
    new_pg.set("id", f"page{len(nv.xpath('./inkscape:page', namespaces=NSS))+1}")
    nv.append(new_pg)
    return (new_pg, nx, ly, lw, lh)


def _collect_ids(root):
    used = set()
    for el in root.iter():
        i = el.get("id")
        if i:
            used.add(i)
    return used

def _gen_unique_id(used, base: str):
    base = re.sub(r"[^a-zA-Z0-9_.:-]", "_", base or "id")
    cand = base
    i = 1
    while cand in used:
        cand = f"{base}-{i}"
        i += 1
    used.add(cand)
    return cand


def is_fullwidth(ch: str) -> bool:
    o = ord(ch)
    return (0x3000 <= o <= 0x30FF) or (0x3400 <= o <= 0x9FFF) or (0xFF01 <= o <= 0xFF60) or (0xFFE0 <= o <= 0xFFE6)

def split_inline_bold(text: str) -> List[Tuple[str, bool]]:
    parts: List[Tuple[str, bool]] = []
    i = 0
    for m in re.finditer(r"\*\*(.+?)\*\*", text):
        if m.start() > i: parts.append((text[i:m.start()], False))
        parts.append((m.group(1), True)); i = m.end()
    if i < len(text): parts.append((text[i:], False))
    return parts or [("", False)]

def apply_style(el, props):
    if not props: return
    cur = {}
    for item in el.get("style","").split(";"):
        if ":" in item:
            k,v = item.split(":",1); cur[k.strip()] = v.strip()
    cur.update(props)
    # font-family / font-weight → -inkscape-font-specification を同期
    fam = (cur.get("font-family") or "").strip().strip('"\'')
    wt  = (cur.get("font-weight") or "").lower()
    if fam or wt:
        spec = fam
        if wt in ("bold","700","800","900"): spec = (spec + " Bold").strip()
        if spec: cur["-inkscape-font-specification"] = spec
    el.set("style",";".join(f"{k}:{v}" for k,v in cur.items() if v is not None))


def parse_css(css_path: Path) -> Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, str]]]:
    semantic: Dict[str, Dict[str, str]] = {}
    by_label: Dict[str, Dict[str, str]] = {}
    if not css_path.exists(): return semantic, by_label
    raw = css_path.read_text(encoding="utf-8", errors="ignore")
    raw = re.sub(r"/\*.*?\*/","", raw, flags=re.S)
    sem_rule = re.compile(r"(##|p|li|h2)\s*\{([^}]*)\}", re.I)
    prop_re = re.compile(r"([a-zA-Z0-9\-]+)\s*:\s*([^;]+);?")
    for m in sem_rule.finditer(raw):
        sel = m.group(1).lower()
        if sel == "##": sel = "h2"
        body = m.group(2)
        semantic[sel] = {pm.group(1).strip(): pm.group(2).strip() for pm in prop_re.finditer(body)}
    lab_rule = re.compile(r"\[\s*inkscape\\:label\s*=\s*\"([^\"]+)\"\s*\]\s*\{([^}]*)\}", re.I)
    for m in lab_rule.finditer(raw):
        lab = m.group(1).strip(); body = m.group(2)
        by_label[lab] = {pm.group(1).strip(): pm.group(2).strip() for pm in prop_re.finditer(body)}
    return semantic, by_label

def find_layer_by_label(root: etree.Element, name: str) -> Optional[etree.Element]:
    for g in root.iterfind(".//svg:g", namespaces=NSS):
        if g.get(f"{{{NSS['inkscape']}}}groupmode")=="layer" and g.get(f"{{{NSS['inkscape']}}}label")==name:
            return g
    return None

# def find_text_by_label(scope: etree.Element, label: str) -> Optional[etree.Element]:
#     res = scope.xpath(".//*[@inkscape:label=$lab and (self::svg:text or self::svg:flowRoot)]", namespaces=NSS, lab=label)
#     return res[0] if res else None
def find_text_by_label(scope, label: str):
    # 変数渡しは不可。式に直書きする
    xp = f".//*[@inkscape:label='{label}' and (self::svg:text or self::svg:flowRoot)]"
    res = scope.xpath(xp, namespaces=NSS)
    return res[0] if res else None


def clone_layer_as(root, src_layer, new_name, text_label_old, text_label_new):
    clone = etree.fromstring(etree.tostring(src_layer))
    clone.set(f"{{{NSS['inkscape']}}}label", new_name)
    for el in clone.xpath(".//*[@inkscape:label]", namespaces=NSS):
        if el.get(f"{{{NSS['inkscape']}}}label") == text_label_old:
            el.set(f"{{{NSS['inkscape']}}}label", text_label_new)
            break
    # ↓ ここを置換（unique_id廃止）
    used = _collect_ids(root)
    for el in clone.iter():
        if "id" in el.attrib:
            el.attrib["id"] = _gen_unique_id(used, el.attrib["id"])
    root.append(clone)
    return clone


def clear_text(el: etree.Element):
    local = el.tag.split('}')[-1]
    if local == "flowRoot":
        for child in list(el):
            c = child.tag.split('}')[-1]
            if c in ("flowPara","flowDiv","flowSpan"):
                el.remove(child)
        el.text = None
    else:
        for child in list(el): el.remove(child)
        el.text = None

def reset_box_style(el, settings):
    # 親<Text>に残るフォント系を除去（子の指定を優先させる）
    style = el.get("style","")
    keep = {}
    for item in style.split(";"):
        if ":" in item:
            k,v = item.split(":",1); keep[k.strip()] = v.strip()
    for k in ["font-family","font-size","font-weight","font-style",
              "-inkscape-font-specification","letter-spacing","word-spacing",
              "text-anchor","text-align","line-height"]:
        keep.pop(k, None)
    el.set("style",";".join(f"{k}:{v}" for k,v in keep.items()))

    base = {
        "text-anchor": "start",
        "text-align": "start",
        "white-space": "pre",
        "stroke-dasharray": "none",
    }
    sd = (settings or {}).get("style_defaults", {})
    if sd.get("fill"):        base["fill"] = sd["fill"]
    if sd.get("stroke"):      base["stroke"] = sd["stroke"]      # ← "none" をそのまま適用
    if sd.get("line-height"): base["line-height"] = sd["line-height"]  # ← 1.65
    apply_style(el, base)


def read_settings(path: Path) -> Dict:
    if path.exists():
        try: return json.loads(path.read_text(encoding="utf-8"))
        except Exception: pass
    return {
        "lines_per_page": {"p1":21, "default":30},
        "cols_per_line": {"p1":86, "default":48},
        "style_defaults": {"fill":"#000000", "stroke":"#ffffff"}
    }

def wrap_text_to_cols(text: str, cols: int) -> List[str]:
    text = text.replace("<br>", "\n")
    text = text.replace("\\\\", "\n")                 # 2本
    text = re.sub(r"(?m)\\\s*$", "\n", text)          # 1本（行末）
    out: List[str] = []
    for para in text.split("\n"):
        if para=="": out.append(""); continue
        line=""; wsum=0
        for ch in para:
            w = 2 if is_fullwidth(ch) else 1
            if wsum + w > cols:
                out.append(line); line=""; wsum=0
                if ch==" ": continue
            line += ch; wsum += w
        out.append(line)
    return out

def parse_markdown(src: str, indent_fullwidth: bool, pagebreak_marker: str, debug_log=None) -> List[Dict]:
    src = src.replace("\r\n","\n").replace("\r","\n")
    lines = src.split("\n"); blocks=[]; i=0
    H2_RE = re.compile(r"^\s*(?:[#＃]{2}|[0-9０-９]+[\.．)])\s*")   # 1. / １． / ## / ＃＃
    LI_RE = re.compile(r"^\s*(?:[-*\uFF0D\uFF0A・•])\s*(.*)$")      # - * 全角－＊ ・ •（後続空白は任意）

    while i < len(lines):
        line = lines[i]
        if debug_log is not None:
            debug_log.append(f"[LINE {i+1}] {line[:40]}")

        if line.strip() == pagebreak_marker:
            blocks.append({"type":"pagebreak","text":""})
            if debug_log is not None: debug_log.append(f"[PARSE] pagebreak at {i+1}")
            i += 1; continue

        if H2_RE.match(line):
            text = H2_RE.sub("", line)
            blocks.append({"type":"h2","text": text})
            if debug_log is not None: debug_log.append(f"[PARSE] H2: '{text[:40]}'")
            i += 1; continue

        m = LI_RE.match(line)
        if m and m.group(1):
            text = "・" + m.group(1).rstrip()
            blocks.append({"type":"li","text": text})
            if debug_log is not None: debug_log.append(f"[PARSE] LI: '{text[:40]}'")
            i += 1; continue

        # 段落（空行まで連結）
        para=[line]; i+=1
        while i<len(lines) and lines[i].strip()!="": para.append(lines[i]); i+=1
        text="\n".join(para).rstrip()
        blocks.append({"type":"p","text":text})
        if debug_log is not None and text:
            debug_log.append(f"[PARSE] P: '{text[:40]}'")
        if i<len(lines) and lines[i].strip()=="": blocks.append({"type":"p","text":""}); i+=1
    return blocks



def add_line_text(el: etree.Element, line: str, inline_bold: bool, style_props: Dict[str,str]):
    if el.tag.endswith("text"):
        # 行をまとめる tspan（この直後の tail に改行を入れる）
        line_tspan = etree.SubElement(el, inkex.addNS("tspan","svg"))
        apply_style(line_tspan, style_props)

        # 空行はスペース1つ（高さ確保）
        if line == "":
            line_tspan.text = " "
            line_tspan.tail = "\n"
            return

        if inline_bold:
            # "**…**" を分割。プレーンは line_tspan.text / bold は子tspan
            buf = ""
            for seg, is_bold in split_inline_bold(line):
                if not seg:
                    continue
                if is_bold:
                    # まずバッファを吐く
                    if buf:
                        if line_tspan.text:
                            line_tspan.text += buf
                        else:
                            line_tspan.text = buf
                        buf = ""
                    b = etree.SubElement(line_tspan, inkex.addNS("tspan","svg"))
                    apply_style(b, {"font-weight":"bold"})
                    b.text = seg
                else:
                    buf += seg
            if buf:
                if line_tspan.text:
                    line_tspan.text += buf
                else:
                    line_tspan.text = buf
        else:
            line_tspan.text = line

        # 行区切りは tail の改行で与える（shape-inside が確実に解釈）
        line_tspan.tail = "\n"

    else:
        # flowed text（flowRoot）は従来通り
        para = etree.SubElement(el, inkex.addNS("flowPara","svg"))
        apply_style(para, style_props)
        if line == "":
            para.text = " "
            return
        if inline_bold:
            for seg,is_bold in split_inline_bold(line):
                if not seg: continue
                if is_bold:
                    sp = etree.SubElement(para, inkex.addNS("flowSpan","svg"))
                    apply_style(sp, {"font-weight":"bold"})
                    sp.text = seg
                else:
                    para.text = (para.text or "") + seg
        else:
            para.text = line


class MdFill(inkex.EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--base_dir", type=str, default="")               # 追加
        pars.add_argument("--indent_fullwidth", type=inkex.Boolean, default=False)
        pars.add_argument("--force_newpage_marker", type=str, default="\\newpage")
        pars.add_argument("--settings_json", type=str, default="settings.json")

    def _auto_workdir(self, docroot) -> Path:
        # 候補を並べ、先に「内容.md」が見つかった場所を採用
        cand: List[Path] = []
        # 1) export-filename の親（設定していれば元SVGの近くであることが多い）
        exp = docroot.get(inkex.addNS('export-filename','inkscape'))
        if exp: cand.append(Path(exp).parent)
        # 2) 入力（一時コピー）の親
        svg_parent = Path(getattr(self.options,"input_file", "") or (self.svg_file or "")).parent
        if svg_parent: cand.append(svg_parent)
        # 3) CWD
        cand.append(Path.cwd())
        # 4) よくある場所
        h = Path.home()
        cand += [h/"Downloads", h/"Documents", h/"Desktop"]
        # 5) 重複除去
        seen=set(); uniq=[]
        for c in cand:
            p=c.resolve()
            if p in seen: continue
            seen.add(p); uniq.append(p)
        for c in uniq:
            try:
                if (c/"内容.md").exists(): return c
            except Exception:
                pass
        return uniq[0] if uniq else Path.cwd()

    def effect(self):
        doc = self.document.getroot()
        _ensure_pages(doc)
        
        # 基準フォルダの決定
        b = (self.options.base_dir or "").strip()
        if b:
            workdir = Path(b).expanduser()
        else:
            workdir = self._auto_workdir(doc)

        md_path = workdir / "内容.md"
        css_path = workdir / "書式.css"
        settings_path = workdir / (self.options.settings_json or "settings.json")
        log_path = workdir / "inkscape_md_fill.log"
        logs: List[str] = [f"[INFO] workdir={workdir}"]

        if not md_path.exists():
            raise inkex.AbortExtension(f"内容.md が見つかりません: {md_path}")

        semantic_css, label_css = parse_css(css_path)
        settings = read_settings(settings_path)

        layer_p1 = find_layer_by_label(doc, "p1")
        layer_p2 = find_layer_by_label(doc, "p2")
        if layer_p1 is None or layer_p2 is None:
            raise inkex.AbortExtension("レイヤ 'p1' および 'p2' が必要です。")
        text_p1 = find_text_by_label(layer_p1, "p1")
        text_p2 = find_text_by_label(layer_p2, "p2")
        if text_p1 is None or text_p2 is None:
            raise inkex.AbortExtension("各レイヤ内に label='p1','p2' のテキストが必要です。")

        pages = [("p1",text_p1),("p2",text_p2)]
        n = 3
        while True:
            lab = f"p{n}"
            layer = find_layer_by_label(doc, lab)
            if layer is None:
                break
            t = find_text_by_label(layer, lab)
            if t is None:
                break
            pages.append((lab, t))
            n += 1

        for lab, el in pages:
            clear_text(el)
            reset_box_style(el, settings)   # 親<Text>で font-* を除去＋white-space:pre, 色, 行高セット
            if lab in label_css:
                apply_style(el, label_css[lab])



        lp1 = int(settings["lines_per_page"].get("p1",21))
        lpn = int(settings["lines_per_page"].get("default",30))
        cp1 = int(settings["cols_per_line"].get("p1",86))
        cpn = int(settings["cols_per_line"].get("default",48))
        def limits(idx: int) -> Tuple[int, int]:
            page_lab = f"p{idx+1}"
            lp = settings["lines_per_page"].get(page_lab,
                settings["lines_per_page"].get("default", 30))
            cp = settings["cols_per_line"].get(page_lab,
                settings["cols_per_line"].get("default", 48))
            return int(lp), int(cp)

        def style_for(kind:str)->Dict[str,str]:
            return {"h2":"h2","li":"li"}.get(kind, "p")
        def style_props(kind: str) -> Dict[str, str]:
            sel = {"h2":"h2","li":"li"}.get(kind, "p")
            props = dict(semantic_css.get(sel, {}))
            # settings の行高を直付け（ズレ対策）
            lh = (settings.get("style_defaults", {}) or {}).get("line-height")
            if lh and "line-height" not in props:
                props["line-height"] = lh
            # 見出しの保険（CSSが無くても BIZ UDGothic Bold）
            if sel == "h2":
                props.setdefault("font-family", "BIZ UDGothic")
                props.setdefault("font-weight", "bold")
            return props

        def get_page(idx:int)->etree.Element:
            nonlocal pages, doc, layer_p2, logs
            logs.append(f"[DBG] get_page idx={idx} existing_layers={len(pages)}")

            # 既存 p{idx+1} レイヤがあればそれを返す（先に事前生成分を拾う）
            pre_lab = f"p{idx+1}"
            pre_layer = find_layer_by_label(doc, pre_lab)
            if pre_layer is not None:
                pre_text = find_text_by_label(pre_layer, pre_lab)
                if pre_text is not None:
                    if idx >= len(pages):
                        pages.append((pre_lab, pre_text))
                    return pre_text

            # 先に物理ページを確保（既存レイヤがあっても必ず実行）
            nv = _namedview(doc)
            if nv is None: raise inkex.AbortExtension("sodipodi:namedview が見つかりません")
            pgs = _list_pages(nv)
            if not pgs:   raise inkex.AbortExtension("inkscape:page が見つかりません")
            while len(pgs) <= idx:
                pgs.append(_append_page_like(nv, pgs[-1], gap_units=10.0))
            logs.append(f"[DBG] phys_pages={len(pgs)} after_ensure")

            # 既存 p{idx+1} レイヤがあればそれを返す
            if idx < len(pages):
                return pages[idx][1]

            # なければ p2 を雛形に複製
            new_lab = f"p{idx+1}"
            new_layer = clone_layer_as(doc, layer_p2, new_lab, "p2", new_lab)
            new_text  = find_text_by_label(new_layer, new_lab)
            if new_text is None:
                raise inkex.AbortExtension(f"複製レイヤ {new_lab} にテキストが見つかりません")

            # p2 基準で新ページ位置へ平行移動
            du = _doc_units(nv); last,new = pgs[idx-1], pgs[idx]
            dx_units = new[1] - last[1]; dx_px = dx_units * PX_PER_MM if du=="mm" else dx_units
            new_layer.set("transform", (new_layer.get("transform","") + f" translate({dx_px},0)").strip())

            if new_lab in label_css: apply_style(new_text, label_css[new_lab])
            pages.append((new_lab, new_text))
            logs.append(f"[INFO] auto page+layer {new_lab} dx={dx_px:.2f}px")
            return new_text


        raw = md_path.read_text(encoding="utf-8")
        blocks = parse_markdown(
            raw,
            indent_fullwidth=bool(self.options.indent_fullwidth),
            pagebreak_marker=self.options.force_newpage_marker,
            debug_log=logs,                              # ← 解析ログを記録
        )
        def _estimate_required_pages(blocks, limits_func) -> int:
            page_idx, used = 0, 0
            for blk in blocks:
                if blk["type"] == "pagebreak":
                    page_idx += 1
                    used = 0
                    continue
                # このブロックが生成する行
                _, cols = limits_func(page_idx)
                lines = wrap_text_to_cols(blk["text"], cols) or [""]
                for _ in lines:
                    m, _ = limits_func(page_idx)
                    if used >= m:
                        page_idx += 1
                        used = 0
                    used += 1
            return page_idx + 1  # 0始まり → 枚数

        required_pages = _estimate_required_pages(blocks, limits)
        ensure_pages_horizontal_from_p3(self, total_pages=required_pages, step_px=220.0, logs=logs)

        page_idx=0; used=0
        for blk in blocks:
            if blk["type"]=="pagebreak":
                page_idx += 1; used = 0
                _ = get_page(page_idx)
                logs.append(f"[PAGE] pagebreak -> p{page_idx+1}")
                continue
            cols = limits(page_idx)[1]
            lines = wrap_text_to_cols(blk["text"], cols) or [""]

            if blk["type"] == "h2":
                lines = [ln.lstrip(FULLWIDTH_SPACE + " ") for ln in lines]
            elif bool(self.options.indent_fullwidth):
                lines = [(FULLWIDTH_SPACE + ln) if ln and not ln.startswith(FULLWIDTH_SPACE) else ln
                        for ln in lines]

            i=0
            while i < len(lines):
                m,_ = limits(page_idx)
                if used >= m:
                    page_idx += 1; used = 0
                    _ = get_page(page_idx)
                    logs.append(f"[PAGE] overflow -> p{page_idx+1}")
                el = get_page(page_idx)
                # 置換：add_line_text 呼び出し
                add_line_text(el, lines[i], inline_bold=True, style_props=style_props(blk["type"]))
                used += 1; i += 1

        try:
            log_path.write_text("\n".join(logs), encoding="utf-8")
            logs.append(f"[BLK] {blk['type']} lines={len(lines)} pidx={page_idx}")
        except Exception as e:
            inkex.utils.debug(f"ログ書出し失敗: {e}")

        h2c = sum(1 for l in logs if "[PARSE] H2:" in l)
        lic = sum(1 for l in logs if "[PARSE] LI:" in l)
        pgc = sum(1 for l in logs if "phys_pages=" in l)
        logs.append(f"[SUMMARY] H2={h2c} LI={lic} phys_pages_seen={pgc}")


if __name__ == "__main__":
    MdFill().run()
