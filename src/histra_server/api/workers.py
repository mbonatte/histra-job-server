from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Worker, utcnow
from ..schemas import WorkerHeartbeatRequest, WorkerRegisterRequest, WorkerResponse

router = APIRouter(prefix="/api/v1/workers", tags=["workers"])


@router.post("/register", response_model=WorkerResponse)
def register_worker(payload: WorkerRegisterRequest, session: Session = Depends(get_session)):
    worker = session.scalar(select(Worker).where(Worker.name == payload.name))
    if worker is None:
        worker = Worker(name=payload.name, enabled=True)
        session.add(worker)
    worker.max_parallel_jobs = payload.max_parallel_jobs
    worker.worker_version = payload.worker_version
    worker.solver_version = payload.solver_version
    worker.metadata_json = payload.metadata
    worker.last_seen_at = utcnow()
    session.commit()
    session.refresh(worker)
    return worker


@router.post("/{worker_id}/heartbeat", response_model=WorkerResponse)
def heartbeat_worker(
    worker_id: str,
    payload: WorkerHeartbeatRequest,
    session: Session = Depends(get_session),
):
    worker = session.get(Worker, worker_id)
    if worker is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found")
    if payload.max_parallel_jobs is not None:
        worker.max_parallel_jobs = payload.max_parallel_jobs
    if payload.worker_version is not None:
        worker.worker_version = payload.worker_version
    if payload.solver_version is not None:
        worker.solver_version = payload.solver_version
    if payload.metadata is not None:
        worker.metadata_json = payload.metadata
    worker.last_seen_at = utcnow()
    session.commit()
    session.refresh(worker)
    return worker


@router.get("", response_model=list[WorkerResponse])
def list_workers(session: Session = Depends(get_session)):
    return session.scalars(select(Worker).order_by(Worker.name)).all()


@router.post("/{worker_id}/disable", response_model=WorkerResponse)
def disable_worker(worker_id: str, session: Session = Depends(get_session)):
    worker = session.get(Worker, worker_id)
    if worker is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found")
    worker.enabled = False
    session.commit()
    session.refresh(worker)
    return worker


@router.post("/{worker_id}/enable", response_model=WorkerResponse)
def enable_worker(worker_id: str, session: Session = Depends(get_session)):
    worker = session.get(Worker, worker_id)
    if worker is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found")
    worker.enabled = True
    session.commit()
    session.refresh(worker)
    return worker
