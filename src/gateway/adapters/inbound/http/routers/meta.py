"""Rutas de diagnóstico y catálogo: `/health`, `/v1/props`, `/v1/models`."""

from fastapi import APIRouter

from .....protocols.openai.models import FALLBACK_MODELS, to_openai_model_list


def build_meta_router(config, token_repository, token_service, model_catalog, clock) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health():
        # /health no exige auth (es la única ruta abierta), así que no debe
        # devolver la ruta absoluta de la SQLite: filtraría el nombre del
        # usuario del sistema a quien tenga acceso de red al puerto.
        token = await token_repository.load()
        return {
            "status": "ok",
            "authenticated": token is not None,
            "token_in_db": token_repository.location() is not None,
            "region": config.region,
            "management_url": config.management_url,
            "runtime_url": config.runtime_url,
        }

    @router.get("/v1/props")
    async def props():
        return {
            "api_version": "2024-05-01",
            "models": [],
            "features": ["chat", "streaming", "tool_calling"],
        }

    @router.get("/v1/models")
    async def list_models():
        token = await token_service.ensure()
        catalog = await model_catalog.fetch(token)
        models = catalog.models or FALLBACK_MODELS
        return {"object": "list", "data": to_openai_model_list(models, int(clock.now()))}

    return router
