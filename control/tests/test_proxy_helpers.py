from nova_control.main import STRIP_REQUEST, _query_without_key


def test_client_session_key_never_reaches_the_workspace():
    assert _query_without_key("session_api_key=evil&resend=1") == "resend=1"
    assert {"x-session-api-key", "authorization", "cookie", "kubeflow-userid"} <= STRIP_REQUEST
