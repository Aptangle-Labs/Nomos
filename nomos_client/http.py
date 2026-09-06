import httpx


class Client:
    def __init__(self, base_url, key, transport=None):
        if not key or not key.strip():
            raise ValueError("An agent API key is required; keyless admin fallback is prohibited.")
        self.http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": "Bearer " + key},
            timeout=70,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def request(self, method, path, body=None, key=None):
        headers = {"Idempotency-Key": key} if key else {}
        response = self.http.request(method, path, json=body, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(payload.get("error", {}).get("message", "Request failed"))
        return payload["data"]

    def close(self):
        self.http.close()
