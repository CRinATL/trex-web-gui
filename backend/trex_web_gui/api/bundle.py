"""/api/settings/bundle routes (P3d): export, preview and apply of the settings bundle. Every route is
`async def`: the file work is a few KB, so it runs on the loop thread, which serialises writers, and
hub.publish (loop-thread only) is called from the right thread."""
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from trex_web_gui.api.bodies import BODY_ANSWERS, BodyRefused, err, read_json_capped
from trex_web_gui.api.declared import answers
from trex_web_gui.store import bundle as bundles

BODY_LIMIT = 4 * 1024 * 1024          # bytes in a preview or apply body


def bundle_router(stores: bundles.Stores, app_version: str, publish: Callable[[dict[str, Any]], None],
                  settings_json: Callable[[], dict[str, Any]], labels_json: Callable[[], dict[str, Any]],
                  setups_json: Callable[[], dict[str, Any]]) -> APIRouter:
    """The routes over `stores`. After an apply, `publish` gets the `settings`, `port_labels` and
    `test_setups` messages built from the three snapshot callables (`setups_json()` carries its own type)."""
    router = APIRouter()

    def publish_state() -> None:
        """Publish the three states an apply can change."""
        publish({"type": "settings", **settings_json()})
        publish({"type": "port_labels", **labels_json()})
        publish(setups_json())

    @router.get("/api/settings/bundle")
    async def get_bundle() -> dict[str, Any]:
        """The bundle for the current settings, port labels and test setups."""
        return bundles.export(stores, app_version)

    @router.post("/api/settings/bundle/preview", response_model=None,
                 responses=answers(*BODY_ANSWERS, (422, "bundle")))
    async def preview_bundle(request: Request) -> dict[str, Any] | JSONResponse:
        """What applying the body (a bundle) would do. 411/413/422 from the body read, 422
        unsupported_bundle for a wrong format or version."""
        try:
            return bundles.preview(await read_json_capped(request, BODY_LIMIT), stores)
        except BodyRefused as e:
            return e.response
        except bundles.BundleError as e:
            return err(422, e.code, str(e))

    @router.post("/api/settings/bundle/apply", response_model=None,
                 responses=answers(
                     *BODY_ANSWERS,
                     (422, "bundle"),
                     (500, "storage_error", "a file cannot be written; `written` and `failed` list them")))
    async def apply_bundle(request: Request) -> dict[str, Any] | JSONResponse:
        """Apply `{bundle, modes}`; modes maps a section to replace, merge or skip. 411/413/422 from the
        body read, 422 bad_request, unsupported_bundle or invalid_bundle (naming the section), 500
        storage_error with {written, failed} when a file cannot be written (the state is published too)."""
        try:
            body = await read_json_capped(request, BODY_LIMIT)
            if (not isinstance(body, dict) or not isinstance(body.get("modes"), dict)
                    or "bundle" not in body):
                return err(422, "bad_request", "expected {bundle, modes}")
            result = bundles.apply(body["bundle"], body["modes"], stores)
        except BodyRefused as e:
            return e.response
        except bundles.BundleError as e:
            return err(422, e.code, str(e))
        except bundles.BundleWriteError as e:
            publish_state()                       # some files did change
            return JSONResponse({"error": "storage_error", "message": str(e), "written": e.written,
                                 "failed": e.failed}, status_code=500)
        publish_state()
        return result

    return router
