"""
tls_requests shim to handle TLS requests using tls_client or standard requests fallback
"""
import requests

def _wrap_response(response):
    if not hasattr(response, 'raise_for_status'):
        def raise_for_status():
            status_code = getattr(response, 'status_code', 200)
            if 400 <= status_code < 600:
                raise requests.HTTPError(f"HTTP Error: {status_code}", response=response)
        response.raise_for_status = raise_for_status
    return response

def get(url, headers=None, timeout=20, **kwargs):
    try:
        import tls_client
        session = tls_client.Session(client_identifier="chrome_120")
        resp = session.get(url, headers=headers, timeout_seconds=timeout, **kwargs)
        return _wrap_response(resp)
    except Exception:
        return requests.get(url, headers=headers, timeout=timeout, **kwargs)

def post(url, headers=None, data=None, json=None, timeout=20, **kwargs):
    try:
        import tls_client
        session = tls_client.Session(client_identifier="chrome_120")
        resp = session.post(url, headers=headers, data=data, json=json, timeout_seconds=timeout, **kwargs)
        return _wrap_response(resp)
    except Exception:
        return requests.post(url, headers=headers, data=data, json=json, timeout=timeout, **kwargs)
