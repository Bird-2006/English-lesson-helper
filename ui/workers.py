"""后台工作线程：登录、取课程、取单元、刷作业、刷时长。

改动要点：

- 停止方式改为协作式：置 ``threading.Event`` 后，业务在每次网络调用之间
  与 ``simulate_time`` 的等待里检查它，能在 1 秒内收工。旧实现直接
  ``QThread.terminate()`` 硬杀线程，池线程、requests 连接、文件句柄都不
  保证释放，也是「关不掉/退不干净」的根源；
- 时长分配改为「均分 + 余数补齐」，各课程秒数之和严格等于单元总秒数
  （旧实现整除后最多丢掉 n-1 秒）；
- 刷时长的结果字典补上 ``completed_units`` / ``total_units``，界面不再
  永远显示「已完成 0/N」；
- 进度事件名改为 ``ProgressEvent`` 常量，拼错会立刻报错而不是静默失效。
"""
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QThread, pyqtSignal

from core.api import WeLearnClient
from core.constants import ProgressEvent
from core.logger import get_logger

MAX_RETRY_ROUNDS = 3
MAX_WORKERS = 32


def format_duration(seconds: int) -> str:
    """把秒数格式化成「x小时y分z秒」。"""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒" if minutes or secs else f"{hours}小时"
    if minutes:
        return f"{minutes}分{secs}秒" if secs else f"{minutes}分钟"
    return f"{secs}秒"


class _BaseThread(QThread):
    """带日志器与协作式停止标志的线程基类。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logger = get_logger(type(self).__name__)
        self._stop_event = threading.Event()

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def request_stop(self) -> None:
        self._stop_event.set()

    def stop(self) -> None:
        """兼容旧调用：只发信号，不硬杀线程。"""
        self.request_stop()


class LoginThread(_BaseThread):
    login_result = pyqtSignal(bool, str)

    def __init__(self, client: WeLearnClient, username: str, password: str):
        super().__init__()
        self.client = client
        self.username = username
        self.password = password

    def run(self):
        success, message = self.client.login(self.username, self.password)
        self.login_result.emit(success, message)


class CourseThread(_BaseThread):
    course_result = pyqtSignal(bool, list, str)

    def __init__(self, client: WeLearnClient):
        super().__init__()
        self.client = client

    def run(self):
        success, courses, message = self.client.get_courses()
        self.course_result.emit(success, courses, message)


class UnitsThread(_BaseThread):
    units_result = pyqtSignal(bool, list, str)

    def __init__(self, client: WeLearnClient, cid):
        super().__init__()
        self.client = client
        self.cid = cid

    def run(self):
        success, data, message = self.client.get_course_info(self.cid)
        if success:
            self.units_result.emit(True, [data], message)
        else:
            self.units_result.emit(False, [], message)


class StudyThread(_BaseThread):
    """刷作业：把选中的单元里未完成的课程标记为已完成。"""

    progress_update = pyqtSignal(str, str)
    study_finished = pyqtSignal(dict)

    def __init__(
        self,
        client: WeLearnClient,
        cid,
        uid,
        classid,
        unit_idx,
        accuracy_config,
        current_units=None,
        max_concurrent: int = 5,
    ):
        super().__init__()
        self.client = client
        self.cid = cid
        self.uid = uid
        self.classid = classid
        self.unit_idx = unit_idx
        self.accuracy_config = accuracy_config
        self.current_units = current_units or []
        self.max_concurrent = max(1, min(int(max_concurrent), MAX_WORKERS))

    # ------------------------------------------------------------ 单门课

    def _pick_accuracy(self) -> str:
        if isinstance(self.accuracy_config, (tuple, list)) and len(self.accuracy_config) == 2:
            low, high = sorted((int(self.accuracy_config[0]), int(self.accuracy_config[1])))
            return str(random.randint(low, high))
        return str(self.accuracy_config)

    def process_single_course(self, course: Dict) -> Tuple[int, int, int, int]:
        location = course.get("location", "未知课程")
        if self.stop_requested:
            return 0, 0, 0, 0
        if course.get("isvisible") == "false":
            self.progress_update.emit(ProgressEvent.SKIP, f"[跳过] {location}")
            return 0, 0, 0, 0
        if "未" not in course.get("iscomplete", ""):
            self.progress_update.emit(ProgressEvent.COMPLETED, f"[已完成] {location}")
            return 0, 0, 0, 0

        accuracy = self._pick_accuracy()
        self.progress_update.emit(ProgressEvent.START, f"[进行] {location} - 正确率 {accuracy}%")
        result = self.client.submit_course_progress(
            self.cid, self.uid, self.classid, course["id"], accuracy
        )
        way1_ok, way1_fail, way2_ok, way2_fail = result
        suffix = f"(步骤1:{'成功' if way1_ok else '失败'} 步骤2:{'成功' if way2_ok else '失败'})"
        self.progress_update.emit(ProgressEvent.FINISH, f"[完成] {location} - {accuracy}% {suffix}")
        self.logger.info("课程处理完成 %s 正确率=%s 结果=%s", location, accuracy, result)
        return result

    # ------------------------------------------------------------ 单元

    def process_unit(self, unit_index: int) -> Tuple[int, int, int, int, List[Dict]]:
        """处理一个单元，返回计数与失败课程列表。"""
        totals = [0, 0, 0, 0]
        success, leaves, message = self.client.get_sco_leaves(
            self.cid, self.uid, self.classid, unit_index
        )
        if not success:
            self.logger.error("获取单元 %s 详情失败: %s", unit_index + 1, message)
            self.progress_update.emit(ProgressEvent.ERROR, f"获取单元详情失败: {message}")
            return 0, 0, 0, 0, []

        pending = [
            course
            for course in leaves
            if course.get("isvisible") != "false" and "未" in course.get("iscomplete", "")
        ]
        skipped = len(leaves) - len(pending)
        self.logger.info(
            "单元 %s: 共 %d 门课，待处理 %d，跳过 %d",
            unit_index + 1,
            len(leaves),
            len(pending),
            skipped,
        )
        if not pending:
            return 0, 0, 0, 0, []

        failed: List[Dict] = []
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            futures = {executor.submit(self.process_single_course, course): course for course in pending}
            for future in as_completed(futures):
                if self.stop_requested:
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    way1_ok, way1_fail, way2_ok, way2_fail = future.result()
                except Exception as exc:
                    self.logger.error("处理课程异常: %s", exc)
                    failed.append(futures[future])
                    continue
                totals[0] += way1_ok
                totals[1] += way1_fail
                totals[2] += way2_ok
                totals[3] += way2_fail
                if way1_fail:
                    failed.append(futures[future])
        return totals[0], totals[1], totals[2], totals[3], failed

    # ------------------------------------------------------------ 主流程

    def run(self):
        units = self.unit_idx if isinstance(self.unit_idx, (list, tuple)) else [self.unit_idx]
        self.logger.info("刷作业开始 cid=%s uid=%s 单元=%s 并发=%d", self.cid, self.uid, units, self.max_concurrent)

        total = [0, 0, 0, 0]
        failed: List[Dict] = []
        retry_round = 0

        try:
            self.progress_update.emit(ProgressEvent.PROGRESS_TOTAL, str(len(units)))
            for index, unit_index in enumerate(units):
                if self.stop_requested:
                    break
                self.progress_update.emit(
                    ProgressEvent.UNIT_START, f"\n=== 开始处理第 {unit_index + 1} 单元 ==="
                )
                counts = self.process_unit(unit_index)
                for i in range(4):
                    total[i] += counts[i]
                failed.extend(counts[4])
                self.progress_update.emit(
                    ProgressEvent.UNIT_FINISH, f"=== 第 {unit_index + 1} 单元处理完成 ===\n"
                )
                self.progress_update.emit(ProgressEvent.PROGRESS_CURRENT, str(index + 1))

            # 只重试确实失败的课程，不再整单元重扫
            while failed and retry_round < MAX_RETRY_ROUNDS and not self.stop_requested:
                retry_round += 1
                self.progress_update.emit(
                    ProgressEvent.INFO, f"\n=== 检测到 {len(failed)} 个失败项，第 {retry_round} 次重试 ==="
                )
                self.logger.info("第 %d 次重试，失败项 %d", retry_round, len(failed))
                still_failed: List[Dict] = []
                with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
                    futures = {
                        executor.submit(self.process_single_course, course): course for course in failed
                    }
                    for future in as_completed(futures):
                        if self.stop_requested:
                            executor.shutdown(wait=False, cancel_futures=True)
                            break
                        try:
                            way1_ok, way1_fail, way2_ok, way2_fail = future.result()
                        except Exception as exc:
                            self.logger.error("重试异常: %s", exc)
                            still_failed.append(futures[future])
                            continue
                        if way1_ok:
                            total[0] += 1
                            total[1] = max(0, total[1] - 1)
                        else:
                            still_failed.append(futures[future])
                        total[2] += way2_ok
                        total[3] += way2_fail
                failed = still_failed

            if self.stop_requested:
                self.progress_update.emit(ProgressEvent.INFO, "任务已停止，剩余课程未处理")

            result = {
                "way1_succeed": total[0],
                "way1_failed": total[1],
                "way2_succeed": total[2],
                "way2_failed": total[3],
                "retry_count": retry_round,
            }
            self.logger.info("刷作业结束 %s", result)
            self.study_finished.emit(result)
        except Exception as exc:
            self.logger.exception("刷作业过程中发生错误")
            self.progress_update.emit(ProgressEvent.ERROR, f"学习过程中发生错误: {exc}")


class TimeStudyThread(_BaseThread):
    """刷时长：把单元时长均分到该单元的每门课，跨单元并发。"""

    progress_update = pyqtSignal(str, str)
    study_finished = pyqtSignal(dict)

    def __init__(
        self,
        client: WeLearnClient,
        cid,
        uid,
        classid,
        unit_idx,
        total_minutes,
        random_range,
        current_units=None,
        max_concurrent: int = 10,
    ):
        super().__init__()
        self.client = client
        self.cid = cid
        self.uid = uid
        self.classid = classid
        self.unit_idx = list(unit_idx) if isinstance(unit_idx, (list, tuple)) else [unit_idx]
        self.total_minutes = int(total_minutes)
        self.random_range = int(random_range)
        self.current_units = current_units or []
        self.max_concurrent = max(1, min(int(max_concurrent), MAX_WORKERS))
        self.unit_course_times: Dict[int, Dict[str, int]] = {}

    # ------------------------------------------------------------ 时长分配

    def calculate_unit_time(self, unit_index: int, course_count: int) -> Tuple[int, int]:
        """计算单元实际分钟数与每课程秒数（余数补齐，保证总和不丢秒）。"""
        actual_minutes = max(1, self.total_minutes + random.randint(-self.random_range, self.random_range))
        total_seconds = actual_minutes * 60
        count = max(1, course_count)
        base, remainder = divmod(total_seconds, count)
        seconds_by_index = [base + (1 if i < remainder else 0) for i in range(count)]
        self.unit_course_times[unit_index] = {
            "actual_minutes": actual_minutes,
            "seconds_by_index": seconds_by_index,
        }
        return actual_minutes, seconds_by_index[0]

    # ------------------------------------------------------------ 单门课

    def study_single_course(self, course_data) -> Tuple[bool, Optional[str]]:
        unit_index, chapter, seconds = course_data
        location = chapter.get("location", "未知课程")
        course_id = chapter["id"]
        if self.stop_requested:
            return False, None

        self.progress_update.emit(
            ProgressEvent.START, f"[单元{unit_index + 1}] {location} - {format_duration(seconds)}"
        )
        ok = self.client.simulate_time(
            self.cid, self.uid, course_id, seconds, stop_event=self._stop_event
        )
        if ok:
            self.progress_update.emit(
                ProgressEvent.FINISH, f"[完成] 单元{unit_index + 1} - {location}"
            )
            return True, course_id
        if self.stop_requested:
            return False, None
        self.progress_update.emit(ProgressEvent.ERROR, f"[失败] 单元{unit_index + 1} - {location}")
        return False, None

    # ------------------------------------------------------------ 准备

    def _prepare_all_courses(self) -> List[Tuple[int, Dict, int]]:
        all_courses: List[Tuple[int, Dict, int]] = []
        for unit_index in self.unit_idx:
            if self.stop_requested:
                break
            success, leaves, message = self.client.get_sco_leaves(
                self.cid, self.uid, self.classid, unit_index
            )
            if not success:
                self.logger.error("单元 %s 获取失败: %s", unit_index + 1, message)
                self.progress_update.emit(ProgressEvent.ERROR, f"单元 {unit_index + 1}: 获取失败 - {message}")
                continue
            visible = [ch for ch in leaves if ch.get("isvisible") != "false"]
            if not visible:
                self.progress_update.emit(ProgressEvent.SKIP, f"单元 {unit_index + 1}: 没有可刷的课程")
                continue

            actual_minutes, _ = self.calculate_unit_time(unit_index, len(visible))
            seconds_list = self.unit_course_times[unit_index]["seconds_by_index"]
            self.progress_update.emit(
                ProgressEvent.INFO,
                f"单元 {unit_index + 1}: {len(visible)} 门课，单元总时长 {format_duration(actual_minutes * 60)}",
            )
            for position, chapter in enumerate(visible):
                all_courses.append((unit_index, chapter, seconds_list[position]))
        return all_courses

    # ------------------------------------------------------------ 主流程

    def _run_batch(self, courses, executor) -> Tuple[int, int, List]:
        success_count = 0
        fail_count = 0
        failed: List = []
        futures = {executor.submit(self.study_single_course, item): item for item in courses}
        completed = 0
        for future in as_completed(futures):
            if self.stop_requested:
                executor.shutdown(wait=False, cancel_futures=True)
                break
            try:
                ok, _ = future.result()
            except Exception as exc:
                self.logger.error("处理课程异常: %s", exc)
                ok = False
            completed += 1
            if ok:
                success_count += 1
            else:
                if not self.stop_requested:
                    fail_count += 1
                    failed.append(futures[future])
            percent = int(completed / max(1, len(courses)) * 100)
            self.progress_update.emit(
                ProgressEvent.PROGRESS, f"进度: {completed}/{len(courses)} ({percent}%)"
            )
            self.progress_update.emit(ProgressEvent.PROGRESS_CURRENT, str(completed))
        return success_count, fail_count, failed

    def run(self):
        self.logger.info(
            "刷时长开始 cid=%s uid=%s 每单元 %d 分钟 ±%d 并发=%d",
            self.cid,
            self.uid,
            self.total_minutes,
            self.random_range,
            self.max_concurrent,
        )
        total_success = 0
        total_fail = 0
        retry_round = 0

        try:
            all_courses = self._prepare_all_courses()
            total_units = len(all_courses)
            if not all_courses:
                self.progress_update.emit(ProgressEvent.INFO, "没有找到可刷的课程")
                self.study_finished.emit(
                    {
                        "way1_succeed": 0,
                        "way1_failed": 0,
                        "way2_succeed": 0,
                        "way2_failed": 0,
                        "retry_count": 0,
                        "completed_units": 0,
                        "total_units": 0,
                    }
                )
                return

            self.progress_update.emit(
                ProgressEvent.INFO, f"总共发现 {total_units} 门课程，开始并发处理..."
            )
            self.progress_update.emit(ProgressEvent.PROGRESS_TOTAL, str(total_units))

            with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
                ok_count, fail_count, failed = self._run_batch(all_courses, executor)
            total_success += ok_count
            total_fail += fail_count

            while failed and retry_round < MAX_RETRY_ROUNDS and not self.stop_requested:
                retry_round += 1
                self.progress_update.emit(
                    ProgressEvent.INFO,
                    f"\n=== 检测到 {len(failed)} 个失败项，开始第 {retry_round} 次重试 ===",
                )
                with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
                    ok_count, _, failed = self._run_batch(failed, executor)
                total_success += ok_count
                total_fail = max(0, total_fail - ok_count)

            if self.stop_requested:
                self.progress_update.emit(ProgressEvent.INFO, "任务已停止，未完成的课程下次可继续")

            result = {
                "way1_succeed": total_success,
                "way1_failed": total_fail,
                "way2_succeed": total_success,
                "way2_failed": total_fail,
                "retry_count": retry_round,
                "completed_units": total_success,
                "total_units": total_units,
            }
            self.logger.info("刷时长结束 %s", result)
            self.study_finished.emit(result)
        except Exception as exc:
            self.logger.exception("刷时长过程中发生错误")
            self.progress_update.emit(ProgressEvent.ERROR, f"刷时长过程中发生错误: {exc}")


class LaunchClientThread(_BaseThread):
    """启动本地客户端 / 浏览器，或抓接口（iSmart），耗时操作放后台避免卡界面。

    ``mode``:
      - ``"client"``：定位并带调试端口启动桌面客户端；
      - ``"browser"``：用本机 Edge/Chrome 带调试端口打开 ``url``；
      - ``"capture"``：抓 ``seconds`` 秒网络请求，弄清业务接口。
    """

    result_ready = pyqtSignal(bool, str)

    def __init__(self, client, port: int = None, mode: str = "client", url: str = None, seconds: float = 60.0):
        super().__init__()
        self.client = client
        self.port = port
        self.mode = mode
        self.url = url
        self.seconds = seconds

    def run(self):
        if self.mode == "capture":
            self._run_capture()
            return

        method_name = "open_in_browser" if self.mode == "browser" else "launch_and_connect"
        launcher = getattr(self.client, method_name, None)
        if launcher is None:
            self.result_ready.emit(False, "当前平台不支持该启动方式")
            return
        try:
            ok, message = launcher(self.url) if self.mode == "browser" else launcher(self.port)
        except Exception as exc:
            self.logger.exception("启动失败（%s）", self.mode)
            ok, message = False, f"启动失败: {exc}"
        self.result_ready.emit(ok, message)

    def _run_capture(self):
        capture = getattr(self.client, "capture_network", None)
        if capture is None:
            self.result_ready.emit(False, "当前平台不支持抓接口")
            return
        try:
            result = capture(self.seconds, should_stop=lambda: self.stop_requested)
            requests_seen = result.get("requests") or []
            lines = [f"抓到 {len(requests_seen)} 个请求"]
            for item in requests_seen[:8]:
                lines.append(f"{item.get('method')} {item.get('url')}")
            if result.get("saved_to"):
                lines.append(f"已保存：{result['saved_to']}")
            self.result_ready.emit(True, "\n".join(lines))
        except Exception as exc:
            self.logger.exception("抓接口失败")
            self.result_ready.emit(False, f"抓接口失败: {exc}")


__all__ = [
    "LoginThread",
    "CourseThread",
    "UnitsThread",
    "StudyThread",
    "TimeStudyThread",
    "LaunchClientThread",
    "format_duration",
    "MAX_RETRY_ROUNDS",
]