import CircleCheck from "lucide-react/icons/circle-check";
import RotateCcw from "lucide-react/icons/rotate-ccw";
import Square from "lucide-react/icons/square";
import { useId } from "react";

import type { AnalysisJob, AnalysisStage } from "../api/jobs";
import { stageLabel } from "./processingLabels";

type JobProgressProps = {
  job: AnalysisJob;
  onCancel?: () => void | Promise<void>;
  onRetry?: () => void | Promise<void>;
  actionPending?: boolean;
  compact?: boolean;
};

const STATUS_COPY: Record<AnalysisJob["status"], string> = {
  created: "已创建",
  queued: "等待处理",
  running: "正在处理",
  canceling: "正在取消",
  succeeded: "已完成",
  failed: "处理失败",
  canceled: "已取消",
};

const ERROR_COPY: Record<string, string> = {
  source_missing: "找不到源文件，请确认文件仍在原位置后重试。",
  model_download_failed: "模型下载失败，请检查网络后重试。",
  model_checksum_mismatch: "模型文件校验失败，请重新下载后重试。",
  media_invalid: "无法解码此文件，请检查源文件格式。",
  media_no_audio: "文件中没有可用的音频流。",
  media_tools_missing: "音频工具不可用，请重新安装应用。",
  media_normalization_failed: "音频标准化失败，请检查文件后重试。",
  runtime_missing: "分轨运行组件不可用，请重新安装应用。",
  separation_failed: "本机分轨未能完成，请重试或检查运行环境。",
  source_changed: "源文件已发生变化，请重新创建项目。",
  lyrics_failed: "本机歌词识别失败，可重试或导入 LRC 歌词。",
  recording_invalid: "录制文件无法校验或保存，请重新保存或录制。",
  output_changed: "人声音轨发生变化，请重新分离后识别歌词。",
};

const OPERATION_LABELS = {
  singing: ["一键分离", "伴奏与歌词已就绪", "人声、伴奏、波形和歌词已保存。勾选想唱的歌词，即可试听与录制。"],
  fingerprint: ["源文件校验", "源文件校验完成", "当前校验任务已结束。可继续准备音频或分离人声/伴奏。"],
  prepare: ["音频准备", "音频准备完成", "当前任务已结束，标准化音频已保存。"],
  separate: ["人声 / 伴奏分离", "人声与伴奏已生成", "当前任务已结束，可试听或下载生成的人声与伴奏。"],
  analyze: ["波形与节拍分析", "波形与节拍分析完成", "实际波形与节拍结果已保存，可定位音频并选择片段。"],
  lyrics: ["歌词识别与切分", "歌词识别完成", "机器草稿与时间戳已保存，请检查识别文字和时间。"],
  lyrics_edit: ["保存歌词", "歌词已保存", "校正内容与 LRC / SRT 已保存为新版本。"],
  recording: ["保存录制", "录制已保存", "录制文件已校验并保存到项目目录。"],
};

export function JobProgress({
  job,
  onCancel,
  onRetry,
  actionPending = false,
  compact = false,
}: JobProgressProps) {
  const titleId=useId();
  const operation = job.operation ?? "fingerprint";
  const [title, completedTitle, terminalNote] = OPERATION_LABELS[operation];
  const percent = progressPercent(job);
  const measuredTotal = operation === "fingerprint" ? job.stage.bytesTotal : job.stage.unitsTotal;
  const determinate = job.stage.status === "succeeded" || (measuredTotal !== null && measuredTotal > 0);
  const canCancel = ["created", "queued", "running"].includes(job.status);
  const canRetry = ["failed", "canceled"].includes(job.status) && !["recording", "lyrics_edit"].includes(operation);

  return (
    <section className={`job-card${compact?" job-card--compact":""}`} aria-labelledby={titleId}>
      <div className="job-card__heading">
        <div>
          <p className="section-kicker">当前任务</p>
          <h2 id={titleId}>{job.status === "succeeded" ? completedTitle : title}</h2>
        </div>
        <span className={`status-pill status-pill--${job.status}`}>
          {job.status === "succeeded" ? <CircleCheck aria-hidden="true" /> : null}
          {STATUS_COPY[job.status]}
        </span>
      </div>

      {!compact||job.status!=="succeeded"?<><p className="job-phase-label">{stageLabel(job.stage.name)}{job.stage.progressPhase ? ` · ${phaseLabel(job.stage.progressPhase)}` : ""}</p>
      {operation !== "fingerprint" ? (
        <p className="job-overall">已完成阶段 {job.stages.filter(stage => stage.status === "succeeded").length} / {job.stages.length} · {clampPercent(job.progressPercent)}%</p>
      ) : null}

      <div className="progress-readout">
        <span className="progress-readout__value">{determinate ? `${percent}%` : STATUS_COPY[job.status]}</span>
        <span>{determinate ? formatUnits(job.stage, operation === "fingerprint") : "等待实际处理进度"}</span>
      </div>
      <progress
        className="job-progress"
        value={determinate ? percent : undefined}
        max={100}
        aria-label={`${stageLabel(job.stage.name)}进度`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={determinate ? percent : undefined}
      />
      </>:null}
      {!compact&&job.status === "succeeded" ? (
        <p className="job-terminal-note" role="status">{terminalNote}</p>
      ) : null}

      <details className="job-progress-details"><summary>处理详情</summary><div className="job-card__meta">
        <span>第 {Math.max(1, job.stage.attempts.length)} 次尝试</span>
        <span>{operation === "fingerprint" ? "数据来自本机实际读取字节" : "阶段进度来自实际处理量"}</span>
      </div>

      {operation !== "fingerprint" && job.stages?.length > 0 ? (
        <ol className="job-stages" aria-label="处理阶段">
          {job.stages.map((stage) => (
            <li key={stage.id} data-status={stage.status} aria-current={stage.id === job.stage.id ? "step" : undefined}>
              <span aria-hidden="true">{stage.status === "succeeded" ? "✓" : "·"}</span>
              {stageLabel(stage.name)}
              <small>{stage.status === "succeeded" ? "完成" : stage.status === "failed" ? "失败" : stage.status === "canceled" ? "已取消" : stage.status === "running" ? "处理中" : "等待"}</small>
            </li>
          ))}
        </ol>
      ) : null}</details>

      {job.errorCode ? (
        <div className="job-error" role="alert">
          <div>
            <p>{ERROR_COPY[job.errorCode] ?? job.errorMessage ?? "本机任务未能完成"}</p>
            <details className="job-error-details"><summary>错误详情</summary><code>{job.errorCode}</code></details>
          </div>
          {canRetry && onRetry ? (
            <button
              className="button button--secondary"
              type="button"
              onClick={() => void onRetry()}
              disabled={actionPending}
            >
              <RotateCcw aria-hidden="true" />
              重试任务
            </button>
          ) : null}
        </div>
      ) : null}

      {!job.errorCode && canRetry && onRetry ? (
        <button
          className="button button--secondary job-card__action"
          type="button"
          onClick={() => void onRetry()}
          disabled={actionPending}
        >
          <RotateCcw aria-hidden="true" />
          重试任务
        </button>
      ) : null}

      {canCancel && onCancel ? (
        <button
          className="button button--quiet job-card__action"
          type="button"
          onClick={() => void onCancel()}
          disabled={actionPending}
        >
          <Square aria-hidden="true" />
          取消任务
        </button>
      ) : null}
    </section>
  );
}

function progressPercent(job: AnalysisJob): number {
  if (job.stage.status === "succeeded") return 100;
  const { bytesDone, bytesTotal } = job.stage;
  const persisted =
    (!job.operation || job.operation === "fingerprint") && bytesTotal !== null && bytesTotal > 0
      ? Math.floor((bytesDone / bytesTotal) * 100)
      : job.stage.progressPercent;
  return clampPercent(persisted);
}

function phaseLabel(phase: string): string {
  const [name,file]=phase.split(":",2);
  const label=({model_download: "下载模型", model_verify: "校验模型", separation: "模型推理", transcription:"本机识别", cloud_upload:"压缩并上传人声", cloud_transcription:"等待云端结果", output_write: "写入结果文件", waveform:"扫描音频采样", beats:"检测节拍"} as Record<string,string>)[name] ?? "处理数据";
  return file ? `${label} · ${file}` : label;
}

function clampPercent(value: number): number {
  return Math.max(0, Math.min(100, Math.floor(value)));
}

function formatUnits(stage: AnalysisStage, fingerprint: boolean): string {
  if (fingerprint || stage.progressUnit === "bytes") {
    return `${formatBytes(fingerprint ? stage.bytesDone : stage.unitsDone)} / ${formatBytes(fingerprint ? stage.bytesTotal : stage.unitsTotal)}`;
  }
  const total = stage.unitsTotal === null ? "—" : formatNumber(stage.unitsTotal);
  const unit = stage.progressUnit === "seconds" ? "秒" : stage.progressUnit === "segments" ? "个片段" : stage.progressUnit === "steps" ? "步" : "个文件";
  return `${formatNumber(stage.unitsDone)} / ${total} ${unit}`;
}

function formatNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function formatBytes(bytes: number | null): string {
  if (bytes === null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && value >= 1024; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value >= 10 ? value.toFixed(1) : value.toFixed(2)} ${unit}`;
}
