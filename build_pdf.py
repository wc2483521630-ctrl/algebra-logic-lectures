#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把多份 HTML 讲义合并成一个带可点击目录的 PDF。

功能：
  1. 按给定顺序读取多份 HTML，提取正文并合并；
  2. 自动生成目录页，目录项是指向对应章节的内部链接；
  3. 用 Chrome 无头模式打印为 PDF（保留公式、图片、CSS 排版）；
  4. 反查 PDF 的命名目标，为同一批锚点添加阅读器侧边栏书签；
  5. 输出最终 PDF 与中间合并 HTML（方便复核/重排）。

用法：
  python build_pdf.py
  python build_pdf.py --input-dir . --output 讲义_带目录.pdf --chrome "C:\\...\\chrome.exe"
  python build_pdf.py --files 大纲.html lectures/L01.html lectures/L02.html --output out.pdf

依赖：pypdf（pip install pypdf）、Chrome 或 Edge。
"""

import argparse
import re
import subprocess
import sys
from html import unescape
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    sys.exit("缺少 pypdf，请先运行：pip install pypdf")


CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]

PRINT_CSS = """
@page { size: A4; margin: 16mm 15mm; }
html, body { background: #fff; }
.cover { break-after: page; text-align: center; padding-top: 38mm; }
.cover-kicker { font-size: 13pt; letter-spacing: .2em; color: #6b7280; margin-bottom: 16mm; }
.cover h1 { font-size: 34pt; font-weight: 700; margin: 0 0 10mm; border: 0; }
.cover-sub { font-size: 17pt; color: #374151; margin-bottom: 26mm; }
.cover-meta { font-size: 11pt; line-height: 2; color: #6b7280; }
.toc-page { break-after: page; }
.toc-page h1 { font-size: 22pt; border-bottom: 2px solid #d1d5db; padding-bottom: 6pt; }
.toc-lectures { list-style: none; padding-left: 0; }
.toc-lectures > li { margin: 0 0 6pt; break-inside: avoid; }
.toc-lectures > li > a { font-weight: 700; font-size: 12pt; }
.toc-sections { list-style: none; padding-left: 14pt; margin: 3pt 0 0; columns: 2; column-gap: 18pt; }
.toc-sections li { font-size: 9.5pt; line-height: 1.55; color: #374151; break-inside: avoid; }
.toc-sections a { color: inherit; text-decoration: none; }
.print-part { break-before: page; }
.print-part:first-of-type { break-before: auto; }
.topbar, .footnav { display: none !important; }
.wrap { max-width: none; padding: 0; }
header h1 { font-size: 23px; }
h2, h3 { break-after: avoid; }
.box, .formula, .ex, .exercise, .tri, .diagram, table, img, svg { break-inside: avoid; }
footer { padding: 10px 0 0; }
a { color: inherit; text-decoration: none; }
"""


def clean_text(html_fragment: str) -> str:
    """从一段 HTML 中取纯文本，用于目录标题。"""
    t = re.sub(r"</span>", " ", html_fragment, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def find_chrome(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            sys.exit(f"找不到浏览器：{p}")
        return p
    for p in CHROME_CANDIDATES:
        if p.exists():
            return p
    sys.exit("未找到 Chrome 或 Edge，请用 --chrome 指定路径")


def collect(files: list[Path]) -> tuple[str, list[tuple[int, str, str]]]:
    """返回合并后的正文，以及书签条目 (层级, 标题, 锚点)。"""
    bodies, entries = [], []
    for idx, path in enumerate(files):
        raw = path.read_text(encoding="utf-8")
        m = re.search(r"<body[^>]*>(.*?)</body>", raw, re.S)
        if not m:
            raise RuntimeError(f"找不到 body：{path}")
        body = m.group(1).strip()

        stem = path.stem
        doc_anchor = f"doc{idx:02d}"
        # 文档级 id 放在第一个标题上，保证目录链接落在讲次开头
        body = re.sub(
            r"(<h1[^>]*)(>)",
            lambda mm: f'{mm.group(1)} id="{doc_anchor}"{mm.group(2)}',
            body,
            count=1,
        )
        if f'id="{doc_anchor}"' not in body:
            body = f'<h1 id="{doc_anchor}"></h1>\n' + body

        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S)
        doc_title = clean_text(h1.group(1)) if h1 else stem
        label = doc_title if idx == 0 else f"{stem} {doc_title}"
        entries.append((0, label, doc_anchor))

        # 为章节标题编号并加 id，收集为二级书签
        counter = [0]

        def tag_h2(mm):
            counter[0] += 1
            anchor = f"s{idx:02d}_{counter[0]:02d}"
            tag = mm.group(1)
            if re.search(r'\bid\s*=', tag):
                existing = re.search(r'\bid\s*=\s*["\']([^"\']+)', tag)
                aid = existing.group(1) if existing else anchor
            else:
                tag += f' id="{anchor}"'
                aid = anchor
            return f"<h2{tag}>" , aid, mm.group(2)

        new_body, pos = [], 0
        for mm in re.finditer(r"<h2([^>]*)>(.*?)</h2>", body, re.S):
            new_body.append(body[pos:mm.start()])
            opened, aid, inner = tag_h2(mm)
            new_body.append(opened + inner + "</h2>")
            entries.append((1, clean_text(inner), aid))
            pos = mm.end()
        new_body.append(body[pos:])
        body = "".join(new_body)

        bodies.append(f'<div class="print-part">{body}</div>')
    return "\n".join(bodies), entries


def build_html(files, entries, bodies, out_html: Path, title: str):
    toc_lines = ['<div class="toc-page"><h1>目录</h1><ol class="toc-lectures">']
    open_sub = False
    for level, label, anchor in entries:
        if level == 0:
            if open_sub:
                toc_lines.append("</ol></li>")
            toc_lines.append(f'<li><a href="#{anchor}">{label}</a>')
            toc_lines.append('<ol class="toc-sections">')
            open_sub = True
        else:
            toc_lines.append(f'<li><a href="#{anchor}">{label}</a></li>')
    if open_sub:
        toc_lines.append("</ol></li>")
    toc_lines.append("</ol></div>")

    cover = f"""
<div class="cover">
  <div class="cover-kicker">代数 · 拓扑 · 逻辑 三线融合</div>
  <h1>{title}</h1>
  <div class="cover-sub">全本打印版 · 可点击目录与书签</div>
  <div class="cover-meta">
    依据 UAL《A Course in Universal Algebra》、Bergman《Universal Algebra》、中文拓扑讲义编写<br>
    共 {sum(1 for e in entries if e[0] == 0)} 份文档 · {sum(1 for e in entries if e[0] == 1)} 个章节 · 2026 年 11 月
  </div>
</div>
"""

    head = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{title} · 带目录打印版</title>
<link rel="stylesheet" href="lectures/style.css">
<style>{PRINT_CSS}</style>
</head>
<body>
"""
    out_html.write_text(
        head + cover + "\n".join(toc_lines) + "\n" + bodies + "\n</body>\n</html>\n",
        encoding="utf-8",
    )


def print_pdf(chrome: Path, html: Path, pdf: Path):
    cmd = [
        str(chrome), "--headless=new", "--disable-gpu", "--no-sandbox",
        "--no-pdf-header-footer", f"--print-to-pdf={pdf.resolve()}", html.resolve().as_uri(),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not pdf.exists():
        sys.exit(f"Chrome 打印失败：{proc.stderr[-1000:]}")


def anchor_pages(reader: PdfReader) -> dict[str, int]:
    """从 PDF 命名目标反查每个锚点所在页码（0 基）。"""
    root = reader.trailer["/Root"]
    dests = None
    if "/Dests" in root:
        dests = root["/Dests"]
    elif "/Names" in root:
        names = root["/Names"].get_object()
        if "/Dests" in names:
            dests = names["/Dests"]
    if dests is None:
        return {}
    dests = dests.get_object()
    if hasattr(dests, "items"):
        items = dests.items()
    else:  # /Names 树是扁平的 [name, dest, name, dest, ...]
        arr = list(dests)
        items = [(arr[i], arr[i + 1]) for i in range(0, len(arr) - 1, 2)]

    result = {}
    for name, dest in items:
        key = str(name).lstrip("/")
        try:
            obj = dest.get_object()
            target = obj[0] if isinstance(obj, list) else obj
            result[key] = reader.get_page_number(target.get_object())
        except Exception:
            continue
    return result


def add_bookmarks(reader: PdfReader, entries, pages, out_pdf: Path):
    writer = PdfWriter()
    writer.append(reader)
    parents = {}
    added = 0
    for level, label, anchor in entries:
        if anchor not in pages:
            continue
        parent = parents.get(0) if level == 1 else None
        item = writer.add_outline_item(
            title=label, page_number=pages[anchor], parent=parent
        )
        if level == 0:
            parents[0] = item
        added += 1
    with open(out_pdf, "wb") as f:
        writer.write(f)
    return added


def main():
    ap = argparse.ArgumentParser(description="多份 HTML 讲义合并为带目录/书签的 PDF")
    ap.add_argument("--input-dir", default=".", help="讲义所在目录")
    ap.add_argument("--files", nargs="*", help="显式指定 HTML 文件（按顺序）")
    ap.add_argument("--output", default=None, help="输出 PDF 路径")
    ap.add_argument("--html", default=None, help="中间合并 HTML 路径")
    ap.add_argument("--chrome", default=None, help="Chrome/Edge 可执行文件路径")
    ap.add_argument("--title", default="代数逻辑讲义", help="封面标题")
    args = ap.parse_args()

    base = Path(args.input_dir).resolve()
    if args.files:
        files = [Path(f) if Path(f).is_absolute() else base / f for f in args.files]
    else:
        outline = base / "代数逻辑讲义_大纲.html"
        files = [outline] + sorted(
            (base / "lectures").glob("L[0-9][0-9].html")
        )
    files = [f for f in files if f.exists()]
    if not files:
        sys.exit("没有找到可转换的 HTML 文件")

    output = Path(args.output) if args.output else base / f"{args.title}_带目录打印版.pdf"
    html_out = (
        Path(args.html) if args.html
        else output.with_name(output.stem + "_source.html")
    )
    tmp_pdf = output.with_name("_tmp_print.pdf")

    bodies, entries = collect(files)
    build_html(files, entries, bodies, html_out, args.title)
    print(f"合并 HTML：{html_out}（{len(entries)} 个目录项）")

    chrome = find_chrome(args.chrome)
    print_pdf(chrome, html_out, tmp_pdf)
    print(f"初版 PDF：{tmp_pdf}")

    reader = PdfReader(str(tmp_pdf))
    pages = anchor_pages(reader)
    print(f"解析到命名目标：{len(pages)} 个 / PDF 共 {len(reader.pages)} 页")

    added = add_bookmarks(reader, entries, pages, output)
    tmp_pdf.unlink(missing_ok=True)
    print(f"完成：{output}（{added} 条书签，{output.stat().st_size} bytes）")


if __name__ == "__main__":
    main()
