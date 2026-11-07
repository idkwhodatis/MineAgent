"""Apply the normal CORS policy to responses emitted outside application middleware."""

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


class CORSErrorResponse(JSONResponse):
    def __init__(self, content, *, cors_options=None, **kwargs):
        super().__init__(content, **kwargs)
        self.cors_options = cors_options

    async def __call__(self, scope, receive, send):
        response = super().__call__
        if self.cors_options is not None:
            # Reuse Starlette's complete policy, including wildcard origins,
            # credential handling and Vary, instead of synthesizing CORS headers.
            response = CORSMiddleware(response, **self.cors_options)
        await response(scope, receive, send)
