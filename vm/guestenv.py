"""The selected test guest's ports, VM dir and hostname, as vm/guests.sh exports them.

Jammy is the default: with TREX_VM_GUEST empty or `jammy` and a variable empty, jammy's value is returned.
Any other guest without its variables is refused, never silently read as jammy (a hand run with only
TREX_VM_GUEST set would otherwise connect to jammy's server). Empty counts as unset, as `make` exports it."""
import os
from collections.abc import Mapping
from pathlib import Path


class GuestEnvError(RuntimeError):
    """A non-jammy guest is selected but vm/guests.sh has not exported its variables."""


def _guest(env: Mapping[str, str]) -> str:
    """The selected guest name; '' when none is selected."""
    return env.get("TREX_VM_GUEST") or ""


def _check_prepared(env: Mapping[str, str]) -> None:
    """Refuse an environment prepared (by an eval'd vm/guests.sh) for another guest than the selected one."""
    prepared = env.get("TREX_VM_CODENAME") or ""
    guest = _guest(env) or "jammy"
    if prepared and prepared != guest:
        raise GuestEnvError(f'this environment was prepared for {prepared}, but {guest} is selected: '
                            f'unset TREX_VM_CODENAME TREX_VM_DIR TREX_VM_DL TREX_VM_SHARED or re-run '
                            f'eval "$(TREX_VM_GUEST={guest} vm/guests.sh)"')


def _refuse(guest: str) -> GuestEnvError:
    """The error for a non-jammy guest whose variables are missing."""
    return GuestEnvError(f'TREX_VM_GUEST={guest} without its ports: '
                         f'run eval "$(TREX_VM_GUEST={guest} vm/guests.sh)" first')


def ports(env: Mapping[str, str] = os.environ) -> tuple[int, int]:
    """(sync/RPC port, async/PUB port) of the selected guest: jammy's (4501, 4500) by default."""
    _check_prepared(env)
    sync, asyn = env.get("TREX_REAL_SYNC_PORT") or "", env.get("TREX_REAL_ASYNC_PORT") or ""
    guest = _guest(env)
    if sync and asyn:
        try:
            return int(sync), int(asyn)
        except ValueError:
            name = guest or "jammy"
            raise GuestEnvError(f"TREX_REAL_SYNC_PORT={sync!r} and TREX_REAL_ASYNC_PORT={asyn!r} must be "
                                f'port numbers: re-run eval "$(TREX_VM_GUEST={name} vm/guests.sh)"') from None
    if guest in ("", "jammy") and not (sync or asyn):
        return 4501, 4500
    if guest in ("", "jammy"):
        raise GuestEnvError("TREX_REAL_SYNC_PORT and TREX_REAL_ASYNC_PORT must be set together: "
                            'unset both or re-run eval "$(TREX_VM_GUEST=<guest> vm/guests.sh)"')
    raise _refuse(guest)


def _pick(env: Mapping[str, str], key: str, jammy: str) -> str:
    """env[key], or jammy's default when no other guest is selected; refuse otherwise."""
    _check_prepared(env)
    value = env.get(key) or ""
    if value:
        return value
    guest = _guest(env)
    if guest in ("", "jammy"):
        return jammy
    raise _refuse(guest)


def vm_dir(env: Mapping[str, str] = os.environ) -> Path:
    """The guest's VM directory (TREX_VM_DIR), /var/tmp/trex-vm for jammy."""
    return Path(_pick(env, "TREX_VM_DIR", "/var/tmp/trex-vm"))


def hostname(env: Mapping[str, str] = os.environ) -> str:
    """The guest's hostname (TREX_VM_HOSTNAME), trex-vm for jammy."""
    return _pick(env, "TREX_VM_HOSTNAME", "trex-vm")
