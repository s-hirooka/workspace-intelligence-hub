"""Local-only image inventory and search endpoints."""

import mimetypes

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ImageAsset, ImageFace
from app.db.session import get_db
from app.services.image_content import analyze_image_content
from app.services.image_metadata import refresh_capture_metadata
from app.services.image_search import (
    image_stats,
    label_face,
    render_face_thumbnail,
    render_thumbnail,
    safe_image_path,
    scan_images,
    search_images,
    update_image_tags,
)

router = APIRouter(tags=["画像検索"])
settings = get_settings()


class ContentAnalysisRequest(BaseModel):
    force: bool = False


class ImageTagsRequest(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=30)


class FaceLabelRequest(BaseModel):
    person_name: str = Field(default="", max_length=200)
    propagate: bool = True


@router.get("/images/stats")
def get_image_stats(db: Session = Depends(get_db)):
    return image_stats(db, settings)


@router.post("/admin/images/scan")
def run_image_scan(db: Session = Depends(get_db)):
    try:
        return scan_images(db, settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/admin/images/analyze-content")
def run_image_content_analysis(body: ContentAnalysisRequest, db: Session = Depends(get_db)):
    try:
        return analyze_image_content(db, settings, body.force)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@router.post("/admin/images/refresh-capture-dates")
def run_capture_date_refresh(body: ContentAnalysisRequest, db: Session = Depends(get_db)):
    return refresh_capture_metadata(db, settings, body.force)

@router.get("/images/search")
def find_images(
    q: str = Query(default="", max_length=500),
    smile_only: bool = False,
    person_name: str = Query(default="", max_length=200),
    min_people: int = Query(default=0, ge=0, le=100),
    exact_people: int = Query(default=0, ge=0, le=100),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return {"items": search_images(db, q, smile_only, person_name, min_people, exact_people, limit)}


@router.get("/images/{image_id}/thumbnail")
def image_thumbnail(image_id: str, db: Session = Depends(get_db)):
    row = db.get(ImageAsset, image_id)
    if not row:
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    try:
        path = safe_image_path(settings, row.file_path)
        payload = render_thumbnail(path, settings.image_thumbnail_max_px)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(payload, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})


@router.get("/images/faces/{face_id}/thumbnail")
def face_thumbnail(face_id: str, db: Session = Depends(get_db)):
    face = db.get(ImageFace, face_id)
    if not face:
        raise HTTPException(status_code=404, detail="顔候補が見つかりません")
    try:
        path = safe_image_path(settings, face.image.file_path)
        payload = render_face_thumbnail(path, face.bbox)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(payload, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})


@router.get("/images/{image_id}/file")
def image_file(image_id: str, db: Session = Depends(get_db)):
    row = db.get(ImageAsset, image_id)
    if not row:
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    try:
        path = safe_image_path(settings, row.file_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name, content_disposition_type="inline")


@router.put("/admin/images/{image_id}/tags")
def set_image_tags(image_id: str, body: ImageTagsRequest, db: Session = Depends(get_db)):
    try:
        return update_image_tags(db, image_id, body.tags)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/admin/images/faces/{face_id}/label")
def set_face_label(face_id: str, body: FaceLabelRequest, db: Session = Depends(get_db)):
    try:
        return label_face(db, settings, face_id, body.person_name, body.propagate)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc