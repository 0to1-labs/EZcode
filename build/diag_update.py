# -*- coding: utf-8 -*-
"""EZcode 自动更新回归自测(纯本地, 不联网、不改本机真实配置)。

覆盖: 版本比较 / 多源并行取最高版本 / 失败降级 / GitHub 与静态清单解析 /
灰度与忽略 / 下载 sha256 校验与篡改拦截 / 配置读写 / 发布清单生成 /
前后端桥接与界面产物 / 版本唯一源同步。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")
sys.path.insert(0, ROOT)
sys.path.insert(0, APP)
import updater as U  # noqa: E402

CHECKS = []


def ck(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("  %s %s%s" % ("OK  " if ok else "FAIL", name,
                         ("  -- " + str(detail)[:130]) if not ok else ""))


def tmpdir():
    return tempfile.mkdtemp(prefix="ezcode-upd-diag-")


def write(path, data, binary=False):
    with open(path, "wb" if binary else "w", **({} if binary else {"encoding": "utf-8"})) as f:
        f.write(data)


def manifest(version, url, digest="", size=0, **extra):
    d = {"version": version, "channel": "stable",
         "released_at": "2026-11-12", "notes": {"zh": "修复若干问题", "en": "Bug fixes"},
         "package": {"url": url, "sha256": digest, "size": size}}
    d.update(extra)
    return d


def main():
    print("== A. 版本号 ==")
    ck("版本解析 v 前缀", U.version_tuple("v1.0.49") == (1, 0, 49))
    ck("版本解析不等长", U.version_tuple("2.0") == (2, 0, 0))
    ck("版本解析带后缀", U.version_tuple("1.0.49-beta2") == (1, 0, 49))
    ck("高位比较", U.is_newer("1.0.49", "1.0.48") and not U.is_newer("1.0.48", "1.0.49"))
    ck("两位数比较不会字符串误判", U.is_newer("1.0.10", "1.0.9"))
    ck("相等不算更新", not U.is_newer("1.0.48", "1.0.48"))
    ck("唯一版本源", U.APP_VERSION == "1.0.48")
    try:
        import ezcode_common  # 需 build 在 sys.path
    except ImportError:
        sys.path.insert(0, HERE)
        import ezcode_common
    ck("安装器版本与客户端一致", ezcode_common.VERSION == U.APP_VERSION,
       "%s vs %s" % (ezcode_common.VERSION, U.APP_VERSION))
    vinfo = open(os.path.join(HERE, "version_info.txt"), encoding="utf-8").read()
    ck("exe 版本资源已同步", "1, 0, 48, 0" in vinfo and "'1.0.48.0'" in vinfo)

    print("\n== B. 配置与多源 ==")
    cfg = U.load_config()
    ck("默认启用自动更新", cfg["enabled"] is True and cfg["check_on_startup"] is True)
    ck("默认两条源(GitHub + 国内)", len(cfg["sources"]) == 2 and
       cfg["sources"][0]["type"] == "github" and cfg["sources"][1]["type"] == "static")
    ck("国内源默认停用", cfg["sources"][1]["enabled"] is False)
    ck("未填仓库不算配置错误", U.check(cfg)["state"] in ("unconfigured", "failed"))
    pub = U._public_config(cfg)
    ck("前端配置不含 token", all("token" not in s for s in pub["sources"]))
    os.environ["EZCODE_GITHUB_REPO"] = "someone/EZcode"
    ck("环境变量可覆盖仓库", U._merge_config({})["sources"][0]["repo"] == "someone/EZcode")
    os.environ.pop("EZCODE_GITHUB_REPO", None)

    print("\n== C. 静态清单 / 双源并行取最高版本 ==")
    d = tmpdir()
    pkg = os.path.join(d, "EZcodeSetup-1.0.51.exe")
    write(pkg, b"EZCODE-FAKE-SETUP" * 32, binary=True)
    digest = hashlib.sha256(open(pkg, "rb").read()).hexdigest()
    size = os.path.getsize(pkg)
    cn = os.path.join(d, "version.json")
    gh = os.path.join(d, "version.github.json")
    write(cn, json.dumps(manifest("1.0.51", "EZcodeSetup-1.0.51.exe", digest, size)))
    write(gh, json.dumps(manifest("1.0.50", "EZcodeSetup-1.0.51.exe", digest, size)))
    two = {"enabled": True, "channel": "stable", "timeout": 4,
           "sources": [
               {"type": "static", "name": "GitHub Releases", "enabled": True, "url": gh},
               {"type": "static", "name": "国内更新服务器", "enabled": True, "url": cn},
           ]}
    r = U.check(two, current_version="1.0.48")
    ck("两条源都被检查", len(r["sources"]) == 2 and all(s["ok"] for s in r["sources"]))
    ck("自动取最高版本", r["latest"] == "1.0.51" and r["has_update"] is True,
       str(r["latest"]))
    ck("发布来源标注正确", r["release"]["source"] == "国内更新服务器")
    ck("相对包地址按清单目录解析",
       r["release"]["package"]["url"] == os.path.join(d, "EZcodeSetup-1.0.51.exe"))
    ck("sha256 与大小透传", r["release"]["package"]["sha256"] == digest and
       r["release"]["package"]["size"] == size)

    print("\n== D. 失败降级 / 忽略 / 强制 / 灰度 ==")
    one = dict(two, sources=[two["sources"][0],
                             {"type": "static", "name": "坏源", "enabled": True,
                              "url": os.path.join(d, "not-exist.json")}])
    r2 = U.check(one, current_version="1.0.48")
    ck("单源失败不拖垮检查", r2["ok"] and r2["has_update"] and r2["latest"] == "1.0.50")
    bad = [s for s in r2["sources"] if s["name"] == "坏源"][0]
    ck("失败原因被记录", bad["ok"] is False and bool(bad["error"]))
    r3 = U.check(dict(two, ignored_version="1.0.51"), current_version="1.0.48")
    ck("忽略此版本生效", r3["ignored"] is True and r3["has_update"] is False)
    write(os.path.join(d, "force.json"), json.dumps(manifest(
        "1.0.52", "EZcodeSetup-1.0.51.exe", digest, size,
        force=True, min_version="1.0.50")))
    r4 = U.check(dict(two, ignored_version="1.0.52", sources=[
        {"type": "static", "name": "强制源", "enabled": True,
         "url": os.path.join(d, "force.json")}]), current_version="1.0.48")
    ck("强制升级覆盖忽略", r4["required"] is True and r4["has_update"] is True)
    ck("已是最新不误报", U.check(two, current_version="1.0.51")["has_update"] is False)
    ck("关闭更新直接返回", U.check(dict(two, enabled=False))["state"] == "disabled")
    ck("全停用返回未配置", U.check(dict(two, sources=[
        {"type": "static", "name": "x", "enabled": False, "url": ""}]))["state"] == "unconfigured")
    ck("rollout=0 不推送", U._rollout_ok({"rollout": 0}) is False)
    ck("rollout=100 全量", U._rollout_ok({"rollout": 100}) is True)
    try:
        U.normalize_manifest({"version": "abc"}, "")
        ck("非法版本号抛错", False)
    except Exception:
        ck("非法版本号抛错", True)
    try:
        U.normalize_manifest({"version": "1.0.49"}, "")
        ck("缺少包地址抛错", False)
    except Exception:
        ck("缺少包地址抛错", True)

    print("\n== E. GitHub Releases 解析(mock, 不联网) ==")
    orig = U._read_json
    release = {"tag_name": "v1.0.55", "published_at": "2026-11-12T10:00:00Z",
               "body": "Fix background tasks",
               "assets": [
                   {"name": "EZcodeSetup-1.0.55.exe", "size": size,
                    "browser_download_url": "https://github.com/o/r/releases/download/v1.0.55/EZcodeSetup-1.0.55.exe"},
                   {"name": "version.json",
                    "browser_download_url": "https://github.com/o/r/releases/download/v1.0.55/version.json"}]}
    mfest = manifest("1.0.55", "", digest, size)
    mfest["notes"] = {"zh": "中文说明", "en": "English notes"}

    def fake(url, timeout, headers=None):
        if "releases/latest" in url:
            return release
        if url.endswith("version.json"):
            return mfest
        raise AssertionError("unexpected url " + url)

    U._read_json = fake
    try:
        out = U._check_one({"type": "github", "name": "GitHub Releases", "enabled": True,
                            "repo": "o/r", "asset": "EZcodeSetup"}, 4, "stable")
        ck("GitHub 源解析成功", out["ok"] is True and out["version"] == "1.0.55",
           out.get("error"))
        ck("Release 说明与资产地址", out["info"]["notes"]["zh"] == "中文说明" and
           out["info"]["package"]["url"].endswith("EZcodeSetup-1.0.55.exe"))
        ck("清单 sha256 生效", out["info"]["package"]["sha256"] == digest)
        miss = U._check_one({"type": "github", "name": "GitHub", "enabled": True,
                             "repo": "", "asset": "EZcodeSetup"}, 4, "stable")
        ck("未填仓库给出明确提示", miss["ok"] is False and "未填写" in miss["error"])
        bad_repo = U._check_one({"type": "github", "name": "GitHub", "enabled": True,
                                 "repo": "not/a/valid/repo"}, 4, "stable")
        ck("非法仓库被拒", bad_repo["ok"] is False)
    finally:
        U._read_json = orig

    print("\n== F. 下载 / 校验 / 安装入口 ==")
    rel = U.check(two, current_version="1.0.48")["release"]
    dest = os.path.join(d, "downloads")
    seen = []
    got = U.download(rel, progress=lambda a, b, c: seen.append(c), dest_dir=dest)
    ck("下载成功", got["ok"] is True, got.get("error"))
    ck("sha256 校验通过", got.get("verified") is True and got.get("sha256") == digest)
    ck("进度回调触发", len(seen) >= 1 and seen[-1] == 100)
    ck("落盘文件存在", os.path.isfile(got.get("path", "")))
    tampered = json.loads(json.dumps(rel))
    tampered["package"]["sha256"] = "0" * 64
    bad_dl = U.download(tampered, dest_dir=dest)
    ck("篡改哈希被拦截", bad_dl["ok"] is False and "sha256" in bad_dl["error"])
    ck("校验失败不留残留", not os.path.exists(U.package_path(rel, dest) + ".part"))
    miss_dl = U.download({"version": "1.0.60", "package": {"url": ""}})
    ck("空包地址被拒", miss_dl["ok"] is False)
    ck("安装入口对不存在包返回错误", U.install(os.path.join(d, "none.exe"))["ok"] is False)

    print("\n== G. 配置写回(临时副本) ==")
    cp = os.path.join(d, "devices.json")
    write(cp, json.dumps({"llm": {"active": "x"}, "ui": {"persona": "coder"},
                          "update": {"sources": [{"type": "github", "name": "GitHub Releases",
                                                  "repo": "old/repo", "token": "SECRET"}]}}))
    saved = U.save_config({"enabled": True, "sources": [
        {"type": "github", "name": "GitHub Releases", "repo": "new/repo", "enabled": True}]}, cp)
    ck("保存成功", saved["ok"] is True, saved.get("error"))
    raw = json.loads(open(cp, encoding="utf-8").read())
    ck("其它配置节未被动过", raw["llm"] == {"active": "x"} and raw["ui"] == {"persona": "coder"})
    ck("仓库已更新", raw["update"]["sources"][0]["repo"] == "new/repo")
    ck("token 未丢失", raw["update"]["sources"][0]["token"] == "SECRET")
    ck("读回合并默认字段", U.load_config(cp)["sources"][0]["api_base"].startswith("https://"))

    print("\n== H. 发布清单生成 ==")
    outd = os.path.join(d, "release")
    cmd = [sys.executable, os.path.join(HERE, "make_release.py"),
           "--repo", "someone/EZcode", "--setup", pkg, "--out", outd,
           "--notes", "修复多任务切换", "--notes-en", "Fix background tasks"]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    ck("make_release 执行成功", p.returncode == 0, (p.stderr or p.stdout)[-200:])
    mf2 = os.path.join(outd, "version.json")
    if os.path.isfile(mf2):
        m2 = json.loads(open(mf2, encoding="utf-8").read())
        ck("清单版本来自唯一源", m2["version"] == U.APP_VERSION)
        ck("清单含 sha256/size", m2["package"]["sha256"] == digest and m2["package"]["size"] == size)
        ck("清单含双语说明", m2["notes"]["zh"] == "修复多任务切换" and
           m2["notes"]["en"] == "Fix background tasks")
        ck("包地址指向 GitHub Release", "/releases/download/v" in m2["package"]["url"])
        ck("带版本号安装包已产出", os.path.isfile(os.path.join(
            outd, "EZcodeSetup-%s.exe" % U.APP_VERSION)))
        ck("输出含 gh 推送命令", "gh release create" in (p.stdout or ""))
        ck("输出含国内服务器说明", "国内服务器" in (p.stdout or ""))
    else:
        ck("清单文件已生成", False)

    print("\n== I. 前后端接线 / 静态产物 ==")
    client = open(os.path.join(APP, "client.py"), encoding="utf-8").read()
    for m in ("update_state", "check_update", "download_update", "install_update",
              "save_update_settings", "ignore_update", "open_update_dir"):
        ck("桥方法 " + m, ('"%s"' % m) in client and ("def %s(" % m) in client)
    js = open(os.path.join(APP, "webui", "app.js"), encoding="utf-8").read()
    for m in ("renderUpdatePanel", "onUpdateEvent", "paintUpdateBadge",
              "bindUpdatePanel", 'id: "update"'):
        ck("前端 " + m, m in js)
    html = open(os.path.join(APP, "webui", "index.html"), encoding="utf-8").read()
    ck("侧栏徽标已挂载", 'id="update-badge"' in html and 'id="brand-ver"' in html)
    ck("资源版本仍为 v44(回归脚本依赖)", "?v=44" in html)
    i18n = open(os.path.join(APP, "webui", "i18n.js"), encoding="utf-8").read()
    ck("i18n 含更新词条", "版本更新" in i18n and "Check for updates" in i18n)
    css = open(os.path.join(APP, "webui", "style.css"), encoding="utf-8").read()
    ck("样式含更新卡片", ".upd-badge" in css and ".upd-src" in css)
    inst = open(os.path.join(HERE, "installer.py"), encoding="utf-8").read()
    ck("安装器支持 --launch", '"--launch" in args' in inst)
    ck("安装器写文件有占用重试", "except PermissionError" in inst)
    for f in ("devices.json", "devices.default.json", "devices.example.json"):
        d3 = json.loads(open(os.path.join(ROOT, f), encoding="utf-8").read())
        ck(f + " 含 update 段", isinstance(d3.get("update"), dict) and
           len(d3["update"]["sources"]) == 2)
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    ck("README 有发布流程", "自动更新：如何把新版本推给已安装的客户端" in readme and
       "make_release.py" in readme and "两条源都启用时" in readme)
    ck("README 分区数已更新", "10 个分区" in readme)

    for x in (d,):
        shutil.rmtree(x, ignore_errors=True)
    ok = sum(1 for _, v in CHECKS if v)
    print("\n== 结果: %d/%d 通过 ==" % (ok, len(CHECKS)))
    if ok != len(CHECKS):
        print("失败项: " + ", ".join(n for n, v in CHECKS if not v))
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
