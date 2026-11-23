"""Bound audio bodies before multipart parsing can spool an oversized upload."""
from starlette.responses import JSONResponse
from app.core.screening_config import get_voice_settings


class ScreeningAudioLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] != 'http' or not path.startswith('/api/screenings/') or not path.endswith('/audio'):
            return await self.app(scope, receive, send)
        try:
            limit = get_voice_settings().voice_max_audio_bytes + 65536
        except ValueError:
            return await JSONResponse({'detail': 'Local voice configuration is unavailable.'}, status_code=503)(scope, receive, send)
        try:
            headers = dict(scope.get('headers', []))
            size = int(headers.get(b'content-length', b'0'))
        except (ValueError, TypeError):
            return await JSONResponse({'detail': 'Invalid audio upload size/configuration.'}, status_code=413)(scope, receive, send)
        if size > limit:
            return await JSONResponse({'detail': 'Audio upload exceeds the configured size limit.'}, status_code=413)(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body.extend(message.get('body', b''))
            if len(body) > limit:
                return await JSONResponse({'detail': 'Audio upload exceeds the configured size limit.'}, status_code=413)(scope, receive, send)
            if not message.get('more_body', False):
                break
        replayed = False
        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()
        await self.app(scope, replay, send)
