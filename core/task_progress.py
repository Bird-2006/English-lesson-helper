"""任务进度管理器：保存与恢复「刷作业 / 刷时长」的断点。

旧实现的问题与改法：

- 文件路径是相对路径 ``task_progress.json``，从别的目录启动就会写到别处
  并表现为「进度丢失」→ 锚定 ``core.paths.data_path``；
- 直接 ``open(path, "w")`` 覆写，崩溃即损坏 → 原子替换；
- 多开详情窗并发读写同一文件没有互斥 → 加类级 ``RLock``；
- 任务只增不删（``mark_task_completed`` 从未被调用），启动弹窗会列出大量
  过期任务 → 补上完成标记与过期清理。
"""
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from core.atomic import write_text_atomic
from core.logger import get_logger
from core.paths import data_path

logger = get_logger("TaskProgress")

DATA_FILE_NAME = "task_progress.json"
STATUS_PAUSED = "paused"
STATUS_COMPLETED = "completed"
DEFAULT_MAX_AGE_DAYS = 7


class TaskProgress:
    """任务进度管理类。"""

    _lock = threading.RLock()

    def __init__(self, progress_file: str = None):
        self.progress_file = progress_file or data_path(DATA_FILE_NAME)
        self.progress_data: Dict[str, Dict[str, Any]] = self._load_progress()

    # ---------------------------------------------------------------- 读写

    def _load_progress(self) -> Dict[str, Dict[str, Any]]:
        with TaskProgress._lock:
            if not os.path.exists(self.progress_file):
                return {}
            try:
                with open(self.progress_file, "r", encoding="utf-8") as stream:
                    data = json.load(stream)
                return data if isinstance(data, dict) else {}
            except Exception as exc:
                logger.error("加载进度文件失败: %s", exc)
                return {}

    def _save_progress(self) -> bool:
        with TaskProgress._lock:
            try:
                write_text_atomic(
                    self.progress_file,
                    json.dumps(self.progress_data, ensure_ascii=False, indent=2),
                    prefix=".tmp_progress_",
                )
                return True
            except Exception as exc:
                logger.error("保存进度文件失败: %s", exc)
                return False

    def reload(self) -> None:
        """从磁盘重新读取（多窗口共享同一文件时用）。"""
        self.progress_data = self._load_progress()

    # ---------------------------------------------------------------- 写入

    def save_task_progress(
        self,
        task_id: str,
        task_type: str,
        cid,
        uid: str,
        classid: str,
        unit_indices: List[int],
        current_units: List[Dict],
        completed_units: List[int],
        completed_courses: Dict[int, List[str]],
        task_config: Dict[str, Any],
    ) -> bool:
        self.progress_data[task_id] = {
            "task_type": task_type,
            "cid": cid,
            "uid": str(uid),
            "classid": str(classid),
            "unit_indices": list(unit_indices),
            "current_units": list(current_units),
            "completed_units": list(completed_units),
            "completed_courses": completed_courses or {},
            "task_config": task_config or {},
            "last_update_time": time.time(),
            "status": STATUS_PAUSED,
        }
        return self._save_progress()

    def mark_task_completed(self, task_id: str) -> bool:
        if task_id not in self.progress_data:
            return False
        self.progress_data[task_id]["status"] = STATUS_COMPLETED
        self.progress_data[task_id]["completion_time"] = time.time()
        return self._save_progress()

    def clear_task_progress(self, task_id: str) -> bool:
        if task_id in self.progress_data:
            del self.progress_data[task_id]
            return self._save_progress()
        return False

    def prune(self, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> int:
        """清理已完成或过期的任务记录，返回删除条数。"""
        deadline = time.time() - max_age_days * 86400
        stale = [
            task_id
            for task_id, task in self.progress_data.items()
            if task.get("status") == STATUS_COMPLETED
            or task.get("last_update_time", 0) < deadline
        ]
        if not stale:
            return 0
        for task_id in stale:
            del self.progress_data[task_id]
        self._save_progress()
        logger.info("已清理 %d 条过期/已完成的任务记录", len(stale))
        return stale.__len__()

    # ---------------------------------------------------------------- 读取

    def get_incomplete_tasks(self) -> List[Dict]:
        incomplete = []
        for task_id, task in self.progress_data.items():
            if task.get("status") != STATUS_PAUSED:
                continue
            last_update = task.get("last_update_time", 0)
            if last_update:
                last_update_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_update))
            else:
                last_update_str = "未知"
            incomplete.append(
                {"task_id": task_id, "last_update_time_str": last_update_str, **task}
            )
        return incomplete

    def find_incomplete_by_uid(self, uid: str) -> Optional[Dict]:
        """按 WeLearn 数字 uid 找该账号的未完成任务。"""
        uid = str(uid or "")
        if not uid:
            return None
        for task in self.get_incomplete_tasks():
            if str(task.get("uid", "")) == uid:
                return task
        return None

    def get_task_progress(self, task_id: str) -> Optional[Dict]:
        return self.progress_data.get(task_id)

    @staticmethod
    def generate_task_id(cid, uid: str, task_type: str) -> str:
        return f"{task_type}_{cid}_{uid}_{int(time.time())}"


__all__ = [
    "TaskProgress",
    "DATA_FILE_NAME",
    "STATUS_PAUSED",
    "STATUS_COMPLETED",
]