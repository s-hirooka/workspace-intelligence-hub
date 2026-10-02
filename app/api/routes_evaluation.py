from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db
from app.services.evaluation import latest_report, load_questions, run_evaluation

router = APIRouter()


class EvaluationRequest(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=20)
    label: str = Field(default="", max_length=200)


@router.get("/evaluation/questions")
def questions():
    return {"questions": load_questions()}


@router.post("/evaluation/run")
def run(request: EvaluationRequest, db: Session = Depends(get_db)):
    try:
        return run_evaluation(db, get_settings(), request.ids, request.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/evaluation/report")
def report(db: Session = Depends(get_db)):
    return latest_report(db)
