from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import RuntimeSetting, Workspace
from app.db.session import get_db
from app.services.sns import (
    configuration_status,
    dashboard,
    facebook_account_dashboard,
    import_tiktok_csv,
    latest_runs,
    post_rankings,
    recommendations,
)
from app.services.ga4 import GA4ReportError, ga4_dashboard
from app.services.ga4_config import resolve_ga4_config
from app.services.gbp import GBPReportError, google_business_profile_dashboard
from app.services.gbp_config import resolve_gbp_config
from app.services.instagram_analytics import (
    connection_status,
    instagram_dashboard,
    instagram_post_detail,
    meta_configured,
)
from app.services.sns_scheduler import run_sns_sync_once, sns_schedule_config
from app.services.ripitte import import_ripitte_csv, ripitte_analytics
from app.services.line_official import import_line_friends_csv, line_friends_analytics

router = APIRouter(tags=["SNS実績"])


class SnsScheduleUpdate(BaseModel):
    enabled: bool
    interval_minutes: int = Field(ge=60, le=10080)


def _workspace(db: Session, workspace_key: str) -> Workspace:
    row = db.get(Workspace, workspace_key)
    if not row or not row.active:
        raise HTTPException(status_code=404, detail="顧客ワークスペースが見つかりません")
    return row


@router.get("/sns/dashboard")
def get_dashboard(days: int = 30, platform: str = "all", workspace_key: str = "workspace",
                  db: Session = Depends(get_db)):
    _workspace(db, workspace_key)
    return dashboard(db, days=days, platform=platform.lower(), workspace_key=workspace_key)


@router.get("/sns/recommendations")
def get_recommendations(days: int = 30, platform: str = "all", workspace_key: str = "workspace",
                        db: Session = Depends(get_db)):
    _workspace(db, workspace_key)
    return recommendations(db, days=days, platform=platform.lower(), workspace_key=workspace_key)


@router.get("/sns/instagram/analytics")
def get_instagram_analytics(days: int = 30, ranking_metric: str = "reach",
                            workspace_key: str = "workspace", db: Session = Depends(get_db)):
    _workspace(db, workspace_key)
    return instagram_dashboard(
        db, get_settings(), workspace_key, days=days, ranking_metric=ranking_metric
    )


@router.get("/sns/instagram/posts/{post_id}")
def get_instagram_post(post_id: str, workspace_key: str = "workspace",
                       db: Session = Depends(get_db)):
    _workspace(db, workspace_key)
    result = instagram_post_detail(db, workspace_key, post_id)
    if not result:
        raise HTTPException(status_code=404, detail="Instagram投稿が見つかりません")
    return result


@router.get("/sns/rankings")
def get_post_rankings(days: int = 30, platform: str = "instagram",
                      ranking_metric: str = "exposure", workspace_key: str = "workspace",
                      db: Session = Depends(get_db)):
    _workspace(db, workspace_key)
    try:
        return post_rankings(
            db,
            workspace_key=workspace_key,
            platform=platform.lower(),
            days=days,
            ranking_metric=ranking_metric,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sns/facebook/analytics")
def get_facebook_analytics(workspace_key: str = "workspace", db: Session = Depends(get_db)):
    _workspace(db, workspace_key)
    return facebook_account_dashboard(db, get_settings(), workspace_key)


@router.get("/sns/ga4")
def get_ga4_dashboard(days: int = 30, workspace_key: str = "workspace",
                      db: Session = Depends(get_db)):
    _workspace(db, workspace_key)
    settings = get_settings()
    ga4_config = resolve_ga4_config(settings, workspace_key)
    if not ga4_config.configured:
        return {
            "configured": False,
            "workspace_key": workspace_key,
            "summary": {},
            "sources": [],
            "landings": [],
            "notice": "この顧客のGA4 Data API設定は未登録です。別顧客の値は表示しません。",
        }
    try:
        return ga4_dashboard(ga4_config, days=days)
    except GA4ReportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/sns/google-business-profile")
def get_google_business_profile_dashboard(
    days: int = 30,
    workspace_key: str = "workspace",
    db: Session = Depends(get_db),
):
    _workspace(db, workspace_key)
    config = resolve_gbp_config(get_settings(), workspace_key)
    if not config.configured:
        return {
            "configured": False,
            "workspace_key": workspace_key,
            "summary": {},
            "series": [],
            "recommendations": [],
            "notice": "この顧客のGoogleビジネスプロフィールは未登録です。別顧客の値は表示しません。",
        }
    try:
        return google_business_profile_dashboard(config, days=days)
    except GBPReportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/sns/ripitte/analytics")
def get_ripitte_analytics(
    months: int = 3,
    workspace_key: str = "workspace",
    db: Session = Depends(get_db),
):
    _workspace(db, workspace_key)
    return ripitte_analytics(db, workspace_key, months=months)


@router.get("/sns/line/friends")
def get_line_friends_analytics(
    months: int = 3,
    workspace_key: str = "workspace",
    db: Session = Depends(get_db),
):
    _workspace(db, workspace_key)
    return line_friends_analytics(db, workspace_key, months=months)

@router.get("/sns/status")
def get_status(workspace_key: str = "workspace", db: Session = Depends(get_db)):
    _workspace(db, workspace_key)
    settings = get_settings()
    configuration = configuration_status(settings, workspace_key)
    notice = "SNS実績分析ではOpenAIを使用しません" if not settings.sns_openai_analysis_enabled else "OpenAI分析が有効です"
    if not meta_configured(settings, workspace_key):
        notice = "この顧客はCSV取込に対応しています。顧客別API認証は未設定のため、別顧客の接続情報は使用しません。"
    return {"configuration": configuration, "schedule": sns_schedule_config(db),
            "latest_runs": latest_runs(db, workspace_key),
            "meta": connection_status(db, settings, workspace_key),
            "notice": notice}


@router.post("/admin/sns/sync")
async def sync_now(workspace_key: str = "workspace", db: Session = Depends(get_db)):
    _workspace(db, workspace_key)
    settings = get_settings()
    configuration = configuration_status(settings, workspace_key)
    if not meta_configured(settings, workspace_key) and not configuration["youtube"]:
        raise HTTPException(status_code=400, detail="SNS API認証情報が未設定です")
    result = await run_sns_sync_once(workspace_key)
    if result["status"] == "busy":
        raise HTTPException(status_code=409, detail="SNS同期は実行中です")
    return result


@router.post("/admin/sns/tiktok/import")
async def import_tiktok(file: UploadFile = File(...), workspace_key: str = Form("workspace"),
                        db: Session = Depends(get_db)):
    _workspace(db, workspace_key)
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="TikTokから出力したCSVを選んでください")
    payload = await file.read(10 * 1024 * 1024 + 1)
    if len(payload) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSVは10MB以下にしてください")
    try:
        return import_tiktok_csv(db, payload, workspace_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/sns/ripitte/import")
async def import_ripitte(
    customer_file: UploadFile = File(...),
    reservation_file: UploadFile = File(...),
    workspace_key: str = Form("workspace"),
    db: Session = Depends(get_db),
):
    _workspace(db, workspace_key)
    files = (customer_file, reservation_file)
    for upload in files:
        if not (upload.filename or "").lower().endswith((".csv", ".tsv")):
            raise HTTPException(status_code=400, detail="リピッテから出力したCSVを2つ選んでください")
    limit = 15 * 1024 * 1024
    customer_payload = await customer_file.read(limit + 1)
    reservation_payload = await reservation_file.read(limit + 1)
    if len(customer_payload) > limit or len(reservation_payload) > limit:
        raise HTTPException(status_code=413, detail="CSVは1ファイル15MB以下にしてください")
    try:
        return import_ripitte_csv(
            db,
            customer_payload,
            reservation_payload,
            workspace_key=workspace_key,
            customer_filename=customer_file.filename or "顧客名簿.csv",
            reservation_filename=reservation_file.filename or "予約一覧.csv",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/sns/line/import")
async def import_line_friends(
    file: UploadFile = File(...),
    workspace_key: str = Form("workspace"),
    db: Session = Depends(get_db),
):
    _workspace(db, workspace_key)
    if not (file.filename or "").lower().endswith((".csv", ".tsv")):
        raise HTTPException(status_code=400, detail="LINE公式アカウントから出力したCSVを選んでください")
    limit = 10 * 1024 * 1024
    payload = await file.read(limit + 1)
    if len(payload) > limit:
        raise HTTPException(status_code=413, detail="CSVは10MB以下にしてください")
    try:
        return import_line_friends_csv(
            db,
            payload,
            workspace_key=workspace_key,
            filename=file.filename or "friend_overview.csv",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/admin/sns/schedule")
def get_sns_schedule(db: Session = Depends(get_db)):
    return sns_schedule_config(db)


@router.put("/admin/sns/schedule")
def set_sns_schedule(payload: SnsScheduleUpdate, request: Request, db: Session = Depends(get_db)):
    row = db.get(RuntimeSetting, "sns_sync") or RuntimeSetting(key="sns_sync")
    row.value = {"enabled": payload.enabled, "interval_minutes": payload.interval_minutes}
    row.updated_by = request.state.user.username
    db.add(row)
    db.commit()
    return sns_schedule_config(db)
