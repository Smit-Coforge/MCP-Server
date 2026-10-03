import os
import tempfile


def pytest_configure(config):
    directory = tempfile.mkdtemp(prefix="review-queue-")
    path = os.path.join(directory, "review_queue.json")
    os.environ["REVIEW_QUEUE_PATH"] = path
    config._review_queue_dir = directory


def pytest_unconfigure(config):
    directory = getattr(config, "_review_queue_dir", "")
    if not directory:
        return
    for name in os.listdir(directory):
        os.unlink(os.path.join(directory, name))
    os.rmdir(directory)
