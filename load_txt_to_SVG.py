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

FULLWIDTH_SPACE = "\u3000"

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

def apply_style(el: etree.Element, props: Dict[str, str]):
    if not props: return
    cur = {}
    for item in (el.get("style","").split(";")):
        if ":" in item:
            k,v = item.split(":",1); cur[k.strip()] = v.strip()
    cur.update(props)
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

def read_settings(path: Path) -> Dict:
    if path.exists():
        try: return json.loads(path.read_text(encoding="utf-8"))
        except Exception: pass
    return {"lines_per_page":{"p1":13,"default":30},"cols_per_line":{"p1":40,"default":48}}

def wrap_text_to_cols(text: str, cols: int) -> List[str]:
    text = text.replace("<br>","\n").replace("\\\\","\n")
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

def parse_markdown(src: str, indent_fullwidth: bool, pagebreak_marker: str) -> List[Dict]:
    src = src.replace("\r\n","\n").replace("\r","\n")
    lines = src.split("\n"); blocks=[]; i=0
    while i < len(lines):
        line = lines[i]
        if line.strip() == pagebreak_marker:
            blocks.append({"type":"pagebreak","text":""}); i+=1; continue
        if re.match(r"^##\s+", line):
            blocks.append({"type":"h2","text": re.sub(r"^##\s+","",line).strip()}); i+=1; continue
        if re.match(r"^(-|\*)\s+", line):
            text = re.sub(r"^(-|\*)\s+","・",line).rstrip()
            if indent_fullwidth: text = FULLWIDTH_SPACE + text
            blocks.append({"type":"li","text":text}); i+=1; continue
        para=[line]; i+=1
        while i<len(lines) and lines[i].strip()!="": para.append(lines[i]); i+=1
        text="\n".join(para).rstrip()
        if indent_fullwidth and text and not text.lstrip().startswith("・"):
            text = FULLWIDTH_SPACE + text
        blocks.append({"type":"p","text":text})
        if i<len(lines) and lines[i].strip()=="": blocks.append({"type":"p","text":""}); i+=1
    return blocks

def add_line_text(el: etree.Element, line: str, inline_bold: bool, style_props: Dict[str,str]):
    if el.tag.endswith("text"):
        tline = etree.SubElement(el, inkex.addNS("tspan","svg"))
        tline.set(inkex.addNS("role","sodipodi"), "line")
        apply_style(tline, style_props)
        if inline_bold:
            for seg,is_bold in split_inline_bold(line):
                if seg=="": continue
                t = etree.SubElement(tline, inkex.addNS("tspan","svg"))
                if is_bold: apply_style(t, {"font-weight":"bold"})
                t.text = seg
        else:
            tline.text = line
    else:
        para = etree.SubElement(el, inkex.addNS("flowPara","svg"))
        apply_style(para, style_props)
        if inline_bold:
            for seg,is_bold in split_inline_bold(line):
                if seg=="": continue
                if is_bold:
                    sp = etree.SubElement(para, inkex.addNS("flowSpan","svg"))
                    apply_style(sp, {"font-weight":"bold"}); sp.text = seg
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

        for lab, el in (("p1",text_p1),("p2",text_p2)):
            if lab in label_css: apply_style(el, label_css[lab])

        pages: List[Tuple[str, etree.Element]] = [("p1",text_p1), ("p2",text_p2)]
        n=3
        while True:
            lab=f"p{n}"; layer=find_layer_by_label(doc, lab)
            if layer is None: break
            t=find_text_by_label(layer, lab)
            if t is None: break
            if lab in label_css: apply_style(t, label_css[lab])
            pages.append((lab,t)); n+=1

        for _,el in pages: clear_text(el)

        lp1 = int(settings["lines_per_page"].get("p1",13))
        lpn = int(settings["lines_per_page"].get("default",30))
        cp1 = int(settings["cols_per_line"].get("p1",40))
        cpn = int(settings["cols_per_line"].get("default",48))
        def limits(idx:int)->Tuple[int,int]: return (lp1,cp1) if idx==0 else (lpn,cpn)
        def style_for(kind:str)->Dict[str,str]:
            return {"h2":"h2","li":"li"}.get(kind, "p")
        def style_props(kind:str)->Dict[str,str]:
            sel = style_for(kind); return semantic_css.get(sel, {})

        def get_page(idx:int)->etree.Element:
            nonlocal pages, doc, layer_p2
            if idx < len(pages): return pages[idx][1]
            new_lab=f"p{idx+1}"; new_layer=clone_layer_as(doc, layer_p2, new_lab, "p2", new_lab)
            new_text=find_text_by_label(new_layer, new_lab)
            if new_text is None:
                raise inkex.AbortExtension(f"複製レイヤ {new_lab} にテキストが見つかりません")
            if new_lab in label_css: apply_style(new_text, label_css[new_lab])
            pages.append((new_lab,new_text)); logs.append(f"[INFO] auto layer {new_lab}")
            return new_text

        raw = md_path.read_text(encoding="utf-8")
        blocks = parse_markdown(raw, indent_fullwidth=bool(self.options.indent_fullwidth),
                                pagebreak_marker=self.options.force_newpage_marker)

        page_idx=0; used=0
        for blk in blocks:
            if blk["type"]=="pagebreak":
                page_idx+=1; used=0; continue
            cols = limits(page_idx)[1]
            lines = wrap_text_to_cols(blk["text"], cols) or [""]
            i=0
            while i < len(lines):
                m,_ = limits(page_idx)
                if used >= m:
                    page_idx+=1; used=0
                el = get_page(page_idx)
                add_line_text(el, lines[i], inline_bold=True, style_props=semantic_css.get(style_for(blk["type"]), {}))
                used += 1; i += 1

        try:
            log_path.write_text("\n".join(logs), encoding="utf-8")
        except Exception as e:
            inkex.utils.debug(f"ログ書出し失敗: {e}")

if __name__ == "__main__":
    MdFill().run()
