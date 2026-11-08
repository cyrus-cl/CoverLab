import type { LyricSegment } from "../api/analysis";
import { HeadTracker, type HeadPose, type HeadTrackingStatus } from "./headTracking";
import { DEFAULT_COMPOSITION, cameraTransform, computeCompositionLayout, mapPoseToPortrait, type CompositionLayout, type CompositionSettings, type Rect } from "./composition";

export type CameraLook = {
  mirror: boolean;
  zoom: number;
  contrast: number;
  paperBag: boolean;
  autoTrack: boolean;
  bagX: number;
  bagY: number;
  bagSize: number;
};
export const DEFAULT_LOOK: CameraLook = {
  mirror: true, zoom: 1, contrast: 1, paperBag: false, autoTrack: true, bagX: 50, bagY: 35, bagSize: 38,
};
export type SceneOptions = { look: CameraLook; lyrics: readonly LyricSegment[]; title: string; time: number; composition?: CompositionSettings; standby?: boolean; preparation?: number; headPose?: HeadPose | null };
let paperBagImage: HTMLImageElement | null = null;
function bagImage(): HTMLImageElement {
  if (!paperBagImage) { paperBagImage = new Image(); paperBagImage.src = new URL("brand/coverlab-paper-bag-v1.png", document.baseURI).href; }
  return paperBagImage;
}

function wrapText(context: CanvasRenderingContext2D, text: string, width: number): string[] {
  const lines: string[] = [];
  let line = "";
  for (const character of text) {
    if (line && context.measureText(line + character).width > width) { lines.push(line); line = ""; }
    line += character;
  }
  if (line) lines.push(line);
  return lines.length ? lines : [""];
}

/** One renderer for both the live preview and the exported video. */
export function drawCompositeFrame(canvas: HTMLCanvasElement, video: HTMLVideoElement | null, options: SceneOptions): void {
  const composition = options.composition ?? DEFAULT_COMPOSITION;
  const layout = computeCompositionLayout(composition);
  if (canvas.width !== layout.width) canvas.width = layout.width;
  if (canvas.height !== layout.height) canvas.height = layout.height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("当前环境不支持视频画面合成。");
  const { look } = options;
  const portrait = layout.portrait;
  context.fillStyle = "#100e0c";
  context.fillRect(0, 0, layout.width, layout.height);
  if (!layout.overlay) { context.fillStyle = "#080808"; context.fillRect(layout.lyrics.x, layout.lyrics.y, layout.lyrics.width, layout.lyrics.height); }
  context.save();
  clipPortrait(context, layout);
  context.fillStyle = "#100e0c"; context.fillRect(portrait.x, portrait.y, portrait.width, portrait.height);
  if (video && video.readyState >= 2 && video.videoWidth && video.videoHeight) {
    const transform = cameraTransform(portrait, video.videoWidth, video.videoHeight, { ...composition, zoom: look.zoom, mirror: look.mirror });
    context.save();
    if (look.mirror) { context.translate(portrait.x * 2 + portrait.width, 0); context.scale(-1, 1); }
    context.filter = `contrast(${look.contrast})`;
    context.drawImage(video, transform.draw.x, transform.draw.y, transform.draw.width, transform.draw.height);
    context.restore();
    if (look.paperBag) {
      context.save(); context.translate(portrait.x, portrait.y);
      if (look.autoTrack && !options.headPose) {
        context.fillStyle = "#26231e"; context.fillRect(0, 0, portrait.width, portrait.height);
        drawBag(context, portrait.width, portrait.height, look, { cx: portrait.width / 2, cy: portrait.height * .45, width: portrait.width * .27, height: portrait.height * .24, rotation: 0 });
        context.fillStyle = "#e1d7c9"; context.font = `${Math.max(12, Math.min(22, portrait.width * .045))}px 'Microsoft YaHei', sans-serif`;
        context.textAlign = "center"; context.fillText("寻找人像…", portrait.width / 2, portrait.height - Math.min(46, portrait.height * .12));
      } else {
        const pose = options.headPose;
        drawBag(context, portrait.width, portrait.height, look, look.autoTrack && pose ? { ...pose, cx: pose.cx - portrait.x, cy: pose.cy - portrait.y } : undefined);
      }
      context.restore();
    }
  } else {
    context.save(); context.translate(portrait.x, portrait.y);
    const scale = Math.min(portrait.width / 640, portrait.height / 720);
    context.scale(scale, scale);
    context.fillStyle = "#e9c69a"; context.font = "600 48px 'Segoe UI', sans-serif";
    context.fillText("CL", 48, 92);
    context.fillStyle = "#b8ab99"; context.font = "24px 'Microsoft YaHei', sans-serif";
    context.fillText("开启摄像头预览", 48, portrait.height / scale / 2);
    context.font = "18px 'Microsoft YaHei', sans-serif";
    context.fillText("人像将出现在此处", 48, portrait.height / scale / 2 + 40);
    context.restore();
  }
  context.restore();
  if (layout.overlay) { context.fillStyle = "rgba(8,8,8,.86)"; context.fillRect(layout.lyrics.x, layout.lyrics.y, layout.lyrics.width, layout.lyrics.height); }
  drawLyrics(context, layout, options);
}

function clipPortrait(context: CanvasRenderingContext2D, layout: CompositionLayout) {
  const { x, y, width, height } = layout.portrait;
  context.beginPath();
  if (layout.portraitShape === "circle") context.ellipse(x + width / 2, y + height / 2, width / 2, height / 2, 0, 0, Math.PI * 2);
  else if (layout.portraitShape === "rounded") context.roundRect(x, y, width, height, Math.min(width, height) * .1);
  else context.rect(x, y, width, height);
  context.clip();
}

/** Reserve the floating camera's actual bounds so it cannot obscure a lyric. */
function textSpace(layout: CompositionLayout, y: number, height: number, padding: number): Rect {
  const notes = layout.lyrics, camera = layout.lyricExclusion;
  let left = notes.x + padding, right = notes.x + notes.width - padding;
  if (camera && y < camera.y + camera.height + padding && y + height > camera.y - padding) {
    const leftWidth = camera.x - padding - left, rightWidth = right - camera.x - camera.width - padding;
    if (rightWidth > leftWidth) left = camera.x + camera.width + padding;
    else right = camera.x - padding;
  }
  return { x: left, y, width: Math.max(1, right - left), height };
}

function drawLyrics(context: CanvasRenderingContext2D, layout: CompositionLayout, options: SceneOptions) {
  const { lyrics, time, title } = options, notes = layout.lyrics;
  const padding = Math.max(18, Math.min(36, notes.width * .055));
  const font = Math.max(18, Math.min(34, notes.width * .055, notes.height * .075));
  const titleFont = Math.min(36, font * 1.1), lineHeight = font * 1.4, cue = font * .35;
  context.save(); context.beginPath(); context.rect(notes.x, notes.y, notes.width, notes.height); context.clip();
  context.fillStyle = "#f2eee7"; context.font = `600 ${titleFont}px 'Microsoft YaHei', sans-serif`;
  const heading = textSpace(layout, notes.y + padding, titleFont * 2 + 34, padding);
  const titleLines = wrapText(context, title || "我的翻唱", heading.width).slice(0, layout.overlay ? 1 : 2);
  let y = notes.y + padding + titleFont;
  titleLines.forEach((line, index) => context.fillText(line, heading.x, y + index * titleFont * 1.15));
  y += titleLines.length * titleFont * 1.15;
  context.fillStyle = "#c5b9a8"; context.font = `${Math.max(12, font * .48)}px 'Segoe UI', sans-serif`;
  if (!layout.overlay) { context.fillText("CoverLab", heading.x, y); y += font; }
  y += font * .7;
  const active = options.standby || options.preparation ? -1 : lyrics.findIndex(line => time >= line.start && time < line.end);
  const upcoming = lyrics.findIndex(line => line.start > time);
  const anchor = active >= 0 ? active : upcoming >= 0 ? upcoming : Math.max(0, lyrics.length - 1);
  const bottom = notes.y + notes.height - padding - 22;
  const first = options.standby ? 0 : anchor;
  context.save(); context.beginPath(); context.rect(notes.x, y - font, notes.width, Math.max(0, bottom - y + font)); context.clip();
  for (let index = first; index < lyrics.length && y < bottom; index++) {
    const line = lyrics[index];
    const current = index === active;
    context.font = `${current ? "600 " : ""}${font}px 'Microsoft YaHei', sans-serif`;
    let rowHeight = lineHeight + font * .65;
    let space = textSpace(layout, y - font, rowHeight, padding), wrapped: string[] = [];
    for (let pass = 0; pass < 3; pass++) {
      wrapped = wrapText(context, line.text, space.width - cue * 2 - font * .65);
      rowHeight = wrapped.length * lineHeight + font * .65;
      space = textSpace(layout, y - font, rowHeight, padding);
    }
    context.strokeStyle = current ? "#f9cb38" : "#77726a"; context.lineWidth = 2;
    const cueX = space.x + cue, cueY = y - font * .3;
    context.beginPath(); context.arc(cueX, cueY, cue, 0, 2 * Math.PI);
    if (current) {
      context.fillStyle = "#f9cb38"; context.fill();
      context.save(); context.strokeStyle = "#1d1910"; context.lineWidth = 2.5;
      context.beginPath(); context.moveTo(cueX - cue * .45, cueY); context.lineTo(cueX - cue * .1, cueY + cue * .4); context.lineTo(cueX + cue * .55, cueY - cue * .4); context.stroke(); context.restore();
    }
    else context.stroke();
    context.fillStyle = current ? "#ffffff" : !options.standby && line.end <= time ? "#817d76" : "#e9e5de";
    wrapped.forEach((text, row) => context.fillText(text, space.x + cue * 2 + font * .65, y + row * lineHeight));
    y += rowHeight;
  }
  if (!lyrics.length) {
    context.fillStyle = "#b8ab99"; context.font = "23px 'Microsoft YaHei', sans-serif";
    context.fillText("勾选歌词后，在这里跟唱", textSpace(layout, y, 30, padding).x, y);
  }
  context.restore();
  context.fillStyle = "#b8ab99"; context.font = "17px 'Microsoft YaHei', sans-serif";
  const whole = Math.floor(Math.max(0, time));
  const clock = `${Math.floor(whole / 60).toString().padStart(2, "0")}:${(whole % 60).toString().padStart(2, "0")}`;
  const status = options.standby ? "等待开始" : options.preparation ? `准备 ${options.preparation}`
    : active >= 0 ? "正在演唱" : upcoming >= 0 ? "等待下一句" : "演唱结束";
  const footerY = notes.y + notes.height - padding * .45;
  context.fillText(`${clock}  ·  ${status}`, textSpace(layout, footerY - 20, 22, padding).x, footerY);
  context.restore();
}

function drawBag(context: CanvasRenderingContext2D, width: number, height: number, look: CameraLook, pose?: HeadPose | null) {
  const sprite = bagImage();
  const bagWidth = pose ? Math.max(pose.width * 1.8, pose.height * 1.1) : width * look.bagSize / 100;
  const bagHeight = pose ? Math.max(pose.height * 1.8, bagWidth * 1.25) : bagWidth * 1.35;
  if (sprite.complete && sprite.naturalWidth) {
    context.save();
    context.translate(pose?.cx ?? width * look.bagX / 100, pose ? pose.cy - pose.height * .13 : height * look.bagY / 100);
    context.rotate(pose?.rotation ?? 0);
    context.fillStyle = "#b99463";
    context.beginPath(); context.ellipse(0, 0, bagWidth * .39, bagHeight * .41, 0, 0, Math.PI * 2); context.fill();
    if (pose) {
      context.beginPath(); context.roundRect(-pose.width * .65, -pose.height * .68, pose.width * 1.3, pose.height * 1.55, pose.width * .08); context.fill();
    }
    context.drawImage(sprite, -bagWidth / 2, -bagHeight / 2, bagWidth, bagHeight);
    context.restore();
    return;
  }
  context.save();
  context.translate(pose?.cx ?? width * look.bagX / 100, pose ? pose.cy - pose.height * .13 : height * look.bagY / 100);
  context.rotate(pose?.rotation ?? 0);
  const x = -bagWidth / 2, y = -bagHeight / 2;
  context.fillStyle = "#b99463"; context.strokeStyle = "#8e6c42"; context.lineWidth = 3;
  if (pose) context.fillRect(-pose.width * .65, -pose.height * .68, pose.width * 1.3, pose.height * 1.55);
  context.beginPath(); context.moveTo(x + 8, y); context.lineTo(x + bagWidth - 10, y + 5);
  context.lineTo(x + bagWidth, y + bagHeight); context.lineTo(x, y + bagHeight - 5); context.closePath(); context.fill(); context.stroke();
  context.fillStyle = "#cda979"; context.fillRect(x + 12, y + 8, bagWidth - 30, bagHeight - 23);
  context.strokeStyle = "#a17d50"; context.beginPath(); context.moveTo(x + bagWidth * .16, y + 12); context.lineTo(x + bagWidth * .12, y + bagHeight - 10); context.stroke();
  context.fillStyle = "#2d251b"; context.textAlign = "center";
  context.font = `700 ${bagWidth * .24}px 'Segoe UI', sans-serif`;
  context.fillText("CL", x + bagWidth / 2, y + bagHeight * .47);
  context.font = `600 ${bagWidth * .105}px 'Segoe UI', sans-serif`;
  context.fillText("CoverLab", x + bagWidth / 2, y + bagHeight * .64);
  context.restore();
}

export class CameraScene {
  private video = document.createElement("video");
  private frame: number | null = null;
  private output: MediaStream | null = null;
  private disposed = false;
  private tracker: HeadTracker | null = null;
  constructor(private canvas: HTMLCanvasElement, input: MediaStream, private options: () => SceneOptions, private onTracking?: (status: HeadTrackingStatus) => void) {
    this.video.muted = true; this.video.playsInline = true; this.video.srcObject = input;
  }
  async start(): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([this.video.play(), new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error("摄像头画面尚未就绪，请重新预览。")), 10000);
      })]);
      if (this.disposed) throw new DOMException("Preview closed", "AbortError");
      const draw = () => {
        if (this.disposed) return;
        const options = this.options(), now = performance.now();
        let headPose: HeadPose | null = null;
        if (options.look.paperBag && options.look.autoTrack) {
          this.tracker ??= new HeadTracker(this.onTracking);
          this.tracker.update(this.video, now);
          const raw = this.tracker.getPose(now);
          if (raw && this.video.videoWidth && this.video.videoHeight) {
            const composition = options.composition ?? DEFAULT_COMPOSITION;
            const layout = computeCompositionLayout(composition);
            headPose = mapPoseToPortrait(raw, cameraTransform(layout.portrait, this.video.videoWidth, this.video.videoHeight, { ...composition, zoom: options.look.zoom, mirror: options.look.mirror }));
          }
        } else { this.tracker?.dispose(); this.tracker = null; }
        drawCompositeFrame(this.canvas, this.video, { ...options, headPose });
        this.frame = requestAnimationFrame(draw);
      };
      draw();
    } finally { clearTimeout(timer); }
  }
  capture(): MediaStream {
    if (this.disposed) throw new Error("摄像头预览已关闭，请重新打开。");
    if (!this.output) this.output = this.canvas.captureStream(30);
    return this.output;
  }
  dispose(): void {
    this.disposed = true;
    this.tracker?.dispose(); this.tracker = null;
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.video.pause(); this.video.srcObject = null;
    this.output?.getTracks().forEach(track => { if (track.readyState !== "ended") track.stop(); });
    this.output = null;
  }
}
