# -*- coding: utf-8 -*-
"""Skills: 用户自定义技能(全局 + 项目级), 注入 system prompt。
- 全局: %USERPROFILE%\\.ezcode\\skills\\*.md   (用户所有环境共享)
- 项目级: <程序目录>\\skills\\*.md            (随部署分发, 优先级高于全局同名)
文件格式(markdown + frontmatter):
    ---
    name: 技能名
    description: 一句话说明
    enabled: true
    ---
    技能正文(提示词内容, 会整体注入系统提示词的"技能注入"章节)
公开产品形态: 设置中心可新建/编辑/启停/删除, 保存后下一轮即生效。"""
import os
import re

_GLOBAL_DIR = os.path.join(os.path.expanduser("~"), ".ezcode", "skills")
_MAX_SKILLS = 20            # 注入上限, 防撑爆上下文
_MAX_SKILL_CHARS = 4000     # 单技能正文上限

_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)


def _project_dir():
    """ROOT 与 engine 一致(打包版=_MEIPASS, 源码版=项目根)。"""
    try:
        import sys
        if getattr(sys, "frozen", False):
            root = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        else:
            root = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        root = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, "skills")


def _scope_dir(scope):
    return _project_dir() if scope == "project" else _GLOBAL_DIR


def parse_skill(path):
    """解析单个技能文件。损坏文件(无frontmatter)按整文件正文处理, 默认启用。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError as e:
        return {"error": str(e)}
    meta, body = {}, raw
    m = _FRONT.match(raw)
    if m:
        body = raw[m.end():]
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip().lower()] = v.strip()
    return {
        "name": meta.get("name") or os.path.splitext(os.path.basename(path))[0],
        "description": meta.get("description") or "",
        "enabled": meta.get("enabled", "true").strip().lower() not in ("false", "0", "no"),
        "content": body.strip(),
        "path": path,
        "size": len(body),
    }


def list_skills():
    """返回技能清单: 内置(随程序分发的 skills/) + 用户(全局)。
    覆盖语义按**文件名**: 用户全局同名文件覆盖内置同名文件——内置技能是默认值,
    用户想改就新建同名文件(设置中心里对内置技能点"编辑"会自动落到全局), 升级不丢。"""
    out = []
    for scope in ("project", "global"):        # project=内置, 先扫; global=用户, 后扫并覆盖
        d = _scope_dir(scope)
        try:
            files = sorted(f for f in os.listdir(d) if f.endswith(".md"))
        except OSError:
            files = []
        for fn in files:
            sk = parse_skill(os.path.join(d, fn))
            if "error" in sk:
                continue
            builtin = (scope == "project")
            sk["scope"] = "builtin" if builtin else "global"
            sk["builtin"] = builtin
            sk["file"] = fn
            if not builtin:
                # 用户同名技能覆盖内置
                out = [s for s in out if not (s.get("builtin") and s["file"] == fn)]
            out.append(sk)
    return out


def prompt_block():
    """启用的技能 → system prompt 注入文本(无技能返回空串)。"""
    skills = [s for s in list_skills() if s["enabled"] and s["content"]][:_MAX_SKILLS]
    if not skills:
        return ""
    parts = ["", "# 技能注入(按内容遵循)", ""]
    for s in skills:
        # 不标注"内置/自定义": 技能来源对模型无意义, 只按内容执行
        head = "## %s" % s["name"]
        if s["description"]:
            head += " — %s" % s["description"]
        parts.append(head)
        parts.append(s["content"][:_MAX_SKILL_CHARS])
        if len(s["content"]) > _MAX_SKILL_CHARS:
            parts.append("...[技能过长已截断, 请精简技能文件]")
        parts.append("")
    return "\n".join(parts)


def save(scope, filename, name, description, enabled, content):
    """新建/覆盖一个技能文件(原子写)。"""
    if not re.fullmatch(r"[A-Za-z0-9_\-\u4e00-\u9fff.]+", str(filename or "")) \
            or ".." in str(filename):
        return {"error": "文件名只能包含中英文/数字/下划线/连字符"}
    d = _scope_dir(scope)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError as e:
        return {"error": "创建技能目录失败: %s" % e}
    path = os.path.join(d, str(filename) if str(filename).endswith(".md") else str(filename) + ".md")
    meta = "---\nname: %s\ndescription: %s\nenabled: %s\n---\n" % (
        str(name or os.path.splitext(str(filename))[0]).replace("\n", " "),
        str(description or "").replace("\n", " "),
        "true" if enabled else "false")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(meta + str(content or "") + "\n")
        os.replace(tmp, path)
    except OSError as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return {"error": "写入技能失败: %s" % e}
    return {"ok": True, "path": path}


def delete(scope, filename):
    path = os.path.join(_scope_dir(scope), str(filename))
    if ".." in str(filename) or not os.path.basename(path).endswith(".md"):
        return {"error": "非法文件名"}
    try:
        os.remove(path)
    except OSError as e:
        return {"error": "删除失败: %s" % e}
    return {"ok": True}
