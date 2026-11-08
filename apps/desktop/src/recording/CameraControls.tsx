import { useEffect, useState } from "react";
import type { CameraLook } from "./compositor";
import type { HeadTrackingStatus } from "./headTracking";
import { ASPECT_RATIOS, type CompositionSettings } from "./composition";

type Props = {
  camera: boolean;
  cameraId: string;
  microphoneId: string;
  look: CameraLook;
  disabled: boolean;
  previewing: boolean;
  requesting: boolean;
  onCamera: (value: boolean) => void;
  onDevice: (kind: "camera" | "microphone", value: string) => void;
  onLook: (value: CameraLook) => void;
  trackingStatus?: HeadTrackingStatus | null;
  composition: CompositionSettings;
  onComposition: (value: CompositionSettings) => void;
};

export function PaperBagToggle({ look, disabled, onLook }: { look: CameraLook; disabled: boolean; onLook: (value: CameraLook) => void }) {
  return <label className={`paper-bag-switch${look.paperBag ? " is-enabled" : ""}`}>
    <span className="paper-bag-switch__identity"><img src="/brand/coverlab-paper-bag-v1.png" alt="" width="38" height="44" /><strong>CoverLab 纸袋特效</strong></span>
    <input aria-label="使用品牌牛皮纸袋" type="checkbox" checked={look.paperBag} disabled={disabled} onChange={event => onLook({ ...look, paperBag: event.target.checked })} />
  </label>;
}

export function CameraControls(props: Props) {
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceError, setDeviceError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const refresh = () => {
      if (!navigator.mediaDevices?.enumerateDevices) return;
      void navigator.mediaDevices.enumerateDevices().then(result => {
        if (live) { setDevices(result); setDeviceError(null); }
      }, () => { if (live) setDeviceError("无法读取设备列表，可先开启预览并允许设备权限。"); });
    };
    refresh();
    navigator.mediaDevices?.addEventListener?.("devicechange", refresh);
    return () => { live = false; navigator.mediaDevices?.removeEventListener?.("devicechange", refresh); };
  }, [props.previewing]);
  const field = (key: keyof CameraLook, value: number | boolean) => props.onLook({ ...props.look, [key]: value });
  const compose = <K extends keyof CompositionSettings>(key: K, value: CompositionSettings[K]) => props.onComposition({ ...props.composition, [key]: value });
  return <fieldset className="camera-controls" disabled={props.disabled || props.requesting}>
    <legend>画面与设备</legend>
    <div className="device-controls">
      <label>麦克风<select aria-label="麦克风" value={props.microphoneId} onChange={event => props.onDevice("microphone", event.target.value)}><option value="">系统默认麦克风</option>{devices.filter(device => device.kind === "audioinput" && device.deviceId !== "default").map((device,index) => <option key={device.deviceId} value={device.deviceId}>{device.label || `麦克风 ${index + 1}`}</option>)}</select></label>
      {props.camera ? <label>摄像头<select aria-label="摄像头" value={props.cameraId} onChange={event => props.onDevice("camera", event.target.value)}><option value="">系统默认摄像头</option>{devices.filter(device => device.kind === "videoinput").map((device,index) => <option key={device.deviceId} value={device.deviceId}>{device.label || `摄像头 ${index + 1}`}</option>)}</select></label> : null}
    </div>
    <div className="camera-options">
      <label className="camera-toggle"><input type="checkbox" checked={props.camera} onChange={event => props.onCamera(event.target.checked)} />同时录制摄像头</label>
      {props.camera ? <label className="camera-toggle"><input type="checkbox" checked={props.look.mirror} onChange={event => field("mirror", event.target.checked)} />镜像画面</label> : null}
    </div>
    {props.camera ? <>
      <div className="composition-controls">
        <label>画布比例<select aria-label="画布比例" value={props.composition.aspect} onChange={event => compose("aspect", event.target.value as CompositionSettings["aspect"])}>{ASPECT_RATIOS.map(ratio => <option key={ratio} value={ratio}>{ratio}</option>)}</select></label>
        <label>画面布局<select aria-label="画面布局" value={props.composition.layout} onChange={event => compose("layout", event.target.value as CompositionSettings["layout"])}><option value="split">左右等分</option><option value="floating">歌词主体 + 浮动人像</option><option value="stacked">上下构图</option><option value="overlay">全画面人像 + 歌词叠层</option></select></label>
        <label>人像容器比例<select aria-label="人像容器比例" value={props.composition.portraitAspect} disabled={props.composition.layout === "floating" && props.composition.floatingShape !== "rounded"} onChange={event => compose("portraitAspect", event.target.value as CompositionSettings["portraitAspect"])}><option value="auto">适应布局</option>{ASPECT_RATIOS.map(ratio => <option key={ratio} value={ratio}>{ratio}</option>)}</select></label>
        <label>人像裁切<select aria-label="人像裁切" value={props.composition.fit} onChange={event => compose("fit", event.target.value as CompositionSettings["fit"])}><option value="cover">填满容器</option><option value="contain">完整显示</option></select></label>
      </div>
      {props.composition.layout === "floating" ? <div className="floating-controls">
        <label>浮动人像形状<select aria-label="浮动人像形状" value={props.composition.floatingShape} onChange={event => compose("floatingShape", event.target.value as CompositionSettings["floatingShape"])}><option value="circle">圆形</option><option value="square">方形</option><option value="rounded">圆角矩形</option></select></label>
        <label>浮动人像大小 <output>{props.composition.floatingSize}%</output><input aria-label="浮动人像大小" type="range" min="14" max="50" step="1" value={props.composition.floatingSize} onChange={event => compose("floatingSize", Number(event.target.value))} /></label>
        <label>画面边距 <output>{props.composition.margin}%</output><input aria-label="画面边距" type="range" min="0" max="10" step="1" value={props.composition.margin} onChange={event => compose("margin", Number(event.target.value))} /></label>
        <div className="portrait-position"><span>人像位置</span><div role="group" aria-label="人像九宫格位置">{["左上", "上中", "右上", "左中", "正中", "右中", "左下", "下中", "右下"].map((label, index) => <button key={label} type="button" aria-label={`人像位置${label}`} aria-pressed={props.composition.floatingPosition === index} onClick={() => compose("floatingPosition", index)}><span aria-hidden="true" /></button>)}</div></div>
      </div> : null}
      <div className="camera-look-controls">
        <label>画面缩放 <output>{props.look.zoom.toFixed(1)}×</output><input aria-label="画面缩放" type="range" min="1" max="2.5" step="0.1" value={props.look.zoom} onChange={event => field("zoom", Number(event.target.value))} /></label>
        <label>对比度 <output>{Math.round(props.look.contrast * 100)}%</output><input aria-label="对比度" type="range" min="0.5" max="1.8" step="0.05" value={props.look.contrast} onChange={event => field("contrast", Number(event.target.value))} /></label>
        <label>裁切水平 <output>{props.composition.cropX}%</output><input aria-label="裁切水平" type="range" min="0" max="100" step="1" value={props.composition.cropX} onChange={event => compose("cropX", Number(event.target.value))} /></label>
        <label>裁切垂直 <output>{props.composition.cropY}%</output><input aria-label="裁切垂直" type="range" min="0" max="100" step="1" value={props.composition.cropY} onChange={event => compose("cropY", Number(event.target.value))} /></label>
      </div>
      {props.look.paperBag ? <div className="paper-bag-settings">
        <label className="camera-toggle"><input type="checkbox" checked={props.look.autoTrack} onChange={event => field("autoTrack", event.target.checked)} />自动跟随头部</label>
        {props.look.autoTrack && props.trackingStatus ? <p className="tracking-status" role="status">{{ loading: "正在定位头部…", tracking: "跟随中", lost: "人像未入镜，已保留遮挡", error: "跟随暂不可用，已保留遮挡。请重新开启预览。" }[props.trackingStatus]}</p> : null}
        {!props.look.autoTrack ? <div className="camera-look-controls">{([['bagX','水平位置',0,100],['bagY','垂直位置',0,100],['bagSize','纸袋大小',15,80]] as const).map(([key,label,min,max]) => <label key={key}>{label}<input type="range" min={min} max={max} step="1" value={props.look[key]} onChange={event => field(key, Number(event.target.value))} /></label>)}</div> : null}
      </div> : null}
    </> : null}
    {deviceError ? <p className="inline-error" role="status">{deviceError}</p> : null}
  </fieldset>;
}
