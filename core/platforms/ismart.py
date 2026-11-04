"""iSmart（外语智能学习平台）平台适配器 —— CDP 驱动本地客户端。

为什么不是纯 HTTP 实现
----------------------
iSmart 客户端判题在本地完成，但上报分数时要带 native 生成的 ``ut`` 签名，
纯 JS/纯 Python 拿不到。社区现有两条路：

1. 驱动官方客户端页面（本项目采用）：客户端是套壳 Chromium，加
   ``--remote-debugging-port=9222`` 后用 Chrome DevTools Protocol 操控页面，
   签名由客户端自己算；
2. 自己复刻签名（unidbg/Frida 还原 so），再直连接口。这条需要逆向 native，
   本项目只留出扩展点，不做实现。

因此本适配器**不发明任何服务端接口**，只做三件事：
发现调试端口 → 附着到目标页面 → 按可配置的脚本/步骤操作页面。

选择器需要现场校准
------------------
不同版本的 iSmart 客户端 DOM 结构不同，代码里的默认 JS 是启发式扫描，
首次使用请先点「抓取页面结构」（``dump_page``），生成 HTML + 截图，
再按实际结构修改 ``ismart_profile.json`` 里的 JS 与步骤。
配置存放于数据目录（默认 ``<数据目录>/ismart_profile.json``）。
"""
import base64
import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

from core.constants import ErrCode
from core.logger import get_logger
from core.paths import data_path
from core.platforms.base import Capability, PlatformClient, PlatformInfo

logger = get_logger("ISmartCDP")

PLATFORM = PlatformInfo(
    key="ismart",
    name="iSmart（外语智能学习平台）",
    description="通过 Chrome DevTools Protocol 驱动本地 iSmart 客户端完成课程任务",
    capabilities=frozenset({Capability.HOMEWORK, Capability.CDP}),
)

DEFAULT_PORT = 9222
DEFAULT_HOST_KEYWORD = "ismart"
PROFILE_FILE = "ismart_profile.json"
DUMP_DIR = "dumps"
COMMAND_TIMEOUT = 15


class ISmartError(Exception):
    """iSmart 适配器的可预期错误（端口没开、页面不匹配、脚本执行失败等）。"""


# ---------------------------------------------------------------- 默认页面脚本

#: 课程列表：先按 data-* 属性找卡片，再退回链接扫描
COURSES_JS = r"""
(() => {
  const out = [], seen = new Set();
  const add = (id, text) => {
    if (!id || !text) return;
    const key = String(id);
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ cid: key, name: text.trim().slice(0, 120), per: "" });
  };
  document.querySelectorAll('[data-courseid],[data-id],a[href*="course"],div.course-item,li.course-item')
    .forEach(el => {
      const ds = el.dataset || {};
      const href = (el.getAttribute && el.getAttribute('href')) || '';
      const m = href.match(/courseI[dD]=([^&]+)/);
      const id = ds.courseid || ds.id || (m && m[1]);
      const text = (el.innerText || '').split('\n')[0];
      add(id, text);
    });
  return out.slice(0, 200);
})()
"""

#: 单元列表：iSmart 的 uid/classid 在 CDP 模式下用不到，取不到就给占位值
UNITS_JS = r"""
(() => {
  const body = document.body.dataset || {};
  const glob = window.__USER__ || window.__user__ || {};
  const uid = glob.id || glob.userId || body.userid || 'cdp';
  const classid = body.classid || 'cdp';
  const units = [...document.querySelectorAll('[data-unitid],li.unit-item,div.unit-item')]
    .map((el, i) => ({
      id: (el.dataset && el.dataset.unitid) || String(i),
      name: ((el.innerText || '').split('\n')[0] || ('Unit ' + (i + 1))).trim().slice(0, 120),
      visible: 'true'
    }));
  return { uid: String(uid), classid: String(classid), units };
})()
"""

#: 任务（SCO）列表
TASKS_JS = r"""
(() => {
  const rows = [...document.querySelectorAll('[data-scoid],[data-taskid],li.task-item,div.task-item')];
  return rows.map((el, i) => {
    const ds = el.dataset || {};
    const text = (el.innerText || '').trim();
    const finished = ds.completed === 'true' || /已完成/.test(text);
    return {
      id: String(ds.scoid || ds.taskid || i),
      location: (text.split('\n')[0] || ('任务 ' + (i + 1))).trim().slice(0, 120),
      isvisible: 'true',
      iscomplete: finished ? '已完成' : '未完成'
    };
  });
})()
"""

#: 提交成功判定（启发式，可在配置里替换）
EXPECT_OK_JS = r"""
(() => {
  const text = document.body.innerText || '';
  return /已完成|提交成功|保存成功/.test(text);
})()
"""

#: 默认提交流程：点开任务 → 等渲染 → 点「提交」→ 等回包 → 判定
DEFAULT_SUBMIT_STEPS: List[Dict[str, Any]] = [
    {"kind": "click_task", "scoid": "{scoid}"},
    {"kind": "wait_ms", "ms": 1200},
    {"kind": "click_text", "text": "提交"},
    {"kind": "wait_ms", "ms": 1500},
    {"kind": "expect_ok"},
]

CLICK_TASK_JS = r"""
(() => {
  const target = %(scoid)s;
  const nodes = [...document.querySelectorAll('[data-scoid],[data-taskid],li,div')];
  for (const el of nodes) {
    const ds = el.dataset || {};
    if (String(ds.scoid || ds.taskid || '') === target) { el.click(); return true; }
  }
  return false;
})()
"""

CLICK_TEXT_JS = r"""
(() => {
  const want = %(text)s;
  const nodes = [...document.querySelectorAll('button,a,div,span,li')];
  const hit = nodes.find(el => (el.innerText || '').trim() === want);
  if (hit) { hit.click(); return true; }
  const soft = nodes.find(el => (el.innerText || '').trim().includes(want) && (el.innerText || '').length < 20);
  if (soft) { soft.click(); return true; }
  return false;
})()
"""


# ---------------------------------------------------------------- 配置

@dataclass
class ISmartProfile:
    """iSmart 适配器配置，可被 ``ismart_profile.json`` 覆盖。"""

    port: int = DEFAULT_PORT
    host_keyword: str = DEFAULT_HOST_KEYWORD
    courses_js: str = COURSES_JS
    units_js: str = UNITS_JS
    tasks_js: str = TASKS_JS
    expect_ok_js: str = EXPECT_OK_JS
    submit_steps: List[Dict[str, Any]] = field(default_factory=lambda: list(DEFAULT_SUBMIT_STEPS))

    @staticmethod
    def path() -> str:
        return data_path(PROFILE_FILE)

    @classmethod
    def load(cls, path: str = None) -> "ISmartProfile":
        target = path or cls.path()
        profile = cls()
        if not os.path.exists(target):
            try:
                profile.save(target)
                logger.info("已生成 iSmart 配置文件: %s", target)
            except OSError as exc:
                logger.warning("生成 iSmart 配置文件失败: %s", exc)
            return profile
        try:
            with open(target, "r", encoding="utf-8") as stream:
                data = json.load(stream)
            for key, value in data.items():
                if hasattr(profile, key):
                    setattr(profile, key, value)
        except Exception as exc:
            logger.warning("读取 iSmart 配置失败，使用默认值: %s", exc)
        return profile

    def save(self, path: str = None) -> None:
        target = path or self.path()
        from core.atomic import write_text_atomic

        write_text_atomic(target, json.dumps(asdict(self), ensure_ascii=False, indent=2), ".tmp_ismart_")


# ---------------------------------------------------------------- CDP 底层

def discover_pages(port: int = DEFAULT_PORT, timeout: float = 3.0) -> List[Dict[str, Any]]:
    """列出调试端口上可附着的页面。"""
    try:
        response = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=timeout)
        response.raise_for_status()
        pages = response.json()
    except Exception as exc:
        raise ISmartError(
            f"无法连接调试端口 {port}：{exc}。请给 iSmart 客户端启动参数加上 "
            f"--remote-debugging-port={port} 并保持运行"
        ) from exc
    return [page for page in pages if page.get("type") == "page" and page.get("webSocketDebuggerUrl")]


class CDPSession:
    """最小可用的 CDP 会话（只用到 Runtime/Page 两个域）。"""

    def __init__(self, ws_url: str, timeout: float = COMMAND_TIMEOUT):
        try:
            import websocket  # websocket-client
        except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确提示
            raise ISmartError("缺少依赖 websocket-client，请执行 pip install -r requirements.txt") from exc

        self._next_id = 0
        try:
            self._ws = websocket.create_connection(ws_url, timeout=timeout)
        except Exception as exc:
            raise ISmartError(f"连接页面失败: {exc}") from exc
        self.timeout = timeout

    def send(self, method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        payload = {"id": request_id, "method": method, "params": params or {}}
        try:
            self._ws.send(json.dumps(payload))
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                message = json.loads(self._ws.recv())
                if message.get("id") != request_id:
                    continue  # 事件推送，忽略
                if "error" in message:
                    raise ISmartError(f"{method} 失败: {message['error'].get('message')}")
                return message.get("result", {})
        except ISmartError:
            raise
        except Exception as exc:
            raise ISmartError(f"{method} 通信失败: {exc}") from exc
        raise ISmartError(f"{method} 超时")

    def evaluate(self, expression: str, await_promise: bool = True) -> Any:
        result = self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
                "userGesture": True,
            },
        )
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            text = detail.get("exception", {}).get("description") or detail.get("text")
            raise ISmartError(f"页面脚本异常: {text}")
        return result.get("result", {}).get("value")

    def screenshot(self) -> bytes:
        result = self.send("Page.captureScreenshot", {"format": "png"})
        return base64.b64decode(result.get("data", ""))

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 平台客户端

class ISmartClient(PlatformClient):
    """iSmart 会话：以本地客户端的登录态为准，账号口令仅用于记录。"""

    info = PLATFORM

    def __init__(self, profile: ISmartProfile = None):
        self.profile = profile or ISmartProfile.load()
        self.last_error_code = ""
        self._session: Optional[CDPSession] = None
        self._page: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------ 连接

    def _attach(self) -> CDPSession:
        if self._session is not None:
            return self._session

        pages = discover_pages(self.profile.port)
        keyword = (self.profile.host_keyword or "").lower()
        candidate = None
        for page in pages:
            url = (page.get("url") or "").lower()
            if not keyword or keyword in url:
                candidate = page
                break
        if candidate is None:
            candidate = pages[0] if pages else None
        if candidate is None:
            raise ISmartError("调试端口上没有可附着的页面，请先在 iSmart 客户端里打开课程页")

        self._page = candidate
        self._session = CDPSession(candidate["webSocketDebuggerUrl"])
        self._session.send("Runtime.enable")
        logger.info("已附着 iSmart 页面: %s", candidate.get("title") or candidate.get("url"))
        return self._session

    def login(self, username: str, password: str) -> Tuple[bool, str]:
        self.last_error_code = ""
        try:
            self._attach()
        except ISmartError as exc:
            # 用独立错误码，界面才会把「请加 --remote-debugging-port=9222」这条
            # 可操作的原文显示出来，而不是笼统的「无法连接服务器」
            self.last_error_code = ErrCode.CDP
            return False, str(exc)
        return True, "已连接 iSmart 客户端（登录态以客户端为准）"

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
        self._session = None
        self._page = None

    # ------------------------------------------------------------ 脚本执行

    def _eval(self, expression: str) -> Any:
        return self._attach().evaluate(expression)

    def get_courses(self) -> Tuple[bool, List[Dict], str]:
        try:
            courses = self._eval(self.profile.courses_js) or []
            courses = [c for c in courses if isinstance(c, dict) and c.get("cid")]
        except ISmartError as exc:
            self.last_error_code = ErrCode.PARSE
            return False, [], str(exc)
        if not courses:
            self.last_error_code = ErrCode.PARSE
            return False, [], "页面里没找到课程元素，请先打开课程列表页，或点「抓取页面结构」后调整 courses_js"
        return True, courses, "获取课程成功"

    def get_course_info(self, cid) -> Tuple[bool, Optional[Dict], str]:
        try:
            data = self._eval(self.profile.units_js) or {}
        except ISmartError as exc:
            self.last_error_code = ErrCode.PARSE
            return False, None, str(exc)

        units = data.get("units") or []
        if not units:
            self.last_error_code = ErrCode.PARSE
            return False, None, "页面里没找到单元元素，请先打开该课程目录页，或调整 units_js"
        return (
            True,
            {
                "uid": str(data.get("uid") or "cdp"),
                "classid": str(data.get("classid") or "cdp"),
                "units": units,
            },
            "获取单元信息成功",
        )

    def get_sco_leaves(self, cid, uid, classid, unit_idx) -> Tuple[bool, List, str]:
        try:
            tasks = self._eval(self.profile.tasks_js) or []
        except ISmartError as exc:
            self.last_error_code = ErrCode.PARSE
            return False, [], str(exc)
        tasks = [t for t in tasks if isinstance(t, dict) and t.get("id")]
        if not tasks:
            self.last_error_code = ErrCode.PARSE
            return False, [], "页面里没找到任务元素，请打开该单元的任务列表，或调整 tasks_js"
        return True, tasks, "Success"

    # ------------------------------------------------------------ 提交流程

    def _run_steps(self, scoid) -> List[str]:
        logs: List[str] = []
        for step in self.profile.submit_steps:
            kind = step.get("kind", "")
            if kind == "click_task":
                target = str(step.get("scoid", "{scoid}")).replace("{scoid}", str(scoid))
                ok = self._eval(CLICK_TASK_JS % {"scoid": json.dumps(target)})
                if not ok:
                    raise ISmartError(f"没找到任务 {target} 对应的元素")
                logs.append(f"已点击任务 {target}")
            elif kind == "click_text":
                text = str(step.get("text", "提交"))
                ok = self._eval(CLICK_TEXT_JS % {"text": json.dumps(text)})
                if not ok:
                    raise ISmartError(f"没找到文本为「{text}」的可点击元素")
                logs.append(f"已点击「{text}」")
            elif kind == "wait_ms":
                time.sleep(max(0, int(step.get("ms", 500))) / 1000.0)
                logs.append(f"等待 {step.get('ms', 500)}ms")
            elif kind == "eval":
                self._eval(str(step.get("js", "1")))
                logs.append("已执行自定义脚本")
            elif kind == "expect_ok":
                if not self._eval(self.profile.expect_ok_js):
                    raise ISmartError("提交后未检测到成功标志，请检查 expect_ok_js")
                logs.append("已检测到成功标志")
            else:
                raise ISmartError(f"未知步骤类型: {kind}")
        return logs

    def submit_course_progress(self, cid, uid, classid, scoid, accuracy) -> Tuple[int, int, int, int]:
        try:
            for line in self._run_steps(scoid):
                logger.info(line)
        except ISmartError as exc:
            self.last_error_code = ErrCode.PARSE
            logger.error("iSmart 提交流程失败: %s", exc)
            return 0, 1, 0, 1
        return 1, 0, 0, 1  # iSmart 没有 WeLearn 的「双通道」概念，第二路恒记为未走

    def simulate_time(self, cid, uid, scoid, learning_time, stop_event=None) -> bool:
        self.last_error_code = ErrCode.UNSUPPORTED
        logger.warning("iSmart 适配器暂不支持挂时长（需要 ut 签名直连接口）")
        return False

    # ------------------------------------------------------------ 调试辅助

    def dump_page(self, name: str = None) -> str:
        """保存当前页面 HTML 与截图，便于校准选择器。"""
        session = self._attach()
        html = session.evaluate("document.documentElement.outerHTML") or ""
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target_dir = os.path.join(os.path.dirname(ISmartProfile.path()), DUMP_DIR)
        os.makedirs(target_dir, exist_ok=True)
        base = os.path.join(target_dir, f"{name or 'ismart'}_{stamp}")

        from core.atomic import write_text_atomic

        write_text_atomic(base + ".html", html)
        try:
            with open(base + ".png", "wb") as stream:
                stream.write(session.screenshot())
        except Exception as exc:
            logger.warning("截图失败（不影响 HTML）: %s", exc)
        logger.info("页面结构已导出: %s.html", base)
        return base + ".html"


__all__ = [
    "PLATFORM",
    "ISmartProfile",
    "ISmartClient",
    "ISmartError",
    "CDPSession",
    "discover_pages",
]