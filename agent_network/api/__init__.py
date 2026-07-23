"""API package wiring and scene-reading boundary overrides."""

from fastapi import HTTPException

from agent_network.file_management import ResourceNotFoundError, ResourceNotReadyError
from agent_network.scene_management import SceneValidationError

from . import simulations as simulations


async def _list_v2_scenes():
    """Return the authoritative v2 scene list through SceneStorage."""

    return {"scenes": simulations.get_scene_storage().list_scenes()}


async def _read_v2_scene(scene_name: str):
    """Return the authoritative v2 scene details through SceneStorage."""

    try:
        return simulations.get_scene_storage().details(scene_name)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ResourceNotReadyError, SceneValidationError, ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# simulations.py contains the execution engine. Keep its public helper surface wired to
# the same storage boundary as the managed scene API instead of maintaining a second
# file parser inside that module.
simulations.list_scenes = _list_v2_scenes
simulations.read_scene = _read_v2_scene

__all__ = ["simulations"]
