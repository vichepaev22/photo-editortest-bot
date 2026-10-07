import pytest

from image_studio.runtime import AlreadyRunning, BotRuntime, status


def test_second_instance_rejected_and_stop_marker_is_local(tmp_path):
    with BotRuntime(tmp_path, "mock") as first:
        first.ready("photo_editortest_bot")
        state = status(tmp_path)
        assert state["running"] and state["mode"] == "mock"
        with pytest.raises(AlreadyRunning):
            with BotRuntime(tmp_path, "openai"):
                pass
        assert status(tmp_path)["mode"] == "mock"
        first.request_stop()
        assert first.stop_requested()
    assert not status(tmp_path)["running"]
    with BotRuntime(tmp_path, "openai") as second:
        assert not second.stop_requested()
    assert not status(tmp_path)["running"]
