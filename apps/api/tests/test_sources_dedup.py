"""Source deduplication: canonical identity, ownership scope, and races.

These tests cover the Phase 4 completion gate for source deduplication. Two
things are worth stating up front, because they are the parts most likely to be
got wrong later:

* Deduplication keys on a **canonical URL only**, never on a similar title. Two
  different papers with near-identical titles stay separate, and there is a test
  below that would fail if title matching were ever introduced.
* The guarantee is enforced by a **partial unique index**, not by a pre-insert
  lookup. A lookup-based check cannot survive two concurrent inserts; the index
  can. ``test_concurrent_insert_of_the_same_url_yields_one_record`` exists to
  prove the constraint is really doing the work.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI

from mafhum_api.services.sources.canonical import canonicalize_url, urls_are_equivalent
from tests.conftest import AuthedClient

PDF_MIME = "application/pdf"


class TestCanonicalization:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            # Fragments never reach the server.
            ("https://ex.dz/paper#page=3", "https://ex.dz/paper"),
            # Scheme and host are case-insensitive.
            ("HTTPS://EX.DZ/Paper", "https://ex.dz/Paper"),
            # The trailing dot is the DNS root label, not part of the name.
            ("https://ex.dz./paper", "https://ex.dz/paper"),
            # Default ports denote the same endpoint.
            ("https://ex.dz:443/paper", "https://ex.dz/paper"),
            ("http://ex.dz:80/paper", "http://ex.dz/paper"),
            # An empty path canonicalizes to the root.
            ("https://ex.dz", "https://ex.dz/"),
        ],
    )
    def test_safe_normalizations(self, url: str, expected: str) -> None:
        assert canonicalize_url(url) == expected

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            # Different transports, potentially different servers.
            ("http://ex.dz/paper", "https://ex.dz/paper"),
            # Stripping "www." is a classic false merge.
            ("https://www.ex.dz/paper", "https://ex.dz/paper"),
            # Paths are case-sensitive.
            ("https://ex.dz/Paper", "https://ex.dz/paper"),
            # The query is preserved verbatim: it often selects the resource.
            ("https://ex.dz/search?q=a", "https://ex.dz/search?q=b"),
            # Non-default ports are distinct endpoints.
            ("https://ex.dz:8443/paper", "https://ex.dz/paper"),
            # A trailing slash is a different resource on many servers.
            ("https://ex.dz/a", "https://ex.dz/a/"),
        ],
    )
    def test_unsafe_equivalences_are_not_asserted(self, a: str, b: str) -> None:
        assert not urls_are_equivalent(a, b)

    @pytest.mark.parametrize(
        "url",
        [
            None,
            "",
            "   ",
            "not a url",
            "ftp://ex.dz/paper",  # Not an http(s) source.
            "https://user:pass@ex.dz/paper",  # Credentials: unsafe as an identity.
            "https://" + "a" * 2100 + ".dz/paper",  # Over the length bound.
        ],
    )
    def test_unusable_urls_have_no_identity(self, url: str | None) -> None:
        assert canonicalize_url(url) is None


class TestSourceDeduplication:
    def _create(self, user: AuthedClient, url: str, title: str = "مصدر") -> dict[str, object]:
        response = user.post("/api/v1/sources", json={"url": url, "title": title})
        assert response.status_code == 201, response.text
        return response.json()

    def test_duplicate_url_returns_the_existing_record(self, user_a: AuthedClient) -> None:
        first = self._create(user_a, "https://ex.dz/paper#page=3", "الأول")
        second = self._create(user_a, "HTTPS://EX.DZ:443/paper", "الثاني")

        assert second["id"] == first["id"]
        listing = user_a.get("/api/v1/sources")
        assert listing.status_code == 200
        assert len(listing.json()["items"]) == 1

    def test_original_url_and_provenance_survive_deduplication(self, user_a: AuthedClient) -> None:
        """Deduplicating must not rewrite what the user actually submitted."""
        created = self._create(user_a, "https://ex.dz/paper#page=3", "الأول")
        self._create(user_a, "https://ex.dz/paper", "الثاني")

        fetched = user_a.get(f"/api/v1/sources/{created['id']}")
        assert fetched.status_code == 200
        body = fetched.json()
        # The first submission wins, unmodified.
        assert body["url"] == "https://ex.dz/paper#page=3"
        assert body["title"] == "الأول"
        assert body["provenance"] == "user_supplied"
        assert body["verification_status"] == "user_asserted"

    def test_the_same_url_for_two_owners_stays_separate(
        self, two_users: tuple[AuthedClient, AuthedClient]
    ) -> None:
        """Deduplication is per owner. Another user's catalog is not shared."""
        user_a, user_b = two_users
        first = self._create(user_a, "https://ex.dz/paper")
        second = self._create(user_b, "https://ex.dz/paper")

        assert first["id"] != second["id"]
        # And neither user can see the other's record.
        assert user_b.get(f"/api/v1/sources/{first['id']}").status_code == 404
        assert user_a.get(f"/api/v1/sources/{second['id']}").status_code == 404

    def test_similar_titles_are_not_merged(self, user_a: AuthedClient) -> None:
        """Distinct documents with near-identical titles are distinct sources."""
        first = self._create(user_a, "https://ex.dz/a", "قانون المالية لسنة 2024")
        second = self._create(user_a, "https://ex.dz/b", "قانون المالية لسنة 2024")
        assert first["id"] != second["id"]
        assert len(user_a.get("/api/v1/sources").json()["items"]) == 2

    def test_sources_without_a_canonical_url_do_not_collide(self, user_a: AuthedClient) -> None:
        """No identity means no duplicate. NULL must never equal NULL here."""
        first = user_a.post("/api/v1/sources", json={"title": "مصدر بلا رابط"})
        second = user_a.post("/api/v1/sources", json={"title": "مصدر بلا رابط 2"})
        assert first.status_code == 201 and second.status_code == 201
        assert first.json()["id"] != second.json()["id"]

    def test_concurrent_insert_of_the_same_url_yields_one_record(
        self, user_a: AuthedClient, app: FastAPI
    ) -> None:
        """The unique index, not an application check, is what prevents duplicates.

        Several transactions race to insert the same canonical URL. Only one can
        commit; the rest must resolve to that same record instead of erroring.
        """
        from tests.test_api_concurrency import in_parallel

        url = "https://ex.dz/race"

        def attempt() -> object:
            from mafhum_api.services.sources.service import SourceDraft

            with app.state.context.database.session() as session:
                record = app.state.context.sources.create(
                    session,
                    owner_id=user_a.user_id,
                    draft=SourceDraft(url=url, title="سباق"),
                )
                session.commit()
                return record.id

        ids = [i for i in in_parallel(6, attempt) if isinstance(i, uuid.UUID)]
        assert len(ids) == 6, "every caller must succeed"
        assert len(set(ids)) == 1, f"expected one source, got {sorted(set(map(str, ids)))}"
