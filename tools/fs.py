# -*- coding: utf-8 -*-
"""通用文件系统工具(OpenCode式): 目录浏览 / glob匹配 / 内容检索 / 精确编辑
只读三件套默认开放(研判分析本地告警导出/日志也常用); file_edit是写操作, 仅可执行模式+ask弹窗。
fs_grep 逐文件过 file_read 权限规则, 敏感路径(.ssh/凭据等)自动跳过。
"""
import os
import re
import time
from . import permissions as _perm

MAX_SCAN_FILES = 2000     # grep/glob 单次扫描文件上限(防大目录拖死)
MAX_FILE_BYTES = 2 * 1024 * 1024   # grep 单文件大小上限
BINARY_SNIFF = b"\x00"
DOC_MAX_TEXT = 400000     # 文档解包后最多检索的字符数
DOC_SCAN_LIMIT = 40       # 单次 grep 最多解包几个文档(解包慢, 防拖死)


def configure(cfg):
    """接口对齐(本模块无外部配置)"""
    pass


def _expand(path):
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path or ".")))


# ---------------- 工具1: 目录浏览 ----------------

SCHEMA_LS = {
    "type": "function",
    "function": {
        "name": "fs_ls",
        "description": "列目录内容(支持递归层数)。找告警导出文件/看项目结构/确认文件位置时用。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径, 默认当前目录"},
                "depth": {"type": "number", "description": "递归层数, 默认1(仅当前层)"},
                "limit": {"type": "number", "description": "返回条目上限, 默认200"},
            },
        },
    },
}


def _fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.0f%s" % (n, unit)
        n /= 1024.0
    return "%.1fTB" % n


def fs_ls(path=".", depth=1, limit=200):
    root = _expand(path)
    if not os.path.exists(root):
        return {"error": "路径不存在: %s" % root}
    if not os.path.isdir(root):
        st = os.stat(root)
        return {"entries": [{"name": root, "type": "file", "size": _fmt_size(st.st_size),
                             "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))}]}
    rows, truncated = [], False
    try:
        depth = max(1, min(int(depth), 6))
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        depth, limit = 1, 200
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        cur_depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        if cur_depth >= depth:
            dirnames[:] = []
        for d in sorted(dirnames):
            if len(rows) >= limit:
                truncated = True
                break
            rows.append({"name": os.path.join(dirpath, d)[len(root):].lstrip(os.sep) or d,
                         "type": "dir"})
        for f in sorted(filenames):
            if len(rows) >= limit:
                truncated = True
                break
            fp = os.path.join(dirpath, f)
            try:
                st = os.stat(fp)
                rows.append({"name": os.path.join(dirpath, f)[len(root):].lstrip(os.sep),
                             "type": "file", "size": _fmt_size(st.st_size),
                             "mtime": time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime))})
            except OSError:
                continue
        if truncated:
            break
    out = {"path": root, "entries": rows}
    if truncated:
        out["note"] = "结果已达上限%d条, 可加depth/换子路径细分" % limit
    return out


# ---------------- 工具2: glob 匹配 ----------------

SCHEMA_GLOB = {
    "type": "function",
    "function": {
        "name": "fs_glob",
        "description": "按通配符找文件(**跨目录递归), 如'logs/*.log'或'**/*.pcap'。找文件位置用。",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "通配符, 支持**/*/?/[]"},
                "path": {"type": "string", "description": "基准目录, 默认当前目录"},
                "limit": {"type": "number", "description": "返回条数上限, 默认100"},
            },
        },
        "required": ["pattern"],
    },
}


def fs_glob(pattern, path=".", limit=100):
    import glob as _g
    base = _expand(path)
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 100
    hits = _g.glob(os.path.join(base, pattern), recursive=True)
    hits = [h for h in hits if os.path.isfile(h) or os.path.isdir(h)]
    hits.sort(key=lambda h: -os.path.getmtime(h) if os.path.isfile(h) else 0)
    out = [{"name": os.path.relpath(h, base), "type": "dir" if os.path.isdir(h) else "file",
            "mtime": time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(h)))}
           for h in hits[:limit]]
    return {"base": base, "matched": len(hits), "entries": out,
            "note": "按修改时间倒序" if len(hits) > limit else None}


# ---------------- 工具3: 内容检索 ----------------

SCHEMA_GREP = {
    "type": "function",
    "function": {
        "name": "fs_grep",
        "description": "正则检索文件内容(类ripgrep): 在目录或单个文件里找匹配行, 返回文件/行号/字符位置。"
                       "**Word/Excel/PPT/PDF 也能直接搜**(自动解包取文字, 不再当二进制跳过): "
                       "命中给出 pos(字符偏移), 用 doc_read(offset=pos) 精读该处完整章节——"
                       "这是分析大文档最快的路径, 不要全量重复读。"
                       "敏感路径(.ssh/凭据等)被file_read权限规则拒绝的自动跳过。",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则表达式"},
                "path": {"type": "string", "description": "目录或单文件, 默认当前目录"},
                "glob_pat": {"type": "string", "description": "文件名通配过滤, 默认*(全部)"},
                "ignore_case": {"type": "boolean", "description": "忽略大小写, 默认false"},
                "context": {"type": "number", "description": "命中行前后各带几行上下文, 默认0, 上限3"},
                "limit": {"type": "number", "description": "匹配行数上限, 默认50"},
            },
        },
        "required": ["pattern"],
    },
}


_DOC_EXT = (".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pdf")


def _doc_lines(fp):
    """Office/PDF 解包成文本行(复用 docs 的纯标准库抽取), 失败返回 None。
    docx/xlsx/pptx 是 zip 包, 必须走这里才搜得到——按二进制读永远 0 命中。"""
    from . import docs as _docs
    ext = os.path.splitext(fp)[1].lower()
    try:
        if ext in (".docx", ".docm"):
            t = _docs._docx_text(fp)
        elif ext in (".xlsx", ".xlsm"):
            t = _docs._xlsx_text(fp)
        elif ext == ".pptx":
            t = _docs._pptx_text(fp)
        elif ext == ".pdf":
            t = _docs._pdf_text(fp)
        else:
            return None
    except Exception:
        return None
    return t[:DOC_MAX_TEXT].splitlines() if t else None


def _read_lines(fp):
    """返回文本行列表; 二进制/读不了返回 None。文档走解包, 普通文本按行读。"""
    if os.path.splitext(fp)[1].lower() in _DOC_EXT:
        return _doc_lines(fp)
    try:
        with open(fp, "rb") as f:
            if BINARY_SNIFF in f.read(1024):
                return None
        with open(fp, encoding="utf-8", errors="replace") as f:
            return [l.rstrip("\r\n") for l in f]
    except (OSError, UnicodeError):
        return None


def fs_grep(pattern, path=".", glob_pat="*", ignore_case=False, limit=50,
            context=0, **_ignored):
    try:
        rx = re.compile(str(pattern), re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        return {"error": "正则非法: %s" % e}
    try:
        limit = max(1, min(int(limit), 200))
        ctx = max(0, min(int(context), 3))
    except (TypeError, ValueError):
        limit, ctx = 50, 0
    root = _expand(path)
    targets = []
    if os.path.isfile(root):
        targets = [root]
    else:
        import fnmatch
        for dirpath, dirnames, filenames in os.walk(root):
            if len(targets) >= MAX_SCAN_FILES:
                break
            for f in filenames:
                if not fnmatch.fnmatch(f, glob_pat or "*"):
                    continue
                fp = os.path.join(dirpath, f)
                is_doc = os.path.splitext(f)[1].lower() in _DOC_EXT
                try:
                    if not is_doc and os.path.getsize(fp) > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                targets.append(fp)
                if len(targets) >= MAX_SCAN_FILES:
                    break
    matches, skipped, docs_scanned, hit_files = [], 0, 0, set()
    for fp in targets:
        if len(matches) >= limit:
            break
        if _perm.check("file_read", fp) == "deny":
            skipped += 1
            continue
        is_doc = os.path.splitext(fp)[1].lower() in _DOC_EXT
        if is_doc:
            if docs_scanned >= DOC_SCAN_LIMIT:
                continue
            docs_scanned += 1
        lines = _read_lines(fp)
        if lines is None:
            continue
        offsets, pos = [], 0
        for ln in lines:
            offsets.append(pos)
            pos += len(ln) + 1
        for i, line in enumerate(lines, 1):
            if not rx.search(line):
                continue
            hit_files.add(fp)
            row = {"file": fp, "line": i, "pos": offsets[i - 1],
                   "text": line.rstrip()[:300]}
            if ctx:
                row["context"] = [l.rstrip()[:200] for l in
                                  lines[max(0, i - 1 - ctx):i - 1] +
                                  lines[i:min(len(lines), i + ctx)]]
            if is_doc:
                row["hint"] = "用 doc_read(offset=%d) 精读该处" % offsets[i - 1]
            matches.append(row)
            if len(matches) >= limit:
                break
    out = {"matched": len(matches), "files_matched": len(hit_files),
           "matches": matches}
    if skipped:
        out["skipped_deny"] = skipped
    if not matches:
        out["note"] = ("无匹配。docx/xlsx/pptx 已自动解包检索; PDF 需本机 pypdf; "
                       "敏感路径跳过 %d 个; 二进制与 >2MB 文本自动跳过" % skipped)
    else:
        out["hint"] = ("命中 %d 处。文档命中用 doc_read(offset=<pos>) 精读, "
                       "信息够了就直接作答, 不要为了读全文反复翻页。" % len(matches))
    return out


# ---------------- 工具4: 精确编辑(写, 仅可执行模式) ----------------

SCHEMA_EDIT = {
    "type": "function",
    "function": {
        "name": "file_edit",
        "description": "[仅可执行模式] 精确编辑文件: 用old_string定位唯一一处并替换为new_string"
                       "(OpenCode edit语义)。old必须在文件中唯一, 不唯一时报错并列出上下文供细化。"
                       "会弹权限确认窗。非UTF-8文本文件不支持(防乱码)。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "old_string": {"type": "string", "description": "要被替换的原文(须唯一)"},
                "new_string": {"type": "string", "description": "替换后的内容"},
            },
        },
        "required": ["path", "old_string", "new_string"],
    },
}


def file_edit(path, old_string, new_string):
    p = os.path.expandvars(os.path.expanduser(str(path)))
    reason, ok = _perm.gate("file_write", p, "编辑文件: %s\n替换 %d字符 → %d字符"
                            % (p, len(str(old_string)), len(str(new_string))))
    if not ok:
        return {"refused": True, "reason": reason}
    if not os.path.exists(p):
        return {"error": "文件不存在: %s (新建请用file_write)" % p}
    try:
        content = open(p, encoding="utf-8").read()
    except UnicodeDecodeError:
        return {"error": "非UTF-8文本文件暂不支持编辑(防编码损坏), 可用file_write整体重写"}
    except OSError as e:
        return {"error": "读取失败: %s" % e}
    old, new = str(old_string), str(new_string)
    n = content.count(old)
    if n == 0:
        return {"error": "old_string在文件中未找到。请核对原文(含缩进/空行)"}
    if n > 1:
        pos, ctxs = content.find(old), []
        while pos != -1 and len(ctxs) < 5:
            ctxs.append("L%s: ...%s..." % (content[:pos].count("\n") + 1,
                                           content[max(0, pos - 40):pos + 60].replace("\n", "\\n")))
            pos = content.find(old, pos + 1)
        return {"error": "old_string出现%d次, 须唯一。可加更多上下文行。出现位置:\n%s"
                % (n, "\n".join(ctxs))}
    new_content = content.replace(old, new, 1)
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_content)
    except OSError as e:
        return {"error": "写入失败: %s" % e}
    idx = new_content.find(new)
    line_no = new_content[:idx].count("\n") + 1
    preview = [l for l in new_content.splitlines()[max(0, line_no - 3):line_no + 2]]
    return {"success": True, "path": p, "edited_line": line_no,
            "preview": "\n".join(preview),
            "note": "已写入; 编辑动作留痕在会话日志"}
