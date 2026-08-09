# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""The /ws/chat endpoint — persistence, cross-session memory, and the turn loop.

Chat is the app's front door, so the failures here are the ones a user meets
first. Three things carry the weight:

  * **Persistence.** Every turn is written to the DB, the session counter
    tracks it, and a "New chat" auto-titles from the user's first message —
    a sidebar full of "New chat" is unusable.
  * **Cross-session context.** A new chat still loads the tail of the previous
    session so the planner knows what the user was doing, which is why "carry
    on with that" works at all. Long messages are truncated so the context
    window isn't spent on one paste.
  * **The turn loop.** A malformed frame, an unknown message type or a planner
    that raises must not kill the socket — a dropped connection mid-answer is
    the most visible failure the product has.

The planner and the AI layer are stubbed; nothing here calls a model.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.api import chat as CHAT
from backend.database import AsyncSessionLocal, init_db


@pytest.fixture(scope="module", autouse=True)
def _schema():
    asyncio.run(init_db())


async def _session(title="New chat", user_id="local", updated=None):
    from backend.models.chat_session import ChatSession
    async with AsyncSessionLocal() as db:
        s = ChatSession(user_id=user_id, title=title, message_count=0)
        if updated:
            s.updated_at = updated
        db.add(s)
        await db.commit()
        return s.id


async def _session_row(sid):
    from backend.models.chat_session import ChatSession
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(ChatSession).where(ChatSession.id == sid))).scalar_one()


async def _messages(sid):
    from backend.models.chat_session import ChatMessage
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == sid)
            .order_by(ChatMessage.created_at.asc()))).scalars().all()


# ── persistence ─────────────────────────────────────────────────────────────

class TestPersistMessage:
    def test_a_message_is_stored_and_counted(self):
        async def go():
            sid = await _session()
            await CHAT._persist_message(sid, "user", "hello there")
            return await _messages(sid), await _session_row(sid)
        msgs, session = asyncio.run(go())
        assert len(msgs) == 1 and msgs[0].content == "hello there"
        assert session.message_count == 1

    def test_a_new_chat_auto_titles_from_the_first_user_message(self):
        """A sidebar full of "New chat" is unusable."""
        async def go():
            sid = await _session()
            await CHAT._persist_message(sid, "user", "How do I make a short?")
            return await _session_row(sid)
        assert asyncio.run(go()).title == "How do I make a short?"

    def test_a_very_long_first_message_is_truncated_into_the_title(self):
        async def go():
            sid = await _session()
            await CHAT._persist_message(sid, "user", "x" * 500)
            return await _session_row(sid)
        title = asyncio.run(go()).title
        assert len(title) <= 84 and title.endswith("...")

    def test_an_assistant_message_never_titles_the_session(self):
        async def go():
            sid = await _session()
            await CHAT._persist_message(sid, "assistant", "Sure, here's how")
            return await _session_row(sid)
        assert asyncio.run(go()).title == "New chat"

    def test_an_already_titled_session_is_not_renamed(self):
        async def go():
            sid = await _session(title="Cooking ideas")
            await CHAT._persist_message(sid, "user", "something else entirely")
            return await _session_row(sid)
        assert asyncio.run(go()).title == "Cooking ideas"

    def test_a_rich_card_is_stored_with_its_payload(self):
        """Tool results and galleries rehydrate from this on reload."""
        async def go():
            sid = await _session()
            await CHAT._persist_message(
                sid, "assistant", msg_type="scout_results",
                data_json=json.dumps({"total": 3}))
            return await _messages(sid)
        msgs = asyncio.run(go())
        assert msgs[0].msg_type == "scout_results"
        assert json.loads(msgs[0].data_json)["total"] == 3

    def test_persisting_to_a_vanished_session_does_not_raise(self):
        """The user can delete a session mid-turn."""
        asyncio.run(CHAT._persist_message("no-such-session", "user", "hi"))


class TestLoadHistory:
    def test_it_returns_the_turns_in_order(self):
        async def go():
            sid = await _session()
            for role, text in (("user", "first"), ("assistant", "second"),
                               ("user", "third")):
                await CHAT._persist_message(sid, role, text)
            return await CHAT._load_session_history(sid)
        hist = asyncio.run(go())
        assert [m["content"] for m in hist] == ["first", "second", "third"]

    def test_rich_cards_without_text_are_excluded_from_ai_context(self):
        """A card carries no prose — feeding an empty turn to the model wastes
        context and confuses the transcript."""
        async def go():
            sid = await _session()
            await CHAT._persist_message(sid, "user", "show me")
            await CHAT._persist_message(sid, "assistant", msg_type="gallery",
                                        data_json="{}")
            return await CHAT._load_session_history(sid)
        assert len(asyncio.run(go())) == 1

    def test_an_empty_session_has_no_history(self):
        async def go():
            return await CHAT._load_session_history(await _session())
        assert asyncio.run(go()) == []


class TestPreviousSessionContext:
    """A new chat still knows what the last one was about — this is why
    "carry on with that" works."""

    def test_it_loads_the_tail_of_the_previous_session(self):
        async def go():
            old = await _session(title="Old", updated=datetime.utcnow() - timedelta(hours=1))
            await CHAT._persist_message(old, "user", "find cooking videos")
            await CHAT._persist_message(old, "assistant", "here are three")
            new = await _session(title="New chat")
            return await CHAT._load_previous_session_context("local", new)
        ctx = asyncio.run(go())
        assert ctx and any("cooking" in m["content"] for m in ctx)

    def test_it_returns_chronological_order(self):
        async def go():
            old = await _session(updated=datetime.utcnow() - timedelta(hours=2))
            await CHAT._persist_message(old, "user", "AAA")
            await CHAT._persist_message(old, "assistant", "BBB")
            new = await _session()
            return await CHAT._load_previous_session_context("local", new)
        ctx = asyncio.run(go())
        assert ctx[0]["content"].startswith("AAA")

    def test_long_messages_are_truncated(self):
        """One pasted transcript must not eat the context window."""
        async def go():
            old = await _session(updated=datetime.utcnow() - timedelta(hours=3))
            await CHAT._persist_message(old, "user", "y" * 2000)
            new = await _session()
            return await CHAT._load_previous_session_context("local", new)
        ctx = asyncio.run(go())
        assert ctx and len(ctx[0]["content"]) < 400
        assert ctx[0]["content"].endswith("...")

    def test_the_current_session_is_never_its_own_context(self):
        async def go():
            sid = await _session()
            await CHAT._persist_message(sid, "user", "only message")
            return await CHAT._load_previous_session_context("local", sid)
        ctx = asyncio.run(go())
        assert not any("only message" in m["content"] for m in ctx)

    def test_a_user_with_no_history_gets_nothing(self):
        async def go():
            return await CHAT._load_previous_session_context(
                "brand-new-user", None)
        assert asyncio.run(go()) == []

    def test_an_empty_previous_session_yields_nothing(self):
        async def go():
            await _session(user_id="empty-user",
                           updated=datetime.utcnow() - timedelta(hours=1))
            new = await _session(user_id="empty-user")
            return await CHAT._load_previous_session_context("empty-user", new)
        assert asyncio.run(go()) == []


class TestProfileUpdate:
    def test_it_runs_when_the_profile_is_stale(self, monkeypatch):
        ran = {}

        async def should(uid):
            return True

        async def update(uid):
            ran["yes"] = True
        monkeypatch.setattr(CHAT.intelligence, "should_update_profile", should)
        monkeypatch.setattr(CHAT.intelligence, "update_profile_with_ai", update)
        asyncio.run(CHAT._maybe_update_profile("local"))
        assert ran.get("yes")

    def test_it_is_skipped_when_fresh(self, monkeypatch):
        async def should(uid):
            return False

        async def boom(uid):
            raise AssertionError("must not re-generate a fresh profile")
        monkeypatch.setattr(CHAT.intelligence, "should_update_profile", should)
        monkeypatch.setattr(CHAT.intelligence, "update_profile_with_ai", boom)
        asyncio.run(CHAT._maybe_update_profile("local"))

    def test_a_failure_is_non_critical(self, monkeypatch):
        """Personalisation is a bonus; it must never break a chat turn."""
        async def boom(uid):
            raise RuntimeError("profile service down")
        monkeypatch.setattr(CHAT.intelligence, "should_update_profile", boom)
        asyncio.run(CHAT._maybe_update_profile("local"))   # must not raise
