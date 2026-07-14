"""
tls_requests shim to handle TLS requests using tls_client or standard requests fallback
"""
import requests

def get(url, headers=None, timeout=20, **kwargs):
    try:
        import tls_client
        session = tls_client.Session(client_identifier="chrome_120")
        return session.get(url, headers=headers, timeout_seconds=timeout, **kwargs)
    except Exception:
        return requests.get(url, headers=headers, timeout=timeout, **kwargs)

def post(url, headers=None, data=None, json=None, timeout=20, **kwargs):
    try:
        import tls_client
        session = tls_client.Session(client_identifier="chrome_120")
        return session.post(url, headers=headers, data=data, json=json, timeout_seconds=timeout, **kwargs)
    except Exception:
        return requests.post(url, headers=headers, data=data, json=json, timeout=timeout, **kwargs)
