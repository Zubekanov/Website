import os
import sys
import fcntl

from util.config_reader import ConfigReader
import logging

def get_lock(lock_name: str):
    """
    Get a file lock for the specified lock name.
    The lock file is created in the locks directory.
    """
    lock_dir = ConfigReader.lock_dir()
    if not os.path.exists(lock_dir):
        os.makedirs(lock_dir)

    lock_file_path = lock_dir / f"{lock_name}.lock"
    
    lock_file = open(lock_file_path, 'w')
    
    # Try to acquire an exclusive lock
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except Exception:
        logging.debug(f"Lock {lock_name} is already held by another process.")
        return None