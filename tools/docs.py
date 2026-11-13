# -*- coding: utf-8 -*-
"""文档读取 —— 把 Word / Excel / PowerPoint / PDF 里的**文字**取出来给模型看。

为什么单独一个工具:
  `file_read` 是按 UTF-8 读文本的; .docx/.xlsx/.pptx 本质是 zip 包,
  直接读只会得到 PK\\x03\\x04 开头的二进制乱码 —— 这就是"读了 Word 但读不出来"的原因。
  这里按格式解包取文字, 只读不改文件:
    .docx/.docm  → word/document.xml   (段落 + 表格单元格)
    .xlsx/.xlsm  → xl/sharedStrings.xml + xl/worksheets/*.xml (逐行逐格)
    .pptx        → ppt/slides/slideN.xml (逐页)
    .doc/.xls/.ppt/.rtf → 本机 Office/WPS COM 只读打开(复用 office 的进程快照清理)
    .pdf         → pypdf / PyPDF2(装了才用, 不硬依赖)
  纯文本类(.txt/.md/.csv/.json/.log/.html)直接当文本读。

探针: `python -c "import tools.docs"` 不会启动任何 Office 进程——只有遇到老二进制
格式(.doc/.xls/.ppt)才走 COM, 并且只结束本次新增的进程。
"""
import html
import os
import re
import zipfile

from . import office as _office

MAX_CHARS = 12000        # 单次返回正文默认上限(留出JSON余量, 保证不被引擎24000字截断)
HARD_MAX_CHARS = 16000   # 单次硬上限: 再大也只会被引擎从尾部切掉, 反而丢失提示信息
FIND_WINDOW = (200, 400)  # 关键词命中: 前取200字, 后取400字
FIND_MAX_HITS = 6        # 最多返回几个命中片段
OUTLINE_MAX = 150        # 章节索引最多返回条数(防目录刷屏)
_TEXT_EXT = (".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log",
             ".xml", ".html", ".htm", ".ini", ".conf", ".yaml", ".yml")
_STATE = {"on": True}


# ---------------- 压缩包式格式(O(1) 依赖, 纯标准库) ----------------

def _docx_text(path):
    """段落级提取: <w:t> 是文字片段, </w:p> 是段落, </w:tc> 单元格, </w:tr> 行。"""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = xml.replace("</w:tc>", "\x02").replace("</w:tr>", "\x03")
    lines = []
    for para in re.split(r"</w:p>", xml):
        chunks = re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", para, re.S)
        cell = "\x02" in para
        row = "\x03" in para
        text = html.unescape("".join(chunks)).strip()
        if text:
            lines.append(text + ("\t" if cell else ""))
        elif cell:
            lines.append("\t")
        if row:
            lines.append("")
    return "\n".join(lines)


def _xlsx_text(path, only_sheet=None):
    """逐工作表、逐行输出 `A1=值 | B1=值`, 空表跳过。"""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            sx = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            for si in re.findall(r"<si>(.*?)</si>", sx, re.S):
                shared.append(html.unescape("".join(
                    re.findall(r"<t(?:\s[^>]*)?>(.*?)</t>", si, re.S))))
        titles = []
        if "xl/workbook.xml" in names:
            wb = z.read("xl/workbook.xml").decode("utf-8", "replace")
            titles = [html.unescape(t) for t in
                      re.findall(r"<sheet[^>]*\bname=\"([^\"]*)\"", wb)]
        sheets = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        out = []
        for idx, fn in enumerate(sheets):
            title = titles[idx] if idx < len(titles) else "Sheet%d" % (idx + 1)
            if only_sheet and str(only_sheet) not in (title, str(idx + 1)):
                continue
            xml = z.read(fn).decode("utf-8", "replace")
            rows = {}
            for m in re.finditer(r"<c r=\"([A-Z]+)(\d+)\"([^>]*)>(.*?)</c>", xml, re.S):
                col, row, attrs, body = m.group(1), int(m.group(2)), m.group(3), m.group(4)
                if 't="s"' in attrs:                      # 共享字符串
                    vm = re.search(r"<v>(\d+)</v>", body)
                    val = shared[int(vm.group(1))] if vm and int(vm.group(1)) < len(shared) else ""
                elif 't="inlineStr"' in attrs:
                    val = html.unescape("".join(re.findall(r"<t(?:\s[^>]*)?>(.*?)</t>", body, re.S)))
                else:
                    vm = re.search(r"<v>(.*?)</v>", body, re.S)
                    val = html.unescape(vm.group(1)) if vm else ""
                    if val.endswith(".0") and re.fullmatch(r"-?\d+\.0", val):
                        val = val[:-2]
                val = str(val).replace("\n", " ").strip()
                if val:
                    rows.setdefault(row, []).append("%s%d=%s" % (col, row, val))
            if rows:
                out.append("### %s" % title)
                for row in sorted(rows):
                    out.append(" | ".join(rows[row]))
                out.append("")
        return "\n".join(out)


def _pptx_text(path):
    """逐页取 <a:t> 文字, 保留页序。"""
    with zipfile.ZipFile(path) as z:
        slides = [n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
        slides.sort(key=lambda n: int(re.search(r"(\d+)", os.path.basename(n)).group(1)))
        out = []
        for i, fn in enumerate(slides, 1):
            xml = z.read(fn).decode("utf-8", "replace")
            parts = []
            for para in re.split(r"</a:p>", xml):
                t = html.unescape("".join(re.findall(r"<a:t(?:\s[^>]*)?>(.*?)</a:t>", para, re.S))).strip()
                if t:
                    parts.append(t)
            if parts:
                out.append("### 第 %d 页" % i)
                out.extend(parts)
                out.append("")
        return "\n".join(out)


def _pdf_text(path):
    """PDF 是可选能力: 用 importlib 动态加载, 免得 PyInstaller 为了 pypdf
    把 numpy/PIL/lxml/cryptography 一起塞进安装包(+50MB)。没装就明确告知用户。"""
    import importlib
    PdfReader = None
    for mod in ("pypdf", "PyPDF2"):
        try:
            PdfReader = getattr(importlib.import_module(mod), "PdfReader")
            break
        except Exception:
            continue
    if PdfReader is None:
        return None
    try:
        reader = PdfReader(path)
        out = []
        for i, page in enumerate(reader.pages, 1):
            try:
                t = (page.extract_text() or "").strip()
            except Exception:
                t = ""
            if t:
                out.append("### 第 %d 页" % i)
                out.append(t)
        return "\n".join(out)
    except Exception:
        return None


# ---------------- 老二进制格式: 本机 Office/WPS 只读打开 ----------------

def _com_text(path, kind):
    """用 COM 以**只读**方式打开取文字, 结束后只清理本次新增的进程。"""
    d = _office._state["detected"] or _office.detect()
    comp = (d.get("components") or {}).get(kind) or {}
    if not comp.get("available"):
        return None, "本机没有可用的 %s, 无法读取该文件" % _office._LABEL.get(kind, kind)
    try:
        import win32com.client as win32
    except Exception as e:
        return None, "缺少 pywin32: %s" % e
    before = _office._snapshot()
    app = None
    doc = None
    try:
        app = win32.DispatchEx(comp["progid"])
        try:
            app.Visible = False
        except Exception:
            pass
        try:
            app.DisplayAlerts = 0
        except Exception:
            pass
        if kind == "word":
            doc = app.Documents.Open(os.path.abspath(path), ReadOnly=True,
                                     AddToRecentFiles=False, ConfirmConversions=False)
            text = doc.Content.Text
        elif kind == "excel":
            doc = app.Workbooks.Open(os.path.abspath(path), ReadOnly=True, UpdateLinks=0)
            parts = []
            for sh in doc.Worksheets:
                parts.append("### %s" % sh.Name)
                used = sh.UsedRange
                vals = used.Value
                if vals is None:
                    continue
                if not isinstance(vals, tuple):
                    vals = ((vals,),)
                for row in vals:
                    if not isinstance(row, tuple):
                        row = (row,)
                    cells = [str(c) for c in row if c not in (None, "")]
                    if cells:
                        parts.append(" | ".join(cells))
                parts.append("")
            text = "\n".join(parts)
        else:
            doc = app.Presentations.Open(os.path.abspath(path), WithWindow=False,
                                         ReadOnly=True)
            parts = []
            for i, slide in enumerate(doc.Slides, 1):
                parts.append("### 第 %d 页" % i)
                for shape in slide.Shapes:
                    try:
                        if shape.HasTextFrame and shape.TextFrame.HasText:
                            parts.append(str(shape.TextFrame.TextRange.Text).strip())
                    except Exception:
                        continue
                parts.append("")
            text = "\n".join(parts)
        return str(text or ""), None
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:200])
    finally:
        try:
            if doc is not None:
                doc.Close(SaveChanges=False)
        except Exception:
            pass
        if app is not None:
            _office._cleanup(app, before)


# ---------------- 工具入口 ----------------

def _outline(text):
    """提取编号章节标题及字符位置 —— 大文档先拿骨架, 再按 pos 精读。
    目录里的标题带页码、正文里的写法可能有空格差异, 统一归一化编号后按标题去重。"""
    rx = re.compile(r"(?m)^\s*(\d+(?:\s*\.\s*\d+){0,4})\s*[、.．]?\s+([^\n]{2,60})$")
    out, seen = [], set()
    for m in rx.finditer(text):
        num = m.group(1).replace(" ", "")
        title = (num + " " + m.group(2).strip()).strip()
        key = title[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append({"pos": m.start(), "title": title[:80]})
        if len(out) >= OUTLINE_MAX:
            break
    return out


SCHEMA_READ = {
    "type": "function",
    "function": {
        "name": "doc_read",
        "description": "读取 Word/Excel/PowerPoint/PDF 里的文字内容(只读, 不改文件)。"
                       "**不要用 file_read 读 docx/xlsx/pptx**——那是压缩包, 直接读只会得到二进制乱码。"
                       "支持 .docx/.docm/.xlsx/.xlsm/.pptx, 以及老格式 .doc/.xls/.ppt(需本机 Office/WPS); "
                       "PDF 需本机装有 pypdf, 没有就让用户另存为 docx; "
                       "纯文本类(.txt/.md/.csv/.json/.log/.html 等)也可读。"
                       "**大文档三步走**: outline=true 先取章节索引(标题+字符位置) → "
                       "find='关键词' 或 fs_grep 定位 → offset=<pos> 精读该处"
                       "(fs_grep 命中文档时返回的 pos 可直接填进 offset)。"
                       "返回按段落/工作表/页组织的纯文本; 元信息在 text 之前, 截断也看得见 hint。"
                       "不要反复加大 max_chars 重读同一文件——单次硬上限16000字, 加大拿不到更后面的内容。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件绝对路径"},
            "max_chars": {"type": "number", "description": "本段最多返回字数, 默认12000, 硬上限16000"},
            "offset": {"type": "number", "description": "从第几字开始读(大文档翻页用), 默认0"},
            "find": {"type": "string", "description": "关键词定位, 多个词用 | 分隔; 返回命中处位置与上下文"},
            "outline": {"type": "boolean", "description": "true=只返回章节索引(标题+pos), 大文档第一步先拿骨架"},
            "sheet": {"type": "string", "description": "可选: 只读某个工作表名(Excel)"},
        }, "required": ["path"]},
    },
}


def read(path, max_chars=MAX_CHARS, offset=0, find="", outline=False,
         sheet=None, **_ignored):
    """读文档正文。大文档用 offset 翻页 / find 关键词定位, 不再"从头截断就没辙"。

    返回 dict 的**字段顺序有讲究**: 引擎会把工具结果整体截到 24000 字,
    所以 path/chars/next_offset/hint 必须排在 text 前面, 否则模型只看到一段
    没有出路的正文(这正是"读大文档读到天荒地老"的根因之一)。
    """
    path = str(path or "").strip().strip('"')
    if not path:
        return {"error": "缺少文件路径"}
    path = os.path.expandvars(os.path.expanduser(path))
    if not os.path.isfile(path):
        return {"error": "文件不存在: %s" % path, "hint": "先用 fs_ls / fs_glob 确认路径"}
    try:
        max_chars = min(max(int(max_chars or MAX_CHARS), 500), HARD_MAX_CHARS)
    except (TypeError, ValueError):
        max_chars = MAX_CHARS
    try:
        offset = max(int(offset or 0), 0)
    except (TypeError, ValueError):
        offset = 0
    find = str(find or "").strip()
    ext = os.path.splitext(path)[1].lower()
    kind = {"word": (".docx", ".docm", ".doc", ".rtf"),
            "excel": (".xlsx", ".xlsm", ".xls", ".csv"),
            "powerpoint": (".pptx", ".ppt")}
    text, note = None, ""
    if ext in (".docx", ".docm"):
        text = _docx_text(path)
    elif ext in (".xlsx", ".xlsm"):
        text = _xlsx_text(path, sheet)
    elif ext == ".pptx":
        text = _pptx_text(path)
    elif ext == ".pdf":
        text = _pdf_text(path)
        if text is None:
            return {"error": "PDF 文字提取不可用: 本机没有 pypdf",
                    "hint": "pip install pypdf 后重试; 或让用户把 PDF 另存为 .docx 再读"
                            "(Word/Excel/PPT 不需要任何额外依赖, 直接可读)"}
    elif ext in (".doc", ".xls", ".ppt", ".rtf"):
        name = next(k for k, v in kind.items() if ext in v)
        text, err = _com_text(path, name)
        if err:
            return {"error": err, "hint": "老格式需要本机 Office/WPS; 也可让用户另存为 docx/xlsx/pptx"}
        note = "老二进制格式: 已用本机 Office/WPS 只读打开"
    elif ext in _TEXT_EXT:
        text = open(path, encoding="utf-8", errors="replace").read()
    else:
        return {"error": "不支持的文档类型: %s" % (ext or "(无扩展名)"),
                "hint": "文本文件用 file_read; 支持 docx/xlsx/pptx/pdf/doc/xls/ppt"}
    text = (text or "").strip()
    if not text:
        return {"path": path, "kind": ext.lstrip("."), "chars": 0,
                "hint": "未取到文字, 不要用更大的 max_chars 重试",
                "text": "(未提取到文字: 可能是扫描件/图片型文档, 或内容为空)",
                "note": note or "如需图片内容, 请让用户提供截图或文字"}
    total = len(text)
    head = {"path": path, "kind": ext.lstrip("."), "chars": total}

    # ---- 章节索引: 大文档先拿骨架, 而不是从头一页页硬读 ----
    if outline:
        secs = _outline(text)
        hint = ("章节索引共%d条(每行: 字符位置<TAB>标题; 前部可能是目录)。"
                "用 offset=<pos> 精读某章, 或用 find='关键词' 定位。"
                "**回答用户问题信息够了就直接作答, 不必读完全文**。" % len(secs))
        if not secs:
            hint += " 本文档没有编号章节, 改用 find='关键词' 或 offset 分段浏览。"
        return {**head, "outline": True, "sections": len(secs), "hint": hint,
                "text": "\n".join("%d\t%s" % (s["pos"], s["title"]) for s in secs)}

    # ---- 关键词定位: 一次命中就能把"第40页的3.1.2"这类深章节送到模型面前 ----
    if find:
        kws = [k.strip() for k in re.split(r"[|,，]", find) if k.strip()][:4]
        low = text.lower()
        hits = []
        for kw in kws:
            # 目录里每个章节名都会出现一次; 只取目录命中会把正文位置全挤掉。
            # 优先跳到正文区(前1500字通常是目录), 每个关键词给"首个+末个"正文命中。
            positions, start = [], 0
            while len(positions) < 40:
                p = low.find(kw.lower(), start)
                if p < 0:
                    break
                positions.append(p)
                start = p + len(kw)
            body = [p for p in positions if p >= 1500] or positions
            picked = sorted(set(body[:1] + body[-1:]))
            hits.extend((p, kw) for p in picked)
        hits = sorted(set(hits))[:FIND_MAX_HITS]
        if not hits:
            return {**head, "offset": 0, "find": find, "truncated": False,
                    "hint": "未找到关键词 %r; 换个词, 或用 offset 分页浏览全文(每段最多%d字)"
                            % (find, max_chars),
                    "text": text[:1000] + "\n...[仅显示开头1000字, 用 offset 翻页]"}
        parts, matches = [], []
        for p, kw in hits:
            a = max(0, p - FIND_WINDOW[0])
            b = min(total, p + len(kw) + FIND_WINDOW[1])
            parts.append("--- 命中 %r @ 第%d字 (用 offset=%d 精读) ---\n%s"
                         % (kw, p, a, text[a:b]))
            matches.append({"keyword": kw, "pos": p, "offset": a})
        out = {**head, "offset": 0, "find": find, "truncated": False,
               "matches": matches,
               "hint": "命中%d处(matches 给出每处在全文中第几字)。用 offset=<pos> 读取该处完整章节; "
                       "回答用户问题时, 已取得的信息够了就直接作答, 不必读完全文。" % len(matches),
               "text": "\n\n".join(parts)}
        if note:
            out["note"] = note
        return out

    # ---- 顺序翻页: 元信息全部排在 text 前, 保证被引擎截断时仍看得见 next_offset ----
    if offset >= total:
        return {**head, "offset": offset, "next_offset": None, "truncated": False,
                "hint": "offset=%d 已超出全文长度%d字, 请从0开始, 或用 find='关键词'定位"
                        % (offset, total),
                "text": ""}
    chunk = text[offset:offset + max_chars]
    end = offset + len(chunk)
    next_off = end if end < total else None
    hint = ("本段第%d~%d字 / 全文%d字。" % (offset, end, total))
    if next_off is not None:
        hint += ("后面还有内容: 继续读请再调 doc_read 传 offset=%d; "
                 "要定位某章节用 find='关键词'。"
                 "**不要反复加大 max_chars 重读本段, 那只会得到同样的开头**。" % next_off)
    else:
        hint += "已读到全文末尾。"
    out = {**head, "offset": offset, "next_offset": next_off,
           "truncated": next_off is not None, "hint": hint, "text": chunk}
    if note:
        out["note"] = note
    return out


def configure(cfg=None):
    """跟随「连接器 → 文档处理」开关: 关闭时 doc_read 也从模型视野移除。"""
    from . import register_tool, unregister_tool
    import caps as _caps
    conn = (cfg or {}).get("connectors") if isinstance(cfg or {}, dict) else None
    on = True if not isinstance(conn, dict) else conn.get("office", True) is not False
    _STATE["on"] = on
    if on:
        register_tool(SCHEMA_READ, read, capabilities=_caps.STATIC_CAPABILITIES.get("doc_read"))
    else:
        unregister_tool("doc_read")
