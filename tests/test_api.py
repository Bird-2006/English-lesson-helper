import json

import pytest

from core.api import WeLearnClient, classify_exception, parse_prelogin_url


def test_parse_prelogin_url_extracts_parameters():
    url = (
        "https://welearn.sflep.com/connect/authorize?client_id=welearn_web"
        "&code_challenge=ABC123&code_challenge_method=S256&state=STATE456"
    )
    challenge, state = parse_prelogin_url(url)
    assert challenge == "ABC123"
    assert state == "STATE456"


def test_parse_prelogin_url_handles_percent_encoding():
    url = (
        "https://welearn.sflep.com/connect/authorize?"
        "code_challenge=Ab-Cd_1&code_challenge_method=S256&state=xy%2Fz"
    )
    challenge, state = parse_prelogin_url(url)
    assert challenge == "Ab-Cd_1"
    assert state == "xy/z"


def test_parse_prelogin_url_rejects_missing_parameters():
    with pytest.raises(ValueError):
        parse_prelogin_url("https://welearn.sflep.com/user/prelogin.aspx")


def test_cmi_payload_is_json_plus_interactioninfo():
    payload = WeLearnClient.build_cmi_payload("85")
    body, _, tail = payload.partition("[INTERACTIONINFO]")
    assert tail == ""
    parsed = json.loads(body)
    assert parsed["cmi"]["score"]["scaled"] == "85"
    assert parsed["cmi"]["completion_status"] == "completed"
    assert parsed["cci"]["service"]["play"]["offline_media_id"] == "9999"


def test_ret_ok_accepts_json_and_legacy_text():
    client = WeLearnClient()
    assert client._ret_ok('{"ret":0,"msg":"ok"}') is True
    assert client._ret_ok('{"ret":1}') is False
    assert client._ret_ok('{"ret":0') is True  # 非严格 JSON 时退回子串判断
    assert client._ret_ok(None) is False


def test_classify_exception_maps_common_errors():
    import requests

    assert classify_exception(requests.exceptions.Timeout()) == "timeout"
    assert classify_exception(requests.exceptions.ConnectionError()) == "network"
    assert classify_exception(ValueError("bad")) == "parse"
    assert classify_exception(RuntimeError("boom")) == "unknown"