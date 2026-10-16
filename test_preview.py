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
        assert reply.status_code == 202
        job = reply.json()['preview_job_id']
        owner = {'X-User-Id': 'test-user', 'X-User-Password': 'test-password'}
        assert client.get(f'/preview/jobs/{job}').status_code == 404
        for _ in range(20):
            result = client.get(f'/preview/jobs/{job}', headers=owner)
            if result.status_code != 202:
                break
        assert result.json()['message'] == 'Muse reply'
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


def test_slow_generation_returns_a_job_and_remains_pollable():
    import asyncio
    import time
    async def backend(request):
        await asyncio.sleep(0.15)
        return httpx.Response(200, json={'message': 'Generated backgammon', 'code': '<html>game</html>'})
    with TestClient(preview.create_preview('https://buildplay.fun', transport=httpx.MockTransport(backend))) as client:
        assert client.post('/agent/chat', json={}).status_code == 401
        owner = {'X-User-Id': 'test-user', 'X-User-Password': 'test-password'}
        start = client.post('/agent/chat', json={}, headers=owner)
        assert start.status_code == 202
        path = '/preview/jobs/' + start.json()['preview_job_id']
        assert client.get(path, headers=owner).status_code == 202
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = client.get(path, headers=owner)
            if result.status_code != 202:
                break
            time.sleep(0.02)
        assert result.json()['message'] == 'Generated backgammon'

def test_preview_returns_an_explicit_timeout_instead_of_a_network_failure():
    def backend(request):
        raise httpx.ReadTimeout('slow model', request=request)
    with TestClient(preview.create_preview('https://buildplay.fun', transport=httpx.MockTransport(backend))) as client:
        result = client.get('/agent/chat')
        assert result.status_code == 504
        assert '15 minutes' in result.json()['detail']
        assert 'preserved' in result.json()['detail']
