"""/api/test-setups routes (P3d). Every route is `async def`: the file work is a few KB, so it runs on the
loop thread, which serialises writers, and hub.publish (loop-thread only) is called from the right thread."""
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from trex_web_gui.api.bodies import BODY_ANSWERS, BodyRefused, err, read_json_capped
from trex_web_gui.api.declared import Item, answers
from trex_web_gui.store.test_setups import (
    SetupError,
    SetupExists,
    SetupNotFound,
    TestSetupStore,
    TooManySetups,
    parse_setup,
)

PUT_LIMIT = 256 * 1024          # bytes in a PUT body


class NameBody(BaseModel):
    name: str


# What a route through `failure` declares.
SETUP_ANSWERS: tuple[Item, ...] = (
    (404, "not_found", "no such test setup"),
    (409, "exists", "a test setup of that name already exists"),
    (409, "too_many", "the test setup directory is full"),
    (422, "invalid_setup", "the test setup does not validate"),
    (500, "storage_error", "the test setup files cannot be read or written"),
)


def test_setups_router(store: TestSetupStore, publish: Callable[[dict[str, Any]], None],
                       snapshot: Callable[[], dict[str, Any]]) -> APIRouter:
    """The routes over `store`. `snapshot()` is the `test_setups` message ({type, setups, load_errors});
    every change publishes it through `publish`."""
    router = APIRouter()

    def listing() -> dict[str, Any]:
        """The GET body: the snapshot without `type`."""
        msg = snapshot()
        return {"setups": msg["setups"], "load_errors": msg["load_errors"]}

    def changed() -> None:
        """Publish the full list after a write."""
        publish(snapshot())

    def failure(e: Exception) -> JSONResponse:
        """The error response for a store exception."""
        if isinstance(e, SetupNotFound):
            return err(404, "not_found", f"No test setup {e}")
        if isinstance(e, SetupExists):
            return err(409, "exists", f"A test setup {e} already exists")
        if isinstance(e, TooManySetups):
            return err(409, "too_many", str(e))
        if isinstance(e, SetupError):
            return err(422, "invalid_setup", str(e))
        return err(500, "storage_error", f"Cannot use the test setup files: {e}")

    @router.get("/api/test-setups")
    async def list_setups() -> dict[str, Any]:
        """Every setup and the files that failed to load."""
        return listing()

    @router.get("/api/test-setups/{slug}", response_model=None, responses=answers(*SETUP_ANSWERS))
    async def get_setup(slug: str) -> dict[str, Any] | JSONResponse:
        """One setup; 404 not_found."""
        try:
            return dict(store.get(slug))
        except (SetupNotFound, OSError, ValueError) as e:
            return failure(e)

    @router.put("/api/test-setups/{slug}", response_model=None,
                responses=answers(*BODY_ANSWERS, *SETUP_ANSWERS))
    async def put_setup(slug: str, request: Request) -> dict[str, Any] | JSONResponse:
        """Create or replace the setup. 404 for a bad slug, 411/413/422 from the body read, 422
        invalid_setup (naming the field, or "slug does not match name"), 409 too_many."""
        try:
            store.path_of(slug)                       # 404 for a bad slug before the body is read
            raw = await read_json_capped(request, PUT_LIMIT)
            setup = parse_setup(raw)
            if setup["slug"] != slug:
                return err(422, "invalid_setup", "slug does not match name")
            saved = store.save(setup)
        except BodyRefused as e:
            return e.response
        except (SetupNotFound, SetupError, TooManySetups, OSError) as e:
            return failure(e)
        changed()
        return dict(saved)

    @router.delete("/api/test-setups/{slug}", response_model=None, responses=answers(*SETUP_ANSWERS))
    async def delete_setup(slug: str) -> dict[str, Any] | JSONResponse:
        """Delete the setup; 404 not_found."""
        try:
            store.delete(slug)
        except (SetupNotFound, OSError) as e:
            return failure(e)
        changed()
        return listing()

    @router.post("/api/test-setups/{slug}/rename", response_model=None, responses=answers(*SETUP_ANSWERS))
    async def rename_setup(slug: str, b: NameBody) -> dict[str, Any] | JSONResponse:
        """Rename the setup (its slug follows the name); 404, 409 exists, 422 invalid_setup."""
        try:
            saved = store.rename(slug, b.name)
        except (SetupNotFound, SetupExists, TooManySetups, SetupError, OSError, ValueError) as e:
            return failure(e)
        changed()
        return dict(saved)

    @router.post("/api/test-setups/{slug}/duplicate", response_model=None, responses=answers(*SETUP_ANSWERS))
    async def duplicate_setup(slug: str, b: NameBody) -> dict[str, Any] | JSONResponse:
        """Copy the setup under a new name; 404, 409 exists or too_many, 422 invalid_setup."""
        try:
            saved = store.duplicate(slug, b.name)
        except (SetupNotFound, SetupExists, TooManySetups, SetupError, OSError, ValueError) as e:
            return failure(e)
        changed()
        return dict(saved)

    return router
