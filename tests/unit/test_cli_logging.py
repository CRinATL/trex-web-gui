"""The serve console log: each line leads with its date and time; trex_web_gui's own loggers print
(RC1-26)."""
import io
import logging
import logging.config
import re

from trex_web_gui.cli import log_config

STAMP = r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d "


def test_each_line_has_its_time_and_trex_web_gui_logs_at_info() -> None:
    cfg = log_config()
    out = io.StringIO()
    for h in cfg["handlers"].values():
        h["stream"] = out
    logging.config.dictConfig(cfg)
    try:
        logging.getLogger("trex_web_gui.sessions.trex_session").info("Released port(s) [0]")
        logging.getLogger("uvicorn.error").warning("Waiting up to 14.5 s")
        logging.getLogger("trex_web_gui").debug("not shown")
    finally:
        reset = {"handlers": [], "propagate": True, "level": "NOTSET"}
        logging.config.dictConfig({"version": 1, "disable_existing_loggers": False,
                                   "loggers": {"trex_web_gui": reset, "uvicorn": dict(reset)}})
    lines = out.getvalue().splitlines()
    assert len(lines) == 2
    assert all(re.match(STAMP, ln) for ln in lines)
    assert "INFO:" in lines[0] and lines[0].endswith("Released port(s) [0]")
    assert "WARNING:" in lines[1]


def test_log_config_leaves_uvicorns_default_alone() -> None:
    from uvicorn.config import LOGGING_CONFIG
    log_config()
    assert "asctime" not in LOGGING_CONFIG["formatters"]["default"]["fmt"]
