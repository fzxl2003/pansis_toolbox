from typing import Any
from fastapi import APIRouter, Request, Response, UploadFile, File
from pydantic import BaseModel, Field
from backend.app.core.security import require_user
from tools.git_blog.backend import public, service

router=APIRouter()
mount_extra=public.mount_extra

class BlogPayload(BaseModel):
    name:str=""; slug:str; repoUrl:str; branch:str="main"; contentRoot:str=""; syncIntervalMinutes:int=15
    config:dict[str,Any]=Field(default_factory=dict); githubKeyId:str=""; enabled:bool=True; autoSyncEnabled:bool=True
class ProbePayload(BaseModel): repoUrl:str; githubKeyId:str=""

@router.get("/blogs")
def blogs(request:Request)->dict[str,Any]: return {"blogs":service.list_blogs(require_user(request))}
@router.post("/blogs",status_code=201)
def create(request:Request,payload:BlogPayload)->dict[str,Any]: return {"blog":service.create_blog(payload.model_dump(),require_user(request))}
@router.get("/blogs/{blog_id}")
def detail(request:Request,blog_id:str)->dict[str,Any]: return {"blog":service.get_blog(blog_id,require_user(request))}
@router.put("/blogs/{blog_id}")
def update(request:Request,blog_id:str,payload:BlogPayload)->dict[str,Any]: return {"blog":service.update_blog(blog_id,payload.model_dump(),require_user(request))}
@router.delete("/blogs/{blog_id}")
def delete(request:Request,blog_id:str)->dict[str,bool]: service.delete_blog(blog_id,require_user(request)); return {"deleted":True}
@router.post("/blogs/{blog_id}/sync",status_code=202)
def sync(request:Request,blog_id:str)->dict[str,str]: service.request_sync(blog_id,require_user(request)); return {"status":"queued"}
@router.get("/blogs/{blog_id}/snapshots")
def snapshots(request:Request,blog_id:str)->dict[str,Any]: return {"snapshots":service.list_snapshots(blog_id,require_user(request))}
@router.post("/blogs/{blog_id}/snapshots/{commit}/rollback")
def rollback(request:Request,blog_id:str,commit:str)->dict[str,Any]: return {"blog":service.rollback_snapshot(blog_id,commit,require_user(request))}
@router.get("/blogs/{blog_id}/runs")
def runs(request:Request,blog_id:str)->dict[str,Any]: return {"runs":service.list_runs(blog_id,require_user(request))}
@router.get("/blogs/{blog_id}/access-logs")
def access_logs(request:Request,blog_id:str)->dict[str,Any]: return {"logs":service.list_access_logs(blog_id,require_user(request))}
@router.post("/blogs/{blog_id}/template")
def upload_template(request: Request, blog_id: str, archive: UploadFile = File(...))->dict[str,bool]:
    service.save_template(blog_id, archive, require_user(request)); return {"uploaded": True}
@router.delete("/blogs/{blog_id}/template")
def remove_template(request: Request, blog_id: str)->dict[str,bool]:
    service.delete_template(blog_id, require_user(request)); return {"deleted": True}
@router.post("/repository/probe")
def probe(request:Request,payload:ProbePayload)->dict[str,Any]:
    user=require_user(request)
    return service.probe_repository(payload.repoUrl,user,payload.githubKeyId)
