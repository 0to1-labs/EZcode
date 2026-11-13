# -*- coding: utf-8 -*-
"""生成 EZcode 发布清单 version.json + 带版本号的安装包, 并给出推送命令。

一次发布只做三件事:
    python build/make_release.py --repo yourname/EZcode --notes "本次更新说明"
    gh release create v1.0.49 dist/update/* --title "EZcode v1.0.49" --generate-notes
    (或把 dist/update/ 整个目录扔到国内静态服务器)

用法:
  --repo          GitHub 仓库 owner/repo, 生成默认包下载地址
  --base-url      自定义包下载地址前缀(国内服务器用), 覆盖 --repo 推导的地址
  --setup         安装包路径, 默认项目根目录 EZcodeSetup.exe
  --out           输出目录, 默认 dist/update
  --notes/--notes-en   更新说明(中/英)
  --min-version   低于此版本的客户端强制升级
  --force         强制升级(忽略"忽略此版本")
  --rollout       灰度百分比 0-100, 默认 100
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def app_version():
    spec = importlib.util.spec_from_file_location(
        "ezcode_version", os.path.join(ROOT, "ezcode_version.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.APP_VERSION


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description="生成 EZcode 发布清单")
    ap.add_argument("--repo", default="", help="GitHub owner/repo")
    ap.add_argument("--base-url", default="", help="自定义包地址前缀(国内服务器)")
    ap.add_argument("--setup", default=os.path.join(ROOT, "EZcodeSetup.exe"))
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "update"))
    ap.add_argument("--notes", default="", help="中文更新说明")
    ap.add_argument("--notes-en", default="", help="英文更新说明")
    ap.add_argument("--min-version", default="", help="低于此版本强制升级")
    ap.add_argument("--force", action="store_true", help="强制升级")
    ap.add_argument("--rollout", type=int, default=100, help="灰度百分比 0-100")
    ap.add_argument("--channel", default="stable", help="发布通道")
    a = ap.parse_args()

    v = app_version()
    setup = os.path.abspath(a.setup)
    if not os.path.isfile(setup):
        raise SystemExit("找不到安装包: %s\n请先运行 build/build_app.py + pack_payload.py + build_setup.py" % setup)
    asset = "EZcodeSetup-%s.exe" % v
    if a.base_url:
        base = a.base_url.rstrip("/")
    elif a.repo:
        base = "https://github.com/%s/releases/download/v%s" % (a.repo.strip("/"), v)
    else:
        base = "https://github.com/<owner>/<repo>/releases/download/v%s" % v
    pkg_url = base + "/" + asset
    os.makedirs(a.out, exist_ok=True)
    dst = os.path.join(a.out, asset)
    if os.path.abspath(dst) != setup:
        shutil.copyfile(setup, dst)
    size = os.path.getsize(dst)
    digest = sha256(dst)

    manifest = {
        "version": v,
        "channel": a.channel,
        "released_at": datetime.date.today().isoformat(),
        "min_version": a.min_version,
        "force": bool(a.force),
        "rollout": max(0, min(100, a.rollout)),
        "notes": {"zh": a.notes or "EZcode %s 已发布" % v,
                  "en": a.notes_en or a.notes or ("EZcode %s is available" % v)},
        "package": {"url": pkg_url, "sha256": digest, "size": size},
    }
    mf = os.path.join(a.out, "version.json")
    with open(mf, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("\n=== 发布清单已生成 ===")
    print("版本      : %s" % v)
    print("安装包    : %s (%.1f MB)" % (dst, size / 1048576))
    print("sha256    : %s" % digest)
    print("清单      : %s" % mf)
    print("包地址    : %s" % pkg_url)

    if a.repo:
        print("\n=== 推送到 GitHub (二选一) ===")
        print("gh release create v%s \"%s\" \"%s\" --title \"EZcode v%s\" --notes \"%s\"" %
              (v, dst, mf, v, (a.notes or "EZcode " + v).replace('"', "'")))
        print("或在 GitHub 网页新建 Release: tag=v%s, 上传 %s 与 version.json 两个文件" % (v, asset))
        print("发布后, 客户端把设置中心「版本更新 → GitHub Releases → 仓库」填为 %s" % a.repo)
    else:
        print("\n[提示] 未指定 --repo, 包地址含占位符; 重新运行并加 --repo owner/repo 生成正式清单")

    print("\n=== 推送到国内服务器 ===")
    print("1) 用 --base-url https://你的域名/ezcode 重新生成清单(包地址会指向国内)")
    print("2) 把 dist/update/ 下的 version.json 与 %s 一起放到该目录" % asset)
    print("3) 客户端「版本更新」里启用「国内更新服务器」并填 https://你的域名/ezcode/version.json")
    print("   两条源会同时检查, 自动采用最高版本; GitHub 不可达时国内源照常生效。")
    print("   内网无公网时, version.json 与安装包也可放 UNC 共享目录, url 直接写共享路径。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
