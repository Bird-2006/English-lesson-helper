import json
import time

from core.task_progress import TaskProgress


def _make(tmp_path) -> TaskProgress:
    return TaskProgress(progress_file=str(tmp_path / "task_progress.json"))


def _save(progress: TaskProgress, task_id: str = "刷时长_1_2_3") -> None:
    progress.save_task_progress(
        task_id=task_id,
        task_type="刷时长",
        cid=1,
        uid="2",
        classid="3",
        unit_indices=[0, 1],
        current_units=[{"name": "Unit 1"}],
        completed_units=[],
        completed_courses={},
        task_config={"course_name": "测试课程", "total_minutes": 60},
    )


def test_save_and_reload(tmp_path):
    progress = _make(tmp_path)
    _save(progress)

    reloaded = _make(tmp_path)
    tasks = reloaded.get_incomplete_tasks()
    assert len(tasks) == 1
    assert tasks[0]["task_type"] == "刷时长"
    assert tasks[0]["task_config"]["course_name"] == "测试课程"


def test_file_is_always_valid_json(tmp_path):
    progress = _make(tmp_path)
    _save(progress)
    with open(progress.progress_file, encoding="utf-8") as stream:
        json.load(stream)


def test_find_incomplete_by_uid(tmp_path):
    progress = _make(tmp_path)
    _save(progress, "a")
    _save(progress, "b")
    task = progress.find_incomplete_by_uid("2")
    assert task is not None
    assert task["uid"] == "2"
    assert progress.find_incomplete_by_uid("999") is None


def test_mark_completed_hides_from_incomplete(tmp_path):
    progress = _make(tmp_path)
    _save(progress)
    assert progress.mark_task_completed("刷时长_1_2_3") is True
    assert progress.get_incomplete_tasks() == []
    assert progress.get_task_progress("刷时长_1_2_3")["status"] == "completed"


def test_clear_task_progress(tmp_path):
    progress = _make(tmp_path)
    _save(progress)
    assert progress.clear_task_progress("刷时长_1_2_3") is True
    assert _make(tmp_path).get_incomplete_tasks() == []


def test_prune_removes_completed_and_stale(tmp_path):
    progress = _make(tmp_path)
    _save(progress, "fresh")
    _save(progress, "old")
    progress.mark_task_completed("fresh")
    progress.progress_data["old"]["last_update_time"] = time.time() - 30 * 86400

    removed = progress.prune(max_age_days=7)
    assert removed == 2
    assert _make(tmp_path).get_incomplete_tasks() == []


def test_generate_task_id_shape():
    task_id = TaskProgress.generate_task_id(123, "10000001", "刷作业")
    parts = task_id.split("_")
    assert parts[0] == "刷作业"
    assert parts[1] == "123"
    assert parts[2] == "10000001"
    assert parts[3].isdigit()