"""Evidence-only system summaries; unconnected steps remain unknown."""

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import CodeSymbol, ExternalResource, IndexedFile, SystemRelation
from app.db.session import get_db
from app.services.scanner import list_projects
from app.services.structure import reanalyze

router = APIRouter()


class ProjectRequest(BaseModel):
    project: str


class FlowRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


@router.post("/analysis/reanalyze")
def reanalyze_api(db: Session = Depends(get_db)):
    # Does not call Embeddings or chat API.
    return reanalyze(db, get_settings())


@router.get("/analysis/graph")
def graph(db: Session = Depends(get_db)):
    projects = list_projects(get_settings())
    relations = db.scalars(select(SystemRelation)).all()
    return {"nodes": [{"id": p, "label": p, "type": "database" if p.startswith("sqlite-") else "application"}
                      for p in projects],
            "edges": [{"source": r.source_project, "target": r.target_project,
                       "relation_type": r.relation_type, "confidence": r.confidence,
                       "status": r.evidence_json.get("status", "candidate"),
                       "source_file": r.source_file, "target_file": r.target_file} for r in relations]}


@router.post("/analysis/project-summary")
def project_summary(request: ProjectRequest, db: Session = Depends(get_db)):
    if request.project not in list_projects(get_settings()):
        raise HTTPException(status_code=400, detail="Unknown project")
    symbols = db.scalars(select(CodeSymbol).where(CodeSymbol.project == request.project).limit(1000)).all()
    resources = db.scalars(select(ExternalResource).where(ExternalResource.project == request.project).limit(1000)).all()
    relations = db.scalars(select(SystemRelation).where(
        (SystemRelation.source_project == request.project) | (SystemRelation.target_project == request.project))).all()
    files = db.scalar(select(func.count()).select_from(IndexedFile).where(IndexedFile.project == request.project)) or 0
    return {"project": request.project, "purpose": "資料だけでは目的を確定できません（要確認）",
            "indexed_files": files,
            "major_classes": [{"name": s.symbol_name, "file": s.file_path, "line": s.start_line}
                              for s in symbols if s.symbol_type in {"class", "interface", "record"}][:50],
            "major_functions": [{"name": s.symbol_name, "file": s.file_path, "line": s.start_line}
                                for s in symbols if s.symbol_type in {"method", "function"}][:50],
            "databases": sorted({r.resource_type for r in resources if r.resource_type in {"database", "sqlite"}}),
            "external_services": [{"type": r.resource_type, "name": r.resource_name,
                                   "endpoint": r.endpoint_masked, "file": r.file_path} for r in resources][:100],
            "relations": [{"project": r.target_project if r.source_project == request.project else r.source_project,
                           "type": r.relation_type, "confidence": r.confidence,
                           "source_file": r.source_file, "target_file": r.target_file} for r in relations],
            "unknowns": ["静的解析だけでは実際の稼働状態・人手の作業は確認できません"]}


@router.post("/analysis/company-system-summary")
def company_summary(db: Session = Depends(get_db)):
    network = graph(db)
    resources = db.scalars(select(ExternalResource).limit(2000)).all()
    return {"systems": network["nodes"], "relations": network["edges"],
            "data_flow": "確認された連携候補のみ。順序は未検証です",
            "databases": [n for n in network["nodes"] if n["type"] == "database"],
            "external_services": [{"project": r.project, "type": r.resource_type,
                                   "endpoint": r.endpoint_masked, "file": r.file_path}
                                  for r in resources if r.resource_type in {"http_api", "wordpress"}][:100],
            "manual_work_candidates": [], "duplicate_candidates": duplicate_candidates(db)["candidates"],
            "unknowns": ["手作業・実行時のデータフローは静的資料だけでは未確認"]}


@router.post("/analysis/system-flow")
def system_flow(request: FlowRequest, db: Session = Depends(get_db)):
    projects = [p for p in list_projects(get_settings()) if p.lower() in request.question.lower()]
    # A list of unrelated edges is not a data-flow path. Only direct evidence
    # between explicitly mentioned projects is returned; no invented ordering.
    relations = []
    if len(projects) == 2:
        relations = [r for r in db.scalars(select(SystemRelation)).all()
                     if {r.source_project, r.target_project} == set(projects)]
    steps = [{"order": 1, "project": r.source_project, "target_project": r.target_project,
              "symbol": None, "file": r.source_file,
              "description": f"{r.relation_type} の静的一致候補", "confidence": r.confidence}
             for r in relations[:20]]
    return {"summary": "静的根拠で一致した連携候補。全工程の連続性は未確認です" if steps else "経路を確認できませんでした。",
            "steps": steps, "unknowns": ["システム間の実行順序と受け渡しの連続性は確認できませんでした"],
            "sources": list(dict.fromkeys(path for r in relations for path in (r.source_file, r.target_file)))}


@router.get("/analysis/duplicate-candidates")
def duplicate_candidates(db: Session = Depends(get_db)):
    groups = defaultdict(list)
    symbols = db.scalars(select(CodeSymbol).where(CodeSymbol.symbol_type.in_(["method", "function"])) ).all()
    for symbol in symbols:
        if len(symbol.symbol_name) >= 6 and symbol.symbol_name.lower() not in {"initialize", "dispose", "getdata"}:
            groups[symbol.symbol_name.lower()].append(symbol)
    candidates = []
    for name, group in groups.items():
        for i, left in enumerate(group):
            for right in group[i + 1:]:
                if left.project != right.project:
                    candidates.append({"symbol": name, "source_project": left.project,
                                       "target_project": right.project, "source_file": left.file_path,
                                       "target_file": right.file_path, "reason": "同名メソッド候補（処理内容の一致は未検証）",
                                       "status": "candidate"})
                if len(candidates) >= 100:
                    return {"candidates": candidates}
    return {"candidates": candidates}
