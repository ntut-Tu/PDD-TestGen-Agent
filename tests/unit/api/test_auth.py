import pytest

from pdd_agent.api.auth import JwtVerifier, bearer_token
from pdd_agent.api.errors import ApiError
from tests.conftest import SECRET, auth, make_token


def test_valid_token_yields_principal():
    principal = JwtVerifier(SECRET).verify(make_token("bob"))
    assert principal.account == "bob"


@pytest.mark.parametrize(
    "token",
    [
        make_token(exp_in=-10),
        make_token(secret="other-secret-0123456789-abcdefghij"),
        make_token(sub=None),
        make_token(oauth=None),
        "garbage",
    ],
)
def test_invalid_tokens_are_rejected(token):
    with pytest.raises(ApiError) as err:
        JwtVerifier(SECRET).verify(token)
    assert err.value.status == 401


def test_token_without_expiry_is_rejected():
    import base64

    import jwt

    key = base64.urlsafe_b64encode(SECRET.encode())
    token = jwt.encode({"sub": "a", "oauth": "x"}, key, algorithm="HS256")
    with pytest.raises(ApiError):
        JwtVerifier(SECRET).verify(token)


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer ", "Bearer"])
def test_bearer_token_required(header):
    with pytest.raises(ApiError) as err:
        bearer_token(header)
    assert err.value.status == 401


def test_bearer_token_extracted():
    assert bearer_token("bearer abc ") == "abc"


def test_auth_helper_builds_header():
    assert auth()["Authorization"].startswith("Bearer ")
