"""Resolución del perfil de Kiro compartida por los routers de chat."""

from fastapi import HTTPException


async def resolve_profile_arn(token: dict, token_service, model_catalog) -> str:
    """Perfil ARN del token, resolviéndolo contra el catálogo si falta.

    Ambos endpoints de chat repetían este mismo preámbulo antes de construir
    el estado de conversación.
    """
    profile_arn = token.get("profile_arn") or token.get("profileArn", "")
    if not profile_arn:
        catalog = await model_catalog.fetch(token)
        profile_arn = catalog.profile_arn
    if not profile_arn:
        raise HTTPException(500, "No profile ARN available.")
    return profile_arn
