import pytest

pytest.importorskip("PyQt5")

from ui.workers import MAX_WORKERS, StudyThread, TimeStudyThread, format_duration


def _duration_thread(total_minutes: int, random_range: int, concurrent: int = 4) -> TimeStudyThread:
    return TimeStudyThread(
        client=None,
        cid=1,
        uid="2",
        classid="3",
        unit_idx=[0],
        total_minutes=total_minutes,
        random_range=random_range,
        current_units=[],
        max_concurrent=concurrent,
    )


def test_duration_split_sums_to_total_exactly():
    thread = _duration_thread(180, 0)
    actual_minutes, _ = thread.calculate_unit_time(0, 7)
    seconds = thread.unit_course_times[0]["seconds_by_index"]

    assert actual_minutes == 180
    assert len(seconds) == 7
    assert sum(seconds) == 180 * 60
    assert max(seconds) - min(seconds) <= 1


def test_duration_split_handles_single_course():
    thread = _duration_thread(30, 0)
    thread.calculate_unit_time(0, 1)
    assert thread.unit_course_times[0]["seconds_by_index"] == [30 * 60]


def test_random_range_keeps_minutes_positive():
    thread = _duration_thread(1, 30)
    for _ in range(50):
        actual_minutes, _ = thread.calculate_unit_time(0, 3)
        assert actual_minutes >= 1


def test_concurrency_is_clamped():
    thread = _duration_thread(10, 0, concurrent=9999)
    assert thread.max_concurrent == MAX_WORKERS


def test_format_duration():
    assert format_duration(0) == "0秒"
    assert format_duration(45) == "45秒"
    assert format_duration(90) == "1分30秒"
    assert format_duration(120) == "2分钟"
    assert format_duration(3600) == "1小时"
    assert format_duration(3661) == "1小时1分1秒"


def test_study_thread_stop_is_cooperative():
    thread = StudyThread(
        client=None,
        cid=1,
        uid="2",
        classid="3",
        unit_idx=[0],
        accuracy_config=90,
        current_units=[],
        max_concurrent=9999,
    )
    assert thread.stop_requested is False
    thread.stop()
    assert thread.stop_requested is True
    assert thread.max_concurrent == MAX_WORKERS


def test_accuracy_pick_respects_range():
    thread = StudyThread(
        client=None,
        cid=1,
        uid="2",
        classid="3",
        unit_idx=[0],
        accuracy_config=(60, 60),
        max_concurrent=2,
    )
    assert thread._pick_accuracy() == "60"

    single = StudyThread(
        client=None,
        cid=1,
        uid="2",
        classid="3",
        unit_idx=[0],
        accuracy_config=85,
        max_concurrent=2,
    )
    assert single._pick_accuracy() == "85"