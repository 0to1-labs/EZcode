"use strict";
/* 渲染器回归测试: 从 app.js 提取纯函数, headless 验证排版修复 */
const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "app", "webui", "app.js"), "utf8");
const grab = names => {
  let code = "";
  for(const n of names){
    let m = src.match(new RegExp("function " + n + "\\([\\s\\S]*?\\n}", "m"))
         || src.match(new RegExp("const " + n + " = [\\s\\S]*?\\n(?=\\/\\*|function|const )", "m"));
    if(!m){ console.error("未找到函数", n); process.exit(1); }
    code += m[0] + "\n";
  }
  return code;
};
const fns = new Function(grab(["esc", "mdInline", "splitRow", "indentOf", "renderList", "hlCode", "renderMD"])
  + "\nreturn {esc, mdInline, hlCode, renderMD, renderList};")();
const { mdInline, hlCode, renderMD } = fns;
let pass = 0, fail = 0;
const t = (name, ok, detail) => {
  if(ok){ pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ " + name + (detail ? " -- " + String(detail).slice(0, 220) : "")); }
};

/* ── 代码高亮 ── */
let h = hlCode("x = 'abc # hash'  # real comment\ny = 123", "python");
t("单引号字符串(含#)完整染色", /tok-s">&#39;abc # hash&#39;/.test(h), h);
t("字符串后真注释被染色", /tok-c"># real comment</.test(h), h);
t("注释不吞字符串", !/tok-c">[^<]*hash&#39;/.test(h), h);
h = hlCode("v = '2024-01-02'  # date", "python");
t("字符串内数字不套染", !/tok-s[^>]*>[^<]*<span/.test(h), h);
h = hlCode('a = "dq # s" + 1  // js', "js");
t("JS字符串", /tok-s">&quot;dq # s&quot;/.test(h), h);
t("JS注释", /tok-c">\/\/ js/.test(h), h);
h = hlCode('s = "q"  # c\nif True:\n    pass', "python");
t("关键字+缩进代码", /tok-k">if<\/span>/.test(h) && /tok-k">True</.test(h) && /tok-k">pass</.test(h), h);
h = hlCode("# pure comment line", "python");
t("纯注释行", /tok-c"># pure comment line</.test(h), h);
h = hlCode("'#39;entity trap' + 42", "python");
t("实体陷阱不再误染", !/&amp;#/.test(h), h);
t("数字染色", /tok-n">42</.test(h), h);

/* ── 行内元素 ── */
h = mdInline("正常 **粗体** 和 `code **nb** end`");
t("行内code不被粗体污染", !/code <strong>/.test(h), h);
t("code外部粗体保留", /<strong>粗体<\/strong>/.test(h), h);
h = mdInline("[链接](https://example.com)");
t("链接渲染", /<a href="https:\/\/example.com"/.test(h), h);

/* ── 块级渲染 ── */
h = renderMD("- a\n  - a1\n  - a2\n- b");
t("嵌套列表子项在父项li内", /<li>a<ul><li>a1<\/li><li>a2<\/li><\/ul><\/li>/.test(h.replace(/\n/g, "")), h);
h = renderMD("1. one\n2. two\n   - two-a\n3. three");
t("有序含无序子层", h.includes("<ol>") && h.includes("<ul>"), h);
h = renderMD("| H1 | H2 |\n|---|---|\n| a | b |");
t("表格", h.includes("<table>") && h.includes("<th") && h.includes("<td"), h);
h = renderMD("## T\n\n> quote\n\n```js\nconst x = 1; // c\n```\n\ntail");
t("标题", h.includes("<h2>T</h2>"), h);
t("引用", h.includes("<blockquote>quote</blockquote>"), h);
t("JS代码块注释染色", h.includes("tok-c"), h);
t("代码块后正文", h.includes("<p>tail</p>"), h);
h = renderMD("text before\n```python\nprint(1)");
t("未闭合代码块容错", h.includes("print"), h);
h = renderMD("<img src=x onerror=alert(1)> <script>alert(2)</script>");
t("XSS转义", !h.includes("<img") && !h.includes("<script>alert"), h);
h = renderMD("## 研判结论\n\n| 结论 | 置信度 |\n|---|---|\n| 误报 | 高 |\n\n1. `unset HISTFILE` 触发\n2. 源IP内网");
t("研判样例整体", h.includes("<table>") && h.includes("<ol>") && h.includes("<code>unset HISTFILE</code>"), h);
h = renderMD("---\n\nCRLF兼容: a\r\n\r\nb");
t("CRLF容错", h.includes("<p>CRLF兼容: a</p>") && h.includes("<p>b</p>"), h);
/* 批量分诊排版: 表格边框样式由CSS提供, 渲染器验证结构 + 多行合并 + 长单元换行 */
h = renderMD("| # | 时间 | 告警ID | 主机 | 定性 | 备注 |\n|---|---|---|---|---|---|\n| 1 | 17:55 | 6af1989c…73 | xb01-gpu-0054 | 误报·高 | history抑制族 |\n| 3-6 | 17:18×4 | 6af18fe6…69b7 | vm-agent-1/3 | 误报·高 | touch族(未逐条核) |");
t("批量分诊6列表格", (h.match(/<th[\s>]/g)||[]).length === 6 && (h.match(/<td[\s>]/g)||[]).length === 12, h);
t("合并行单元格保留", h.includes("3-6") && h.includes("touch族"), h);
h = renderMD("| a | b |\n|:--:|--:|\n| 1 | 2 |");
t("对齐标记解析", h.includes('text-align:center') && h.includes('text-align:right'), h);

console.log("\n渲染测试: " + pass + "通过 / " + fail + "失败");
process.exit(fail ? 1 : 0);
