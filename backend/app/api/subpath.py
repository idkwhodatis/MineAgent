"""Explicit strip-prefix deployment adapter; never trusts request headers."""

from urllib.parse import urlsplit

from starlette.datastructures import URL
from starlette.types import ASGIApp, Receive, Scope, Send


class SubpathMiddleware:
    def __init__(self, app: ASGIApp, subpath: str = ""):
        self.app = app
        self.subpath = subpath

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if self.subpath and scope["type"] in {"http", "websocket"}:
            scope = dict(scope)
            # The proxy strips SUBPATH; reconstruct ASGI's public path and
            # root_path together (root_path alone breaks overlapping /api).
            # Do not additionally configure a server --root-path.
            if scope.get("root_path"):
                raise RuntimeError("Configure SUBPATH only, not server root_path")
            scope["root_path"] = self.subpath
            scope["path"] = self.subpath + scope["path"]
            if "raw_path" in scope:
                scope["raw_path"] = self.subpath.encode("ascii") + scope["raw_path"]
            public = urlsplit(str(URL(scope=scope)))

            async def prefix_redirect(message):
                if message["type"] == "http.response.start" and message["status"] in {
                    301,
                    302,
                    303,
                    307,
                    308,
                }:
                    headers = []
                    for name, value in message.get("headers", []):
                        if name.lower() == b"location":
                            target = urlsplit(value.decode("latin-1"))
                            # Same-origin app redirects need no authority/scheme.
                            # Preserve the browser's outer TLS and port without
                            # trusting arbitrary forwarded headers.
                            if (target.scheme, target.netloc) == (
                                public.scheme,
                                public.netloc,
                            ) and target.path.startswith(self.subpath + "/"):
                                value = (
                                    target._replace(scheme="", netloc="")
                                    .geturl()
                                    .encode("latin-1")
                                )
                        headers.append((name, value))
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, prefix_redirect)
            return
        await self.app(scope, receive, send)
