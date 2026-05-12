import threading


def assert_main_thread() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Blender scene updates must run on the main thread")
