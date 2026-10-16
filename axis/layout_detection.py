"""Suggest battle Layout anchors from six visible Avatar rings and a button."""
from __future__ import annotations

import copy
from itertools import combinations

import cv2
import numpy as np

from .vision import DEFAULT_LAYOUT


def _radial(mask,x,y,rx,ry,scale):
    angles=np.linspace(0,2*np.pi,96,endpoint=False)
    xx=np.clip(np.rint(x+rx*scale*np.cos(angles)).astype(int),0,mask.shape[1]-1)
    yy=np.clip(np.rint(y+ry*scale*np.sin(angles)).astype(int),0,mask.shape[0]-1)
    return mask[yy,xx].astype(float)


def _rings(mask):
    height,width=mask.shape
    top=round(height*.70)
    candidates=[]
    for warp in (1.,1.2):
        patch=cv2.resize((mask*255)[top:],(round(width*warp),height-top))
        patch=cv2.GaussianBlur(patch,(3,3),0)
        found=cv2.HoughCircles(patch,cv2.HOUGH_GRADIENT,1.1,28,param1=100,param2=25,minRadius=20,maxRadius=48)
        for x,y,radius in () if found is None else found[0]:
            x=float(x)/warp;y=float(y)+top;rx=float(radius)/warp;ry=float(radius)
            if not height*.77<=y<=height*.91:continue
            support=float(np.max([_radial(mask,x,y,rx,ry,scale) for scale in (.94,.98,1.02,1.06)],axis=0).mean())
            outer=float(np.mean([_radial(mask,x,y,rx,ry,scale) for scale in (1.15,1.25)]))
            contrast=support-outer
            if support>.5 and contrast>.20:
                candidates.append(dict(x=x,y=y,rx=rx,ry=ry,support=support,contrast=contrast))
    candidates.sort(key=lambda item:item['contrast'],reverse=True)
    unique=[]
    for candidate in candidates:
        if not any(np.hypot(candidate['x']-other['x'],candidate['y']-other['y'])<20 for other in unique):
            unique.append(candidate)
    return unique[:14]


def _buttons(frame,mask):
    height,width=mask.shape
    left,top=round(width*.75),round(height*.66)
    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
    candidates=[]
    for warp in (.85,1.,1.2,1.4):
        patch=cv2.resize(gray[top:,left:],(round((width-left)*warp),height-top))
        patch=cv2.GaussianBlur(patch,(3,3),0)
        found=cv2.HoughCircles(patch,cv2.HOUGH_GRADIENT,1.1,25,param1=100,param2=24,minRadius=27,maxRadius=48)
        for x,y,radius in () if found is None else found[0]:
            x=float(x)/warp+left;y=float(y)+top;rx=float(radius)/warp;ry=float(radius)
            if not height*.75<=y<=height*.91:continue
            yy,xx=np.indices(mask.shape)
            distance=((xx-x)/rx)**2+((yy-y)/ry)**2
            filled=float(mask[distance<.7**2].mean())
            edge=float(mask[(distance>.9**2)&(distance<1.08**2)].mean())
            outer=float(mask[(distance>1.10**2)&(distance<1.25**2)].mean())
            if filled>.45 and edge>.45 and edge-outer>.05:
                candidates.append(dict(x=x,y=y,rx=rx,ry=ry,filled=filled,support=edge,contrast=edge-outer))
    return sorted(candidates,key=lambda item:item['support']+item['contrast']+.25*item['filled'],reverse=True)


def detect_layout(normalizedframe,base_layout=None):
    """Return a geometric Layout candidate or None; TURN OCR remains mandatory.

    The input must show the full normalized game viewport. Scores express
    geometric agreement, not a calibrated probability or a Bootstrap verdict.
    """
    if (not isinstance(normalizedframe,np.ndarray) or normalizedframe.dtype!=np.uint8
            or normalizedframe.ndim!=3 or normalizedframe.shape[2]!=3 or min(normalizedframe.shape[:2])<180):
        return None
    frame=cv2.resize(normalizedframe,(640,360),interpolation=cv2.INTER_LINEAR)
    hsv=cv2.cvtColor(frame,cv2.COLOR_BGR2HSV)
    pink=((hsv[:,:,0]>=135)&(hsv[:,:,0]<=179)&(hsv[:,:,1]>=65)&(hsv[:,:,2]>=95)).astype(np.uint8)
    # Broad magenta effects must not count as circular Avatar boundaries.
    if float(pink[250:].mean())<.015:return None
    rings=_rings(pink)
    buttons=_buttons(frame,pink)
    if len(rings)<6 or not buttons:return None
    best=None
    for button in buttons[:4]:
        # A partial circle inside the filled Confirm button can have excellent
        # ring support. Keep complete Avatar boundaries clear of that button.
        available=[item for item in rings
                   if item['x']+item['rx']*.95<button['x']-button['rx']*.95]
        for group in combinations(available,6):
            group=sorted(group,key=lambda item:item['x'])
            # Weak arcs formed between adjacent UI elements are too ambiguous
            # to use as a complete Avatar, even if the other five slots fit.
            if min(item['contrast'] for item in group)<.30:continue
            xx=np.array([item['x'] for item in group]);yy=np.array([item['y'] for item in group])
            gaps=np.diff(xx);spacing=float(gaps.mean())
            if xx[-1]-xx[0]<640*.45 or xx[0]>640*.23 or xx[-1]<640*.55:continue
            if float(yy.max()-yy.min())>360*.05 or spacing<=0:continue
            variation=float(gaps.std()/spacing)
            if variation>.20 or float(gaps.min())<np.median([item['rx'] for item in group])*1.5:continue
            # Do not drop an earlier strong Avatar and shift every slot by one.
            # Extra rings within the same Avatar are handled by deduplication;
            # a separate ring to the left makes this six-slot group ambiguous.
            if any(item['x']<xx[0]-.55*spacing
                   and abs(item['y']-float(np.median(yy)))<360*.05
                   for item in available):continue
            if abs(button['y']-float(np.median(yy)))>360*.07:continue
            radii=np.array([item['ry'] for item in group])
            if float(radii.std()/radii.mean())>.30:continue
            score=(float(np.mean([item['contrast'] for item in group]))+2*(1-variation)
                   -1.5*float(yy.std()/radii.mean()))/2.5
            if best is None or score>best[0]:best=(score,group,button)
    if best is None:return None
    score,group,button=best
    layout=copy.deepcopy({**DEFAULT_LAYOUT,**(base_layout or {})})
    base_centers=np.asarray(layout['portrait_centers'],dtype=float)[:3,0]
    width,height=layout['size']
    sx,sy=width/640,height/360
    def box(x1,y1,x2,y2):
        return [max(0,round(x1*sx)),max(0,round(y1*sy)),min(width,round(x2*sx)),min(height,round(y2*sy))]
    layout['portrait_centers']=[[round(item['x']*sx),round(item['y']*sy)] for item in group]
    layout['portrait_radii']=[[round(item['rx']*sx),round(item['ry']*sy)] for item in group]
    layout['portrait_boxes']=[box(item['x']-1.35*item['rx'],item['y']-1.10*item['ry'],
                                   item['x']+1.35*item['rx'],item['y']+1.10*item['ry']) for item in group]
    layout['role_boxes']=[box(item['x']-1.25*item['rx'],item['y']-.90*item['ry'],
                              item['x']-.10*item['rx'],item['y']-.35*item['ry']) for item in group]
    spacing=float(np.median(np.diff([item['x'] for item in group[:3]])))
    layout['skill_boxes']=[box(item['x']-min(1.60*item['rx'],spacing*.48),item['y']-1.60*item['ry'],
                               item['x']+min(1.60*item['rx'],spacing*.48),item['y']-.85*item['ry']) for item in group[:3]]
    layout['button_box']=box(button['x']-.72*button['rx'],button['y']-.35*button['ry'],
                             button['x']+.72*button['rx'],button['y']+.35*button['ry'])
    detected_centers=np.asarray(layout['portrait_centers'],dtype=float)[:3,0]
    if float(np.ptp(base_centers))>1:
        xscale,xoffset=np.polyfit(base_centers,detected_centers,1)
        for key in ('turn_box','bonus_box'):
            x1,y1,x2,y2=layout[key]
            # Counter columns do not necessarily move with the Avatar row.
            layout[key]=[max(0,min(x1,round(x1*xscale+xoffset))),max(0,y1-30),
                         min(width,max(x2,round(x2*xscale+xoffset))),min(height,y2+30)]
    def evidence(item):
        return {'center':[round(item['x']*sx,1),round(item['y']*sy,1)],
                'radius':[round(item['rx']*sx,1),round(item['ry']*sy,1)],
                'pink_support':round(item['support'],3),'boundary_contrast':round(item['contrast'],3)}
    return {'layout':layout,'confidence':round(min(.99,.55+.4*max(0.,min(1.,score))),3),
            'evidence':{'method':'six_avatar_rings_and_confirm_button','avatars':[evidence(item) for item in group],
                        'button':evidence(button),'note':'Geometric candidate; verify labeled TURN before accepting Bootstrap.'}}
