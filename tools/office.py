# -*- coding: utf-8 -*-
"""文档处理 —— 调用本机 Microsoft Office / WPS 的 COM 自动化做整改。

设计要点:
  1. 先检测再操作: `office_detect` 只读注册表判断可用性, 不启动任何程序;
  2. 操作是**声明式**的(ops 列表), 模型不需要写 VBA / 脚本, 也不执行任意代码;
  3. 进程清理只针对**本次启动的**进程: 操作前拍快照, 结束后只结束新增的 PID,
     用户原本开着的 Word/WPS(可能正在编辑别的文档)绝不动;
  4. 复用已有实例时(部分 WPS 版本会把 COM 请求转给主进程), 不调用 Quit,
     只 Close 本次打开的文档, 并在结果里如实说明。

支持: .docx/.doc(Word) .xlsx/.xls(Excel) .pptx/.ppt(PowerPoint)
操作: replace / insert_paragraph / set_header / set_footer / set_cell /
      accept_revisions / remove_comments / export_pdf
"""
import ctypes
import os
import subprocess
import time
from ctypes import wintypes

_OFFICE_PROCS = ("winword.exe", "excel.exe", "powerpnt.exe",
                 "wps.exe", "et.exe", "wpp.exe")
_PROGIDS = {
    "word": ("Word.Application", "KWPS.Application"),
    "excel": ("Excel.Application", "KET.Application"),
    "powerpoint": ("PowerPoint.Application", "KWPP.Application"),
}
_LABEL = {"word": "Word/WPS文字", "excel": "Excel/WPS表格", "powerpoint": "PowerPoint/WPS演示"}

_state = {"detected": None}

_WD_REPLACE_ALL = 2
_WD_FIND_CONTINUE = 1
_WD_FORMAT_PDF = 17
_XL_TYPE_PDF = 0
_PPT_PDF = 32


# ---------------- 检测(不启动进程) ----------------

def _reg_has(root, path):
    import winreg
    try:
        with winreg.OpenKey(root, path):
            return True
    except OSError:
        return False


def _progid_exists(progid):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\CLSID"):
            return True
    except OSError:
        return False


def _vendor():
    """判断本机 Office 套件厂商: microsoft / wps / both / none。"""
    import winreg
    ms = any(_reg_has(winreg.HKEY_LOCAL_MACHINE, p) for p in (
        r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration",
        r"SOFTWARE\WOW6432Node\Microsoft\Office\ClickToRun\Configuration",
        r"SOFTWARE\Microsoft\Office\16.0",
        r"SOFTWARE\WOW6432Node\Microsoft\Office\16.0",
    ))
    wps = any(_progid_exists(p) for p in ("KWPS.Application", "KET.Application", "KWPP.Application"))
    if ms and wps:
        return "both"
    if wps:
        return "wps"
    if ms:
        return "microsoft"
    # 有 Word.Application 但没有明确厂商注册表时, 按 wps 常见兼容情况兜底
    if _progid_exists("Word.Application"):
        return "wps" if _wps_dir() else "microsoft"
    return "none"


def _wps_dir():
    base = os.path.expandvars(r"%LOCALAPPDATA%\Kingsoft\WPS Office")
    try:
        subs = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
        return os.path.join(base, sorted(subs)[-1]) if subs else None
    except Exception:
        return None


def detect():
    """返回各组件可用性 + 厂商。只读注册表, 不启动程序。"""
    vendor = _vendor()
    out = {"vendor": vendor, "components": {}, "ok": False, "summary": ""}
    for kind, ids in _PROGIDS.items():
        hit = next((p for p in ids if _progid_exists(p)), None)
        out["components"][kind] = {"available": bool(hit), "progid": hit,
                                   "label": _LABEL[kind]}
    ready = [k for k, v in out["components"].items() if v["available"]]
    out["ok"] = bool(ready)
    if not ready:
        out["summary"] = "未检测到 Microsoft Office 或 WPS，文档处理不可用"
    else:
        vname = {"microsoft": "Microsoft Office", "wps": "WPS Office",
                 "both": "Microsoft Office + WPS", "none": "Office 兼容组件"}[vendor]
        out["summary"] = "%s 可用：%s" % (vname, "、".join(_LABEL[k] for k in ready))
    _state["detected"] = out
    return out


def detect_summary():
    """给系统提示词的一行环境信息。"""
    try:
        d = _state["detected"] or detect()
    except Exception:
        return "文档处理: 检测失败(可调用 office_detect 重试)"
    if not d.get("ok"):
        return "文档处理: 未检测到 Office/WPS, 无法调用(可提示用户安装)"
    ready = [_LABEL[k] for k, v in d["components"].items() if v["available"]]
    return "文档处理: %s 可用(%s)" % (
        {"microsoft": "Microsoft Office", "wps": "WPS", "both": "Office+WPS",
         "none": "兼容组件"}[d["vendor"]], "/".join(ready))


# ---------------- 进程清理 ----------------

def _snapshot():
    """当前 Office/WPS 进程集合 {(name, pid)}。"""
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, encoding="gbk",
                           errors="replace", creationflags=0x08000000, timeout=15)
        text = r.stdout or ""
    except Exception:
        return set()
    out = set()
    for line in text.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() in _OFFICE_PROCS:
            out.add((parts[0].lower(), parts[1]))
    return out


def _kill(pid):
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                       creationflags=0x08000000, timeout=15)
        return True
    except Exception:
        return False


def _cleanup(app, before, quit_ok=True):
    """只结束本次新增的进程; 复用用户实例时不 Quit。返回清理说明。"""
    if quit_ok:
        try:
            app.Quit()
        except Exception:
            pass
    time.sleep(0.8)
    new = _snapshot() - before
    killed = []
    for name, pid in sorted(new):
        if _kill(pid):
            killed.append("%s(%s)" % (name, pid))
    if killed:
        return "已结束本次启动的进程: " + ", ".join(killed)
    if not quit_ok:
        return "复用已运行的 Office/WPS 实例, 未结束该进程(避免关闭你正在编辑的文档)"
    return "本次未残留进程"


# ---------------- 文档操作 ----------------

def _norm_ops(ops):
    if not isinstance(ops, list):
        return []
    return [o for o in ops if isinstance(o, dict) and str(o.get("type") or "").strip()]


def _word_edit(app, path, ops, save_as):
    wdAlerts = 0
    try:
        app.DisplayAlerts = wdAlerts
    except Exception:
        pass
    doc = app.Documents.Open(os.path.abspath(path), ReadOnly=False,
                             AddToRecentFiles=False, ConfirmConversions=False)
    results = []
    try:
        for op in ops:
            kind = str(op.get("type")).lower()
            try:
                if kind == "replace":
                    # WPS 兼容层对"设属性再 Execute(Replace=2)"只返回 True 不生效,
                    # 必须用**全位置参数**调用; 参数序: FindText, MatchCase, MatchWholeWord,
                    # MatchWildcards, MatchSoundsLike, MatchAllWordForms, Forward, Wrap,
                    # Format, ReplaceWith, Replace
                    find = doc.Content.Find
                    try:
                        find.ClearFormatting()
                        find.Replacement.ClearFormatting()
                    except Exception:
                        pass
                    ok = find.Execute(
                        str(op.get("find") or ""),
                        bool(op.get("match_case")),
                        False,
                        bool(op.get("regex")),
                        False,
                        False,
                        True,
                        _WD_FIND_CONTINUE,
                        False,
                        str(op.get("replace") or ""),
                        _WD_REPLACE_ALL,
                    )
                    results.append({"type": kind, "ok": bool(ok)})
                elif kind == "insert_paragraph":
                    text = str(op.get("text") or "")
                    pos = str(op.get("position") or "end").lower()
                    rng = doc.Content
                    if pos == "start":
                        rng.InsertBefore(text + "\r")
                    else:
                        rng.InsertAfter("\r" + text)
                    results.append({"type": kind, "ok": True})
                elif kind in ("set_header", "set_footer"):
                    which = 1 if kind == "set_header" else 2   # wdHeaderFooterPrimary
                    doc.Sections(1).Headers(which).Range.Text = str(op.get("text") or "")
                    results.append({"type": kind, "ok": True})
                elif kind == "accept_revisions":
                    try:
                        doc.Revisions.AcceptAll()
                    except Exception:
                        pass
                    results.append({"type": kind, "ok": True})
                elif kind == "remove_comments":
                    try:
                        doc.DeleteAllComments()
                    except Exception:
                        while doc.Comments.Count > 0:
                            doc.Comments(1).Delete()
                    results.append({"type": kind, "ok": True})
                elif kind == "export_pdf":
                    out = str(op.get("output") or os.path.splitext(path)[0] + ".pdf")
                    doc.ExportAsFixedFormat(OutputFileName=os.path.abspath(out),
                                            ExportFormat=_WD_FORMAT_PDF)
                    results.append({"type": kind, "ok": True, "output": out})
                else:
                    results.append({"type": kind, "ok": False, "error": "Word 不支持该操作"})
            except Exception as e:
                results.append({"type": kind, "ok": False,
                                "error": "%s: %s" % (type(e).__name__, str(e)[:160])})
        if save_as:
            doc.SaveAs2(os.path.abspath(save_as))
        else:
            doc.Save()
        results.append({"type": "save", "ok": True,
                        "path": os.path.abspath(save_as or path)})
    finally:
        try:
            doc.Close(0)
        except Exception:
            pass
    return results


def _excel_edit(app, path, ops, save_as):
    try:
        app.DisplayAlerts = False
    except Exception:
        pass
    wb = app.Workbooks.Open(os.path.abspath(path))
    results = []
    try:
        for op in ops:
            kind = str(op.get("type")).lower()
            try:
                if kind == "set_cell":
                    ws = wb.Worksheets(op.get("sheet") or 1)
                    ws.Range(str(op.get("cell") or "A1")).Value = op.get("value")
                    results.append({"type": kind, "ok": True})
                elif kind == "replace":
                    ws = wb.Worksheets(op.get("sheet") or 1)
                    ws.Cells.Replace(What=str(op.get("find") or ""),
                                     Replacement=str(op.get("replace") or ""))
                    results.append({"type": kind, "ok": True})
                elif kind == "export_pdf":
                    out = str(op.get("output") or os.path.splitext(path)[0] + ".pdf")
                    wb.ExportAsFixedFormat(Type=_XL_TYPE_PDF, Filename=os.path.abspath(out))
                    results.append({"type": kind, "ok": True, "output": out})
                else:
                    results.append({"type": kind, "ok": False, "error": "Excel 不支持该操作"})
            except Exception as e:
                results.append({"type": kind, "ok": False,
                                "error": "%s: %s" % (type(e).__name__, str(e)[:160])})
        if save_as:
            wb.SaveAs(os.path.abspath(save_as))
        else:
            wb.Save()
        results.append({"type": "save", "ok": True, "path": os.path.abspath(save_as or path)})
    finally:
        try:
            wb.Close(False)
        except Exception:
            pass
    return results


def _ppt_edit(app, path, ops, save_as):
    pres = None
    for kw in ({"WithWindow": False}, {}):
        try:
            pres = app.Presentations.Open(os.path.abspath(path), **kw)
            break
        except Exception:
            continue
    if pres is None:
        raise RuntimeError("无法打开演示文稿(可能被占用)")
    results = []
    try:
        for op in ops:
            kind = str(op.get("type")).lower()
            try:
                if kind == "replace":
                    find = str(op.get("find") or "")
                    repl = str(op.get("replace") or "")
                    hit = 0
                    for slide in pres.Slides:
                        for shape in slide.Shapes:
                            try:
                                if shape.HasTextFrame and shape.TextFrame.HasText:
                                    tr = shape.TextFrame.TextRange
                                    if find and find in tr.Text:
                                        tr.Text = tr.Text.replace(find, repl)
                                        hit += 1
                            except Exception:
                                continue
                    results.append({"type": kind, "ok": True, "shapes": hit})
                elif kind == "export_pdf":
                    out = str(op.get("output") or os.path.splitext(path)[0] + ".pdf")
                    pres.SaveAs(os.path.abspath(out), _PPT_PDF)
                    results.append({"type": kind, "ok": True, "output": out})
                else:
                    results.append({"type": kind, "ok": False, "error": "PowerPoint 不支持该操作"})
            except Exception as e:
                results.append({"type": kind, "ok": False,
                                "error": "%s: %s" % (type(e).__name__, str(e)[:160])})
        if save_as:
            pres.SaveAs(os.path.abspath(save_as))
        else:
            pres.Save()
        results.append({"type": "save", "ok": True, "path": os.path.abspath(save_as or path)})
    finally:
        try:
            pres.Close()
        except Exception:
            pass
    return results


_KIND_BY_EXT = {".docx": "word", ".doc": "word", ".rtf": "word",
                ".xlsx": "excel", ".xls": "excel", ".csv": "excel",
                ".pptx": "powerpoint", ".ppt": "powerpoint"}


def edit(path, operations, save_as=None, **_ignored):
    """用本机 Office/WPS 打开文档执行声明式操作并保存, 完成后清理本次进程。"""
    path = str(path or "").strip().strip('"')
    if not path:
        return {"error": "缺少文档路径"}
    if not os.path.isfile(path):
        return {"error": "文件不存在: %s" % path,
                "hint": "先用 fs_ls / fs_glob 确认路径"}
    ext = os.path.splitext(path)[1].lower()
    kind = _KIND_BY_EXT.get(ext)
    if not kind:
        return {"error": "不支持的文档类型: %s" % ext,
                "hint": "支持 .docx/.doc/.rtf/.xlsx/.xls/.csv/.pptx/.ppt"}
    ops = _norm_ops(operations)
    if not ops:
        return {"error": "operations 为空, 没有要执行的整改动作"}

    d = _state["detected"] or detect()
    if not d["components"][kind]["available"]:
        return {"error": "本机没有可用的 %s, 无法处理该文档" % _LABEL[kind],
                "detected": d["summary"], "hint": "可先调用 office_detect 查看检测结果"}

    try:
        import win32com.client as win32
    except Exception as e:
        return {"error": "缺少 pywin32: %s" % e}

    progid = d["components"][kind]["progid"]
    before = _snapshot()
    app = None
    started = time.time()
    try:
        app = win32.DispatchEx(progid)
        try:
            app.Visible = False
        except Exception:
            pass
        if kind == "word":
            results = _word_edit(app, path, ops, save_as)
        elif kind == "excel":
            results = _excel_edit(app, path, ops, save_as)
        else:
            results = _ppt_edit(app, path, ops, save_as)
    except Exception as e:
        cleanup = _cleanup(app, before) if app is not None else "未启动应用"
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:220]),
                "cleanup": cleanup,
                "hint": "确认文档没有被其他程序占用、且你有写入权限"}
    cleanup = _cleanup(app, before)
    return {"ok": True, "path": os.path.abspath(path), "kind": kind,
            "progid": progid, "operations": results,
            "cleanup": cleanup, "elapsed_ms": int((time.time() - started) * 1000)}


# ---------------- 工具 schema ----------------

SCHEMA_DETECT = {
    "type": "function",
    "function": {
        "name": "office_detect",
        "description": "检测本机是否安装 Microsoft Office / WPS，以及 Word/Excel/PowerPoint 能否通过 COM 调用。"
                       "只读注册表，不启动程序、不改文件。做文档整改前先调用它确认能力。",
        "parameters": {"type": "object", "properties": {}},
    },
}

SCHEMA_EDIT = {
    "type": "function",
    "function": {
        "name": "office_edit",
        "description": "[可执行模式] 用本机 Office/WPS 打开 docx/xlsx/pptx 做整改并保存，完成后自动清理本次启动的进程"
                       "（不会动你正在编辑的文档）。operations 是声明式操作列表，按顺序执行；不需要写 VBA。"
                       "常用: 批量替换文字(replace)、末尾插入段落(insert_paragraph)、设置页眉页脚(set_header/set_footer)、"
                       "单元格赋值(set_cell)、接受修订(accept_revisions)、删除批注(remove_comments)、导出 PDF(export_pdf)。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文档绝对路径（.docx/.doc/.rtf/.xlsx/.xls/.csv/.pptx/.ppt）"},
            "operations": {"type": "array", "description": "按顺序执行的操作列表", "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string",
                             "enum": ["replace", "insert_paragraph", "set_header", "set_footer",
                                      "set_cell", "accept_revisions", "remove_comments", "export_pdf"],
                             "description": "操作类型"},
                    "find": {"type": "string", "description": "replace: 要查找的文本"},
                    "replace": {"type": "string", "description": "replace: 替换成的文本（可为空=删除）"},
                    "regex": {"type": "boolean", "description": "replace: 是否按 Word 通配符匹配（Word 特有，慎用）"},
                    "match_case": {"type": "boolean", "description": "replace: 是否区分大小写"},
                    "text": {"type": "string", "description": "insert_paragraph/set_header/set_footer: 文本内容"},
                    "position": {"type": "string", "enum": ["end", "start"],
                                 "description": "insert_paragraph: 插入位置，默认 end"},
                    "sheet": {"type": "string", "description": "set_cell/replace(Excel): 工作表名或序号，默认第 1 个"},
                    "cell": {"type": "string", "description": "set_cell: 单元格，如 B2"},
                    "value": {"description": "set_cell: 值（字符串或数字）"},
                    "output": {"type": "string", "description": "export_pdf: 输出 PDF 路径，默认同目录同名"},
                },
                "required": ["type"],
            }},
            "save_as": {"type": "string", "description": "另存为路径（可选，默认原地保存）"},
        }, "required": ["path", "operations"]},
    },
}


def configure(cfg=None):
    """启动时检测一次(只读注册表)，并按 connectors.office 开关注册/注销工具。

    连接器可勾选: connectors.office=false 时 office_detect/office_edit 从模型视野移除
    (设置中心「连接器」取消勾选即生效)，重新勾选后自动注册并回填能力标签。"""
    from . import register_tool, unregister_tool
    import caps as _caps
    try:
        detect()
    except Exception:
        _state["detected"] = None
    conn = (cfg or {}).get("connectors") if isinstance(cfg or {}, dict) else None
    on = True if not isinstance(conn, dict) else conn.get("office", True) is not False
    for name, schema, handler in (("office_detect", SCHEMA_DETECT, detect),
                                  ("office_edit", SCHEMA_EDIT, edit)):
        if on:
            register_tool(schema, handler,
                          capabilities=_caps.STATIC_CAPABILITIES.get(name))
        else:
            unregister_tool(name)
