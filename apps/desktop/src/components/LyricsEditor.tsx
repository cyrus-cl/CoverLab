import { useEffect, useRef, useState, type ReactNode } from "react";
import { EngineApiError } from "../api/engine";
import {
  importLRC,
  saveLyrics,
  type LyricsDocument,
  type LyricSegment,
  type TimeRange,
} from "../api/analysis";
import { getArtifactBlob, type ProjectArtifact } from "../api/processing";
import { formatTime } from "./AudioTimeline";
import { Converter } from "opencc-js/t2cn";
import "../styles/lyrics-tools.css";
import { suspiciousRepetition } from "../recording/lyricReview";

const simplify = Converter({ from: "t", to: "cn" });

type Props = {
  projectId: string;
  document: LyricsDocument;
  artifacts: ProjectArtifact[];
  currentTime: number;
  playback?: ReactNode;
  loading?: boolean;
  onSeek?: (time: number) => void;
  onSelect: (range: TimeRange) => void;
  onSaved: () => void;
  disabled?: boolean;
  onDirtyChange?: (dirty: boolean) => void;
  selectedIndices?: readonly number[];
  onSelectionChange?: (indices: number[]) => void;
  onReviewApprove?: (index: number) => void;
};

export function LyricsEditor({
  projectId,
  document: original,
  artifacts,
  currentTime,
  playback,
  loading = false,
  onSeek,
  onSelect,
  onSaved,
  disabled = false,
  onDirtyChange,
  selectedIndices = [],
  onSelectionChange,
  onReviewApprove,
}: Props) {
  const [document, setDocument] = useState(original);
  const [editing, setEditing] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lrc, setLrc] = useState("");
  const [history, setHistory] = useState<LyricsDocument[]>([]);
  const [find, setFind] = useState("");
  const [replacement, setReplacement] = useState("");
  const [onlySelected, setOnlySelected] = useState(false);
  const [exitConfirm, setExitConfirm] = useState(false);
  const [reviewed, setReviewed] = useState<number[]>([]);
  const needsReview = (segment: LyricSegment) => suspiciousRepetition(segment.text) || Boolean(segment.review?.excludeFromPrompt);
  const fields = useRef(new Map<number, HTMLTextAreaElement>());
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (!editing) {
      setDocument(original);
      setError(null);
      setReviewed([]);
      setHistory([]);
    }
  }, [original, editing]);
  useEffect(() => {
    onDirtyChange?.(editing || pending || lrc.trim().length > 0);
  }, [editing, pending, lrc, onDirtyChange]);
  useEffect(() => {
    if (!editing && !lrc.trim()) return;
    const prevent = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [editing, lrc]);
  function revise(update: (doc: LyricsDocument) => LyricsDocument) {
    setHistory(items => [...items.slice(-49), document]);
    setDocument(update(document));
    setError(null);
  }
  function change(index: number, patch: Partial<LyricSegment>) {
    revise((doc) => ({
      ...doc,
      segments: doc.segments.map((segment, i) =>
        i === index ? { ...segment, ...patch, words: [], review: undefined } : segment,
      ),
    }));
  }
  function split(index: number) {
    const segment = document.segments[index],
      caret = fields.current.get(index)?.selectionStart ?? 0;
    const left = segment.text.slice(0, caret).trim(),
      right = segment.text.slice(caret).trim();
    if (
      !left ||
      !right ||
      currentTime <= segment.start ||
      currentTime >= segment.end
    ) {
      setError("请把文本光标放在分句处，并将播放位置移到本句内部。");
      return;
    }
    setError(null);
    revise((doc) => ({
      ...doc,
      segments: doc.segments.flatMap((item, i) =>
        i === index
          ? [
              { ...item, text: left, end: currentTime, words: [] },
              { ...item, text: right, start: currentTime, words: [] },
            ]
          : [item],
      ),
    }));
  }
  async function readLRC(file?: File) {
    if (!file || disabled || pending) return;
    if (!file.name.toLowerCase().endsWith(".lrc") || file.size > 1024 * 1024) {
      setError("请选择不超过 1 MB 的 .lrc 文件。"); return;
    }
    try {
      const text = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result ?? ""));
        reader.onerror = () => reject(new Error("read failed"));
        reader.readAsText(file, "utf-8");
      });
      if (!mounted.current) return;
      if (text.includes("\uFFFD")) { setError("文件编码不是 UTF-8，请转为 UTF-8 后重试或粘贴歌词。"); return; }
      setLrc(text.replace(/^\uFEFF/, "")); setError(null);
    } catch { if (mounted.current) setError("无法读取 LRC 文件，请重新选择。"); }
  }
  function validationError(): string | null {
    let previous = 0;
    for (const item of document.segments) {
      if (
        !item.text.trim() ||
        !Number.isFinite(item.start) ||
        !Number.isFinite(item.end) ||
        item.start < previous ||
        item.end <= item.start ||
        item.end > document.durationSeconds + 0.01
      )
        return "请检查歌词：文本不能为空，时间需递增、互不重叠且位于音频范围内。";
      previous = item.end;
    }
    return null;
  }
  async function save(importing = false) {
    const issue = importing
      ? !lrc.trim()
        ? "请粘贴带时间戳的 LRC 文本。"
        : null
      : validationError();
    if (issue) {
      setError(issue);
      return;
    }
    setPending(true);
    setError(null);
    try {
      if (importing) await importLRC(projectId, lrc, document.durationSeconds);
      else await saveLyrics(projectId, document);
      if (mounted.current) {
        setEditing(false);
        setLrc("");
        setHistory([]);
        onSaved();
      }
    } catch (error) {
      if (mounted.current)
        setError(
          error instanceof EngineApiError
            ? error.message
            : "歌词保存失败，请检查时间和本机引擎后重试。",
        );
    } finally {
      if (mounted.current) setPending(false);
    }
  }
  async function download(artifact: ProjectArtifact) {
    setError(null);
    try {
      const blob = await getArtifactBlob(artifact);
      if (!mounted.current) return;
      const url = URL.createObjectURL(blob);
      const anchor = window.document.createElement("a");
      anchor.href = url;
      anchor.download = artifact.path.split(/[\\/]/).pop() ?? "lyrics.txt";
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      if (mounted.current) setError("无法读取歌词导出文件。");
    }
  }
  return (
    <section className="lyrics-workspace" aria-labelledby="lyrics-heading">
      <div className="analysis-heading">
        <div><h2 id="lyrics-heading">歌词备忘录</h2></div>
        <button
          type="button"
          className="button button--quiet"
          disabled={pending || disabled}
          onClick={() => {
            if (editing && JSON.stringify(document) !== JSON.stringify(original)) setExitConfirm(true);
            else setEditing(!editing);
          }}
        >
          {editing ? "退出校正" : "校正歌词"}
        </button>
      </div>
      {exitConfirm ? <div role="alertdialog" aria-label="保留歌词校正" className="lyric-exit-confirm"><p>本次校正尚未保存。</p><button autoFocus type="button" className="button button--primary" onClick={() => setExitConfirm(false)}>继续校正</button><button type="button" className="button button--quiet" onClick={() => {setExitConfirm(false);setEditing(false);}}>放弃本次校正</button></div> : null}
      {playback}
      {!editing && onSelectionChange && document.segments.length ? <div className="lyric-selection-toolbar"><span>已选 {selectedIndices.length} / {document.segments.length} 句</span><button type="button" className="button button--quiet" disabled={disabled} onClick={() => onSelectionChange(document.segments.flatMap((item, index) => needsReview(item) && !reviewed.includes(index) ? [] : [index]))}>全选歌词</button><button type="button" className="button button--quiet" disabled={disabled || !selectedIndices.length} onClick={() => onSelectionChange([])}>清空选择</button></div> : null}
      {loading ? <p className="runtime-note" role="status">正在读取歌词…</p> : !document.segments.length && !editing ? <p className="runtime-note">暂无歌词</p> : null}
      {!editing && onSelectionChange ? <label className="lyric-filter"><input type="checkbox" checked={onlySelected} onChange={event => setOnlySelected(event.target.checked)} />只看已选</label> : null}
      {!editing && onlySelected && !selectedIndices.length ? <p role="status">尚未选择歌词</p> : null}
      {editing ? (
        <fieldset className="lyrics-fields" disabled={pending || disabled}>
          <div className="lyric-batch-tools">
            <label>查找文字<input value={find} onChange={event => setFind(event.target.value)} /></label>
            <label>替换为<input value={replacement} onChange={event => setReplacement(event.target.value)} /></label>
            <button type="button" className="button button--quiet" disabled={!find} onClick={() => revise(doc => ({...doc,segments:doc.segments.map(item => item.text.includes(find) ? {...item,text:item.text.split(find).join(replacement),words:[]} : item)}))}>全部替换</button>
            <button type="button" className="button button--quiet" onClick={() => revise(doc => ({...doc,segments:doc.segments.map(item => ({...item,text:simplify(item.text),words:[]}))}))}>全文转简体</button>
            <button type="button" className="button button--quiet" disabled={!history.length} onClick={() => {setDocument(history.at(-1)!);setHistory(items => items.slice(0,-1));setError(null);}}>撤销</button>
            <button type="button" className="button button--quiet" onClick={() => revise(() => original)}>重置本次校正</button>
          </div>
          {onSeek ? <label className="lyric-edit-position">
            播放位置（秒）
            <input type="number" min="0" max={document.durationSeconds} step="0.01" value={Number(Math.max(0, currentTime).toFixed(2))} onChange={event => onSeek(Number(event.target.value))} />
          </label> : null}
          <div className="lyrics-editor-lines">
            {document.segments.map((item, index) => (
              <div className="lyric-edit-row" key={index}>
                <span className="lyric-index">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <label>
                  开始
                  <input
                    aria-label={`第 ${index + 1} 句开始`}
                    type="number"
                    min="0"
                    step="0.01"
                    value={item.start}
                    onChange={(e) =>
                      change(index, { start: Number(e.target.value) })
                    }
                  />
                </label>
                <label>
                  结束
                  <input
                    aria-label={`第 ${index + 1} 句结束`}
                    type="number"
                    min="0"
                    step="0.01"
                    value={item.end}
                    onChange={(e) =>
                      change(index, { end: Number(e.target.value) })
                    }
                  />
                </label>
                <textarea
                  aria-label={`第 ${index + 1} 句歌词`}
                  value={item.text}
                  rows={2}
                  ref={(node) => {
                    if (node) fields.current.set(index, node);
                    else fields.current.delete(index);
                  }}
                  onChange={(e) => change(index, { text: e.target.value })}
                />
                <div className="lyric-edit-actions">
                  <button
                    type="button"
                    className="button button--quiet"
                    aria-label={`在播放位置切分第 ${index + 1} 句`}
                    onClick={() => split(index)}
                  >
                    切分
                  </button>
                  <button
                    type="button"
                    className="button button--quiet"
                    aria-label={`删除第 ${index + 1} 句`}
                    onClick={() =>
                      revise((doc) => ({
                        ...doc,
                        segments: doc.segments.filter((_, i) => i !== index),
                      }))
                    }
                  >
                    删除
                  </button>
                </div>
              </div>
            ))}
          </div>
          <div className="processing-buttons lyric-save-bar">
            <button
              type="button"
              className="button button--secondary"
              disabled={
                pending ||
                disabled ||
                (document.segments.at(-1)?.end ?? 0) >= document.durationSeconds
              }
              onClick={() =>
                revise((doc) => {
                  const start = doc.segments.at(-1)?.end ?? 0;
                  return {
                    ...doc,
                    segments: [
                      ...doc.segments,
                      {
                        start,
                        end: Math.min(start + 3, doc.durationSeconds),
                        text: "",
                        words: [],
                      },
                    ],
                  };
                })
              }
            >
              添加一句
            </button>
            <button
              type="button"
              className="button button--primary"
              disabled={pending || disabled}
              onClick={() => void save()}
            >
              {pending ? "正在保存…" : "保存歌词与时间"}
            </button>
          </div>
        </fieldset>
      ) : (
        <ol className="timed-lyrics">
          {document.segments.map((segment, index) => onlySelected && !selectedIndices.includes(index) ? null : (
            <li
              key={index}
              data-current={
                currentTime >= segment.start && currentTime < segment.end
              }
              data-selected={selectedIndices.includes(index)}
            >
              <button
                type="button"
                aria-label={`${formatTime(segment.start)} ${segment.text} ${formatTime(segment.end)}`}
                disabled={disabled || pending}
                onClick={() =>
                  onSelect({ start: segment.start, end: segment.end })
                }
              >
                <i className="lyric-cue" aria-hidden="true" />
                <time>{formatTime(segment.start)}</time>
                <span>{needsReview(segment) && !reviewed.includes(index) ? "疑似重复识别，请校正后选用" : <ReviewableLyric segment={segment} />}</span>
                <small>{formatTime(segment.end)}</small>
              </button>
              {needsReview(segment) && !reviewed.includes(index) ? <button type="button" className="button button--quiet" disabled={disabled} aria-label={`确认保留第 ${index + 1} 句`} onClick={() => {setReviewed(items => [...items,index]);onReviewApprove?.(index);}}>保留本句</button> : null}
              {onSelectionChange ? <label className="lyric-check"><input type="checkbox" data-guide={index === 0 ? "lyrics" : undefined} aria-label={`选择第 ${index + 1} 句`} checked={selectedIndices.includes(index)} disabled={disabled || (needsReview(segment) && !reviewed.includes(index))} onChange={event => onSelectionChange(event.target.checked ? [...selectedIndices, index].sort((a,b) => a-b) : selectedIndices.filter(item => item !== index))} /><span aria-hidden="true">✓</span></label> : null}
            </li>
          ))}
        </ol>
      )}
      <details className="lrc-import" open={lrc.trim().length > 0 ? true : undefined} onDragOver={event => event.preventDefault()} onDrop={event => {event.preventDefault();void readLRC(event.dataTransfer.files[0]);}}>
        <summary>导入 LRC 歌词</summary>
        <label>选择 LRC 文件<input type="file" accept=".lrc" disabled={pending || disabled} onChange={event => {void readLRC(event.target.files?.[0]);event.target.value="";}} /></label>
        <label>
          带时间戳的 LRC 文本
          <textarea
            aria-label="LRC 文本"
            disabled={pending || disabled}
            value={lrc}
            onChange={(e) => setLrc(e.target.value)}
            rows={5}
            placeholder="[00:12.500]歌词内容"
          />
        </label>
        <button
          type="button"
          className="button button--secondary"
          disabled={pending || disabled}
          onClick={() => void save(true)}
        >
          导入并保存
        </button>
      </details>
      <div className="lyric-exports">
        {artifacts
          .filter((a) =>
            ["lyrics", "lyrics_lrc", "lyrics_srt"].includes(a.kind),
          )
          .map((artifact) => (
            <button
              key={artifact.id}
              type="button"
              className="button button--quiet"
              disabled={pending || editing}
              onClick={() => void download(artifact)}
            >
              导出{" "}
              {artifact.kind === "lyrics"
                ? "JSON"
                : artifact.kind === "lyrics_lrc"
                  ? "LRC"
                  : "SRT"}
            </button>
          ))}
      </div>
      {error ? (
        <p className="inline-error" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}

function ReviewableLyric({ segment }: { segment: LyricSegment }) {
  const parts: ReactNode[] = [];
  let cursor = 0, copiedUntil = 0;
  for (const word of Array.isArray(segment.words) ? segment.words : []) {
    if (!word || typeof word.word !== "string") return <>{segment.text}</>;
    const text = word.word.trim();
    if (!text) continue;
    const start = segment.text.indexOf(text, cursor);
    // Unmatched tokenization cannot safely identify a later repeated word.
    if (start < 0) return <>{segment.text}</>;
    cursor = start + text.length;
    const probability = word.probability;
    if (typeof probability !== "number" || !Number.isFinite(probability) || probability < 0 || probability >= .5) continue;
    parts.push(segment.text.slice(copiedUntil, start));
    parts.push(<mark className="lyric-review" title="建议校对：识别模型对此处不确定" key={`${start}:${text}`}>{text}</mark>);
    copiedUntil = cursor;
  }
  return <>{parts}{segment.text.slice(copiedUntil)}</>;
}
