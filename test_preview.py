import importlib.util
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location('course_preview', Path(__file__).with_name('preview.py'))
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def test_preview_serves_local_builder_and_forwards_authenticated_muse_requests():
    seen = []
    def backend(request):
        seen.append(request)
        return httpx.Response(200, json={'message': 'Muse reply', 'code': None})
    with TestClient(preview.create_preview('https://buildplay.fun', transport=httpx.MockTransport(backend))) as client:
        page = client.get('/builder')
        assert page.status_code == 200
        assert 'builder.css' in page.text
        assert 'Connected to buildplay.fun' in page.text
        reply = client.post('/agent/chat', json={'messages': []}, headers={'X-User-Id': 'test-user', 'X-User-Password': 'test-password'})
        assert reply.json()['message'] == 'Muse reply'
        assert str(seen[0].url) == 'https://buildplay.fun/agent/chat'
        assert seen[0].headers['x-user-id'] == 'test-user'
        assert seen[0].headers['x-user-password'] == 'test-password'
        assert client.get('/admin/stats').status_code == 404
        assert client.get('/debug/all-data').status_code == 404


def test_preview_keeps_backend_errors_and_query_parameters():
    def backend(request):
        assert request.url.params['owner_user_id'] == 'owner'
        return httpx.Response(401, json={'detail': 'invalid credentials'})
    with TestClient(preview.create_preview('https://buildplay.fun', transport=httpx.MockTransport(backend))) as client:
        response = client.get('/games?owner_user_id=owner')
        assert response.status_code == 401
        assert response.json()['detail'] == 'invalid credentials'


def test_preview_reports_unreachable_backend():
    def backend(request):
        raise httpx.ConnectError('unreachable', request=request)
    with TestClient(preview.create_preview('https://buildplay.fun', transport=httpx.MockTransport(backend))) as client:
        assert client.get('/auth/config').status_code == 502
