import Download from "lucide-react/icons/download";
import Play from "lucide-react/icons/play";
import Pause from "lucide-react/icons/pause";
import { useEffect, useId, useRef, useState } from "react";
import { EngineApiError } from "../api/engine";
import {
  getArtifactDownloadUrl, getArtifactPlaybackUrl, isAudioArtifact, latestByKind,
  type AudioArtifact, type ProjectArtifact,
} from "../api/processing";
import { claimAudioFocus } from "../recording/audioFocus";

type ProjectArtifactsProps = {
  artifacts: ProjectArtifact[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  selectedAudioId: string | null;
  onSelectedAudioChange: (id: string) => void;
  disabled?: boolean;
};

const LABELS = { normalized_audio: "原声", vocals: "人声", instrumental: "伴奏" };
const TRACK_ORDER = { normalized_audio: 0, vocals: 1, instrumental: 2 };

export function ProjectArtifacts({
  artifacts, loading, error, onRefresh, selectedAudioId, onSelectedAudioChange, disabled = false,
}: ProjectArtifactsProps) {
  const selectId = useId();
  const tracks = latestByKind(artifacts).filter(isAudioArtifact)
    .sort((a, b) => TRACK_ORDER[a.kind] - TRACK_ORDER[b.kind]);
  const selected = tracks.find(item => item.id === selectedAudioId)
    ?? tracks.find(item => item.kind === "instrumental") ?? tracks[0];
  if (!selected && !error) return null;

  return (
    <section className="selected-track" aria-label="当前音轨" aria-busy={loading || undefined}>
      {error ? (
        <div className="artifact-error" role="alert">
          <p>{error}</p>
          <button type="button" className="button button--quiet" onClick={onRefresh}>重新读取音轨</button>
        </div>
      ) : null}
      {selected ? <>
        <div className="selected-track__choice">
          <label htmlFor={selectId}>音轨</label>
          <select id={selectId} value={selected.id} disabled={disabled} onChange={event => onSelectedAudioChange(event.target.value)}>
            {tracks.map(item => <option key={item.id} value={item.id}>{LABELS[item.kind]}</option>)}
          </select>
        </div>
        <ArtifactCard key={`${selected.id}:${selected.sha256}`} artifact={selected} disabled={disabled} />
      </> : null}
    </section>
  );
}

function ArtifactCard({ artifact, disabled }: { artifact: AudioArtifact; disabled: boolean }) {
  const [hasSource, setHasSource] = useState(false);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const auditionEpoch = useRef(0);
  const disabledRef = useRef(disabled);
  disabledRef.current = disabled;
  const label = LABELS[artifact.kind];

  useEffect(() => {
    const abort = new AbortController();
    controller.current = abort;
    const player = audio.current;
    const focus = (event: Event) => {
      if ((event as CustomEvent<{ active: HTMLMediaElement | null }>).detail.active !== player) {
        ++auditionEpoch.current;
        player?.pause();
        setPlaying(false);
      }
    };
    window.addEventListener("coverlab:audio-focus", focus);
    return () => {
      ++auditionEpoch.current;
      window.removeEventListener("coverlab:audio-focus", focus);
      abort.abort();
      player?.pause();
      player?.removeAttribute("src");
      player?.load();
    };
  }, []);

  useEffect(() => {
    if (disabled) {
      ++auditionEpoch.current;
      audio.current?.pause();
      setPlaying(false);
    }
  }, [disabled]);

  async function audition() {
    const player = audio.current;
    const signal = controller.current?.signal;
    if (!player || !signal || disabledRef.current || loading) return;
    setError(null);
    if (playing) {
      ++auditionEpoch.current;
      player.pause();
      setPlaying(false);
      return;
    }

    const attempt = ++auditionEpoch.current;
    const isCurrent = () => !signal.aborted && !disabledRef.current && attempt === auditionEpoch.current;
    claimAudioFocus(player);
    setLoading(true);
    try {
      if (!player.getAttribute("src")) {
        const url = await getArtifactPlaybackUrl(artifact, signal);
        if (!isCurrent()) return;
        player.src = url;
        setHasSource(true);
      }
      await player.play();
      if (isCurrent()) setPlaying(true);
    } catch (caught) {
      if (isCurrent()) {
        setError(caught instanceof EngineApiError ? caught.message : "此音轨无法播放，请重试。");
        player.removeAttribute("src");
        setHasSource(false);
      }
    } finally {
      if (!signal.aborted) setLoading(false);
    }
  }

  async function download() {
    const signal = controller.current?.signal;
    if (!signal || disabledRef.current || downloading) return;
    setError(null);
    setDownloading(true);
    try {
      const url = await getArtifactDownloadUrl(artifact, signal);
      if (signal.aborted || disabledRef.current) return;
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = artifact.path.split(/[\\/]/).pop() ?? `${artifact.kind}.wav`;
      anchor.click();
    } catch (caught) {
      if (!signal.aborted) setError(caught instanceof EngineApiError ? caught.message : "无法下载音轨，请重试。");
    } finally {
      if (!signal.aborted) setDownloading(false);
    }
  }

  return (
    <article className="selected-audio-file" aria-label={`${label}文件`}>
      <div className="selected-audio-file__actions">
        <button type="button" className="button button--secondary" disabled={disabled || loading} onClick={() => void audition()}>
          {playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
          {loading ? "正在读取…" : `${playing ? "暂停" : "试听"}${label}`}
        </button>
        <button type="button" className="button button--quiet" disabled={disabled || downloading} onClick={() => void download()}>
          <Download aria-hidden="true" />
          {downloading ? "正在下载…" : `下载${label}`}
        </button>
      </div>
      <audio
        ref={audio}
        hidden={!hasSource}
        controls={hasSource && !disabled}
        className="selected-audio-player"
        preload="metadata"
        aria-label={hasSource ? `${label}试听` : undefined}
        onPlay={() => {
          if (disabledRef.current) {
            audio.current?.pause();
            return;
          }
          claimAudioFocus(audio.current);
          setPlaying(true);
        }}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onError={() => {
          if (!audio.current?.getAttribute("src")) return;
          audio.current.removeAttribute("src");
          setHasSource(false);
          setPlaying(false);
          setError("此音轨无法播放，请重试。");
        }}
      />
      {error ? <p className="inline-error selected-audio-file__error" role="alert">{error}</p> : null}
    </article>
  );
}
