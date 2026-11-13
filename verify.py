# -*- coding: utf-8 -*-
"""EZcode 确定性流程校验门 (Verification Gate)
课程对齐: aiengineeringfromscratch.com Phase 14.38 —— 核心理念:
  "The agent does not get to mark its own work as done."
  校验器从证据确定性重算, 不采信模型的自我声明(流程执行清单由模型自己填=agent给自己打分)。
  不用LLM裁判(确定性), block级发现=结论未通过校验。

设备无关设计: 情报/资产核查按 caps.py 的"能力"判定(不写死工具名),
接入新安全设备时在 caps.py 注册能力即可, 本文件零改动。

三条硬判据映射:
  G1 [block] 误报定性必须有报文级/主机侧证据 (判据R-001)
  G2 [block] 肯定性封禁建议必须有威胁情报能力工具的查询记录 (判据R-008/R-013)
  G3 [warn]  处置调用被拒绝(未确认) (判据R-008)
  G4 [warn]  告警中的公网IP未全部做资产归属核查 (纪律3)
"""
import re
import caps
# 情报/资产能力工具在 verify_round 内动态从 caps 注册表现算——
# 新设备任何时候注册(含运行时)都立即生效, 本文件零改动。

# 高危告警类型关键词(触发报文级铁律)
HIGH_RISK_KEYWORDS = ("注入", "命令执行", "代码执行", "webshell", "web shell",
                      "反弹", "挖矿", "矿池", "stratum", "敏感文件", "失陷", "后门", "木马")
# 报文级证据特征(出现在工具结果/用户提供的payload中)
EVIDENCE_MARKERS = ("packet_decoded", "hexdump", "http_req_body", "http_rsp_body",
                    "uri_path", "attack_character", "ascii_strings")
# 主机侧直接证据特征(主机侧详情)
HOST_EVIDENCE_MARKERS = ("detection_msg", "process_tree", "detection_process")
# 用户自贴payload特征
USER_PAYLOAD_MARKERS = ("payload", "上行", "下行", "GET /", "POST /", "HTTP/", "0000  ")

# 否定性"封禁"语境: 结论明确说不需要/没有封禁, 或只是声明"封禁由用户手动执行"
# (这类句子出现"封禁"二字不代表给出了封禁建议, 不能触发G2 block)
_NEG_BAN = re.compile(
    r"无(?:需|须|有)?封禁|不(?:需要|建议|要|用|宜|得|予|会|做|进行|应)?封禁|"
    r"禁止封禁|不得.{0,6}封禁|无需.{0,6}封禁|未(?:给出|提出).{0,6}封禁|"
    r"封禁(?:项|建议|措施)?\s*(?:为|是|：|:)?\s*无|不(?:建议|应|宜).{0,8}封禁")
# 肯定性封禁建议: 建议/应/可/维持/续封等 + 封禁, 或"封禁该IP"这类明确动作
_POS_BAN = re.compile(
    r"(?:建议|应|宜|需|须|可|予以|进行|维持|继续|续|申请|考虑|给出|做)[^。；\n]{0,10}封禁"
    r"|封禁(?:该|此|源|攻击|外网|恶意)?\s*IP|封禁(?:建议|处置|措施)|续封")
# 否定性"误报"语境(避免"不是误报/倾向真阳性"被当成误报定性)
_NEG_FP = re.compile(r"非误报|不是误报|不属于误报|并非误报|排除误报|"
                     r"不(?:能|应|宜)?(?:判定|认定|定为).{0,6}误报|倾向真阳性")

_PUB_IP = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b")


def _split_sentences(text):
    return re.split(r"[\n。；;！!？?]+", text or "")


def has_ban_advice(answer):
    """answer 是否真的给出了'肯定性封禁建议'(按句判断, 否定语境优先)。
    裸的 '封禁' in answer 会把'无封禁项/不需要封禁/封禁由用户手动执行'误判成建议,
    进而给整份结论盖BLOCK章并污染后续对话——G2 是 block 级, 宁可漏报不可误伤。"""
    text = answer or ""
    # 旧版本把校验横幅拼进了回答正文, 横幅里的"封禁建议"是系统文案而非模型建议;
    # 先剥离, 避免用横幅自己给自己二次判错(旧会话/粘贴文本兼容)。
    cut = text.find("── 流程校验")
    if cut >= 0:
        text = text[:cut]
    for seg in _split_sentences(text):
        if "封禁" not in seg:
            continue
        if _NEG_BAN.search(seg):
            continue
        if _POS_BAN.search(seg):
            return True
    return False


def claims_false_positive(answer):
    """answer 是否真的下了'误报'定性(排除'不是误报/排除误报')。"""
    for seg in _split_sentences(answer):
        if "误报" in seg and not _NEG_FP.search(seg):
            return True
    return False
# 内网/非公网网段: RFC1918 + 回环 + 链路本地 + 运营商级NAT(100.64.0.0/10)
_PRIVATE = re.compile(
    r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|127\.|0\.|169\.254\."
    r"|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.)")


def _is_public(ip):
    return not _PRIVATE.match(ip)


def verify_round(tool_log, messages, answer):
    """确定性校验一轮研判。tool_log=[{name,args,result_head}], messages=会话, answer=最终回答。
    返回 {"passed": bool, "findings": [{"code","severity","text"}]}"""
    findings = []
    called = set()
    asset_ips = set()          # 已发起过资产归属核查的IP(来自任意asset能力工具的查询参数)
    intel_tools = set(caps.tools_with("intel"))
    asset_tools = set(caps.tools_with("asset"))
    result_text = ""
    for t in tool_log:
        called.add(t.get("name"))
        head = str(t.get("result_head") or "")
        result_text += head + "\n"
        if t.get("name") in asset_tools:
            # 宽容提取: 查询参数文本里出现的所有IPv4都算"已发起核查"
            # (新设备的资产工具参数名可能不同, 按文本提取比解析JSON字段稳)
            asset_ips.update(_PUB_IP.findall(str(t.get("args") or "")))

    user_text = "\n".join(str(m.get("content") or "") for m in messages
                          if m.get("role") == "user")

    evidence_seen = (any(mk in result_text for mk in EVIDENCE_MARKERS)
                     or any(mk in user_text for mk in USER_PAYLOAD_MARKERS))
    host_evidence = any(mk in result_text for mk in HOST_EVIDENCE_MARKERS)
    # 高危关键词: 用户消息与工具结果都扫(批量分诊场景告警名常在工具返回里, 不在用户输入里)
    high_risk = (any(k in user_text for k in HIGH_RISK_KEYWORDS)
                 or any(k in result_text for k in HIGH_RISK_KEYWORDS))
    fp_claim = claims_false_positive(answer)

    # ---- G1: 高危告警误报定性必须有报文级/主机侧证据 ----
    if fp_claim and high_risk and not (evidence_seen or host_evidence):
        findings.append({
            "code": "G1/R-001", "severity": "block",
            "text": "误报定性但本轮未见报文级证据(未下钻evidence或无用户提供的payload)且无主机侧详情——"
                    "违反报文级铁律R-001。补救: 补报文证据后重判; 批量分诊场景可对缺证据的"
                    "单条告警降级为[待确认]并列入'需深挖清单'后重述结论(其余有证据的条目不受影响)。"})

    # ---- G2: 肯定性封禁建议必须有威胁情报查询记录 ----
    # 只认"建议封禁X"这类肯定建议; "无封禁项/不需要封禁/封禁由用户手动执行"是
    # 否定或流程声明, 不属于处置建议(旧版裸字符串匹配会误伤并盖BLOCK章)。
    # 情报工具集按caps.py能力推导——新情报源接入后自动纳入判定, 本文件零改动。
    if has_ban_advice(answer) and not (called & intel_tools):
        findings.append({
            "code": "G2/R-008", "severity": "block",
            "text": "给出封禁建议但本轮无威胁情报查询记录(%s未调用)——"
                    "违反封禁双确认R-008/R-013(情报+行为缺一不可)。"
                    % (", ".join(sorted(intel_tools)) or "情报能力")})

    # ---- G3: 处置调用曾被拒绝 ----
    if '"refused": true' in result_text or '"refused": True' in result_text:
        findings.append({
            "code": "G3/R-008", "severity": "warn",
            "text": "本轮存在被拒绝的写操作调用(未获用户确认)。若最终结论依赖该操作, 请让用户明确确认后重试。"})

    # ---- G4: 告警中的公网IP未全部核查归属 ----
    pub_ips = {ip for ip in _PUB_IP.findall(user_text) if _is_public(ip)}
    missing = pub_ips - asset_ips
    # 豁免: 用户消息太短(非告警研判场景)
    if len(user_text) > 30 and missing:
        findings.append({
            "code": "G4/纪律3", "severity": "warn",
            "text": "告警中的公网IP %s 未做资产归属核查(核查无记录≠不存在, 但按纪律所有IP必查)。"
                    % ", ".join(sorted(missing)[:5])})

    return {"passed": not any(f["severity"] == "block" for f in findings),
            "findings": findings}


def gate_banner(gate):
    """渲染校验结果横幅(追加到最终回答)"""
    lines = ["", "── 流程校验(确定性, 非模型自评) ──"]
    for f in gate["findings"]:
        mark = "✗ BLOCK" if f["severity"] == "block" else "⚠ WARN"
        lines.append("%s %s: %s" % (mark, f["code"], f["text"]))
    if gate["passed"]:
        lines.append("✓ 校验通过" + ("(含%d条提醒)" % len(gate["findings"]) if gate["findings"] else ""))
    else:
        lines.append("✗ 结论未通过校验 —— 定性应标注'待确认'或补证据后重判, 勿直接引用")
    return "\n".join(lines)
