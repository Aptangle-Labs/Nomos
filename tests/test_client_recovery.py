import pytest

from nomos_client.http import Client
from nomos_client.spool import Spool


def test_spool_restart_dedupe_and_atomic_backpressure(tmp_path):
    path = tmp_path / "spool.db"
    s = Spool(path, "project-a", capacity=2)
    s.ingest([{"id": 1}, {"id": 2}], 2)
    s.close()
    s = Spool(path, "project-a", capacity=2)
    assert s.cursor == 2 and len(s.pending()) == 2
    s.ingest([{"id": 2}], 2)
    with pytest.raises(BufferError):
        s.ingest([{"id": 3}], 3)
    assert s.cursor == 2 and [e["id"] for e in s.pending()] == [1, 2]
    s.emitted(1)
    s.ingest([{"id": 3}], 3)
    assert [e["id"] for e in s.pending()] == [2, 3]
    other = Spool(path, "different-subscription")
    assert other.cursor == 0 and other.pending() == []
    other.close()
    s.close()


def test_no_keyless_fallback():
    with pytest.raises(ValueError, match="key"):
        Client("http://localhost:8484", "")
