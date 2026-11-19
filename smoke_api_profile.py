"""Fictional temporary UI only; never reads the user's profile."""
import tempfile
from unittest.mock import patch
from local_chat import LocalChat, MODE_SIMULATION
from test_local_api_profile import FAKE


def main():
    with tempfile.TemporaryDirectory() as folder, patch('requests.post', side_effect=AssertionError('network')) as network:
        app = LocalChat(data_dir=folder)
        for key, value in FAKE.items():
            app.api_values[key].set(value)
        app.save_api_button.invoke()
        assert app.api_profile.load() == FAKE
        app.api_values['model'].set('unsaved')
        app.close()
        app = LocalChat(data_dir=folder)
        try:
            app.window.update()
            assert {k: v.get() for k, v in app.api_values.items()} == FAKE
            assert app.mode.get() == MODE_SIMULATION
            app.busy = True
            app.api_values['model'].set('busy-change')
            app.save_api_button.invoke()
            assert app.api_profile.load() == FAKE
            app.busy = False
            app.forget_api_button.invoke()
            assert app.api_profile.load() is None
            assert all(not v.get() for v in app.api_values.values())
            assert app.save_api_button.winfo_ismapped()
            network.assert_not_called()
        finally:
            app.close()
        app = LocalChat(data_dir=folder)
        assert all(not v.get() for v in app.api_values.values())
        app.close()
    print('PASS: encrypted save/reopen, explicit save, busy guard, forget, no HTTP')


if __name__ == '__main__':
    main()
