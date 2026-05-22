from __future__ import annotations

import ssl
from functools import lru_cache

try:
    import certifi
except ImportError:  # pragma: no cover - exercised when optional dependency is unavailable
    certifi = None


@lru_cache(maxsize=1)
def trusted_ca_bundle() -> str:
    if certifi is None:
        return ""
    try:
        return str(certifi.where()).strip()
    except Exception:
        return ""


@lru_cache(maxsize=1)
def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    bundle = trusted_ca_bundle()
    if bundle:
        try:
            context.load_verify_locations(cafile=bundle)
        except OSError:
            pass
    return context


def websocket_ssl_options() -> dict[str, object]:
    return {"context": tls_context()}


def describe_tls_error(reason: object) -> str:
    detail = str(reason).strip()
    lowered = detail.lower()
    if "certificate verify failed" not in lowered and "unable to get local issuer certificate" not in lowered:
        return detail
    guidance = (
        "Could not verify Twitch's TLS certificate. This Python runtime is missing trusted root certificates. "
        "Reinstall the app dependencies so certifi is available, or repair the Python certificates on this machine."
    )
    if not detail:
        return guidance
    return f"{guidance} ({detail})"
