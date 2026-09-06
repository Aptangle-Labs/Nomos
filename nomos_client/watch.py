import hashlib
import json
import random
import time

import httpx

from .spool import Spool

# Every event type a runner must not miss: reply requests, direct work
# assignment, mentions, and pending handoff offers awaiting acceptance.
WATCH_TYPES = "inbox_requested,inbox_updated,ticket_assigned,mention,handoff_updated"


def watch(client, pid, path, *, once=False, emit=print):
    identity = client.request("GET", "/api/me")
    types = WATCH_TYPES
    scope = hashlib.sha256(
        json.dumps([str(client.http.base_url), pid, identity["id"], types]).encode()
    ).hexdigest()
    spool = Spool(path, scope)
    consumer = client.request("POST", f"/api/projects/{pid}/consumers", {"name": scope})
    delay = 1
    try:
        while True:
            try:
                client.request("POST", f"/api/projects/{pid}/consumers/{consumer['id']}/heartbeat")
                # Drain committed output before fetching more (also after a crash).
                for event in spool.pending():
                    emit(json.dumps({"delivery": "emitted_acceptance_unknown", "event": event}))
                    spool.emitted(event["id"])
                data = client.request(
                    "GET",
                    f"/api/projects/{pid}/events?since_id={spool.cursor}&types={types}&timeout={0 if once else 30}",
                )
                spool.ingest(data["items"], data["last_event_id"])
                for event in spool.pending():
                    emit(json.dumps({"delivery": "emitted_acceptance_unknown", "event": event}))
                    spool.emitted(event["id"])
                delay = 1
                if once:
                    return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise  # especially revocation; never retry without credentials
                if once:
                    raise
                time.sleep(delay + random.uniform(0, 0.25))
                delay = min(delay * 2, 30)
            except httpx.TransportError:
                if once:
                    raise
                time.sleep(delay + random.uniform(0, 0.25))
                delay = min(delay * 2, 30)
    finally:
        spool.close()
