#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EZcode 插件管理CLI
    python plugin.py list               # 列出插件/MCP服务器/注册的工具
    python plugin.py add <路径>         # 安装插件(复制.py到plugins/)
    python plugin.py remove <名字>      # 卸载插件
    python plugin.py disable <名字>     # 禁用(加入devices.json plugins.disabled)
    python plugin.py enable <名字>      # 启用
"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

PLUGIN_DIR = os.path.join(BASE, "plugins")
DEVICE_FILES = [os.path.join(BASE, "devices.json"), os.path.join(BASE, "config.json")]
DEVICES = DEVICE_FILES[0]


def _devices():
    for p in DEVICE_FILES:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    raise SystemExit("缺少 devices.json (或旧版 config.json), 无法管理插件配置")


def _save_devices(cfg):
    with open(DEVICES, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _disabled(cfg):
    return (cfg.setdefault("plugins", {}).setdefault("disabled", []))


def cmd_list():
    os.makedirs(PLUGIN_DIR, exist_ok=True)
    files = [f for f in sorted(os.listdir(PLUGIN_DIR)) if f.endswith(".py") and not f.startswith("_")]
    cfg = _devices()
    dis = set(_disabled(cfg))
    print("== 插件 (%s) ==" % PLUGIN_DIR)
    if not files:
        print("  (空)  安装: python plugin.py add <路径.py>")
    for f in files:
        name = os.path.splitext(f)[0]
        print("  %s %s" % ("✓" if name not in dis else "✗禁用", f))
    print()
    print("== MCP服务器 (devices.json mcp.servers) ==")
    servers = (cfg.get("mcp") or {}).get("servers") or []
    if not servers:
        print("  (未配置)  示例见 devices.example.json")
    for s in servers:
        print("  %s %s [%s] %s" % ("✓" if s.get("enabled", True) else "✗禁用",
                                   s.get("name"), s.get("type"),
                                   s.get("url") or " ".join(s.get("command") or [])))
    print()
    print("实际注册的工具以启动日志为准(插件+MCP工具会合并进工具表).")


def cmd_add(path):
    if not os.path.exists(path):
        print("文件不存在:", path)
        sys.exit(1)
    if not path.endswith(".py"):
        print("只支持.py插件文件")
        sys.exit(1)
    os.makedirs(PLUGIN_DIR, exist_ok=True)
    dst = os.path.join(PLUGIN_DIR, os.path.basename(path))
    shutil.copy(path, dst)
    name = os.path.splitext(os.path.basename(path))[0]
    cfg = _devices()
    d = _disabled(cfg)
    if name in d:
        d.remove(name)
        _save_devices(cfg)
    print("已安装: %s (重启EZcode生效)" % dst)


def cmd_remove(name):
    p = os.path.join(PLUGIN_DIR, name + ".py")
    if not os.path.exists(p):
        print("插件不存在:", name)
        sys.exit(1)
    os.remove(p)
    print("已卸载:", name)


def cmd_disable(name):
    cfg = _devices()
    d = _disabled(cfg)
    if name not in d:
        d.append(name)
        _save_devices(cfg)
    print("已禁用:", name)


def cmd_enable(name):
    cfg = _devices()
    d = _disabled(cfg)
    if name in d:
        d.remove(name)
        _save_devices(cfg)
    print("已启用:", name)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "list":
        cmd_list()
    elif args[0] == "add" and len(args) > 1:
        cmd_add(args[1])
    elif args[0] == "remove" and len(args) > 1:
        cmd_remove(args[1])
    elif args[0] == "disable" and len(args) > 1:
        cmd_disable(args[1])
    elif args[0] == "enable" and len(args) > 1:
        cmd_enable(args[1])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
