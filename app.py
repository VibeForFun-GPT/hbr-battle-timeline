"""本地 GUI / CLI：Video 转行动轴、校准、核对和 Export。"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, unquote, urlparse
import webbrowser

from axis.export import export_all, export_bundle
from axis import __version__
from axis.live import CaptureSession, CapturePaused, WindowSource, list_windows
from axis.model import ALIASES, SCHEMA_VERSION, annotate_rows
from axis.pipeline import Cancelled, analyze, identify_team_names, save_json
from axis.start_detection import StartSearchCancelled, find_start
from axis.vision import DEFAULT_LAYOUT, Video, read_image, save_image

HERE=Path(__file__).resolve().parent
VIDEO_EXTENSIONS={".mp4",".mkv",".mov",".avi",".webm",".m4v"}
CATALOG_FOLDER=HERE/"data"/"character-catalog"


def catalog_status():
    """读取本地 Catalog Metadata；不在 UI Polling 中加载 Matcher。"""
    result={"ready":False,"characters":0,"styles":0,"updated_at":None,"error":"尚未下载角色图库"}
    try:
        catalog=json.loads((CATALOG_FOLDER/"catalog.json").read_text(encoding="utf-8"))
        if not isinstance(catalog,dict) or catalog.get("schema_version")!=1 or not isinstance(catalog.get("styles"),list):
            return {**result,"error":"图库格式不可读，请更新角色图库"}
        styles=[]
        for style in catalog["styles"]:
            if not isinstance(style,dict) or not isinstance(style.get("image"),str) or not isinstance(style.get("character_id"),str):continue
            path=(CATALOG_FOLDER/style["image"]).resolve()
            if path.is_relative_to(CATALOG_FOLDER.resolve()) and path.is_file():styles.append(style)
        result.update(ready=bool(styles),characters=len({s["character_id"] for s in styles}),styles=len(styles),
                      updated_at=catalog.get("updated_at"),error="" if styles else "图库图片缺失，请更新角色图库")
    except (OSError,ValueError,TypeError,KeyError):
        pass
    return result


def parse_time(value):
    if value in (None,""):return None
    if isinstance(value,(int,float)):
        result=float(value)
    else:
        parts=str(value).strip().replace("：",":").split(":")
        if not 1<=len(parts)<=3:raise ValueError("时间请使用秒数、MM:SS 或 HH:MM:SS。")
        result=0.
        for part in parts:result=result*60+float(part)
    if not math.isfinite(result) or result<0:raise ValueError("时间需要为非负数。")
    return result


def preview_video(path,timestamp=0,*,include_image=True):
    """Return Video Metadata and an optional frame-accurate Preview."""
    if not isinstance(path,str) or not path.strip():
        raise ValueError("请选择有效的 Video 文件。")
    requested=parse_time(timestamp) or 0
    video=Video(path)
    try:
        if (not math.isfinite(video.fps) or video.fps<=0 or video.frames<1
                or not math.isfinite(video.duration) or video.duration<=0):
            raise ValueError("无法读取 Video 的 FPS 或时长。")
        maximum=(video.frames-1)/video.fps
        result={"metadata":video.metadata(),"duration":video.duration,"fps":video.fps,"max_time":maximum}
        if not include_image:
            return result
        import base64
        import cv2
        # Seek to an existing Frame, including low-FPS and one-Frame Videos.
        # The returned time belongs to the displayed Frame, not the input value.
        frame_index=min(video.frames-1,int(min(requested,maximum)*video.fps+1e-7))
        video.cap.set(cv2.CAP_PROP_POS_FRAMES,frame_index)
        ok,frame=video.cap.read()
        if not ok:
            raise ValueError(f"无法读取 {frame_index/video.fps:.2f}s 的 Frame。")
        position=video.cap.get(cv2.CAP_PROP_POS_FRAMES)
        if math.isfinite(position) and 1<=position<=video.frames:
            frame_index=max(0,min(video.frames-1,round(position)-1))
        frame=video.normalize(frame)
        ok,encoded=cv2.imencode(".jpg",frame,[cv2.IMWRITE_JPEG_QUALITY,87])
        if not ok:
            raise ValueError("无法生成 Video Preview。")
        result.update(time=frame_index/video.fps,frame_index=frame_index,
                      image="data:image/jpeg;base64,"+base64.b64encode(encoded).decode("ascii"))
        return result
    finally:
        video.close()


def validate_profile(profile):
    if not isinstance(profile,dict):raise ValueError("队伍配置需要是 JSON Object。")
    name_mode=profile.get("name_mode") or ("profile" if "team" in profile else "auto")
    if name_mode not in ("auto","placeholder","ocr","profile"):raise ValueError("命名方式需要为自动识别、Placeholder、OCR 或 Profile。")
    if name_mode=="profile" and "team" not in profile:raise ValueError("复用队伍配置需要提供 6 个名字。")
    layout={**DEFAULT_LAYOUT,**profile.get("layout",{})}
    if layout["size"]!=[1920,1080]:raise ValueError("UI Layout 的基准 Shape 必须为 1920 × 1080。")
    rect=layout["crop"]
    if len(rect)!=4 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in rect) or not(0<=rect[0]<rect[2]<=1 and 0<=rect[1]<rect[3]<=1):raise ValueError("Crop 需要是有效的 [left, top, right, bottom] 比例范围。")
    centers=layout["portrait_centers"]
    if len(centers)!=6 or any(len(p)!=2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in p) or not(0<=p[0]<1920 and 0<=p[1]<1080) for p in centers):raise ValueError("Avatar Center 需要为游戏画面中的 6 个有效位置。")
    for key in ("portrait_boxes","skill_boxes","role_boxes"):
        expected=3 if key=="skill_boxes" else 6
        if len(layout[key])!=expected:raise ValueError(f"{key} 的位置数量不正确。")
    for boxes in [layout["portrait_boxes"],layout["skill_boxes"],layout["role_boxes"],[layout[k] for k in ["turn_box","bonus_box","od_box","button_box"]]]:
        for b in boxes:
            if len(b)!=4 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in b) or not(0<=b[0]<b[2]<=1920 and 0<=b[1]<b[3]<=1080):raise ValueError("UI Layout 的 Rectangle 无效。")
    if "team" in profile:
        if len(profile["team"])!=6:raise ValueError("队伍需要 6 个角色。")
        for p in profile["team"]:
            if not isinstance(p,dict) or any(not isinstance(p.get(k,""),str) or len(p.get(k,""))>80 for k in ("name","alias")):raise ValueError("角色名称格式不正确。")
    aliases=profile.get("aliases",{})
    if not isinstance(aliases,dict) or len(aliases)>1500 or any(not isinstance(k,str) or not isinstance(v,str) or len(k)>160 or len(v)>80 for k,v in aliases.items()):raise ValueError("Skill 简写表格式不正确。")
    return {**profile,"layout":layout,"name_mode":name_mode}


def validate_document(document,original):
    if not isinstance(document,dict) or document.get("schema_version")!=SCHEMA_VERSION:raise ValueError("行动轴格式不正确。")
    if not isinstance(document.get("title"),str) or len(document["title"])>200:raise ValueError("标题过长。")
    team=document.get("team",[])
    validate_profile({"team":team,"layout":document.get("settings",{}).get("layout",DEFAULT_LAYOUT),"aliases":document.get("settings",{}).get("aliases",{})})
    if set(p.get("id") for p in team)!={1,2,3,4,5,6}:raise ValueError("Avatar ID 需要为 1–6，且不能重复。")
    rows=document.get("rows")
    if not isinstance(rows,list) or not 1<=len(rows)<=6000:raise ValueError("行动轴需要 1–6000 行。")
    people={p["id"]:p for p in team}
    for i,row in enumerate(rows):
        for key in ("time","confirm_time"):
            n=row.get(key)
            if not isinstance(n,(int,float)) or not math.isfinite(n) or not 0<=n<=original["video"]["duration"]+.5:raise ValueError(f"第 {i+1} 行的时间无效。")
        if row.get("turn") is not None and (type(row["turn"]) is not int or not 1<=row["turn"]<=999):raise ValueError("TURN 需要为 1–999。")
        if row.get("kind") not in ("normal","extra","pre_od","post_od","od"):raise ValueError("回合类型不正确。")
        for key in ("od_before","od_after"):
            n=row.get(key)
            if n is not None and (not isinstance(n,(int,float)) or not math.isfinite(n) or not -1<=n<=1000):raise ValueError("OD 读数无效。")
        actions=row.get("actions",[])
        if len(actions)!=3 or len({a.get("character_id") for a in actions})!=3 or any(a.get("character_id") not in people for a in actions):raise ValueError("每行需要 3 个不同角色的前排位置。")
        for slot,a in enumerate(actions):
            person=people[a["character_id"]]
            a.update(character=person["alias"],character_full=person["name"],slot=slot+1)
            if any(not isinstance(a.get(k,""),str) or len(a.get(k,""))>200 for k in ("skill","skill_full","skill_raw")):raise ValueError("Skill 名称无效。")
        if not isinstance(row.get("label",""),str) or len(row.get("label",""))>60 or not isinstance(row.get("note",""),str) or len(row.get("note",""))>2000:raise ValueError("回合标签或备注过长。")
        image=row.get("screenshot","")
        if image and (not re.fullmatch(r"frames/[A-Za-z0-9_-]+\.jpg",image)):raise ValueError("Screenshot 路径不正确。")
        row["index"]=i+1
    # Video 源路径与原始分析 Metadata 只能由 Server 确定。
    document["video"]=copy.deepcopy(original["video"])
    document["review_history"]=original.get("review_history",[])+[{"at":datetime.now().astimezone().isoformat(),"rows":len(rows),"reviewed":sum(bool(r.get('reviewed')) for r in rows)}]
    return document


class Store:
    def __init__(self,data,source_factory=WindowSource):
        self.data=Path(data).resolve()
        self.jobs=self.data/"jobs"
        self.jobs.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.running={}
        self.capture=None
        self.capture_job=None
        self.source_factory=source_factory
        self.closed=threading.Event()
        self.catalog_update={"updating":False,"fraction":0.,"message":""}
        self.start_searches={}
        candidates=[p for p in [self.data/"sample-final",self.data/"sample-v4",self.data/"sample"] if (p/"axis.json").is_file()]
        self.example=candidates[0] if candidates else None

    def folder(self,job_id):
        if job_id=="sample" and self.example:return self.example
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}",job_id):raise ValueError("Job ID 无效。")
        folder=self.jobs/job_id
        if not folder.is_dir():raise ValueError("该分析结果不存在。")
        return folder

    def metadata(self,job_id):
        with self.lock:
            folder=self.folder(job_id)
            file=folder/"job.json"
            if file.exists():return json.loads(file.read_text(encoding="utf-8"))
            doc=json.loads((folder/"axis.json").read_text(encoding="utf-8"))
            return {"id":job_id,"title":doc["title"],"status":"done","fraction":1.,"message":"示例分析已完成","created_at":doc["created_at"]}

    def list(self):
        jobs=[]
        for folder in self.jobs.iterdir():
            if (folder/"job.json").is_file():
                metadata=self.metadata(folder.name)
                if metadata["status"] in ("running","capturing") and folder.name not in self.running:
                    metadata.update(status="interrupted",message="上次服务已关闭，可重新分析")
                jobs.append(metadata)
        jobs.sort(key=lambda x:x["created_at"],reverse=True)
        if self.example:jobs.append(self.metadata("sample"))
        return jobs

    def document(self,job_id):
        with self.lock:
            path=self.folder(job_id)/"axis.json"
            if not path.exists():raise ValueError("分析尚未完成。")
            return json.loads(path.read_text(encoding="utf-8"))

    def catalog_state(self):
        with self.lock:
            return {**catalog_status(),**self.catalog_update}

    def update_catalog(self):
        with self.lock:
            if self.catalog_update["updating"]:return self.catalog_state()
            self.catalog_update.update(updating=True,fraction=0.,message="正在下载角色信息与 Avatar")
        def worker():
            try:
                from tools.sync_character_catalog import sync_catalog
                def progress(done,total,message):
                    with self.lock:
                        self.catalog_update.update(fraction=min(1.,done/max(total,1)),message=message)
                catalog=sync_catalog(CATALOG_FOLDER,progress=progress)
                with self.lock:
                    self.catalog_update.update(fraction=1.,message=f"图库已更新：{len(catalog['styles'])} 个 Style")
            except Exception as error:
                with self.lock:self.catalog_update.update(message="更新未完成，已有图库仍可使用："+str(error))
            finally:
                with self.lock:self.catalog_update["updating"]=False
        threading.Thread(target=worker,daemon=True).start()
        return self.catalog_state()

    def identify_names(self,job_id):
        document=self.document(job_id)
        folder=self.folder(job_id)
        images=[]
        for person in document["team"]:
            relative=person.get("avatar","")
            path=(folder/relative).resolve()
            if not path.is_relative_to(folder) or not relative.startswith("avatars/"):
                raise ValueError("Avatar 图片路径无效。")
            images.append(read_image(path) if path.is_file() else None)
        team=copy.deepcopy(document["team"])
        manual=sum(not p.get("name_is_placeholder",False) for p in team)
        prior=sum(p.get("name_source")=="hbr_quest_avatar" for p in team)
        identify_team_names(team,images)
        return {"team":team,"matched":sum(p.get("name_source")=="hbr_quest_avatar" for p in team)-prior,
                "kept_manual":manual,"unresolved":sum(p.get("name_is_placeholder",False) for p in team),"catalog":self.catalog_state()}

    def find_start_state(self,search_id):
        with self.lock:
            record=self.start_searches.get(search_id)
            if record is None:raise ValueError("起点搜索不存在，请重新开始。")
            return copy.deepcopy(record["state"])

    def cancel_start_search(self,search_id):
        with self.lock:
            record=self.start_searches.get(search_id)
            if record is None:raise ValueError("起点搜索不存在。")
            record["cancel"].set()
        return self.find_start_state(search_id)

    def start_search(self,body):
        path=body.get("path")
        if not isinstance(path,str) or not path.strip():raise ValueError("请选择有效的 Video 文件。")
        path=Path(path).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:raise ValueError("请选择有效的 Video 文件。")
        search_from=parse_time(body.get("search_from",0)) or 0
        profile=validate_profile(body.get("profile",{}))
        with self.lock:
            if self.closed.is_set():raise ValueError("服务已关闭。")
            pending=[record for record in self.start_searches.values() if record["state"]["status"]=="running"]
            if any(not record["cancel"].is_set() for record in pending):
                raise ValueError("正在寻找初始画面，请等待完成或取消后重试。")
            # Cancelled OCR initialization may still be releasing resources.
            if len(pending)>=4:
                raise ValueError("已取消的搜索仍在释放资源，请稍后重试。")
            while len(self.start_searches)>=8:
                expired=next((key for key,item in self.start_searches.items() if item["state"]["status"]!="running"),None)
                if expired is None:break
                self.start_searches.pop(expired)
            search_id="start-"+secrets.token_hex(6)
            record={"cancel":threading.Event(),"state":{"id":search_id,"status":"running","found":False,
                    "fraction":0.,"message":"正在自动寻找可用选招画面","scanned_until":search_from,"result":None}}
            self.start_searches[search_id]=record

        def worker():
            def progress(update):
                with self.lock:record["state"].update(update)
            try:
                result=find_start(path,search_from=search_from,layout=profile["layout"],cancel=record["cancel"],progress=progress)
                if record["cancel"].is_set():raise StartSearchCancelled("已取消自动寻找起点。")
                with self.lock:
                    record["state"].update(status="done" if result["found"] else "not_found",found=result["found"],
                        fraction=1.,message="已找到可用选招画面" if result["found"] else result["message"],result=result)
            except StartSearchCancelled as error:
                with self.lock:record["state"].update(status="cancelled",message=str(error))
            except Exception as error:
                with self.lock:record["state"].update(status="error",message=str(error),error=str(error))

        record["thread"]=threading.Thread(target=worker,daemon=True)
        record["thread"].start()
        return self.find_start_state(search_id)

    def create(self,body):
        video=Path(body.get("path","")).expanduser().resolve()
        if not video.is_file() or video.suffix.lower() not in VIDEO_EXTENSIONS:raise ValueError("请选择 MP4 / MKV / MOV / AVI / WebM 视频。")
        profile=validate_profile(body.get("profile",{}))
        start=parse_time(body.get("start",0)) or 0
        end=parse_time(body.get("end"))
        rate=float(body.get("sample_rate",4))
        if not math.isfinite(rate) or not 1<=rate<=12:raise ValueError("Sample Rate 必须在 1–12 FPS 之间。")
        with self.lock:
            if any(not item["cancel"].is_set() for item in self.running.values()):raise ValueError("已有 Video 正在分析。完成或停止后再开始下一段。")
            job_id=datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+secrets.token_hex(3)
            folder=self.jobs/job_id
            folder.mkdir()
            metadata={"id":job_id,"title":video.stem,"status":"running","fraction":0.,"message":"开始分析","created_at":datetime.now().astimezone().isoformat()}
            save_json(folder/"job.json",metadata)
            cancel=threading.Event()
            self.running[job_id]={"cancel":cancel}

        def worker():
            last=[0.]
            def progress(f,message):
                now=time.monotonic()
                if now-last[0]<.25 and f<1:return
                with self.lock:
                    metadata.update(fraction=f,message=message)
                    save_json(folder/"job.json",metadata)
                last[0]=now
            try:
                document=analyze(video,folder,start=start,end=end,sample_rate=rate,profile=profile,progress=progress,cancel=cancel)
                export_all(document,folder)
                metadata.update(status="done",fraction=1.,message=f"完成：{len(document['rows'])} 次行动确认")
            except Cancelled:
                metadata.update(status="cancelled",message="分析已停止")
            except Exception as error:
                metadata.update(status="error",message=str(error))
            finally:
                with self.lock:
                    save_json(folder/"job.json",metadata)
                    self.running.pop(job_id,None)
        threading.Thread(target=worker,daemon=True).start()
        return metadata

    def start_live(self,body):
        profile=validate_profile(body.get("profile",{}))
        rate=float(body.get("sample_rate",4))
        fps=float(body.get("fps",8))
        if not math.isfinite(rate) or not 1<=rate<=12 or not math.isfinite(fps) or not 4<=fps<=12:
            raise ValueError("扫描精度需要为 1–12 FPS，捕获精度需要为 4–12 FPS。")
        if type(body.get("hwnd")) is not int:raise ValueError("请选择游戏窗口。")
        with self.lock:
            if any(not item["cancel"].is_set() for item in self.running.values()):raise ValueError("已有捕获或分析正在进行，请先结束。")
            source=self.source_factory(body["hwnd"])
            job_id=datetime.now().strftime("%Y%m%d-%H%M%S")+"-live-"+secrets.token_hex(3)
            folder=self.jobs/job_id;folder.mkdir()
            metadata={"id":job_id,"title":"实时战斗 · "+source.info["title"],"status":"capturing","fraction":0.,
                      "message":"请切回游戏窗口，捕获将自动开始","source":"live","created_at":datetime.now().astimezone().isoformat()}
            cancel=threading.Event();self.running[job_id]={"cancel":cancel}
            save_json(folder/"job.json",metadata)

            def progress(stats):
                with self.lock:
                    metadata.update(message=stats["message"],capture=stats)
                    save_json(folder/"job.json",metadata)

            def finished(video,stats,error):
                try:
                    save_json(folder/"capture.json",stats)
                    if self.closed.is_set() or cancel.is_set():raise Cancelled("捕获已停止，素材保存在分析目录中。")
                    if error:raise ValueError(error)
                    with self.lock:
                        metadata.update(status="running",fraction=0.,message="捕获结束，正在生成行动轴",capture=stats)
                        save_json(folder/"job.json",metadata)
                    previous=[0.]
                    def analysis_progress(f,message):
                        now=time.monotonic()
                        if now-previous[0]<.25 and f<1:return
                        with self.lock:
                            metadata.update(fraction=f,message=message);save_json(folder/"job.json",metadata)
                        previous[0]=now
                    with self.lock:chosen_profile=copy.deepcopy(session.profile)
                    document=analyze(video,folder,sample_rate=min(rate,fps),profile=chosen_profile,progress=analysis_progress,cancel=cancel)
                    document["title"]=metadata["title"]
                    document["capture"]={**stats,"path":str(video),"source":"window","audio":False}
                    if stats["paused_seconds"]>.1:
                        document["warnings"].append("游戏窗口离开前台时已暂停取新画面。请核对暂停期间是否有行动遗漏。")
                    save_json(folder/"axis.json",document);save_json(folder/"axis.automatic.json",document)
                    export_all(document,folder)
                    metadata.update(status="done",fraction=1.,message=f"完成：{len(document['rows'])} 次行动确认")
                except Cancelled as error:metadata.update(status="cancelled",message=str(error))
                except Exception as error:metadata.update(status="error",message=str(error))
                finally:
                    with self.lock:
                        save_json(folder/"job.json",metadata);self.running.pop(job_id,None)

            session=CaptureSession(folder,source,fps=fps,progress=progress,finished=finished)
            session.profile=profile
            self.capture=session
            self.capture_job=job_id
            self.capture.start()
            return dict(metadata)

    def stop_live(self):
        with self.lock:
            if self.capture is None:raise ValueError("没有正在进行的捕获。")
            if self.capture.snapshot()["active"]:self.capture.stop()
            return {"ok":True,"id":self.capture_job}

    def update_live_profile(self,profile):
        validated=validate_profile(profile)
        with self.lock:
            if not self.capture or not self.capture.snapshot()["active"]:raise ValueError("捕获已结束，校准可用于下次捕获。")
            self.capture.profile=validated
        return {"ok":True}

    def close(self):
        self.closed.set()
        for record in list(self.start_searches.values()):record["cancel"].set()
        for item in list(self.running.values()):item["cancel"].set()
        if self.capture and self.capture.snapshot()["active"]:
            self.capture.stop();self.capture.thread.join(timeout=3)


def make_handler(store,token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass

        def route(self):
            parsed=urlparse(self.path)
            prefix="/"+token+"/"
            if not parsed.path.startswith(prefix):raise PermissionError("请从启动器打开此本地程序。")
            return unquote(parsed.path[len(prefix):]),parse_qs(parsed.query)

        def send_json(self,data,status=200):
            raw=json.dumps(data,ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Content-Length",str(len(raw)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def body(self):
            origin=self.headers.get("Origin")
            if origin and urlparse(origin).netloc!=self.headers.get("Host"):raise PermissionError("Origin 不匹配。")
            size=int(self.headers.get("Content-Length","0"))
            if not 0<size<=12*1024*1024:raise ValueError("请求大小不正确。")
            return json.loads(self.rfile.read(size).decode("utf-8"))

        def send_file(self,path,download=False):
            path=Path(path)
            if not path.is_file():self.send_error(404);return
            total=path.stat().st_size
            content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            start,end,status=0,total-1,200
            header=self.headers.get("Range")
            if header:
                match=re.fullmatch(r"bytes=(\d*)-(\d*)",header)
                if not match or (not match[1] and not match[2]):self.send_error(416);return
                if not match[1]:start=max(0,total-int(match[2]))
                else:
                    start=int(match[1])
                    if match[2]:end=min(end,int(match[2]))
                if start>=total or end<start:
                    self.send_response(416);self.send_header("Content-Range",f"bytes */{total}");self.end_headers();return
                status=206
            self.send_response(status)
            self.send_header("Content-Type",content_type+("; charset=utf-8" if path.suffix in (".html",".css",".js") else ""))
            self.send_header("Content-Length",str(end-start+1))
            self.send_header("Accept-Ranges","bytes")
            self.send_header("X-Content-Type-Options","nosniff")
            if header:self.send_header("Content-Range",f"bytes {start}-{end}/{total}")
            if download:
                from urllib.parse import quote
                self.send_header("Content-Disposition","attachment; filename*=UTF-8''"+quote(path.name))
            self.end_headers()
            with path.open("rb") as file:
                file.seek(start)
                left=end-start+1
                while left:
                    piece=file.read(min(left,1024*1024))
                    if not piece:break
                    self.wfile.write(piece)
                    left-=len(piece)

        def do_GET(self):
            try:
                route,query=self.route()
                if route=="api/bootstrap":
                    self.send_json({"jobs":store.list(),"aliases":ALIASES,"layout":DEFAULT_LAYOUT,"live_supported":os.name=="nt","catalog":store.catalog_state()});return
                if route=="api/catalog/status":self.send_json(store.catalog_state());return
                if route.startswith("api/find-start/"):
                    self.send_json(store.find_start_state(route[len("api/find-start/"):]));return
                if route=="api/jobs":self.send_json(store.list());return
                if route=="api/live/windows":self.send_json({"windows":list_windows()});return
                if route=="api/live/status":
                    self.send_json({"id":store.capture_job,"capture":store.capture.snapshot() if store.capture else None});return
                if route.startswith("api/job/"):
                    job=route[len("api/job/"):]
                    self.send_json({"job":store.metadata(job),"document":store.document(job) if (store.folder(job)/"axis.json").exists() else None,
                                    "capture":store.capture.snapshot() if store.capture and store.capture_job==job else None});return
                if route.startswith("file/"):
                    job,relative=route[5:].split("/",1)
                    folder=store.folder(job)
                    path=(folder/relative).resolve()
                    if not path.is_relative_to(folder) or path.suffix.lower() not in (".jpg",".png",".json",".xlsx",".csv",".html",".zip"):raise PermissionError("文件路径无效。")
                    self.send_file(path,download="download" in query);return
                if route.startswith("video/"):
                    document=store.document(route[6:])
                    self.send_file(document["video"]["path"]);return
                if route in ("","index.html","app.js","style.css"):
                    self.send_file(HERE/"web"/(route or "index.html"));return
                self.send_error(404)
            except (PermissionError,ValueError,FileNotFoundError) as error:self.send_json({"error":str(error)},400)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
            except Exception as error:self.send_json({"error":str(error)},500)

        def do_POST(self):
            try:
                route,query=self.route()
                body=self.body()
                if route=="api/catalog/update":self.send_json(store.update_catalog());return
                if route.startswith("api/names/"):self.send_json(store.identify_names(route[len("api/names/"):]));return
                if route=="api/analyze":self.send_json(store.create(body));return
                if route=="api/find-start":self.send_json(store.start_search(body));return
                if route.startswith("api/find-start/") and route.endswith("/cancel"):
                    self.send_json(store.cancel_start_search(route[len("api/find-start/"):-len("/cancel")]));return
                if route=="api/live/start":self.send_json(store.start_live(body));return
                if route=="api/live/stop":self.send_json(store.stop_live());return
                if route=="api/live/profile":self.send_json(store.update_live_profile(body.get("profile",{})));return
                if route=="api/live/preview":
                    if store.capture and store.capture.snapshot()["active"]:frame=store.capture.frame()
                    else:
                        source=store.source_factory(body.get("hwnd"))
                        # Give the user time to switch from this page back to the game.
                        time.sleep(3)
                        try:frame=source.frame()
                        except CapturePaused as reason:raise ValueError(str(reason)+"；请在倒计时内切回游戏，再载入预览。") from reason
                    import base64
                    import cv2
                    data=base64.b64encode(cv2.imencode(".jpg",frame,[cv2.IMWRITE_JPEG_QUALITY,87])[1]).decode("ascii")
                    self.send_json({"image":"data:image/jpeg;base64,"+data});return
                if route=="api/pick-video":
                    import tkinter as tk
                    from tkinter import filedialog
                    root=tk.Tk();root.withdraw();root.attributes("-topmost",True)
                    try:path=filedialog.askopenfilename(title="选择绯染天空战斗视频",filetypes=[("Video","*.mp4 *.mkv *.mov *.avi *.webm *.m4v"),("所有文件","*.*")],parent=root)
                    finally:root.destroy()
                    self.send_json({"path":path});return
                if route=="api/video-info":
                    self.send_json(preview_video(body["path"],include_image=False));return
                if route=="api/preview":
                    self.send_json(preview_video(body["path"],body.get("time",0)));return
                if route.startswith("api/cancel/"):
                    job=route[len("api/cancel/"):]
                    if job in store.running:store.running[job]["cancel"].set()
                    if job==store.capture_job and store.capture and store.capture.snapshot()["active"]:store.capture.stop()
                    self.send_json({"ok":True});return
                if route.startswith("api/save/"):
                    job=route[len("api/save/"):]
                    folder=store.folder(job)
                    with store.lock:
                        document=validate_document(body["document"],store.document(job))
                        save_json(folder/"axis.json",document)
                    self.send_json({"ok":True,"document":document});return
                if route.startswith("api/export/"):
                    job=route[len("api/export/"):]
                    folder=store.folder(job)
                    file=export_bundle(store.document(job),folder,compact=bool(body.get("compact")),od=body.get("od","after"))
                    self.send_json({"files":{ext:"file/"+job+"/"+str((file.parent/("行动轴."+ext)).relative_to(folder)).replace("\\","/") for ext in ["png","xlsx","csv","json","html"]}|{"zip":"file/"+job+"/"+str(file.relative_to(folder)).replace("\\","/")}});return
                if route.startswith("api/capture/"):
                    job=route[len("api/capture/"):]
                    folder=store.folder(job)
                    video=Video(store.document(job)["video"]["path"],store.document(job)["settings"]["layout"])
                    try:
                        relative="frames/manual-"+secrets.token_hex(5)+".jpg"
                        save_image(folder/relative,video.frame(parse_time(body["time"])))
                    finally:video.close()
                    self.send_json({"screenshot":relative});return
                self.send_error(404)
            except (PermissionError,ValueError,FileNotFoundError,KeyError) as error:self.send_json({"error":str(error)},400)
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception as error:self.send_json({"error":str(error)},500)
    return Handler


def main():
    parser=argparse.ArgumentParser(description="绯染天空 · Video 转行动轴")
    parser.add_argument("--version",action="version",version=f"HBR Battle Timeline {__version__}")
    parser.add_argument("--port",type=int,default=0)
    parser.add_argument("--no-browser",action="store_true")
    parser.add_argument("--data",default=str(HERE/"data"))
    parser.add_argument("--video",help="CLI Mode：要分析的 Video")
    parser.add_argument("--start",default="0")
    parser.add_argument("--end")
    parser.add_argument("--sample-rate",type=float,default=4)
    parser.add_argument("--profile",help="角色与 UI Layout 的 JSON 配置")
    parser.add_argument("--output",help="CLI Export 输出目录")
    args=parser.parse_args()
    if args.video:
        profile=json.loads(Path(args.profile).read_text(encoding="utf-8-sig")) if args.profile else {}
        output=Path(args.output or HERE/"data"/"cli"/datetime.now().strftime("%Y%m%d-%H%M%S"))
        previous=[-1.]
        def progress(f,text):
            if f-previous[0]>=.025 or f==1.:print(f"{f:5.0%} {text}",flush=True);previous[0]=f
        document=analyze(args.video,output,start=parse_time(args.start) or 0,end=parse_time(args.end),sample_rate=args.sample_rate,profile=validate_profile(profile),progress=progress)
        export_all(document,output)
        export_all(document,output,compact=True)
        print("已导出："+str(output.resolve()))
        return
    store=Store(args.data)
    token=secrets.token_urlsafe(24)
    server=ThreadingHTTPServer(("127.0.0.1",args.port),make_handler(store,token))
    url=f"http://127.0.0.1:{server.server_port}/{token}/"
    save_json(store.data/"server.json",{"url":url,"port":server.server_port,"pid":os.getpid()})
    print("视频转轴已启动："+url,flush=True)
    print("关闭此窗口可退出。识别与 Video 均在本机运行。",flush=True)
    if not args.no_browser:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        store.close()
        server.server_close()


if __name__=="__main__":main()
