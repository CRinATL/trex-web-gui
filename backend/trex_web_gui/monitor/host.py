"""The host rule of the Monitor's target: stricter than validation.check_host, because libzmq refuses an
endpoint such as `tcp://-x:4500`."""
import re

LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?")


def check_monitor_host(host: str) -> str:
    """`host` when it is an IPv6 literal (anything with a colon is left to check_host) or a dotted name whose
    every label is non-empty and starts and ends with a letter or digit; ValueError otherwise."""
    if ":" not in host and not all(LABEL_RE.fullmatch(label) for label in host.split(".")):
        raise ValueError("every dot-separated label of the host must start and end with a letter or digit")
    return host
