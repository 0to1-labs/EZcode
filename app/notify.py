# -*- coding: utf-8 -*-
"""任务完成桌面提示（右下角浮窗）。

设计取舍
--------
* 独立无边框置顶窗口，`focus=False`：绝不抢走你正在打字的焦点；
* 单窗口复用 + 队列：多条完成提示堆叠，全部消失后 hide（不反复创建 WebView2，
  避免每弹一次就多一个渲染进程）；
* 位置按系统工作区右下角算（排除任务栏），并做 DPI 换算，避免 200% 缩放下跑到屏幕外；
* 视觉与动画全在前端：淡入上滑、悬停暂停、底部倒计时进度条、点击回到对应任务；
* 任何异常一律吞掉：提示是锦上添花，绝不能因为它影响主流程。
"""
import ctypes
import json
import os
import threading
import time
from ctypes import wintypes

W = 396          # 窗口宽
CARD_H = 104     # 单条卡片高
GAP = 8
PAD = 10
MAX_ITEMS = 3
LIFE_MS = 6000   # 自动消失时间(毫秒)，悬停时暂停

HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<style>
html,body{margin:0;height:100%;background:transparent;overflow:hidden;
  font-family:"Segoe UI","Microsoft YaHei UI","Microsoft YaHei",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;user-select:none}
#wrap{padding:10px;display:flex;flex-direction:column;gap:8px;height:100%;box-sizing:border-box}
.n{position:relative;display:flex;gap:11px;padding:12px 13px 13px;border-radius:15px;flex:none;
  background:linear-gradient(158deg,rgba(34,38,52,.985),rgba(21,24,32,.985));
  border:1px solid rgba(255,255,255,.11);color:#eef1f7;cursor:pointer;overflow:hidden;
  box-shadow:0 12px 30px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.07);
  animation:in .3s cubic-bezier(.2,.9,.3,1.15) both}
@keyframes in{from{opacity:0;transform:translateY(16px) scale(.965)}to{opacity:1;transform:none}}
.n.out{animation:out .24s ease forwards}
@keyframes out{to{opacity:0;transform:translateY(10px) scale(.98)}}
.ic{width:36px;height:36px;flex:none;border-radius:12px;display:flex;align-items:center;justify-content:center;
  font-size:18px;background:linear-gradient(135deg,#5b8cff,#38bdf8);
  box-shadow:0 5px 14px rgba(91,140,255,.38)}
.m{flex:1;min-width:0;padding-right:2px}
.t{display:flex;align-items:baseline;gap:7px;font-size:12.8px;font-weight:700;color:#fff}
.t .time{margin-left:auto;font-weight:400;font-size:10.5px;color:#8b93a7;letter-spacing:.2px}
.b{margin-top:4px;font-size:12px;line-height:1.5;color:#c6ccdb;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;word-break:break-word}
.meta{margin-top:5px;font-size:10.5px;color:#7d8598;display:flex;gap:8px;align-items:center}
.meta .dot{width:3px;height:3px;border-radius:50%;background:#5a6275}
.bar{position:absolute;left:0;bottom:0;height:2px;width:100%;transform-origin:left;
  background:linear-gradient(90deg,#5b8cff,#38bdf8);animation:bar linear forwards}
@keyframes bar{from{transform:scaleX(1)}to{transform:scaleX(0)}}
.n:hover .bar{animation-play-state:paused}
.x{position:absolute;top:7px;right:7px;width:19px;height:19px;border-radius:50%;color:#8b93a7;
  font-size:11px;line-height:19px;text-align:center;opacity:0;transition:opacity .15s,background .15s}
.n:hover .x{opacity:1}
.x:hover{background:rgba(255,255,255,.12);color:#fff}
.e{position:absolute;inset:0;display:none;align-items:center;justify-content:center;font-size:11.5px;
  color:#d8dced;background:rgba(12,14,20,.72)}
.n.done .e{display:flex}
</style></head><body><div id="wrap"></div>
<script>
let live = [];
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function add(n){
  const el = document.createElement("div");
  el.className = "n";
  el.innerHTML = '<div class="ic"></div>'
    + '<div class="m"><div class="t"><span class="tt"></span><span class="time"></span></div>'
    + '<div class="b"></div><div class="meta"></div></div>'
    + '<div class="bar"></div><div class="x">✕</div><div class="e"></div>';
  el.querySelector(".e").textContent = (n.lang === "en" ? "Opening…" : "正在打开…");
  el.querySelector(".ic").textContent = n.icon || "✅";
  el.querySelector(".tt").textContent = n.title || "任务已完成";
  el.querySelector(".time").textContent = n.time || "";
  el.querySelector(".b").textContent = n.body || "";
  const en = n.lang === "en";
  const meta = [];
  if(n.dt) meta.push((en ? "took " : "用时 ") + n.dt + "s");
  if(n.tools) meta.push(en ? (n.tools + " tool calls") : (n.tools + " 次工具调用"));
  el.querySelector(".meta").innerHTML = meta.map((x,i)=>
    (i ? '<span class="dot"></span>' : '') + '<span>' + esc(x) + '</span>').join("");
  const bar = el.querySelector(".bar");
  bar.style.animationDuration = (n.life || 6000) + "ms";
  const close = ()=>{ if(el.dataset.gone) return; el.dataset.gone = "1";
    el.classList.add("out"); setTimeout(()=>{ el.remove();
      live = live.filter(x=>x!==el);
      try{ pywebview.api.gone(); }catch(_){}
      if(!live.length) notifyEmpty(); }, 240); };
  el.querySelector(".x").onclick = e=>{ e.stopPropagation(); close(); };
  el.onclick = ()=>{
    if(el.dataset.gone) return;
    el.classList.add("done");
    try{ pywebview.api.open(n.sid); }catch(_){}
    setTimeout(close, 260);
  };
  el._timer = setTimeout(close, n.life || 6000);
  el.onmouseenter = ()=>{ clearTimeout(el._timer); };
  el.onmouseleave = ()=>{ el._timer = setTimeout(close, 2200); };
  const wrap = document.getElementById("wrap");
  wrap.appendChild(el);
  live.push(el);
  while(live.length > n.max){ const old = live[0]; if(old){ old.dataset.gone="1"; old.remove(); live.shift(); } }
}
function notifyEmpty(){ try{ pywebview.api.hide_self(); }catch(_){} }
window.pushNote = add;
</script></body></html>"""


def _warn_notify(msg):
    try:
        print("[notify] " + str(msg)[:300], flush=True)
    except Exception:
        pass


def _scale():
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        return (dpi or 96) / 96.0
    except Exception:
        return 1.0


def _workarea():
    """返回逻辑像素(96dpi 基准)的工作区右下角，已排除任务栏。"""
    try:
        r = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            s = _scale()
            return int(r.right / s), int(r.bottom / s)
    except Exception:
        pass
    return 1920, 1080


def _clean(text, limit=150):
    """把 markdown 噪声压掉，只留能一眼看懂的一句半。"""
    s = str(text or "")
    for a, b in (("```", " "), ("**", ""), ("__", ""), ("`", ""), ("#", ""),
                 ("|", " "), ("\r", "")):
        s = s.replace(a, b)
    s = " ".join(s.split())
    return s[:limit] + ("…" if len(s) > limit else "")


class _Api:
    """通知窗自己的 JS 桥：点卡片回到对应任务、或让这个窗自己关掉。"""

    def __init__(self, notifier, rec):
        self._n = notifier
        self._rec = rec

    def open(self, sid):
        self._n.open_task(str(sid or ""), self._rec)
        return {"ok": True}

    def hide_self(self):
        self._n.close(self._rec)

    def gone(self):
        return {"ok": True}


class Notifier:
    """一条通知一个无边框透明小窗（简单可靠：不用隐藏/复用，避免 WebView2 的
    透明 hack 与 hidden 冲突）。窗口创建时先摆到屏幕外，页面加载完再挪到右下角。"""

    def __init__(self, app):
        self.app = app
        self._wins = []
        self._lock = threading.Lock()
        self._main_hwnd = 0

    # ---------- 窗口 ----------
    @staticmethod
    def _notify_pos():
        """默认贴右下角(留 14px); 多条时 _relayout 会往上推开。"""
        x, bottom = _workarea()
        return x - W - 14, bottom - (PAD * 2 + CARD_H) - 14

    def _relayout(self):
        """从屏幕底部往上排，最新的贴底。"""
        x, bottom = _workarea()
        h = PAD * 2 + CARD_H
        with self._lock:
            wins = list(self._wins)
        for i, rec in enumerate(reversed(wins)):
            try:
                rec["win"].resize(W, h)
                rec["win"].move(x - W - 14, bottom - 14 - h - i * (h + GAP))
            except Exception:
                pass

    def _win_alive(self, rec):
        with self._lock:
            return rec in self._wins

    def close_all(self):
        """退出应用时把残留提示窗一起收掉, 否则进程会留着。"""
        with self._lock:
            wins = list(self._wins)
            self._wins = []
        for rec in wins:
            try:
                rec["win"].destroy()
            except Exception:
                pass

    def close(self, rec):
        with self._lock:
            if rec in self._wins:
                self._wins.remove(rec)
            else:
                return
        try:
            rec["win"].destroy()
        except Exception:
            pass
        self._relayout()

    # ---------- 前台判断 ----------
    def _main_window_handle(self):
        if self._main_hwnd:
            return self._main_hwnd
        try:
            import win32gui
            h0 = win32gui.FindWindow(None, "EZcode")     # 最直接
            if h0:
                self._main_hwnd = h0
                return h0
            pid = os.getpid()
            found = []

            def cb(h, _):
                # 必须 return True: 返回 None/False 会让 EnumWindows 立刻停止
                try:
                    if not win32gui.IsWindowVisible(h):
                        return True
                    if win32gui.GetWindowText(h) != "EZcode":
                        return True
                    _, wpid = win32gui.GetWindowThreadProcessId(h)
                    if wpid == pid:
                        found.append(h)
                except Exception:
                    pass
                return True

            win32gui.EnumWindows(cb, None)
            if found:
                self._main_hwnd = found[0]
        except Exception:
            pass
        return self._main_hwnd

    def main_in_foreground(self):
        try:
            import win32gui
            h = self._main_window_handle()
            if not h:
                return True                      # 判断不了就当在前台, 不打扰
            if win32gui.IsIconic(h):
                return False                     # 最小化了, 肯定没在看
            fg = win32gui.GetForegroundWindow()
            if not fg:
                return False
            if fg == h:
                return True
            root = win32gui.GetAncestor(fg, 2)   # GA_ROOT: 点输入框时 fg 是子窗
            return root == h
        except Exception:
            return True

    # ---------- 对外 ----------
    def allowed(self, mode, sid):
        """mode: off / auto(仅不在前台或不在该任务时) / always"""
        if mode == "off":
            return False
        if mode == "always":
            return True
        try:
            cur = ""
            if self.app is not None and getattr(self.app, "engine", None) is not None:
                cur = self.app.engine.sid
            if cur and sid and cur != sid:
                return True                      # 你看的是另一个任务 → 提醒
        except Exception:
            pass
        return not self.main_in_foreground()

    def _lang(self):
        try:
            if self.app is not None and getattr(self.app, "engine", None) is not None:
                return "en" if getattr(self.app.engine, "lang", "zh") == "en" else "zh"
        except Exception:
            pass
        return "zh"

    def notify(self, sid="", title="", body="", icon="✅", dt=0, tools=0, life=None):
        try:
            import webview
            lang = self._lang()
            payload = {"sid": sid, "lang": lang,
                       "title": title or ("Task finished" if lang == "en" else "任务已完成"),
                       "body": _clean(body),
                       "icon": icon or "✅", "time": time.strftime("%H:%M"),
                       "dt": round(float(dt or 0), 1), "tools": int(tools or 0),
                       "life": int(life or LIFE_MS), "max": 1}
            ox, oy = self._notify_pos()
            rec = {"sid": sid, "win": None}
            win = webview.create_window(
                "EZcode 提示", html=HTML, width=W, height=PAD * 2 + CARD_H,
                x=ox, y=oy, frameless=True, on_top=True, transparent=True,
                shadow=False, resizable=False, focus=False, easy_drag=False,
                text_select=False, js_api=_Api(self, rec))
            rec["win"] = win
            with self._lock:
                self._wins.append(rec)
                if len(self._wins) > MAX_ITEMS:          # 超出上限关掉最旧的
                    old = self._wins[0]
                    threading.Thread(target=self.close, args=(old,), daemon=True).start()

            def loaded():
                if not self._win_alive(rec):
                    return
                try:
                    win.evaluate_js("window.pushNote(%s)"
                                    % json.dumps(payload, ensure_ascii=False))
                except Exception as e:
                    _warn_notify("推送失败: %s: %s" % (type(e).__name__, e))
                # 透明窗口在 pywebview 里是「先 Show 再 Hide、等首次导航完成才真正显示」,
                # 所以必须显式 show 一次, 否则窗口可见但内容不绘制。
                try:
                    win.show()
                except Exception:
                    pass
                self._relayout()

            win.events.loaded += loaded
            win.events.closed += lambda: self._forget(rec)
        except Exception as e:
            _warn_notify("弹出失败: %s: %s" % (type(e).__name__, e))

    def _forget(self, rec):
        with self._lock:
            if rec in self._wins:
                self._wins.remove(rec)

    def open_task(self, sid, rec=None):
        try:
            app = self.app
            win = getattr(app, "window", None)
            if win is not None:
                try:
                    win.show()
                    win.restore()
                except Exception:
                    pass
                try:
                    win.evaluate_js(
                        "window.openSessionFromNotify && openSessionFromNotify(%s)"
                        % json.dumps(sid))
                except Exception:
                    pass
            try:
                import win32gui
                h = self._main_window_handle()
                if h:
                    win32gui.ShowWindow(h, 9)          # SW_RESTORE
                    win32gui.SetForegroundWindow(h)
            except Exception:
                pass
            if rec is not None:
                self.close(rec)
        except Exception:
            pass
