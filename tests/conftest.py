"""Shared test set-up: the command line's educational-use notice is accepted through the environment
(windlab.disclaimer), so no test waits for a ``yes`` and none writes ~/.windlab/accepted. The tests of
the notice itself remove the variable again."""

from __future__ import annotations

import pytest

from windlab import disclaimer


@pytest.fixture(autouse=True)
def _accept_disclaimer(monkeypatch):
    monkeypatch.setenv(disclaimer.ENV_ACCEPT, "1")
