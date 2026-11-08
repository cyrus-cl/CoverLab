import type { ProjectArtifact, RuntimeStatus } from "../api/processing";
import type { AnalysisJob } from "../api/jobs";
import type { WorkflowStep } from "./workspaceNavigation";
import { runtimeErrorLabel } from "./processingLabels";

export type RuntimeOverviewProps = {
  runtime: RuntimeStatus | null;
  runtimeLoading: boolean;
  runtimeError: string | null;
  onRefreshRuntime: () => void;
  hasProject?: boolean;
  artifacts?: ProjectArtifact[];
  job?: AnalysisJob | null;
  onNavigate?: (step:WorkflowStep) => void;
};

export function CapabilityOverview({ runtime, runtimeLoading, runtimeError, onRefreshRuntime,hasProject=false,artifacts=[],job=null,onNavigate }: RuntimeOverviewProps) {
  const separationAvailable = runtime?.media.available && runtime.separation.available;
  const active = Boolean(job && !["succeeded","failed","canceled"].includes(job.status));
  const hasBacking=artifacts.some(item=>item.kind==="instrumental"), hasLyrics=artifacts.some(item=>item.kind==="lyrics");
  const ready=[hasBacking?"伴奏":null,artifacts.some(item=>item.kind==="vocals")?"人声":null,hasLyrics?"歌词":null].filter(Boolean).join("、");
  const nextStep:WorkflowStep=!hasProject?"import":hasBacking&&hasLyrics?"lyrics":"prepare";
  const nextLabel=nextStep==="import"?"选择音频":nextStep==="lyrics"?"选择歌词":"提取伴奏";
  return (
    <section className="capability-overview" aria-label="结果与下一步">
      <h2>{active?"正在处理歌曲":`下一步：${nextLabel}`}</h2>
      {ready&&!active ? <p className="contextual-result">{ready}已就绪</p> : null}
      {active ? <p className="runtime-note">处理完成后可选择想唱的歌词。</p> : <p className="runtime-note">{nextStep==="lyrics"?"勾选想唱的部分，试听后进入录制。":nextStep==="import"?"选择歌曲和项目保存位置。":"生成伴奏后继续选段。"}</p>}
      {!active&&onNavigate ? <button type="button" className="button button--secondary context-next" onClick={()=>onNavigate(nextStep)}>{nextStep==="lyrics"?"去选歌词":nextStep==="import"?"选择音频":"去提取伴奏"}</button> : null}
      <details className="runtime-settings"><summary>处理环境与模型</summary>
      {runtimeLoading ? <p className="runtime-note" role="status">正在检测处理环境…</p> : null}
      {runtimeError ? <p className="inline-error" role="alert">{runtimeError}</p> : null}
      {runtime ? (
        <>
          <dl className="runtime-facts">
            <div><dt>音频准备</dt><dd data-ready={runtime.media.available}>{runtime.media.available ? "可用" : "不可用"}</dd></div>
            <div><dt>人声 / 伴奏分离</dt><dd data-ready={Boolean(separationAvailable)}>{separationAvailable ? "可用" : "不可用"}</dd></div>
            <div><dt>分离模型</dt><dd>{runtime.separation.model_cached ? "已就绪" : "首次使用需下载"}</dd></div>
            {runtime.analysis ? <div><dt>波形 / BPM 分析</dt><dd data-ready={runtime.analysis.available}>{runtime.analysis.available ? "可用" : "不可用"}</dd></div> : null}
            {runtime.lyrics ? <div><dt>{runtime.lyrics.cloud ? "云端歌词识别" : "本机歌词识别"}</dt><dd data-ready={runtime.lyrics.available}>{runtime.lyrics.available ? runtime.lyrics.cloud ? "已配置" : runtime.lyrics.model_cached ? "已缓存" : "需下载模型" : "不可用"}</dd></div> : null}
          </dl>
          {!runtime.media.available ? <p className="runtime-note">{runtimeErrorLabel(runtime.media.errorCode)}</p> : null}
          {runtime.media.available && !runtime.separation.available ? <p className="runtime-note">{runtimeErrorLabel(runtime.separation.errorCode ?? "demucs_missing")}</p> : null}
          <details className="model-details">
            <summary>模型信息</summary>
            <dl>
              <div><dt>模型</dt><dd>{runtime.separation.model_id}</dd></div>
              <div><dt>版本</dt><dd>{runtime.separation.revision}</dd></div>
              <div><dt>设备策略</dt><dd>{runtime.separation.device === "auto" ? "自动选择" : runtime.separation.device}{runtime.separation.cpu_supported ? " · 支持 CPU" : ""}</dd></div>
              <div><dt>SHA-256</dt><dd><code>{runtime.separation.model_sha256}</code></dd></div>
              {runtime.lyrics ? <div><dt>歌词模型</dt><dd>{runtime.lyrics.model_id} · CPU</dd></div> : null}
            </dl>
          </details>
        </>
      ) : null}
      <button type="button" className="button button--quiet runtime-refresh" onClick={onRefreshRuntime}>重新检测</button>
      </details>
    </section>
  );
}
