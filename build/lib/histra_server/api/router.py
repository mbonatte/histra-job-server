from fastapi import APIRouter

from .health import router as health_router
from .jobs import router as jobs_router
from .workers import router as workers_router

router = APIRouter()
router.include_router(health_router)
router.include_router(workers_router)
router.include_router(jobs_router)
