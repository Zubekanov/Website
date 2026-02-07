from __future__ import annotations

from sql.psql_interface import PSQLInterface

def bootstrap_single_admin() -> tuple[bool, str]:
	interface = PSQLInterface()
	rows, _ = interface.client.get_rows_with_filters(
		"users",
		page_limit=2,
		page_num=0,
	)

	if not rows:
		return False, "No users found."
	if len(rows) > 1:
		return False, "Multiple users found; refusing to promote."

	user_id = rows[0].get("id")
	if not user_id:
		return False, "Single user has no id; cannot promote."

	return interface.promote_user_to_admin(user_id, note="bootstrapped admin")


if __name__ == "__main__":
	ok, message = bootstrap_single_admin()
	print(message)
	raise SystemExit(0 if ok else 1)
