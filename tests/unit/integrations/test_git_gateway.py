import httpx
import pytest

from pdd_agent.domain.errors import SourceError
from pdd_agent.integrations.git_gateway import GitGatewayClient
from tests.conftest import GATEWAY


async def tree(upstreams, **mock):
    upstreams.tree_route.side_effect = None
    upstreams.tree_route.mock(**mock)
    async with httpx.AsyncClient() as http:
        return await GitGatewayClient(http, GATEWAY + "/", "tok").tree("alice", 7, 3, "sha")


async def test_unreachable_gateway(upstreams):
    with pytest.raises(SourceError, match="unreachable"):
        await tree(upstreams, side_effect=httpx.ConnectError("down"))


async def test_unexpected_body(upstreams):
    with pytest.raises(SourceError, match="unexpected"):
        await tree(upstreams, return_value=httpx.Response(200, json={"entries": "x"}))
    with pytest.raises(SourceError, match="unexpected"):
        await tree(upstreams, return_value=httpx.Response(200, text="<html>"))


async def test_status_is_exposed(upstreams):
    with pytest.raises(SourceError) as err:
        await tree(upstreams, return_value=httpx.Response(404))
    assert err.value.status == 404
