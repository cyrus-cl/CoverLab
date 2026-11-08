const STAGE_LABELS: Record<string, string> = {
  fingerprint_source: "源文件指纹校验",
  validate_media: "检查音频格式",
  normalize_audio: "音频标准化",
  prepare_model: "准备分离模型",
  separate_stems: "分离人声与伴奏",
  extract_waveform: "提取真实波形",
  estimate_beats: "估计 BPM 与节拍",
  prepare_lyrics_model: "检查云端歌词配置",
  transcribe_lyrics: "云端歌词与时间戳",
  save_lyrics: "保存歌词与时间",
  save_recording: "校验与保存录制",
};

export function stageLabel(name: string): string {
  return STAGE_LABELS[name] ?? "文件处理";
}

export function runtimeErrorLabel(code?: string | null): string {
  if (code === "media_tools_missing" || code?.includes("ffmpeg") || code?.includes("ffprobe")) {
    return "未检测到 FFmpeg / ffprobe，请安装后重新检测。";
  }
  if (code?.includes("demucs") || code?.includes("torch")) {
    return "分离引擎依赖不可用，请安装后重新检测。";
  }
  return "运行环境不可用，请检查本机引擎后重新检测。";
}
