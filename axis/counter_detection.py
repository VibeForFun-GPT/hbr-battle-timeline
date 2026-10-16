"""Read labeled UI Counters from geometrically grouped OCR Detections."""
from __future__ import annotations

import re
from collections import Counter

import numpy as np


def reconcile_turn_counters(rows):
    """Use explicit video Counter totals to flag or repair impossible numerals.

    This only repairs a dropped separator with an unambiguous shared total;
    it never fills an unreadable TURN from a previous action.
    """
    totals=Counter()
    for row in rows:
        status=row.get("ocr_status",{})
        text=re.sub(r"\s+","",status.get("raw",{}).get("turn","")).upper().replace("O","0")
        match=re.fullmatch(r"TURN(\d{1,3})[/\\.](\d{1,3})",text)
        if match and 1<=int(match[1])<=int(match[2])<=999:
            totals[int(match[2])]+=1
    if len(totals)!=1:
        return rows
    limit=next(iter(totals))
    for row in rows:
        turn=row.get("turn")
        if type(turn) is not int or turn<=limit:
            continue
        text=re.sub(r"\s+","",row.get("ocr_status",{}).get("raw",{}).get("turn","")).upper().replace("O","0")
        match=re.fullmatch(r"TURN(\d{1,6})",text)
        digits=match[1] if match else ""
        suffix=str(limit)
        prefix=digits[:-len(suffix)] if digits.endswith(suffix) else ""
        repaired=int(prefix) if prefix.isdecimal() and 1<=int(prefix)<=limit else None
        row["turn_original"]=turn
        row["turn"]=repaired
        row["turn_repair_source"]="explicit_video_counter_total"
        row["needs_review"]=True
        message=(f"TURN OCR 原值 {turn} 超过视频明确显示的总数 {limit}；按缺失分隔符暂记为 {repaired}，请核对" if repaired is not None else
                 f"TURN OCR 原值 {turn} 超过视频明确显示的总数 {limit}，编号保留未知，请核对")
        row.setdefault("notes",[]).append(message)
    return rows


def grouped_counter_text(detections, keyword):
    """Join one horizontal Counter, rejecting unrelated digits and conflicting lines."""
    records=[]
    for box,text,confidence in detections or []:
        points=np.asarray(box,dtype=float)
        records.append({"text":re.sub(r"\s+","",text).upper(),"confidence":float(confidence),
                        "x1":float(points[:,0].min()),"x2":float(points[:,0].max()),
                        "y":float(points[:,1].mean()),"height":float(np.ptp(points[:,1]))})
    found=[]
    for anchor in records:
        if not anchor["text"].startswith(keyword) or anchor["confidence"]<.65:
            continue
        tolerance=max(8.,anchor["height"]*.65)
        line=sorted((item for item in records if abs(item["y"]-anchor["y"])<=tolerance
                     and item["x1"]>=anchor["x1"]-2),key=lambda item:item["x1"])
        # A Counter has a label and at most a fraction, not scene prose.
        numeric=[]
        for item in line[1:]:
            if item["x1"]-anchor["x2"]>220 or not re.fullmatch(r"[0-9O/\\.]+",item["text"]):
                break
            numeric.append(item)
        text=anchor["text"]+"".join(item["text"] for item in numeric)
        confidence=min([anchor["confidence"]]+[item["confidence"] for item in numeric])
        # The numerator uses a visibly larger font than the denominator. When
        # DBNet splits both numerals but drops the slash, retain that geometry
        # instead of interpreting e.g. TURN + large 2 + small 30 as TURN230.
        if (anchor["text"]==keyword and len(numeric)==2 and
                all(item["text"].isdecimal() for item in numeric) and
                numeric[0]["height"]>=numeric[1]["height"]*1.2):
            text=keyword+numeric[0]["text"]+"/"+numeric[1]["text"]
            confidence=min(confidence,.8)
        found.append((text,confidence))
    unique={text for text,_ in found}
    return max(found,key=lambda item:item[1]) if len(unique)==1 else None
