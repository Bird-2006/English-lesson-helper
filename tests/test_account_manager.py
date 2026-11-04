import json
import os

from core import secure_store
from core.account_manager import Account, AccountManager


def _manager(tmp_path) -> AccountManager:
    return AccountManager(data_file=str(tmp_path / "accounts.json"))


def test_add_and_reload(tmp_path):
    manager = _manager(tmp_path)
    assert manager.add_account("user1", "pass1", "昵称") is True
    assert manager.get_account_count() == 1

    reloaded = _manager(tmp_path)
    account = reloaded.get_account("user1")
    assert account is not None
    assert account.password == "pass1"
    assert account.nickname == "昵称"


def test_duplicate_account_is_rejected(tmp_path):
    manager = _manager(tmp_path)
    manager.add_account("user1", "pass1")
    assert manager.add_account("user1", "pass2") is False
    assert manager.get_account("user1").password == "pass1"


def test_password_on_disk_is_protected(tmp_path):
    manager = _manager(tmp_path)
    manager.add_account("user1", "pass1")

    with open(os.path.join(tmp_path, "accounts.json"), encoding="utf-8") as stream:
        raw = stream.read()
    if secure_store.is_available():
        assert "pass1" not in raw
        assert secure_store.PREFIX in raw
    data = json.loads(raw)
    assert data["accounts"][0]["username"] == "user1"


def test_update_password_and_nickname(tmp_path):
    manager = _manager(tmp_path)
    manager.add_account("user1", "pass1")
    assert manager.update_account_password("user1", "pass2") is True
    assert manager.update_account_nickname("user1", "新昵称") is True

    reloaded = _manager(tmp_path)
    account = reloaded.get_account("user1")
    assert account.password == "pass2"
    assert account.nickname == "新昵称"


def test_update_status_without_save(tmp_path):
    manager = _manager(tmp_path)
    manager.add_account("user1", "pass1")
    manager.update_status("user1", "运行中", "progress", save=False)
    assert manager.get_account("user1").status == "运行中"
    assert _manager(tmp_path).get_account("user1").status == "待处理"

    manager.save_accounts()
    assert _manager(tmp_path).get_account("user1").status == "运行中"


def test_remove_and_clear(tmp_path):
    manager = _manager(tmp_path)
    manager.add_account("user1", "pass1")
    manager.add_account("user2", "pass2")
    assert manager.remove_account("user1") is True
    assert manager.remove_account("missing") is False
    manager.clear_accounts()
    assert _manager(tmp_path).get_account_count() == 0


def test_load_tolerates_legacy_and_unknown_fields(tmp_path):
    path = os.path.join(tmp_path, "accounts.json")
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(
            {"accounts": [{"username": "u", "password": "p", "legacy_field": 1}]},
            stream,
        )
    manager = AccountManager(data_file=path)
    assert manager.get_account("u").password == "p"


def test_load_broken_file_does_not_raise(tmp_path):
    path = os.path.join(tmp_path, "accounts.json")
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("{not json")
    manager = AccountManager(data_file=path)
    assert manager.get_account_count() == 0


def test_import_and_export_roundtrip(tmp_path):
    manager = _manager(tmp_path)
    source = tmp_path / "in.csv"
    source.write_text("用户名,密码,昵称,平台\nu1,p1,昵称1,\nu2,p2,,iSmart\n", encoding="utf-8")
    count, error = manager.import_from_file(str(source))
    assert (count, error) == (2, "")
    assert manager.get_account("u1").platform == "welearn"
    assert manager.get_account("u2").platform == "ismart"

    target = tmp_path / "out.csv"
    ok, error = manager.export_to_file(str(target))
    assert ok and error == ""
    content = target.read_text(encoding="utf-8-sig")
    assert "u1" in content and "p1" in content
    assert "平台" in content and "ismart" in content

    ok, error = manager.export_to_file(str(tmp_path / "out.unknown"))
    assert ok is False and "不支持" in error


def test_platform_defaults_and_normalization(tmp_path):
    manager = _manager(tmp_path)
    manager.add_account("a", "p")
    assert manager.get_account("a").platform == "welearn"

    manager.add_account("b", "p", "", "ismart")
    assert manager.get_account("b").platform == "ismart"

    assert AccountManager.normalize_platform("iSmart") == "ismart"
    assert AccountManager.normalize_platform("WeLearn") == "welearn"
    assert AccountManager.normalize_platform("") == "welearn"
    assert AccountManager.normalize_platform(None) == "welearn"

    reloaded = _manager(tmp_path)
    assert reloaded.get_account("b").platform == "ismart"


def test_account_from_dict_ignores_extra_keys():
    account = Account.from_dict({"username": "u", "password": "p", "extra": "x"})
    assert account.username == "u"
    assert account.uid == ""
    assert account.platform == "welearn"