from fastapi import APIRouter

from control.api import agent_jobs, automations, deployments, health, nodes

router = APIRouter()
router.include_router(health.router)  # /health (sin prefijo)
router.include_router(nodes.router)  # /api/v1/...
router.include_router(automations.router)
router.include_router(deployments.router)
router.include_router(agent_jobs.router)
