"""B-21: the static UI build (`apps/sentra-web/dist`) is served by sentra-api itself (PRD 6.4, one CF app).

The build is a single-page app: a path without a file (`/cases/<id>`, `/whatif`) answers `index.html` and the browser
router takes over. The API prefixes never do: an unknown `/api/...` stays a JSON `404 NOT_FOUND`, not a 200 HTML page.
"""

from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

INDEX = "index.html"
ASSETS = "assets"  # Vite writes content-hashed names here, so a file never changes under its name
API_PREFIXES = frozenset({"api", "internal", "mock-s4"})


def default_web_dist() -> Path:
    """`<root>/apps/sentra-web/dist`; the CF bundle (prepare_cf_bundle.ps1) keeps the same layout as the repository."""
    return Path(__file__).resolve().parents[3] / "apps" / "sentra-web" / "dist"


class SpaStaticFiles(StaticFiles):
    def __init__(self, directory: Path) -> None:
        super().__init__(directory=directory, html=True)  # html=True: a folder answers its index.html

    async def get_response(self, path: str, scope: Scope) -> Response:
        parts = Path(path).parts  # StaticFiles hands over an OS-normalised path: `internal\x` on Windows, `internal/x` on CF
        first = parts[0] if parts else ""
        if first in API_PREFIXES:
            raise HTTPException(status_code=404)  # reached only when no API route matched
        try:
            response = await super().get_response(path, scope)
        except HTTPException as error:
            # Only a path that names no file (no extension, not under assets/) is a UI route; a missing .js stays 404.
            if error.status_code != 404 or first == ASSETS or Path(path).suffix:
                raise
            response = await super().get_response(INDEX, scope)
        if first == ASSETS:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif Path(path).suffix in ("", ".html"):
            response.headers["Cache-Control"] = "no-cache"  # a build made at H-1 must show up without clearing the cache
        return response


def has_build(directory: Path) -> bool:
    return (directory / INDEX).is_file()
