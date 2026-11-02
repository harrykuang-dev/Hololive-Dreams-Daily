import pytest

from daily.game_process import ParentProcess, GameProcessError


class FakeSignalBackend:
    def __init__(self):
        self.created = 12345
        self.running = True
        self.opened = []
        self.closed = []
    def open_signal_process(self, pid):
        self.opened.append(pid)
        return 'original-parent-object'
    def creation_time(self, handle): return self.created
    def is_running(self, handle):
        assert handle == 'original-parent-object'
        return self.running
    def close_handle(self, handle): self.closed.append(handle)


def test_parent_exit_is_observed_on_original_handle_without_pid_relookup():
    backend = FakeSignalBackend()
    parent = ParentProcess(99, expected_creation=12345, backend=backend)
    assert parent.alive()
    backend.running = False
    backend.created = 98765  # A newly reused PID belongs to another object.
    assert not parent.alive()
    assert backend.opened == [99]
    parent.close()
    assert backend.closed == ['original-parent-object'] and not parent.alive()


def test_parent_pid_reused_before_binding_fails_creation_identity():
    backend = FakeSignalBackend()
    with pytest.raises(GameProcessError, match='identity'):
        ParentProcess(99, expected_creation=123, backend=backend)
    assert backend.closed == ['original-parent-object']
