# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# FORK: one-time codes/tokens must not appear in the argsrepr/kwargsrepr celery logs.

from unittest.mock import patch

import pytest

from plane.bgtasks.magic_link_code_task import magic_link
from plane.bgtasks.secret_args_task import MASK
from plane.bgtasks.workspace_delete_code_task import send_workspace_delete_code

TOKEN = "482913"


def _sent(task, *args, **kwargs):
    """Call task.delay / apply_async and return the options handed to send_task."""
    with patch.object(task.app, "send_task") as send_task:
        if kwargs.pop("_apply_async", False):
            task.apply_async(args=args, kwargs=kwargs.pop("_kwargs", None), **kwargs)
        else:
            task.delay(*args, **kwargs)
    assert send_task.call_count == 1
    call = send_task.call_args
    return call.args, call.kwargs


@pytest.mark.unit
def test_magic_link_token_masked_in_reprs():
    args, options = _sent(magic_link, "user@example.com", "magic_user@example.com", TOKEN)
    # The real token still reaches the task.
    assert args[1] == ("user@example.com", "magic_user@example.com", TOKEN)
    assert TOKEN not in options["argsrepr"]
    assert MASK in options["argsrepr"]
    assert "user@example.com" in options["argsrepr"]


@pytest.mark.unit
def test_delete_code_masked_in_reprs():
    args, options = _sent(send_workspace_delete_code, "owner@example.com", "Acme", TOKEN)
    assert args[1] == ("owner@example.com", "Acme", TOKEN)
    assert TOKEN not in options["argsrepr"]
    assert "Acme" in options["argsrepr"]


@pytest.mark.unit
def test_secret_passed_as_kwarg_is_masked():
    _, options = _sent(send_workspace_delete_code, "owner@example.com", workspace_name="Acme", code=TOKEN)
    assert TOKEN not in options["argsrepr"]
    assert TOKEN not in options["kwargsrepr"]
    assert MASK in options["kwargsrepr"]


@pytest.mark.unit
def test_caller_supplied_repr_cannot_leak_secret():
    _, options = _sent(
        magic_link,
        "user@example.com",
        "magic_user@example.com",
        TOKEN,
        _apply_async=True,
        argsrepr=repr(("user@example.com", "magic_user@example.com", TOKEN)),
    )
    assert TOKEN not in options["argsrepr"]


@pytest.mark.unit
def test_published_message_carries_masked_repr():
    """The repr the worker logs is read from the message itself; check it there."""
    amqp = magic_link.app.amqp
    with patch.object(amqp, "send_task_message") as send:
        magic_link.delay("user@example.com", "magic_user@example.com", TOKEN)
    message = send.call_args.args[2]
    headers, body = message.headers, message.body
    assert TOKEN not in headers["argsrepr"]
    assert TOKEN not in headers["kwargsrepr"]
    assert body[0][2] == TOKEN  # the task still gets the real token
