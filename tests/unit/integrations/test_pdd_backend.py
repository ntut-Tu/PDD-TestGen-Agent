import httpx
import pytest

from pdd_agent.domain.errors import ForbiddenError, UnauthorizedError, UpstreamError
from pdd_agent.domain.jobs import Principal
from pdd_agent.integrations.pdd_backend import Membership
from tests.conftest import BACKEND


class FakeClock:
    now = 0.0

    def __call__(self):
        return self.now


async def test_membership_is_cached_for_ttl(upstreams):
    clock = FakeClock()
    async with httpx.AsyncClient() as http:
        membership = Membership(http, BACKEND, 30, clock)
        principal = Principal("alice", "t1")
        assert await membership.project_ids(principal) == {7}
        await membership.ensure_member(principal, 7)
        route = upstreams.backend
        assert route.call_count == 1
        clock.now = 31
        await membership.project_ids(principal)
        assert route.call_count == 2
        with pytest.raises(ForbiddenError) as err:
            await membership.ensure_member(principal, 99)
        assert err.value.code == "forbidden"


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(401), UnauthorizedError),
        (httpx.Response(500), UpstreamError),
        (httpx.Response(200, json={"x": 1}), UpstreamError),
        (httpx.Response(200, text="not json"), UpstreamError),
    ],
)
async def test_membership_backend_failures(upstreams, response, error):
    upstreams.router.get(f"{BACKEND}/project/").mock(return_value=response)
    async with httpx.AsyncClient() as http:
        with pytest.raises(error):
            await Membership(http, BACKEND, 30).project_ids(Principal("a", "t"))


async def test_membership_backend_unreachable(upstreams):
    upstreams.router.get(f"{BACKEND}/project/").mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(UpstreamError):
            await Membership(http, BACKEND, 30).project_ids(Principal("a", "t"))
