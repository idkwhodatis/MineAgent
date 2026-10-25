"""Actual ASGI raw admission tests without DB/parser/filesystem substitutes."""

import asyncio

import pytest

from backend.app.api.body_limit import BodyLimitMiddleware
from backend.app.factor_import_services import FactorImportIngressMiddleware


def scope(media=b"application/zip", headers=()):
    return {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/factor-packages/import",
        "headers": [(b"content-type", media), *headers],
    }


def test_ingress_counts_chunked_and_dishonest_lengths_before_app():
    async def run():
        calls = []
        messages = []

        async def app(s, r, send):
            calls.append(await r())

        middleware = FactorImportIngressMiddleware(
            BodyLimitMiddleware(app, 1000), max_bytes=10
        )
        chunks = iter(
            [
                {"type": "http.request", "body": b"123456", "more_body": True},
                {"type": "http.request", "body": b"123456", "more_body": False},
            ]
        )

        async def receive():
            return next(chunks)

        async def send(message):
            messages.append(message)

        await middleware(scope(headers=[(b"content-length", b"1")]), receive, send)
        assert messages[0]["status"] == 413
        assert not calls
        assert middleware.inflight == 0

    asyncio.run(run())


@pytest.mark.parametrize(
    "media,headers",
    [
        (b"multipart/form-data", ()),
        (b"application/zip", [(b"content-encoding", b"gzip")]),
    ],
)
def test_ingress_refuses_multipart_and_encoded_without_read(media, headers):
    async def run():
        messages = []

        async def unreachable(*args):
            raise AssertionError("must not read or dispatch")

        async def send(message):
            messages.append(message)

        middleware = FactorImportIngressMiddleware(unreachable)
        await middleware(scope(media, headers), unreachable, send)
        assert messages[0]["status"] == 415

    asyncio.run(run())


def test_ingress_timeout_releases_slot_without_application_or_disk():
    async def run():
        messages = []

        async def app(*args):
            raise AssertionError("must not reach app")

        async def receive():
            await asyncio.sleep(1)

        async def send(message):
            messages.append(message)

        middleware = FactorImportIngressMiddleware(
            BodyLimitMiddleware(app, 1000), read_seconds=0.01
        )
        await middleware(scope(), receive, send)
        assert messages[0]["status"] == 408
        assert middleware.inflight == 0

    asyncio.run(run())


def test_ingress_two_slots_busy_and_disconnect_cleanup():
    async def run():
        release = asyncio.Event()
        messages = []

        async def app(*args):
            raise AssertionError("disconnected")

        async def receive():
            await release.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        middleware = FactorImportIngressMiddleware(BodyLimitMiddleware(app, 1000))
        tasks = [
            asyncio.create_task(middleware(scope(), receive, send)) for _ in range(2)
        ]
        await asyncio.sleep(0)
        assert middleware.inflight == 2
        await middleware(scope(), receive, send)
        assert messages[0]["status"] == 503
        release.set()
        await asyncio.gather(*tasks)
        assert middleware.inflight == 0

    asyncio.run(run())


def test_near_limit_raw_body_is_delivered_without_spooling(tmp_path, monkeypatch):
    import tempfile

    def no_spool(*args, **kwargs):
        raise AssertionError("raw body must not spool")

    monkeypatch.setattr(tempfile, "TemporaryFile", no_spool)
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", no_spool)

    async def run():
        received = []

        async def app(s, r, send):
            received.append((await r())["body"])

        async def receive():
            return {"type": "http.request", "body": b"x" * 999, "more_body": False}

        async def send(message):
            pass

        middleware = FactorImportIngressMiddleware(
            BodyLimitMiddleware(app, 1100), max_bytes=1000
        )
        await middleware(scope(), receive, send)
        assert received == [b"x" * 999]
        assert list(tmp_path.iterdir()) == []

    asyncio.run(run())
