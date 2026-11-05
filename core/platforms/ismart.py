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
import glob
import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

from core.constants import ErrCode
from core.logger import get_logger
from core.paths import data_path
from core.platforms.base import Capability, PlatformClient, PlatformInfo
from core.platforms.ismart_flow import (
    INTROSPECT_JS,
    LOGIN_CHECK_JS,
    RECORDER_BOOTSTRAP_JS,
    RECORDER_START_JS,
    RECORDER_STOP_JS,
    FlowError,
    FlowRunner,
    build_steps,
)

logger = get_logger("ISmartCDP")

PLATFORM = PlatformInfo(
    key="ismart",
    name="iSmart（外语智能学习平台）",
    description="通过 Chrome DevTools Protocol 驱动真实页面完成任务（支持录制流程回放）",
    capabilities=frozenset({Capability.HOMEWORK, Capability.DURATION, Capability.CDP}),
)

DEFAULT_PORT = 9222
DEFAULT_HOST_KEYWORD = "ismart"
PROFILE_FILE = "ismart_profile.json"
DUMP_DIR = "dumps"
COMMAND_TIMEOUT = 15

#: 业务接口前缀（自检抓到的真实域名：课程列表、用户、统计都在这台）
API_BASE = "https://school.ismartlearning.cn"
#: 课程列表接口（默认取在读课程 status=1）
COURSE_LIST_PATH = "/client/course/list-of-student"


class ISmartError(Exception):
    """iSmart 适配器的可预期错误（端口没开、页面不匹配、脚本执行失败等）。"""


# ---------------------------------------------------------------- 默认页面脚本

#: 课程列表：先按 data-* 属性找卡片，再退回链接扫描
COURSES_JS = r"""
(() => {
  // iSmart 学生中心（me.ismartlearning.cn/center/student/index.html）的课程卡片没有
  // data-courseid，只有一个「进入课程」按钮 + 标题 + 主讲。因此按「按钮 → 向上找卡片 → 取标题」
  // 的方式识别，cid 用 card:<序号>（同一页面内稳定，点击时按同样顺序再扫一遍）。
  const LABELS = ['进入课程', '继续学习', '开始学习', '进入学习'];
  const isLabel = (t) => LABELS.some((l) => t === l || t.indexOf(l) === 0);
  const visible = (el) => !!(el.getBoundingClientRect && el.getBoundingClientRect().width > 0);
  // 有些页面用 <input type=button value=...> 或 title/aria-label，innerText 是空的
  const labelOf = (el) => ((el.innerText || el.value || el.getAttribute('title') ||
    el.getAttribute('aria-label') || '') + '').trim();

  const candidates = Array.from(document.querySelectorAll('a,button,div,span,input,[role=button]'))
    .filter((el) => { const t = labelOf(el); return t && isLabel(t) && visible(el); });
  const primary = candidates.filter((el) => !candidates.some((other) => other !== el && el.contains(other)));

  const out = [];
  primary.forEach((btn, index) => {
    const label = labelOf(btn);
    if (LABELS.indexOf(label) !== 0) return;   // 只认「进入课程」开头的那个
    let card = btn.parentElement, depth = 0, title = '';
    while (card && depth < 5) {
      const txt = (card.innerText || '').trim();
      if (txt.length > label.length + 2 && txt.length < 400) { title = txt; break; }
      card = card.parentElement; depth += 1;
    }
    const lines = title.split('\n').map((s) => s.trim()).filter(Boolean)
      .filter((s) => !isLabel(s) && !/^[¥￥]?\d+(\.\d+)?\s*元?$/.test(s) && !/查看详情/.test(s));
    const name = (lines[0] || ('课程 ' + (index + 1))).slice(0, 60);
    const teacher = (lines.find((s) => /老师|教师|主讲/.test(s)) || '').replace(/^主讲[:：]?\s*/, '').slice(0, 20);
    // per 表示「学习进度」，这里没有；主讲并进名称里更直观
    out.push({ cid: 'card:' + index, name: teacher ? (name + '（' + teacher + '）') : name, per: '' });
  });
  return out.slice(0, 100);
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

#: 任务候选的文案关键词：TASKS_JS（识别）与 CLICK_TASK_JS（点击）必须共用同一份，
#: 否则两边过滤出的元素序号会错位 → 出现「识别到 3 个，第 3 个点不到」（实测踩过）。
TASK_LABELS = [
    "测验", "活动", "问答", "作业", "练习", "单元", "Unit", "Chapter", "第",
    "视频", "课件", "听力", "阅读", "写作", "翻译", "任务", "开始学习", "继续学习",
]

#: 任务（SCO）列表：优先 data-*；学生中心/课程页没有 data-* 时按可见文案识别
TASKS_JS = r"""
(() => {
  const out = [], seen = new Set();
  const pushTask = (id, label, finished) => {
    const key = String(id);
    if (!id || seen.has(key)) return;
    seen.add(key);
    out.push({
      id: key,
      location: (label || ('任务 ' + (out.length + 1))).trim().slice(0, 120),
      isvisible: 'true',
      iscomplete: finished ? '已完成' : '未完成'
    });
  };

  [...document.querySelectorAll('[data-scoid],[data-taskid],li.task-item,div.task-item')]
    .forEach((el, i) => {
      const ds = el.dataset || {};
      const text = (el.innerText || '').trim();
      pushTask(String(ds.scoid || ds.taskid || 'task:' + i), text.split('\n')[0],
               ds.completed === 'true' || /已完成/.test(text));
    });
  if (out.length) return out;

  // 没有 data-* 的页面：按可见条目识别（含 input 按钮、视频/课件/作业这类标签）
  // 标签表由 %(labels)s 注入，与 CLICK_TASK_JS 共用同一份（避免序号错位）
  const LABELS = %(labels)s;
  const labelOf = (el) => ((el.innerText || el.value || el.getAttribute('title') ||
    el.getAttribute('aria-label') || '') + '').trim();
  const clickable = Array.from(document.querySelectorAll('a,button,div,span,li,input,[role=button]'))
    .filter((el) => {
      const t = labelOf(el);
      return t && t.length < 80 && LABELS.some((l) => t.indexOf(l) >= 0) &&
             el.getBoundingClientRect().width > 0;
    });
  const primary = clickable.filter((el) => !clickable.some((other) => other !== el && el.contains(other)));

  // 给每个候选算一个 CSS 选择器，方便回放时精确定位
  const cssEscape = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/[^\w-]/g, '\\$&');
  const selectorOf = (el) => {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + cssEscape(el.id);
    for (const attr of ['data-scoid', 'data-taskid', 'data-id']) {
      const v = el.getAttribute && el.getAttribute(attr);
      if (v) return '[' + attr + '="' + v + '"]';
    }
    const parts = [];
    let node = el, depth = 0;
    while (node && node.nodeType === 1 && depth < 4) {
      let part = node.tagName.toLowerCase();
      const cls = (typeof node.className === 'string' ? node.className : '').trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) part += cls.map((c) => '.' + cssEscape(c)).join('');
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      node = parent; depth += 1;
    }
    return parts.join(' > ');
  };

  primary.forEach((el, i) => {
    const text = labelOf(el);
    const key = 'task:' + i;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({
      id: key,
      location: text.split('\n')[0].slice(0, 120),
      selector: selectorOf(el),
      isvisible: 'true',
      iscomplete: /已完成|已完成学习|100%/.test(text) ? '已完成' : '未完成'
    });
  });
  return out.slice(0, 200);
})()
"""

#: 提交成功判定（启发式，可在配置里替换）
EXPECT_OK_JS = r"""
(() => {
  const text = document.body.innerText || '';
  return /已完成|提交成功|保存成功/.test(text);
})()
"""

#: 提交按钮的候选文案：不同页面叫法不同，按顺序试（录制流程可覆盖）
SUBMIT_LABELS: Tuple[str, ...] = ("提交", "保存", "完成", "交卷", "确定", "下一题", "继续")

_TASK_LABELS_JSON = json.dumps(TASK_LABELS, ensure_ascii=False)


def fill_task_labels(script: str) -> str:
    """把 ``%(labels)s`` 换成任务标签表。

    用字符串替换而不是 ``%`` 格式化：TASKS_JS 里有 ``/100%/`` 这种字面量百分号，
    走 ``%`` 格式化会直接抛 ValueError。
    """
    return script.replace("%(labels)s", _TASK_LABELS_JSON)


def click_task_script(target: str) -> str:
    """生成点击某个任务的脚本（card:序号 / task:序号 / data-* id 都支持）。"""
    return fill_task_labels(CLICK_TASK_JS).replace("%(scoid)s", json.dumps(str(target)))


#: 默认提交流程：点开任务 → 等渲染 → 依次试提交文案 → 等回包 → 判定
DEFAULT_SUBMIT_STEPS: List[Dict[str, Any]] = [
    {"kind": "click_task", "scoid": "{scoid}"},
    {"kind": "wait_ms", "ms": 1200},
    {"kind": "click_submit"},
    {"kind": "wait_ms", "ms": 1500},
    {"kind": "expect_ok"},
]

CLICK_TASK_JS = r"""
(() => {
  const target = String(%(scoid)s);
  // 「card:序号」= 学生中心的课程卡片；「task:序号」= 课程页里的任务条目。
  // 两类都按「按钮文案 + 出现顺序」定位，因为页面没有稳定 data-id（实测学生中心如此）。
  const m = target.match(/^(card|task):(\d+)$/);
  if (m) {
    const labels = m[1] === 'card'
      ? ['进入课程', '继续学习', '开始学习', '进入学习']
      : %(labels)s;   // 与 TASKS_JS 共用同一份标签表，保证序号一致
    const hit = (t) => labels.some((l) => t === l || t.indexOf(l) === 0 || t.indexOf(l) >= 0);
    // 必须包含 input：学生中心的「进入课程」实测是 <input type=button value=...>，
    // innerText 为空，只能读 value / title / aria-label
    const labelOf = (el) => ((el.innerText || el.value || el.getAttribute('title') ||
      el.getAttribute('aria-label') || '') + '').trim();
    const nodes = Array.from(document.querySelectorAll('a,button,div,span,input,[role=button]'))
      .filter((el) => {
        const t = labelOf(el);
        return t && t.length < 60 && hit(t) && el.getBoundingClientRect().width > 0;
      });
    const primary = nodes.filter((el) => !nodes.some((other) => other !== el && el.contains(other)));
    const el = primary[parseInt(m[2], 10)];
    if (!el) return false;
    el.scrollIntoView({ block: 'center' });
    el.click();
    return true;
  }
  const nodes = [...document.querySelectorAll('[data-scoid],[data-taskid]')];
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
  const labelOf = (el) => ((el.innerText || el.value || el.getAttribute('title') ||
    el.getAttribute('aria-label') || '') + '').trim();
  const nodes = [...document.querySelectorAll('button,a,div,span,li,input,[role=button]')];
  const hit = nodes.find(el => labelOf(el) === want);
  if (hit) { hit.scrollIntoView && hit.scrollIntoView({ block: 'center' }); hit.click(); return true; }
  const soft = nodes.find(el => {
    const t = labelOf(el);
    return t.indexOf(want) >= 0 && t.length < 20;
  });
  if (soft) { soft.scrollIntoView && soft.scrollIntoView({ block: 'center' }); soft.click(); return true; }
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
    #: 浏览器模式：iSmart 网页版地址（没有桌面客户端时走这条路）
    browser_url: str = ""
    browser_port: int = DEFAULT_PORT
    #: 录制的操作流程（刷作业用；为空则回落到 submit_steps）
    recorded_steps: List[Dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def path() -> str:
        # 测试/脚本可用 ELH_ISMART_PROFILE 指向临时文件，避免污染真实配置
        override = os.environ.get("ELH_ISMART_PROFILE")
        return override or data_path(PROFILE_FILE)

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


# ---------------------------------------------------------------- 客户端探测

#: 依次尝试的调试端口（客户端版本/安装方式不同，端口不一定是我们默认的那个）
CDP_PORTS: Tuple[int, ...] = (DEFAULT_PORT, 9223, 9224, 9229, 8315)

#: 找客户端时扫描的目录（支持通配符）
CLIENT_SEARCH_PATTERNS: Tuple[str, ...] = (
    r"%LOCALAPPDATA%\Programs\*iSmart*",
    r"%LOCALAPPDATA%\Programs\*ismart*",
    r"%LOCALAPPDATA%\*iSmart*",
    r"%APPDATA%\*iSmart*",
    r"%ProgramFiles%\*iSmart*",
    r"%ProgramFiles(x86)%\*iSmart*",
    r"%ProgramFiles%\*ismart*",
    r"%ProgramFiles(x86)%\*ismart*",
)

_EXE_HINTS = ("ismart.exe", "ismartclient.exe", "ismartlearning.exe")

#: 可用作 CDP 宿主的浏览器（Windows 自带 Edge，通常无需另外安装）
BROWSER_CANDIDATES: Tuple[str, ...] = (
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
)

BROWSER_PROFILE_DIR = "browser-profile"


def find_browser() -> Optional[str]:
    """定位 Edge / Chrome 可执行文件（用作 CDP 宿主）。"""
    for pattern in BROWSER_CANDIDATES:
        candidate = os.path.expandvars(pattern)
        if os.path.isfile(candidate):
            return candidate
    return None


def launch_browser(
    url: str,
    port: int = DEFAULT_PORT,
    profile_dir: str = None,
    executable: str = None,
    wait_seconds: float = 25.0,
) -> Tuple[bool, str]:
    """带着调试端口启动浏览器并打开 ``url``，等端口就绪。

    使用独立 ``--user-data-dir``：既不会污染用户日常浏览器配置，
    也是 Chrome 允许开启远程调试的前提。
    """
    browser = executable or find_browser()
    if not browser:
        return False, "未找到 Edge / Chrome，无法使用浏览器模式"

    profile_dir = profile_dir or os.path.join(os.path.dirname(ISmartProfile.path()), BROWSER_PROFILE_DIR)
    try:
        os.makedirs(profile_dir, exist_ok=True)
    except OSError as exc:
        return False, f"创建浏览器配置目录失败: {exc}"

    args = [
        browser,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    try:
        subprocess.Popen(args, close_fds=True)
    except Exception as exc:
        return False, f"启动浏览器失败: {exc}"

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if probe_cdp_port(port):
            return True, f"浏览器已启动，调试端口 {port} 就绪"
        time.sleep(0.5)
    return False, f"浏览器已启动，但 {int(wait_seconds)} 秒内调试端口 {port} 未就绪"


def probe_cdp_port(port: int, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
    """检查某端口是不是 Chromium 的 DevTools 端点，是则返回 ``/json/version`` 内容。"""
    try:
        response = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=timeout)
        if response.status_code != 200:
            return None
        data = response.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    # Chromium 系都会带 webSocketDebuggerUrl / Browser 字段
    if "webSocketDebuggerUrl" in data or "Browser" in data:
        return data
    return None


def find_cdp_port(candidates: Tuple[int, ...] = None, timeout: float = 1.0) -> Optional[int]:
    """在候选端口里找到第一个可用的 DevTools 端点。"""
    for port in candidates or CDP_PORTS:
        if probe_cdp_port(port, timeout=timeout):
            logger.info("在端口 %s 发现 DevTools 端点", port)
            return port
    return None


def _registry_candidates():
    """从卸载表里找 iSmart 的安装路径（DisplayIcon / InstallLocation）。"""
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows
        return

    sub_keys = (
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
        r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    )
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for sub in sub_keys:
            try:
                base = winreg.OpenKey(root, sub)
            except OSError:
                continue
            for index in range(winreg.QueryInfoKey(base)[0]):
                try:
                    entry = winreg.OpenKey(base, winreg.EnumKey(base, index))
                except OSError:
                    continue
                try:
                    display = str(winreg.QueryValueEx(entry, "DisplayName")[0] or "")
                except OSError:
                    continue
                if "ismart" not in display.replace(" ", "").lower():
                    continue
                for value_name in ("DisplayIcon", "InstallLocation"):
                    try:
                        raw = str(winreg.QueryValueEx(entry, value_name)[0] or "")
                    except OSError:
                        continue
                    candidate = raw.split(",")[0].strip().strip('"')
                    if candidate.lower().endswith(".exe"):
                        yield candidate
                    elif candidate:
                        for hint in _EXE_HINTS:
                            yield os.path.join(candidate, hint)


def find_client_executable() -> Optional[str]:
    """定位 iSmart 桌面客户端可执行文件；找不到返回 None。"""
    seen = set()
    for candidate in _registry_candidates():
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(candidate) and "ismart" in os.path.basename(candidate).lower():
            return candidate

    for pattern in CLIENT_SEARCH_PATTERNS:
        expanded = os.path.expandvars(pattern)
        for directory in glob.glob(expanded):
            if not os.path.isdir(directory):
                continue
            for hint in _EXE_HINTS:
                candidate = os.path.join(directory, hint)
                if os.path.isfile(candidate):
                    return candidate
            matches = glob.glob(os.path.join(directory, "*.exe"))
            for candidate in matches:
                if "ismart" in os.path.basename(candidate).lower():
                    return candidate
    return None


def launch_client(port: int = DEFAULT_PORT, executable: str = None, wait_seconds: float = 25.0) -> Tuple[bool, str]:
    """带调试端口启动客户端，并等端口就绪。"""
    exe = executable or find_client_executable()
    if not exe:
        return False, "未找到 iSmart 客户端可执行文件，请先安装桌面客户端"

    try:
        # 新版 Chromium 只在来源可信时才接受 WS 连接，客户端内部页面互连需要这个开关
        subprocess.Popen(
            [
                exe,
                f"--remote-debugging-port={port}",
                "--remote-allow-origins=*",
                "--remote-debugging-address=127.0.0.1",
            ],
            close_fds=True,
        )
    except Exception as exc:
        return False, f"启动客户端失败: {exc}"

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if probe_cdp_port(port):
            return True, f"客户端已启动，调试端口 {port} 就绪"
        time.sleep(0.5)
    return False, f"客户端已启动，但 {int(wait_seconds)} 秒内调试端口 {port} 未就绪"


def normalize_url(url: str) -> str:
    """只接受 http/https 地址；其余（data:/file:/javascript: 等）返回空串。"""
    text = (url or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return text
    return ""


def _short_error(exc: BaseException) -> str:
    """把 requests/urllib3 的长堆栈压成一句人话。"""
    text = str(exc)
    if "Max retries exceeded" in text or "Connection refused" in text or "10061" in text:
        return "端口未开放"
    return type(exc).__name__


def extract_list(payload: Any) -> List[Dict[str, Any]]:
    """从各种「列表接口」返回结构里挖出记录数组。

    这些平台常见包法：``{"data": [...]}``、``{"data": {"list": [...]}}``、
    ``{"rows": [...]}``、``{"result": {"records": [...]}}``，直接递归找第一个 dict 列表。
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("list", "rows", "records", "items", "content", "courses", "data", "result"):
        if key in payload:
            found = extract_list(payload[key])
            if found:
                return found
    for value in payload.values():
        found = extract_list(value)
        if found:
            return found
    return []


def _first(payload: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if isinstance(payload, dict) and payload.get(key) not in (None, "", []):
            return payload[key]
    return None


# ---------------------------------------------------------------- CDP 底层

def discover_pages(port: int = DEFAULT_PORT, timeout: float = 3.0) -> List[Dict[str, Any]]:
    """列出调试端口上可附着的页面。"""
    try:
        response = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=timeout)
        response.raise_for_status()
        pages = response.json()
    except Exception as exc:
        raise ISmartError(f"无法连接调试端口 {port}（{_short_error(exc)}）") from exc
    return [page for page in pages if page.get("type") == "page" and page.get("webSocketDebuggerUrl")]


#: 学生中心把内容放在同源 iframe 里（``#ifra`` → ``courses.html``）。
#: 所有页面脚本都先切到「有内容的那个文档」再执行，否则在顶层文档里什么都找不到（实测踩过）。
CONTENT_SCRIPT_WRAPPER = r"""
(() => {
  let doc = document;
  const candidates = [
    '#ifra',
    'iframe[name=indexIframe]',
    '#index-iframe iframe',
    'iframe[src*="courses"]',
    'iframe[src*="course"]'
  ];
  for (const sel of candidates) {
    let frame = null;
    try { frame = document.querySelector(sel); } catch (e) { frame = null; }
    if (!frame) continue;
    let inner = null;
    try { inner = frame.contentDocument; } catch (e) { inner = null; }  // 跨域会抛
    if (!inner || !inner.body) continue;
    if (((inner.body.innerText || '') + '').trim().length > 0) { doc = inner; break; }
    if (doc === document) doc = inner;  // 还没渲染好也先用它，交给重试逻辑
  }
  const win = doc.defaultView || window;
  const loc = doc.location || location;
  return (function (document, window, location) {
    return (%s);
  })(doc, win, loc);
})()
"""


def wrap_for_content(script: str) -> str:
    """把页面脚本包进「优先在内容 iframe 里执行」的包装器。

    注意 ``return (...)`` 的括号：脚本本身以换行开头，不括号会被 JS 的自动分号插入
    （ASI）截断成 ``return;``，函数直接返回 undefined —— 实测踩过，排查了很久。
    """
    return CONTENT_SCRIPT_WRAPPER % (script.strip(),)


class CDPSession:
    """CDP 会话。

    附着方式用的是 **浏览器级 WebSocket + ``Target.attachToTarget(flatten=True)``**：
    目标 id 通过 ``Target.getTargets`` 实时枚举，而不是 ``/json/list`` 里可能已失效的 id
    （实测中带上旧 id 握手会得到 ``No such target id``，进而表现为「连上了但没有任何回复」）。
    """

    def __init__(self, ws_url: str, timeout: float = COMMAND_TIMEOUT):
        try:
            import websocket  # websocket-client
        except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确提示
            raise ISmartError("缺少依赖 websocket-client，请执行 pip install -r requirements.txt") from exc

        self._next_id = 0
        self.session_id: Optional[str] = None
        self.timeout = timeout
        try:
            self._ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
        except Exception as exc:
            raise ISmartError(f"连接浏览器调试端口失败: {exc}") from exc

    @classmethod
    def attach_to_browser(cls, port: int, host_keyword: str = "") -> "CDPSession":
        """连到浏览器级端点，挑选页面 target 并建立 flattened session。"""
        try:
            version = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=3).json()
            ws_url = version["webSocketDebuggerUrl"]
        except Exception as exc:
            raise ISmartError(f"无法读取浏览器调试信息（{_short_error(exc)}）") from exc

        session = cls(ws_url)
        targets = session.send("Target.getTargets").get("targetInfos", [])
        pages = [info for info in targets if info.get("type") == "page"]
        keyword = (host_keyword or "").lower()
        chosen = None
        for info in pages:
            if not keyword or keyword in (info.get("url") or "").lower():
                chosen = info
                break
        if chosen is None and pages:
            chosen = pages[0]
        if chosen is None:
            session.close()
            raise ISmartError("浏览器里没有可附着的页面，请先打开课程页")

        attached = session.send(
            "Target.attachToTarget", {"targetId": chosen["targetId"], "flatten": True}
        )
        session.session_id = attached.get("sessionId")
        if not session.session_id:
            session.close()
            raise ISmartError("附着页面失败（没有拿到 sessionId）")
        session.target_url = chosen.get("url") or ""
        session.target_title = chosen.get("title") or ""
        # 把端点里所有 target 打出来：换客户端/换平台时，这行日志能立刻看出暴露了什么
        for info in targets:
            logger.info(
                "  target[%s] %s %s",
                info.get("type"),
                info.get("title") or "-",
                (info.get("url") or "-")[:120],
            )
        logger.info("已附着页面 target=%s url=%s", chosen.get("targetId"), session.target_url)
        return session

    def send(self, method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        payload = {"id": request_id, "method": method, "params": params or {}}
        if self.session_id:
            payload["sessionId"] = self.session_id
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

    def drain(self, seconds: float, should_stop=None) -> List[Dict[str, Any]]:
        """读一段时间的事件帧（抓 XHR / 页面事件用）。"""
        events: List[Dict[str, Any]] = []
        deadline = time.monotonic() + max(0.0, float(seconds))
        try:
            self._ws.settimeout(0.5)
        except Exception:  # 替身对象可能没有 settimeout
            pass
        while time.monotonic() < deadline:
            if should_stop is not None and should_stop():
                break
            try:
                raw = self._ws.recv()
            except Exception:
                continue  # 超时是正常的：说明这段时间没有事件
            if not raw:
                break
            try:
                message = json.loads(raw)
            except Exception:
                continue
            if "method" in message:
                events.append(message)
        try:
            self._ws.settimeout(self.timeout)
        except Exception:
            pass
        return events

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
        # 后台标签页可能被节流导致截图超时：先切到前台，失败再重试一次
        try:
            self.send("Page.bringToFront")
        except ISmartError:
            pass
        try:
            result = self.send("Page.captureScreenshot", {"format": "png"})
        except ISmartError:
            time.sleep(0.5)
            result = self.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
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
        self._recorder_script_id: Optional[str] = None
        #: 课程列表页地址（进入课程后用它回到列表）
        self._list_url = ""
        #: 外壳页面地址（iframe 回退失败时整页重载用）
        self._outer_url = ""

    # ------------------------------------------------------------ 连接

    def _attach(self) -> CDPSession:
        if self._session is not None:
            return self._session

        # 客户端版本不同、启动参数不同，端口不一定是 9222：先试配置里的端口，再扫候选端口
        port = self.profile.port
        if find_cdp_port((port,), timeout=1.0) is None:
            discovered = find_cdp_port(CDP_PORTS, timeout=1.0)
            if discovered:
                port = discovered
        self.profile.port = port

        # 附着失败重试一次：页面可能刚导航完，target 还在切换
        last_error: Optional[ISmartError] = None
        for attempt in range(2):
            try:
                session = CDPSession.attach_to_browser(port, self.profile.host_keyword)
                session.send("Runtime.enable")
                self._session = session
                self._page = {
                    "url": getattr(session, "target_url", ""),
                    "title": getattr(session, "target_title", ""),
                }
                logger.info("已附着 iSmart 页面: %s", self._page.get("title") or self._page.get("url"))
                return session
            except ISmartError as exc:
                last_error = exc
                logger.warning("附着失败（第 %d 次）: %s", attempt + 1, exc)
                if attempt == 0:
                    time.sleep(0.4)
        raise last_error or ISmartError("附着页面失败")

    def launch_and_connect(self, port: int = None, wait_seconds: float = 25.0) -> Tuple[bool, str]:
        """优先启动桌面客户端；没有客户端时若有网页版地址则改用浏览器模式。"""
        target_port = int(port or self.profile.port)
        exe = find_client_executable()
        if not exe:
            if self.profile.browser_url:
                logger.info("未找到桌面客户端，改用浏览器模式: %s", self.profile.browser_url)
                return self.open_in_browser(self.profile.browser_url, target_port, wait_seconds)
            self.last_error_code = ErrCode.CDP
            return False, (
                "未找到 iSmart 桌面客户端，也没有配置网页版地址。\n"
                "① 安装桌面版后点「🚀 启动并连接」；"
                "② 或点「🌐 浏览器模式」，填入学校给的 iSmart 网页版地址，用本机 Edge/Chrome 打开。"
            )
        logger.info("启动 iSmart 客户端: %s（端口 %s）", exe, target_port)
        ok, message = launch_client(target_port, exe, wait_seconds)
        if not ok:
            self.last_error_code = ErrCode.CDP
            return False, message
        self.profile.port = target_port
        return self.login("", "")

    def login(self, username: str, password: str) -> Tuple[bool, str]:
        self.last_error_code = ""
        try:
            self._attach()
        except ISmartError as exc:
            self.last_error_code = ErrCode.CDP
            return False, self._explain_unavailable(str(exc))
        return True, "已连接 iSmart 客户端（登录态以客户端为准）"

    def _explain_unavailable(self, reason: str) -> str:
        """把「连不上」翻译成一眼能照做的下一步（不再甩 urllib3 堆栈）。"""
        tried = "、".join(str(port) for port in (self.profile.port, *CDP_PORTS))
        executable = find_client_executable()
        if executable:
            tail = f"已找到客户端：{executable}\n点「🚀 启动并连接」可自动带调试参数启动。"
        elif self.profile.browser_url:
            tail = (
                "未找到 iSmart 桌面客户端，但已配置网页版地址：\n"
                f"{self.profile.browser_url}\n"
                "点「🌐 浏览器模式」会用系统 Edge/Chrome 带着调试端口打开它。"
            )
        else:
            tail = (
                "未找到 iSmart 桌面客户端。\n"
                "两条路：① 安装桌面版后点「🚀 启动并连接」；"
                "② 点「🌐 浏览器模式」，填入学校给的 iSmart 网页版地址，用本机 Edge/Chrome 打开。"
            )
        return f"{reason}\n已尝试端口：{tried}\n{tail}"

    def _browser_profile_dir(self) -> str:
        return os.path.join(os.path.dirname(ISmartProfile.path()), BROWSER_PROFILE_DIR)

    def open_in_browser(self, url: str = None, port: int = None, wait_seconds: float = 25.0) -> Tuple[bool, str]:
        """用本机 Edge/Chrome（带调试端口）打开 iSmart 网页版并附着。"""
        raw = (url or self.profile.browser_url or "").strip()
        target = normalize_url(raw)
        if not target:
            self.last_error_code = ErrCode.CDP
            if raw:
                # 历史配置里可能残留 data:/file: 之类的无效值，直接提示重填
                self.profile.browser_url = ""
                return False, (
                    f"这个地址不是网页地址，无法打开：{raw[:60]}\n"
                    "请填学校给的 iSmart 网页版地址（以 http:// 或 https:// 开头）。"
                )
            return False, "请先填写 iSmart 网页版地址（学校给的登录页），再点「🌐 浏览器模式」"

        browser = find_browser()
        if not browser:
            self.last_error_code = ErrCode.CDP
            return False, "未找到 Edge / Chrome，无法使用浏览器模式"

        target_port = int(port or self.profile.browser_port or DEFAULT_PORT)
        logger.info("浏览器模式启动: %s -> %s（端口 %s）", browser, target, target_port)
        ok, message = launch_browser(
            target,
            target_port,
            profile_dir=self._browser_profile_dir(),
            executable=browser,
            wait_seconds=wait_seconds,
        )
        if not ok:
            self.last_error_code = ErrCode.CDP
            return False, message

        self.profile.browser_url = target
        self.profile.browser_port = target_port
        self.profile.port = target_port
        try:
            self.profile.save()
        except OSError as exc:
            logger.warning("保存 iSmart 配置失败: %s", exc)
        return self.login("", "")

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
        self._session = None
        self._page = None

    # ------------------------------------------------------------ 脚本执行

    def _eval(self, expression: str) -> Any:
        """执行页面脚本（自动切到内容 iframe）。

        顶层文档只是外壳，课程/任务都在 ``#ifra`` 里；直接在最外层跑什么都读不到。
        个别调用方需要顶层（例如判断登录页），用 ``_eval_top``。
        """
        return self._attach().evaluate(wrap_for_content(expression))

    def _eval_top(self, expression: str) -> Any:
        """在顶层文档执行（不切 iframe）。"""
        return self._attach().evaluate(expression)

    # ------------------------------------------------------------ 页面自检

    def login_page_info(self) -> Dict[str, Any]:
        """当前页面是不是登录页（用于给出「先登录」这种能照做的提示）。"""
        try:
            data = self._eval(LOGIN_CHECK_JS) or {}
        except ISmartError:
            return {}
        return data if isinstance(data, dict) else {}

    def page_introspect(self) -> Dict[str, Any]:
        """结构化自检：属性统计 / 链接 / 按钮 / XHR 接口，比整页 HTML 有用得多。"""
        data: Dict[str, Any] = {}
        # 页面刚打开时内容是异步渲染的，立刻读会得到「标题有、内容全空」的假象（实测踩过）
        for _ in range(6):
            data = self._eval(INTROSPECT_JS) or {}
            if not isinstance(data, dict):
                data = {}
            if data.get("text") or data.get("inputs") or data.get("anchors"):
                break
            time.sleep(0.8)

        stamp = time.strftime("%Y%m%d_%H%M%S")
        target_dir = os.path.join(os.path.dirname(ISmartProfile.path()), DUMP_DIR)
        try:
            os.makedirs(target_dir, exist_ok=True)
            from core.atomic import write_text_atomic

            path = os.path.join(target_dir, f"introspect_{stamp}.json")
            write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))
            data["saved_to"] = path
        except OSError as exc:
            logger.warning("保存自检结果失败: %s", exc)

        logger.info(
            "页面自检: url=%s title=%r 登录页=%s data-*前3=%s 链接=%d 按钮=%d XHR=%d",
            data.get("url"),
            data.get("title"),
            data.get("likelyLogin"),
            (data.get("dataAttrs") or [])[:3],
            len(data.get("anchors") or []),
            len(data.get("buttons") or []),
            len(data.get("xhr") or []),
        )
        for url in (data.get("xhr") or [])[:10]:
            logger.info("  接口: %s", url)
        return data

    def get_courses(self) -> Tuple[bool, List[Dict], str]:
        self._wait_content()
        # 优先走接口：学生中心是 XHR 渲染的，扒 DOM 容易受渲染时机/框架影响
        ok, payload, err = self.api_get(COURSE_LIST_PATH, {"status": 1, "pager.pageSize": 50})
        if ok:
            courses = self._courses_from_records(extract_list(payload))
            if courses:
                logger.info("接口拿到 %d 门课程", len(courses))
                self._remember_list_url()
                return True, courses, "获取课程成功（接口）"
            logger.info("接口没有返回课程记录，回退页面扫描")
        elif err:
            logger.info("接口取课程失败（%s），回退页面扫描", str(err))

        try:
            raw_courses = self._eval(self.profile.courses_js)
        except ISmartError as exc:
            self.last_error_code = ErrCode.PARSE
            return False, [], str(exc)
        courses = (
            [c for c in raw_courses if isinstance(c, dict) and c.get("cid")]
            if isinstance(raw_courses, list)
            else []
        )
        if courses:
            self._remember_list_url()
            return True, courses, "获取课程成功"

        self.last_error_code = ErrCode.PARSE
        login = self.login_page_info()
        if login.get("isLogin"):
            return (
                False,
                [],
                "当前还是登录页（%s）。请先用「🌐 浏览器模式」打开的窗口完成登录，"
                "进到课程列表页后回到本窗口点「🔄 刷新课程」。" % (login.get("title") or "登录"),
            )
        return (
            False,
            [],
            "当前页面不是课程列表（url=%s）。请先在浏览器窗口里进到课程列表页，"
            "再点「🔄 刷新课程」；若仍找不到，点「🔎 页面自检」把结果发我，我按真实结构改 courses_js。"
            % (login.get("url") or "未知"),
        )

    def _wait_content(self, timeout: float = None) -> bool:
        """等内容 iframe 渲染出可见文本（学生中心是 XHR 渲染，读太早会全空）。

        超时可用 ``ELH_CONTENT_TIMEOUT`` 覆盖（测试里调到 0.1 秒，避免拖慢用例）。
        """
        if timeout is None:
            try:
                timeout = float(os.environ.get("ELH_CONTENT_TIMEOUT", "8"))
            except ValueError:
                timeout = 8.0
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            try:
                text = str(self._eval("(document.body ? document.body.innerText : '')") or "")
            except Exception:
                text = ""
            if text.strip():
                return True
            time.sleep(0.5)
        logger.warning("等了 %ss 内容区仍然是空的", int(timeout))
        return False

    def _remember_list_url(self) -> None:
        try:
            self._list_url = str(self._eval("location.href") or "")   # 内容 iframe 的地址
        except Exception:
            self._list_url = ""
        try:
            self._outer_url = str(self._eval_top("location.href") or "")  # 外壳地址
        except Exception:
            self._outer_url = ""
        logger.info("课程列表地址: iframe=%s 外壳=%s", self._list_url, self._outer_url)

    @staticmethod
    def _courses_from_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        courses: List[Dict[str, Any]] = []
        for item in records or []:
            cid = _first(item, "courseId", "course_id", "id", "courseCode")
            if not cid:
                continue
            name = _first(item, "courseName", "name", "title", "courseTitle") or f"课程 {cid}"
            teacher = _first(item, "teacherName", "teacher", "lecturer", "teacherRealName") or ""
            label = f"{name}（{teacher}）" if teacher else str(name)
            # per 留给「学习进度」：iSmart 的记录里没有，别把主讲塞进去
            courses.append({"cid": str(cid), "name": label[:70], "per": ""})
        return courses

    # ------------------------------------------------------------ 识别任务

    def discover_tasks(self) -> Dict[str, Any]:
        """识别当前页上的任务条目（进课程后点「🔍 识别任务」用）。

        不需要预先录制：直接把候选条目、文案与 CSS 选择器列出来，存 dumps/tasks_*.json。
        """
        self._wait_content()
        try:
            raw = self._eval(fill_task_labels(self.profile.tasks_js)) or []
        except ISmartError as exc:
            self.last_error_code = ErrCode.PARSE
            logger.error("识别任务失败: %s", exc)
            return {"count": 0, "tasks": [], "error": str(exc)}

        tasks = [t for t in raw if isinstance(t, dict) and t.get("id")] if isinstance(raw, list) else []
        result = {
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "page": self._list_url,
            "count": len(tasks),
            "tasks": tasks[:100],
        }
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target_dir = os.path.join(os.path.dirname(ISmartProfile.path()), DUMP_DIR)
        try:
            os.makedirs(target_dir, exist_ok=True)
            from core.atomic import write_text_atomic

            path = os.path.join(target_dir, f"tasks_{stamp}.json")
            write_text_atomic(path, json.dumps(result, ensure_ascii=False, indent=2))
            result["saved_to"] = path
        except OSError as exc:
            logger.warning("保存任务清单失败: %s", exc)

        logger.info("识别到 %d 个任务候选（页面 %s）", len(tasks), self._list_url)
        for task in tasks[:20]:
            logger.info("  %s | %s | %s", task.get("id"), task.get("location"), task.get("selector", ""))
        return result

    # ------------------------------------------------------------ 接口调用

    def cookies(self) -> Dict[str, str]:
        """取页面登录态 Cookie（优先 CDP，能拿到 HttpOnly）。"""
        jar: Dict[str, str] = {}
        try:
            data = self._attach().send("Network.getAllCookies") or {}
            for item in data.get("cookies", []) or []:
                name = item.get("name")
                if name:
                    jar[name] = item.get("value", "")
            if jar:
                logger.info("已从浏览器取到 %d 个 Cookie", len(jar))
                return jar
        except Exception as exc:  # 会话替身/不支持该命令都不该让上层崩掉
            logger.debug("CDP 取 Cookie 失败: %s", exc)

        try:
            raw = str(self._eval("document.cookie") or "")
        except Exception:
            raw = ""
        for part in raw.split(";"):
            if "=" in part:
                key, value = part.split("=", 1)
                jar[key.strip()] = value.strip()
        return jar

    def api_get(self, path: str, params: Dict[str, Any] = None, timeout: float = 10.0) -> Tuple[bool, Any, str]:
        """带页面登录态调业务接口（只读，用来替代扒 DOM）。"""
        try:
            jar = self.cookies()
        except Exception as exc:
            return False, None, f"取登录态失败（{_short_error(exc)}）"
        if not jar:
            return False, None, "拿不到登录 Cookie，请先在浏览器窗口里登录"
        url = path if str(path).startswith("http") else API_BASE + str(path)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": self._list_url or "https://me.ismartlearning.cn/",
        }
        try:
            response = requests.get(url, params=params or {}, cookies=jar, headers=headers, timeout=timeout)
        except Exception as exc:
            return False, None, f"接口请求失败（{_short_error(exc)}）"
        if response.status_code != 200:
            return False, None, f"接口返回 HTTP {response.status_code}"
        try:
            return True, response.json(), ""
        except ValueError:
            return False, None, "接口返回的不是 JSON（登录可能已失效）"

    # ------------------------------------------------------------ 进/出课程

    def click_course(self, cid, wait_seconds: float = 12.0) -> bool:
        """点开第 N 张课程卡片（cid = ``card:N``），并等页面切换。"""
        if not str(cid).startswith("card:"):
            return bool(self._eval(click_task_script(cid)))
        before = str(self._eval("location.href") or "")
        if not self._eval(click_task_script(cid)):
            return False
        return bool(self._wait_url_change(before, wait_seconds))

    def _wait_url_change(self, before: str, timeout: float = 12.0) -> str:
        """等 URL 变化（点击后多为整页跳转或 SPA 路由切换）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.4)
            try:
                current = str(self._eval("location.href") or "")
            except ISmartError:
                return ""
            if current and current != before:
                logger.info("页面已切换: %s", current)
                return current
        logger.warning("点了之后 %ss 内页面没有变化", int(timeout))
        return ""

    def return_to_list(self) -> bool:
        """回到课程列表（优先把 iframe 拨回去，避免整页重载）。"""
        if not self._list_url:
            return False
        try:
            # 内容在 iframe 里，改 iframe 的 location 即可，外壳不用重载
            self._eval("location.href = %s" % json.dumps(self._list_url))
            time.sleep(2.0)
            logger.info("已把内容 iframe 拨回课程列表: %s", self._list_url)
            return True
        except Exception as exc:
            logger.warning("iframe 回退失败，改用整页重载: %s", exc)

        if not self._outer_url:
            return False
        try:
            self._attach().send("Page.navigate", {"url": self._outer_url})
        except ISmartError as exc:
            logger.warning("返回课程列表失败: %s", exc)
            return False
        time.sleep(2.5)
        logger.info("已整页重载回课程列表: %s", self._outer_url)
        return True

    def get_course_info(self, cid) -> Tuple[bool, Optional[Dict], str]:
        try:
            data = self._eval(self.profile.units_js) or {}
        except ISmartError as exc:
            self.last_error_code = ErrCode.PARSE
            return False, None, str(exc)

        units = data.get("units") or []
        if not units:
            # 学生中心/课程页没有 data-unitid 时退化为「整门课程当一个单元」：
            # 这样刷作业/刷时长仍能按课程卡片跑（具体走位交给录制流程）
            logger.info("页面里没有单元元素，按整门课程处理: %s", cid)
            units = [{"id": str(cid), "name": "整门课程", "visible": "true"}]
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
        self._wait_content()
        try:
            raw_tasks = self._eval(fill_task_labels(self.profile.tasks_js))
        except ISmartError as exc:
            self.last_error_code = ErrCode.PARSE
            return False, [], str(exc)
        tasks = (
            [t for t in raw_tasks if isinstance(t, dict) and t.get("id")]
            if isinstance(raw_tasks, list)
            else []
        )
        if not tasks:
            # 识别不到任务条目时，把「这张课程卡片本身」当任务：
            # 进课程 + 录制流程（或挂时长）照样能跑
            logger.info("页面里没有任务元素，按课程卡片处理: %s", cid)
            tasks = [{"id": str(cid), "location": "整门课程", "isvisible": "true", "iscomplete": "未完成"}]
        return True, tasks, "Success"

    # ------------------------------------------------------------ 提交流程

    def _run_steps(self, scoid) -> List[str]:
        card_mode = str(scoid).startswith("card:")
        if card_mode:
            # 课程卡片模式：先把课程点开，后续步骤在课程页里执行
            if not self.click_course(scoid):
                raise ISmartError(f"没能点开课程卡片 {scoid}")

        # 录制过流程就优先用录制的（真实页面走位比默认启发式可靠得多）
        if self.profile.recorded_steps:
            task_id = "" if card_mode else scoid
            try:
                return self._runner().run(
                    self.profile.recorded_steps, {"scoid": task_id, "task": task_id}
                )
            except FlowError as exc:
                raise ISmartError(str(exc)) from exc

        logs: List[str] = []
        for step in self.profile.submit_steps:
            kind = step.get("kind", "")
            if kind == "click_task":
                if card_mode:
                    continue  # 已经进课程了，别再点一次卡片
                target = str(step.get("scoid", "{scoid}")).replace("{scoid}", str(scoid))
                ok = self._eval(click_task_script(target))
                if not ok:
                    raise ISmartError(f"没找到任务 {target} 对应的元素")
                logs.append(f"已点击任务 {target}")
            elif kind == "click_submit":
                # 不同页面把提交按钮叫不同名字，按顺序试，命中即用
                clicked = None
                for label in SUBMIT_LABELS:
                    if self._eval(CLICK_TEXT_JS % {"text": json.dumps(label)}):
                        clicked = label
                        break
                if not clicked:
                    raise ISmartError(
                        "没找到提交按钮（试过：" + "、".join(SUBMIT_LABELS) + "）；"
                        "请用「⏺ 录制流程」录一遍提交动作"
                    )
                logs.append(f"已点击「{clicked}」")
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
        card_mode = str(scoid).startswith("card:")
        try:
            for line in self._run_steps(scoid):
                logger.info(line)
        except ISmartError as exc:
            self.last_error_code = ErrCode.PARSE
            logger.error("iSmart 提交流程失败: %s", exc)
            return 0, 1, 0, 1
        finally:
            if card_mode:
                self.return_to_list()
        return 1, 0, 0, 1  # iSmart 没有 WeLearn 的「双通道」概念，第二路恒记为未走

    # ------------------------------------------------------------ 录制 / 回放

    def _runner(self) -> FlowRunner:
        return FlowRunner(self._attach())

    def start_recording(self) -> bool:
        """开始录制点击：注入监听器，并登记「导航后自动重新注入」。"""
        try:
            session = self._attach()
            session.send("Page.enable")
            # 真实平台点一下就会跳转，页面一换 window 状态就没了，所以让它在每次导航后自己回来
            result = session.send(
                "Page.addScriptToEvaluateOnNewDocument", {"source": RECORDER_BOOTSTRAP_JS}
            )
            self._recorder_script_id = result.get("identifier")
            state = str(self._eval(RECORDER_START_JS))
            if state not in ("ready", "started"):
                logger.warning("录制器注入返回异常状态: %s", state)
            return True
        except ISmartError as exc:
            self.last_error_code = ErrCode.CDP
            logger.error("开始录制失败: %s", exc)
            return False

    def stop_recording(self) -> List[Dict[str, Any]]:
        """结束录制，把事件转成步骤并写进配置（返回生成的步骤）。"""
        events = []
        try:
            events = self._eval(RECORDER_STOP_JS) or []
        except ISmartError as exc:
            self.last_error_code = ErrCode.CDP
            logger.error("结束录制失败: %s", exc)
            return []
        finally:
            self._remove_recorder_script()

        steps = build_steps(events if isinstance(events, list) else [])
        if not steps:
            logger.warning("录制结果为空（可能一次都没点，或页面还没加载完）")
            return []
        self.profile.recorded_steps = steps
        try:
            self.profile.save()
        except OSError as exc:
            logger.warning("保存录制流程失败: %s", exc)
        logger.info("已录制 %d 个步骤（原始点击 %d 次）", len(steps), len(events))
        return steps

    def _remove_recorder_script(self) -> None:
        script_id = getattr(self, "_recorder_script_id", None)
        self._recorder_script_id = None
        if not script_id or self._session is None:
            return
        try:
            self._session.send(
                "Page.removeScriptToEvaluateOnNewDocument", {"identifier": script_id}
            )
        except Exception as exc:  # 页面可能已关闭，忽略
            logger.debug("移除录制脚本失败: %s", exc)

    def clear_recording(self) -> None:
        self.profile.recorded_steps = []
        try:
            self.profile.save()
        except OSError as exc:
            logger.warning("清空录制流程失败: %s", exc)

    def run_recorded_flow(self, scoid=None, extra: Dict[str, Any] = None) -> Tuple[bool, str]:
        """在录制流程上按当前任务重放（``{scoid}`` 会被替换）。"""
        steps = self.profile.recorded_steps
        if not steps:
            return False, "还没有录制流程，请先点「⏺ 录制流程」在页面里手动走一遍"
        variables = {"scoid": scoid if scoid is not None else "", "task": scoid}
        variables.update(extra or {})
        try:
            logs = self._runner().run(steps, variables)
        except FlowError as exc:
            return False, str(exc)
        except ISmartError as exc:
            return False, f"回放失败: {exc}"
        logger.info("回放完成：%s", "；".join(logs[-5:]))
        return True, "回放完成"

    # ------------------------------------------------------------ 挂时长

    def simulate_time(self, cid, uid, scoid, learning_time, stop_event=None) -> bool:
        """保持任务页活跃，让平台把时间累计上去。

        机制：先点开该任务，再注入 15 秒一次的轻微鼠标/滚动活动，直到时间满。
        是否计入由平台决定；本方法只负责「让页面看起来一直有人在看」。
        """
        learning_time = max(1, int(learning_time))
        try:
            if scoid:
                ok = self._eval(click_task_script(scoid))
                if not ok:
                    logger.warning("没找到任务 %s 的元素，直接在当前页面挂时长", scoid)
                else:
                    time.sleep(1.5)  # 等页面切换/播放器初始化
            self._attach().send("Page.bringToFront")
        except ISmartError as exc:
            self.last_error_code = ErrCode.CDP
            logger.error("挂时长前打开任务失败: %s", exc)
            return False

        runner = self._runner()
        try:
            runner.keepalive_start()
        except ISmartError as exc:
            logger.warning("注入保持活跃脚本失败: %s", exc)

        started = time.monotonic()
        logger.info("开始挂时长：目标 %ss（任务 %s）", learning_time, scoid)
        try:
            while True:
                elapsed = time.monotonic() - started
                remaining = learning_time - elapsed
                if remaining <= 0:
                    break
                wait_for = min(30.0, remaining)
                if stop_event is not None:
                    if stop_event.wait(wait_for):
                        logger.info("挂时长被中断，已完成 %ds", int(elapsed))
                        return False
                else:
                    time.sleep(wait_for)
                logger.debug("挂时长进度 %ds/%ds", int(elapsed + wait_for), learning_time)
        finally:
            try:
                runner.keepalive_stop()
            except ISmartError as exc:
                logger.debug("移除保持活跃脚本失败: %s", exc)
        logger.info("挂时长完成：任务 %s 累计 %ds", scoid, int(time.monotonic() - started))
        # 卡片模式下页面已经跳到课程里了，挂完回列表，方便下一张卡片继续
        if str(scoid).startswith("card:"):
            self.return_to_list()
        return True

    # ------------------------------------------------------------ 抓接口

    def capture_network(self, seconds: float = 60.0, should_stop=None) -> Dict[str, Any]:
        """抓一段时间的网络请求（含 POST body），用来搞清真正的业务接口。"""
        session = self._attach()
        session.send("Network.enable")
        logger.info("开始抓接口 %ss —— 请在浏览器里点几下（进课程/点任务）", int(seconds))
        events = session.drain(seconds, should_stop)

        aggregated: Dict[str, Dict[str, Any]] = {}
        for event in events:
            if event.get("method") != "Network.requestWillBeSent":
                continue
            request = (event.get("params") or {}).get("request") or {}
            url = request.get("url") or ""
            if not url or url.startswith("data:"):
                continue
            entry = aggregated.setdefault(
                url, {"url": url, "method": request.get("method"), "count": 0, "postData": None}
            )
            entry["count"] += 1
            post = request.get("postData")
            if post and not entry["postData"]:
                entry["postData"] = str(post)[:4000]

        result = {
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "seconds": int(seconds),
            "page": self._list_url or "",
            "requests": list(aggregated.values()),
        }
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target_dir = os.path.join(os.path.dirname(ISmartProfile.path()), DUMP_DIR)
        try:
            os.makedirs(target_dir, exist_ok=True)
            from core.atomic import write_text_atomic

            path = os.path.join(target_dir, f"api_{stamp}.json")
            write_text_atomic(path, json.dumps(result, ensure_ascii=False, indent=2))
            result["saved_to"] = path
        except OSError as exc:
            logger.warning("保存抓包结果失败: %s", exc)

        logger.info("抓接口完成：%d 个请求", len(result["requests"]))
        for item in result["requests"][:30]:
            logger.info("  %s %s", item.get("method"), item.get("url"))
        return result

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