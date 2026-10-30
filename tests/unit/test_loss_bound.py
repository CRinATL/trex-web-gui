import re
from pathlib import Path

import pytest
from trex_web_gui.trexapi import loss_bound

RRC = (Path(__file__).resolve().parents[2]
       / "backend/trex_web_gui/_vendor/trex/common/trex_req_resp_client.py")


def test_loss_bound_values() -> None:
    assert loss_bound(3.0) == pytest.approx(14.5)  # the plan's "about 14.5 s at the default 3 s"
    assert loss_bound(3.0, margin=0.0) == pytest.approx(12.5)
    assert loss_bound(1.0, margin=0.5) == pytest.approx(5.0)


def test_loss_bound_tracks_vendored_retry_base() -> None:
    # 4 = 1 attempt + ZMQ_RETRY_BASE retries; if the vendored client changes it, the bound is stale.
    m = re.search(r"^ZMQ_RETRY_BASE = (\d+)$", RRC.read_text(), re.MULTILINE)
    assert m, f"ZMQ_RETRY_BASE not found in {RRC}"
    attempts = 1 + int(m.group(1))
    assert loss_bound(2.0, margin=0.0) - loss_bound(1.0, margin=0.0) == pytest.approx(attempts)
