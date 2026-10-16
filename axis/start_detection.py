"""Bounded detection of a usable battle-selection Bootstrap Frame."""
from __future__ import annotations

import base64
import math
import threading
import time

import cv2
import numpy as np

from .layout_detection import detect_layout
from .vision import DEFAULT_LAYOUT, Reader, Video


class StartSearchCancelled(Exception):
    pass


def bootstrap_evidence(frame,layout,reader):
    """Apply the Pipeline's Avatar Color Gate plus selection-state checks."""
    candidate=detect_layout(frame,layout)
    if not candidate:
        return {"accepted":False,"colored_avatars":0,"detailed_avatars":0,"button_pink":0.,
                "turn":None,"reason":"six_avatar_geometry_missing"}
    suggested=candidate["layout"]
    offsets=np.linalg.norm(np.asarray(suggested["portrait_centers"])-np.asarray(layout["portrait_centers"]),axis=1)
    effective=layout if float(offsets.max())<30 else suggested
    small=cv2.resize(frame,(960,540))
    hsv=cv2.cvtColor(small,cv2.COLOR_BGR2HSV)
    colors=((hsv[:,:,0]>=138)&(hsv[:,:,0]<=178)&(hsv[:,:,1]>65)&(hsv[:,:,2]>105))
    ratios=[float(colors[b[1]//2:b[3]//2,b[0]//2:b[2]//2].mean()) for b in effective["portrait_boxes"]]
    colored=sum(value>.10 for value in ratios)
    x1,y1,x2,y2=map(int,effective["button_box"])
    button=cv2.cvtColor(frame[y1:y2,x1:x2],cv2.COLOR_BGR2HSV)
    button_pink=float(((button[:,:,0]>136)&(button[:,:,0]<179)&(button[:,:,1]>65)&(button[:,:,2]>105)).mean())
    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
    detail=[]
    for x,y in effective["portrait_centers"]:
        patch=gray[int(y)-15:int(y)+57,int(x)-39:int(x)+39]
        detail.append(float(patch.std()) if patch.size else 0.)
    detailed=sum(value>=8 for value in detail)
    result={"accepted":False,"colored_avatars":colored,"avatar_color_ratios":[round(v,3) for v in ratios],
            "detailed_avatars":detailed,"button_pink":round(button_pink,3),"turn":None,"reason":"not_selection",
            "layout":effective,"layout_detection":candidate["evidence"],"layout_adjusted":effective is not layout}
    if colored<5 or button_pink<=.5 or detailed<5:
        return result
    previous_layout=getattr(reader,"layout",None)
    reader.layout=effective
    try:
        status=reader.status(frame)
    finally:
        if previous_layout is not None:reader.layout=previous_layout
    result.update(turn=status.get("turn"),turn_raw=status.get("raw",{}).get("turn",""),
                  turn_confidence=float(status.get("confidence",{}).get("turn",0.)))
    if status.get("turn") is None:
        result["reason"]="turn_unreadable"
        return result
    result.update(accepted=True,reason="usable_selection")
    return result


def find_start(path,*,search_from=0,layout=None,cancel=None,progress=None,
               budget_seconds=90,max_samples=2400,video_factory=Video,reader_factory=Reader):
    """Scan asynchronously via callers; return a time only for a verified Frame."""
    if not math.isfinite(search_from) or search_from<0:
        raise ValueError("起点搜索时间需要为非负数。")
    stop=cancel or threading.Event()
    begun=time.monotonic()
    video=video_factory(path,layout)
    try:
        if search_from>=video.duration:
            raise ValueError("起点搜索时间需要小于 Video 时长。")
        metadata=video.metadata()
        effective={**DEFAULT_LAYOUT,**(layout or {})}
        if progress:
            progress({"fraction":0.,"message":"载入 OCR，寻找可用的选招画面", "scanned_until":search_from,"metadata":metadata})
        if stop.is_set():
            raise StartSearchCancelled("已取消自动寻找起点。")
        reader=reader_factory(effective)
        scanned_until=search_from
        checked=0
        closest=None
        limited=False
        for when,frame in video.sample(search_from,video.duration,rate=2):
            if stop.is_set():
                raise StartSearchCancelled("已取消自动寻找起点。")
            if time.monotonic()-begun>=budget_seconds or checked>=max_samples:
                limited=True
                break
            scanned_until=float(when)
            checked+=1
            evidence=bootstrap_evidence(frame,effective,reader)
            rank=(evidence["colored_avatars"],evidence["detailed_avatars"],evidence["button_pink"])
            if closest is None or rank>closest[0]:
                closest=(rank,{**evidence,"time":scanned_until})
            if evidence["accepted"]:
                encoded=cv2.imencode(".jpg",frame,[cv2.IMWRITE_JPEG_QUALITY,87])[1]
                confidence=min(.99,.65+.15*evidence["colored_avatars"]/6+.1*evidence["detailed_avatars"]/6
                               +.1*evidence.get("turn_confidence",0.))
                return {"found":True,"time":scanned_until,"metadata":metadata,"duration":video.duration,"fps":video.fps,
                        "max_time":max(0.,(video.frames-1)/video.fps),"crop":list(effective["crop"]),"layout":evidence.get("layout",effective),
                        "confidence":round(confidence,3),"evidence":evidence,"checked_frames":checked,
                        "image":"data:image/jpeg;base64,"+base64.b64encode(encoded).decode("ascii")}
            if progress and (checked==1 or checked%4==0):
                fraction=(scanned_until-search_from)/max(video.duration-search_from,.001)
                progress({"fraction":min(.99,max(0.,fraction)),"message":f"寻找可用选招画面：{scanned_until:.1f}s",
                          "scanned_until":scanned_until,"checked_frames":checked})
        if stop.is_set():
            raise StartSearchCancelled("已取消自动寻找起点。")
        resume=min(max(0.,video.duration-1/video.fps),max(search_from+1/video.fps,scanned_until+.5)) if limited else None
        return {"found":False,"metadata":metadata,"duration":video.duration,"fps":video.fps,
                "crop":list(effective["crop"]),"checked_frames":checked,"scanned_until":scanned_until,
                "complete":not limited,"resume_from":resume,"evidence":closest[1] if closest else None,
                "message":"尚未找到可用初始 Frame；可继续搜索或重新校准游戏区域。" if limited else
                          "没有找到同时包含 6 人 Avatar、TURN 和行动开始按钮的可用选招画面，请校准游戏区域后重试。"}
    finally:
        video.close()
