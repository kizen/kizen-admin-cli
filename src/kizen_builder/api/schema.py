"""Schema lookups backing the filtering DSL.

:class:`SchemaClient` implements the small read-only surface that
``kizen_builder.filtering`` needs to resolve api_names to UUIDs: objects,
fields (with options), dynamictag/contact tags, subscription lists, and
agentic workflows. Every lookup is cached for the life of the instance —
schema data changes rarely, and one filter expression can trigger many
lookups. A name lookup that misses an object or field list cached by an
earlier call re-fetches that list once, so an object or field created after
the cache filled still resolves.

The endpoints and response handling mirror the internal ``kznclient``
library the DSL was ported from; notably fields come from
``/fields/settings-search`` (the endpoint the UI's filter builder uses),
which includes ``is_default`` and inline ``options``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from kizen_builder.api.client import KizenClient
from kizen_builder.config import load_env_config

CONTACTS_IDENTIFIER = "client_client"


def _is_uuid(value: Any) -> bool:
    try:
        UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


class SchemaClient:
    """Cached, read-only schema lookups for one environment."""

    def __init__(self, client: KizenClient) -> None:
        self._client = client
        self._cache: dict[tuple, Any] = {}

    @classmethod
    def from_env(cls) -> SchemaClient:
        return cls(KizenClient(load_env_config()))

    def _cached(self, key: tuple, fn) -> Any:
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def _find_cached(self, key: tuple, fetch, matches) -> tuple[Any, list]:
        """The first item in the list cached at ``key`` that ``matches``, and
        that list. A miss against a list cached by an earlier call re-fetches
        it once; a list this call fetched is already fresh."""
        was_cached = key in self._cache
        items = self._cached(key, fetch)
        match = next((i for i in items if matches(i)), None)
        if match is None and was_cached:
            items = self._cache[key] = fetch()
            match = next((i for i in items if matches(i)), None)
        return match, items

    def _all_pages(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        items: list[dict] = []
        next_path: str | None = path
        next_params = params
        while next_path:
            resp = self._client.get(next_path, params=next_params)
            next_params = None  # `next` URLs carry their own querystring
            if isinstance(resp, list):
                items.extend(resp)
                break
            items.extend(resp.get("results", []))
            nxt = resp.get("next")
            if nxt:
                parts = urlsplit(nxt)
                next_path = parts.path + (f"?{parts.query}" if parts.query else "")
            else:
                next_path = None
        return items

    # -- objects ----------------------------------------------------------

    def custom_object(self, api_name: str) -> dict[str, Any]:
        """Resolve an object api_name (or UUID, or `client_client`) to its
        object dict. `name` in the result is the api_name."""
        if _is_uuid(api_name):
            return self._cached(
                ("object", api_name),
                lambda: self._client.get(f"/api/custom-objects/{api_name}"),
            )
        match, objects = self._find_cached(
            ("objects",), self._list_objects, lambda o: o.get("name") == api_name
        )
        if match is None:
            available = sorted(o.get("name") or "" for o in objects)
            raise LookupError(f"object '{api_name}' not found. Available: {available}")
        return match

    def _list_objects(self) -> list[dict[str, Any]]:
        # custom_only=false so client_client (contacts) is included
        return self._all_pages("/api/custom-objects", params={"custom_only": "false"})

    # -- fields -----------------------------------------------------------

    def get_field(self, obj: str, field: str) -> dict[str, Any] | None:
        """Find a field by api_name or UUID on an object (api_name or UUID).

        Returns the raw settings-search field dict — includes ``id``,
        ``name`` (api_name), ``field_type``, ``is_default``, and inline
        ``options`` — or None if not found.
        """
        obj_id = obj if _is_uuid(obj) else self.custom_object(obj)["id"]
        match, _ = self._find_cached(
            ("fields", obj_id),
            lambda: self._client.get(
                f"/api/custom-objects/{obj_id}/fields/settings-search"
            ),
            lambda f: field == f.get("id") or field == f.get("name"),
        )
        return match

    # -- tags ---------------------------------------------------------------

    def get_field_tags(
        self, object_id: str, field_id: str, search: str = ""
    ) -> list[dict[str, Any]]:
        """Options for a dynamictags field (their metadata has no options).

        Custom objects serve tags from /pipelines/...; contacts from
        /client/fields/....
        """
        obj = self.custom_object(object_id)
        if obj.get("name") == CONTACTS_IDENTIFIER:
            path = f"/api/client/fields/{field_id}/tags"
        else:
            path = f"/api/pipelines/{object_id}/fields/{field_id}/tags"
        return self._cached(
            ("field_tags", object_id, field_id, search),
            lambda: self._all_pages(
                path,
                params={
                    "search": search,
                    "ordering": "name",
                    "page_size": 100,
                    "page": 1,
                },
            ),
        )

    def get_contact_tags(self, search: str = "") -> list[dict[str, Any]]:
        """Tags on the Contacts object (the UI's "Tags" filter category)."""
        field = self.get_field(CONTACTS_IDENTIFIER, "tags")
        if field is None:
            raise LookupError("contacts object has no 'tags' field")
        return self._cached(
            ("contact_tags", search),
            lambda: self._all_pages(
                f"/api/client/fields/{field['id']}/tags",
                params={
                    "search": search,
                    "ordering": "name",
                    "page_size": 100,
                    "page": 1,
                },
            ),
        )

    # -- other filterable entities ------------------------------------------

    def get_subscription_lists(self) -> list[dict[str, Any]]:
        return self._cached(
            ("subscription_lists",),
            lambda: self._all_pages("/api/subscription-list"),
        )

    def get_agentic_workflows(
        self, object_id: str, search: str = ""
    ) -> list[dict[str, Any]]:
        return self._cached(
            ("workflows", object_id, search),
            lambda: self._all_pages(
                "/api/automation2/automations",
                params={
                    "search": search,
                    "custom_object_id": object_id,
                    "page_size": 100,
                    "page": 1,
                },
            ),
        )
