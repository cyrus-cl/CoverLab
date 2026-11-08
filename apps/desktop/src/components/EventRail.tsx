import Activity from "lucide-react/icons/activity";
import Check from "lucide-react/icons/check";
import CircleAlert from "lucide-react/icons/circle-alert";
import Clock3 from "lucide-react/icons/clock-3";

import type { JobEvent, JobStreamState } from "../hooks/useJobEvents";
import { CapabilityOverview, type RuntimeOverviewProps } from "./CapabilityOverview";
import { stageLabel } from "./processingLabels";

type EventContentProps = {
  events: JobEvent[];
  streamState: JobStreamState;
  errorMessage: string | null;
};
type EventRailProps = EventContentProps & RuntimeOverviewProps;

const EVENT_COPY: Record<string, string> = {
  JOB_QUEUED: "任务已进入本机队列",
  FINGERPRINT_STARTED: "开始读取源文件",
  PROGRESS: "正在计算文件指纹",
  FINGERPRINT_SUCCEEDED: "源文件指纹已完成",
  CANCEL_REQUESTED: "已请求取消任务",
  JOB_CANCELED: "任务已取消",
  JOB_RETRIED: "任务已重新排队",
  WORKER_LOST: "应用恢复后继续任务",
  JOB_FAILED: "任务未能完成",
  PROCESSING_SUCCEEDED: "音频处理已完成，结果已保存",
};

const STREAM_COPY: Record<JobStreamState, string> = {
  idle: "事件已同步",
  connecting: "连接本机事件",
  live: "实时记录中",
  reconnecting: "正在恢复连接",
};

export function EventRail({ events, streamState, errorMessage, ...runtimeProps }: EventRailProps) {
  return (
    <>
      <aside className="event-rail event-rail--desktop" aria-label="任务详情">
        <CapabilityOverview {...runtimeProps} />
        <details className="event-panel"><summary>任务记录{events.length ? ` · ${events.length}` : ""}</summary>
        <EventRailContent
          events={events}
          streamState={streamState}
          errorMessage={errorMessage}
        />
        </details>
      </aside>
      <details className="event-drawer">
        <summary>
          <Activity aria-hidden="true" />
          {runtimeProps.hasProject?"结果与下一步":"详情"}
        </summary>
        <div className="event-drawer__panel">
          <CapabilityOverview {...runtimeProps} />
          <details className="event-history"><summary>历史处理记录</summary>
          <EventRailContent
            events={events}
            streamState={streamState}
            errorMessage={errorMessage}
          />
          </details>
        </div>
      </details>
    </>
  );
}

function EventRailContent({ events, streamState, errorMessage }: EventContentProps) {
  const grouped:{event:JobEvent;count:number;key:string}[]=[];
  for(const event of [...events].reverse()) {
    const key=`${event.type}:${event.payload.stageId??event.payload.stageName??""}`;
    const previous=grouped.at(-1);
    if(previous?.key===key)previous.count+=1;
    else grouped.push({event,count:1,key});
  }
  const recentEvents=grouped.slice(0,12);
  return (
    <>
      <div className="event-rail__heading">
        <div>
          <p className="section-kicker">任务记录</p>
          <h2>处理记录</h2>
        </div>
        <span className={`stream-state stream-state--${streamState}`}>
          <i aria-hidden="true" />
          {STREAM_COPY[streamState]}
        </span>
      </div>

      {errorMessage ? (
        <p className="event-error" role="status">
          <CircleAlert aria-hidden="true" />
          {errorMessage}
        </p>
      ) : null}

      {recentEvents.length === 0 ? (
        <div className="event-empty">
          <Activity aria-hidden="true" />
          <p>暂无处理记录</p>
        </div>
      ) : (
        <ol className="event-list">
          {recentEvents.map(({event,count}) => (
            <li key={event.sequence}>
              <span className="event-list__marker">
                {event.type.includes("SUCCEEDED") ? (
                  <Check aria-hidden="true" />
                ) : (
                  <Clock3 aria-hidden="true" />
                )}
              </span>
              <div>
                <p>{eventLabel(event)}</p>
                <time dateTime={event.createdAt}>{formatEventTime(event.createdAt)}</time>
                {count>1?<small className="event-repeat">合并 {count} 条记录</small>:null}
              </div>
              <span className="event-sequence">#{event.sequence}</span>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}

function eventLabel(event: JobEvent): string {
  if (event.type.startsWith("STAGE_")) {
    const stage = stageLabel(typeof event.payload.stageName === "string" ? event.payload.stageName : "");
    const verb = event.type === "STAGE_STARTED" ? "开始" : event.type === "STAGE_SUCCEEDED" ? "完成" : "处理中";
    return `${stage} · ${verb}`;
  }
  return EVENT_COPY[event.type] ?? "本机任务状态已更新";
}

function formatEventTime(value: string): string {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(timestamp);
}
