"""iSmart 页面流程：录制 → 回放，以及「保持活跃」计时。

为什么这么设计
--------------
iSmart 的提交要带 native 生成的 ``ut`` 签名，脱离客户端没法自己造。但**页面自己发请求时
签名是免费的**：所以只要让页面按顺序点下去，签名由页面在发送瞬间生成，我们完全不碰 ``ut``。

于是这里提供两件事：

1. **录制**：注入监听器，把人手动点的那几下（选择器 + 文案 + 时间间隔）录成步骤；
2. **回放**：把步骤按任务逐条重放（``{scoid}`` 之类的占位符按当前任务替换）。

刷时长同理：很多平台靠「任务页是否活跃」来累计时长，因此保持页面可见 + 周期性地
制造一点点鼠标/滚动活动即可，具体是否计入由平台决定（本工具只负责维持活跃状态）。
"""
import time
from typing import Any, Dict, List, Optional

from core.logger import get_logger

logger = get_logger("ISmartFlow")

#: 录制器引导脚本（既是「开始录制」用的内联脚本，也用于导航后自动重新注入）
#:
#: 关键点：事件同时写进 ``localStorage`` 与内存数组。真实平台点一下就会跳转/刷新，
#: ``window`` 上的内存状态会丢，而 ``localStorage``（同源）能留下，因此录制不会被打断。
RECORDER_BOOTSTRAP_JS = r"""
(() => {
  const KEY = '__ELH_REC_EVENTS__', START = '__ELH_REC_STARTED__';
  if (window.__ELH_REC_READY) return 'ready';
  window.__ELH_REC_READY = true;

  let started = Date.now();
  try {
    const saved = Number(localStorage.getItem(START) || 0);
    if (saved > 0) started = saved; else localStorage.setItem(START, String(started));
  } catch (e) {}

  const cssEscape = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/[^\w-]/g, '\\$&');
  const selectorOf = (el) => {
    if (!el || el.nodeType !== 1) return '';
    const ds = el.dataset || {};
    if (ds.scoid) return '[data-scoid="' + ds.scoid + '"]';
    if (ds.taskid) return '[data-taskid="' + ds.taskid + '"]';
    if (el.id) return '#' + cssEscape(el.id);
    if (ds.courseid) return '[data-courseid="' + ds.courseid + '"]';
    const parts = [];
    let node = el, depth = 0;
    while (node && node.nodeType === 1 && depth < 5) {
      let part = node.tagName.toLowerCase();
      const cls = (typeof node.className === 'string' ? node.className : '').trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) part += cls.map(c => '.' + cssEscape(c)).join('');
      const parent = node.parentElement;
      if (parent) {
        const sameTag = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (sameTag.length > 1) part += ':nth-of-type(' + (sameTag.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      node = parent; depth += 1;
    }
    return parts.join(' > ');
  };

  const store = (item) => {
    window.__ELH_REC_MEM = window.__ELH_REC_MEM || [];
    window.__ELH_REC_MEM.push(item);
    try {
      const arr = JSON.parse(localStorage.getItem(KEY) || '[]');
      arr.push(item);
      localStorage.setItem(KEY, JSON.stringify(arr.slice(-500)));
    } catch (e) {}
  };

  document.addEventListener('click', (ev) => {
    const el = ev.target;
    if (!el || el.nodeType !== 1) return;
    const link = el.closest ? el.closest('a') : null;
    store({
      t: Date.now() - started,
      selector: selectorOf(el),
      text: (el.innerText || '').trim().slice(0, 60),
      href: link ? link.getAttribute('href') : null,
      url: location.href
    });
  }, true);

  return 'ready';
})()
"""

#: 兼容旧名字（开始录制就是执行引导脚本）
RECORDER_START_JS = RECORDER_BOOTSTRAP_JS

#: 取出录制结果并清理状态（合并 localStorage 与内存数组，按时间+选择器去重）
RECORDER_STOP_JS = r"""
(() => {
  const KEY = '__ELH_REC_EVENTS__', START = '__ELH_REC_STARTED__';
  let stored = [];
  try {
    stored = JSON.parse(localStorage.getItem(KEY) || '[]');
    localStorage.removeItem(KEY);
    localStorage.removeItem(START);
  } catch (e) {}
  const memory = window.__ELH_REC_MEM || [];
  window.__ELH_REC_MEM = [];
  window.__ELH_REC_READY = false;
  const seen = {}, all = [];
  stored.concat(memory).forEach((item) => {
    const key = item.t + '|' + item.selector;
    if (seen[key]) return;
    seen[key] = true;
    all.push(item);
  });
  all.sort((a, b) => (a.t || 0) - (b.t || 0));
  return all;
})()
"""

#: 保持页面活跃（每 15 秒一次轻微鼠标/滚动活动，模拟有人在看）
KEEPALIVE_START_JS = r"""
(() => {
  if (window.__ELH_KA) clearInterval(window.__ELH_KA);
  window.__ELH_KA = setInterval(() => {
    try {
      const x = 80 + Math.floor(Math.random() * 200);
      const y = 120 + Math.floor(Math.random() * 200);
      const opts = { clientX: x, clientY: y, bubbles: true, cancelable: true, view: window };
      document.dispatchEvent(new MouseEvent('mousemove', opts));
      document.dispatchEvent(new MouseEvent('mousedown', opts));
      document.dispatchEvent(new MouseEvent('mouseup', opts));
      const el = document.scrollingElement || document.body;
      if (el) { el.scrollTop = el.scrollTop + 1; el.scrollTop = el.scrollTop - 1; }
      if (document.hasFocus && !document.hasFocus() && window.focus) window.focus();
    } catch (e) {}
  }, 15000);
  return 'keepalive';
})()
"""

KEEPALIVE_STOP_JS = r"""
(() => {
  if (window.__ELH_KA) { clearInterval(window.__ELH_KA); delete window.__ELH_KA; }
  return 'stopped';
})()
"""

#: 点击一个 CSS 选择器；找不到返回 false（回放时据此报错）
CLICK_SELECTOR_JS = r"""
(() => {
  const sel = %(selector)s;
  let el = null;
  try { el = document.querySelector(sel); } catch (e) { return false; }
  if (!el) return false;
  el.scrollIntoView && el.scrollIntoView({ block: 'center' });
  el.click();
  return true;
})()
"""

#: 按可见文本点击（录制的选择器太长时的兜底）
CLICK_TEXT_EXACT_JS = r"""
(() => {
  const want = %(text)s;
  const nodes = Array.from(document.querySelectorAll('button,a,div,span,li,td'));
  let hit = nodes.find(el => (el.innerText || '').trim() === want);
  if (!hit) hit = nodes.find(el => (el.innerText || '').trim().includes(want) && (el.innerText || '').trim().length < 30);
  if (!hit) return false;
  hit.scrollIntoView && hit.scrollIntoView({ block: 'center' });
  hit.click();
  return true;
})()
"""

#: 判断某个文本是否出现在页面上
HAS_TEXT_JS = r"""
(() => {
  const want = %(text)s;
  return (document.body ? document.body.innerText : '').indexOf(want) >= 0;
})()
"""


class FlowError(Exception):
    """录制/回放过程中的可预期错误。"""


def build_steps(events: List[Dict[str, Any]], max_wait_ms: int = 4000) -> List[Dict[str, Any]]:
    """把录制到的点击事件转成可回放的步骤序列。

    相邻两次点击之间的真实间隔会转成一次 ``wait``（上限 ``max_wait_ms``），
    两次点击完全相同的事件会被合并，避免重复弹窗。
    """
    steps: List[Dict[str, Any]] = []
    last_time = 0
    last_key: Optional[str] = None
    for event in events or []:
        selector = str(event.get("selector") or "").strip()
        text = str(event.get("text") or "").strip()
        if not selector and not text:
            continue
        key = selector or text
        if key == last_key:
            continue

        gap = int(event.get("t") or 0) - last_time
        if 200 <= gap <= max_wait_ms:
            steps.append({"kind": "wait", "ms": gap})
        last_time = int(event.get("t") or 0)

        # 选择器里带任务 id 的，替换成占位符，回放时按当前任务替换
        if "data-scoid=" in selector:
            selector = '[data-scoid="{scoid}"]'
        if selector:
            steps.append({"kind": "click", "selector": selector, "text": text})
        else:
            steps.append({"kind": "click_text", "text": text})
        last_key = key
    return steps


class FlowRunner:
    """在一个 CDPSession 上执行步骤序列。"""

    def __init__(self, session):
        self.session = session

    def _eval(self, expression: str) -> Any:
        return self.session.evaluate(expression)

    def run(self, steps: List[Dict[str, Any]], variables: Dict[str, Any] = None) -> List[str]:
        variables = variables or {}
        logs: List[str] = []
        for step in steps or []:
            kind = step.get("kind", "")
            if kind == "wait":
                time.sleep(max(0, int(step.get("ms", 500))) / 1000.0)
                logs.append(f"等待 {step.get('ms')}ms")
            elif kind == "click":
                selector = _fill(step.get("selector", ""), variables)
                ok = self._eval(CLICK_SELECTOR_JS % {"selector": _js(selector)})
                if not ok and step.get("text"):
                    ok = self._eval(CLICK_TEXT_EXACT_JS % {"text": _js(_fill(step["text"], variables))})
                if not ok:
                    raise FlowError(f"没找到可点击元素：{selector or step.get('text')}")
                logs.append(f"点击 {selector or step.get('text')}")
            elif kind == "click_text":
                text = _fill(step.get("text", ""), variables)
                if not self._eval(CLICK_TEXT_EXACT_JS % {"text": _js(text)}):
                    raise FlowError(f"没找到文本为「{text}」的元素")
                logs.append(f"点击「{text}」")
            elif kind == "eval":
                self._eval(str(step.get("js", "1")))
                logs.append("执行自定义脚本")
            elif kind == "expect_text":
                text = _fill(step.get("text", ""), variables)
                if not self._eval(HAS_TEXT_JS % {"text": _js(text)}):
                    raise FlowError(f"页面上没有出现「{text}」")
                logs.append(f"确认出现「{text}」")
            else:
                raise FlowError(f"未知步骤类型: {kind}")
        return logs

    # ---------------------------------------------------------------- 便捷动作

    def click_selector(self, selector: str) -> bool:
        return bool(self._eval(CLICK_SELECTOR_JS % {"selector": _js(selector)}))

    def has_text(self, text: str) -> bool:
        return bool(self._eval(HAS_TEXT_JS % {"text": _js(text)}))

    def keepalive_start(self) -> None:
        self._eval(KEEPALIVE_START_JS)

    def keepalive_stop(self) -> None:
        self._eval(KEEPALIVE_STOP_JS)


def _js(value: str) -> str:
    """安全地把 Python 字符串塞进 JS 字面量。"""
    import json

    return json.dumps(str(value), ensure_ascii=False)


def _fill(template: str, variables: Dict[str, Any]) -> str:
    out = str(template)
    for key, value in (variables or {}).items():
        out = out.replace("{" + str(key) + "}", str(value))
    return out


__all__ = [
    "FlowError",
    "FlowRunner",
    "build_steps",
    "RECORDER_START_JS",
    "RECORDER_STOP_JS",
    "KEEPALIVE_START_JS",
    "KEEPALIVE_STOP_JS",
]