# -*- coding: utf-8 -*-
"""模型厂商快捷接入回归: 预设完整性 + 配置归一 + 连接测试边界。

运行: python build/diag_providers.py
不联网(最后一个用例只验证"不可达地址快速失败", 用本机 127.0.0.1:9)。
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, ROOT)

import providers as P                      # noqa: E402
from engine import Engine                  # noqa: E402

PASS = FAIL = 0


def ck(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % name)
    else:
        FAIL += 1
        print("  ✗ %s   %s" % (name, detail))


def main():
    print("=== 一、厂商预设 ===")
    ps = P.list_providers()
    ck("厂商数量 >= 8", len(ps) >= 8, "got=%d" % len(ps))
    ids = [p["id"] for p in ps]
    ck("id 唯一", len(ids) == len(set(ids)), str(ids))
    ck("包含 DeepSeek", "deepseek" in ids)
    ck("包含通义千问", "dashscope" in ids)
    ck("包含 Kimi", "moonshot" in ids)
    ck("包含智谱", "zhipu" in ids)
    ck("包含豆包", "doubao" in ids)
    ck("包含 OpenAI", "openai" in ids)

    bad_url = [p["id"] for p in ps if not str(p.get("base_url", "")).startswith("https://")]
    ck("全部为 https 地址", not bad_url, str(bad_url))
    no_models = [p["id"] for p in ps if not p.get("models")]
    ck("每家至少一个模型", not no_models, str(no_models))
    bad_model = [(p["id"], m) for p in ps for m in p["models"]
                 if not m.get("id") or not isinstance(m.get("ctx"), int) or m["ctx"] <= 0]
    ck("模型都有 id 与正数上下文", not bad_model, str(bad_model[:3]))
    bad_note = [p["id"] for p in ps if not (isinstance(p.get("note"), dict)
                                            and p["note"].get("zh") and p["note"].get("en"))]
    ck("厂商说明中英双语齐全", not bad_note, str(bad_note))
    ck("不含占位符 Key", not any("api_key" in p for p in ps))

    print("=== 二、profile 生成 ===")
    prof = P.build_profile("deepseek", "", "sk-test")
    ck("默认模型取第一个", prof["model"] == "deepseek-v4-flash", prof.get("model"))
    ck("带 provider 标记", prof.get("provider") == "deepseek")
    ck("上下文取自模型表", prof.get("context") == 1000000, prof.get("context"))
    ck("地址为官方地址", prof["base_url"] == "https://api.deepseek.com/v1")
    ck("手工模型名保留", P.build_profile("dashscope", "qwen-plus")["model"] == "qwen-plus")
    ck("未知厂商返回 None", P.build_profile("nope") is None)
    ck("get_provider 返回副本", P.get_provider("openai") is not P.PROVIDERS[8])
    P.list_providers()[0]["models"][0]["id"] = "hacked"
    ck("list_providers 不污染常量", P.PROVIDERS[0]["models"][0]["id"] != "hacked")

    print("=== 三、配置归一(落盘字段) ===")
    c = Engine.clean_llm_profile({"base_url": " https://x/v1 ", "api_key": "sk-1",
                                  "model": " m ", "provider": "openai", "context": "1000",
                                  "temperature": 0, "verify_ssl": False, "output": 8192,
                                  "evil": "drop-me"})
    ck("base_url 去空格", c["base_url"] == "https://x/v1", c.get("base_url"))
    ck("provider 保留", c.get("provider") == "openai")
    ck("context 转 int", c.get("context") == 1000)
    ck("temperature=0 保留", c.get("temperature") == 0.0, c.get("temperature"))
    ck("verify_ssl=False 保留", c.get("verify_ssl") is False)
    ck("output 保留", c.get("output") == 8192)
    ck("未知字段被丢弃", "evil" not in c)
    ck("空配置不报错", Engine.clean_llm_profile({})["base_url"] == "")

    print("=== 四、连接测试边界 ===")
    fake = types.SimpleNamespace(clean_llm_profile=Engine.clean_llm_profile)
    r = Engine.test_llm_profile(fake, {})
    ck("空配置返回失败", r.get("ok") is False and "接口地址" in r.get("error", ""))
    r = Engine.test_llm_profile(fake, {"base_url": "http://127.0.0.1:9/v1", "model": "x",
                                       "api_key": "k"})
    ck("不可达地址返回失败且不抛异常", r.get("ok") is False, str(r)[:120])
    ck("错误信息可读", bool(r.get("error")))

    print("=== 五、桥接与前端接线 ===")
    src = open(os.path.join(ROOT, "app", "client.py"), encoding="utf-8").read()
    ck("桥接注册 llm_providers", '"llm_providers"' in src)
    ck("桥接注册 test_llm_config", '"test_llm_config"' in src)
    js = open(os.path.join(ROOT, "app", "webui", "app.js"), encoding="utf-8").read()
    for cls in ("prov-grid", "prov-form", "pf-test", "pf-add", "_provHit"):
        ck("前端含 %s" % cls, cls in js)
    ck("保留自定义配置入口", "set-add" in js and "set-url" in js)
    ck("接入即调用 save_llm_config", "save_llm_config" in js)
    css = open(os.path.join(ROOT, "app", "webui", "style.css"), encoding="utf-8").read()
    ck("样式含厂商卡片", ".prov-card" in css and ".prov-form" in css)
    i18n = open(os.path.join(ROOT, "app", "webui", "i18n.js"), encoding="utf-8").read()
    ck("词条含快速接入", '"快速接入"' in i18n and "Quick setup" in i18n)

    print("\n结果: %d 通过 / %d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
