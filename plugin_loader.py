# -*- coding: utf-8 -*-
"""EZcode 插件与MCP加载器
插件: plugins/*.py 丢进目录即安装。标准接口(全部可选但至少有TOOLS):
    TOOLS         = [{"schema": OpenAI函数schema, "handler": callable, "exec": False}]
    PLUGIN_PROMPT = "附加给系统提示词的说明/纪律(如插件工具的使用时机)"
    def on_load(cfg): 启动钩子(配置注入)
MCP: devices.json 的 mcp.servers 登记, 工具注册为 mcp__<server>__<tool>
"""
import os
import sys
import importlib.util
import threading
import traceback

import tools as T
import mcp_client

BASE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.join(BASE, "plugins")

_extra_prompt = []
_warnings = []
_mcp_servers = []      # 持有连接(stdio子进程需要保活)
_bootstrapped = False
_mcp_done = False      # 后台MCP连接是否完成
_on_mcp_done = None    # 完成回调(UI注册)
_loaded = {"plugins": [], "plugin_tools": 0, "mcp_servers": [], "mcp_tools": 0}
_plugin_tools = {}     # 插件名 -> [该插件注册的工具名](卸载/重载用)


def prompt_addon():
    return "\n".join(_extra_prompt)


def warnings():
    return list(_warnings)


def status():
    return dict(_loaded)


def plugin_files():
    """扫描插件目录(排除下划线开头的模板), 返回[(文件名, 模块名)]。"""
    try:
        os.makedirs(PLUGIN_DIR, exist_ok=True)
        files = sorted(f for f in os.listdir(PLUGIN_DIR)
                       if f.endswith(".py") and not f.startswith("_"))
    except OSError:
        files = []
    return [(f, os.path.splitext(f)[0]) for f in files]


def _unload_plugin(name):
    """注销一个插件注册的全部工具(热卸载)。"""
    for fn in _plugin_tools.pop(name, []):
        T.TOOLS[:] = [t for t in T.TOOLS if t["function"]["name"] != fn]
        T.HANDLERS.pop(fn, None)
        T.EXEC_TOOL_NAMES.discard(fn)
        try:
            import caps
            caps.CAPABILITIES.pop(fn, None)
        except Exception:
            pass
    _extra_prompt[:] = [p for p in _extra_prompt
                        if not p.startswith("[插件:%s]" % name)]


def reload_plugins(cfg):
    """全量重载插件目录(安装/启停/卸载后调用, 热生效)。
    先注销全部已加载插件工具, 再按 plugins.disabled 过滤重载。返回状态。"""
    for name in list(_plugin_tools):
        _unload_plugin(name)
    _loaded["plugins"] = []
    _loaded["plugin_tools"] = 0
    disabled = set((cfg.get("plugins") or {}).get("disabled") or [])
    for fn, name in plugin_files():
        if name in disabled:
            continue
        _load_plugin_file(os.path.join(PLUGIN_DIR, fn), cfg)
    return dict(_loaded)


def _load_plugin_file(path, cfg):
    name = os.path.splitext(os.path.basename(path))[0]
    try:
        spec = importlib.util.spec_from_file_location("ezcode_plugin_%s" % name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:
        _warnings.append("插件%s加载失败: %s" % (name, traceback.format_exc(limit=1)[-300:]))
        return
    count = 0
    registered = _plugin_tools.setdefault(name, [])
    for item in (getattr(mod, "TOOLS", None) or []):
        try:
            schema, handler = item["schema"], item["handler"]
            fn_name = schema["function"]["name"]
            if fn_name in T.HANDLERS:
                _warnings.append("插件%s工具名冲突, 跳过: %s" % (name, fn_name))
                continue
            T.TOOLS.append(schema)
            T.HANDLERS[fn_name] = handler
            if item.get("exec"):
                T.EXEC_TOOL_NAMES.add(fn_name)
            registered.append(fn_name)
            count += 1
        except Exception:
            _warnings.append("插件%s条目注册失败: %s" % (name, traceback.format_exc(limit=1)[-200:]))
    if getattr(mod, "PLUGIN_PROMPT", None):
        _extra_prompt.append("[插件:%s] %s" % (name, mod.PLUGIN_PROMPT))
    if hasattr(mod, "on_load"):
        try:
            mod.on_load(cfg)
        except Exception:
            _warnings.append("插件%s on_load失败: %s" % (name, traceback.format_exc(limit=1)[-200:]))
    _loaded["plugins"].append(name)
    _loaded["plugin_tools"] += count


def _load_mcp_server(spec, disabled):
    name = spec.get("name") or "mcp"
    if name in disabled or not spec.get("enabled", True):
        return
    try:
        srv = mcp_client.connect_server(spec)
        raw_tools = srv.list_tools()
    except Exception as e:
        _warnings.append("MCP服务器%s连接失败: %s" % (name, str(e)[:200]))
        return
    count = 0
    for t in raw_tools:
        try:
            fn_name = _sanitize(name, t.get("name"))
            if fn_name in T.HANDLERS:
                fn_name += "_%d" % count
            params = t.get("inputSchema") or {"type": "object", "properties": {}}
            desc = (t.get("description") or "")[:600]
            schema = {"type": "function",
                      "function": {"name": fn_name, "description": "[MCP:%s] %s" % (name, desc),
                                   "parameters": params}}
            T.TOOLS.append(schema)
            T.HANDLERS[fn_name] = _mcp_handler(srv, t["name"])
            count += 1
        except Exception:
            continue
    _mcp_servers.append(srv)
    _loaded["mcp_servers"].append(name)
    _loaded["mcp_tools"] += count


def _sanitize(server, tool):
    return mcp_client._sanitize_tool_name(server, tool)


def _mcp_handler(srv, raw_tool_name):
    def handler(**kwargs):
        try:
            return srv.call_tool(raw_tool_name, kwargs)
        except mcp_client.McpError as e:
            return {"error": str(e)[:400]}
        except Exception as e:
            return {"error": "%s: %s" % (type(e).__name__, e)}
    handler.__name__ = "mcp_%s" % raw_tool_name[:40]
    return handler


def set_on_mcp_done(fn):
    global _on_mcp_done
    _on_mcp_done = fn


def is_mcp_done():
    return _mcp_done


def bootstrap(cfg):
    """启动时加载: 插件目录同步加载(本地, 快); MCP服务器后台连接(网络, 不阻塞UI)。
    MCP工具注册进T.TOOLS后, 下一轮toolset()自动可见。"""
    global _bootstrapped
    if _bootstrapped:
        return
    _bootstrapped = True
    disabled = set((cfg.get("plugins") or {}).get("disabled") or [])
    os.makedirs(PLUGIN_DIR, exist_ok=True)
    for fn in sorted(os.listdir(PLUGIN_DIR)):
        if fn.endswith(".py") and not fn.startswith("_"):
            if os.path.splitext(fn)[0] in disabled:
                continue
            _load_plugin_file(os.path.join(PLUGIN_DIR, fn), cfg)
    specs = list((cfg.get("mcp") or {}).get("servers") or [])
    if specs:
        threading.Thread(target=_bg_load_mcp, args=(specs, disabled), daemon=True).start()
    else:
        global _mcp_done
        _mcp_done = True


def _bg_load_mcp(specs, disabled):
    global _mcp_done
    try:
        for spec in specs:
            try:
                _load_mcp_server(spec, disabled)
            except Exception as e:
                _warnings.append("MCP配置项异常: %s" % str(e)[:200])
    finally:
        _mcp_done = True
        cb = _on_mcp_done
        if cb:
            try:
                cb(dict(_loaded))
            except Exception:
                pass


def shutdown():
    for s in _mcp_servers:
        try:
            s.close()
        except Exception:
            pass


def reload_mcp(cfg):
    """设置界面保存MCP配置后的热重载: 关闭旧连接/注销旧工具, 重新连接。
    只重载MCP部分, 不动plugins目录; 失败的服务器跳过并返回警告列表。"""
    for s in _mcp_servers:
        try:
            s.close()
        except Exception:
            pass
    _mcp_servers.clear()
    _loaded["mcp_servers"] = []
    _loaded["mcp_tools"] = 0
    # 注销旧的 mcp__ 工具(TOOLS/HANDLERS)
    import tools as T
    T.TOOLS[:] = [t for t in T.TOOLS
                  if not t["function"]["name"].startswith("mcp__")]
    for k in [k for k in T.HANDLERS if k.startswith("mcp__")]:
        T.HANDLERS.pop(k, None)
    global _mcp_done
    _mcp_done = False
    specs = list((cfg.get("mcp") or {}).get("servers") or [])
    disabled = set((cfg.get("plugins") or {}).get("disabled") or [])
    warns = []
    if specs:
        for spec in specs:
            try:
                _load_mcp_server(spec, disabled)
            except Exception as e:
                warns.append("MCP[%s] 连接失败: %s" % (spec.get("name"), str(e)[:150]))
    _mcp_done = True
    cb = _on_mcp_done
    if cb:
        try:
            cb(dict(_loaded))
        except Exception:
            pass
    return {"loaded": dict(_loaded), "warnings": warns}
