# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# FORK: keep one-time codes/tokens out of celery logs.
#
# The worker logs every "Task ... received" line with the task's argsrepr/kwargsrepr
# (our JsonFormatter emits it as the `data` field), and task events carry the same
# reprs. By default those are saferepr(args), so a plaintext code passed as a task
# argument ends up in the worker log. Tasks built on SecretArgsTask send a masked
# repr instead; the real arguments still reach the task unchanged.
#
# Usage:
#   @shared_task(base=SecretArgsTask, secret_args=("token",))
#   def my_task(email, token): ...

# Python imports
import inspect

# Third party imports
from celery import Task
from celery.utils.saferepr import saferepr

MASK = "***"


class SecretArgsTask(Task):
    abstract = True
    # Parameter names whose values must never be logged.
    secret_args = ()

    def _secret_positions(self):
        names = list(inspect.signature(self.run).parameters)
        return {i for i, name in enumerate(names) if name in self.secret_args}

    def masked_reprs(self, args=None, kwargs=None):
        positions = self._secret_positions()
        masked_args = tuple(MASK if i in positions else a for i, a in enumerate(args or ()))
        masked_kwargs = {k: (MASK if k in self.secret_args else v) for k, v in (kwargs or {}).items()}
        return saferepr(masked_args), saferepr(masked_kwargs)

    def apply_async(self, args=None, kwargs=None, **options):
        if self.secret_args:
            argsrepr, kwargsrepr = self.masked_reprs(args, kwargs)
            # Always override: a caller-supplied repr could still carry the secret.
            options["argsrepr"] = argsrepr
            options["kwargsrepr"] = kwargsrepr
        return super().apply_async(args, kwargs, **options)
