"use strict";
/* ═══════════ EZcode 桌面客户端前端 ═══════════
   单通道事件: Python evaluate_js("window.ezcodeBridge.event(payload)") → onEvent 分发
   所有动态文本均先 esc() 再入DOM, 工具/权限内容不执行任何注入的HTML */

/* ---------- mini 工具 ---------- */
const $ = s => document.querySelector(s);
/* 语言: 文案由 i18n.js 的 tr() 翻译; 语言包缺失时退化为原文, 绝不让界面白屏 */
const T = s => (typeof tr === "function" ? tr(s) : s);
const CUR_LANG = () => (typeof currentLang === "function" ? currentLang() : "zh");
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
/* 消息容器兜底: 即使HTML结构缺了 .stream 也能自建, 不让整个消息流哑火 */
function streamBox(){
  let s = $("#chat .stream");
  if(!s){
    s = document.createElement("div");
    s.className = "stream";
    const chat = $("#chat");
    if(chat) chat.appendChild(s);
  }
  return s;
}

/* ---------- Markdown 渲染 ----------
   渲染质量规则(吸取旧版排版错乱教训):
   1. 行内code/代码token先抽成占位符再染其它元素, 防止正则互相二次替换污染
   2. 代码高亮先抽字符串/注释, 再对剩余文本染关键字/数字, 不产生嵌套span */
function mdInline(s){
  const codes = [];
  let t = esc(s).replace(/`([^`]+)`/g, (m, c) => {
    codes.push(c); return "\x00C" + (codes.length - 1) + "\x00";
  });
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
       .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
       .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
                '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return t.replace(/\x00C(\d+)\x00/g, (m, i) => "<code>" + codes[+i] + "</code>");
}
function splitRow(l){
  return l.replace(/^\s*\|/,"").replace(/\|\s*$/,"").split("|").map(x=>x.trim());
}
function indentOf(l){ return (l.match(/^\s*/) || [""])[0].length; }
/* 嵌套列表: 按缩进递归渲染(旧版把子列表拍平成同级, 排版混乱的主因之一) */
function renderList(items){
  if(!items.length) return "";
  const ind = indentOf(items[0]);
  const ordered = /^\s*\d/.test(items[0]);
  const tag = ordered ? "ol" : "ul";
  let html = "<" + tag + ">";
  let i = 0;
  while(i < items.length){
    const text = items[i].replace(/^\s*([-•*]|\d+[.)])\s+/, "");
    let j = i + 1;
    while(j < items.length && indentOf(items[j]) > ind) j++;   // 本项的子层块
    html += "<li>" + mdInline(text)
          + (j > i + 1 ? renderList(items.slice(i + 1, j)) : "")
          + "</li>";
    i = j;
  }
  return html + "</" + tag + ">";
}
/* 代码高亮: 先在原始文本上标记token区间, 再对非token段esc, 最后包span。
   不能"esc之后再正则"——esc 产生的 &#39; &quot; 实体会被注释/字符串正则误匹配
   (旧版单引号字符串整段被染成注释色的根源)。token 优先级: 注释>字符串>关键字>数字。 */
function hlCode(code, lang){
  const raw = String(code ?? "");
  const l = (lang||"").toLowerCase();
  if(l === "html" || l === "xml" || l === "markdown" || l === "md") return esc(raw);
  const hashLangs = ["python","py","bash","sh","shell","powershell","ps1","yaml","yml","json","ini","conf","dockerfile","makefile",""];
  const slashLangs = ["javascript","js","java","c","cpp","c++","go","rust","ts","typescript","php"];
  const hasHash = hashLangs.includes(l) || l.includes("json") || l.includes("yaml");
  const hasSlash = slashLangs.some(x => l === x);
  const marks = [];                       // [start, end, class]
  const scan = (re, cls) => {
    re.lastIndex = 0;
    let m, guard = 0;
    while((m = re.exec(raw)) && guard++ < 5000){
      const s = m.index, e = s + m[0].length;
      const hit = marks.find(([a, b]) => s < b && e > a);
      if(hit){
        /* 撞上更高优先级的token(如字符串里的#): 从该token末尾继续找,
           让"字符串后的真注释"也能被染色, 而不是整段被拒。 */
        if(hit[1] >= raw.length) break;
        re.lastIndex = hit[1];            // 从重叠token结束处重搜(不能用max, 会跳过后文)
        continue;
      }
      marks.push([s, e, cls]);
      if(re.lastIndex === m.index) re.lastIndex++;
    }
  };
  /* 字符串先于注释: Python字符串里 '#' 很常见, 反过来会把半个字符串染成注释 */
  scan(/"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'/g, "tok-s");
  if(hasHash)  scan(/#[^\n]*/g, "tok-c");
  if(hasSlash) scan(/\/\/[^\n]*/g, "tok-c");
  scan(/\b(def|class|import|from|return|if|elif|else|for|while|try|except|finally|with|as|in|not|and|or|is|pass|raise|del|global|assert|break|continue|None|True|False|lambda|yield|async|await|function|const|let|var|new|echo|export|sudo|package|func|struct|impl|pub|use|fn|typeof|instanceof)\b/g, "tok-k");
  scan(/\b\d+(?:\.\d+)?\b/g, "tok-n");
  marks.sort((a, b) => a[0] - b[0]);
  let out = "", pos = 0;
  for(const [s, e, cls] of marks){
    if(s < pos) continue;
    out += esc(raw.slice(pos, s))
         + '<span class="' + cls + '">' + esc(raw.slice(s, e)) + '</span>';
    pos = e;
  }
  return out + esc(raw.slice(pos));
}
function renderMD(src){
  src = String(src ?? "").replace(/\r\n/g,"\n");
  const lines = src.split("\n");
  const out = []; let i = 0, m;
  while(i < lines.length){
    const L = lines[i];
    /* 围栏代码块 */
    if(/^```/.test(L)){
      const lang = L.slice(3).trim(); const buf = []; i++;
      while(i < lines.length && !/^```/.test(lines[i])){ buf.push(lines[i]); i++; }
      i++;
      const code = buf.join("\n");
      const id = "cpx" + Math.random().toString(36).slice(2,8);
      out.push(`<pre><div class="code-head"><span>${esc(lang||"text")}</span>`+
        `<span class="code-copy" data-code-id="${id}">复制</span></div>`+
        `<code id="${id}">${hlCode(code, lang)}</code></pre>`);
      continue;
    }
    /* 表格 */
    if(/\|/.test(L) && i+1 < lines.length && /^\s*\|?[\s:\-|]+\|[\s:\-|]*$/.test(lines[i+1])){
      const hCells = splitRow(L);
      const align = splitRow(lines[i+1]).map(x =>
        /:-+:/.test(x) ? "center" : /-+:$/.test(x) || /-+:/.test(x) && x.trim().endsWith(":") ? "right" : "left");
      i += 2; const rows = [];
      while(i < lines.length && /\|/.test(lines[i]) && lines[i].trim()){ rows.push(splitRow(lines[i])); i++; }
      /* 外包一层 .md-table: 表格撑满内容宽度, 超宽时横向滚动(裸 table 用 display:block 会塌成内容宽度) */
      out.push('<div class="md-table"><table><thead><tr>' +
        hCells.map((h,j)=>`<th style="text-align:${align[j]||"left"}">${mdInline(h)}</th>`).join("") +
        "</tr></thead><tbody>" +
        rows.map(r=>"<tr>"+r.map((c,j)=>`<td style="text-align:${align[j]||"left"}">${mdInline(c)}</td>`).join("")+"</tr>").join("") +
        "</tbody></table></div>");
      continue;
    }
    /* 标题 */
    if((m = L.match(/^(#{1,4})\s+(.*)$/))){
      out.push(`<h${m[1].length}>${mdInline(m[2])}</h${m[1].length}>`); i++; continue;
    }
    /* 分隔线 */
    if(/^\s*(---+|\*\*\*+)\s*$/.test(L)){ out.push("<hr>"); i++; continue; }
    /* 引用 */
    if(/^\s*>\s?/.test(L)){
      const buf = [];
      while(i < lines.length && /^\s*>\s?/.test(lines[i])){ buf.push(lines[i].replace(/^\s*>\s?/,"")); i++; }
      out.push("<blockquote>" + buf.map(x=>mdInline(x)).join("<br>") + "</blockquote>"); continue;
    }
    /* 列表(无序/有序/嵌套统一处理) */
    if(/^\s*([-•*]|\d+[.)])\s+/.test(L)){
      const items = [];
      while(i < lines.length && /^\s*([-•*]|\d+[.)])\s+/.test(lines[i])){
        items.push(lines[i]); i++;
      }
      out.push(renderList(items)); continue;
    }
    /* 空行 */
    if(!L.trim()){ i++; continue; }
    /* 段落 */
    const para = [L]; i++;
    while(i < lines.length && lines[i].trim() &&
          !/^(#{1,4}\s|```|\s*[-•*]\s|\s*\d+[.)]\s|\s*>|\s*---)/.test(lines[i]) &&
          !(/\|/.test(lines[i]))){
      para.push(lines[i]); i++;
    }
    out.push("<p>" + mdInline(para.join("\n")).replace(/\n/g,"<br>") + "</p>");
  }
  return out.join("\n");
}
/* 代码块复制(事件委托) */
document.addEventListener("click", e => {
  const btn = e.target.closest(".code-copy");
  if(!btn) return;
  const el = document.getElementById(btn.dataset.codeId);
  if(el) copyText(el.textContent).then(()=>toast("代码已复制","ok"));
});

/* ---------- 状态 ---------- */
const ui = {
  busy:false,
  sid:null,            // 当前激活任务 id; 后台任务的事件进缓存, 不渲染到当前对话
  persona:"ezcode",
  personas:[],             // 专家清单缓存(personas_list)
  streamEl:null,          // 流式正文DOM(正文buffer挂在其 _raw 上)
  thinkEl:null,           // 思考面板
  thinkOpen:false,
  lastToolCard:null,
  autoScroll:true,
  renderTimer:null,
  confirmOpen:false,
  confirmQueue:[],        // 同时来了多条确认时串行弹(任何任务的确认都会立即弹, 不排队等切回)
  taskBuf:{},             // sid -> 后台轮次事件摘要, 切回该任务时补渲染
  taskMeta:{},            // sid -> {busy, done, stage, title, text, think, tools[]}
  lastListRefresh:0,
};

function toast(msg, sev="ok", ms=3200){
  const t = document.createElement("div");
  t.className = "toast " + sev; t.textContent = T(msg);
  $("#toasts").appendChild(t);
  setTimeout(()=>{ t.style.opacity="0"; t.style.transition="opacity .25s";
                  setTimeout(()=>t.remove(), 260); }, ms);
}
function copyText(text){
  if(navigator.clipboard && navigator.clipboard.writeText)
    return navigator.clipboard.writeText(String(text||""));
  return new Promise((res,rej)=>rej());
}
/* 跟随滚动状态: 必须声明在使用点之前(let 有暂时性死区, 放到文末会踩)*/
let _progTo = -1;       // 程序滚动的目标 scrollTop(识别"这行是程序滚的")
let _lastFollowAt = 0;  // 节流时间戳(约一帧一次, 不依赖 rAF)
let _followTimer = 0;   // 被节流挡下时的尾随补偿

/* ---------- 视口锚定 ----------
   流式渲染是每 90ms 全量替换 body 的 innerHTML, 思考面板折叠、工具卡插入也会
   改变文档高度。这些突变会让浏览器 clamp/重排 scrollTop, 用户正在看的位置就被
   拽走了(典型现象: 上翻看中间过程, 一下回到提问处)。这里在突变前后锚定
   "视口顶部第一条消息 + 它相对视口的偏移", 突变后还原, 与高度变化多少无关。 */
function captureAnchor(){
  const chat = $("#chat");
  if(!chat) return null;
  const items = chat.querySelectorAll(".stream > *");
  const cy = chat.getBoundingClientRect().top;
  for(let i = 0; i < items.length; i++){
    const r = items[i].getBoundingClientRect();
    if(r.bottom > cy + 2) return {i, off: r.top - cy};
  }
  return null;
}
function restoreAnchor(a){
  if(!a) return;
  const chat = $("#chat");
  if(!chat) return;
  const items = chat.querySelectorAll(".stream > *");
  const el = items[a.i];
  if(!el) return;                                  // 目标被移除: 放弃(不乱跳)
  const cy = chat.getBoundingClientRect().top;
  const delta = (el.getBoundingClientRect().top - cy) - a.off;
  if(Math.abs(delta) > 0.5) chat.scrollTop += delta;   // 反方向补偿, 视觉位置不动
}

function scrollBottom(force){
  const chat = $("#chat");
  if(!chat) return;
  if(!(force || ui.autoScroll)) return;
  if(Date.now() < _userLockUntil) return;      // 用户刚上翻: 保护期内不抢视口
  /* 程序跟随: 记录目标 scrollTop, scroll 监听器据此识别"这行是程序滚的"并跳过。
     不用"两帧窗口"标记——窗口切到后台时 rAF 不跑, 标记会永久卡在 true,
     回来后的用户滚动全被当成程序滚动, 表现就是"视口停在离开处不动"。
     非 force 时用 rAF 节流(流式高频调用每帧最多滚一次)。 */
  const doScroll = ()=>{
    const c = $("#chat");
    if(!c) return;
    const target = c.scrollHeight - c.clientHeight;
    if(target - c.scrollTop <= 2) return;          // 已在底部
    _progTo = target;
    c.scrollTop = target;                          // 直接赋值: 不继承 CSS smooth, 不会变被打断的动画
  };
  if(force){ doScroll(); return; }
  /* 节流用时间戳而非 requestAnimationFrame: 窗口不可见时 rAF 会被浏览器暂停,
     跟随滚动就再也不会执行(切到别的工具再回来, 视口停在离开处就是这个原因)。
     被节流挡下时必须"尾随补一次"——否则流式最后一帧的高度增长会被丢掉,
     视口永远差最后几十像素到底。 */
  const now = Date.now();
  const wait = 16 - (now - _lastFollowAt);
  if(wait > 0){
    clearTimeout(_followTimer);
    _followTimer = setTimeout(()=>{ _lastFollowAt = Date.now(); doScroll(); }, wait);
    return;
  }
  _lastFollowAt = now;
  doScroll();
}
function syncJumpBtn(){
  const chat = $("#chat"), b = $("#jump-bottom");
  if(!b) return;
  b.classList.toggle("show",
    !ui.autoScroll && chat.scrollHeight - chat.clientHeight > 60);
}

/* ---------- 空任务欢迎页 ----------
   与消息流分离的独立层, 不参与 stream 清空/滚动跟随; 有任意消息时自动隐藏。*/
function showWelcome(){ const w = $("#welcome"); if(w) w.classList.add("show"); }
function hideWelcome(){ const w = $("#welcome"); if(w) w.classList.remove("show"); }

/* ---------- 消息渲染 ---------- */
function addUser(text){
  hideWelcome();
  const d = document.createElement("div");
  d.className = "msg user";
  d.innerHTML = `<div class="bubble"></div>`;
  d.querySelector(".bubble").textContent = text;
  streamBox().appendChild(d);
  scrollBottom(true);
  return d;                      // 供发送失败时回滚标注
}
function addSys(text){
  hideWelcome();
  const d = document.createElement("div");
  d.className = "msg sys";
  d.innerHTML = `<div class="bubble"></div>`;
  d.querySelector(".bubble").textContent = text;
  streamBox().appendChild(d);
  scrollBottom();            // 系统消息也要跟随(旧版不滚, 轮次收尾的"用时"提示会把底部悬空43px)
}
function newBotShell(){
  hideWelcome();
  const d = document.createElement("div");
  d.className = "msg bot";
  d.innerHTML = `<div class="avatar">🛡</div>
    <div class="content">
      <div class="msg-tools-row"><span></span><button class="copy-btn">复制全文</button></div>
      <div class="body md"></div>
    </div>`;
  d.querySelector(".copy-btn").onclick = ()=>{
    copyText(d._raw || "").then(()=>toast("已复制全文","ok"));
  };
  streamBox().appendChild(d);
  return d;
}
function addBotFull(text){
  const d = newBotShell();
  d._raw = text;
  d.querySelector(".body").innerHTML = renderMD(text);
  scrollBottom();
  return d;
}
function addError(text){
  const d = document.createElement("div");
  d.className = "msg bot";
  d.innerHTML = `<div class="avatar" style="background:linear-gradient(135deg,#ef4444,#f97316)">✕</div>
    <div class="content"><div class="errbox"></div></div>`;
  d.querySelector(".errbox").textContent = text;
  streamBox().appendChild(d);
  scrollBottom();
}

/* ---------- 思考面板 ---------- */
function thinkStart(){
  const d = document.createElement("details");
  d.className = "think"; d.open = true;
  d.innerHTML = `<summary>思考中…</summary><div class="think-body"></div>`;
  d.addEventListener("toggle", ()=>{ ui.thinkOpen = d.open; });
  /* 用户一旦手动滚动思考区, 后续帧就不再抢内部滚动位置 */
  const tb = d.querySelector(".think-body");
  tb.addEventListener("wheel", ()=>{ ui.thinkUserScrolled = true; }, {passive:true});
  ui.thinkUserScrolled = false;
  ui.thinkEl = d;
  const stream = streamBox();
  stream.appendChild(d);
  scrollBottom();
}
function thinkDelta(text, chars){
  if(!ui.thinkEl) thinkStart();
  const body = ui.thinkEl.querySelector(".think-body");
  body.textContent = text;
  /* 内部滚动只在用户没动过时自动跟随(默认看最新); 用户手动滚过就不再抢 */
  if(!ui.thinkUserScrolled) body.scrollTop = body.scrollHeight;
  ui.thinkEl.querySelector("summary").textContent = `思考中… ${chars}字`;
  /* 不再强制 open: 用户折叠了就保持折叠, 别每帧又给他弹开 */
  scrollBottom();
}
function thinkEnd(chars){
  if(!ui.thinkEl) return;
  /* 不再强制折叠 —— 用户正在看思考过程时把面板折掉, 会让文档高度骤降、
     视口被浏览器 clamp 到很上面(表现就是"一下回到提问的地方")。
     折叠与否完全交给用户, 程序只更新标题文案。 */
  const anchor = ui.autoScroll ? null : captureAnchor();
  ui.thinkEl.querySelector("summary").textContent = `✦ 思考过程(${chars}字)`;
  ui.thinkEl = null;
  if(anchor) restoreAnchor(anchor);
}

/* ---------- 流式正文 ----------
   吞字修复: 正文buffer挂到当前流的DOM元素(_raw)上, 而不是全局共享。
   旧版全局buffer在轮次结束时不清空, 下一轮工具/正文交错时会把模型上一段
   说明文字直接覆盖掉(只有最后一段保留), 用户追问时看起来像"文字被吃掉"。 */
function textStart(){
  if(ui.streamEl) return;
  ui.streamEl = newBotShell();
  ui.streamEl._raw = "";
  scheduleRender();
}
function textDelta(full){
  if(!ui.streamEl) textStart();
  ui.streamEl._raw = String(full || "");
  scheduleRender();
  scrollBottom();
}
function scheduleRender(){
  if(ui.renderTimer) return;
  ui.renderTimer = setTimeout(()=>{
    ui.renderTimer = null;
    if(!ui.streamEl) return;
    const html = renderMD(ui.streamEl._raw || "") + '<span class="cursor"></span>';
    const anchor = ui.autoScroll ? null : captureAnchor();
    ui.streamEl.querySelector(".body").innerHTML = html;
    if(anchor) restoreAnchor(anchor);   // 用户在看中间: 重排后把视口钉回原处
    scrollBottom();          // 渲染落地后再跟随一次: DOM高度在渲染后才长高,
  }, 90);                    // 只在textDelta里滚会滞后一拍, 视口停不到底
}
function textEnd(){
  if(ui.renderTimer){ clearTimeout(ui.renderTimer); ui.renderTimer = null; }
  const el = ui.streamEl;
  ui.streamEl = null;
  if(!el) return;
  el.querySelector(".body").innerHTML = renderMD(el._raw || "");
  scrollBottom();
}

/* ---------- 工具卡(显示档位: hidden隐藏 / compact紧凑一行 / detailed详细卡片) ---------- */
const TOOL_VIEWS = [
  {id: "compact",   label: "紧凑(一行, 点击展开)", btn: "🧾 紧凑", short: "紧凑"},
  {id: "detailed",  label: "详细(完整卡片)",       btn: "📋 详细", short: "详细"},
  {id: "hidden",    label: "隐藏(顶部仅运行提示)", btn: "🙈 隐藏", short: "隐藏"},
];
function toolViewMode(){
  let m = localStorage.getItem("ezcode-toolview") || "compact";
  return TOOL_VIEWS.some(v => v.id === m) ? m : "compact";
}
/* 工具返回的原始 JSON 一律不进对话 DOM: 里面可能有 api_key/secret/token 等明文,
   点开工具卡也只看得到"调了什么", 看不到"返回了什么"; 排查走会话日志。 */
const SENSITIVE_KV = /("?(?:api_key|apikey|secret|token|password|passwd|pwd|authorization|credential|private_key|access_key|session_id)"?\s*[:=]\s*)("[^"]*"|'[^']*'|[^,}\]\s]+)/gi;
function redact(s){
  return String(s == null ? "" : s).replace(SENSITIVE_KV, "$1***").slice(0, 300);
}
function toolSummary(name, args){
  let keys = [];
  try{ keys = Object.keys(JSON.parse(String(args || "{}")) || {}); }catch(e){}
  /* 只列工具名与参数键名(不列值), 纯符号无语言问题, 也不含任何返回数据 */
  return name + (keys.length ? " · " + keys.join(", ") : "");
}
function toolCard(name, args){
  textEnd();                     // 封版上一段说明文字: 工具调用前的话保留为独立气泡,
                                 // 否则会被下一段正文流覆盖(旧版吞字根因之一)
  if(toolViewMode() === "hidden"){
    ui.lastToolCard = null;       // 隐藏档: 不建卡; 运行中由顶部runbar提示, 结果由轮次统计行留痕
    return;
  }
  const compact = toolViewMode() === "compact";
  const d = document.createElement("div");
  d.className = "toolcard" + (compact ? " compact" : "");
  d.innerHTML = `
    <div class="t-head">
      <span class="t-icon">⚙</span>
      <span class="t-name"></span>
      <span class="t-args"></span>
      <span class="t-stat"><span class="tspin"></span>${T("运行中")}</span>
    </div>
    <div class="t-body" style="display:none"></div>`;
  d.querySelector(".t-name").textContent = name;
  d.querySelector(".t-args").textContent = compact ? "" : redact(String(args||"").slice(0,150));
  d.querySelector(".t-head").title = T("点击查看调用概要(不显示返回内容)");
  d._args = String(args||"");
  d._summary = toolSummary(name, args);
  d.querySelector(".t-head").onclick = ()=>{
    const b = d.querySelector(".t-body");
    const a = d.querySelector(".t-args");
    const open = b.style.display !== "none";
    b.style.display = open ? "none" : "block";
    b.textContent = d._summary;      /* 只写调用概要, 绝不写返回结果 */
    if(compact) a.textContent = open ? "" : redact(String(d._args||"").slice(0,150));
  };
  ui.lastToolCard = d;
  streamBox().appendChild(d);
  scrollBottom();
}
function toolDone(o){
  const d = ui.lastToolCard;
  if(!d) return;
  const stat = d.querySelector(".t-stat");
  stat.innerHTML = "";
  if(o.ok){
    stat.className = "t-stat ok";
    stat.textContent = `✓ ${o.dt}s`;
  }else{
    /* 工具失败不在对话里铺红色错误: 只留一个中性小标记, 失败原因由模型在回答里说明 */
    stat.className = "t-stat dim";
    stat.textContent = `${o.dt}s 未取到结果`;
    d.classList.add("tool-failed");
    d.querySelector(".t-args").textContent = "";
  }
  /* 不把 o.preview / o.err(工具返回原始JSON, 可能含密钥)写进 DOM。
     完整结果仍写进会话日志, 需要排查时去日志看。 */
  d.querySelector(".t-body").textContent = d._summary || String(o.name || "tool");
  ui.lastToolCard = null;
  scrollBottom();
}
function cycleToolView(){
  const cur = toolViewMode();
  const next = TOOL_VIEWS[(TOOL_VIEWS.findIndex(v => v.id === cur) + 1) % TOOL_VIEWS.length];
  localStorage.setItem("ezcode-toolview", next.id);
  applyToolViewBtn();
  toast("工具调用显示: " + next.label + (next.id === "hidden" ? "(运行时顶部仍有提示)" : ""), "ok");
}
function applyToolViewBtn(){
  const b = $("#btn-toolview");
  if(!b) return;
  const v = TOOL_VIEWS.find(x => x.id === toolViewMode());
  b.textContent = "🧾 " + T("工具显示") + " · " + T(v.short);
  b.title = T("工具调用只显示概要, 不展示返回内容(点击切换档位): 隐藏/紧凑/详细");
}
$("#btn-toolview").onclick = cycleToolView;

/* ---------- 验证门 ---------- */
function gateBanner(g){
  const d = document.createElement("div");
  d.className = "gate " + (g.passed ? "pass" : "block");
  const title = g.passed ? "流程校验通过(确定性校验, 非模型自评)" : "流程校验未通过";
  d.innerHTML = `<div class="g-title"></div>`;
  d.querySelector(".g-title").textContent = title;
  for(const f of (g.findings||[])){
    const it = document.createElement("div");
    it.className = "g-item";
    it.textContent = `${f.severity === "block" ? "✗" : "⚠"} ${f.code}: ${f.text}`;
    d.appendChild(it);
  }
  streamBox().appendChild(d);
  scrollBottom();
}

/* ---------- 运行状态条 ---------- */
function runbar(text){
  const bar = $("#runbar");
  if(text){ bar.classList.add("show"); $("#run-text").textContent = text; }
  else{ bar.classList.remove("show"); }
}

/* ---------- 权限确认弹窗 ---------- */
function confirmBox(o){
  ui.confirmOpen = true;
  const box = $("#modal");
  box.classList.remove("set-wide", "set-wide2");
  const from = (o.sid && o.sid !== ui.sid)
    ? `<div class="m-from">${esc(T("来自后台任务："))}${esc(o.title || (ui.taskMeta[o.sid]||{}).title || "?")}`
      + ` · ${esc(T("确认后该任务继续运行"))}</div>` : "";
  box.innerHTML = `
    <h3><span class="m-icon">⚠</span>权限确认 · ${esc(o.action)}</h3>
    ${from}
    <div class="m-detail"></div>
    <div class="m-btns">
      <button class="deny">拒绝</button>
      <button class="always">本会话总是允许</button>
      <button class="allow">允许这一次</button>
    </div>`;
  box.querySelector(".m-detail").textContent = o.detail || o.resource || "";
  const pick = choice => {
    ui.confirmOpen = false;
    $("#overlay").className = "overlay";
    pywebview.api.confirm_result(o.seq, choice);       /* 字符串直传 */
    setTimeout(()=>flushConfirms(), 0);                // 串行弹下一条待确认
  };
  box.querySelector(".deny").onclick = ()=>pick("deny");
  box.querySelector(".always").onclick = ()=>pick("always");
  box.querySelector(".allow").onclick = ()=>pick("once");
  $("#overlay").className = "overlay show";
}

/* ---------- 后台任务: 事件缓存 / 权限确认 / 切回补渲染 ----------
   后台任务的轮次在引擎里照常跑完。旧版把它的事件整条丢掉, 结果切回去看到
   一片空白、状态还锁在"运行中"(round_end 也被丢了)——体感就是"不思考了"。
   现在按 sid 缓存事件摘要, 切回时补出已生成的内容; 权限确认则立即弹窗,
   不再排队等切回(否则引擎会在确认超时前一直干等)。 */
function bgMeta(sid){
  return ui.taskMeta[sid] || (ui.taskMeta[sid] = {
    busy:false, done:false, title:"", text:"", think:"", thinkChars:0,
    stage:"", round:null, error:null
  });
}
function recordBackground(e){
  const m = bgMeta(e.sid);
  /* 轮次结束后引擎还会补发 title/notice(标题生成、上下文压缩), 这些事件不能
     把已结束的任务重新标成"运行中", 否则切回去又被锁住。 */
  if(!m.done && e.t !== "round_end" && e.t !== "error") m.busy = true;
  switch(e.t){
    case "text":      m.text = String(e.text || ""); break;
    case "think":     m.think = String(e.text || ""); m.thinkChars = e.chars || 0; break;
    case "think_end": m.thinkChars = e.chars || m.thinkChars; break;
    case "title":     m.title = e.title || ""; break;
    case "round_end": m.busy = false; m.done = true; m.round = e; break;
    case "error":     m.busy = false; m.done = true; m.error = e.text || ""; break;
  }
  const b = ui.taskBuf[e.sid] || (ui.taskBuf[e.sid] = []);
  b.push({t:e.t, chars:String(e.text || "").length});   // 只留摘要, 不存全文
  if(b.length > 200) b.shift();
}
function restoreBackground(sid){
  const m = ui.taskMeta[sid];
  if(!m) return;
  if(m.title) $("#chat-title").textContent = m.title;
  if(m.done){
    /* 轮次在别的任务激活时结束: 补做 roundEnd 的收尾, 否则界面锁在"运行中" */
    textEnd(); runbar(""); setBusy(false);
    if(m.round && m.round.n_tools !== undefined){
      addSys(T(`用时 ${m.round.dt}s · 工具 ${m.round.n_tools} 次${m.round.think ? " · 思考 " + m.round.think + " 字" : ""}`));
    } else if(m.error){ addError(m.error); }
    delete ui.taskMeta[sid]; delete ui.taskBuf[sid];
    refreshSessions();
    return;
  }else if(m.busy){
    /* 仍在跑: 补出当前已生成的正文/思考, 后续实时事件会继续覆盖同一流式DOM */
    if(m.think) thinkDelta(m.think, m.thinkChars);
    if(m.text) textDelta(m.text);
    setBusy(true);
    runbar(T("后台任务继续运行中…"));
  }
  /* 已补渲染, 缓存清掉; 此后由实时事件接管, 再切走时重新累积 */
  delete ui.taskMeta[sid]; delete ui.taskBuf[sid];
}
function throttleSessions(){
  const now = Date.now();
  if(now - ui.lastListRefresh < 900) return;
  ui.lastListRefresh = now;
  refreshSessions();
}
function flushConfirms(sid){
  if(ui.confirmOpen || !ui.confirmQueue.length) return;
  let i = sid ? ui.confirmQueue.findIndex(x => x.sid === sid) : 0;
  if(i < 0) i = 0;                       // 指定任务没有待确认时, 弹任意一条
  if(i >= ui.confirmQueue.length) return;
  confirmBox(ui.confirmQueue.splice(i, 1)[0]);
}

/* ---------- 事件单通道分发 ---------- */
/* 事件桥: 显式挂在 window 上, 避开下方 const ui 状态对象的全局词法绑定遮蔽。
   Python 用 evaluate_js("window.ezcodeBridge.event(payload)") 推送。 */
window.ezcodeBridge = { event: onEvent };
function onEvent(e){
  /* 多任务: 后台任务仍在跑, 但它的流式输出/工具卡/思考绝不落到当前对话 DOM。
     事件按 sid 缓存, 切回该任务时补渲染; 权限确认立即弹(标注来源任务),
     不再排队等切回——否则引擎会一直干等确认超时, 后台任务就真的停了。 */
  if(e.sid && ui.sid && e.sid !== ui.sid){
    if(e.t === "confirm"){
      ui.confirmQueue.push(e);
      flushConfirms();
    }else{
      recordBackground(e);
    }
    if(e.t === "round_end" || e.t === "error" || e.t === "title" || e.t === "tool"){
      throttleSessions();
    }
    return;
  }
  if(e.t === "think")       thinkDelta(e.text || "", e.chars || 0);
  else if(e.t === "think_end") thinkEnd(e.chars || 0);
  else if(e.t === "text")   textDelta(e.text || "");
  else if(e.t === "tool")   { toolCard(e.name, e.args); runbar("工具: " + e.name); }
  else if(e.t === "tool_done") toolDone(e);
  else if(e.t === "sub")    runbar(e.text || "");
  else if(e.t === "gate")   gateBanner(e);
  else if(e.t === "confirm") confirmBox(e);
  else if(e.t === "notice") { addSys(e.text || ""); }
  else if(e.t === "title")  { $("#chat-title").textContent = e.title; refreshSessions(); }
  else if(e.t === "error") { textEnd(); addError(e.text || "未知错误"); setBusy(false); runbar(""); refreshSessions(); }
  else if(e.t === "round_end") roundEnd(e);
  else if(e.t === "update") onUpdateEvent(e.data || {});
}
function roundEnd(e){
  textEnd();
  thinkEnd(e.think || 0);
  runbar("");
  if(e.cancelled){ addSys("已中止本轮"); }
  setBusy(false);
  if(e.ctx_pct !== undefined) setCtx(e.ctx_pct);
  if(e.n_tools !== undefined){
    addSys(T(`用时 ${e.dt}s · 工具 ${e.n_tools} 次${e.think ? " · 思考 " + e.think + " 字" : ""}`));
  }
  /* round_end 是轮次结束的权威信号; 拉取state时强制按空闲渲染,
     防止引擎状态尚未复位时把界面重新锁成"运行中" */
  pywebview.api.state().then(st=>{ st.busy = false; applyState(st); }).catch(()=>{});
  refreshSessions();
}

/* ---------- busy/状态 ---------- */
function setBusy(b){
  ui.busy = b;
  const dot = $("#busy-dot");
  dot.className = "busy-dot" + (b ? " busy" : "");
  dot.title = b ? T("运行中") : T("空闲");
  const btn = $("#sendbtn");
  btn.textContent = b ? "■" : "↑";
  btn.title = b ? "停止本轮运行" : "发送 (Enter)";
  btn.className = "send" + (b ? " stop" : "");
  if(!b) runbar("");
}
/* 上下文水位: 输入框工具栏里的百分比环 + 压缩菜单里的文字, 两处同源 */
function setCtx(pct){
  const n = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
  const t = $("#ctx-pct"); if(t) t.textContent = n + "%";
  const d = $("#ctx-detail"); if(d) d.textContent = "~" + n + "%";
  const ring = $("#ctx-ring"); if(ring) ring.style.setProperty("--p", n + "%");
  const chip = $("#btn-ctx");
  if(chip){ chip.classList.toggle("hot", n > 80);
            chip.title = "上下文占用约 " + n + "% · 点击查看 / 压缩"; }
  const c = $("#btn-compact"); if(c) c.classList.toggle("hot", n > 80);
}
/* 手动压缩上下文: 历史→AI纪要+保留最近对话 */
$("#btn-compact").onclick = ()=>{
  hideMenus();
  pywebview.api.compact_now().then(r=>{
    if(!r || r.ok === false)
      return toast((r && r.error) || "压缩失败", "warn");
    toast("正在压缩上下文…(完成后对话流里会有提示)", "ok", 4200);
  }).catch(()=>toast("压缩失败: 桥接异常", "err"));
};
/* ---------- 专家(persona)联动 ---------- */
const PERSONA_TEXT = {
  ezcode: {
    label: "安全运营",
    placeholder: "粘贴告警或描述任务，例如：研判今天 13:00 后未处理的告警…",
    hint: "Enter 发送 · Shift+Enter 换行 · @路径 附加文件证据 · 告警处置回写需切可执行模式",
    disclaim: "只做研判与建议 · 封禁/加白一律由你在防火墙手动执行 · 告警处置回写需逐次确认",
    welcome: "粘贴告警或直接说人话, EZcode 自动调用已接入的系统能力、跑行为链、检索知识库给出研判",
  },
  coder: {
    label: "通用编码",
    placeholder: "直接下任务，例如：帮我写个Python脚本统计 logs 目录下今天的会话数…",
    hint: "Enter 发送 · Shift+Enter 换行 · @路径 附加文件 · 写文件/跑命令需切可执行模式",
    disclaim: "只读模式可直接干活 · 写文件/跑命令请切可执行模式 · 命令执行逐次弹窗确认",
    welcome: "把任务交给 EZcode: 写代码、改文件、跑命令、分析数据。只读可直接干, "
             + "写文件/跑命令请先切到可执行模式",
  },
  assistant: {
    label: "日常工作助手",
    placeholder: "直接说事，例如：记一下明天上午开例会；帮我写一份周报框架…",
    hint: "Enter 发送 · Shift+Enter 换行 · @路径 附加材料 · 记待办/查资料无需可执行模式",
    disclaim: "写文档存盘需切可执行模式 · 待办清单只存本机 · 查资料只访问公开网页",
    welcome: "写作、总结、翻译、查资料、记待办都可以交给我; 试着说「记一下: 明天上午开例会」体验本地待办",
  },
};
/* 自建专家(专家中心动态注册)的通用 UI 文案; 内置专家另有 en 版 */
const PERSONA_TEXT_EN = {
  ezcode: {
    label: "Security Ops",
    placeholder: "Paste an alert or describe a task, e.g. triage unhandled alerts since 13:00 today…",
    hint: "Enter to send · Shift+Enter for newline · @path attaches evidence · alert write-back needs Execute mode",
    disclaim: "Analysis and advice only · blocking/allow-listing stays manual in your firewall · write-back needs confirmation",
    welcome: "Paste an alert or just say it in plain words — EZcode calls your connected systems, walks the behavior chain and searches the knowledge base",
  },
  coder: {
    label: "Coding",
    placeholder: "Just ask, e.g. write a Python script that counts today's sessions under logs…",
    hint: "Enter to send · Shift+Enter for newline · @path attaches a file · writes/commands need Execute mode",
    disclaim: "Read-only mode is ready to work · switch to Execute for writes and commands · each command asks for confirmation",
    welcome: "Hand the task to EZcode: code, file edits, commands, data analysis. Read-only works right away; switch to Execute for writes and commands",
  },
  assistant: {
    label: "Daily Assistant",
    placeholder: "Just say it, e.g. note a meeting tomorrow morning; draft a weekly report outline…",
    hint: "Enter to send · Shift+Enter for newline · @path attaches material · todos and research need no Execute mode",
    disclaim: "Saving documents needs Execute mode · todos stay on this machine · research only reads public pages",
    welcome: "Writing, summaries, translation, research and todos are all fair game — try “note: team meeting tomorrow morning”",
  },
};
function personaUi(id){
  const en = CUR_LANG() === "en";
  const table = en ? PERSONA_TEXT_EN : PERSONA_TEXT;
  if(table[id]) return table[id];
  const p = (ui.personas||[]).find(x => x.id === id);
  const label = (p && p.label) || (en ? "Expert" : "专家");
  return {
    label,
    placeholder: T("向「" + label + "」描述任务…"),
    hint: T("Enter 发送 · Shift+Enter 换行 · @路径 附加文件证据 · 写操作需切可执行模式"),
    disclaim: T("只做分析与建议 · 高危操作一律由你确认后执行"),
    welcome: (p && p.welcome) || T("已切换到「" + label + "」，直接描述任务即可"),
  };
}
function applyState(s){
  ui.sid = s.sid || ui.sid;      // 任务切换后, 事件过滤以引擎的激活任务为准
  ui.mode = s.mode;
  ui.notify = s.notify || ui.notify || "auto";
  ui.persona = s.persona || "ezcode";
  const pt = personaUi(ui.persona);
  const lbl = pt.label || s.persona_label || "安全运营";
  $("#persona-label").textContent = lbl;
  const ico = $("#persona-icon");
  if(ico) ico.textContent = s.persona_icon || (ui.persona === "coder" ? "💻" : "🛡");
  $("#btn-persona").className = "side-item sub persona-row" + (ui.persona === "coder" ? " coder" : "");
  $("#btn-persona").title = T("专家中心：当前「" + lbl + "」，点击选择或新建专家");
  const ec = $("#expert-chip");
  if(ec) ec.textContent = (s.persona_icon || "🛡") + " " + lbl;
  $("#input").placeholder = pt.placeholder;
  $("#hint").textContent = pt.hint;
  $("#disclaim").textContent = pt.disclaim;
  $("#mode-label").textContent = s.mode === "execute" ? T("可执行") : T("只读查询");
  $("#btn-mode").className = "ib-chip perm" + (s.mode === "execute" ? " exec" : "");
  $("#model-label").textContent = s.model || s.active;
  $("#chat-title").textContent = s.title || T("新任务");
  setBusy(s.busy);
  setCtx(s.ctx_pct || 0);
  syncModeMenu(s.mode);
  renderWelcomeCards(s.persona_cards, ui.persona);
}
function refreshState(){ pywebview.api.state().then(applyState).catch(()=>{}); }

/* ---------- 会话列表 ---------- */
function stageText(st){
  if(!st || !st.k) return "";
  if(st.k === "think") return `${T("思考")} ${st.n}`;
  if(st.k === "text")  return `${T("生成回复")} ${st.n}`;
  if(st.k === "tool")  return `${T("工具")} ${st.n}`;
  if(st.k === "sub")   return T("子代理运行中");
  return "";
}
function paintSessions(list, sid){
  const box = $("#sessions");
  box.innerHTML = "";
  const cnt = $("#task-count");
  if(cnt) cnt.textContent = (list || []).length;
  if(!list.length){
    box.innerHTML = `<div class="sess-empty">暂无任务，点上方「＋ 新建任务」开始</div>`;
    return;
  }
  for(const s of list.slice(0, 30)){
    const d = document.createElement("div");
    const pending = ui.confirmQueue.some(x => x.sid === s.id);
    d.className = "sess-item" + (s.id === sid ? " on" : "") + (s.busy ? " running" : "")
      + (s.unread ? " unread" : "") + (pending ? " awaiting" : "");
    d.innerHTML = `<span class="s-main"><span class="s-title"></span><span class="s-meta"></span></span>`
      + (s.busy ? `<span class="s-stop" title="停止这个任务">■</span>` : "")
      + `<span class="s-del" title="删除">✕</span>`;
    d.querySelector(".s-title").textContent = s.title;
    const bits = [s.persona_icon || "🧠", s.persona_label || "", s.model || ""];
    if(s.busy){
      bits.push(s.id === sid ? T("运行中")
        : [T("后台运行中"), stageText(s.stage)].filter(Boolean).join(" · "));
    }
    if(s.unread) bits.push(T("有新回答"));
    if(pending)  bits.push(T("待确认"));
    d.querySelector(".s-meta").textContent = bits.filter(Boolean).join(" · ");
    d.title = `${s.n_msgs}条消息 · 专家:${s.persona_label || "-"} · 模型:${s.model || "-"} · ${s.saved_at}`;
    d.onclick = ev=>{
      if(ev.target.classList.contains("s-del") || ev.target.classList.contains("s-stop")) return;
      if(s.id === ui.sid) return;
      switchTask(s.id);
    };
    const st = d.querySelector(".s-stop");
    if(st) st.onclick = ev=>{
      ev.stopPropagation();
      pywebview.api.stop(s.id).then(()=>{ toast("已请求停止该任务", "warn"); refreshSessions(); });
    };
    d.querySelector(".s-del").onclick = ev=>{
      ev.stopPropagation();
      pywebview.api.delete_session(s.id).then(r=>{
        if(r.error) toast(r.error, "err");
        else{ toast("已删除", "ok"); refreshSessions(); }
      });
    };
    box.appendChild(d);
  }
}
function refreshSessions(){
  pywebview.api.list_sessions().then(list=>{
    pywebview.api.state().then(st=>{
      if(st.sid) ui.sid = st.sid;
      paintSessions(list, st.sid);
    }).catch(()=>{});
  }).catch(()=>{});
}
/* 切换/新建任务: 先切引擎的激活会话, 再按该任务重绘对话/状态/控件。
   旧任务即使在跑也不受影响, 它的事件带自己的 sid, 不会渲染到这里。 */
function switchTask(sid){
  pywebview.api.open_session(sid).then(r=>{
    if(r && r.error) return toast(r.error, "err");
    ui.sid = (r && r.sid) || sid;
    /* 先按历史重绘, 再把切走期间后台产生的进度补上(顺序不能反) */
    rebuildChat(()=>restoreBackground(sid));
    refreshState(); refreshSessions();
    toast("已切换任务", "ok");
    flushConfirms(ui.sid);
  });
}
/* 桌面提示卡片点击(通知窗调用): 回到对应任务 */
window.openSessionFromNotify = function(sid){
  if(!sid || sid === ui.sid) return;
  switchTask(sid);
};
function newTask(){
  pywebview.api.new_session().then(r=>{
    if(r && r.error) return toast(r.error, "err");
    ui.sid = (r && r.sid) || ui.sid;
    streamBox().innerHTML = "";
    ui.streamEl = null; ui.thinkEl = null; ui.lastToolCard = null;
    ui.autoScroll = true;
    $("#chat-title").textContent = "新任务";
    setBusy(false);                 // 旧任务可能还在跑, 新任务是空闲的
    refreshState(); refreshSessions();
    showWelcome();
    scrollBottom(true);
    flushConfirms(ui.sid);
  });
}
function rebuildChat(after){
  streamBox().innerHTML = "";
  ui.streamEl = null; ui.thinkEl = null; ui.lastToolCard = null;
  ui.autoScroll = true;              // 切换/恢复会话重置跟随状态
  return pywebview.api.history().then(list=>{
    for(const m of (list||[])){
      if(m.kind === "user") addUser(m.text);
      else addBotFull(m.text);
    }
    scrollBottom(true);
    if(after) after();
  }).catch(()=>{ if(after) after(); });
}

/* ---------- 输入区 ---------- */
const ta = $("#input");
ta.addEventListener("input", ()=>{
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
});
ta.addEventListener("keydown", e=>{
  if(e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.altKey){
    e.preventDefault();
    /* 吞字修复: 运行中按Enter不再误触"停止"——旧版会把用户正在输入的追问
       变成中止指令且不发送, 体感就是"我的文字被吃掉了"。现在只提示,
       输入内容原样保留, 中止只能通过"停止"按钮触发。 */
    if(ui.busy){ toast("本轮仍在运行——输入已保留, 回复结束后再发送; 需中止请点「停止」", "warn"); return; }
    sendMsg();
  }
});
ta.addEventListener("paste", ()=>{          // 粘贴后下一帧重算高度
  setTimeout(()=>{ ta.style.height="auto";
                    ta.style.height=Math.min(ta.scrollHeight,220)+"px"; }, 30);
});
$("#sendbtn").onclick = ()=>{
  if(ui.busy){                      // 按钮在运行中显示"停止", 点击=请求中止
    pywebview.api.stop().then(()=>{ toast("已请求中止", "warn"); });
    return;
  }
  sendMsg();
};
function sendMsg(){
  const text = ta.value.trim();
  if(!text) return;
  ta.value = ""; ta.style.height = "auto";
  const bubble = addUser(text);
  setBusy(true);
  refreshSessions();                 // 让任务列表立刻显示本任务「运行中」
  pywebview.api.send(text).then(r=>{
    if(r && r.ok === false){
      /* 发送被拒: 文字放回输入框, 气泡标注失败——绝不无声吞掉用户输入 */
      ta.value = text;
      ta.style.height = "auto";
      ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
      bubble.classList.add("failed");
      setBusy(false);
      toast(r.error || "发送失败", "err");
    }
  }).catch(()=>{
    ta.value = text;
    bubble.classList.add("failed");
    setBusy(false);
    toast("发送失败: 与引擎的桥接异常", "err");
  });
}

/* ---------- 侧边栏按钮 ---------- */
$("#btn-new").onclick = ()=>{ newTask(); };   /* 运行中也能开新任务, 两者互不干扰 */
/* ---------- 输入框工具栏(v23): ＋ / 权限 / 上下文 / 模型 ----------
   原来散落在左下角「运行状态」与顶栏的四组控件统一收进输入框底部工具栏,
   全部改为就地浮层选择, 不再弹全屏设置窗打断输入。 */
function hideMenus(){
  document.querySelectorAll(".ib-menu.show").forEach(m=>m.classList.remove("show"));
  document.querySelectorAll(".ib-chip.sel, .ib-ico.sel").forEach(b=>b.classList.remove("sel"));
}
function toggleMenu(sel, btn){
  const m = $(sel);
  if(!m) return;
  const open = m.classList.contains("show");
  hideMenus();
  if(open) return;
  m.classList.add("show");
  if(btn) btn.classList.add("sel");
}
document.addEventListener("click", e=>{
  if(!e.target.closest(".ib-menu-wrap")) hideMenus();
});
document.addEventListener("keydown", e=>{ if(e.key === "Escape") hideMenus(); });

/* 权限选择: 只读查询 / 可执行 */
function syncModeMenu(mode){
  document.querySelectorAll("#mode-menu [data-mode]").forEach(b=>{
    b.classList.toggle("on", b.dataset.mode === (mode || "query"));
  });
}
function setMode(next){
  if(ui.busy) return toast("运行中不能切模式", "warn");
  pywebview.api.set_mode(next).then(r=>{
    if(r && r.error) return toast(r.error, "err");
    refreshState();
    toast(next === "execute" ? "已切换到可执行模式(写操作仍需逐次确认)"
                             : "已切换到只读查询模式", "ok");
  }).catch(()=>toast("切换失败: 桥接异常", "err"));
}
$("#btn-mode").onclick = e=>{ e.stopPropagation(); toggleMenu("#mode-menu", e.currentTarget); };
document.querySelectorAll("#mode-menu [data-mode]").forEach(b=>{
  b.onclick = ()=>{ hideMenus(); setMode(b.dataset.mode); };
});

/* 模型选择: 就地列出已配置 profiles, 选中即切换 */
function openModelMenu(){
  if(ui.busy) return toast("运行中不能切模型", "warn");
  pywebview.api.state().then(st=>{
    const list = $("#model-list");
    if(!list) return;
    list.innerHTML = "";
    const items = Object.entries(st.profiles || {});
    if(!items.length){
      const d = document.createElement("div");
      d.className = "ib-menu-note";
      d.textContent = "尚未配置模型，请到设置中心「大模型」添加";
      list.appendChild(d);
    }
    for(const [k, prof] of items){
      const name = (typeof prof === "string" ? prof
                    : (prof && (prof.model || prof.name))) || k;
      const b = document.createElement("button");
      b.className = "ib-menu-item model-item" + (k === st.active ? " on" : "");
      const t = document.createElement("b"); t.textContent = name;
      const sub = document.createElement("small");
      sub.textContent = k + (k === st.active ? " · 当前" : "");
      b.append("🧠 ", t, sub);
      b.onclick = ()=>{
        hideMenus();
        if(k === st.active) return;
        pywebview.api.set_model(k).then(r=>{
          if(r && r.error) return toast(r.error, "err");
          toast("已切换: " + k, "ok"); refreshState();
        }).catch(()=>toast("切换失败: 桥接异常", "err"));
      };
      list.appendChild(b);
    }
    const manage = document.createElement("button");
    manage.className = "ib-menu-item";
    manage.innerHTML = "⚙ <b>管理模型…</b>";
    manage.onclick = ()=>{ hideMenus(); openSettings("llm"); };
    list.appendChild(manage);
    toggleMenu("#model-menu", $("#btn-model"));
  }).catch(()=>toast("读取模型清单失败", "err"));
}
$("#btn-model").onclick = e=>{
  e.stopPropagation();
  if($("#model-menu").classList.contains("show")){ hideMenus(); return; }
  openModelMenu();
};
$("#btn-ctx").onclick = e=>{ e.stopPropagation(); toggleMenu("#ctx-menu", e.currentTarget); };
$("#btn-persona").onclick = ()=>{ openExperts(); };
$("#btn-experts").onclick = ()=>{ openExperts(); };
/* 技能/连接器: 直达设置中心对应分区(复用设置中心, 不重复造编辑器) */
$("#btn-skills").onclick = ()=>{ openSettings("skills"); };
$("#btn-connectors").onclick = ()=>{ openSettings("custom_api"); };

/* ---------- 专家中心: 浏览/切换/新建专家 ---------- */
let _expAll = [];
function openExperts(){
  if(ui.confirmOpen) return;
  pywebview.api.personas_list().then(r=>{
    if(!r || r.ok === false || !Array.isArray(r.personas))
      return toast((r && r.error) || "读取专家清单失败", "err");
    _expAll = r.personas;
    ui.personas = r.personas;
    renderExperts();
  }).catch(()=>toast("读取专家清单失败: 桥接异常", "err"));
}

function renderExperts(){
  const box = $("#modal");
  box.classList.remove("set-wide", "set-wide2");
  box.classList.add("set-wide");
  const groups = [
    {t: "内置专家", items: _expAll.filter(p => p.builtin)},
    {t: "自建专家", items: _expAll.filter(p => p.custom)},
  ];
  const card = p => `
    <div class="ex-card ${p.id === ui.persona ? "on" : ""}">
      <div class="ex-top"><span class="ex-ico">${esc(p.icon || "🧠")}</span>
        ${p.id === ui.persona ? '<span class="ex-cur">使用中</span>' : ""}</div>
      <div class="ex-name">${esc(p.label)}</div>
      <div class="ex-desc">${esc(p.desc || "")}</div>
      <div class="ex-tags">
        ${p.secops ? '<span class="tag">安全工具面</span>' : ""}
        <span class="tag">${p.builtin ? "内置" : "自建"}</span>
      </div>
      <div class="ex-ops">
        ${p.id === ui.persona
          ? '<button class="plain" disabled>当前任务专家</button>'
          : `<button class="primary" data-use="${esc(p.id)}">开始任务</button>`}
        ${p.custom ? `<button class="plain ex-del" data-del="${esc(p.id)}">删除</button>` : ""}
      </div>
    </div>`;
  box.innerHTML = `
    <h3><span class="m-icon">🧠</span>专家中心
      <span class="set-path">专家决定任务的系统提示词与工具面 · 自建专家保存在 agents/personas/</span></h3>
    <div class="ex-body">
      ${groups.map(g => (g.items.length || g.t === "自建专家") ? `
        <div class="ex-group">${g.t}${g.t === "自建专家" ? " · 项目级 agents/personas/ 与全局 ~/.ezcode/personas/ 均可" : ""}</div>
        <div class="ex-grid">
          ${g.items.map(card).join("")}
          ${g.t === "自建专家" ? `
          <div class="ex-card ex-add" id="ex-add">
            <div class="ex-top"><span class="ex-ico">＋</span></div>
            <div class="ex-name">新建专家</div>
            <div class="ex-desc">名称 / 描述 / 系统提示词，保存即生效，可随时删除</div>
          </div>` : ""}
        </div>` : "").join("")}
    </div>
    <div class="m-btns"><button class="plain" id="ex-close">关闭</button></div>`;
  box.querySelectorAll("[data-use]").forEach(b=>{
    b.onclick = ()=>{
      if(ui.busy) return toast("运行中不能切换专家, 请先停止", "warn");
      const id = b.dataset.use;
      pywebview.api.set_persona(id).then(r=>{
        if(r && r.ok === false) return toast(r.error || "切换失败", "err");
        $("#overlay").className = "overlay";
        refreshState();
        toast("已切换专家 · 写操作回退只读", "ok");
      }).catch(()=>toast("切换失败: 桥接异常", "err"));
    };
  });
  box.querySelectorAll("[data-del]").forEach(b=>{
    b.onclick = ()=>{
      if(!b.dataset.arm){                 /* 两步确认, 不依赖原生confirm */
        b.dataset.arm = "1"; b.textContent = "确认删除?";
        setTimeout(()=>{ b.dataset.arm = ""; b.textContent = "删除"; }, 2600);
        return;
      }
      const id = b.dataset.del;
      pywebview.api.persona_delete(id).then(r=>{
        if(r && r.ok === false) return toast(r.error || "删除失败", "err");
        toast((r && r.note) || "已删除", "ok", 4200);
        refreshState();
        openExperts();
      }).catch(()=>toast("删除失败: 桥接异常", "err"));
    };
  });
  const add = box.querySelector("#ex-add");
  if(add) add.onclick = ()=>{ renderExpertForm(); };
  box.querySelector("#ex-close").onclick = ()=>{ $("#overlay").className = "overlay"; };
  $("#overlay").className = "overlay show";
}

function renderExpertForm(){
  const box = $("#modal");
  box.innerHTML = `
    <h3><span class="m-icon">✨</span>新建专家
      <span class="set-path">保存到 agents/personas/*.md · 立即生效 · 所有任务可用</span></h3>
    <div class="ex-form">
      ${fRow("专家名称", fInput("ex-label", "", "例如：告警分诊助手"))}
      ${fRow("图标(可选)", fInput("ex-icon", "🧠", "一个 emoji"))}
      ${fRow("一句话描述", fInput("ex-desc", "", "说明这位专家做什么"))}
      ${fRow("工具面", `<select class="ex-tools">
        <option value="general">通用工具(编码/文件/网页等)</option>
        <option value="secops">含安全运营工具面(告警检索/主机侧/资产/情报)</option>
      </select>`)}
      ${fRow("系统提示词", `<textarea class="ex-prompt" rows="10" placeholder=
        "写清角色、工作方式、输出格式与纪律。&#10;例如：你是告警分诊助手, 收到告警后先查历史处置, 再输出分诊结论与处置建议…"></textarea>`)}
    </div>
    <div class="m-btns">
      <button class="plain" id="ex-back">返回</button>
      <button class="primary" id="ex-save">保存专家</button>
    </div>`;
  box.querySelector("#ex-back").onclick = ()=>{ renderExperts(); };
  box.querySelector("#ex-save").onclick = ()=>{
    const label  = box.querySelector(".ex-label").value.trim();
    const icon   = box.querySelector(".ex-icon").value.trim() || "🧠";
    const desc   = box.querySelector(".ex-desc").value.trim();
    const tools  = box.querySelector(".ex-tools").value;
    const prompt = box.querySelector(".ex-prompt").value.trim();
    if(!label || !prompt) return toast("专家名称与系统提示词不能为空", "warn");
    pywebview.api.persona_create(label, desc, prompt, icon, tools).then(r=>{
      if(!r || r.ok === false) return toast((r && r.error) || "保存失败", "err");
      toast((r && r.note) || "已创建专家", "ok", 4200);
      refreshState();
      openExperts();
    }).catch(()=>toast("保存失败: 桥接异常", "err"));
  };
  $("#overlay").className = "overlay show";
}

/* ---------- 设置中心: 大模型/系统API/情报/RAG/MCP/技能/插件 ---------- */
let _setAll = null;              // settings()读回的全部节(编辑态)
let _setTab = "llm";             // 当前分区

/* 设置中心信息架构(对齐 WorkBuddy): 先分组, 再分区;
   每区带一句话说明与配置状态徽标, 降低"不知道该去哪儿配"的成本。*/
const SET_GROUPS = [
  {id: "model",  label: "模型与运行"},
  {id: "access", label: "能力接入"},
  {id: "extend", label: "扩展与内容"},
  {id: "about",  label: "关于"},
];
const SET_TABS = [
  {id: "llm",         group: "model",  label: "大模型",      icon: "🧠",
   desc: "配置模型服务地址、密钥与默认使用的模型"},
  {id: "preferences", group: "model",  label: "运行偏好",    icon: "🎛",
   desc: "主题外观、工具调用显示、执行模式与上下文压缩"},
  {id: "connectors",  group: "access", label: "连接器",      icon: "🔌",
   desc: "内置能力与外部接入，勾选启用；也可以自己添加"},
  {id: "custom_api",  group: "access", label: "系统API接入", icon: "🔗",
   desc: "用你自己的系统命名，配置 url + key 即可接入"},
  {id: "mcp",         group: "access", label: "MCP服务器",   icon: "🔗",
   desc: "通过标准协议接入外部工具服务"},
  {id: "skills",      group: "extend", label: "技能",        icon: "🛠",
   desc: "全局与项目级研判思路，.md 文件即插即用"},
  {id: "plugins",     group: "extend", label: "插件",        icon: "🧩",
   desc: "安装、启停与卸载深度集成插件"},
  {id: "rag",         group: "extend", label: "RAG知识库",   icon: "📚",
   desc: "接一个 HTTP 检索接口即可，无需模型支持 RAG"},
  {id: "update",      group: "about",  label: "版本更新",    icon: "⬆",
   desc: "启动时自动检查 GitHub 与国内服务器上的新版本"},
  {id: "about",       group: "about",  label: "关于 EZcode", icon: "🐿",
   desc: "版本信息、开源协议与本机数据位置"},
];

function openSettings(tab){
  if(ui.confirmOpen) return;
  if(!tab || typeof tab !== "string") tab = "llm";   /* 事件直绑时 tab=Event, 防御归一 */
  pywebview.api.settings().then(c=>{
    if(!c || c.ok === false) return toast((c && c.error) || "读取配置失败", "err");
    _setAll = c;
    _setTab = tab;
    renderSettings();
  }).catch(()=>toast("读取配置失败: 桥接异常", "err"));
}

function renderSettings(){
  const box = $("#modal");
  box.classList.remove("set-wide");
  box.classList.add("set-wide2");
  const nav = SET_GROUPS.map(g=>{
    const items = SET_TABS.filter(t => t.group === g.id).map(t=>`
      <button class="set-tab ${t.id===_setTab?"on":""}" data-tab="${t.id}" title="${esc(t.desc)}">
        <span class="st-ico">${t.icon}</span><span class="st-label">${t.label}</span>
      </button>`).join("");
    return `<div class="set-group"><div class="set-group-title">${g.label}</div>${items}</div>`;
  }).join("");
  const cur = SET_TABS.find(t => t.id === _setTab) || SET_TABS[0];
  const fname = esc((_setAll.path||"").split(/[\\/]/).pop()||"devices.json");
  box.innerHTML = `
    <div class="set-header">
      <div class="set-h-ico">⚙</div>
      <div class="set-h-text">
        <h3>设置中心</h3>
        <p>配置模型、接入系统与扩展能力 · 保存后立即生效</p>
      </div>
      <button class="set-h-close" id="set-close-x" title="关闭">✕</button>
    </div>
    <div class="set-layout">
      <div class="set-tabs">${nav}</div>
      <div class="set-main">
        <div class="set-panel-head">
          <h4>${cur.icon} ${esc(cur.label)}</h4>
          <p>${esc(cur.desc)}</p>
        </div>
        <div class="set-panel" id="set-panel"></div>
      </div>
    </div>
    <div class="set-footer">
      <span class="set-path" title="${esc(_setAll.path||"")}">配置文件 · ${fname}</span>
      <div class="m-btns">
        <button class="plain" id="btn-export2">导出当前会话</button>
        <button class="plain" id="set-close">关闭</button>
      </div>
    </div>`;
  box.querySelectorAll(".set-tab").forEach(b=>{
    b.onclick = ()=>{ _setTab = b.dataset.tab; renderSettings(); };
  });
  box.querySelectorAll("#set-close, #set-close-x").forEach(b=>{
    b.onclick = ()=>{ $("#overlay").className = "overlay"; };
  });
  box.querySelector("#btn-export2").onclick = ()=>{
    pywebview.api.export_session().then(p=>toast("已导出: " + p, "ok")).catch(()=>{});
  };
  renderSetPanel(_setTab);
  $("#overlay").className = "overlay show";
}

/* ---- 通用小部件 ---- */
function setPanel(){ return $("#set-panel"); }
function fRow(label, inputHtml, hint){
  return `<div class="frow"><label>${esc(label)}</label>
    <div class="fval">${inputHtml}${hint?`<div class="fhint">${esc(hint)}</div>`:""}</div></div>`;
}
function fInput(cls, val, ph, type){
  return `<input class="${cls}" type="${type||"text"}" value="${esc(val ?? "")}" placeholder="${esc(ph||"")}">`;
}
function fBool(cls, checked, label){
  return `<label class="fbool"><input type="checkbox" class="${cls}" ${checked?"checked":""}>${esc(label)}</label>`;
}
function bindField(root, sel, obj, key, cast){
  const el = root.querySelector(sel);
  if(!el) return;
  el.addEventListener("input", ev=>{
    let v = el.type === "checkbox" ? el.checked : el.value;
    if(cast === "num") v = Number(v) || 0;
    obj[key] = v;
  });
}
function panelFoot(sec, extra){
  return `${extra||""}
    <div class="set-actions">
      <button class="primary" data-save="${sec}">保存本节</button>
    </div>`;
}
function bindSave(root){
  root.querySelectorAll("[data-save]").forEach(b=>{
    b.onclick = ()=>{
      const sec = b.dataset.save;
      pywebview.api.save_settings({[sec]: _setAll[sec]}).then(r=>{
        if(r && r.ok === false) return toast(r.error || "保存失败", "err");
        toast(r.note || "已保存并生效", "ok", 5200);
      }).catch(()=>toast("保存失败: 桥接异常", "err"));
    };
  });
}

/* ---- 运行偏好(纯前端, 只写本机 localStorage / 现有桥接) ---- */
function renderPreferences(p){
  const seg = (name, items, cur) => `<div class="pref-seg" data-pref="${name}">
    ${items.map(([id,label])=>`<button class="pref-opt ${id===cur?"on":""}" data-val="${id}">${esc(label)}</button>`).join("")}
  </div>`;
  const theme = document.documentElement.dataset.theme || "light";
  const lang = CUR_LANG();
  p.innerHTML = `
    <div class="pref-card">
      <div class="pref-t">界面语言</div>
      <div class="pref-d">切换中文 / English，界面与模型回复语言一起切换</div>
      ${seg("lang", [["zh","中文"],["en","English"]], lang)}
    </div>
    <div class="pref-card">
      <div class="pref-t">外观主题</div>
      <div class="pref-d">切换明亮 / 暗色界面，选择保存在本机</div>
      ${seg("theme", [["light","☀ 明亮"],["dark","🌙 暗色"]], theme)}
    </div>
    <div class="pref-card">
      <div class="pref-t">工具调用显示</div>
      <div class="pref-d">只显示「调了什么工具」，不展示返回内容与密钥等敏感数据；隐藏档运行时顶部仍有进度提示</div>
      ${seg("toolview", TOOL_VIEWS.map(v=>[v.id, v.btn]), toolViewMode())}
    </div>
    <div class="pref-card">
      <div class="pref-t">完成提示</div>
      <div class="pref-d">回答结束时在右下角弹一张提示卡；选「自动」只在你切到别的任务或窗口不在前台时才提醒</div>
      ${seg("notify", [["auto","自动"],["always","总是"],["off","关闭"]], ui.notify || "auto")}
    </div>
    <div class="pref-card">
      <div class="pref-t">执行模式</div>
      <div class="pref-d">只读查询可直接干活；写文件与跑命令需切到可执行模式并逐次确认</div>
      ${seg("mode", [["query","只读查询"],["execute","可执行"]], ui.mode || "query")}
    </div>
    <div class="pref-card">
      <div class="pref-t">上下文压缩</div>
      <div class="pref-d">历史对话 → AI 纪要 + 保留最近原文；水位超过 80% 自动触发，会话记录仍完整留存</div>
      <button class="pref-btn" id="pref-compact">⇲ 立即压缩上下文</button>
    </div>
    <div class="pref-note">这些偏好只对当前这台机器生效，不写入发布包，也不会同步到任何服务器。</div>`;
  p.querySelectorAll("[data-pref]").forEach(g=>{
    g.querySelectorAll(".pref-opt").forEach(b=>{
      b.onclick = ()=>{
        const kind = g.dataset.pref, val = b.dataset.val;
        if(kind === "lang"){
          /* 语言: 立即重绘界面并落盘(引擎据此切换回复语言); 设置中心整块重绘以换掉全部文案。
             语言包缺失时退化为只提示, 不影响其它功能。 */
          if(typeof setLang !== "function") return toast("语言包未加载", "err");
          setLang(val, {save: true});
          if(typeof renderSettings === "function") renderSettings();
          if(typeof applyI18nAll === "function") applyI18nAll();
          toast(val === "en" ? "Language switched to English" : "已切换为中文", "ok");
          return;
        }
        if(kind === "theme"){
          document.documentElement.dataset.theme = val;
          try{ localStorage.setItem("ezcode-theme-v2", val); }catch(e){}
        }else if(kind === "toolview"){
          try{ localStorage.setItem("ezcode-toolview", val); }catch(e){}
          applyToolViewBtn();
        }else if(kind === "notify"){
          ui.notify = val;
          pywebview.api.set_notify(val).then(r=>{
            if(r && r.error) toast(r.error, "err");
          }).catch(()=>toast("切换失败: 桥接异常", "err"));
        }else if(kind === "mode"){
          if(ui.busy) return toast("运行中不能切换模式", "warn");
          pywebview.api.set_mode(val).then(r=>{
            if(r && r.error) return toast(r.error, "err");
            refreshState();
          }).catch(()=>toast("切换失败: 桥接异常", "err"));
        }
        g.querySelectorAll(".pref-opt").forEach(x=>x.classList.toggle("on", x===b));
        toast("偏好已更新", "ok");
      };
    });
  });
  const c = p.querySelector("#pref-compact");
  if(c) c.onclick = ()=>{
    pywebview.api.compact_now().then(r=>{
      if(!r || r.ok === false) return toast((r&&r.error)||"压缩失败", "warn");
      toast("正在压缩上下文…(完成后对话流里会有提示)", "ok", 4200);
    }).catch(()=>toast("压缩失败: 桥接异常", "err"));
  };
}

/* ---- 关于: 版本 / 协议 / 数据位置 ---- */
function renderAbout(p){
  p.innerHTML = `
    <div class="about-hero">
      <img src="EZcode.png" alt="EZcode">
      <div><h4>EZcode</h4>
        <span class="about-ver">版本 v${esc((_upd && _upd.app_version) || "44")} · Windows 单文件安装版</span></div>
    </div>
    <div class="about-grid">
      <div class="about-item"><b>产品定位</b><span>本地优先的多场景桌面 AI 应用：安全运营研判、编写项目代码、日常工作助手。模型只负责推理，工具能力与外部系统由你自己在设置中心配置。</span></div>
      <div class="about-item"><b>开源协议</b><span>MIT License · 可自由使用、修改与分发</span></div>
      <div class="about-item"><b>本机数据</b><span>配置、会话、技能、插件全部留在安装目录与用户目录，不上传任何服务器；发布包内不含凭据。</span></div>
      <div class="about-item"><b>能力组织</b><span>专家决定提示词与工具面；技能沉淀工作方法；连接器接入系统 API / MCP / 插件；内置工具箱按组开关。</span></div>
    </div>
    <div class="about-links">
      <button class="plain" id="ab-settings">查看接入配置</button>
      <button class="plain" id="ab-export">导出当前会话</button>
    </div>
    <div class="pref-note">未做代码签名，首次运行 Windows SmartScreen 可能提示「未知发布者」，选择「仍要运行」即可。</div>`;
  p.querySelector("#ab-settings").onclick = ()=>{ _setTab = "custom_api"; renderSettings(); };
  p.querySelector("#ab-export").onclick = ()=>{
    pywebview.api.export_session().then(x=>toast("已导出: " + x, "ok")).catch(()=>{});
  };
}

/* ---------- 自动更新: 侧栏徽标 + 「版本更新」设置页 ----------
   后端启动时后台检查全部更新源, 结果经 update 事件推来; 前端只渲染,
   不发网络请求、不碰凭据。下载进度事件较密, 只有状态签名变化才整块重绘,
   否则只更新进度条, 避免用户正在填仓库地址时输入框失焦。 */
let _upd = null;
let _updSig = "";

/* 更新桥可能尚未注入(首屏握手前 / 诊断脚本 mock 环境): 统一兜底成拒绝,
   绝不让一个可选功能把整个设置中心或启动流程带崩。 */
function updApi(name, ...args){
  try{
    const fn = window.pywebview && pywebview.api && pywebview.api[name];
    if(typeof fn !== "function") return Promise.resolve({ok: false, error: "更新桥不可用"});
    return fn(...args);
  }catch(e){ return Promise.resolve({ok: false, error: String(e)}); }
}

/* 通用桥接调用: 桥未注入(诊断/mock 环境)时返回 rejected, 由调用方 catch。 */
function apiCall(name, ...args){
  try{
    const fn = window.pywebview && pywebview.api && pywebview.api[name];
    if(typeof fn !== "function") return Promise.reject(new Error("桥接不可用: " + name));
    return fn(...args);
  }catch(e){ return Promise.reject(e); }
}

function onUpdateEvent(d){
  if(!d || typeof d !== "object") return;
  _upd = d;
  paintUpdateBadge();
  const sig = [d.state, d.has_update, d.latest, (d.download || {}).state,
               (d.download || {}).percent,
               (d.sources || []).map(s => String(s.ok) + ":" + (s.error || "")).join(",")].join("|");
  const showing = $("#overlay").classList.contains("show") && _setTab === "update";
  if(showing && sig !== _updSig){
    const panel = setPanel();
    if(panel) renderUpdatePanel(panel);
  }else if(showing){
    paintUpdateProgress(d);
  }
  _updSig = sig;
}

function paintUpdateBadge(){
  const b = $("#update-badge"), v = $("#brand-ver");
  if(v && _upd && _upd.app_version) v.textContent = "v" + _upd.app_version;
  if(!b || !_upd) return;
  const on = !!_upd.has_update;
  b.hidden = !on;
  b.textContent = on ? ("⬆ " + (_upd.latest || "")) : "";
  b.title = on ? T(`发现新版本 v${_upd.latest || ""}，点击查看与安装`) : T("检查更新");
}

function paintUpdateProgress(d){
  const dl = d.download || {};
  const bar = $("#upd-bar"), txt = $("#upd-prog-txt"), msg = $("#upd-msg");
  if(bar) bar.style.width = (dl.percent || 0) + "%";
  if(txt) txt.textContent = dl.state === "downloading"
    ? T(`正在下载 ${dl.percent || 0}%`)
    : (dl.state === "ready" ? T("下载完成，可安装") : "");
  if(msg){
    if(dl.state === "failed") msg.textContent = T("下载失败: ") + (dl.error || "");
    else if(dl.state === "ready") msg.textContent = dl.path || "";
  }
}

function updStateText(d){
  if(d.state === "checking")   return T("正在检查更新…");
  if(d.state === "disabled")   return T("启动检查已关闭，可手动检查");
  if(d.state === "unconfigured") return T("未启用任何更新源");
  if(d.state === "failed")     return T("检查失败：网络不可达或更新源未配置");
  if(d.has_update)             return T(`发现新版本 v${d.latest || ""}`);
  if(d.ignored)                return T("此版本已被忽略");
  return T("已是最新版本");
}

function updSrcState(s, r){
  if(!s.enabled) return T("已停用");
  if(!r) return T("未检查");
  if(r.ok === true) return T(`可用 · v${r.version || ""}`);
  return r.error ? T("检查失败: ") + r.error : T("未检查");
}

function renderUpdatePanel(p){
  if(!_upd){
    p.innerHTML = `<div class="fhint">正在读取更新状态…</div>`;
    updApi("update_state").then(x=>{ if(x) onUpdateEvent(x); }).catch(()=>{});
    return;
  }
  const d = _upd, cfg = d.config || {};
  const srcs = Array.isArray(cfg.sources) ? cfg.sources : [];
  const rel = d.release || {}, pkg = rel.package || {}, dl = d.download || {};
  const rmap = {};
  (d.sources || []).forEach(r=>{ rmap[r.name] = r; });
  const lang = CUR_LANG();
  const notes = (rel.notes && (lang === "en" ? (rel.notes.en || rel.notes.zh)
                                             : (rel.notes.zh || rel.notes.en))) || "";
  const downloading = dl.state === "downloading";
  const ready = dl.state === "ready";
  const srcRows = srcs.map((s, i)=>{
    const isGh = s.type === "github";
    const r = rmap[s.name];
    return `<div class="upd-src">
      <div class="upd-src-h">
        <label class="fbool"><input type="checkbox" class="upd-src-en" data-i="${i}"
          ${s.enabled ? "checked" : ""}>${esc(s.name || (isGh ? "GitHub Releases" : "静态更新源"))}</label>
        <span class="upd-src-kind">${isGh ? "GitHub Releases" : "version.json"}</span>
        <span class="upd-src-st ${r && r.ok === true ? "ok" : (s.enabled ? "bad" : "off")}">${esc(updSrcState(s, r))}</span>
      </div>
      <div class="upd-src-body">
        ${isGh
          ? `<div class="upd-lbl">仓库 owner/repo</div>
             <input class="upd-repo" data-i="${i}" value="${esc(s.repo || "")}" placeholder="yourname/EZcode">
             <div class="upd-lbl">下载镜像（可选，国内加速）</div>
             <input class="upd-mirror" data-i="${i}" value="${esc(s.mirror || "")}" placeholder="https://mirror.example.com/gh/">`
          : `<div class="upd-lbl">version.json 地址（HTTPS / 内网共享 / 本地文件）</div>
             <input class="upd-url" data-i="${i}" value="${esc(s.url || "")}" placeholder="https://update.example.com/ezcode/version.json">`}
      </div>
    </div>`;
  }).join("");
  p.innerHTML = `
    <div class="upd-hero ${d.has_update ? "has" : ""}">
      <div class="upd-hero-main">
        <div class="upd-cur">当前版本 <b>v${esc(d.app_version || "")}</b></div>
        <div class="upd-state">${updStateText(d)}</div>
        <div class="upd-sub">${d.checked_at ? T(`上次检查 ${d.checked_at}`) : T("尚未检查")} · ${esc(d.download_dir || "")}</div>
      </div>
      <button class="primary" id="upd-check" ${d.state === "checking" ? "disabled" : ""}>${d.state === "checking" ? "检查中…" : "检查更新"}</button>
    </div>
    ${d.has_update ? `
    <div class="upd-card">
      <div class="upd-card-h">新版本 <b>v${esc(d.latest || "")}</b>
        <span class="upd-tag">${esc(rel.source || "")}</span>
        ${d.required ? `<span class="upd-tag force">必须升级</span>` : ""}</div>
      ${notes ? `<div class="upd-notes">${esc(notes)}</div>` : ""}
      <div class="upd-meta">
        ${rel.released_at ? `<span>发布于 ${esc(String(rel.released_at).slice(0, 10))}</span>` : ""}
        ${pkg.size ? `<span>安装包 ${(Number(pkg.size) / 1048576).toFixed(1)} MB</span>` : ""}
        <span>${pkg.sha256 ? "sha256 校验已启用" : "未声明 sha256，将跳过完整性校验"}</span>
      </div>
      <div class="upd-prog"><div class="upd-prog-bar" id="upd-bar"
        style="width:${downloading ? (dl.percent || 0) : (ready ? 100 : 0)}%"></div></div>
      <div class="upd-prog-txt" id="upd-prog-txt">${downloading
        ? T(`正在下载 ${dl.percent || 0}%`) : (ready ? T("下载完成，可安装") : "")}</div>
      <div class="upd-actions">
        <button class="primary" id="upd-download" ${downloading ? "disabled" : ""}>${ready ? "重新下载" : "下载更新"}</button>
        <button class="plain" id="upd-install" ${ready ? "" : "disabled"}>安装并重启</button>
        <button class="plain" id="upd-open">打开下载目录</button>
        <button class="plain" id="upd-ignore">忽略此版本</button>
      </div>
      <div class="upd-msg" id="upd-msg">${dl.error ? esc(dl.error) : (ready ? esc(dl.path || "") : "")}</div>
    </div>` : ""}
    <div class="upd-sec">
      <div class="upd-sec-t">更新源 <small>可同时启用多条，客户端并行检查并自动采用最高版本</small></div>
      <div class="upd-switches">
        <label class="fbool"><input type="checkbox" id="upd-enabled" ${cfg.enabled !== false ? "checked" : ""}>启用自动更新</label>
        <label class="fbool"><input type="checkbox" id="upd-startup" ${cfg.check_on_startup !== false ? "checked" : ""}>启动时自动检查</label>
      </div>
      <div class="upd-srcs">${srcRows || `<div class="fhint">未配置更新源</div>`}</div>
      <div class="set-actions"><button class="primary" id="upd-save">保存更新设置</button></div>
      <div class="pref-note">更新只读取版本清单并下载安装包，不会上传本机配置、会话或凭据；升级安装会保留 devices.json 与判据库。</div>
    </div>`;
  bindUpdatePanel(p, d, cfg, srcs);
}

function bindUpdatePanel(p, d, cfg, srcs){
  const q = s => p.querySelector(s);
  q("#upd-check").onclick = ()=>{
    pywebview.api.check_update().then(r=>{
      if(r && r.ok === false) toast(r.error || "检查失败", "err");
    }).catch(()=>toast("检查更新失败: 桥接异常", "err"));
  };
  const btnDl = q("#upd-download");
  if(btnDl) btnDl.onclick = ()=>{
    btnDl.disabled = true;
    pywebview.api.download_update().then(r=>{
      if(r && r.ok === false){ toast(r.error || "下载失败", "err"); btnDl.disabled = false; }
    }).catch(()=>{ toast("下载失败: 桥接异常", "err"); btnDl.disabled = false; });
  };
  const btnIn = q("#upd-install");
  if(btnIn){
    let armed = false;
    btnIn.onclick = ()=>{
      if(!armed){                       /* 二次确认: 安装会关闭客户端, 不做静默强更 */
        armed = true;
        btnIn.textContent = "再次点击确认（将关闭 EZcode）";
        btnIn.classList.add("danger");
        setTimeout(()=>{ armed = false; btnIn.textContent = "安装并重启"; btnIn.classList.remove("danger"); }, 6000);
        return;
      }
      btnIn.disabled = true;
      pywebview.api.install_update().then(r=>{
        if(r && r.ok === false){ toast(r.error || "启动安装失败", "err"); btnIn.disabled = false; }
        else toast("EZcode 即将关闭并自动安装新版本", "ok", 6000);
      }).catch(()=>{ toast("启动安装失败: 桥接异常", "err"); btnIn.disabled = false; });
    };
  }
  const btnOpen = q("#upd-open");
  if(btnOpen) btnOpen.onclick = ()=>{ pywebview.api.open_update_dir().catch(()=>{}); };
  const btnIg = q("#upd-ignore");
  if(btnIg) btnIg.onclick = ()=>{
    pywebview.api.ignore_update(d.latest || "").then(()=>toast("已忽略此版本", "ok")).catch(()=>{});
  };
  const btnSave = q("#upd-save");
  if(btnSave) btnSave.onclick = ()=>{
    const out = {
      enabled: q("#upd-enabled").checked,
      check_on_startup: q("#upd-startup").checked,
      channel: cfg.channel || "stable",
      ignored_version: cfg.ignored_version || "",
      timeout: Number(cfg.timeout || 6),
      sources: srcs.map((s, i)=>{
        const o = Object.assign({}, s);
        const en = p.querySelector(`.upd-src-en[data-i="${i}"]`);
        const rp = p.querySelector(`.upd-repo[data-i="${i}"]`);
        const ur = p.querySelector(`.upd-url[data-i="${i}"]`);
        const mi = p.querySelector(`.upd-mirror[data-i="${i}"]`);
        if(en) o.enabled = en.checked;
        if(rp) o.repo = rp.value.trim();
        if(ur) o.url = ur.value.trim();
        if(mi) o.mirror = mi.value.trim();
        return o;
      })
    };
    pywebview.api.save_update_settings(out).then(r=>{
      if(r && r.ok === false) return toast(r.error || "保存失败", "err");
      toast("更新设置已保存，正在重新检查…", "ok", 4200);
    }).catch(()=>toast("保存失败: 桥接异常", "err"));
  };
}

/* ---- 各分区渲染 ---- */
function renderSetPanel(tab){
  const p = setPanel();
  if(tab === "llm")           return renderLlmPanel(p);
  if(tab === "connectors")    return renderConnectors(p);
  if(tab === "custom_api")    return renderSysApi(p);
  if(tab === "mcp")           return renderMcp(p);
  if(tab === "plugins")       return renderPlugins(p);
  if(tab === "rag")           return renderRag(p);
  if(tab === "skills")        return renderSkills(p);
  if(tab === "preferences")   return renderPreferences(p);
  if(tab === "update")        return renderUpdatePanel(p);
  if(tab === "about")         return renderAbout(p);
}

/* ---- 插件(运营人员可安装的扩展; 安装/启停/卸载热生效) ---- */
let _plugins = null;
function renderPlugins(p){
  if(!_plugins){
    p.innerHTML = `<div class="fhint">加载插件清单…</div>`;
    pywebview.api.plugins_list().then(r=>{
      _plugins = (r && r.plugins) ? r : {plugins: []};
      renderSetPanel("plugins");
    }).catch(()=>{ toast("读取插件失败", "err"); _plugins = {plugins: []}; renderSetPanel("plugins"); });
    return;
  }
  const items = _plugins.plugins || [];
  let html = `<div class="fhint" data-i18n-html="plugins-intro" style="margin:0 0 12px">
      <b>插件 = 运营人员写的能力扩展</b>(.py文件): 自定义工具(TOOLS) + 使用思路说明
      (PLUGIN_PROMPT, 注入系统提示词)。安装后<b>热生效</b>, 每个插件有独立开关。
      插件接口: TOOLS=[{"schema":函数schema, "handler":函数, "exec":是否需可执行模式}] +
      PLUGIN_PROMPT="何时用这些工具的思路说明" + 可选 on_load(cfg)。
      纯提示词类的"研判思路"用「技能」分区更简单。目录: ${esc(_plugins.dir||"plugins/")}</div>`
    + (items.length ? items.map(it=>`
      <div class="set-card plugin-card" data-file="${esc(it.file)}">
        <div class="set-row">
          <b class="sk-name">${esc(it.name)}</b>
          <span class="sk-desc">${it.loaded ? "已加载" : "未加载(禁用或加载失败)"}
            · ${it.n_tools}个工具${it.warning ? " · ⚠"+esc(String(it.warning).slice(0,60)) : ""}</span>
          <label class="fbool"><input type="checkbox" class="pl-enabled" ${it.enabled?"checked":""}>启用</label>
          <button class="set-del pl-del" title="卸载">✕</button>
        </div>
        ${it.tools && it.tools.length ? `<div class="fhint">工具: ${esc(it.tools.join(", "))}</div>` : ""}
      </div>`).join("") : `<div class="fhint" style="margin-bottom:10px">暂无插件——安装一个试试</div>`)
    + `<div class="set-row" style="gap:8px">
        <button class="pill ghost" id="pl-install-file">📁 从文件安装…</button>
        <button class="pill ghost" id="pl-install-code">✏️ 粘贴代码安装</button>
        <span class="pl-installed-note"></span>
      </div>
      <div class="pl-code-wrap" style="display:none">
        <input class="pl-new-name" placeholder="插件文件名(如 告警速判)">
        <textarea class="sk-body" placeholder='TOOLS = [{...}]  PLUGIN_PROMPT = "..."'></textarea>
        <div class="sk-actions"><button class="primary pl-code-save">安装</button></div>
      </div>`;
  p.innerHTML = html;
  const refresh = ()=>{
    pywebview.api.plugins_list().then(r=>{ _plugins = (r && r.plugins) ? r : {plugins: []}; renderSetPanel("plugins"); });
  };
  p.querySelector("#pl-install-file").onclick = ()=>{
    pywebview.api.pick_plugin_file().then(r=>{
      if(r && r.ok === false){ if(r.error && r.error !== "未选择") toast(r.error, "err"); return; }
      const note = p.querySelector(".pl-installed-note");
      note.textContent = "安装中…";
      pywebview.api.plugin_install(r.path).then(res=>{
        if(res && res.ok === false) return toast(res.error || "安装失败", "err");
        toast(res.note || "已安装", "ok", 5000);
        if(res && res.warnings && res.warnings.length) toast("警告: " + res.warnings[0], "warn", 6000);
        _plugins = null; refresh();
      });
    });
  };
  const wrap = p.querySelector(".pl-code-wrap");
  p.querySelector("#pl-install-code").onclick = ()=>{
    wrap.style.display = wrap.style.display === "none" ? "block" : "none";
  };
  p.querySelector(".pl-code-save").onclick = ()=>{
    const name = p.querySelector(".pl-new-name").value.trim();
    const code = p.querySelector(".pl-code-wrap .sk-body").value;
    if(!name || !code.trim()) return toast("请填文件名和代码", "warn");
    pywebview.api.plugin_install(null, name, code).then(res=>{
      if(res && res.ok === false) return toast(res.error || "安装失败", "err");
      toast(res.note || "已安装", "ok", 5000);
      if(res && res.warnings && res.warnings.length) toast("警告: " + res.warnings[0], "warn", 6000);
      _plugins = null; refresh();
    });
  };
  p.querySelectorAll(".plugin-card").forEach(card=>{
    const file = card.dataset.file;
    card.querySelector(".pl-enabled").onchange = ev=>{
      pywebview.api.plugin_set_enabled(file, ev.target.checked).then(r=>{
        if(r && r.ok === false) return toast(r.error || "操作失败", "err");
        toast(ev.target.checked ? "已启用" : "已禁用", "ok");
        _plugins = null; refresh();
      });
    };
    card.querySelector(".pl-del").onclick = ()=>{
      pywebview.api.plugin_delete(file).then(r=>{
        if(r && r.ok === false) return toast(r.error || "卸载失败", "err");
        toast("已卸载", "ok"); _plugins = null; refresh();
      });
    };
  });
}

/* ---- 产品内置工具箱(设置中心「内置集成」): 文件与代码 / 联网查询 / 分析脚本 / 待办 ---- */
function biSub(title, desc, inner, removeId){
  return `<div class="bi-sub"><div class="bi-title-row">
      <div class="bi-title">${esc(title)}</div>
      ${removeId ? `<button class="pill ghost bi-remove" data-remove="${removeId}" title="删除此接入的全部配置(保存后生效, 对话工具随配置移除)">移除预置接入</button>` : ""}
    </div>
   <div class="fhint">${esc(desc)}</div>${inner}</div>`;
}


/* ---- 系统API接入 = 预置深度接入(按配置显示, 可移除/可添加) + 自定义命名接入 ---- */
const PRESET_BLOCKS = [
  {id: "ngsoc",  title: "告警检索系统",        desc: "深度接入（代码内置解析器，地址与密钥由你填写）：流量侧告警检索、报文下钻与处置回写",
   mk: ()=>({sites_file: "", verify_ssl: false})},
  {id: "edr",    title: "主机侧告警系统",      desc: "深度接入（代码内置解析器，项目与 Token 由你填写）：主机防护类告警查询与主机证据下钻",
   mk: ()=>({client_module_path: "", projects: []})},
  {id: "netbox", title: "资产库(IP归属核查)",  desc: "深度接入（代码内置解析器，地址与密钥由你填写）：IP 归属与资产核查",
   mk: ()=>({base_url: "", token: "", verify_ssl: false})},
];
function _presetInner(id){
  const d = _setAll;
  if(id === "ngsoc"){
    /* 站点卡片(url+key直配, 与主机侧项目同形态); 从外部文件导入的站点带迁移提示 */
    const ns = d.ngsoc.sites = Array.isArray(d.ngsoc.sites) ? d.ngsoc.sites : [];
    const importTip = d.ngsoc._imported
      ? `<div class="fhint">以下站点从外部清单文件导入: ${esc(d.ngsoc._imported)}<br>
         <b>保存后将以"内联站点"写入配置, 该外部文件不再被使用。</b></div>` : "";
    return (importTip ? `<div style="margin-bottom:8px">${importTip}</div>` : "")
      + `<div class="set-list">` + ns.map((it,i)=>`
        <div class="set-card ngsoc-site" data-i="${i}">
          <div class="set-row">
            <input data-nsf="name" value="${esc(it.name||"")}" placeholder="站点名称(对话里引用)">
            <button class="set-del" data-nsdel title="删除站点">✕</button>
          </div>
          <div class="set-grid">
            <span>URL</span><input data-nsf="url" value="${esc(it.url||"")}" placeholder="https://alert.example.com">
            <span>Access Key</span><input data-nsf="token" type="password" value="${esc(it.token||"")}" placeholder="Access-Token">
            <span>站点代码</span><input data-nsf="code" value="${esc(it.code||"")}" placeholder="检索别名(可选)">
            <span>API版本</span><select data-nsf="apiStyle">
              <option value="new" ${it.apiStyle!=="old"?"selected":""}>新版接口</option>
              <option value="old" ${it.apiStyle==="old"?"selected":""}>旧版接口</option></select>
            <span>启用</span><input type="checkbox" data-nsf="enabled" ${it.enabled!==false?"checked":""}>
          </div>
        </div>`).join("") + `</div>
      <button class="set-add" id="ngsoc-add">＋ 新增站点</button>`
      + fRow("校验SSL证书", fBool("ng-ssl", d.ngsoc.verify_ssl, "内网自签环境请关闭"));
  }
  if(id === "edr"){
    return fRow("客户端模块目录", fInput("edr-mod", d.edr.client_module_path, "外部API客户端所在目录, 可留空"))
      + `<div class="set-list">` + (d.edr.projects||[]).map((it,i)=>`
        <div class="set-card edr-proj" data-i="${i}">
          <div class="set-row">
            <input data-f="name" value="${esc(it.name||"")}" placeholder="项目名(自己命名)">
            <button class="set-del" data-edrdel title="删除">✕</button>
          </div>
          <div class="set-grid">
            <span>服务器</span><input data-f="server" value="${esc(it.server||"")}" placeholder="https://...">
            <span>Token</span><input data-f="token" type="password" value="${esc(it.token||"")}" placeholder="访问令牌">
          </div>
        </div>`).join("") + `</div>
      <button class="set-add" id="bi-edr-add">＋ 新增项目</button>`;
  }
  return fRow("接口地址", fInput("nb-url", d.netbox.base_url, "https://..."))
    + fRow("API Token", fInput("nb-token", d.netbox.token, "", "password"))
    + fRow("校验SSL证书", fBool("nb-ssl", d.netbox.verify_ssl, "自签环境请关闭"));
}
/* 自动适配结果: 可编辑映射 JSON + 归一化预览 + 保存生效 */
function showAdaptModal(project, device, r){
  const box = $("#modal2");
  box.classList.remove("set-wide", "set-wide2");
  box.innerHTML = `<h3><span class="m-icon">🧩</span>字段映射草案 · ${esc(device)}</h3>
    <div class="fhint" style="margin:0 0 10px">${esc(r.note || "已用真实样本校验通过")}。左侧是解析出的映射，可直接修改；保存后立即生效。</div>
    <div class="adapt-pair">
      <div class="adapt-col"><b>映射 JSON</b><textarea id="adapt-json" spellcheck="false"></textarea></div>
      <div class="adapt-col"><b>归一化预览（前 ${((r.preview||[]).length)} 条）</b><pre id="adapt-preview"></pre></div>
    </div>
    <div class="m-btns"><button class="plain" id="adapt-cancel">取消</button>
      <button class="primary" id="adapt-save">保存映射并生效</button></div>`;
  box.querySelector("#adapt-json").value = JSON.stringify(r.mapping || {}, null, 2);
  box.querySelector("#adapt-preview").textContent = (r.preview || []).map(a =>
    [a.time || "-", a.name || "-", a.severity_label || "-",
     a.src_ip || "-", a.dst_ip || "-"].join("  |  ")).join("\n") || "(无预览)";
  box.querySelector("#adapt-cancel").onclick = ()=>{ $("#overlay2").className = "overlay"; };
  box.querySelector("#adapt-save").onclick = ()=>{
    let m;
    try{ m = JSON.parse(box.querySelector("#adapt-json").value); }
    catch(e){ return toast("映射 JSON 解析失败: " + e.message, "err"); }
    const sb = box.querySelector("#adapt-save");
    sb.disabled = true; sb.textContent = "保存中…";
    pywebview.api.sec_apply_mapping(project, device, m).then(x=>{
      sb.disabled = false; sb.textContent = "保存映射并生效";
      if(!x || !x.ok) return toast((x && x.error) || "保存失败", "err");
      $("#overlay2").className = "overlay";
      toast(x.note || "映射已保存并生效", "ok", 4000);
      refreshState();
      renderSetPanel("custom_api");
    }).catch(e=>{
      sb.disabled = false; sb.textContent = "保存映射并生效";
      toast("保存异常: " + e, "err");
    });
  };
  $("#overlay2").className = "overlay show";
}

/* ═══════ 连接器: 内置能力统一入口(勾选即生效) ═══════ */
const CONNECTOR_DEFS = [
  {id: "office",     icon: "📄", name: "Office / WPS 文档处理",
   desc: "调用本机 Office/WPS 整改 Word/Excel/PPT，完成后自动清理进程", tab: null},
  {id: "fs_tools",   icon: "📁", name: "文件与代码",
   desc: "浏览目录、按内容检索、读写与精确编辑文件", tab: null},
  {id: "web_tools",  icon: "🌐", name: "联网查询",
   desc: "搜索公开资料并抓取原文（自带内网地址拦截）", tab: null},
  {id: "run_python", icon: "🧪", name: "分析脚本",
   desc: "直接跑一段分析代码，省去先写文件再执行", tab: null},
  {id: "todo",       icon: "✅", name: "本地待办清单",
   desc: "记任务 / 列待办，纯本机存储、跨会话", tab: null},
  {id: "custom_api", icon: "🔗", name: "系统 API 接入",
   desc: "用你的系统命名接入，填 url + key 即可", tab: "custom_api"},
  {id: "mcp",        icon: "🧩", name: "MCP 服务器",
   desc: "内置模板一键添加，也支持自定义 stdio / HTTP", tab: "mcp"},
  {id: "plugins",    icon: "🧷", name: "插件",
   desc: "深度集成 + 把处理思路一起注入", tab: "plugins"},
  {id: "skills",     icon: "🛠", name: "技能",
   desc: "自定义提示词片段，勾选后注入系统提示词", tab: "skills"},
  {id: "rag",        icon: "📚", name: "RAG 知识库",
   desc: "接一个 HTTP 检索接口即可", tab: "rag"},
];
const BUILTIN_CONN_KEYS = ["fs_tools", "web_tools", "run_python", "todo"];

function renderConnectors(p){
  const d = _setAll;
  if(!d.connectors || typeof d.connectors !== "object") d.connectors = {};
  const cn = d.connectors;
  const bt = d.builtin_tools = (d.builtin_tools && typeof d.builtin_tools === "object") ? d.builtin_tools : {};
  const isOn = (id) => BUILTIN_CONN_KEYS.includes(id) ? (bt[id] !== false) : (cn[id] !== false);

  const cards = CONNECTOR_DEFS.map(c => {
    const on = isOn(c.id);
    return `<div class="conn-card ${on ? "" : "off"}" data-id="${c.id}">
      <div class="cc-top">
        <span class="cc-ico">${c.icon}</span>
        <div class="cc-title"><b>${esc(c.name)}</b><small>${esc(c.desc)}</small></div>
        <label class="cc-switch" title="${on ? "已启用，点击停用" : "已停用，点击启用"}">
          <input type="checkbox" class="cc-on" data-id="${c.id}" ${on ? "checked" : ""}>
          <span></span>
        </label>
      </div>
      <div class="cc-foot">
        <span class="cc-state" id="cc-state-${c.id}">${on ? "已启用" : "已停用"}</span>
        ${c.tab ? `<button class="pill ghost cc-config" data-tab="${c.tab}">配置</button>` : ""}
      </div>
    </div>`;
  }).join("");

  p.innerHTML = `<div class="fhint" data-i18n-html="connectors-intro" style="margin:0 0 12px">
      内置能力与外部接入都在这里。<b>取消勾选立即生效</b>——对应工具会从模型视野移除，但配置保留，随时可以再打开。
    </div>
    <div class="conn-grid">${cards}</div>
    <div class="set-actions"><span class="fhint">状态读取中…</span></div>`;

  p.querySelectorAll(".cc-on").forEach(el => {
    el.onchange = () => {
      const id = el.dataset.id;
      const card = p.querySelector('.conn-card[data-id="' + id + '"]');
      if(card) card.classList.toggle("off", !el.checked);
      const st = p.querySelector("#cc-state-" + id);
      if(st) st.textContent = el.checked ? "已启用" : "已停用";
      let payload, label;
      if(BUILTIN_CONN_KEYS.includes(id)){
        bt[id] = el.checked;
        payload = {builtin_tools: bt};
        label = {"fs_tools": "文件与代码", "web_tools": "联网查询",
                 "run_python": "分析脚本", "todo": "本地待办"}[id] || id;
      }else{
        cn[id] = el.checked;
        payload = {connectors: cn};
        label = id;
      }
      pywebview.api.save_settings(payload).then(r => {
        if(r && r.error) return toast(r.error, "err");
        toast((el.checked ? "已启用" : "已停用") + "：" + label, "ok", 1800);
      }).catch(e => toast("保存失败: " + e, "err"));
    };
  });
  p.querySelectorAll(".cc-config").forEach(b => {
    b.onclick = () => { _setTab = b.dataset.tab; renderSettings(); };
  });

  pywebview.api.connector_status().then(st => {
    if(!st) return;
    const set = (id, txt) => { const el = p.querySelector("#cc-state-" + id); if(el) el.textContent = txt; };
    if(st.office) set("office", st.office);
    set("custom_api", st.custom_api ? (st.custom_api + " 个接入") : "未配置");
    set("mcp", st.mcp ? (st.mcp + " 个服务器") : "未配置");
    set("plugins", st.plugins ? (st.plugins + " 个插件") : "无插件");
    set("skills", st.skills ? (st.skills + " 个技能") : "无技能");
    set("rag", st.rag ? "已配置" : "未配置");
    set("todo", st.todo ? (st.todo + " 条待办") : "空清单");
    const foot = p.querySelector(".set-actions .fhint");
    if(foot) foot.textContent = "勾选状态立即生效，写入本机配置";
  }).catch(()=>{});
}

function renderSysApi(p){
  const d = _setAll;
  /* 预置深度接入: 有配置才显示——无配置=不存在(真删除语义), 需要时「添加预置接入」 */
  const present = PRESET_BLOCKS.filter(b => d[b.id] != null
    && typeof d[b.id] === "object" && Object.keys(d[b.id]).length > 0);
  const missing = PRESET_BLOCKS.filter(b => !present.includes(b));

  let pre = `<div class="fhint" data-i18n-html="customapi-intro" style="margin:0 0 10px">
      <b>接入任何外部系统</b>——下方: 深度接入(代码有专用解析器的几类，有配置才显示、可移除可添加)；
      再往下: 自定义命名接入(接任何内部系统，需要什么自己加)。</div>`;
  for(const b of present) pre += biSub(b.title, b.desc, _presetInner(b.id), b.id);
  if(present.length)
    pre += `<div class="set-actions"><button class="primary" id="preset-save">保存预置接入</button></div>`;
  if(missing.length){
    pre += `<div class="set-row" style="gap:8px;margin-top:8px">
      <button class="pill ghost" id="preset-add">＋ 添加预置接入</button>
      <span class="fhint">可选: ${esc(missing.map(b=>b.title).join(" / "))}</span></div>
      <div class="preset-add-list" style="display:none;margin-top:8px">`
      + missing.map(b=>`<button class="pill ghost preset-pick" data-add="${b.id}">＋ ${esc(b.title)}</button>`).join("")
      + `</div>`;
  }

  /* 项目 = 设备组: sec_search 的聚合数据源; http 设备支持「自检 / 自动适配」 */
  const projects = Array.isArray(d.projects) ? d.projects : [];
  if(projects.length){
    pre += `<div class="bi-sub sec-block"><div class="bi-title-row">
        <div class="bi-title">项目与设备组</div>
        <button class="pill ghost" id="sec-probe-all" title="逐设备校验 token 权限与可达性">自检全部</button>
      </div>
      ${projects.map((pr, pi) => `
        <div class="sec-proj">
          <div class="sec-proj-name">${esc(pr.name)}<span class="sec-count">${(pr.devices||[]).length} 台设备</span></div>
          ${(pr.devices||[]).map((dev, di) => {
            const mapped = !!(dev.mapping && Object.keys(dev.mapping).length);
            const isHttp = dev.kind === "http";
            /* 状态只说可用性: 你的配置是否已能被读成统一告警对象; 适配方式看类型标签 */
            const state = isHttp ? (mapped ? "就绪" : "待映射") : "就绪";
            const ready = !isHttp || mapped;
            const typeTip = isHttp
              ? "通用 HTTP 接入：靠字段映射归一化，未配映射前无法参与聚合检索"
              : (dev.kind === "edr"
                  ? "主机侧专用适配器：支持主机告警查询与进程/文件类证据下钻"
                  : "流量侧专用适配器：支持告警检索、报文下钻与处置回写");
            const hasDoc = !!(dev.api_doc && String(dev.api_doc).trim());
            return `<div class="sec-dev">
              <div class="sec-dev-main"><b>${esc(dev.name||"")}</b>
                <span class="sec-kind" title="${esc(typeTip)}">${esc(dev.kind||"")}</span>
                <span class="sec-addr">${esc(dev.base_url||dev.site||dev.project||"")}</span>
                <span class="sec-state ${ready?"on":""}">${state}</span>
                ${dev.kind === "http" && hasDoc ? `<span class="sec-state on">有文档</span>` : ""}
              </div>
              ${dev.kind === "http"
                ? `<button class="pill ghost sec-doc-toggle" data-p="${pi}" data-d="${di}">文档</button>
                   <button class="pill ghost sec-adapt" data-p="${pi}" data-d="${di}">自动适配</button>`
                : ""}
            </div>
            ${dev.kind === "http" ? `<div class="sec-doc" data-p="${pi}" data-d="${di}" style="display:none">
              <textarea class="sec-doc-input" spellcheck="false"
                placeholder="粘贴该设备的接口文档：告警列表接口的请求参数、响应字段、鉴权方式（只要相关章节即可）。仅在「自动适配」时发给当前模型，保存在本机配置，不上传。">${esc(dev.api_doc||"")}</textarea>
              <div class="set-row" style="margin-top:6px;gap:8px">
                <button class="pill ghost sec-doc-save" data-p="${pi}" data-d="${di}">保存文档</button>
                <span class="fhint">文档会随「自动适配」一起给模型；文档与真实样本冲突时以样本为准。</span>
              </div>
            </div>` : ""}`;
          }).join("")}
        </div>`).join("")}
      <div class="sec-probe-out" style="display:none"></div>
    </div>`;
  }

  renderListPanel(p, "custom_api", "endpoints",
    ()=>({name: "", description: "", base_url: "", api_key: "", method: "GET", capabilities: "knowledge", timeout: 30, verify_ssl: false}),
    (it) => `
      <div class="set-row">
        <input data-f="name" value="${esc(it.name||"")}" placeholder="名称(注册为 api_<名称>)">
        <button class="set-del" data-del title="删除">✕</button>
      </div>
      <div class="set-grid">
        <span>说明</span><input data-f="description" value="${esc(it.description||"")}" placeholder="给模型看的一句话用途说明">
        <span>接口地址</span><input data-f="base_url" value="${esc(it.base_url||"")}" placeholder="https://cmdb.example.com/api/query">
        <span>API Key</span><input data-f="api_key" type="password" value="${esc(it.api_key||"")}" placeholder="可选, Bearer鉴权">
        <span>能力标签</span><input data-f="capabilities" value="${esc(Array.isArray(it.capabilities)?it.capabilities.join(","):(it.capabilities||""))}" placeholder="knowledge / asset / intel / host, 逗号分隔">
        <span>请求方式</span><select data-f="method">
          <option value="GET" ${it.method!=="POST"?"selected":""}>GET</option>
          <option value="POST" ${it.method==="POST"?"selected":""}>POST</option></select>
        <span>校验SSL</span><input type="checkbox" data-f="verify_ssl" ${it.verify_ssl?"checked":""}>
        <span>超时秒</span><input data-f="timeout" data-cast="num" value="${esc(it.timeout||30)}">
      </div>`,
    "新增自定义接入",
    pre);
  /* —— 预置部分绑定(主机侧卡片用.edr-proj区隔endpoints卡片) —— */
  const pbtn = p.querySelector("#sec-probe-all");
  if(pbtn) pbtn.onclick = ()=>{
    pbtn.disabled = true; pbtn.textContent = "自检中…";
    pywebview.api.sec_probe_list().then(r=>{
      pbtn.disabled = false; pbtn.textContent = "自检全部";
      const box = p.querySelector(".sec-probe-out");
      if(!box) return;
      box.style.display = "block";
      if(r && r.error){
        box.innerHTML = `<div class="fhint" style="color:var(--warn)">${esc(r.error)}</div>`;
        return;
      }
      const rows = ((r && r.projects) || []).flatMap(pr =>
        (pr.devices || []).map(x => Object.assign({proj: pr.name}, x)));
      box.innerHTML = `<table class="sec-table"><thead><tr>
          <th>项目</th><th>设备</th><th>状态</th><th>条数</th><th>说明</th></tr></thead><tbody>`
        + rows.map(x=>`<tr>
            <td>${esc(x.proj)}</td><td>${esc(x.device||"")}</td>
            <td>${x.ok ? "可用" : "失败"}</td>
            <td>${x.n_alarms == null ? "-" : esc(String(x.n_alarms))}</td>
            <td class="fhint">${esc(x.error||"")}</td></tr>`).join("")
        + `</tbody></table>`;
    }).catch(e=>{
      pbtn.disabled = false; pbtn.textContent = "自检全部";
      toast("自检失败: " + e, "err");
    });
  };
  p.querySelectorAll(".sec-adapt").forEach(btn=>{
    btn.onclick = ()=>{
      const proj = (d.projects || [])[+btn.dataset.p];
      const dev = ((proj || {}).devices || [])[+btn.dataset.d];
      if(!proj || !dev) return;
      btn.disabled = true; btn.textContent = "生成中…";
      toast("正在取样并让模型生成字段映射，请稍候…", "info", 4000);
      pywebview.api.sec_adapt(proj.name, dev.name).then(r=>{
        btn.disabled = false; btn.textContent = "自动适配";
        if(!r || !r.ok) return toast((r && r.error) || "自动适配失败", "err", 6000);
        showAdaptModal(proj.name, dev.name, r);
      }).catch(e=>{
        btn.disabled = false; btn.textContent = "自动适配";
        toast("自动适配异常: " + e, "err");
      });
    };
  });
  p.querySelectorAll(".sec-doc-toggle").forEach(btn=>{
    btn.onclick = ()=>{
      const box = p.querySelector('.sec-doc[data-p="' + btn.dataset.p + '"][data-d="' + btn.dataset.d + '"]');
      if(!box) return;
      box.style.display = (box.style.display === "none") ? "block" : "none";
    };
  });
  p.querySelectorAll(".sec-doc-save").forEach(btn=>{
    btn.onclick = ()=>{
      const pi = +btn.dataset.p, di = +btn.dataset.d;
      const box = p.querySelector('.sec-doc[data-p="' + pi + '"][data-d="' + di + '"]');
      if(!box) return;
      d.projects[pi].devices[di].api_doc = box.querySelector(".sec-doc-input").value;
      btn.disabled = true; btn.textContent = "保存中…";
      pywebview.api.save_settings({projects: d.projects}).then(r=>{
        btn.disabled = false; btn.textContent = "保存文档";
        if(r && r.error) return toast(r.error, "err");
        toast("接口文档已保存", "ok", 2600);
      }).catch(e=>{
        btn.disabled = false; btn.textContent = "保存文档";
        toast("保存失败: " + e, "err");
      });
    };
  });

  const has = id => present.some(b=>b.id===id);
  if(has("ngsoc")){
    bindField(p, ".ng-ssl", d.ngsoc, "verify_ssl");
    /* 站点卡片绑定(与主机侧项目同款): 保存即内联化(sites_file清空; _imported由后端剔除) */
    d.ngsoc.sites_file = "";
    const nadd = p.querySelector("#ngsoc-add");
    if(nadd) nadd.onclick = ()=>{
      d.ngsoc.sites.push({name: "", url: "", token: "", code: "", apiStyle: "new", enabled: true});
      renderSetPanel("custom_api");
    };
    p.querySelectorAll(".ngsoc-site").forEach(card=>{
      const it = d.ngsoc.sites[+card.dataset.i];
      if(!it) return;
      card.querySelector("[data-nsdel]").onclick = ()=>{
        d.ngsoc.sites.splice(+card.dataset.i, 1);
        renderSetPanel("custom_api");
      };
      card.querySelectorAll("[data-nsf]").forEach(el=>{
        const ev = (el.type === "checkbox" || el.tagName === "SELECT") ? "change" : "input";
        el.addEventListener(ev, ()=>{
          it[el.dataset.nsf] = el.type === "checkbox" ? el.checked : el.value;
        });
      });
    });
  }
  if(has("edr")){
    bindField(p, ".edr-mod", d.edr, "client_module_path");
    const add = p.querySelector("#bi-edr-add");
    if(add) add.onclick = ()=>{ d.edr.projects.push({name:"",server:"",token:""}); renderSetPanel("custom_api"); };
    p.querySelectorAll(".edr-proj").forEach(card=>{
      const it = (d.edr.projects||[])[+card.dataset.i];
      if(!it) return;
      card.querySelector("[data-edrdel]").onclick = ()=>{
        d.edr.projects.splice(+card.dataset.i, 1); renderSetPanel("custom_api");
      };
      card.querySelectorAll("[data-f]").forEach(el=>{
        el.addEventListener("input", ev=>{ it[el.dataset.f] = el.value; });
      });
    });
  }
  if(has("netbox")){
    bindField(p, ".nb-url", d.netbox, "base_url");
    bindField(p, ".nb-token", d.netbox, "token");
    bindField(p, ".nb-ssl", d.netbox, "verify_ssl");
  }
  p.querySelectorAll(".bi-remove").forEach(b=>{
    b.onclick = ()=>{
      d[b.dataset.remove] = null;      // 删除配置节: 保存时写入devices.json, 工具随配置移除
      renderSetPanel("custom_api");
    };
  });
  const addBtn = p.querySelector("#preset-add");
  if(addBtn) addBtn.onclick = ()=>{
    const lst = p.querySelector(".preset-add-list");
    lst.style.display = lst.style.display === "none" ? "block" : "none";
  };
  p.querySelectorAll(".preset-pick").forEach(b=>{
    b.onclick = ()=>{
      const blk = PRESET_BLOCKS.find(x=>x.id===b.dataset.add);
      d[blk.id] = blk.mk();            // 生成默认配置结构 → 子区块出现, 填完保存生效
      renderSetPanel("custom_api");
    };
  });
  const psave = p.querySelector("#preset-save");
  if(psave) psave.onclick = ()=>{
    const secs = {};
    for(const blk of PRESET_BLOCKS) secs[blk.id] = d[blk.id];   // null节=删除
    /* 设备组原本由旧站点/项目内存迁移而来, 保存时一并固化, 否则磁盘上永远没有 projects */
    const migrating = !!d._projects_migrated;
    if(Array.isArray(d.projects) && d.projects.length) secs.projects = d.projects;
    pywebview.api.save_settings(secs).then(r=>{
      if(r && r.ok === false) return toast(r.error || "保存失败", "err");
      toast(r.note || "预置接入已保存并生效", "ok", 5200);
      if(migrating){
        d._projects_migrated = false;
        setTimeout(()=>toast("设备组已写入配置；原站点与项目配置保留但不再参与聚合", "ok", 6000), 600);
      }
    }).catch(()=>toast("保存失败: 桥接异常", "err"));
  };
  /* capabilities 文本 → 逗号拆分数组 */
  p.querySelectorAll("[data-f='capabilities']").forEach(el=>{
    el.addEventListener("change", ()=>{
      const card = el.closest(".set-card");
      const it = _setAll.custom_api.endpoints[+card.dataset.i];
      it.capabilities = el.value.split(/[,，;；]/).map(s=>s.trim()).filter(Boolean);
    });
  });
}

let _skills = null;              // skills_list() 读回
function renderSkills(p){
  if(!_skills){                 // 首次进入该分区时拉取
    p.innerHTML = `<div class="fhint">加载技能清单…</div>`;
    pywebview.api.skills_list().then(r=>{
      _skills = (r && r.skills) ? r : {skills: []};
      renderSetPanel("skills");
    }).catch(()=>{ toast("读取技能失败", "err"); _skills = {skills: []}; renderSetPanel("skills"); });
    return;
  }
  /* 技能只有一张清单: 不区分"内置/自定义", 也不打来源标签——
     随程序带来的技能和自建技能地位一致, 都是可编辑的提示词片段。 */
  const all = (_skills.skills||[]);
  const items = all;
  let html = `<div class="fhint" data-i18n-html="skills-intro" style="margin:0 0 12px">
      技能 = 提示词片段(markdown), 启用后注入系统提示词。<b>研判流程就是技能「安全研判流程」</b>——
      想改研判步骤/输出格式就编辑它, 不用改代码; 编辑会自动另存为本机副本, 升级时你的版本优先。
    </div>`;
  html += `<div class="bi-sub"><div class="bi-title">技能</div>`
    + (items.length ? items.map((s,i)=>`
        <div class="set-card skill-card" data-scope="${s.builtin?"global":s.scope}" data-file="${esc(s.file)}" data-i="${i}">
          <div class="set-row">
            <b class="sk-name">${esc(s.name)}</b>
            <span class="sk-desc">${esc(s.description||"")}</span>
            <label class="fbool"><input type="checkbox" class="sk-enabled" ${s.enabled?"checked":""}>启用</label>
            <button class="pill ghost sk-edit">编辑</button>
            ${s.builtin ? "" : `<button class="set-del sk-del" title="删除">✕</button>`}
          </div>
          <textarea class="sk-body" style="display:none"></textarea>
          <div class="sk-actions" style="display:none">
            <button class="primary sk-save-body">保存内容</button>
            <span class="fhint">名称/说明的修改也在保存内容时生效</span>
          </div>
        </div>`).join("") : `<div class="fhint">暂无技能</div>`)
    + `</div>`;
  html += `<div class="set-row" style="gap:8px">
      <select class="sk-new-scope"><option value="global">全局</option><option value="project">项目级</option></select>
      <input class="sk-new-name" placeholder="新技能名称(如: 周报格式规范)">
      <button class="pill ghost" id="sk-add">＋ 新建技能</button>
    </div>`;
  p.innerHTML = html;

  const refresh = ()=>{
    pywebview.api.skills_list().then(r=>{ _skills = (r && r.ok !== false) ? r : {skills: []}; renderSetPanel("skills"); });
  };
  p.querySelector("#sk-add").onclick = ()=>{
    const name = p.querySelector(".sk-new-name").value.trim();
    if(!name) return toast("请填写技能名称", "warn");
    const scope = p.querySelector(".sk-new-scope").value;
    const fn = name.replace(/[\\/:*?"<>|\s]+/g, "_") + ".md";
    pywebview.api.skills_save(scope, fn, name, "", true,
      "在这里写技能内容(提示词片段), 保存后下一轮对话生效。").then(r=>{
      if(r && r.ok === false) return toast(r.error || "创建失败", "err");
      toast("技能已创建", "ok"); refresh();
    });
  };
  p.querySelectorAll(".skill-card").forEach(card=>{
    const scope = card.dataset.scope, file = card.dataset.file;
    /* 按文件名查找: 内置卡片的 data-scope 是 global(编辑即覆盖), 与列表里 builtin 的 scope 不同 */
    const s = (_skills.skills||[]).find(x=>x.file===file) || {};
    s.name = s.name || card.querySelector(".sk-name").textContent;
    s.content = s.content || "";
    card.querySelector(".sk-enabled").onchange = ev=>{
      pywebview.api.skills_save(scope, file, s.name, s.description, ev.target.checked, s.content)
        .then(r=>{ if(r && r.ok === false) toast(r.error||"保存失败","err"); else { toast(ev.target.checked?"已启用":"已停用","ok"); refresh(); } });
    };
    const delBtn = card.querySelector(".sk-del");
    if(delBtn) delBtn.onclick = ()=>{
      pywebview.api.skills_delete(scope, file).then(r=>{
        if(r && r.ok === false) return toast(r.error||"删除失败","err");
        toast("已删除", "ok"); refresh();
      });
    };
    card.querySelector(".sk-edit").onclick = ()=>{
      const ta = card.querySelector(".sk-body"), acts = card.querySelector(".sk-actions");
      const open = ta.style.display !== "none";
      ta.style.display = open ? "none" : "block";
      acts.style.display = open ? "none" : "flex";
      if(!open) ta.value = s.content;
    };
    card.querySelector(".sk-save-body").onclick = ()=>{
      pywebview.api.skills_save(scope, file, s.name, s.description, s.enabled,
        card.querySelector(".sk-body").value).then(r=>{
        if(r && r.ok === false) return toast(r.error||"保存失败","err");
        toast("技能已保存, 下一轮生效", "ok"); refresh();
      });
    };
  });
}

function renderRag(p){
  const d = _setAll.rag = (_setAll.rag && typeof _setAll.rag==="object") ? _setAll.rag
    : {enabled: false, url: "", api_key: "", method: "POST", query_field: "query", timeout: 30,
       send_top_k: false, verify_ssl: false};
  p.innerHTML = fRow("启用", fBool("rag-on", d.enabled, "注册检索工具后，模型才能在需要时自己调用"))
    + fRow("检索接口地址", fInput("rag-url", d.url, "http://kb.example.com/search"))
    + fRow("查询字段名", fInput("rag-qf", d.query_field, "默认 query；接口要 q / question / keyword 就改这里"))
    + fRow("请求方式", `<select class="rag-method">
        <option value="POST" ${d.method!=="GET"?"selected":""}>POST（JSON body）</option>
        <option value="GET" ${d.method==="GET"?"selected":""}>GET（query 参数）</option></select>`)
    + fRow("API Key", fInput("rag-key", d.api_key, "可选，Bearer 鉴权", "password"))
    + fRow("附带 top_k", fBool("rag-topk", !!d.send_top_k, "默认不发；接口支持条数限制再打开"))
    + fRow("校验SSL", fBool("rag-ssl", !!d.verify_ssl, "内网自签证书请保持关闭"))
    + fRow("超时(秒)", fInput("rag-tmo", d.timeout, "30"))
    + `<div class="set-actions"><button class="pill ghost" id="rag-test">测试检索</button>
       <span class="fhint" id="rag-test-out">用当前填写的地址真发一次请求，不保存配置</span></div>`
    + panelFoot("rag");
  bindField(p, ".rag-on", d, "enabled");
  bindField(p, ".rag-url", d, "url");
  bindField(p, ".rag-key", d, "api_key");
  bindField(p, ".rag-qf", d, "query_field");
  bindField(p, ".rag-tmo", d, "timeout", "num");
  bindField(p, ".rag-topk", d, "send_top_k");
  bindField(p, ".rag-ssl", d, "verify_ssl");
  p.querySelector(".rag-method").onchange = ev=>{ d.method = ev.target.value; };
  const tb = p.querySelector("#rag-test");
  if(tb) tb.onclick = ()=>{
    const out = p.querySelector("#rag-test-out");
    tb.disabled = true; tb.textContent = "测试中…";
    out.textContent = ""; out.style.color = "";
    pywebview.api.rag_test({url: d.url, api_key: d.api_key, method: d.method,
      query_field: d.query_field, timeout: d.timeout,
      send_top_k: !!d.send_top_k, verify_ssl: !!d.verify_ssl}).then(r=>{
      tb.disabled = false; tb.textContent = "测试检索";
      if(!r || !r.ok){
        out.style.color = "var(--err)";
        out.textContent = "失败：" + ((r && r.error) || "未知错误")
          + (r && r.hint ? "（" + r.hint + "）" : "");
        return;
      }
      out.textContent = "成功 " + r.ms + "ms，返回 " + r.chars + " 字符"
        + (r.empty ? "（内容为空，检查字段名或检索库）" : "")
        + "：" + String(r.preview || "").slice(0, 120).replace(/\s+/g, " ");
    }).catch(e=>{
      tb.disabled = false; tb.textContent = "测试检索";
      out.style.color = "var(--err)"; out.textContent = "测试异常：" + e;
    });
  };
  bindSave(p);
}

/* ---- 列表卡片型分区(自定义API/MCP) ---- */
function renderListPanel(p, sec, listKey, mkItem, drawItem, addLabel, extraHtml){
  const d = _setAll[sec] = (_setAll[sec] && typeof _setAll[sec]==="object") ? _setAll[sec] : {};
  const list = d[listKey] = Array.isArray(d[listKey]) ? d[listKey] : [];
  p.innerHTML = (extraHtml || "")
    + `<div class="set-list">` + list.map((it, i) =>
        `<div class="set-card lv-item" data-i="${i}">${drawItem(it, i)}</div>`).join("") + `</div>
      <button class="set-add lv-add">＋ ${esc(addLabel)}</button>`
    + panelFoot(sec);
  p.querySelector(".lv-add").onclick = ()=>{ list.push(mkItem()); renderSetPanel(_setTab); };
  p.querySelectorAll(".lv-item").forEach(card=>{
    const it = list[+card.dataset.i];
    card.querySelectorAll("[data-del]").forEach(b=>{
      b.onclick = ()=>{ list.splice(+card.dataset.i, 1); renderSetPanel(_setTab); };
    });
    card.querySelectorAll("[data-f]").forEach(el=>{
      const key = el.dataset.f;
      const ev = el.type === "checkbox" || el.tagName === "SELECT" ? "change" : "input";
      el.addEventListener(ev, ()=>{
        it[key] = el.type === "checkbox" ? el.checked
                : el.dataset.cast === "num" ? (Number(el.value) || 0)
                : el.value;
      });
    });
  });
  bindSave(p);
  return list;
}


/* MCP 内置模板: 只收录内置能力覆盖不到的服务(文件/联网/脚本/文档/时间已在「连接器」
   与系统提示词里内置, 再装 MCP 会出现同一能力两套实现, 模型选择不可预期)。
   勾选即添加并启用; 取消勾选只停用, 配置保留。 */
const MCP_PRESETS = [
  {icon: "📘", name: "context7", type: "http", url: "https://mcp.context7.com/mcp",
   desc: "第三方库文档与代码示例检索，无需密钥"},
  {icon: "🧠", name: "memory", type: "stdio",
   command: ["npx", "-y", "@modelcontextprotocol/server-memory"],
   desc: "知识图谱长期记忆，跨会话记住实体与关系"},
  {icon: "🗄", name: "sqlite", type: "stdio",
   command: ["npx", "-y", "@modelcontextprotocol/server-sqlite", "path/to/db.sqlite"],
   desc: "查询本地 SQLite 数据库，添加后改成库文件路径"},
  {icon: "🐘", name: "postgres", type: "stdio",
   command: ["npx", "-y", "@modelcontextprotocol/server-postgres",
             "postgresql://user:pass@localhost/db"],
   desc: "查询 PostgreSQL 数据库，添加后改连接串"},
  {icon: "🐙", name: "github", type: "http", url: "https://api.githubcopilot.com/mcp/",
   desc: "仓库代码 / Issue / PR 检索，需在鉴权头填 GitHub Token"},
  {icon: "🎭", name: "playwright", type: "stdio",
   command: ["npx", "-y", "@playwright/mcp@latest"],
   desc: "真实浏览器操作：打开页面、点击、填表、截图"},
  {icon: "💡", name: "sequential-thinking", type: "stdio",
   command: ["npx", "-y", "@modelcontextprotocol/server-sequential-thinking"],
   desc: "结构化分步推理，复杂问题拆解时更稳"},
];

function renderMcp(p){
  const d = _setAll.mcp = (_setAll.mcp && typeof _setAll.mcp === "object") ? _setAll.mcp : {};
  const list = d.servers = Array.isArray(d.servers) ? d.servers : [];
  const byName = n => list.find(x => x && x.name === n);
  const isOn = it => !!it && it.enabled !== false;

  /* ---- 模板卡: 勾选=添加并启用, 取消=只停用(配置保留) ---- */
  const tpl = MCP_PRESETS.map((m, i) => {
    const it = byName(m.name);
    const on = isOn(it);
    const state = on ? "已启用" : (it ? "已停用" : "未添加");
    return `<div class="conn-card mcp-preset ${on ? "" : "off"}" data-preset="${i}">
      <div class="cc-top">
        <span class="cc-ico">${m.icon}</span>
        <div class="cc-title"><b>${esc(m.name)}</b><small>${esc(m.desc)}</small></div>
        <label class="cc-switch" title="${on ? "已启用，点击停用" : "点击添加并启用"}">
          <input type="checkbox" class="mcp-tpl" data-preset="${i}" ${on ? "checked" : ""}><span></span>
        </label>
      </div>
      <div class="cc-foot"><span class="cc-state">${m.type === "http" ? "HTTP" : "stdio"}</span>
        <span class="cc-state mcp-tpl-state">${state}</span></div>
    </div>`;
  }).join("");

  /* ---- 已添加: 简洁行, 详情折在下面 ---- */
  const rows = list.length ? list.map((it, i) => {
    const on = isOn(it);
    const isStdio = it.type === "stdio";
    return `<div class="mcp-row ${on ? "" : "off"}" data-i="${i}">
      <b class="mcp-name" title="${esc(it.name || "")}">${esc(it.name || "(未命名)")}</b>
      <span class="mcp-kind">${isStdio ? "stdio" : "HTTP"}</span>
      <span class="mcp-state ${on ? "on" : ""}">${on ? "已启用" : "已停用"}</span>
      <label class="cc-switch" title="${on ? "点击停用" : "点击启用"}">
        <input type="checkbox" class="mcp-on" data-i="${i}" ${on ? "checked" : ""}><span></span>
      </label>
      <button class="pill ghost mcp-det" data-i="${i}">详情</button>
      <button class="set-del" data-del="${i}" title="删除该服务器">✕</button>
    </div>
    <div class="mcp-detail" data-det="${i}" hidden>
      <div class="set-grid">
        <span>名称</span><input data-f="name" value="${esc(it.name || "")}" placeholder="工具注册为 mcp__名称__工具">
        <span>传输</span><select data-f="type">
          <option value="http" ${!isStdio ? "selected" : ""}>http (Streamable HTTP)</option>
          <option value="stdio" ${isStdio ? "selected" : ""}>stdio (本地命令行)</option></select>
        <span>HTTP地址</span><input data-f="url" value="${esc(it.url || "")}" placeholder="https://mcp.example.com/mcp">
        <span>stdio命令</span><input class="mcp-cmd" value="${esc(Array.isArray(it.command) ? it.command.join(" ") : (it.command || ""))}" placeholder="http型留空; stdio型: npx -y xxx-server">
        <span>鉴权头</span><input class="mcp-hdr" value="${esc(it.headers ? JSON.stringify(it.headers) : "")}" placeholder='JSON, 如 {"Authorization":"Bearer xx"}'>
      </div>
      <div class="set-row">
        <button class="pill ghost mcp-test">测试连接</button>
        <span class="mcp-test-result"></span>
      </div>
    </div>`;
  }).join("") : `<div class="fhint" style="padding:10px 0">还没有添加任何 MCP 服务器。</div>`;

  p.innerHTML = `<div class="bi-sub sec-block"><div class="bi-title">内置模板
      <span class="sec-count">勾选即添加并启用</span></div>
      <div class="conn-grid">${tpl}</div></div>
    <div class="bi-sub sec-block"><div class="bi-title">已添加的服务器
      <span class="sec-count">${list.filter(isOn).length} 个启用 / 共 ${list.length} 个</span></div>
      <div class="mcp-list">${rows}</div>
      <button class="set-add mcp-add" type="button">＋ 自定义服务器</button>
    </div>
    <div class="fhint" data-i18n-html="mcp-intro" style="margin:8px 0 12px">MCP 工具注册为 mcp__&lt;名称&gt;__&lt;工具&gt;，
      <b>改动需点「保存本节」才生效</b>（保存后自动重连）。文件读写、联网抓取、脚本执行、Office 文档处理已内置，
      不要再为这些能力装 MCP。</div>`
    + panelFoot("mcp");

  /* 模板开关 */
  p.querySelectorAll(".mcp-tpl").forEach(el => {
    el.onchange = () => {
      const m = MCP_PRESETS[+el.dataset.preset];
      if(!m) return;
      const cur = byName(m.name);
      if(el.checked){
        if(cur) cur.enabled = true;
        else list.push({name: m.name, type: m.type, url: m.url || "",
                        command: m.command ? m.command.slice() : [], enabled: true});
        toast("已启用 " + m.name + "，点「保存本节」生效", "ok", 2600);
      }else{
        if(cur) cur.enabled = false;
        toast("已停用 " + m.name, "ok", 2000);
      }
      renderSetPanel("mcp");
    };
  });

  /* 每行开关: 只改状态, 不整页重画 */
  p.querySelectorAll(".mcp-on").forEach(el => {
    el.onchange = () => {
      const it = list[+el.dataset.i];
      if(!it) return;
      it.enabled = el.checked;
      const row = el.closest(".mcp-row");
      if(row){
        row.classList.toggle("off", !el.checked);
        const st = row.querySelector(".mcp-state");
        if(st){ st.textContent = el.checked ? "已启用" : "已停用"; st.classList.toggle("on", el.checked); }
      }
      toast((el.checked ? "已启用 " : "已停用 ") + (it.name || ""), "ok", 1800);
    };
  });

  /* 详情展开 */
  p.querySelectorAll(".mcp-det").forEach(b => {
    b.onclick = () => {
      const box = p.querySelector('.mcp-detail[data-det="' + b.dataset.i + '"]');
      if(!box) return;
      const open = box.hidden;
      box.hidden = !open;
      b.textContent = open ? "收起" : "详情";
    };
  });

  /* 删除 */
  p.querySelectorAll("[data-del]").forEach(b => {
    b.onclick = () => {
      const it = list[+b.dataset.del];
      if(!it) return;
      if(!confirm("删除 MCP 服务器「" + (it.name || "") + "」？保存后其工具将下线。")) return;
      list.splice(+b.dataset.del, 1);
      renderSetPanel("mcp");
    };
  });

  /* 新增自定义 */
  const addBtn = p.querySelector(".mcp-add");
  if(addBtn) addBtn.onclick = () => {
    const base = "自定义服务器"; let n = 2, name = base;
    while(byName(name)) name = base + n++;
    list.push({name, type: "http", url: "", enabled: true});
    renderSetPanel("mcp");
  };

  /* 字段绑定(详情内) */
  p.querySelectorAll(".mcp-row").forEach(row => {
    const it = list[+row.dataset.i];
    const box = p.querySelector('.mcp-detail[data-det="' + row.dataset.i + '"]');
    if(!it || !box) return;
    box.querySelectorAll("[data-f]").forEach(el => {
      const key = el.dataset.f;
      const ev = el.tagName === "SELECT" ? "change" : "input";
      el.addEventListener(ev, () => { it[key] = el.value; });
    });
    const cmdEl = box.querySelector(".mcp-cmd");
    cmdEl.addEventListener("change", () => {
      it.command = cmdEl.value.trim() ? cmdEl.value.trim().split(/\s+/) : [];
    });
    const hEl = box.querySelector(".mcp-hdr");
    hEl.addEventListener("change", () => {
      const t = hEl.value.trim();
      if(!t){ delete it.headers; hEl.style.borderColor = ""; return; }
      try{ it.headers = JSON.parse(t); hEl.style.borderColor = ""; }
      catch(e){ hEl.style.borderColor = "var(--err)"; toast("鉴权头不是合法 JSON", "warn"); }
    });
    box.querySelector(".mcp-test").onclick = () => {
      const out = box.querySelector(".mcp-test-result");
      out.textContent = "连接中…";
      const spec = {name: it.name || "test", type: it.type, url: it.url,
                    command: it.command, headers: it.headers};
      pywebview.api.mcp_test(spec).then(r => {
        if(r && r.ok) out.textContent = `✓ 连通，${r.n_tools} 个工具：${(r.tools || []).join(", ").slice(0, 60)}`;
        else out.textContent = "✗ " + ((r && r.error) || "失败");
      }).catch(() => { out.textContent = "✗ 桥接异常"; });
    };
  });

  bindSave(p);
}

/* ---- 大模型分区: 厂商快捷接入(只填 Key) + 自定义配置(原表单保留) ---- */
let _provCache = null;      // 厂商预设, 只拉一次
let _provOpen  = "";        // 当前展开的厂商 id

/* 找出该厂商已接入的 profile(优先 provider 标记, 兼容老配置按地址匹配)。
   老配置可能写 https://api.deepseek.com, 预设是 .../v1, 两个方向都要认。 */
function _provHit(cur, prov){
  const norm = u => String(u||"").replace(/\/+$/,"");
  return Object.entries(cur.profiles || {}).find(([,v])=>{
    if(!v) return false;
    if(v.provider === prov.id) return true;
    const u = norm(v.base_url), b = norm(prov.base_url);
    return !!u && !!b && (u.startsWith(b) || b.startsWith(u));
  }) || null;
}
function _fmtCtx(n){
  n = Number(n) || 0;
  if(n >= 1000000) return (n/1000000).toFixed(n % 1000000 ? 1 : 0) + "M";
  if(n >= 1000) return Math.round(n/1000) + "K";
  return n ? String(n) : "";
}
function _provNote(prov){
  const n = prov && prov.note;
  if(!n) return "";
  if(typeof n === "string") return n;
  return n[CUR_LANG()] || n.zh || "";
}

function renderLlmPanel(p){
  if(!_provCache){
    p.innerHTML = `<div class="fhint">${esc(T("加载模型厂商…"))}</div>`;
    apiCall("llm_providers").then(r=>{
      _provCache = (r && r.providers) || [];
      renderSetPanel("llm");
    }).catch(()=>{ _provCache = []; renderSetPanel("llm"); });
    return;
  }
  const cur = _setAll.llm = (_setAll.llm && _setAll.llm.profiles) ? _setAll.llm : {active: "", profiles: {}};

  /* ---- 1. 厂商卡片 ---- */
  const cards = _provCache.map(prov=>{
    const hit = _provHit(cur, prov);
    const on = !!hit;
    const isDefault = on && cur.active === hit[0];
    const badge = on ? (isDefault ? T("当前默认") : T("已接入")) : T("填 Key 即用");
    return `<button class="prov-card${on?" on":""}${_provOpen===prov.id?" sel":""}" data-pid="${esc(prov.id)}">
      <b>${esc(prov.name)}</b><span>${esc(badge)}</span></button>`;
  }).join("");

  /* ---- 2. 展开的接入表单 ---- */
  let form = "";
  const prov = _provCache.find(x=>x.id === _provOpen);
  if(prov){
    const hit = _provHit(cur, prov);
    const saved = hit ? hit[1] : {};
    // 同厂商官方地址 + 模型名不在预设里(通常是厂商改名/下线): 默认给推荐新模型;
    // 自建网关/中转站(地址不同)则保留用户自己的模型名, 不擅自替换。
    const normU = u => String(u||"").replace(/\/+$/,"");
    const sameHost = !saved.base_url || !prov.base_url
      || normU(saved.base_url).startsWith(normU(prov.base_url))
      || normU(prov.base_url).startsWith(normU(saved.base_url));
    const firstId = ((prov.models||[])[0]||{}).id || "";
    const savedInList = (prov.models||[]).some(m=>m.id === saved.model);
    const model = (saved.model && (savedInList || !sameHost)) ? saved.model : (firstId || saved.model || "");
    const inList = (prov.models||[]).some(m=>m.id === model);
    const opts = (prov.models||[]).map(m=>
      `<option value="${esc(m.id)}"${m.id===model?" selected":""}>${esc(m.id)}${m.ctx?" · "+_fmtCtx(m.ctx):""}</option>`
    ).join("") + `<option value="__custom"${inList?"":" selected"}>${esc(T("其他模型(手动输入)"))}</option>`;
    form = `<div class="prov-form">
      <div class="prov-form-h"><b>${esc(T("接入"))} ${esc(prov.name)}</b>
        <button class="prov-x" title="${esc(T("收起"))}">✕</button></div>
      <div class="prov-row"><span>${esc(T("模型"))}</span>
        <select class="pf-model">${opts}</select></div>
      <div class="prov-row pf-custom-row" style="${inList?"display:none":""}"><span></span>
        <input class="pf-custom" placeholder="${esc(T("模型名，可手动填写"))}" value="${inList?"":esc(model)}"></div>
      <div class="prov-row"><span>API Key</span>
        <input class="pf-key" type="password" autocomplete="off"
          value="${esc(saved.api_key||"")}" placeholder="sk-…"></div>
      ${_provNote(prov) ? `<div class="prov-note">${esc(_provNote(prov))}</div>` : ""}
      <div class="set-actions">
        <button class="pill ghost pf-test">${esc(T("测试连接"))}</button>
        <button class="primary pf-add">${esc(T("接入并使用"))}</button>
        <span class="pf-out"></span>
      </div>
    </div>`;
  }

  /* ---- 3. 自定义配置(原表单) ---- */
  let rows = "";
  for(const [name, prof] of Object.entries(cur.profiles)){
    rows += `
    <div class="set-card" data-name="${esc(name)}">
      <div class="set-row">
        <label class="set-radio"><input type="radio" name="llm-active" ${name===cur.active?"checked":""}>${esc(T("默认"))}</label>
        <input class="set-name" value="${esc(name)}" placeholder="${esc(T("配置名称"))}">
        <button class="set-del" title="${esc(T("删除该配置"))}">✕</button>
      </div>
      <div class="set-grid">
        <span>${esc(T("接口地址"))}</span><input class="set-url" value="${esc(prof.base_url||"")}" placeholder="https://api.deepseek.com/v1">
        <span>${esc(T("模型名"))}</span><input class="set-model" value="${esc(prof.model||"")}" placeholder="deepseek-v4-flash">
        <span>${esc(T("上下文"))}</span><input class="set-ctx" value="${esc(prof.context||"")}" placeholder="${esc(T("128000(可选)"))}">
        <span>API Key</span><input class="set-key" type="password" value="${esc(prof.api_key||"")}" placeholder="sk-...">
      </div>
    </div>`;
  }

  p.innerHTML = `
    <div class="prov-sect-t">${esc(T("快速接入"))}</div>
    <div class="prov-grid">${cards}</div>
    ${form}
    <div class="prov-sect-t custom-t">${esc(T("自定义配置"))}
      <span class="fhint">${esc(T("接口地址与模型名自己填，兼容任何 OpenAI 协议服务"))}</span></div>
    <div class="set-list">${rows}</div>
    <button class="set-add">＋ ${esc(T("新增模型配置"))}</button>`
    + panelFoot("llm");

  /* 卡片点击: 展开/收起该厂商表单 */
  p.querySelectorAll(".prov-card").forEach(b=>{
    b.onclick = ()=>{
      _provOpen = (_provOpen === b.dataset.pid) ? "" : b.dataset.pid;
      renderSetPanel("llm");
    };
  });
  if(prov){
    const box = p.querySelector(".prov-form");
    const sel = box.querySelector(".pf-model");
    const cus = box.querySelector(".pf-custom");
    const cusRow = box.querySelector(".pf-custom-row");
    const keyEl = box.querySelector(".pf-key");
    const out = box.querySelector(".pf-out");
    sel.onchange = ()=>{
      const manual = sel.value === "__custom";
      cusRow.style.display = manual ? "" : "none";   // 整行显隐, 避免输入框被挤没
      if(manual) cus.focus();
    };
    box.querySelector(".prov-x").onclick = ()=>{ _provOpen = ""; renderSetPanel("llm"); };
    const draft = ()=>{
      const m = sel.value === "__custom" ? cus.value.trim() : sel.value;
      const mm = (prov.models||[]).find(x=>x.id === m);
      const prof = {provider: prov.id, base_url: prov.base_url, model: m,
                    api_key: keyEl.value.trim()};
      if(mm && mm.ctx) prof.context = mm.ctx;
      return prof;
    };
    box.querySelector(".pf-test").onclick = ev=>{
      const prof = draft();
      if(!prof.api_key) return toast(T("请先填写 API Key"), "warn");
      const btn = ev.currentTarget;
      btn.disabled = true; out.className = "pf-out"; out.textContent = T("测试中…");
      apiCall("test_llm_config", prof).then(r=>{
        btn.disabled = false;
        if(r && r.ok){ out.className = "pf-out ok"; out.textContent = "✓ " + (r.detail || T("连接正常")); }
        else { out.className = "pf-out err"; out.textContent = "✗ " + ((r && r.error) || T("连接失败")); }
      }).catch(()=>{ btn.disabled = false; out.className = "pf-out err"; out.textContent = "✗ " + T("桥接异常"); });
    };
    box.querySelector(".pf-add").onclick = ev=>{
      const prof = draft();
      if(!prof.api_key) return toast(T("请先填写 API Key"), "warn");
      if(!prof.model) return toast(T("请先选择或填写模型名"), "warn");
      const btn = ev.currentTarget;
      btn.disabled = true;
      cur.profiles[prov.id] = prof;    // 厂商 id 即配置名, 重复接入=更新
      cur.active = prov.id;
      pywebview.api.save_llm_config(cur.active, cur.profiles).then(r=>{
        btn.disabled = false;
        if(r && r.error) return toast(r.error, "err");
        toast(T("已接入并设为默认模型") + " · " + prov.name, "ok");
        _provOpen = "";
        renderSetPanel("llm");
      }).catch(()=>{ btn.disabled = false; toast(T("保存失败: 桥接异常"), "err"); });
    };
  }

  p.querySelector(".set-add").onclick = ()=>{
    const base = T("新配置"); let n = 2, name = base;
    while(cur.profiles[name]) name = base + n++;
    cur.profiles[name] = {base_url: "", api_key: "", model: ""};
    renderSetPanel(_setTab);
  };
  p.querySelectorAll(".set-card").forEach(card=>{
    const name = card.dataset.name;
    card.querySelector(".set-del").onclick = ()=>{
      if(Object.keys(cur.profiles).length <= 1) return toast(T("至少保留一个配置"), "warn");
      if(card.querySelector(".set-radio input").checked)
        cur.active = Object.keys(cur.profiles).find(k=>k!==name) || "";
      delete cur.profiles[name];
      renderSetPanel(_setTab);
    };
    card.querySelector(".set-radio input").onchange = ev=>{ cur.active = name; };
    const bind = (sel, key) => card.querySelector(sel).addEventListener("input",
      ev=>{ cur.profiles[name][key] = ev.target.value; });
    bind(".set-url", "base_url");
    bind(".set-model", "model");
    bind(".set-ctx", "context");
    bind(".set-key", "api_key");
    card.querySelector(".set-name").addEventListener("change", ev=>{
      const nn = ev.target.value.trim();
      if(!nn || nn === name) return;
      if(cur.profiles[nn]){ ev.target.value = name; return toast(T("名称已存在"), "warn"); }
      cur.profiles[nn] = cur.profiles[name];
      if(cur.active === name) cur.active = nn;
      delete cur.profiles[name];
      renderSetPanel(_setTab);
    });
  });
  bindSave(p);
}

$("#btn-settings").onclick = ()=>{ openSettings(); };
/* 欢迎页能力卡片: 按当前专家动态渲染(state.persona_cards), 点击把示例任务填入输入框 */
const FALLBACK_CARDS = [
  {icon:"🛡", title:"告警研判", sub:"批量分诊 · 行为链 · 处置建议",
   prompt:"帮我研判今天未处理的高危告警，先做批量分诊再给处置建议"},
  {icon:"💻", title:"写代码 / 跑命令", sub:"文件 · 脚本 · 数据分析",
   prompt:"帮我写一个 Python 脚本统计日志并输出报表"},
  {icon:"📚", title:"查知识库", sub:"判据 · 历史处置 · 规范",
   prompt:"检索知识库，总结这类事件的处置判据与历史案例"},
  {icon:"🔌", title:"查看已接入能力", sub:"系统API · MCP · 插件",
   prompt:"列出当前已接入的系统能力，并说明每类能查什么"},
];
let _wcPersona = null;          // 已渲染专家id: 同专家不重画, 避免闪烁
function renderWelcomeCards(cards, persona){
  const grid = $("#wc-grid");
  if(!grid) return;
  const pid = persona || "ezcode";
  if(_wcPersona === pid && grid.childElementCount) return;
  _wcPersona = pid;
  const list = (Array.isArray(cards) && cards.length) ? cards : FALLBACK_CARDS;
  grid.innerHTML = "";
  for(const c of list.slice(0, 8)){
    const b = document.createElement("button");
    b.className = "wc-card";
    b.dataset.prompt = c.prompt || "";
    b.innerHTML = `<span class="wc-ico"></span><b></b><small></small>`;
    b.querySelector(".wc-ico").textContent = c.icon || "💡";
    b.querySelector("b").textContent = c.title || "";
    b.querySelector("small").textContent = c.sub || "";
    b.onclick = ()=>{
      const t = $("#input");
      if(!t) return;
      t.value = b.dataset.prompt || "";
      t.dispatchEvent(new Event("input", {bubbles:true}));
      t.focus();
      hideWelcome();
    };
    grid.appendChild(b);
  }
}
/* ---------- ＋ 加号菜单: 附件 / 专家 / 工具显示 / 主题 / 设置 ---------- */
function toggleTheme(){
  const html = document.documentElement;
  const next = html.dataset.theme === "dark" ? "light" : "dark";
  html.dataset.theme = next;
  try{ localStorage.setItem("ezcode-theme-v2", next); }catch(e){}
  toast(next === "dark" ? "已切换到暗色主题" : "已切换到明亮主题", "ok");
}
/* 原生文件选择 → 插入 @路径 引用(带空格的路径用引号包裹, 引擎侧同步支持) */
function attachFile(){
  const t = $("#input");
  if(!t) return;
  const addToken = token=>{
    const pre = t.value && !/\s$/.test(t.value) ? " " : "";
    t.value += pre + token + " ";
    t.dispatchEvent(new Event("input", {bubbles:true}));
    t.focus();
    hideWelcome();
  };
  if(!(window.pywebview && pywebview.api && pywebview.api.pick_attach_file)){
    addToken("@");
    toast("在 @ 后粘贴文件绝对路径即可附加", "ok", 4200);
    return;
  }
  pywebview.api.pick_attach_file().then(r=>{
    if(!r || !r.ok){
      if(r && r.error && r.error !== "未选择") toast(r.error, "warn");
      return;
    }
    const p = String(r.path || "");
    addToken(/[\s]/.test(p) ? '@"' + p + '"' : "@" + p);
    toast("已附加文件证据", "ok");
  }).catch(()=>toast("文件选择失败: 桥接异常", "err"));
}
$("#btn-plus").onclick = e=>{ e.stopPropagation(); toggleMenu("#plus-menu", e.currentTarget); };
$("#plus-menu").querySelectorAll("[data-act]").forEach(b=>{
  b.onclick = ()=>{
    hideMenus();
    switch(b.dataset.act){
      case "attach":    attachFile(); break;
      case "expert":    openExperts(); break;
      case "toolview":  cycleToolView(); break;
      case "theme":     toggleTheme(); break;
      case "settings":  openSettings(); break;
    }
  };
});

/* ---------- 专家中心子项: 默认收起「当前任务专家」, 右侧箭头可展开 ---------- */
function setPersonaFold(open){
  const box = $("#persona-collapse"), btn = $("#btn-persona-fold");
  if(!box || !btn) return;
  box.classList.toggle("open", !!open);
  btn.classList.toggle("on", !!open);
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  btn.title = open ? "收起当前任务专家" : "展开当前任务专家";
  try{ localStorage.setItem("ezcode-persona-fold", open ? "open" : "closed"); }catch(e){}
}
(function initPersonaFold(){
  let open = false;
  try{ open = localStorage.getItem("ezcode-persona-fold") === "open"; }catch(e){}
  setPersonaFold(open);
  const btn = $("#btn-persona-fold");
  if(btn) btn.onclick = e=>{
    e.stopPropagation();
    setPersonaFold(!$("#persona-collapse").classList.contains("open"));
  };
})();

/* ---------- 滚动跟随 ----------
   判据从"scrollTop 差值"改成"真实输入事件", 因为切窗口/内容增长/布局变化都会让
   scrollTop 变化, 差值推断必然误判(用户遇到的两个现象都是它造成的):
   · 切出去再回来停在离开处 → 回来时的 scroll 事件被判成"用户上滚", autoScroll 被关掉;
   · 上滚一格直接跳到很早的位置 → 判据混乱后既没停跟随也没接管, 与内容增长互相拉扯。

   规则:
   ① 程序跟随: scrollBottom 记录目标 _progTo, scroll 事件命中目标即跳过;
   ② 用户意图: wheel / 触摸 / PageUp 等按键打 _userTs 时间戳(400ms 窗口);
   ③ 有意图的滚动 → 按是否接近底部(70px)决定 autoScroll: 贴底跟随 / 用户接管;
   ④ 无意图的滚动(窗口恢复、布局变化、内容增长) → 不改 autoScroll,
      跟随态下补滚到底 —— 所以切出去工作再回来, 回答照样停在最新；
   ⑤ 窗口重新可见 / 重新获得焦点 → 清掉程序滚动残留标记, 跟随态强制贴底。 */
let _userTs = 0;       // 最近一次真实输入的时间戳
let _userLockUntil = 0;  // 用户主动上翻后的保护期: 期间 scrollBottom 一律不执行

/* 容差必须很小: 原来 70px 意味着"离底部 70px 内仍算跟随",
   用户上滚 50px 想看清中间内容, 下一帧就被 scrollBottom 拽回底部 —— 这段滚动被吞掉,
   表现就是"一滑就跳过去/回到提问处"。只有真正滚到底(8px)才恢复跟随。 */
function nearBottom(el, pad){
  return el.scrollHeight - el.scrollTop - el.clientHeight <= (pad || 8);
}
function markUserScroll(){ _userTs = Date.now(); }

$("#chat").addEventListener("scroll", ()=>{
  const chat = $("#chat");
  if(!chat) return;
  /* 程序滚动: 命中目标值 */
  if(_progTo >= 0 && Math.abs(chat.scrollTop - _progTo) <= 3){
    _progTo = -1; syncJumpBtn(); return;
  }
  _progTo = -1;
  const fresh = Date.now() - _userTs < 400;     // 刚有真实输入?
  if(!fresh){
    /* 非用户滚动: 不改状态。若仍在跟随态(切窗口回来/流式增长), 补滚到底 */
    if(ui.autoScroll) scrollBottom();
    syncJumpBtn(); return;
  }
  if(nearBottom(chat)){
    ui.autoScroll = true;                       // 用户主动滚到真正的底部 → 恢复跟随
    _userLockUntil = 0;
  }else{
    ui.autoScroll = false;                      // 其他位置: 保持接管
  }
  syncJumpBtn();
});

/* 真实输入事件: 只有这些才算"用户在滚" */
$("#chat").addEventListener("wheel", e=>{
  markUserScroll();
  if(e.deltaY < 0){
    /* 向上滚: 立刻交还控制权, 并进入 1.5s 保护期 —— 这期间无论内容怎么增长,
       scrollBottom 都不许动视口, 用户能安稳地把中间过程看完 */
    ui.autoScroll = false;
    _userLockUntil = Date.now() + 1500;
    syncJumpBtn();
  }
}, {passive:true});
$("#chat").addEventListener("touchstart", markUserScroll, {passive:true});
$("#chat").addEventListener("touchmove", markUserScroll, {passive:true});
$("#chat").addEventListener("mousedown", e=>{
  if(e.target && e.target.id === "chat") markUserScroll();     // 拖动滚动条
});
$("#chat").addEventListener("keydown", e=>{
  if(/^(PageUp|PageDown|ArrowUp|ArrowDown|Home|End| )$/.test(e.key || "")) markUserScroll();
});

$("#jump-bottom").onclick = ()=>{
  ui.autoScroll = true;
  _userLockUntil = 0;
  _progTo = -1;
  scrollBottom(true);
  syncJumpBtn();
};

/* 窗口切回: 清掉后台残留标记, 跟随态贴到最新 */
function resumeFollow(){
  _progTo = -1;
  _lastFollowAt = 0;
  if(Date.now() < _userLockUntil) return;   // 用户正在上翻看历史: 切回窗口也别动
  if(ui.autoScroll) scrollBottom(true);
  syncJumpBtn();
}
document.addEventListener("visibilitychange", ()=>{ if(!document.hidden) resumeFollow(); });
window.addEventListener("focus", resumeFollow);

/* ---------- 启动 ----------
   不依赖 pywebviewready 事件时序: 事件可能在真实页面加载前触发(或在注入完成后
   才注册监听)而丢失。这里用"事件 + 立即尝试 + 轮询"三重保险, 且只在真实页面
   DOM(#chat)已存在且 pywebview.api 已注入时才握手。 */
function boot(){
  if(window.__ezcodeBooted) return;
  if(!document.getElementById("chat")) return;
  if(!(window.pywebview && window.pywebview.api)) return;
  // pywebview.api 注入早期可能是占位对象, 调用会静默失败。以 bootstrap() 真正
  // 返回作为握手成功标志; 未成功则 1.5s 后由轮询重试, 绝不提前锁死 boot。
  if(window.__bootPending && Date.now() - (window.__bootAt || 0) < 800) return;
  window.__bootPending = true;
  window.__bootAt = Date.now();
  pywebview.api.bootstrap().then(d=>{
    if(window.__ezcodeBooted) return;
    if(!d || d.ok === false){        // 引擎还在后台初始化, 保留占位并交给轮询重试
      window.__bootPending = false;
      return;
    }
    window.__ezcodeBooted = true;
    window.__bootPending = false;
    try{ const t = localStorage.getItem("ezcode-theme-v2"); if(t) document.documentElement.dataset.theme = t; }catch(e){}
    /* 语言: 引擎侧 devices.json 的 ui.lang 是唯一事实源, 首屏按它落定, 避免中英混排 */
    try{ if(typeof setLang === "function") setLang((d.state && d.state.lang) || "zh"); }catch(e){}
    applyState(d.state);
    /* 自动更新: 握手自带状态(省一次COM往返), 同时把侧栏新版本徽标接上设置页 */
    try{
      if(d.update) onUpdateEvent(d.update);
      else updApi("update_state").then(x=>{ if(x) onUpdateEvent(x); }).catch(()=>{});
      const ub = $("#update-badge");
      if(ub) ub.onclick = ()=>openSettings("update");
    }catch(e){}
    applyToolViewBtn();            // 恢复工具显示档位按钮文案(localStorage)
    streamBox().innerHTML = "";              // 清掉首屏"正在连接引擎"占位
    /* 恢复当前会话历史 */
    const list = d.history || [];
    if(list.length){
      hideWelcome();
      for(const m of list){
        if(m.kind === "user") addUser(m.text);
        else addBotFull(m.text);
      }
      addSys("已恢复会话上下文, 继续对话即可");
    }else{
      showWelcome();                 // 新任务: 欢迎页 + 能力卡片
    }
    scrollBottom(true);
    paintSessions(d.sessions || [], d.state.sid);   /* 用握手自带数据, 省两次COM往返 */
    pywebview.api.personas_list().then(r=>{         /* 专家清单缓存: 自建专家的UI文案 */
      if(r && Array.isArray(r.personas)){ ui.personas = r.personas; refreshState(); }
    }).catch(()=>{});
    try{ pywebview.api.boot_done(); }catch(e){}     /* 首屏渲染完成标记 */
  }).catch(()=>{
    window.__bootPending = false;            // 握手未完成, 交给轮询重试
  });
}
window.addEventListener("pywebviewready", boot);
boot();
const bootTimer = setInterval(()=>{
  boot();
  if(window.__ezcodeBooted) clearInterval(bootTimer);
}, 80);
