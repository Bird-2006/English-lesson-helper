"""WeLearn（welearn.sflep.com）平台适配器。

内容基本沿用原来的 ``core/api.py``，只是改为实现 :class:`PlatformClient`
接口；``core/api.py`` 保留为兼容转发层，历史导入路径仍然可用。

相比旧实现改掉的坑：

- 登录参数原来靠 ``url.split("%26")`` 再 ``split("%3D")`` 抠
  ``code_challenge`` / ``state``，服务端换个参数顺序就静默失效 →
  改用 ``urllib.parse`` 正规解析（抽成纯函数便于测试）；
- 全部线程共用一个 ``requests.Session``，cookie jar 会被并发线程同时改写 →
  改为线程局部 Session；
- CMI 报文用 f-string 手拼 JSON、成功判定靠 ``'"ret":0' in text`` →
  改 ``json.dumps`` + 解析 ``ret``；
- ``simulate_time`` 逐秒 ``time.sleep(1)`` 空转且不可中断 → 可中断等待；
- 增加 ``ErrCode`` 分类，界面不用再对中文错误串做子串匹配。
"""
import json
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests

from core.constants import ErrCode
from core.crypto import generate_cipher_text
from core.logger import get_logger
from core.platforms.base import Capability, PlatformClient, PlatformInfo

logger = get_logger("WeLearnAPI")

PLATFORM = PlatformInfo(
    key="welearn",
    name="WeLearn（welearn.sflep.com）",
    description="上海外语教育出版社 WeLearn 平台，凭账号口令直接调用接口",
    capabilities=frozenset({Capability.HOMEWORK, Capability.DURATION}),
)

DEFAULT_TIMEOUT = 30
SHORT_TIMEOUT = 10
HEARTBEAT_SECONDS = 60
MAX_LOGIN_RETRY = 3


def parse_prelogin_url(url: str) -> Tuple[str, str]:
    """从 prelogin 重定向后的 URL 里取 ``code_challenge`` 与 ``state``。

    取不到时抛 ``ValueError``，由调用方转成「登录URL格式异常」。
    """
    query = parse_qs(urlparse(url).query)
    challenge = (query.get("code_challenge") or [""])[0]
    state = (query.get("state") or [""])[0]
    if not challenge or not state:
        raise ValueError("prelogin URL 缺少 code_challenge/state")
    return challenge, state


def classify_exception(exc: BaseException) -> str:
    """把异常归类成 ``ErrCode``，供界面决定提示文案。"""
    if isinstance(exc, requests.exceptions.Timeout):
        return ErrCode.TIMEOUT
    if isinstance(exc, requests.exceptions.ConnectionError):
        return ErrCode.NETWORK
    if isinstance(exc, (ValueError, KeyError, json.JSONDecodeError)):
        return ErrCode.PARSE
    if isinstance(exc, requests.exceptions.RequestException):
        return ErrCode.NETWORK
    return ErrCode.UNKNOWN


class WeLearnClient(PlatformClient):
    """单个账号的 WeLearn 会话。"""

    info = PLATFORM

    BASE_URL = "https://welearn.sflep.com"
    SSO_URL = "https://sso.sflep.com/idsvr/account/login"

    def __init__(self):
        self.last_error_code = ""
        self._local = threading.local()
        self._header_template = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    # ---------------------------------------------------------------- 会话

    @property
    def session(self) -> requests.Session:
        """每线程一个 Session，避免并发线程互相覆盖 cookie。"""
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(self._header_template)
            self._local.session = session
        return session

    def close(self) -> None:
        session = getattr(self._local, "session", None)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
            self._local.session = None

    def _fail(self, code: str, message: str) -> Tuple[bool, str]:
        self.last_error_code = code
        return False, message

    # ---------------------------------------------------------------- 登录

    def login(self, username: str, password: str) -> Tuple[bool, str]:
        self.last_error_code = ""
        last_message = "登录失败"
        for attempt in range(MAX_LOGIN_RETRY):
            try:
                ok, message, retryable = self._login_once(username, password)
                if ok:
                    return True, message
                last_message = message
                if not retryable:
                    return False, message
            except Exception as exc:
                self.last_error_code = classify_exception(exc)
                last_message = f"登录过程中发生错误: {exc}"
                logger.warning("登录异常（第 %d 次）: %s", attempt + 1, exc)
            if attempt < MAX_LOGIN_RETRY - 1:
                time.sleep(2)
        return False, last_message

    def _login_once(self, username: str, password: str) -> Tuple[bool, str, bool]:
        prelogin = self.session.get(
            f"{self.BASE_URL}/user/prelogin.aspx?loginret=http://welearn.sflep.com/user/loginredirect.aspx",
            timeout=DEFAULT_TIMEOUT,
        )
        if prelogin.status_code != 200:
            self.last_error_code = ErrCode.SERVER
            return False, f"网络请求失败，状态码: {prelogin.status_code}", True

        try:
            code_challenge, state = parse_prelogin_url(prelogin.url)
        except ValueError:
            self.last_error_code = ErrCode.PARSE
            return False, "登录URL格式异常（可能是网络问题或服务器维护）", True

        rturl = (
            "/connect/authorize/callback?client_id=welearn_web"
            "&redirect_uri=https%3A%2F%2Fwelearn.sflep.com%2Fsignin-sflep"
            "&response_type=code&scope=openid%20profile%20email%20phone%20address"
            f"&code_challenge={code_challenge}&code_challenge_method=S256&state={state}"
            "&x-client-SKU=ID_NET472&x-client-ver=6.32.1.0"
        )
        cipher, timestamp = generate_cipher_text(password)
        form = {"rturl": rturl, "account": username, "pwd": cipher, "ts": timestamp}

        response = self.session.post(self.SSO_URL, data=form, timeout=DEFAULT_TIMEOUT)
        if response.status_code != 200:
            self.last_error_code = ErrCode.SERVER
            return False, f"登录请求失败，状态码: {response.status_code}", True

        try:
            payload = response.json()
        except ValueError:
            self.last_error_code = ErrCode.PARSE
            return False, "登录响应不是合法 JSON", True

        code = payload.get("code", -1)
        if code == 1:
            self.last_error_code = ErrCode.AUTH
            return False, "帐号或密码错误", False
        if code != 0:
            self.last_error_code = ErrCode.AUTH
            return False, str(payload.get("message") or "登录失败"), False

        self.session.get(
            f"{self.BASE_URL}/user/prelogin.aspx?loginret=http://welearn.sflep.com/user/loginredirect.aspx",
            timeout=DEFAULT_TIMEOUT,
        )
        return True, "登录成功", False

    # ---------------------------------------------------------------- 课程 / 单元

    def get_courses(self) -> Tuple[bool, List, str]:
        try:
            response = self.session.get(
                f"{self.BASE_URL}/ajax/authCourse.aspx?action=gmc",
                headers={"Referer": f"{self.BASE_URL}/2019/student/index.aspx"},
                timeout=DEFAULT_TIMEOUT,
            )
            if response.status_code != 200:
                self.last_error_code = ErrCode.SERVER
                return False, [], f"获取课程失败，状态码: {response.status_code}"
            data = response.json()
            courses = data.get("clist") or []
            if not courses:
                return False, [], "没有找到课程"
            return True, courses, "获取课程成功"
        except Exception as exc:
            self.last_error_code = classify_exception(exc)
            return False, [], f"获取课程列表失败: {exc}"

    def get_course_info(self, cid) -> Tuple[bool, Optional[Dict], str]:
        try:
            response = self.session.get(
                f"{self.BASE_URL}/student/course_info.aspx?cid={cid}", timeout=DEFAULT_TIMEOUT
            )
            if response.status_code != 200:
                self.last_error_code = ErrCode.SERVER
                return False, None, f"获取课程信息失败，状态码: {response.status_code}"

            uid = _extract(response.text, r'"uid":\s*(\d+),')
            classid = _extract(response.text, r'"classid":"(\w+)"')
            if not uid or not classid:
                self.last_error_code = ErrCode.PARSE
                return False, None, "无法解析课程信息（可能是登录状态失效）"

            units_response = self.session.get(
                f"{self.BASE_URL}/ajax/StudyStat.aspx",
                params={"action": "courseunits", "cid": cid, "uid": uid},
                headers={"Referer": f"{self.BASE_URL}/2019/student/course_info.aspx"},
                timeout=DEFAULT_TIMEOUT,
            )
            if units_response.status_code != 200:
                self.last_error_code = ErrCode.SERVER
                return False, None, f"获取单元信息失败，状态码: {units_response.status_code}"

            data = units_response.json()
            if "info" not in data:
                self.last_error_code = ErrCode.PARSE
                return False, None, "单元信息格式错误"
            return True, {"uid": uid, "classid": classid, "units": data["info"]}, "获取单元信息成功"
        except Exception as exc:
            self.last_error_code = classify_exception(exc)
            return False, None, f"获取课程单元失败: {exc}"

    def get_sco_leaves(self, cid, uid, classid, unit_idx) -> Tuple[bool, List, str]:
        try:
            response = self.session.get(
                f"{self.BASE_URL}/ajax/StudyStat.aspx",
                params={
                    "action": "scoLeaves",
                    "cid": cid,
                    "uid": uid,
                    "unitidx": unit_idx,
                    "classid": classid,
                },
                headers={"Referer": f"{self.BASE_URL}/2019/student/course_info.aspx?cid={cid}"},
                timeout=SHORT_TIMEOUT,
            )
            if response.status_code != 200:
                self.last_error_code = ErrCode.SERVER
                return False, [], f"获取课程详情失败，状态码: {response.status_code}"
            return True, response.json().get("info", []), "Success"
        except Exception as exc:
            self.last_error_code = classify_exception(exc)
            return False, [], str(exc)

    # ---------------------------------------------------------------- 提交进度

    def _ajax_sco(self, data: Dict[str, Any], referer: str) -> Optional[str]:
        try:
            response = self.session.post(
                f"{self.BASE_URL}/Ajax/SCO.aspx",
                data=data,
                headers={"Referer": referer},
                timeout=SHORT_TIMEOUT,
            )
            if response.status_code != 200:
                self.last_error_code = ErrCode.SERVER
                return None
            return response.text
        except Exception as exc:
            self.last_error_code = classify_exception(exc)
            logger.debug("SCO 请求失败: %s", exc)
            return None

    @staticmethod
    def _ret_ok(text: Optional[str]) -> bool:
        if not text:
            return False
        try:
            return int(json.loads(text).get("ret", -1)) == 0
        except (ValueError, TypeError, AttributeError):
            return '"ret":0' in text

    @staticmethod
    def build_cmi_payload(accuracy: str) -> str:
        """构造提交给 SCO 接口的 CMI 报文（JSON + ``[INTERACTIONINFO]``）。"""
        payload = {
            "cmi": {
                "completion_status": "completed",
                "interactions": [],
                "launch_data": "",
                "progress_measure": "1",
                "score": {"scaled": str(accuracy), "raw": "100"},
                "session_time": "0",
                "success_status": "unknown",
                "total_time": "0",
                "mode": "normal",
            },
            "adl": {"data": []},
            "cci": {
                "data": [],
                "service": {
                    "dictionary": {"headword": "", "short_cuts": ""},
                    "new_words": [],
                    "notes": [],
                    "writing_marking": [],
                    "record": {"files": []},
                    "play": {"offline_media_id": "9999"},
                },
                "retry_count": "0",
                "submit_time": "",
            },
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "[INTERACTIONINFO]"

    def submit_course_progress(self, cid, uid, classid, scoid, accuracy) -> Tuple[int, int, int, int]:
        referer = f"{self.BASE_URL}/Student/StudyCourse.aspx?cid={cid}&classid={classid}&sco={scoid}"
        common = {"cid": cid, "scoid": scoid, "uid": uid}

        self._ajax_sco({"action": "startsco160928", **common}, referer)

        way1_text = self._ajax_sco(
            {
                "action": "setscoinfo",
                **common,
                "data": self.build_cmi_payload(accuracy),
                "isend": "False",
            },
            referer,
        )
        way1_ok = self._ret_ok(way1_text)

        way2_text = self._ajax_sco(
            {
                "action": "savescoinfo160928",
                **common,
                "progress": "100",
                "crate": accuracy,
                "status": "unknown",
                "cstatus": "completed",
                "trycount": "0",
            },
            referer,
        )
        way2_ok = self._ret_ok(way2_text)

        return (
            1 if way1_ok else 0,
            0 if way1_ok else 1,
            1 if way2_ok else 0,
            0 if way2_ok else 1,
        )

    # ---------------------------------------------------------------- 挂时长

    def simulate_time(self, cid, uid, scoid, learning_time, stop_event=None) -> bool:
        referer = f"{self.BASE_URL}/student/StudyCourse.aspx"
        common = {"uid": uid, "cid": cid, "scoid": scoid}

        if self._ajax_sco({"action": "startsco160928", **common}, referer) is None:
            return False

        started = time.monotonic()
        heartbeats = 0
        while True:
            elapsed = time.monotonic() - started
            remaining = learning_time - elapsed
            if remaining <= 0:
                break
            if stop_event is not None:
                if stop_event.wait(min(1.0, remaining)):
                    logger.info("收到停止信号，中断挂时长")
                    return False
            else:
                time.sleep(min(1.0, remaining))
            elapsed = time.monotonic() - started
            due = int(elapsed // HEARTBEAT_SECONDS)
            if due > heartbeats and elapsed < learning_time:
                heartbeats = due
                self._ajax_sco({"action": "keepsco_with_getticket_with_updatecmitime", **common}, referer)

        text = self._ajax_sco(
            {
                "action": "savescoinfo160928",
                **common,
                "progress": "100",
                "crate": "0",
                "status": "unknown",
                "cstatus": "completed",
                "trycount": "0",
            },
            referer,
        )
        return self._ret_ok(text)


def _extract(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else ""


__all__ = [
    "PLATFORM",
    "WeLearnClient",
    "parse_prelogin_url",
    "classify_exception",
    "DEFAULT_TIMEOUT",
    "MAX_LOGIN_RETRY",
]