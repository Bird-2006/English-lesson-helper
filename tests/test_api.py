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


def test_parse_prelogin_url_keeps_raw_encoding():
    """返回值是原始片段：state 里的 %2F 不解码，因为要原样拼进 rturl。"""
    url = (
        "https://welearn.sflep.com/connect/authorize?"
        "code_challenge=Ab-Cd_1&code_challenge_method=S256&state=xy%2Fz"
    )
    challenge, state = parse_prelogin_url(url)
    assert challenge == "Ab-Cd_1"
    assert state == "xy%2Fz"


# 线上真实形态（参数嵌在 returnUrl 里，二次编码；值已脱敏）
NESTED_URL = (
    "https://sso.sflep.com/idsvr/transfer.html?returnUrl=%2Fconnect%2Fauthorize%2Fcallback"
    "%3Fclient_id%3Dwelearn_web"
    "%26redirect_uri%3Dhttps%253A%252F%252Fwelearn.sflep.com%252Fsignin-sflep"
    "%26response_type%3Dcode"
    "%26scope%3Dopenid%2520profile%2520email%2520phone%2520address"
    "%26code_challenge%3DTn6RQt-EXAMPLEchallenge_-mLOoTibKZc"
    "%26code_challenge_method%3DS256"
    "%26state%3DOpenIdConnect.AuthenticationProperties%253DEXAMPLEstateValue"
    "%26x-client-SKU%3DID_NET472"
    "%26x-client-ver%3D6.32.1.0"
)


def test_parse_prelogin_url_handles_nested_encoded_url():
    """回归测试：真实返回的 transfer.html?returnUrl=... 形态必须能解析。"""
    challenge, state = parse_prelogin_url(NESTED_URL)
    assert challenge == "Tn6RQt-EXAMPLEchallenge_-mLOoTibKZc"
    assert state == "OpenIdConnect.AuthenticationProperties%253DEXAMPLEstateValue"


def test_parse_prelogin_url_ignores_parameter_order():
    """旧实现写死 split 下标，参数换顺序就失效；这里必须顺序无关。"""
    reordered = (
        "https://sso.sflep.com/idsvr/transfer.html?returnUrl=%2Fx%3Fstate%3DSTATE"
        "%26code_challenge%3DCHALLENGE"
    )
    assert parse_prelogin_url(reordered) == ("CHALLENGE", "STATE")


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