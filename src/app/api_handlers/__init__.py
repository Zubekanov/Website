from __future__ import annotations

from app.api_context import ApiContext


def register_all(api, ctx: ApiContext) -> None:
	from app.api_handlers import (
		admin,
		auth,
		discord,
		integrations,
		metrics,
		minecraft,
		ping,
		popugame,
		stocks,
	)

	ping.register(api, ctx)
	auth.register(api, ctx)
	metrics.register(api, ctx)
	integrations.register(api, ctx)
	minecraft.register(api, ctx)
	discord.register(api, ctx)
	admin.register(api, ctx)
	popugame.register(api, ctx)
	stocks.register(api, ctx)
