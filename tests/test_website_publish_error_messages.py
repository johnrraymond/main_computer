from __future__ import annotations

from main_computer.viewport_routes_applications import _website_publish_error_message


def test_website_publish_error_message_surfaces_blog_runtime_error() -> None:
    message = _website_publish_error_message(
        {
            "ok": False,
            "verified": False,
            "blog_runtime_verify": {
                "status": 502,
                "payload": {
                    "ok": False,
                    "blog": {
                        "error": "Directus request failed with HTTP 403: missing public posts permission"
                    },
                },
            },
        }
    )

    assert message == "Directus request failed with HTTP 403: missing public posts permission"


def test_website_publish_error_message_prefers_explicit_blog_runtime_error() -> None:
    message = _website_publish_error_message(
        {
            "ok": False,
            "verified": False,
            "blog_runtime_verify_error": "posts collection missing",
        }
    )

    assert message == "posts collection missing"
