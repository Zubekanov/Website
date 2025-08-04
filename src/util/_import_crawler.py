import os
import ast
import argparse

def find_imports_in_file(path):
	"""Parse a .py file and return a list of its import statements."""
	imports = []
	try:
		with open(path, 'r', encoding='utf-8') as f:
			tree = ast.parse(f.read(), filename=path)
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				for alias in node.names:
					imports.append(f"import {alias.name}")
			elif isinstance(node, ast.ImportFrom):
				module = node.module or ""
				for alias in node.names:
					imports.append(f"from {module} import {alias.name}")
	except (SyntaxError, UnicodeDecodeError):
		# skip files that can't be parsed
		pass
	return imports

def crawl_directory(base_dir):
	"""Recursively walk through base_dir, collecting imports from .py files."""
	results = {}
	for root, _, files in os.walk(base_dir):
		for name in files:
			if name.endswith('.py'):
				full_path = os.path.join(root, name)
				imports = find_imports_in_file(full_path)
				if imports:
					results[full_path] = imports
	return results

def main():
	parser = argparse.ArgumentParser(
		description="Recursively list all import statements in .py files."
	)
	parser.add_argument(
		"directory",
		help="Base directory to crawl"
	)
	args = parser.parse_args()

	results = crawl_directory(args.directory)
	for path, imports in results.items():
		print(f"\n{path}:")
		imports.sort()
		for stmt in imports:
			print(f"  {stmt}")

if __name__ == "__main__":
	main()