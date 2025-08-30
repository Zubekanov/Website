import os
import fcntl
from pathlib import Path
import logging

from util.config_reader import ConfigReader


def get_lock_file_path(lock_name: str) -> str:
	lock_dir_path: Path = ConfigReader().lock_dir.base
	lock_dir_path.mkdir(parents=True, exist_ok=True)
	return str(lock_dir_path / f"{lock_name}.lock")


def get_lock(lock_name: str, fd: int):
	try:
		fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
		return True
	except Exception:
		logger = logging.getLogger(__name__)
		logger.debug(f"Lock {lock_name} is already held by another process.")
		return False
