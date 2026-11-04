from __future__ import annotations

from pathlib import Path

import pytest

from kestrel.research import SECTIONS, ResearchError, ResearchStore

FILLED = {k: f"{k} text" for k in SECTIONS}


@pytest.fixture
def store(tmp_path: Path) -> ResearchStore:
    return ResearchStore(tmp_path)


def specified(store: ResearchStore, name: str = "BTC trend") -> str:
    slug = store.create(name).slug
    store.edit(slug, 1, FILLED, "ma-trend")
    store.freeze(slug, 1)
    return slug


def test_create_starts_as_an_idea_with_an_empty_draft(store: ResearchStore) -> None:
    s = store.create("BTC Trend (4h)")
    assert s.slug == "btc-trend-4h"
    assert s.status == "idea"
    assert s.latest.version == 1
    assert not s.latest.frozen
    assert set(s.latest.sections) == set(SECTIONS)


def test_slugs_are_unique(store: ResearchStore) -> None:
    assert store.create("Trend").slug == "trend"
    assert store.create("Trend").slug == "trend-2"


def test_blank_name_is_refused(store: ResearchStore) -> None:
    with pytest.raises(ResearchError, match="name"):
        store.create("   ")


def test_everything_persists_across_instances(store: ResearchStore, tmp_path: Path) -> None:
    slug = specified(store)
    again = ResearchStore(tmp_path).get(slug)
    assert again.latest.sections == FILLED
    assert again.latest.frozen
    assert [h.status for h in again.history] == ["idea", "specified"]


def test_drafts_can_be_edited(store: ResearchStore) -> None:
    slug = store.create("Trend").slug
    store.edit(slug, 1, {"hypothesis": "Trends persist"}, None)
    assert store.get(slug).latest.sections["hypothesis"] == "Trends persist"


@pytest.mark.parametrize(
    ("sections", "impl", "message"),
    [({"vibes": "x"}, None, "unknown section"), ({}, "magic", "unknown implementation")],
)
def test_edit_validates(
    store: ResearchStore, sections: dict[str, str], impl: str | None, message: str
) -> None:
    slug = store.create("Trend").slug
    with pytest.raises(ResearchError, match=message):
        store.edit(slug, 1, sections, impl)


def test_freezing_needs_every_section_and_an_implementation(store: ResearchStore) -> None:
    slug = store.create("Trend").slug
    store.edit(slug, 1, {**FILLED, "falsification": "  "}, "ma-trend")
    with pytest.raises(ResearchError, match="What would prove it wrong"):
        store.freeze(slug, 1)
    store.edit(slug, 1, FILLED, None)
    with pytest.raises(ResearchError, match="implementation"):
        store.freeze(slug, 1)


def test_freezing_specifies_the_strategy(store: ResearchStore) -> None:
    slug = specified(store)
    s = store.get(slug)
    assert s.status == "specified"
    assert s.latest.frozen_at is not None


def test_a_frozen_note_cannot_be_edited(store: ResearchStore) -> None:
    slug = specified(store)
    with pytest.raises(ResearchError, match="frozen; start a new version"):
        store.edit(slug, 1, {"hypothesis": "rewritten after seeing results"}, "ma-trend")
    with pytest.raises(ResearchError, match="frozen"):
        store.freeze(slug, 1)


def test_new_version_needs_a_frozen_predecessor_and_a_reason(store: ResearchStore) -> None:
    slug = store.create("Trend").slug
    with pytest.raises(ResearchError, match="still a draft"):
        store.new_version(slug, "because")
    store.edit(slug, 1, FILLED, "ma-trend")
    store.freeze(slug, 1)
    with pytest.raises(ResearchError, match="say why"):
        store.new_version(slug, " ")
    v2 = store.new_version(slug, "Costs were higher than expected")
    assert v2.version == 2
    assert v2.sections == FILLED  # starts from v1
    assert v2.change_reason == "Costs were higher than expected"
    assert not v2.frozen
    assert store.note(slug, 1).frozen  # v1 is untouched


def test_only_the_latest_version_changes(store: ResearchStore) -> None:
    slug = specified(store)
    store.new_version(slug, "Tighter rules")
    with pytest.raises(ResearchError, match="only the latest version"):
        store.edit(slug, 1, {"hypothesis": "x"}, "ma-trend")


def test_rejection_needs_a_reason_and_is_final(store: ResearchStore) -> None:
    slug = specified(store)
    with pytest.raises(ResearchError, match="record why"):
        store.reject(slug, "")
    store.reject(slug, "Edge smaller than costs")
    s = store.get(slug)
    assert s.status == "rejected"
    assert s.history[-1].reason == "Edge smaller than costs"
    with pytest.raises(ResearchError, match="rejected"):
        store.new_version(slug, "try again")
    with pytest.raises(ResearchError, match="rejected"):
        store.require_testable(slug)


def test_rejected_drafts_cannot_be_edited(store: ResearchStore) -> None:
    slug = store.create("Trend").slug
    store.reject(slug, "Not worth testing")
    with pytest.raises(ResearchError, match="rejected"):
        store.edit(slug, 1, FILLED, "ma-trend")


def test_drafts_are_not_testable(store: ResearchStore) -> None:
    slug = store.create("Trend").slug
    with pytest.raises(ResearchError, match="before the results are seen"):
        store.require_testable(slug)
    with pytest.raises(ResearchError, match="no research strategy"):
        store.require_testable("missing")


def test_first_run_marks_backtested_once(store: ResearchStore) -> None:
    slug = specified(store)
    store.mark_backtested(slug, 1)
    store.mark_backtested(slug, 1)
    assert [h.status for h in store.get(slug).history] == ["idea", "specified", "backtested"]


def test_conversations_are_kept(store: ResearchStore) -> None:
    slug = store.create("Trend").slug
    assert store.chat(slug) == []
    store.append_chat(
        slug, [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    )
    store.append_chat(slug, [{"role": "user", "content": "more"}])
    assert [m["content"] for m in store.chat(slug)] == ["hi", "hello", "more"]


@pytest.mark.parametrize("bad", ["../etc", "Upper", "a/b", ""])
def test_unsafe_slugs_are_not_found(store: ResearchStore, bad: str) -> None:
    with pytest.raises(KeyError):
        store.get(bad)


def test_list_is_oldest_first(store: ResearchStore) -> None:
    store.create("First")
    store.create("Second")
    assert [s.slug for s in store.strategies()] == ["first", "second"]


def test_freezing_a_later_version_keeps_the_status(store: ResearchStore) -> None:
    slug = specified(store)
    store.mark_backtested(slug, 1)
    store.new_version(slug, "Lower turnover")
    store.freeze(slug, 2)
    assert store.get(slug).status == "backtested"  # never moves backwards


def test_missing_note_version(store: ResearchStore) -> None:
    slug = store.create("Trend").slug
    with pytest.raises(KeyError):
        store.note(slug, 7)
