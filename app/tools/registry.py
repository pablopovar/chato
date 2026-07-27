from __future__ import annotations

from dataclasses import dataclass

from app.services.registry import BotConfig
from app.services.retrieval import SearchHit, search


@dataclass(frozen=True)
class ToolContext:
    bot: BotConfig


def search_knowledge(
    context: ToolContext,
    query: str,
) -> list[SearchHit]:
    return search(context.bot, query)
