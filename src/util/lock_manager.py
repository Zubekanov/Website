import os
import sys
import fcntl

from util.config_reader import ConfigReader
import logging

def get_lock_file_path(lock_name: str) -> str:
    lock_dir = ConfigReader.lock_dir()
    if not os.path.exists(lock_dir):
        os.makedirs(lock_dir)
    
    return os.path.join(lock_dir, f"{lock_name}.lock")

def get_lock(lock_name: str, fd: int):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except Exception:
        logging.debug(f"Lock {lock_name} is already held by another process.")
        return False