"""Small Python adaptation of translate.js's client.edge batch service.

Upstream: xnx3/translate, df9d77c85399470d10e2cb610f17e294ac107740,
translate.js/translate.js, translate.service.edge (MIT, Copyright 2025 xnx3).
See licenses/translate.js-MIT.txt. DOM handling and caching remain in CSSwitch.
"""
import json
import urllib.request

ENDPOINT = 'https://edge.microsoft.com/translate/translatetext?from=en&to=zh-CHS&isEnterpriseClient=false'


def translate(texts, opener=None):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(ENDPOINT, data=json.dumps(texts).encode(),
        headers={'Content-Type':'application/json', 'User-Agent':'Mozilla/5.0'})
    with opener.open(request, timeout=20) as response:
        raw = response.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        raise ValueError('Oversized translation response')
    data = json.loads(raw)
    if not isinstance(data, list) or len(data) != len(texts):
        raise ValueError('Invalid translation response count')
    return [item['translations'][0]['text'] for item in data]
