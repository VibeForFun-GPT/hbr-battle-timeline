"""Video 分析的执行流程，保留每次实际确认的 Screenshot 与 OCR Evidence。"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import threading
import time

import cv2

from .avatar_names import AvatarNameMatcher
from .model import SCHEMA_VERSION, annotate_rows, build_team, stamp
from .start_detection import bootstrap_evidence
from .vision import Avatars, Reader, Video, save_image


class Cancelled(Exception):
    pass


def identify_team_names(team, images, matcher=None):
    """用本地 Avatar Catalog 补全 Placeholder，保留已有的用户姓名。"""
    matcher=matcher or AvatarNameMatcher.load()
    pending=[i for i,p in enumerate(team) if p.get("name_is_placeholder",False)]
    matches=matcher.match_many([images[i] for i in pending])
    for index,match in zip(pending,matches):
        person=team[index]
        person["name_match"]=match
        if match.get("accepted"):
            person.update(name=match["name"],alias=match.get("name_alias") or match["name"],
                          name_confidence=.95,name_raw="",name_source="hbr_quest_avatar",name_is_placeholder=False,
                          database_character_id=match["database_character_id"],matched_style_id=match["matched_style_id"],
                          name_language=match.get("name_language",""))
    return team


def save_json(path,data):
    path=Path(path)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    # Windows readers may briefly hold a handle without FILE_SHARE_DELETE.
    for attempt in range(10):
        try:
            tmp.replace(path)
            break
        except PermissionError:
            if attempt==9:
                raise
            time.sleep(.02*(attempt+1))


def is_od_menu_transition(row,following,activations):
    """排除被 OD Menu 打断的短暂空白选招画面。"""
    if not following or row["turn"]!=following["turn"] or row["bonus"] or not following["bonus"]:
        return False
    if not all(a["skill"]=="A" for a in row["actions"]):
        return False
    # 三人都选 A 的正常 Turn 也能接后置 OD；保留有完整选招过程或
    # 在确认与 OD Menu 之间存在战斗动画的行动。
    if row["time"]-row["phase_start"]>.65:
        return False
    return any(0<=event["time"]-row["confirm_time"]<=.75 for event in activations)


def analyze(video_path, output, *, start=0, end=None, sample_rate=4, profile=None, progress=None, cancel=None):
    profile=profile or {}
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    stop=cancel or threading.Event()
    begun=time.monotonic()

    def report(fraction,message):
        if stop.is_set():
            raise Cancelled("已停止分析。")
        if progress:
            progress(max(0,min(1,fraction)),message)

    video=Video(video_path,profile.get("layout"))
    try:
        finish=min(video.duration,video.duration if end is None else float(end))
        start=float(start)
        if not 0 <= start < finish <= video.duration:
            raise ValueError("开始 / 结束时间不在有效的 Video 范围内。")
        if not 1<=sample_rate<=12:
            raise ValueError("Sample Rate 必须在 1–12 FPS 之间。")
        report(.01,"载入本地 OCR Model")
        reader=Reader(video.layout,profile.get("aliases"))
        report(.02,"定位首次战斗选招画面")
        boot,boot_time=None,None
        for stamp_time,frame in video.sample(start,finish,rate=2):
            evidence=bootstrap_evidence(frame,video.layout,reader)
            if evidence["accepted"]:
                boot,boot_time=frame,stamp_time
                break
            report(.02+min(.07,(stamp_time-start)/max(finish-start,1)*.1),f"自动寻找可用选招画面：{stamp(stamp_time)}")
        if boot is None:
            raise ValueError("未找到战斗选招画面。请设置接近战斗开始的时间；有边框或直播布局时，在「画面校准」中框选完整游戏画面。")
        save_image(output/"bootstrap.jpg",boot)
        roles=reader.roles(boot)
        avatars=Avatars(boot,video.layout,roles)
        supplied=profile.get("team")
        name_mode=profile.get("name_mode") or ("profile" if supplied else "auto")
        team=None
        naming_status=None
        if name_mode=="profile" and supplied and len(supplied)==6:
            team=build_team(supplied)
        elif name_mode=="ocr" and boot_time>5:
            report(.08,"读取战斗前的队伍名称")
            for when in range(max(0,int(boot_time)-6),max(-1,int(boot_time)-76),-5):
                team=reader.roster(video.frame(when))
                if team:
                    save_image(output/"roster.jpg",video.frame(when))
                    break
        if team is None:
            team=build_team()
        if name_mode=="auto":
            report(.08,"用本地角色图库识别 6 人 Avatar")
            team=identify_team_names(team,avatars.images)
            naming_status={"source":"https://hbr.quest/","matched":sum(p.get("name_source")=="hbr_quest_avatar" for p in team),
                           "unresolved":sum(p.get("name_is_placeholder",False) for p in team)}
        for i,person in enumerate(team):
            person.update(id=i+1,role=roles[i],avatar=f"avatars/{i+1}.jpg")
            save_image(output/person["avatar"],avatars.images[i])
        report(.1,"跟踪 Avatar 与每次行动确认")
        segments=[]
        activations=[]
        in_od_menu=False
        pending=None
        phase_start=None
        lost_at=None
        last_report=-10
        for when,frame in video.sample(boot_time,finish,rate=sample_rate):
            if when-last_report>=1:
                report(.1+(when-boot_time)/max(finish-boot_time,1)*.53,f"扫描 {stamp(when)} / {stamp(finish)}")
                last_report=when
            pink,grey,button=avatars.button_state(frame)
            menu_now=grey and avatars.od_menu(frame) and (in_od_menu or reader.od_menu(frame))
            if menu_now:
                if not in_od_menu:
                    state=reader.status(frame)
                    image=f"od/{len(activations)+1:03d}.jpg"
                    save_image(output/image,frame)
                    activations.append({"time":round(when,3),"gauge_before":state["od"],"screenshot":image})
                in_od_menu=True
                pending=None
                phase_start=None
                lost_at=None
                continue
            in_od_menu=False
            if pink:
                present,scores,_=avatars.match(cv2.resize(frame,(960,540)))
                if present:
                    if phase_start is None:phase_start=when
                    if pending is None:pending={"start":phase_start,"end":when,"frames":[],"last_button_frames":[]}
                    pending["end"]=when
                    data=(when,frame.copy(),scores.copy(),button)
                    pending["frames"].append(data)
                    pending["frames"]=pending["frames"][-4:]
                    pending["last_button_frames"]=pending["frames"]
                    lost_at=None
            elif pending and grey and when-pending["end"]<.9:
                pending["confirm_time"]=when
                segments.append(pending)
                pending=None
                phase_start=None
                lost_at=None
            elif pending and not grey:
                present,_,_=avatars.match(cv2.resize(frame,(960,540)))
                if not present:
                    if lost_at is None:lost_at=when
                    if when-lost_at>=1.30:
                        pending["confirm_time"]=lost_at
                        segments.append(pending)
                        pending=None
                        phase_start=None
                        lost_at=None
                else:
                    lost_at=None
        rows=[]
        phase_log=[]
        for index,segment in enumerate(segments):
            report(.64+.26*index/max(len(segments),1),f"读取选招画面 {index+1}/{len(segments)}")
            frames=segment["last_button_frames"] or segment["frames"]
            if not segment["last_button_frames"]:
                phase_log.append({"start":segment["start"],"end":segment["end"],"reason":"短暂 UI 或没有行动开始按钮"})
                continue
            # 多个相邻 Frame 投票读取 Counter；Skill 使用最后一次完整可见的选择。
            statuses=[reader.status(f[1]) for f in frames[-4:]]
            turns=[s["turn"] for s in statuses if s["turn"] is not None]
            turn=Counter(turns).most_common(1)[0][0] if turns else rows[-1]["turn"] if rows else None
            valid=[(f,s) for f,s in zip(frames[-4:],statuses) if s["turn"]==turn] if turns else list(zip(frames[-4:],statuses))
            when,frame,scores,button=valid[-1][0]
            status=valid[-1][1]
            bonus_values=[tuple(s["bonus"]) for _,s in valid if s["bonus"]]
            bonus=list(Counter(bonus_values).most_common(1)[0][0]) if bonus_values else None
            bonus_inferred=False
            if bonus is None and rows and "bonus" in status["raw"]["bonus"].casefold() and rows[-1]["turn"]==turn:
                bonus=rows[-1]["bonus"]
                bonus_inferred=True
            slot_roles=reader.roles(frame)
            order,identity_confidence=avatars.assign(scores,slot_roles)
            skills=reader.skills(frame)
            availability=avatars.availability(frame)
            notes=[]
            if not turns:notes.append("TURN Counter 不可读，暂沿用最近的 TURN，请核对")
            if bonus_inferred:notes.append("BONUS Counter 不可读，暂沿用最近的 BONUS，请核对")
            if button<.55:
                notes.append("行动开始按钮的 Template Match 较低")
            actions=[]
            for slot,item in enumerate(skills):
                ident=order[slot]
                person=team[ident]
                if availability[slot]<.28:
                    item.update(skill="—",skill_full="不行动",skill_note="该位置的 Avatar Ring 为灰色，不能行动")
                elif item["skill"]=="—":
                    item.update(skill="A",skill_full="普通攻擊",skill_note="追加回合未选择 Skill，按 A 记录")
                item.update(slot=slot+1,character_id=ident+1,character=person["alias"],character_full=person["name"],identity_confidence=identity_confidence[slot],availability_score=availability[slot],confidence=round(min(identity_confidence[slot],item["skill_confidence"]),3))
                if identity_confidence[slot]<.78:
                    notes.append(f"位置 {slot+1} 的头像被遮挡或损坏，请核对 Character")
                if item["skill_confidence"]<.85 and item["skill_raw"]:
                    notes.append(f"位置 {slot+1} 的 Skill OCR 需要核对")
                if item.get("skill_truncated"):
                    notes.append(f"位置 {slot+1} 的 Skill 名称被 UI 截断，可手动补全")
                if person["name_confidence"]<.8 and not person.get("name_is_placeholder"):
                    notes.append(f"角色 {ident+1} 的名称需要核对")
                if name_mode=="auto" and person.get("name_is_placeholder"):
                    notes.append(f"角色 {ident+1} 的名字未识别，请在队伍页核对")
                actions.append(item)
            od_values=[s["od"] for _,s in valid if s["od"] is not None]
            od=od_values[-1] if od_values else None
            if od is None:
                notes.append("OD Gauge 被遮挡，未填写数值")
            picture=f"frames/{len(rows)+1:03d}.jpg"
            save_image(output/picture,frame)
            row={"id":f"row-{len(rows)+1}","time":round(when,3),"time_text":stamp(when),
                 "confirm_time":round(segment.get("confirm_time",segment["end"]+1/sample_rate),3),"phase_start":round(segment["start"],3),
                 "turn":turn,"bonus":bonus,"actions":actions,"positions":[i+1 for i in order],"od_before":od,
                 "od_after":None,"od_source":"video_ocr","screenshot":picture,"confidence":round(min(a["confidence"] for a in actions),3),
                 "needs_review":bool(notes),"reviewed":False,"notes":notes,"ocr_status":status}
            rows.append(row)
        # OD Menu 本身不确认行动：在同一 TURN 的 BONUS 前，空白选招画面不生成一行。
        cleaned=[]
        for i,row in enumerate(rows):
            following=rows[i+1] if i+1<len(rows) else None
            if is_od_menu_transition(row,following,activations):
                phase_log.append({"start":row["phase_start"],"end":row["time"],"reason":"OD Menu 发动前的空白选招画面"})
            else:
                cleaned.append(row)
        rows=cleaned
        for event in activations:
            row=next((r for r in rows if r["bonus"] and r["time"]>event["time"] and not r.get("od_activation")),None)
            if row:
                row.update(od_activation=True,od_trigger=event)
                before,after=event["gauge_before"],row["od_before"]
                if before is not None and after is not None:
                    level=round((before-after)/100)
                    if 1<=level<=9 and abs(before-after-100*level)<=3:
                        row["od_level"]=level
        rows=annotate_rows(rows)
        for session in {r["od_session"] for r in rows if r.get("od_session")}:
            group=[r for r in rows if r.get("od_session")==session]
            total=Counter(r["bonus"][1] for r in group).most_common(1)[0][0]
            for row in group:
                if row["bonus"][1]!=total:
                    row["notes"].append("BONUS 总数由同一 OD 内的多次读数投票校正")
                    row["needs_review"]=True
                row["bonus"][1]=total
                row["bonus_text"]=f"BONUS {row['bonus'][0]}/{total}"
        if not rows:
            raise ValueError("未识别到有效行动。请核对 Crop 与战斗开始时间。")
        report(.91,"读取每次行动后的 OD Gauge")
        for i,row in enumerate(rows):
            next_time=rows[i+1]["phase_start"] if i+1<len(rows) else min(finish,row["confirm_time"]+30)
            # 最靠近下一次选招的 Gauge；不经过 OD 激活扣减。
            probes=[min(next_time-.5,row["confirm_time"]+12),min(next_time-.1,row["confirm_time"]+24)]
            values=[]
            for when in sorted(set(probes)):
                if when>row["confirm_time"]:
                    state=reader.status(video.frame(when))
                    if state["od"] is not None:
                        values.append(state["od"])
            if values:
                row["od_after"]=values[-1]
            event=rows[i+1].get("od_trigger") if i+1<len(rows) else None
            if event and event["gauge_before"] is not None:
                row["od_after"]=event["gauge_before"]
                row["od_after_evidence"]=event
        # 后置 OD 可直接排队发动，不一定打开档位选择 Menu。
        for i,row in enumerate(rows):
            if row["od_after"] is None:
                row["notes"].append("行动后 OD Gauge 不可读，请核对或补充")
                row["needs_review"]=True
            if row["kind"] in ("pre_od","post_od") and not row.get("od_level") and i:
                before,after=rows[i-1].get("od_after"),row.get("od_before")
                if before is not None and after is not None:
                    level=round((before-after)/100)
                    if 1<=level<=9 and abs(before-after-level*100)<=3:
                        row["od_level"]=level
                        row["od_level_source"]="video_gauge_difference"
                if not row.get("od_level"):
                    row["notes"].append("OD 档位未读到消耗差，暂用 BONUS 总数，请核对")
                    row["needs_review"]=True
            if row["kind"] in ("pre_od","post_od") and row.get("od_level"):
                row["label"]=f"{'前置' if row['kind']=='pre_od' else '后置'}OD{row['od_level']}"
        report(.97,"生成行动轴与核对资料")
        document={"schema_version":SCHEMA_VERSION,"title":video.path.stem,"created_at":datetime.now().astimezone().isoformat(),
                  "video":video.metadata(),"settings":{"start":start,"end":finish,"sample_rate":sample_rate,"layout":video.layout,"aliases":profile.get("aliases",{}),"name_mode":name_mode},
                  "bootstrap_time":round(boot_time,3),"team":team,"rows":rows,"discarded_phases":phase_log,"od_activations":activations,
                  "elapsed_seconds":round(time.monotonic()-begun,1),
                  "warnings":["OD Gauge 为视频中可见的百分比，不能由整数读数恢复未显示的小数。", "只有完整可见的选招和确认画面才能自动提取；剪辑省略、遮挡或新的 UI Layout 需要校准 / 核对。"]}
        if naming_status:
            document["name_recognition"]=naming_status
            if naming_status["unresolved"]:
                document["warnings"].append(f"{naming_status['unresolved']} 个 Avatar 未从本地图库识别，已保留 Placeholder；请核对队伍名字。")
        save_json(output/"axis.json",document)
        save_json(output/"axis.automatic.json",document)
        report(1.,f"识别完成：{len(rows)} 次行动确认，{sum(r['needs_review'] for r in rows)} 行待核对")
        return document
    finally:
        video.close()
