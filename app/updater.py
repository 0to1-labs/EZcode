# -*- coding: utf-8 -*-
"""EZcode 自动更新内核(纯标准库, 零新依赖)

设计要点
--------
* 多源并行: update.sources[] 每条独立拉取, 任一成功即可, 结果取最高版本。
  现在默认只启用 GitHub Releases; 以后国内服务器建好, 把 static 那条
  enabled 打开并填 url, 两条就会同时检查 —— 新增源不需要改代码。
* 统一 manifest(version.json): version/channel/released_at/min_version/force/
  notes{zh,en}/package{url,sha256,size}。GitHub 源可以直接读 Release 的
  tag_name + assets, 也支持在 Release 里附一份 version.json 提供 sha256/说明。
* 静态源支持 https:// / 国内服务器 / UNC 内网共享 / 本地文件(file://),
  内网部署不需要公网。
* 所有网络调用带超时且吞异常: 检查失败静默降级, 绝不阻塞启动、不弹错误框。
* 下载包强制 sha256 校验(manifest 提供时); 校验不过直接删除, 不交给安装器。
"""
import concurrent.futures
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

if not getattr(sys, "frozen", False):
    _PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _PKG_ROOT not in sys.path:
        sys.path.insert(0, _PKG_ROOT)

try:
    import ezcode_version as V
    APP_VERSION = V.APP_VERSION
    DEFAULT_GITHUB_REPO = V.GITHUB_REPO
    DEFAULT_STATIC_URL = V.STATIC_UPDATE_URL
except Exception:                                  # 打包/源码路径异常时兜底
    APP_VERSION = "1.0.48"
    DEFAULT_GITHUB_REPO = ""
    DEFAULT_STATIC_URL = ""

APP_NAME = "EZcode"
UA = "EZcode-Updater/%s" % APP_VERSION
MAX_MANIFEST = 512 * 1024
DEFAULT_TIMEOUT = 6
CHUNK = 256 * 1024

DEFAULT_CONFIG = {
    "enabled": True,
    "check_on_startup": True,
    "channel": "stable",
    "ignored_version": "",
    "timeout": DEFAULT_TIMEOUT,
    "sources": [
        {"type": "github", "name": "GitHub Releases", "enabled": True,
         "repo": DEFAULT_GITHUB_REPO, "api_base": "https://api.github.com",
         "asset": "EZcodeSetup", "token": "", "mirror": ""},
        {"type": "static", "name": "国内更新服务器", "enabled": False,
         "url": DEFAULT_STATIC_URL},
    ],
}

_SOURCE_DEFAULTS = {
    "github": {"type": "github", "name": "GitHub Releases", "enabled": True,
               "repo": DEFAULT_GITHUB_REPO, "api_base": "https://api.github.com",
               "asset": "EZcodeSetup", "token": "", "mirror": ""},
    "static": {"type": "static", "name": "国内更新服务器", "enabled": False,
               "url": DEFAULT_STATIC_URL},
}


# ---------------- 版本 ----------------

def version_tuple(v):
    """'v1.0.48' / '1.0.48-beta2' → (1,0,48); 非数字段按 0 处理, 永不抛异常。"""
    s = str(v or "").strip()
    s = re.sub(r"^[vV]", "", s)
    s = re.split(r"[-+]", s)[0]
    parts = []
    for x in s.split(".")[:4]:
        m = re.match(r"^(\d+)", x.strip())
        parts.append(int(m.group(1)) if m else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(a, b):
    return version_tuple(a) > version_tuple(b)


def normalize_version(v):
    s = re.sub(r"^[vV]", "", str(v or "").strip())
    return s if re.match(r"^\d+(\.\d+)*", s) else ""


# ---------------- 路径 / 配置 ----------------

def _root():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path():
    p = os.path.join(_root(), "devices.json")
    return p if os.path.exists(p) else os.path.join(_root(), "config.json")


def download_dir():
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    try:
        d = os.path.join(base, APP_NAME, "updates")
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        return tempfile.gettempdir()


def _merge_config(user):
    """默认值 + 用户覆盖。sources 是完整列表(设置中心维护两条),
    每条补齐缺省字段; 环境变量可覆盖 GitHub 仓库, 方便批量部署。"""
    c = dict(DEFAULT_CONFIG)
    if isinstance(user, dict):
        for k, v in user.items():
            if k == "sources":
                continue
            if k in c and v is not None:
                c[k] = v
    raw_sources = (user or {}).get("sources") if isinstance(user, dict) else None
    if not isinstance(raw_sources, list) or not raw_sources:
        raw_sources = [dict(s) for s in DEFAULT_CONFIG["sources"]]
    out = []
    for s in raw_sources:
        if not isinstance(s, dict):
            continue
        t = str(s.get("type") or "static").lower()
        d = dict(_SOURCE_DEFAULTS.get(t, _SOURCE_DEFAULTS["static"]))
        d.update(s)
        d["type"] = t
        d["enabled"] = bool(d.get("enabled", True))
        out.append(d)
    c["sources"] = out
    try:
        c["timeout"] = max(2, min(30, int(c.get("timeout") or DEFAULT_TIMEOUT)))
    except Exception:
        c["timeout"] = DEFAULT_TIMEOUT
    env_repo = os.environ.get("EZCODE_GITHUB_REPO", "").strip()
    if env_repo:
        for s in c["sources"]:
            if s["type"] == "github":
                s["repo"] = env_repo
    return c


def load_config(path=None):
    """读 devices.json 的 update 段(读不到就用默认, 不抛异常)。"""
    p = path or config_path()
    raw = {}
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        pass
    return _merge_config(raw.get("update") if isinstance(raw, dict) else None)


def save_config(update, path=None):
    """把设置中心的更新配置写回 devices.json, 原子替换, 保留其它节和 token。

    update 里未出现的敏感字段(如 GitHub token)从原配置继承, 前端不回传也不会丢。
    """
    p = path or config_path()
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {"ok": False, "error": "读取 devices.json 失败"}
    if not isinstance(update, dict):
        return {"ok": False, "error": "更新配置格式错误"}
    old = raw.get("update") if isinstance(raw.get("update"), dict) else {}
    merged = dict(old)
    for k, v in update.items():
        if str(k).startswith("_"):
            continue
        merged[k] = v
    # 源级别合并: 前端只回传可编辑字段, token / api_base 等保留原值
    if isinstance(update.get("sources"), list):
        prev = {}
        for s in (old.get("sources") or []):
            if isinstance(s, dict):
                prev[(s.get("type"), s.get("name"))] = s
        merged["sources"] = []
        for s in update["sources"]:
            if not isinstance(s, dict):
                continue
            base = dict(prev.get((s.get("type"), s.get("name"))) or {})
            base.update(s)
            merged["sources"].append(base)
    raw["update"] = merged
    tmp = p + ".upd.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return {"ok": True, "config": _public_config(_merge_config(merged))}
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return {"ok": False, "error": "写入失败: %s" % e}


def _public_config(c):
    """给前端的编辑态: 不回传 GitHub token。"""
    srcs = []
    for s in c["sources"]:
        d = {k: v for k, v in s.items() if k not in ("token",)}
        srcs.append(d)
    return {"enabled": bool(c.get("enabled", True)),
            "check_on_startup": bool(c.get("check_on_startup", True)),
            "channel": c.get("channel", "stable"),
            "ignored_version": c.get("ignored_version", ""),
            "timeout": c.get("timeout", DEFAULT_TIMEOUT),
            "sources": srcs}


# ---------------- 低层读取 ----------------

def _is_http(url):
    return bool(re.match(r"^https?://", str(url or ""), re.I))


def _local_path(url):
    u = str(url or "")
    if u.lower().startswith("file://"):
        p = urllib.parse.unquote(urllib.parse.urlparse(u).path)
        if re.match(r"^/[A-Za-z]:", p):
            p = p[1:]
        return p.replace("/", os.sep)
    return u


def _read_bytes(url, timeout, headers=None, limit=MAX_MANIFEST):
    """支持 http(s) / file:// / UNC / 本地绝对路径。返回前 limit+1 字节。"""
    u = str(url or "").strip()
    if not u:
        raise ValueError("空地址")
    if not _is_http(u):
        with open(_local_path(u), "rb") as f:
            return f.read(limit + 1)
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(u, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit + 1)


def _read_json(url, timeout, headers=None):
    b = _read_bytes(url, timeout, headers)
    if len(b) > MAX_MANIFEST:
        raise ValueError("清单过大")
    return json.loads(b.decode("utf-8-sig", "replace"))


def _resolve_url(base, url):
    """manifest 里的相对包地址: http 用 urljoin, 本地/UNC 用同目录拼接。"""
    u = str(url or "").strip()
    if not u or _is_http(u) or u.lower().startswith("file://"):
        return u
    if os.path.isabs(u) or u.startswith("\\\\"):
        return u
    b = str(base or "")
    if _is_http(b):
        return urllib.parse.urljoin(b, u)
    return os.path.join(os.path.dirname(_local_path(b)), u)


# ---------------- manifest 归一化 ----------------

def normalize_manifest(m, source_url="", fallback=None):
    """把任意来源的清单收敛成统一结构。缺字段一律用 fallback 补齐, 不抛异常。"""
    f = fallback or {}
    if not isinstance(m, dict):
        m = {}
    pkg = m.get("package") if isinstance(m.get("package"), dict) else {}
    notes = m.get("notes")
    if isinstance(notes, str):
        notes = {"zh": notes, "en": notes}
    elif not isinstance(notes, dict):
        notes = {}
    version = normalize_version(m.get("version") or f.get("version") or "")
    if not version:
        raise ValueError("清单缺少 version")
    url = _resolve_url(source_url, pkg.get("url") or f.get("url") or "")
    if not url:
        raise ValueError("清单缺少 package.url")
    try:
        size = int(pkg.get("size") or f.get("size") or 0)
    except Exception:
        size = 0
    try:
        rollout = max(0, min(100, int(m.get("rollout") if m.get("rollout") is not None else 100)))
    except Exception:
        rollout = 100
    return {
        "version": version,
        "channel": str(m.get("channel") or "stable"),
        "released_at": str(m.get("released_at") or f.get("released_at") or ""),
        "min_version": str(m.get("min_version") or ""),
        "force": bool(m.get("force", False)),
        "rollout": rollout,
        "notes": {"zh": str(notes.get("zh") or f.get("notes") or ""),
                  "en": str(notes.get("en") or notes.get("zh") or f.get("notes") or "")},
        "package": {"url": url,
                    "sha256": str(pkg.get("sha256") or "").strip(),
                    "size": size},
    }


def _gh_assets(data, asset_hint):
    """从 Release assets 里挑安装包和可选 version.json。"""
    assets = [a for a in (data.get("assets") or []) if isinstance(a, dict)]
    want = str(asset_hint or "EZcodeSetup").lower()
    exes = [a for a in assets if str(a.get("name") or "").lower().endswith(".exe")]
    setup = next((a for a in exes if want in str(a.get("name") or "").lower()), None)
    if setup is None and exes:
        setup = exes[0]
    mf = next((a for a in assets
               if str(a.get("name") or "").lower() == "version.json"), None)
    return setup, mf


def _mirror(url, src):
    """可选下载镜像: 把包地址挂到国内可达的前缀(如 https://mirror.example.com/gh/)。"""
    mir = str(src.get("mirror") or "").strip()
    if not mir or not _is_http(url):
        return url
    return mir.rstrip("/") + "/" + url


# ---------------- 单个源检查 ----------------

def _check_github(src, timeout):
    repo = str(src.get("repo") or "").strip().strip("/")
    if not repo or repo.count("/") != 1 or re.search(r"[\s<>\"]", repo) or "<" in repo:
        return {"ok": False, "error": "未填写 GitHub 仓库 (owner/repo)"}
    base = str(src.get("api_base") or "https://api.github.com").rstrip("/")
    hdr = {"Accept": "application/vnd.github+json",
           "X-GitHub-Api-Version": "2022-11-28"}
    tok = str(src.get("token") or "").strip()
    if tok:
        hdr["Authorization"] = "Bearer " + tok
    url = "%s/repos/%s/releases/latest" % (base, repo)
    data = _read_json(url, timeout, hdr)
    if not isinstance(data, dict):
        raise ValueError("GitHub 返回格式异常")
    if data.get("message") and not data.get("tag_name"):
        raise ValueError(str(data["message"])[:120])
    tag = normalize_version(data.get("tag_name") or data.get("name") or "")
    if not tag:
        raise ValueError("Release 版本号无法解析")
    setup, mf = _gh_assets(data, src.get("asset"))
    pkg_url = _mirror(str((setup or {}).get("browser_download_url") or ""), src)
    if not pkg_url:
        raise ValueError("Release 里没有 .exe 安装包")
    fallback = {
        "version": tag,
        "url": pkg_url,
        "size": (setup or {}).get("size") or 0,
        "released_at": data.get("published_at") or "",
        "notes": str(data.get("body") or "")[:4000],
    }
    m = {}
    if mf and mf.get("browser_download_url"):
        try:
            m = _read_json(mf["browser_download_url"], timeout, hdr) or {}
        except Exception:
            m = {}                      # 清单是可选的, 读不到就用 tag/assets
    if isinstance(m, dict) and m.get("version"):
        info = normalize_manifest(m, mf.get("browser_download_url") or url, fallback)
    else:
        info = normalize_manifest({}, url, fallback)
    # 清单没写包地址时以 Release 资产为准
    if not info["package"]["url"]:
        info["package"]["url"] = pkg_url
    return {"ok": True, "info": info,
            "detail": "%s/releases/tag/%s" % ("https://github.com/" + repo,
                                              data.get("tag_name") or tag)}


def _check_static(src, timeout):
    url = str(src.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "未填写更新清单地址"}
    m = _read_json(url, timeout)
    return {"ok": True, "info": normalize_manifest(m, url), "detail": url}


def _check_one(src, timeout, channel):
    r = {"name": str(src.get("name") or src.get("type") or "更新源"),
         "type": str(src.get("type") or "static"),
         "enabled": bool(src.get("enabled", True)), "ok": False,
         "version": "", "error": "", "detail": ""}
    if not r["enabled"]:
        r["error"] = "已停用"
        return r
    t0 = time.perf_counter()
    try:
        if r["type"] == "github":
            out = _check_github(src, timeout)
        else:
            out = _check_static(src, timeout)
        if not out.get("ok"):
            r["error"] = out.get("error") or "未知错误"
            return r
        info = out["info"]
        if channel and info.get("channel") and info["channel"] != channel:
            r["error"] = "通道不匹配 (%s)" % info["channel"]
            return r
        r.update({"ok": True, "version": info["version"],
                  "detail": out.get("detail") or "", "elapsed": round(time.perf_counter() - t0, 2)})
        r["info"] = info
        return r
    except Exception as e:
        r["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        return r


def _rollout_ok(info):
    """灰度: rollout<100 时按机器标识稳定取模, 同一台机器每次结果一致。"""
    try:
        r = int(info.get("rollout") if info.get("rollout") is not None else 100)
    except Exception:
        r = 100
    if r >= 100:
        return True
    if r <= 0:
        return False
    seed = (os.environ.get("COMPUTERNAME", "") + "|" +
            os.environ.get("USERNAME", "")).encode("utf-8", "replace")
    return int(hashlib.sha256(seed).hexdigest()[:8], 16) % 100 < r


def check(cfg=None, current_version=None, timeout=None):
    """并行检查全部启用的更新源, 返回统一状态。任何异常都被吞掉, 调用方永远拿得到 dict。"""
    t0 = time.perf_counter()
    cur = current_version or APP_VERSION
    c = _merge_config(cfg if cfg is not None else load_config())
    to = int(timeout or c["timeout"])
    base = {"ok": True, "state": "idle", "current": cur,
            "checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed": 0.0, "sources": [], "latest": None, "has_update": False,
            "ignored": False, "required": False, "release": None,
            "config": _public_config(c)}
    if not c.get("enabled"):
        base["state"] = "disabled"
        return base
    srcs = [s for s in c["sources"] if s.get("enabled", True)]
    if not srcs:
        base["state"] = "unconfigured"
        return base
    results = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(srcs))) as ex:
            futs = [ex.submit(_check_one, s, to, c.get("channel")) for s in srcs]
            for f in futs:
                try:
                    results.append(f.result(timeout=to + 3))
                except Exception as e:
                    results.append({"name": "更新源", "type": "?", "enabled": True,
                                    "ok": False, "version": "",
                                    "error": "超时: %s" % type(e).__name__, "detail": ""})
    except Exception as e:
        base["ok"] = False
        base["state"] = "failed"
        base["error"] = str(e)[:200]
        return base
    good = [r for r in results
            if r.get("ok") and r.get("info") and _rollout_ok(r["info"])]
    best = None
    best_src = None
    for r in good:
        if best is None or is_newer(r["info"]["version"], best["version"]):
            best, best_src = r["info"], r
    if best:
        base["latest"] = best["version"]
        base["release"] = best
        base["release"]["source"] = best_src["name"]
        base["release"]["source_type"] = best_src["type"]
        ignored = str(c.get("ignored_version") or "").strip() == best["version"]
        required = bool(best.get("force")) or bool(
            best.get("min_version") and version_tuple(cur) < version_tuple(best["min_version"]))
        base["ignored"] = bool(ignored and not required)
        base["required"] = required
        base["has_update"] = is_newer(best["version"], cur) and not base["ignored"]
    base["sources"] = [{k: v for k, v in r.items() if k != "info"} for r in results]
    base["elapsed"] = round(time.perf_counter() - t0, 2)
    if not good:
        missing = all("未填写" in (r.get("error") or "") for r in results)
        base["state"] = "unconfigured" if missing else "failed"
    elif not base["has_update"]:
        base["state"] = "latest"
    return base


# ---------------- 下载 / 校验 / 安装 ----------------

def _safe_name(v):
    return re.sub(r"[^0-9A-Za-z._-]", "_", str(v or "latest"))


def package_path(release, dest_dir=None):
    v = _safe_name((release or {}).get("version") or "latest")
    return os.path.join(dest_dir or download_dir(), "%sSetup-%s.exe" % (APP_NAME, v))


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def download(release, progress=None, dest_dir=None, timeout=60):
    """下载安装包 → sha256 校验 → 返回本地路径。progress(done,total,percent)。"""
    if not isinstance(release, dict) or not isinstance(release.get("package"), dict):
        return {"ok": False, "error": "没有可用的更新包"}
    pkg = release["package"]
    url = str(pkg.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "更新包地址为空"}
    dest = package_path(release, dest_dir)
    tmp = dest + ".part"
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
    except Exception:
        dest = package_path(release, tempfile.gettempdir())
        tmp = dest + ".part"
    total = int(pkg.get("size") or 0)
    done = 0
    try:
        if not _is_http(url):
            src = _local_path(url)
            if not os.path.isfile(src):
                return {"ok": False, "error": "更新包不存在: %s" % src}
            total = os.path.getsize(src)
            shutil.copyfile(src, tmp)
            done = total
            if progress:
                progress(done, total, 100)
        else:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as out:
                total = int(r.headers.get("Content-Length") or total or 0)
                while True:
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total,
                                 int(done * 100 / total) if total else 0)
        if done <= 0:
            raise ValueError("下载内容为空")
        if total and done != total:
            raise ValueError("下载不完整(%d/%d 字节)" % (done, total))
        declared = int(pkg.get("size") or 0)
        if declared and done != declared:
            raise ValueError("文件大小与清单不符(%d/%d 字节)" % (done, declared))
        expect = str(pkg.get("sha256") or "").strip().lower()
        actual = file_sha256(tmp)
        verified = bool(expect)
        if verified and actual != expect:
            raise ValueError("sha256 校验失败")
        os.replace(tmp, dest)
        return {"ok": True, "path": dest, "size": done, "sha256": actual,
                "verified": verified}
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


def install(setup_path, launch=True):
    """延迟启动静默安装器: 由调用方随后关闭主窗口, 让文件不再被占用。

    ping 延时约 4 秒, 不写任何 .bat/.ps1(中文路径不会因脚本编码乱码),
    shell=True 走 CreateProcessW, Unicode 路径安全。
    """
    p = str(setup_path or "")
    if not os.path.isfile(p):
        return {"ok": False, "error": "安装包不存在: %s" % p}
    args = '--silent%s' % (" --launch" if launch else "")
    cmd = 'ping -n 5 127.0.0.1 >nul & "%s" %s' % (p, args)
    try:
        subprocess.Popen(cmd, shell=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=0x08000000, close_fds=True)
        return {"ok": True, "note": "安装器将在 EZcode 关闭后自动运行"}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


def ignore_version(version, path=None):
    c = _public_config(_merge_config(load_config(path)))
    c["ignored_version"] = str(version or "").strip()
    return save_config(c, path)
