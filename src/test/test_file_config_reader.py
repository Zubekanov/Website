# test/test_file_config_reader.py
# Pytests for util.fcr.file_config_reader.FileConfigReader

import json
import os
import time
import io
import pathlib
import shutil
import textwrap
import pytest

from util.fcr.file_config_reader import FileConfigReader, ConfTypes


def _write_key_value_conf(path: pathlib.Path, mapping: dict[str, str]) -> None:
	"""Write KEY_VALUE config: lines 'key = value'; allows comments and duplicates."""
	lines = []
	for k, v in mapping.items():
		lines.append(f"{k} = {v}")
	path.write_text("\n".join(lines), encoding="utf-8")


def _write_json_conf(path: pathlib.Path, mapping: dict) -> None:
	path.write_text(json.dumps(mapping), encoding="utf-8")


def _touch_future(path: pathlib.Path, seconds_ahead: int = 2) -> None:
	"""Bump mtime forward so mtime definitely changes cross-platform."""
	now = time.time()
	os.utime(path, (now + seconds_ahead, now + seconds_ahead))


def _make_tree(root: pathlib.Path, files: dict[str, bytes | str]) -> None:
	"""
	Create files under root. Dict key is relative path; value is content (str → UTF-8).
	"""
	for rel, content in files.items():
		fp = root / rel
		fp.parent.mkdir(parents=True, exist_ok=True)
		if isinstance(content, bytes):
			fp.write_bytes(content)
		else:
			fp.write_text(content, encoding="utf-8")


@pytest.fixture
def temp_project(tmp_path: pathlib.Path):
	"""
	Create a temporary project layout with:
	- a config directory housing the config file
	- a 'content' directory used as the scan root
	"""
	project = tmp_path
	(project / "util" / "fcr").mkdir(parents=True)
	(project / "config").mkdir()
	(project / "content").mkdir()
	yield project


def _make_reader_with_conf(project: pathlib.Path, conf_type: ConfTypes, root_subdir: str = "content", *, force_refresh: bool = False) -> FileConfigReader:
	"""
	Create a config file next to module (in util/fcr for tests) and instantiate the reader.
	We pass explicit config_path and conf_type to match the specified API.
	"""
	config_path = project / "util" / "fcr" / "config.conf"
	root_abs = str(project / root_subdir)
	if conf_type == ConfTypes.KEY_VALUE:
		_write_key_value_conf(config_path, {"root": root_abs})
	else:
		_write_json_conf(config_path, {"root": root_abs})
	reader = FileConfigReader(config_path=config_path, conf_type=conf_type, force_refresh=force_refresh)
	return reader


# 1. Initialisation fails without `root`
def test_init_missing_root_raises_keyerror(temp_project: pathlib.Path):
	config_path = temp_project / "util" / "fcr" / "config.conf"
	_write_key_value_conf(config_path, {"not_root": "/nope"})
	with pytest.raises(KeyError):
		FileConfigReader(config_path=config_path, conf_type=ConfTypes.KEY_VALUE, force_refresh=True)


# 2. Config caching: unchanged mtime → same parsed config content reused
def test_config_caching_same_mtime_reuse(temp_project: pathlib.Path):
	reader1 = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE)
	# Make a second instance with same config path and unchanged mtime
	reader2 = FileConfigReader(config_path=temp_project / "util" / "fcr" / "config.conf", conf_type=ConfTypes.KEY_VALUE)
	assert reader1.config == reader2.config
	assert reader1.config["root"] == reader2.config["root"]


# 3. Config reload on mtime change
def test_config_reload_on_mtime_change(temp_project: pathlib.Path):
	# First config points to 'content'
	reader1 = _make_reader_with_conf(temp_project, ConfTypes.JSON, root_subdir="content")
	# Change config to point to 'content2' and bump mtime
	(temp_project / "content2").mkdir()
	config_path = temp_project / "util" / "fcr" / "config.conf"
	_write_json_conf(config_path, {"root": str(temp_project / "content2")})
	_touch_future(config_path)
	reader2 = FileConfigReader(config_path=config_path, conf_type=ConfTypes.JSON)
	assert pathlib.Path(reader1.config["root"]) != pathlib.Path(reader2.config["root"])


# 4. Tree caching: two instances for same root do not see new files until invalidated
def test_tree_caching_no_auto_invalidate(temp_project: pathlib.Path):
	root = temp_project / "content"
	_make_tree(root, {
		"a.txt": "one",
		"sub/a.txt": "two",
	})
	reader = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE, root_subdir="content", force_refresh=True)
	# Unscoped find resolves the shallowest 'a.txt' → "one"
	assert reader.find("a.txt") == "one"

	# Add new file after initial scan
	_make_tree(root, {"zzz/a.txt": "three"})
	# By spec, find should NOT see it until tree cache is invalidated
	assert reader.find("a.txt") == "one"

	# Invalidate caches for this root
	FileConfigReader.invalidate_caches(root=str(root))
	# New instance will rescan and find remains deterministic (shallowest still "a.txt")
	reader2 = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE, root_subdir="content")
	assert reader2.find("a.txt") == "one"


# 5. Find unscoped, multiple duplicates → shallowest then lexicographic
def test_find_unscoped_deterministic(temp_project: pathlib.Path):
	root = temp_project / "content"
	_make_tree(root, {
		"a.txt": "root",
		"b/a.txt": "depth1_b",
		"a/a.txt": "depth1_a",  # same depth as 'b/a.txt', lexicographic tie-break should prefer 'a/a.txt'
		"c/d/a.txt": "depth2",
	})
	reader = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE, root_subdir="content", force_refresh=True)
	# First preference: shallowest 'a.txt' at root
	assert reader.find("a.txt") == "root"

	# Remove root copy then invalidate to rescan, to test lexicographic among equal depths
	os.remove(root / "a.txt")
	FileConfigReader.invalidate_caches(root=str(root))
	reader2 = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE, root_subdir="content")
	assert reader2.find("a.txt") == "depth1_a"  # 'a/a.txt' < 'b/a.txt'


# 6. Find with `start` narrows scope
def test_find_with_start_scopes_search(temp_project: pathlib.Path):
	root = temp_project / "content"
	_make_tree(root, {
		"a.txt": "root",
		"x/a.txt": "x",
		"y/a.txt": "y",
	})
	reader = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE, root_subdir="content", force_refresh=True)
	assert reader.find("a.txt", start="x") == "x"
	with pytest.raises(FileNotFoundError):
		reader.find("a.txt", start="nope")


# 7. Find with `name` including subdir narrows scope automatically
def test_find_with_name_subdir_scopes(temp_project: pathlib.Path):
	root = temp_project / "content"
	_make_tree(root, {
		"cfg/app.json": json.dumps({"v": 1}),
		"cfg/other/app.json": json.dumps({"v": 2}),
	})
	reader = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE, root_subdir="content", force_refresh=True)
	# Scope becomes 'cfg/'
	out = reader.find("cfg/app.json")
	assert isinstance(out, dict)
	assert out["v"] == 1


# 8. Return types: .json → dict
def test_return_type_json_dict(temp_project: pathlib.Path):
	root = temp_project / "content"
	_make_tree(root, {"data.json": json.dumps({"a": "b"})})
	reader = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE, root_subdir="content", force_refresh=True)
	res = reader.find("data.json")
	assert isinstance(res, dict)
	assert res == {"a": "b"}


# 8b. Return types: .conf → dict (KEY_VALUE rules)
def test_return_type_conf_dict(temp_project: pathlib.Path):
	root = temp_project / "content"
	conf_text = textwrap.dedent("""
		# comment
		a = 1
		b= two
		a = 3  # duplicate; last wins
	""").strip()
	_make_tree(root, {"kv.conf": conf_text})
	reader = _make_reader_with_conf(temp_project, ConfTypes.JSON, root_subdir="content", force_refresh=True)
	res = reader.find("kv.conf")
	assert isinstance(res, dict)
	assert res["a"].startswith("3")
	assert res["b"] == "two"


# 8c. Return types: .sql → list of statements
def test_return_type_sql_list(temp_project: pathlib.Path):
	root = temp_project / "content"
	sql = "CREATE TABLE t(id INT);\nINSERT INTO t VALUES (1);\n"
	_make_tree(root, {"schema.sql": sql})
	reader = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE, root_subdir="content", force_refresh=True)
	res = reader.find("schema.sql")
	assert isinstance(res, list)
	assert [s.strip().upper() for s in res if s.strip()] == [
		"CREATE TABLE T(ID INT);",
		"INSERT INTO T VALUES (1);",
	]


# 8d. Return types: other → UTF-8 string with replacement for invalid bytes
def test_return_type_text_with_replacement(temp_project: pathlib.Path):
	root = temp_project / "content"
	# Invalid UTF-8 byte sequence
	data = b"hello \xff world"
	_make_tree(root, {"notes.txt": data})
	reader = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE, root_subdir="content", force_refresh=True)
	out = reader.find("notes.txt")
	assert isinstance(out, str)
	assert "hello" in out and "world" in out
	assert "\ufffd" in out  # replacement character


# 9. Empty name → ValueError
def test_find_empty_name_raises(temp_project: pathlib.Path):
	reader = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE, root_subdir="content", force_refresh=True)
	with pytest.raises(ValueError):
		reader.find("")


# 10. No filesystem access during find: add file after scan and verify not visible
def test_no_fs_touch_during_find_after_scan(temp_project: pathlib.Path):
	root = temp_project / "content"
	_make_tree(root, {"only.txt": "v1"})
	reader = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE, root_subdir="content", force_refresh=True)
	assert reader.find("only.txt") == "v1"
	# Add a new file with a name that would be preferred if scanning occurred
	_make_tree(root, {"a/only.txt": "v2"})
	# Still returns cached resolution
	assert reader.find("only.txt") == "v1"


# 11. Force refresh on initialisation clears relevant caches
def test_force_refresh_clears_caches(temp_project: pathlib.Path):
	root1 = temp_project / "content"
	root2 = temp_project / "content_alt"
	root1.mkdir(exist_ok=True)
	root2.mkdir(exist_ok=True)
	_make_tree(root1, {"x.txt": "R1"})
	_make_tree(root2, {"x.txt": "R2"})
	# First instance points to root1
	reader1 = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE, root_subdir="content", force_refresh=True)
	assert reader1.find("x.txt") == "R1"
	# Update config to root2 and force refresh
	config_path = temp_project / "util" / "fcr" / "config.conf"
	_write_key_value_conf(config_path, {"root": str(root2)})
	_touch_future(config_path)
	reader2 = FileConfigReader(config_path=config_path, conf_type=ConfTypes.KEY_VALUE, force_refresh=True)
	assert reader2.find("x.txt") == "R2"


# 12. Security/path handling: start and name never escape root
def test_path_normalisation_prevents_escape(temp_project: pathlib.Path):
	root = temp_project / "content"
	_make_tree(root, {"safe.txt": "ok"})
	reader = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE, root_subdir="content", force_refresh=True)
	# Attempts to escape should not find anything
	with pytest.raises(FileNotFoundError):
		reader.find("safe.txt", start="../..")
	with pytest.raises(FileNotFoundError):
		reader.find("../../etc/passwd")


# 13. Candidate selection respects scope prefixing
def test_scope_prefix_matching(temp_project: pathlib.Path):
	root = temp_project / "content"
	_make_tree(root, {
		"a/a.txt": "in_a",
		"ab/a.txt": "in_ab",
	})
	reader = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE, root_subdir="content", force_refresh=True)
	# Scope 'a' should include 'a/a.txt' but not 'ab/a.txt'
	assert reader.find("a.txt", start="a") == "in_a"
	with pytest.raises(FileNotFoundError):
		reader.find("a.txt", start="a/child")  # no nested candidate exists


# 14. parse_known_types=False returns raw string even for .json/.conf
def test_parse_known_types_false_returns_text(temp_project: pathlib.Path):
	root = temp_project / "content"
	_make_tree(root, {
		"raw.json": json.dumps({"a": 1}),
		"raw.conf": "a = 1\n",
	})
	reader = _make_reader_with_conf(temp_project, ConfTypes.KEY_VALUE, root_subdir="content", force_refresh=True)
	out_json = reader.find("raw.json", parse_known_types=False)
	out_conf = reader.find("raw.conf", parse_known_types=False)
	assert isinstance(out_json, str) and out_json.strip().startswith("{")
	assert isinstance(out_conf, str) and "a = 1" in out_conf
