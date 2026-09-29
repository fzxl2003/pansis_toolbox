import html
from typing import Any
from fastapi import APIRouter, Request, Response, UploadFile, File
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from backend.app.core.security import require_user
from tools.git_blog.backend import public, service

router=APIRouter()
mount_extra=public.mount_extra

class BlogPayload(BaseModel):
    name:str=""; slug:str; repoUrl:str; branch:str="main"; contentRoot:str=""; syncIntervalMinutes:int=15
    config:dict[str,Any]=Field(default_factory=dict); githubKeyId:str=""; enabled:bool=True; autoSyncEnabled:bool=True
class ProbePayload(BaseModel): repoUrl:str; githubKeyId:str=""
class VisibilityPayload(BaseModel): visibility:str
class AccessUserPayload(BaseModel): username:str=""; canShare:bool=False
class AccessPasswordPayload(BaseModel): label:str=""; password:str=""; canShare:bool=False; enabled:bool=True
class SharingPayload(BaseModel): enabled:bool
class ShareUpdatePayload(BaseModel):
    mode:str="document"; expiresAt:str|None=None; maxViews:int|None=None; enabled:bool=True
    passwordAction:str="keep"; password:str=""

@router.get("/blogs")
def blogs(request:Request)->dict[str,Any]: return {"blogs":service.list_blogs(require_user(request))}
@router.get("/themes")
def themes(request:Request)->dict[str,Any]: return {"themes":service.list_themes(require_user(request))}
@router.post("/themes",status_code=201)
def create_theme(request:Request,css:UploadFile=File(...))->dict[str,Any]: return {"theme":service.save_theme(css,require_user(request))}
@router.get("/themes/{theme_id}/css")
def theme_css(request:Request,theme_id:str)->Response:
    raw=service.theme_css(theme_id,require_user(request))
    return Response(raw,media_type="text/css",headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})
@router.get("/themes/{theme_id}/preview",response_class=HTMLResponse)
def preview_theme(request:Request,theme_id:str)->HTMLResponse:
    user=require_user(request); service.theme_css(theme_id,user)
    theme=next(item for item in service.list_themes(user) if item["id"]==theme_id)
    title=html.escape(theme["name"])
    return HTMLResponse(f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>html{{background:#f1f5f9}}body{{margin:0}}#write{{box-sizing:border-box;min-height:100vh;background:var(--bg-color,#fff)}}img{{max-width:100%}}</style><link rel="stylesheet" href="/api/tools/git-blog/themes/{theme_id}/css"></head><body><article id="write" class="typora-export"><h1>Typora 主题预览</h1><p>这是一段用于检查正文、<strong>强调文字</strong>、<em>斜体</em>和<a href="#">链接样式</a>的示例内容。</p><h2>二级标题</h2><blockquote><p>主题会以原始 CSS 直接应用，无需修改选择器。</p></blockquote><h3>列表与代码</h3><ul><li>第一项</li><li>第二项<ul><li>嵌套项目</li></ul></li></ul><pre class="md-fences"><code>def hello():\n    return "theme preview"</code></pre><p>行内代码示例：<code>custom-theme.css</code></p><table class="md-table"><thead><tr><th>功能</th><th>状态</th></tr></thead><tbody><tr><td>Typora CSS</td><td>已加载</td></tr><tr><td>上传即用</td><td>支持</td></tr></tbody></table></article></body></html>''')
@router.delete("/themes/{theme_id}")
def remove_theme(request:Request,theme_id:str)->dict[str,Any]: return {"deleted":True,"affectedBlogs":service.delete_theme(theme_id,require_user(request))}
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
@router.get("/blogs/{blog_id}/access")
def access_settings(request:Request,blog_id:str)->dict[str,Any]: return service.get_access_settings(blog_id,require_user(request))
@router.put("/blogs/{blog_id}/access")
def update_visibility(request:Request,blog_id:str,payload:VisibilityPayload)->dict[str,Any]: return service.set_blog_visibility(blog_id,payload.visibility,require_user(request))
@router.post("/blogs/{blog_id}/access/users")
def add_access_user(request:Request,blog_id:str,payload:AccessUserPayload)->dict[str,Any]: return service.add_access_user(blog_id,payload.username,payload.canShare,require_user(request))
@router.put("/blogs/{blog_id}/access/users/{user_id}")
def update_access_user(request:Request,blog_id:str,user_id:str,payload:AccessUserPayload)->dict[str,Any]: return service.update_access_user(blog_id,user_id,payload.canShare,require_user(request))
@router.delete("/blogs/{blog_id}/access/users/{user_id}")
def remove_access_user(request:Request,blog_id:str,user_id:str)->dict[str,Any]: return service.remove_access_user(blog_id,user_id,require_user(request))
@router.post("/blogs/{blog_id}/access/passwords",status_code=201)
def add_access_password(request:Request,blog_id:str,payload:AccessPasswordPayload)->dict[str,Any]: return {"password":service.add_access_password(blog_id,payload.label,payload.password,payload.canShare,require_user(request))}
@router.put("/blogs/{blog_id}/access/passwords/{password_id}")
def update_access_password(request:Request,blog_id:str,password_id:str,payload:AccessPasswordPayload)->dict[str,Any]: return {"password":service.update_access_password(blog_id,password_id,payload.model_dump(),require_user(request))}
@router.delete("/blogs/{blog_id}/access/passwords/{password_id}")
def remove_access_password(request:Request,blog_id:str,password_id:str)->dict[str,bool]: service.remove_access_password(blog_id,password_id,require_user(request)); return {"deleted":True}
@router.get("/blogs/{blog_id}/sharing")
def sharing_settings(request:Request,blog_id:str)->dict[str,Any]: return service.get_sharing_settings(blog_id,require_user(request))
@router.put("/blogs/{blog_id}/sharing")
def update_sharing(request:Request,blog_id:str,payload:SharingPayload)->dict[str,Any]: return service.set_sharing_enabled(blog_id,payload.enabled,require_user(request))
@router.put("/blogs/{blog_id}/shares/{share_id}")
def update_share(request:Request,blog_id:str,share_id:str,payload:ShareUpdatePayload)->dict[str,Any]: return {"share":service.update_share(blog_id,share_id,payload.model_dump(),require_user(request))}
@router.delete("/blogs/{blog_id}/shares/{share_id}")
def delete_share(request:Request,blog_id:str,share_id:str)->dict[str,bool]: service.delete_share(blog_id,share_id,require_user(request)); return {"deleted":True}
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
