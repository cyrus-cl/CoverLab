import { useEffect, useState } from "react";
import { readArtifactJSON } from "../api/analysis";
import type { ProjectArtifact } from "../api/processing";

export function useArtifactData<T>(
  artifact: ProjectArtifact | undefined,
  valid: (value: T) => boolean,
) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    setError(null);
    setData(null);
    if (!artifact) {
      setLoading(false);
      return () => abort.abort();
    }
    setLoading(true);
    void readArtifactJSON<T>(artifact, abort.signal)
      .then((value) => {
        if (abort.signal.aborted) return;
        if (!valid(value)) throw new Error("invalid_analysis_data");
        setData(value);
      })
      .catch(() => {
        if (!abort.signal.aborted)
          setError("分析结果无法读取，请重新生成或检查项目文件。");
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [artifact?.id, artifact?.sha256, valid]);
  return { data, loading, error };
}
