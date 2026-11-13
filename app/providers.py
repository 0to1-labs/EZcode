# -*- coding: utf-8 -*-
"""内置模型厂商预设。

目的: 用户在「设置中心 → 大模型」只需要选厂商 + 填 API Key 就能用,
不用自己查 base_url 和模型名。自定义配置仍然保留(手填 base_url)。

原则:
* 只收录**OpenAI 兼容**接口的厂商, 调用层零改动;
* 模型名一律可改: 预设只是"当前推荐的默认值", 厂商上新/改名时用户手改即可;
* 每个模型带 ctx(上下文 token), 用于引擎的上下文预算; 估不准就保守给值。
"""
from collections import OrderedDict

# 说明: 定价/模型名会变, 这里只保证「地址正确 + 默认模型可用」。
PROVIDERS = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": [
            {"id": "deepseek-v4-flash", "ctx": 1000000},
            {"id": "deepseek-v4-pro", "ctx": 1000000},
            {"id": "deepseek-v4-flash-vision-exp", "ctx": 1000000},
        ],
        "note": {"zh": "官方直连，性价比高；deepseek-chat / deepseek-reasoner 已下线，请用 v4 系列", "en": "Official API. deepseek-chat / deepseek-reasoner are retired — use the v4 models."},
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    {
        "id": "dashscope",
        "name": "通义千问（阿里云百炼）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": [
            {"id": "qwen3.8-max", "ctx": 262144},
            {"id": "qwen3-max", "ctx": 262144},
            {"id": "qwen-plus", "ctx": 131072},
            {"id": "qwen-turbo", "ctx": 131072},
            {"id": "qwen-long", "ctx": 1000000},
        ],
        "note": {"zh": "百炼控制台创建 Key；国际版把地址换成 dashscope-intl.aliyuncs.com", "en": "Create the key in Bailian console; for the intl region use dashscope-intl.aliyuncs.com."},
        "key_url": "https://bailian.console.aliyun.com/",
    },
    {
        "id": "moonshot",
        "name": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "models": [
            {"id": "kimi-k2.6", "ctx": 262144},
            {"id": "kimi-k2.5", "ctx": 262144},
            {"id": "kimi-k2-thinking", "ctx": 262144},
            {"id": "moonshot-v1-128k", "ctx": 131072},
        ],
        "note": {"zh": "长文本与工具调用稳定，适合读大文档", "en": "Strong long-context and tool calling — good for large documents."},
        "key_url": "https://platform.moonshot.cn/console/api-keys",
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": [
            {"id": "glm-5.3", "ctx": 204800},
            {"id": "glm-4.6", "ctx": 200000},
            {"id": "glm-4-flash", "ctx": 131072},
        ],
        "note": {"zh": "智谱开放平台 API Key，格式为 id.secret 时整串粘贴即可", "en": "Use the API key from Zhipu console; if it looks like id.secret paste the whole string."},
        "key_url": "https://open.bigmodel.cn/usercenter/apikeys",
    },
    {
        "id": "doubao",
        "name": "豆包（火山方舟）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": [
            {"id": "doubao-seed-2-1-pro-260915", "ctx": 262144},
            {"id": "doubao-seed-1-6-250615", "ctx": 262144},
            {"id": "doubao-seed-1-6-flash-250715", "ctx": 262144},
        ],
        "note": {"zh": "也可填方舟控制台的推理接入点 ID（ep- 开头），模型名以控制台为准", "en": "You may also use an Ark endpoint ID (ep-...). Model names follow the console."},
        "key_url": "https://console.volcengine.com/ark",
    },
    {
        "id": "hunyuan",
        "name": "腾讯混元",
        "base_url": "https://tokenhub.tencentmaas.com/v1",
        "models": [
            {"id": "hy4-preview", "ctx": 262144},
            {"id": "hy3", "ctx": 262144},
        ],
        "note": {"zh": "走腾讯云 TokenHub；旧地址 api.hunyuan.cloud.tencent.com 已停用，可手改", "en": "Uses Tencent Cloud TokenHub; the old api.hunyuan.cloud.tencent.com endpoint is retired."},
        "key_url": "https://console.cloud.tencent.com/tokenhub",
    },
    {
        "id": "siliconflow",
        "name": "硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": [
            {"id": "deepseek-ai/DeepSeek-V4.1-Flash", "ctx": 1000000},
            {"id": "zai-org/GLM-5.3", "ctx": 204800},
            {"id": "tencent/Hy4-preview", "ctx": 262144},
        ],
        "note": {"zh": "一个 Key 聚合多家开源模型，模型广场里复制完整模型名即可", "en": "One key for many open models — copy the full model id from the model plaza."},
        "key_url": "https://cloud.siliconflow.cn/account/ak",
    },
    {
        "id": "qianfan",
        "name": "百度千帆（文心）",
        "base_url": "https://qianfan.baidubce.com/v2",
        "models": [
            {"id": "ernie-4.5-turbo-128k", "ctx": 131072},
            {"id": "deepseek-v3.1-250821", "ctx": 131072},
        ],
        "note": {"zh": "使用对全部应用有权限的 API Key；模型名以千帆控制台为准", "en": "Use a key with access to all apps; model names follow the Qianfan console."},
        "key_url": "https://console.bce.baidu.com/qianfan/ais/console/applicationConsole/application",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": [
            {"id": "gpt-6.1-sol", "ctx": 1050000},
            {"id": "gpt-6-astra", "ctx": 1050000},
            {"id": "gpt-6.1-terra", "ctx": 1050000},
            {"id": "gpt-6-luna", "ctx": 1050000},
        ],
        "note": {"zh": "国内需自备网络出口；默认 gpt-6.1-sol 兼顾质量与成本", "en": "Needs direct network access outside mainland China; gpt-6.1-sol balances quality and cost."},
        "key_url": "https://platform.openai.com/api-keys",
    },
    {
        "id": "anthropic",
        "name": "Claude（Anthropic）",
        "base_url": "https://api.anthropic.com/v1",
        "models": [
            {"id": "claude-sonnet-5-5", "ctx": 200000},
            {"id": "claude-opus-5-5", "ctx": 200000},
            {"id": "claude-haiku-5-5", "ctx": 200000},
        ],
        "note": {"zh": "走官方 OpenAI 兼容层；提示缓存等原生特性不可用", "en": "Uses the official OpenAI-compatible layer; native features such as prompt caching are unavailable."},
        "key_url": "https://console.anthropic.com/settings/keys",
    },
]

_BY_ID = OrderedDict((p["id"], p) for p in PROVIDERS)

# 对外暴露时去掉内部字段(当前没有, 留作扩展), 深拷贝避免前端/调用方改到常量
_PUBLIC_KEYS = ("id", "name", "base_url", "models", "note", "key_url")


def list_providers():
    """返回可直接 JSON 化的厂商列表。"""
    out = []
    for p in PROVIDERS:
        item = {k: p[k] for k in _PUBLIC_KEYS if k in p}
        item["models"] = [dict(m) for m in p.get("models") or []]
        out.append(item)
    return out


def get_provider(pid):
    p = _BY_ID.get(str(pid or "").strip())
    if not p:
        return None
    item = {k: p[k] for k in _PUBLIC_KEYS if k in p}
    item["models"] = [dict(m) for m in p.get("models") or []]
    return item


def default_model(pid):
    p = _BY_ID.get(str(pid or "").strip())
    ms = (p or {}).get("models") or []
    return ms[0]["id"] if ms else ""


def build_profile(pid, model="", api_key="", context=None):
    """按厂商预设 + 用户 Key 生成 profile(dict)。厂商不存在返回 None。"""
    p = _BY_ID.get(str(pid or "").strip())
    if not p:
        return None
    model = str(model or "").strip() or default_model(pid)
    prof = {"provider": p["id"], "base_url": p["base_url"],
            "api_key": str(api_key or "").strip(), "model": model}
    ctx = context
    if not ctx:
        for m in p.get("models") or []:
            if m["id"] == model:
                ctx = m.get("ctx")
                break
    if ctx:
        try:
            prof["context"] = int(ctx)
        except (TypeError, ValueError):
            pass
    return prof
