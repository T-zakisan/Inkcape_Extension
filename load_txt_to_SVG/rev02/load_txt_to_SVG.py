# -*- coding: utf-8 -*-
# Inkscape 1.x / inkex API
# 目的: レイヤ[label=p1] 内のテキスト[label=t1]に "内容.md" を流し込む
# 書式や行間は未対応。改行のみ反映。
import sys
from pathlib import Path
import inkex
from inkex import TextElement, Tspan, NSS
import re

def _find_layer_by_label(root, label: str):
    """指定labelのレイヤ(<g>)を返す"""
    xp = f".//svg:g[@inkscape:groupmode='layer'][@inkscape:label='{label}']"
    res = root.xpath(xp, namespaces=NSS)
    return res[0] if res else None

def _find_text_by_label(scope, label: str):
    """scope直下で指定labelの<text>を返す"""
    xp = f".//*[@inkscape:label='{label}' and self::svg:text]"
    res = scope.xpath(xp, namespaces=NSS)
    return res[0] if res else None



def _line_height_em(style: str, default=1.4) -> float:
    m = re.search(r"line-height\s*:\s*([0-9.]+)", style or "")
    try: return float(m.group(1)) if m else default
    except: return default

def _ensure_text_lines(text_elem: TextElement, lines):
    # 既存子要素削除
    for c in list(text_elem):
        text_elem.remove(c)
    text_elem.text = None
    # 改行をそのまま表示させる
    text_elem.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    style = text_elem.get("style","")
    if "white-space:" not in style:
        text_elem.set("style", (style + ";white-space:pre").strip(";"))

    # 基準位置
    x0 = (text_elem.get("x") or "0").split()[0]
    y0 = (text_elem.get("y") or "0").split()[0]
    lh = _line_height_em(text_elem.get("style",""), default=1.4)  # em単位

    first = True
    for line in lines:
        t = Tspan()
        t.set("{%s}role" % NSS["sodipodi"], "line")
        t.set("x", x0)
        if first:
            t.set("y", y0)
            first = False
        else:
            t.set("dy", f"{lh}em")
        t.text = line if line != "" else " "
        text_elem.append(t)


class MdFill(inkex.EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--base_dir", type=str, default="")
        # 受け取るだけで未使用（互換のため）
        pars.add_argument("--indent_fullwidth", type=inkex.Boolean, default=False)
        pars.add_argument("--force_newpage_marker", type=str, default="\\newpage")
        pars.add_argument("--settings_json", type=str, default="settings.json")

    def effect(self):
        base = (self.options.base_dir or "").strip()
        workdir = Path(base).expanduser() if base else Path(self.options.input_file).resolve().parent
        md_path = workdir / "内容.md"
        if not md_path.exists():
            raise inkex.AbortExtension(f"内容.md が見つかりません: {md_path}")
        md_text = md_path.read_text(encoding="utf-8-sig", errors="replace")
        # 改行トークンを実改行へ
        md_text = md_text.replace("<br>", "\n")
        md_text = md_text.replace("\\\\", "\n")                   # 「\\」→改行
        md_text = re.sub(r"(?m)\\\s*$", "\n", md_text)            # 行末の単独「\」→改行
        # 2) Markdown風の区切りを改行に
        #   見出し "## " の手前で改行
        md_text = re.sub(r"\s*##\s*", "\n", md_text)
        #   箇条書き " - " / " * " の手前で改行
        md_text = re.sub(r"\s+([\-＊*・])\s+", r"\n\1 ", md_text)
        #   番号付き "1. " などの手前で改行
        md_text = re.sub(r"\s+([0-9０-９]+[\.．])\s+", r"\n\1 ", md_text)
        #   ページ区切りはそのまま文字として残す（今はp1/t1のみ対応）
        # 正規化
        lines = md_text.replace("\r\n","\n").replace("\r","\n").split("\n")

        root = self.svg 
        layer_p1 = _find_layer_by_label(root, "p1")
        if layer_p1 is None:
            raise inkex.AbortExtension("レイヤ[label=p1] が見つかりません。")
        t1 = _find_text_by_label(layer_p1, "t1")
        if t1 is None or not isinstance(t1, TextElement):
            raise inkex.AbortExtension("テキスト[label=t1] の <text> が見つかりません。")
        _ensure_text_lines(t1, lines)

if __name__ == "__main__":
    MdFill().run()
