"""Unit — the plugin-local dual-auth dispatch decorator.

``_require_session_or_api_key(scope)`` must, at request time, route through the
core API-key guard when an ``X-API-Key`` header is present (reusing the
middleware machinery, never reimplementing verification) and otherwise through
``require_auth``. Both paths set ``g.user_id`` and the route stays flagged as
authenticated for the route-exposure oracle.
"""
from flask import Flask

import plugins.dataset.dataset.routes as routes_mod


def _install_recording_guards(monkeypatch):
    """Swap the two core guards for recorders so the test observes dispatch."""
    calls = []

    def fake_require_api_key(scope=None):
        def decorator(view_func):
            def wrapper(*args, **kwargs):
                calls.append(("api_key", scope))
                return view_func(*args, **kwargs)

            return wrapper

        return decorator

    def fake_require_auth(view_func):
        def wrapper(*args, **kwargs):
            calls.append(("session",))
            return view_func(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(routes_mod, "require_api_key", fake_require_api_key)
    monkeypatch.setattr(routes_mod, "require_auth", fake_require_auth)
    return calls


def _decorated_view():
    @routes_mod._require_session_or_api_key(scope=routes_mod.DATASET_READ_SCOPE)
    def view():
        return "ok"

    return view


def test_dispatch_uses_api_key_path_when_header_present(monkeypatch):
    calls = _install_recording_guards(monkeypatch)
    view = _decorated_view()

    app = Flask(__name__)
    with app.test_request_context(headers={"X-API-Key": "some-key"}):
        assert view() == "ok"

    assert calls == [("api_key", routes_mod.DATASET_READ_SCOPE)]


def test_dispatch_uses_session_path_when_no_api_key_header(monkeypatch):
    calls = _install_recording_guards(monkeypatch)
    view = _decorated_view()

    app = Flask(__name__)
    with app.test_request_context(headers={"Authorization": "Bearer token"}):
        assert view() == "ok"

    assert calls == [("session",)]


def test_decorated_view_is_flagged_authenticated_for_route_oracle():
    view = _decorated_view()
    assert getattr(view, "requires_auth", False) is True
